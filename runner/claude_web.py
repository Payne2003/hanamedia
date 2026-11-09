"""Claude browser adapter for production agents; separate user-owned Chrome profile.

Selectors are conservative and must be checked on the user's authenticated site.
No login credentials are read or stored by this adapter.
"""
from .gemini_web import GeminiWebRunner, GeminiWebSession
from .chatgpt_web import JS_PICK_MODEL

JS_FIND_INPUT = r"""
for(const selector of ['.ProseMirror[contenteditable="true"]','[contenteditable="true"][role="textbox"]','textarea[placeholder]']) {
 for(const e of document.querySelectorAll(selector)) {
  const r=e.getBoundingClientRect();if(r.width>80&&r.height>10&&getComputedStyle(e).visibility!=='hidden')return e;
 }
}return null;
"""
JS_SEND = r"""
for(const b of document.querySelectorAll('button[aria-label],button[data-testid]')) {
 const label=(b.getAttribute('aria-label')||'')+' '+(b.getAttribute('data-testid')||'');
 if(/send message|send-message|gửi tin nhắn/i.test(label)&&!b.disabled&&b.getBoundingClientRect().width) {b.click();return true;}
}return false;
"""
JS_STATE = r"""
const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden';};
const candidates=[...document.querySelectorAll('[data-is-streaming],.font-claude-response,[data-testid="assistant-message"]')];
const responses=candidates.filter(e=>!candidates.some(other=>other!==e&&other.contains(e)));
const last=responses[responses.length-1];
const busy=[...document.querySelectorAll('button[aria-label]')].some(b=>visible(b)&&/stop response|stop generating|dừng/i.test(b.getAttribute('aria-label')||''))||Boolean(last&&last.getAttribute('data-is-streaming')==='true');
const challenge=/verify you are human|just a moment|xác minh bạn/i.test(document.title+' '+document.body.innerText.slice(0,600))||[...document.querySelectorAll('iframe[src*="challenges.cloudflare"],iframe[src*="recaptcha"],#challenge-form')].some(visible);
const done=Boolean(last&&!busy&&(last.getAttribute('data-is-streaming')==='false'||[...last.parentElement.querySelectorAll('button[aria-label]')].some(b=>/copy|sao chép|retry/i.test(b.getAttribute('aria-label')||''))));
return {count:responses.length,text:last?last.innerText:'',busy,done,email:'',host:location.hostname,
 signIn:/\/login|\/signup/.test(location.pathname),loggedOut:false,challenge,rejected:false};
"""
JS_OPEN_MODEL_MENU = r"""
const b=document.querySelector('button[data-testid="model-selector-dropdown"],button[aria-label*="model" i]');
if(!b)return null;b.click();return b.innerText||'';
"""


class ClaudeWebSession(GeminiWebSession):
    NAME='Claude'
    URL='https://claude.ai/new'
    LOGIN_HINT='Bấm Đăng nhập Claude trong bảng agent, đăng nhập rồi đóng cửa sổ đó trước khi chạy lại.'
    JS_STATE, JS_FIND_INPUT, JS_SEND=JS_STATE, JS_FIND_INPUT, JS_SEND
    JS_OPEN_MODEL_MENU, JS_PICK_MODEL=JS_OPEN_MODEL_MENU, JS_PICK_MODEL


def claude_runner(profile, session_factory=ClaudeWebSession):
    return GeminiWebRunner(profile,session_factory,name='Claude',login_address=lambda email='':'https://claude.ai/login',
                           login_done='thấy ô nhắn tin Claude')
