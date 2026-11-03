"""Tự động dùng ChatGPT trên web (chatgpt.com) bằng tài khoản ChatGPT của người dùng.

Dùng chung toàn bộ cách làm với Gemini (runner/gemini_web.py): Chrome hồ sơ riêng (data/profiles/chatgpt-web),
đăng nhập một lần bằng Chrome thường, dán yêu cầu bằng Ctrl+V, chờ viết xong, đọc câu trả lời, hỏi lại cảnh thiếu.
Chỉ khác các đoạn JavaScript nhận diện trang ChatGPT bên dưới.

Nếu ChatGPT hiện trang xác minh ("Verify you are human"), tool chỉ chờ người dùng tự hoàn tất trong cửa sổ đó.
"""
from .gemini_web import GeminiWebRunner, GeminiWebSession

CHATGPT_URL = "https://chatgpt.com/"


def chatgpt_login_url(email=""):
    return "https://chatgpt.com/auth/login"


# Ô nhập: ProseMirror #prompt-textarea (bản cũ là <textarea>).
JS_FIND_INPUT = r"""
const selectors = ['#prompt-textarea[contenteditable="true"]', 'div#prompt-textarea', 'textarea#prompt-textarea',
  '[contenteditable="true"][data-virtualkeyboard="true"]', 'form [contenteditable="true"]', 'textarea[name="prompt-textarea"]'];
for (const selector of selectors) {
  for (const element of document.querySelectorAll(selector)) {
    const box = element.getBoundingClientRect();
    if (box.width > 80 && box.height > 10 && getComputedStyle(element).visibility !== 'hidden') return element;
  }
}
return null;
"""

JS_SEND = r"""
const candidates = [...document.querySelectorAll('button[data-testid="send-button"], #composer-submit-button, button[aria-label]')]
  .filter(b => {
    const label = (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-testid') || '');
    return /send|gửi/i.test(label) && !/stop|dừng/i.test(label);
  });
for (const button of candidates) {
  const box = button.getBoundingClientRect();
  if (box.width && !button.disabled && button.getAttribute('aria-disabled') !== 'true') { button.click(); return true; }
}
return false;
"""

# Trạng thái trang ChatGPT. "text" chỉ lấy phần .markdown của tin nhắn trợ lý cuối (bỏ phần "Thought for…").
JS_STATE = r"""
const visible = e => { const box = e.getBoundingClientRect(); return box.width > 0 && box.height > 0; };
const responses = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
  .filter((element, index, all) => !all.some(other => other !== element && other.contains(element)));
const last = responses[responses.length - 1];
let text = '', done = false;
if (last) {
  const bodies = [...last.querySelectorAll('.markdown, [class*="markdown"]')]
    .filter(e => !e.closest('[class*="thought"], [class*="reasoning"], details'));
  const body = bodies[bodies.length - 1];
  text = body ? (body.innerText || '') : '';
  // ChatGPT hiển thị danh sách bằng <ol><li>: số thứ tự không có trong innerText -> dựng lại và gắn số.
  if (body && body.querySelector('li')) {
    const lines = [];
    for (const block of body.querySelectorAll('li, p, h1, h2, h3, h4, h5, h6, pre')) {
      if (block.tagName !== 'LI' && block.closest('li')) continue;
      const line = (block.innerText || '').replace(/\s*\n\s*/g, ' ').trim();
      if (block.tagName === 'LI') {
        const list = block.parentElement;
        const items = [...list.children].filter(child => child.tagName === 'LI');
        const start = list.tagName === 'OL' ? parseInt(list.getAttribute('start') || '1', 10) || 1 : 0;
        const tail = line.match(/\s*(END OF LIST|HẾT DANH SÁCH)\s*$/i);
        const clean = tail ? line.slice(0, tail.index).trim() : line;
        lines.push(start ? `${start + items.indexOf(block)}. ${clean}` : clean);
        if (tail) lines.push(tail[1]);
      } else if (line) {
        lines.push(line);
      }
    }
    if (lines.length) text = lines.join('\n');
  }
  // Viết xong: hiện các nút Sao chép / Thích / Không thích / Đọc to dưới câu trả lời.
  const turn = last.closest('article, [data-testid^="conversation-turn"]') || last.parentElement || last;
  done = [...turn.querySelectorAll('button')].some(b => visible(b) &&
    /copy|sao chép|good response|bad response|thích|read aloud|đọc to/i.test(
      (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-testid') || '')));
}
const busy = [...document.querySelectorAll('button')].some(b => {
  const label = (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-testid') || '');
  return visible(b) && /stop-button|stop streaming|stop generating|dừng/i.test(label);
}) || Boolean(last && last.querySelector('.result-streaming, [class*="streaming-animation"]'));
const host = location.hostname;
const challenge = /just a moment|verify you are human|xác minh bạn là con người/i.test(document.title + ' ' +
  ((document.body && document.body.innerText) || '').slice(0, 600)) || Boolean(document.querySelector('iframe[src*="challenges.cloudflare"]'));
const signIn = /^auth\.|^auth0\./.test(host) || /\/auth\/login|\/log-in/.test(location.pathname);
const loggedOut = Boolean(document.querySelector('[data-testid="login-button"]'));
return {count: responses.length, busy, done, text, email: '', signIn, loggedOut, challenge, host, rejected: false};
"""

JS_OPEN_MODEL_MENU = r"""
const picker = document.querySelector('[data-testid="model-switcher-dropdown-button"], button[aria-label*="Model"], button[aria-label*="mô hình"]');
if (!picker) return null;
picker.click();
return (picker.innerText || '').trim();
"""

JS_PICK_MODEL = r"""
const wanted = arguments[0].toLowerCase();
const items = [...document.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"]')]
  .filter(e => e.getBoundingClientRect().width > 0);
const item = items.find(e => (e.innerText || '').toLowerCase().split('\n')[0].includes(wanted));
if (item) { item.click(); return (item.innerText || '').split('\n')[0]; }
document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
return '';
"""


class ChatGPTWebSession(GeminiWebSession):
    NAME = "ChatGPT"
    URL = CHATGPT_URL
    LOGIN_HINT = "Bấm “Đăng nhập ChatGPT” để mở Chrome thường, đăng nhập rồi đóng cửa sổ đó, sau đó bấm Chạy lại."
    JS_STATE, JS_FIND_INPUT, JS_SEND = JS_STATE, JS_FIND_INPUT, JS_SEND
    JS_OPEN_MODEL_MENU, JS_PICK_MODEL = JS_OPEN_MODEL_MENU, JS_PICK_MODEL


def chatgpt_runner(profile, session_factory=ChatGPTWebSession):
    return GeminiWebRunner(profile, session_factory, name="ChatGPT", login_address=chatgpt_login_url,
                           login_done="thấy ô nhắn tin của ChatGPT")
