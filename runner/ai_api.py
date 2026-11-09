"""Fixed-origin AI API clients and a local key vault. Never serialize keys to public state."""
import base64
import ctypes
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit


PROVIDERS = {
    'chatgpt': dict(name='OpenAI', model='gpt-4.1-mini', url='https://api.openai.com/v1/responses',
                    variables=('HANAMEDIA_OPENAI_API_KEY','OPENAI_API_KEY')),
    'gemini': dict(name='Gemini', model='gemini-2.5-flash', url='https://generativelanguage.googleapis.com/v1beta/models/',
                   variables=('HANAMEDIA_GEMINI_API_KEY','GEMINI_API_KEY')),
    'claude': dict(name='Anthropic', model='claude-sonnet-4-5', url='https://api.anthropic.com/v1/messages',
                   variables=('HANAMEDIA_ANTHROPIC_API_KEY','ANTHROPIC_API_KEY')),
}


class APIError(ValueError):
    pass


def private_write(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data)
        os.chmod(temporary,0o600)
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def dpapi(data, decrypt=False):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise APIError('Không mở được kho khóa của tài khoản Windows này.')
    try:return ctypes.string_at(target.data,target.size)
    finally:ctypes.windll.kernel32.LocalFree(target.data)


class KeyVault:
    def __init__(self, data_dir):
        self.folder=Path(data_dir)/'api-private';self.path=self.folder/'credentials.enc'
        self.lock=threading.RLock()

    def crypt(self, data, decrypt=False):
        if os.name=='nt':return dpapi(data,decrypt)
        from cryptography.fernet import Fernet
        keyfile=self.folder/'local.key'
        if not keyfile.exists():
            if decrypt:raise APIError('Thiếu khóa giải mã cấu hình API trên máy này.')
            private_write(keyfile,Fernet.generate_key())
        cipher=Fernet(keyfile.read_bytes())
        return cipher.decrypt(data) if decrypt else cipher.encrypt(data)

    def read(self):
        if not self.path.exists():return {}
        try:return json.loads(self.crypt(self.path.read_bytes(),True))
        except APIError:raise
        except Exception:raise APIError('Không đọc được kho cấu hình API. Không ghi đè dữ liệu cũ.') from None

    def credential(self, provider):
        if provider not in PROVIDERS:raise APIError('Nhà cung cấp không hợp lệ.')
        for name in PROVIDERS[provider]['variables']:
            if os.environ.get(name):return os.environ[name],'environment'
        with self.lock:return self.read().get(provider,{}).get('key',''),'local'

    def options(self, provider):
        with self.lock:record=self.read().get(provider,{})
        return dict(model=record.get('model') or PROVIDERS[provider]['model'],max_output_tokens=record.get('max_output_tokens',4096))

    def public(self):
        result={}
        for provider,spec in PROVIDERS.items():
            key,source=self.credential(provider)
            result[provider]=dict(name=spec['name'],configured=bool(key),source=source if key else 'missing',**self.options(provider))
        return result

    def gateway(self, public=False):
        with self.lock:record=self.read().get('_gateway',{})
        if public:return dict(url=record.get('url',''),configured=bool(record.get('url') and record.get('token')))
        return record

    def save_gateway(self,url,token=None,remove=False):
        if not isinstance(url,str):raise APIError('Địa chỉ máy chủ không hợp lệ.')
        url=url.strip().rstrip('/')
        if any(ord(c)<32 or ord(c)==127 for c in url):raise APIError('Địa chỉ máy chủ không được chứa ký tự điều khiển.')
        try:
            parsed=urlsplit(url);port=parsed.port
        except ValueError:raise APIError('Địa chỉ máy chủ không hợp lệ.') from None
        if url and (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or
                    not (parsed.scheme=='https' or parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost'))):
            raise APIError('Dùng URL HTTPS; HTTP chỉ dành cho máy chủ thử trên localhost.')
        if token is not None and (not isinstance(token,str) or len(token)>512 or any(c.isspace() for c in token)):
            raise APIError('Mã truy cập không hợp lệ.')
        with self.lock:
            records=self.read();record=records.setdefault('_gateway',{});record['url']=url
            if remove:record.pop('token',None)
            elif token:record['token']=token
            private_write(self.path,self.crypt(json.dumps(records).encode()))
        return self.gateway(public=True)

    def save(self, provider, key=None, model=None, max_output_tokens=None, remove=False):
        if provider not in PROVIDERS:raise APIError('Nhà cung cấp không hợp lệ.')
        if key is not None and (not isinstance(key,str) or len(key)>4096 or any(c.isspace() for c in key)):
            raise APIError('Khóa API không hợp lệ; không được có khoảng trắng.')
        if model is not None and (not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}',model)):
            raise APIError('Tên model API không hợp lệ.')
        if max_output_tokens is not None and (type(max_output_tokens) is not int or not 64<=max_output_tokens<=8192):
            raise APIError('Giới hạn đầu ra từ 64 đến 8192 token.')
        with self.lock:
            records=self.read();record=records.setdefault(provider,{})
            if remove:record.pop('key',None)
            elif key:record['key']=key
            if model is not None:record['model']=model
            if max_output_tokens is not None:record['max_output_tokens']=max_output_tokens
            private_write(self.path,self.crypt(json.dumps(records).encode()))
        return self.public()


def image_parts(files):
    if len(files)>8:raise APIError('Tối đa 8 ảnh cho một yêu cầu AI.')
    result=[]
    for filename in files:
        file=Path(filename)
        mime={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png'}.get(file.suffix.lower())
        if not mime or not file.is_file() or file.stat().st_size>5_000_000:
            raise APIError('Ảnh bằng chứng không hợp lệ hoặc lớn hơn 5 MB.')
        result.append((mime,base64.b64encode(file.read_bytes()).decode('ascii')))
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise APIError('API trả chuyển hướng bất thường; không chuyển tiếp khóa sang địa chỉ khác.')


class APIClients:
    def __init__(self,data_dir,post=None):
        self.vault=KeyVault(data_dir);self.post=post or self._post

    @staticmethod
    def _post(url,headers,payload):
        request=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={**headers,'Content-Type':'application/json'},method='POST')
        try:
            # The default HTTPS handler verifies TLS and inherits proxy/CA settings. Redirects are rejected.
            opener=urllib.request.build_opener(NoRedirect())
            with opener.open(request,timeout=120) as response:
                body=response.read(4_000_001)
            if len(body)>4_000_000:raise APIError('Phản hồi API quá lớn.')
            return json.loads(body)
        except urllib.error.HTTPError as error:
            messages={400:'Yêu cầu/model không được API hỗ trợ.',401:'API không chấp nhận khóa. Kiểm tra khóa mới.',
                      403:'API từ chối quyền truy cập hoặc bị chính sách mạng chặn.',404:'Không tìm thấy model API.',
                      429:'Đã chạm hạn mức hoặc giới hạn tốc độ API.'}
            raise APIError(messages.get(error.code,f'API trả HTTP {error.code}. Không tự gửi lại.')) from None
        except APIError:raise
        except Exception:raise APIError('Không nhận được phản hồi API hợp lệ. Kiểm tra mạng/TLS; không tự gửi lại yêu cầu chưa rõ kết quả.') from None

    def complete(self,provider,prompt,files=(),model=''):
        if provider not in PROVIDERS:raise APIError('Nhà cung cấp không hợp lệ.')
        key,_=self.vault.credential(provider)
        if not key:raise APIError('Chưa cấu hình khóa '+PROVIDERS[provider]['name']+'. Mở Khóa API để nhập trên máy này.')
        options=self.vault.options(provider);model=model or options['model']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}',model):raise APIError('Tên model không hợp lệ.')
        images=image_parts(files);limit=options['max_output_tokens'];url=PROVIDERS[provider]['url']
        if provider=='chatgpt':
            content=[dict(type='input_text',text=prompt)]+[dict(type='input_image',image_url='data:'+mime+';base64,'+data,detail='low') for mime,data in images]
            payload=dict(model=model,input=[dict(role='user',content=content)],max_output_tokens=limit,store=False)
            response=self.post(url,{'Authorization':'Bearer '+key},payload)
            if response.get('status') not in (None,'completed') or response.get('error'):
                raise APIError('OpenAI chưa trả kết quả hoàn chỉnh; không tự gọi lại.')
            answer='\n'.join(c.get('text','') for item in response.get('output',[]) if item.get('type')=='message'
                             for c in item.get('content',[]) if c.get('type')=='output_text')
            usage=response.get('usage') or {};tokens=dict(input_tokens=usage.get('input_tokens',0),output_tokens=usage.get('output_tokens',0))
        elif provider=='claude':
            content=[dict(type='image',source=dict(type='base64',media_type=mime,data=data)) for mime,data in images]+[dict(type='text',text=prompt)]
            response=self.post(url,{'x-api-key':key,'anthropic-version':'2023-06-01'},dict(model=model,max_tokens=limit,messages=[dict(role='user',content=content)]))
            if response.get('stop_reason') not in ('end_turn','stop_sequence'):
                raise APIError('Claude chưa kết thúc câu trả lời hoặc chạm giới hạn token; cần kiểm tra.')
            answer='\n'.join(item.get('text','') for item in response.get('content',[]) if item.get('type')=='text')
            usage=response.get('usage') or {};tokens=dict(input_tokens=usage.get('input_tokens',0)+usage.get('cache_creation_input_tokens',0)+usage.get('cache_read_input_tokens',0),output_tokens=usage.get('output_tokens',0))
        else:
            parts=[dict(text=prompt)]+[dict(inlineData=dict(mimeType=mime,data=data)) for mime,data in images]
            response=self.post(url+quote(model.removeprefix('models/'),safe='')+':generateContent',{'x-goog-api-key':key},
                               dict(contents=[dict(role='user',parts=parts)],generationConfig=dict(maxOutputTokens=limit,responseMimeType='application/json')))
            candidates=response.get('candidates') or []
            if not candidates or candidates[0].get('finishReason')!='STOP':
                raise APIError('Gemini không trả kết quả hoàn chỉnh hoặc đã chặn nội dung; cần kiểm tra.')
            answer='\n'.join(item.get('text','') for item in candidates[0].get('content',{}).get('parts',[]) if not item.get('thought'))
            usage=response.get('usageMetadata') or {};tokens=dict(input_tokens=usage.get('promptTokenCount',0),output_tokens=usage.get('candidatesTokenCount',0)+usage.get('thoughtsTokenCount',0))
        if not isinstance(answer,str) or not answer.strip() or len(answer)>200000:
            raise APIError('API không trả nội dung văn bản hợp lệ.')
        return dict(answer=answer,model=response.get('model') or response.get('modelVersion') or model,usage=tokens)

    def relay_info(self):
        gateway=self.vault.gateway()
        if not gateway.get('url') or not gateway.get('token'):raise APIError('Chưa lưu địa chỉ máy chủ và mã truy cập.')
        request=urllib.request.Request(gateway['url']+'/v1/me',headers={'Authorization':'Bearer '+gateway['token']})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
                result=json.loads(response.read(200000))
            if not isinstance(result,dict) or 'quota' not in result:raise APIError('Máy chủ không trả thông tin hạn mức hợp lệ.')
            return result
        except urllib.error.HTTPError as error:
            raise APIError('Mã truy cập không hợp lệ hoặc bị thu hồi.' if error.code==401 else 'Không đọc được hạn mức máy chủ.') from None
        except APIError:raise
        except Exception:raise APIError('Không kết nối được máy chủ. Kiểm tra địa chỉ HTTPS và mạng.') from None

    def relay(self,provider,prompt,files=(),model='',request_id=None):
        import uuid
        gateway=self.vault.gateway()
        if not gateway.get('url') or not gateway.get('token'):
            raise APIError('Chưa cấu hình máy chủ trung gian và mã truy cập.')
        payload=dict(provider=provider,prompt=prompt,model=model,request_id=request_id or uuid.uuid4().hex,
                     images=[dict(mime=mime,data=data) for mime,data in image_parts(files)])
        result=self.post(gateway['url']+'/v1/agent',{'Authorization':'Bearer '+gateway['token']},payload)
        if not isinstance(result,dict) or not isinstance(result.get('answer'),str) or not result['answer'].strip() or len(result['answer'])>200000:
            raise APIError('Máy chủ trung gian không trả kết quả hợp lệ.')
        return result

    def test(self,provider):
        result=self.complete(provider,'Return only JSON: {"ok":true}.')
        return dict(ok=True,model=result['model'],usage=result['usage'])
