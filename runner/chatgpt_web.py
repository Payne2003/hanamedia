"""Tự động dùng ChatGPT trên web (chatgpt.com) bằng tài khoản ChatGPT của người dùng.

Dùng chung toàn bộ cách làm với Gemini (runner/gemini_web.py): Chrome hồ sơ riêng (data/profiles/chatgpt-web),
đăng nhập một lần bằng Chrome thường, dán yêu cầu bằng Ctrl+V, chờ viết xong, đọc câu trả lời, hỏi lại cảnh thiếu.
Chỉ khác các đoạn JavaScript nhận diện trang ChatGPT bên dưới.

Nếu ChatGPT hiện trang xác minh ("Verify you are human"), tool chỉ chờ người dùng tự hoàn tất trong cửa sổ đó.
"""
import re

from .gemini_web import GeminiWebRunner, GeminiWebSession

CHATGPT_URL = "https://chatgpt.com/"


def chatgpt_login_url(email=""):
    return "https://chatgpt.com/auth/login"


# Ô nhập: ProseMirror #prompt-textarea (bản cũ là <textarea>).
JS_FIND_INPUT = r"""
const selectors = ['#prompt-textarea[contenteditable="true"]', 'div#prompt-textarea', 'textarea#prompt-textarea',
  'form[data-chatgpt-composer] .ProseMirror[contenteditable="true"]', '.ProseMirror[contenteditable="true"][role="textbox"]',
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
// Tin nhắn trợ lý. Giao diện ChatGPT 2026: khối [data-content-search-unit-key$=":assistant"] (có h4 data-conversation-role);
// bản cũ: [data-message-author-role="assistant"] hoặc article[data-turn="assistant"].
const outermost = list => list.filter((element, index, all) => !all.some(other => other !== element && other.contains(element)));
let responses = outermost([...document.querySelectorAll('[data-content-search-unit-key$=":assistant"], [data-chatgpt-search-unit-key$=":assistant"]')]);
if (!responses.length) responses = outermost([...document.querySelectorAll('[data-message-author-role="assistant"]')]);
if (!responses.length) responses = [...document.querySelectorAll('h4[data-conversation-role="assistant"]')].map(h => h.parentElement);
if (!responses.length) responses = [...document.querySelectorAll('[data-turn="assistant"], article[data-testid^="conversation-turn"]:has(.markdown)')];
const last = responses[responses.length - 1];
// Lượt trả lời (chứa cả hàng nút Sao chép/Đọc to...) và trạng thái của nó: data-talvt-turn-state="complete" khi viết xong.
const turn = last ? (last.closest('[data-talvt-turn-state]') || last.closest('article, [data-testid^="conversation-turn"]') || last.parentElement || last) : null;
const turnState = turn ? (turn.getAttribute('data-talvt-turn-state') || '') : '';
let text = '', done = false;
if (last) {
  const bodies = [...last.querySelectorAll('[data-markdown-text-style="assistant-message"], .markdown, [class*="markdown"]')]
    .filter(e => !e.closest('[class*="thought"], [class*="reasoning"], details'));
  // Nếu mọi khối đều nằm trong vùng bị coi là "suy nghĩ" thì vẫn lấy khối cuối; cuối cùng lấy cả tin nhắn.
  const all = [...last.querySelectorAll('[data-markdown-text-style="assistant-message"], .markdown, [class*="markdown"]')];
  const body = outermost(bodies).pop() || outermost(all).pop() || null;
  text = body ? (body.innerText || '') : (last.innerText || '').replace(/^\s*ChatGPT đã nói:\s*/i, '');
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
  // Viết xong: lượt có trạng thái "complete", hoặc đã hiện các nút Sao chép / Đọc to / Tạo lại dưới câu trả lời.
  done = turnState === 'complete' || [...turn.querySelectorAll('button')].some(b => visible(b) &&
    !b.closest('[data-chatgpt-search-unit-key$=":user"], [data-content-search-unit-key$=":user"], [data-user-message-bubble]') &&
    /copy|sao chép|good response|bad response|đánh giá|read aloud|đọc to|tạo lại/i.test(
      (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-testid') || '')));
}
// Đang viết: chỉ tính nút Dừng của ô nhắn tin (không tính các nút "Dừng đọc"... khác trên trang).
const busy = (turnState !== '' && turnState !== 'complete') ||
  [...document.querySelectorAll('button[data-testid="stop-button"], #composer-submit-button, form button[aria-label]')].some(b => {
    const label = (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-testid') || '');
    return visible(b) && /stop-button|stop streaming|stop generating|dừng phát|dừng tạo|dừng trả lời|ngừng/i.test(label);
  }) || Boolean(last && last.querySelector('.result-streaming'));
const host = location.hostname;
// Trang xác minh Cloudflare: tiêu đề "Just a moment…" / "Chờ một chút…"; ô xác minh nằm trong iframe riêng.
// Xác minh "tôi không phải người máy": Cloudflare (trang riêng hoặc khung Turnstile), reCAPTCHA, hCaptcha, Arkose.
// Chỉ tính khung ĐANG HIỆN; ô ẩn còn sót lại sau khi đã xác minh không tính.
const shown = e => { const r = e.getBoundingClientRect(); const st = getComputedStyle(e);
  return r.width > 30 && r.height > 30 && st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0'; };
const challenge = /just a moment|chờ một chút|verify you are human|xác minh bạn là con người/i.test(document.title + ' ' +
  ((document.body && document.body.innerText) || '').slice(0, 600)) ||
  [...document.querySelectorAll('iframe[src*="challenges.cloudflare"], iframe[src*="recaptcha"], iframe[src*="hcaptcha"], ' +
    'iframe[src*="arkoselabs"], iframe[src*="funcaptcha"], iframe[title*="challenge" i], iframe[title*="reCAPTCHA" i], #challenge-form')].some(shown);
const signIn = /^auth\.|^auth0\./.test(host) || /\/auth\/login|\/log-in/.test(location.pathname);
const loggedOut = Boolean(document.querySelector('[data-testid="login-button"]'));
return {count: responses.length, busy, done, text, email: '', signIn, loggedOut, challenge, host, rejected: false};
"""

