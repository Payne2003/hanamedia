"""Owner-hosted AI relay. Run behind HTTPS; provider keys never leave this process."""
import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ai_api import APIClients, PROVIDERS
from .media import image_info

SCHEMA='''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1,daily_tokens INTEGER NOT NULL,daily_requests INTEGER NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS requests(user_id TEXT NOT NULL REFERENCES users(id),request_id TEXT NOT NULL,
 payload_hash TEXT NOT NULL,day TEXT NOT NULL,status TEXT NOT NULL,reserved INTEGER NOT NULL,charged INTEGER NOT NULL,
 response TEXT NOT NULL DEFAULT '',created REAL NOT NULL,PRIMARY KEY(user_id,request_id));
'''


class GatewayError(ValueError):
    def __init__(self,status,message):self.status=status;super().__init__(message)


class GatewayStore:
    def __init__(self,folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        self.path=folder/'gateway.sqlite3'
        with self.connect() as db:db.executescript(SCHEMA)
        os.chmod(self.path,0o600)
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def create_user(self,name,daily_tokens=500000,daily_requests=100):
        if not isinstance(name,str) or not name.strip() or len(name)>120 or type(daily_tokens) is not int or not 100<=daily_tokens<=100000000 or type(daily_requests) is not int or not 1<=daily_requests<=10000:
            raise ValueError('Tên hoặc hạn mức người dùng không hợp lệ.')
        user_id=secrets.token_hex(16);token='hana_'+secrets.token_urlsafe(32)
        with self.connect() as db:db.execute('INSERT INTO users VALUES(?,?,?,1,?,?,?)',(user_id,name.strip(),hashlib.sha256(token.encode()).hexdigest(),daily_tokens,daily_requests,time.time()))
        return dict(id=user_id,name=name.strip(),token=token,daily_tokens=daily_tokens,daily_requests=daily_requests)
    def authenticate(self,token):
        if not isinstance(token,str) or not token or len(token)>512:raise GatewayError(401,'Mã truy cập không hợp lệ.')
        with self.connect() as db:row=db.execute('SELECT * FROM users WHERE token_hash=? AND enabled=1',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row:raise GatewayError(401,'Mã truy cập không hợp lệ hoặc đã bị thu hồi.')
        return dict(row)
    def revoke(self,user_id):
        with self.connect() as db:
            if db.execute('UPDATE users SET enabled=0 WHERE id=?',(user_id,)).rowcount!=1:raise ValueError('Không tìm thấy người dùng.')
    def set_limits(self,user_id,daily_tokens,daily_requests):
        if type(daily_tokens) is not int or not 100<=daily_tokens<=100000000 or type(daily_requests) is not int or not 1<=daily_requests<=10000:
            raise ValueError('Hạn mức không hợp lệ.')
        with self.connect() as db:
            if db.execute('UPDATE users SET daily_tokens=?,daily_requests=? WHERE id=?',(daily_tokens,daily_requests,user_id)).rowcount!=1:
                raise ValueError('Không tìm thấy người dùng.')

    def report(self):
        day=datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:return [dict(r) for r in db.execute('''SELECT u.id,u.name,u.enabled,u.daily_tokens,u.daily_requests,
            coalesce(sum(r.charged),0) AS used_tokens,count(r.request_id) AS used_requests
            FROM users u LEFT JOIN requests r ON r.user_id=u.id AND r.day=? GROUP BY u.id ORDER BY u.created''',(day,))]
    def quota(self,user_id):
        report=next((r for r in self.report() if r['id']==user_id),None)
        if not report:raise GatewayError(401,'Người dùng không còn tồn tại.')
        return {**report,'remaining_tokens':max(0,report['daily_tokens']-report['used_tokens']),
                'remaining_requests':max(0,report['daily_requests']-report['used_requests']),
                'day':datetime.now(timezone.utc).date().isoformat(),'timezone':'UTC'}

    def reserve(self,user_id,request_id,digest,amount):
        day=datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user=db.execute('SELECT * FROM users WHERE id=? AND enabled=1',(user_id,)).fetchone()
            if not user:raise GatewayError(401,'Mã truy cập đã bị thu hồi.')
            previous=db.execute('SELECT * FROM requests WHERE user_id=? AND request_id=?',(user_id,request_id)).fetchone()
            if previous:
                if previous['payload_hash']!=digest:raise GatewayError(409,'Mã yêu cầu đã dùng cho nội dung khác.')
                if previous['status']=='done':return json.loads(previous['response'])
                raise GatewayError(409,'Yêu cầu đã được tiếp nhận; không tự gửi lại khi kết quả chưa rõ.')
            usage=db.execute('SELECT coalesce(sum(charged),0),count(*) FROM requests WHERE user_id=? AND day=?',(user_id,day)).fetchone()
            if usage[0]+amount>user['daily_tokens'] or usage[1]>=user['daily_requests']:
                raise GatewayError(429,'Không đủ hạn mức trong ngày để dự trù yêu cầu này.')
            if db.execute("SELECT 1 FROM requests WHERE user_id=? AND status='running'",(user_id,)).fetchone():
                raise GatewayError(409,'Người dùng này đang có yêu cầu AI khác chạy.')
            db.execute("INSERT INTO requests VALUES(?,?,?,?,'running',?,?,?,?)",(user_id,request_id,digest,day,amount,amount,'',time.time()))
        return None
    def finish(self,user_id,request_id,response):
        usage=response.get('usage') or {}
        actual=sum(max(0,int(usage.get(k,0))) for k in ('input_tokens','output_tokens'))
        with self.connect() as db:
            row=db.execute('SELECT reserved FROM requests WHERE user_id=? AND request_id=?',(user_id,request_id)).fetchone()
            # Missing provider usage keeps the conservative reservation rather than reporting zero cost.
            charged=actual if actual else row['reserved']
            db.execute("UPDATE requests SET status='done',charged=?,response=? WHERE user_id=? AND request_id=?",(charged,json.dumps(response),user_id,request_id))
    def uncertain(self,user_id,request_id):
        with self.connect() as db:db.execute("UPDATE requests SET status='review' WHERE user_id=? AND request_id=?",(user_id,request_id))


class GatewayService:
    def __init__(self,folder,api=None):
        self.store=GatewayStore(folder);self.api=api or APIClients(folder);self.slots=threading.BoundedSemaphore(4)
        with self.store.connect() as db:db.execute("UPDATE requests SET status='review' WHERE status='running'")
    def complete(self,user,payload):
        if not isinstance(payload,dict) or set(payload)-{'provider','model','prompt','images','request_id'}:raise GatewayError(400,'Yêu cầu không hợp lệ.')
        provider=payload.get('provider');prompt=payload.get('prompt');request_id=payload.get('request_id')
        if provider not in PROVIDERS or not isinstance(prompt,str) or not 1<=len(prompt)<=200000 or not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id):raise GatewayError(400,'Provider, prompt hoặc mã yêu cầu không hợp lệ.')
        model=self.api.vault.options(provider)['model']
        if payload.get('model') and payload['model']!=model:raise GatewayError(400,'Model này chưa được chủ máy chủ cho phép.')
        if not self.api.vault.credential(provider)[0]:raise GatewayError(503,'Chủ máy chủ chưa cấu hình khóa cho nhà cung cấp này.')
        images=payload.get('images',[])
        if not isinstance(images,list) or len(images)>8:raise GatewayError(400,'Tối đa 8 ảnh bằng chứng.')
        decoded=[]
        for image in images:
            if not isinstance(image,dict) or set(image)!={'mime','data'} or not isinstance(image['data'],str) or len(image['data'])>1500000:raise GatewayError(400,'Ảnh không hợp lệ.')
            try:
                data=base64.b64decode(image['data'],validate=True);info=image_info(data)
            except Exception:raise GatewayError(400,'Không đọc được ảnh.') from None
            if not info or info[1] not in ('image/png','image/jpeg') or image['mime']!=info[1] or not 1<=info[2]<=1024 or not 1<=info[3]<=1024:raise GatewayError(400,'Ảnh phải là PNG/JPEG, tối đa 1024×1024.')
            decoded.append((info[0],data))
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        reserve=len(prompt.encode('utf-8'))+8192*len(images)+self.api.vault.options(provider)['max_output_tokens']
        if not self.slots.acquire(blocking=False):raise GatewayError(503,'Máy chủ đang bận. Thử lại sau.')
        try:
            cached=self.store.reserve(user['id'],request_id,digest,reserve)
            if cached is not None:return cached
            import tempfile
            try:
                with tempfile.TemporaryDirectory() as directory:
                    files=[]
                    for index,(extension,data) in enumerate(decoded):
                        target=Path(directory)/(str(index)+extension);target.write_bytes(data);files.append(str(target))
                    result=self.api.complete(provider,prompt,files,model)
                self.store.finish(user['id'],request_id,result)
                return result
            except Exception:
                self.store.uncertain(user['id'],request_id)
                raise GatewayError(502,'Không nhận được kết quả nhà cung cấp. Yêu cầu được giữ để chủ máy chủ kiểm tra, không tự gửi lại.') from None
        finally:self.slots.release()


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass  # Never log auth headers, prompts or response bodies.
    def reply(self,status,data):
        body=json.dumps(data,ensure_ascii=False).encode();self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass
    def do_GET(self):
        if self.path=='/health':return self.reply(200,{'ok':True})
        if self.path=='/v1/me':
            try:
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):raise GatewayError(401,'Thiếu mã truy cập.')
                user=self.server.service.store.authenticate(auth[7:])
                providers={name:dict(model=value['model'],configured=value['configured']) for name,value in self.server.service.api.vault.public().items()}
                return self.reply(200,dict(quota=self.server.service.store.quota(user['id']),providers=providers))
            except GatewayError as error:return self.reply(error.status,{'error':str(error)})
            except Exception:return self.reply(500,{'error':'Không đọc được hạn mức.'})
        return self.reply(404,{'error':'Không tìm thấy.'})
    def do_POST(self):
        if self.path!='/v1/agent':return self.reply(404,{'error':'Không tìm thấy.'})
        try:
            auth=self.headers.get('Authorization','')
            if not auth.startswith('Bearer '):raise GatewayError(401,'Thiếu mã truy cập.')
            user=self.server.service.store.authenticate(auth[7:])
            size=int(self.headers.get('Content-Length','0'))
            if self.headers.get_content_type()!='application/json' or not 0<size<=12000000:raise GatewayError(400,'Yêu cầu phải là JSON, tối đa 12 MB.')
            self.connection.settimeout(20)
            payload=json.loads(self.rfile.read(size))
            result=self.server.service.complete(user,payload)
            return self.reply(200,result)
        except GatewayError as error:return self.reply(error.status,{'error':str(error)})
        except (ValueError,TimeoutError):return self.reply(400,{'error':'Dữ liệu không hợp lệ.'})
        except Exception:return self.reply(500,{'error':'Lỗi máy chủ; không trả thông tin khóa hoặc nội dung yêu cầu.'})


class GatewayServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,service):self.service=service;super().__init__(address,GatewayHandler)


def main():
    parser=argparse.ArgumentParser(description='HANA MEDIA shared AI gateway; deploy behind HTTPS.')
    parser.add_argument('--data-dir',required=True);parser.add_argument('--port',type=int,default=8899)
    group=parser.add_mutually_exclusive_group();group.add_argument('--create-user');group.add_argument('--revoke');group.add_argument('--list-users',action='store_true');group.add_argument('--set-key',choices=tuple(PROVIDERS));group.add_argument('--set-limits')
    parser.add_argument('--model');parser.add_argument('--max-output-tokens',type=int,default=4096)
    parser.add_argument('--daily-tokens',type=int,default=500000);parser.add_argument('--daily-requests',type=int,default=100)
    args=parser.parse_args();store=GatewayStore(args.data_dir)
    if args.set_key:
        import getpass
        key=getpass.getpass('Nhập API key (ẩn, không ghi vào lịch sử lệnh): ')
        APIClients(args.data_dir).vault.save(args.set_key,key=key,model=args.model or PROVIDERS[args.set_key]['model'],max_output_tokens=args.max_output_tokens)
        print('Đã lưu khóa trên máy chủ.');return
    if args.create_user:
        user=store.create_user(args.create_user,args.daily_tokens,args.daily_requests)
        print(json.dumps(user,ensure_ascii=False));return
    if args.set_limits:store.set_limits(args.set_limits,args.daily_tokens,args.daily_requests);print('Đã cập nhật hạn mức.');return
    if args.revoke:store.revoke(args.revoke);print('Đã thu hồi quyền truy cập.');return
    if args.list_users:print(json.dumps(store.report(),ensure_ascii=False,indent=2));return
    server=GatewayServer(('127.0.0.1',args.port),GatewayService(args.data_dir))
    print('HANA MEDIA gateway started. Expose only through an HTTPS reverse proxy; admin commands remain local.')
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