JS_OPEN_MODEL_MENU = r"""
const picker = document.querySelector('[data-testid="model-switcher-dropdown-button"], button[data-composer-navigation-target="reasoning"], button[aria-label*="Model"], button[aria-label*="mô hình"]');
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


# Thông báo hết lượt của gói Plus (vd hết lượt Thinking trong tuần) trên chatgpt.com, tiếng Anh/Việt.
LIMIT_NOTICE = re.compile(r"(?:reached|hit|used up)\s+(?:your|the)\s+(?:[\w.-]+\s+){0,4}(?:limit|cap)|usage (?:limit|cap)|"
                          r"limit (?:for|on) (?:\w+[\s.-]*){0,3}thinking|đã (?:đạt|hết|dùng hết)\s+(?:\S+\s+){0,4}(?:giới hạn|lượt)|"
                          r"hết lượt", re.IGNORECASE)
JS_PAGE_TAIL = "return ((document.body && document.body.innerText) || '').slice(-4000);"


class ChatGPTWebSession(GeminiWebSession):
    NAME = "ChatGPT"
    URL = CHATGPT_URL
    LOGIN_HINT = "Bấm “Đăng nhập ChatGPT” để mở Chrome thường, đăng nhập rồi đóng cửa sổ đó, sau đó bấm Chạy lại."
    JS_STATE, JS_FIND_INPUT, JS_SEND = JS_STATE, JS_FIND_INPUT, JS_SEND
    JS_OPEN_MODEL_MENU, JS_PICK_MODEL = JS_OPEN_MODEL_MENU, JS_PICK_MODEL
    FALLBACK_MODEL = "Auto"

    def limit_shown(self):
        try:
            return bool(LIMIT_NOTICE.search(self.driver.execute_script(JS_PAGE_TAIL) or ""))
        except Exception:
            return False

    def choose_model(self, name):
        """Chọn chế độ trên chatgpt.com (Thinking/Auto/Instant). Thinking đã hết lượt thì chọn Auto."""
        self.wanted_model = name
        picked = super().choose_model(name)
        if name and name.lower() != self.FALLBACK_MODEL.lower() and self.limit_shown():
            auto = super().choose_model(self.FALLBACK_MODEL)
            return f"{auto or self.FALLBACK_MODEL} (hết lượt {name})"
        return picked

    def ask(self, text, status, stop, email="", timeout=1200, label="ChatGPT đang viết"):
        """Hết lượt Thinking giữa chừng (ChatGPT báo giới hạn thay vì trả lời): chuyển sang Auto rồi gửi lại một lần
        (tin nhắn bị chặn vì hết lượt chưa được xử lý nên không bị trùng)."""
        wanted = getattr(self, "wanted_model", "") or ""
        try:
            answer = super().ask(text, status, stop, email, timeout, label)
        except Exception:
            if not (wanted.lower() != self.FALLBACK_MODEL.lower() and self.limit_shown()):
                raise
            answer = None
        if answer is not None and not (len(answer) < 800 and LIMIT_NOTICE.search(answer)):
            return answer
        status(f"ChatGPT báo hết lượt {wanted or 'của chế độ đang chọn'} · chuyển sang {self.FALLBACK_MODEL} và gửi lại")
        self.wanted_model = self.FALLBACK_MODEL
        GeminiWebSession.choose_model(self, self.FALLBACK_MODEL)
        return super().ask(text, status, stop, email, timeout, label)


def chatgpt_runner(profile, session_factory=ChatGPTWebSession):
    return GeminiWebRunner(profile, session_factory, name="ChatGPT", login_address=chatgpt_login_url,
                           login_done="thấy ô nhắn tin của ChatGPT")
