"""Tự động dùng Gemini trên web (gemini.google.com) bằng tài khoản Google của người dùng.

Không dùng API. Tool mở Chrome với hồ sơ riêng (data/profiles/gemini-web); người dùng đăng nhập
Google một lần trong cửa sổ đó (tool không đọc/lưu mật khẩu). Sau đó tool:
  dán yêu cầu -> gửi -> chờ Gemini viết xong -> đọc câu trả lời -> nếu chưa hết thì nhắn "viết tiếp"
  -> ghép các phần -> content.parse_answer().

Phần thao tác trang nằm trong các đoạn JavaScript bên dưới để dễ sửa khi Google đổi giao diện.
"""
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from . import content

GEMINI_URL = "https://gemini.google.com/app"
END_MARK = "HẾT DANH SÁCH"
MAX_CONTINUE = 12

# Trả về ô nhập prompt đang hiển thị, hoặc null.
JS_FIND_INPUT = r"""
const selectors = ['rich-textarea .ql-editor[contenteditable="true"]', 'div.ql-editor[contenteditable="true"]',
  '[contenteditable="true"][role="textbox"]', 'textarea[aria-label]', '[contenteditable="true"]'];
for (const selector of selectors) {
  for (const element of document.querySelectorAll(selector)) {
    const box = element.getBoundingClientRect();
    if (box.width > 80 && box.height > 10 && getComputedStyle(element).visibility !== 'hidden') return element;
  }
}
return null;
"""

# Điền text vào ô nhập (giữ xuống dòng, không bấm Enter). Trả về số ký tự đã vào ô.
JS_INSERT = r"""
// Điền bằng sự kiện DÁN (như Ctrl+V): giữ xuống dòng mà ô của Gemini không coi "\n" là phím Enter (gửi sớm).
// Trả về {length, ok}; ok = ô nhập chứa đủ đầu và cuối yêu cầu.
const [element, text] = arguments;
const squash = value => (value || '').replace(/\s+/g, '');
const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
const head = squash(lines[0]).slice(0, 40), tail = squash(lines[lines.length - 1]).slice(-40);
const filled = () => { const value = squash(element.tagName === 'TEXTAREA' ? element.value : element.innerText);
  return value.includes(head) && value.includes(tail) && value.length >= squash(text).length * 0.9; };
const result = () => ({length: (element.tagName === 'TEXTAREA' ? element.value : element.innerText).length, ok: filled()});
element.focus();
if (element.tagName === 'TEXTAREA') {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(element, text);
  element.dispatchEvent(new Event('input', {bubbles: true}));
  return result();
}
const selectAll = () => { const range = document.createRange(); range.selectNodeContents(element);
  const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); };
selectAll();
document.execCommand('delete', false);
try {
  const data = new DataTransfer();
  data.setData('text/plain', text);
  element.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
} catch (error) {}
if (filled()) return result();
// Cách 2: dựng thẳng từng đoạn <p> (trình soạn thảo Quill tự nhận thay đổi DOM).
element.innerHTML = '';
for (const line of text.split('\n')) {
  const p = document.createElement('p');
  if (line) p.textContent = line; else p.appendChild(document.createElement('br'));
  element.appendChild(p);
}
element.dispatchEvent(new Event('input', {bubbles: true}));
if (filled()) return result();
// Cách 3: gộp thành một dòng (không có ký tự xuống dòng nên không thể bị gửi sớm).
selectAll();
document.execCommand('delete', false);
document.execCommand('insertText', false, text.replace(/\s*\n+\s*/g, ' '));
return result();
"""

# Kiểm tra ô nhập đã chứa đủ yêu cầu chưa (đầu, cuối và độ dài). Trả về {length, ok}.
JS_CHECK = r"""
const [element, text] = arguments;
const squash = value => (value || '').replace(/\s+/g, '');
const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
const value = squash(element.tagName === 'TEXTAREA' ? element.value : element.innerText);
const ok = value.includes(squash(lines[0]).slice(0, 40)) && value.includes(squash(lines[lines.length - 1]).slice(-40)) &&
  value.length >= squash(text).length * 0.9;
return {length: value.length, ok};
"""

JS_CLEAR = r"""
const element = arguments[0];
element.focus();
const range = document.createRange(); range.selectNodeContents(element);
const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
document.execCommand('delete', false);
return (element.innerText || element.value || '').trim().length;
"""

# Ghi clipboard trong trang (cần quyền clipboard đã cấp qua CDP).
JS_CLIPBOARD = r"""
const [text, done] = arguments;
navigator.clipboard.writeText(text).then(() => done(true), () => done(false));
"""

# Bấm nút Gửi. Trả về true nếu bấm được.
JS_SEND = r"""
const buttons = [...document.querySelectorAll('button')].filter(b => {
  const label = (b.getAttribute('aria-label') || '') + ' ' + (b.className || '') + ' ' + (b.getAttribute('mattooltip') || '');
  return /send|gửi/i.test(label) && !/stop|dừng/i.test(label);
});
for (const button of buttons.reverse()) {
  const box = button.getBoundingClientRect();
  if (box.width && !button.disabled && button.getAttribute('aria-disabled') !== 'true') { button.click(); return true; }
}
return false;
"""

# Trạng thái trang: số câu trả lời, Gemini còn đang viết không, text câu trả lời cuối, đăng nhập, email tài khoản.
JS_STATE = r"""
// Trạng thái trang Gemini. "text" chỉ lấy phần câu trả lời (message-content), KHÔNG lấy phần "suy nghĩ" (model-thoughts).
const visible = e => { const box = e.getBoundingClientRect(); return box.width > 0 && box.height > 0; };
const responses = [...document.querySelectorAll('model-response, [data-test-id="model-response"], .model-response')]
  .filter((element, index, all) => !all.some(other => other !== element && other.contains(element)));
const last = responses[responses.length - 1];
let text = '', done = false;
if (last) {
  const bodies = [...last.querySelectorAll('message-content, .model-response-text, .markdown')]
    .filter(e => !e.closest('model-thoughts, .model-thoughts, [class*="thought"]'));
  const body = bodies[bodies.length - 1];
  text = body ? (body.innerText || '') : '';
  // Gemini hiển thị danh sách đánh số bằng <ol><li>: số thứ tự chỉ là hình vẽ, không có trong innerText.
  // Dựng lại chữ theo từng khối và gắn số cho từng <li> (tính cả thuộc tính start của <ol>).
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
  // Gemini chỉ hiện nút Sao chép / Thích / Không thích dưới câu trả lời khi đã viết xong.
  done = [...last.querySelectorAll('button, [role="button"]')].some(b => visible(b) &&
    /sao chép|copy|thích|good response|bad response|chia sẻ|share|thử lại|redo|regenerate/i.test(
      (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('mattooltip') || '') + ' ' + (b.getAttribute('data-test-id') || '')));
}
const busy = [...document.querySelectorAll('button, [role="button"]')].some(b => {
  const label = (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('mattooltip') || '') + ' ' + (b.className || '');
  return visible(b) && /stop|dừng|đang tạo|generating/i.test(label);
}) || Boolean(last && last.querySelector('[aria-busy="true"], .loading, .pending, mat-progress-bar, [class*="spinner"]'));
const account = [...document.querySelectorAll('a[aria-label*="@"], button[aria-label*="@"]')]
  .map(e => e.getAttribute('aria-label')).join(' ');
const email = (account.match(/[\w.+-]+@[\w-]+\.[\w.-]+/) || [''])[0].toLowerCase();
const signIn = location.hostname.startsWith('accounts.') ||
  [...document.querySelectorAll('a,button')].some(e => /^(đăng nhập|sign in)$/i.test((e.innerText || '').trim()));
return {count: responses.length, busy, done, text, email, signIn, host: location.hostname,
  rejected: location.hostname.startsWith('accounts.') && /rejected|disallowed/i.test(location.pathname)};
"""

# Chọn model theo tên (ví dụ "Pro", "Argon"). Trả về tên đang hiển thị trên nút chọn, hoặc ''.
JS_OPEN_MODEL_MENU = r"""
const picker = [...document.querySelectorAll('button')].find(b =>
  /bard-mode|mode-switcher|model/i.test((b.className || '') + ' ' + (b.getAttribute('data-test-id') || '')) ||
  /chế độ|mode picker|chọn mô hình|model/i.test(b.getAttribute('aria-label') || ''));
if (!picker) return null;
picker.click();
return (picker.innerText || '').trim();
"""
JS_PICK_MODEL = r"""
const wanted = arguments[0].toLowerCase();
const items = [...document.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"], mat-option, button')]
  .filter(e => e.getBoundingClientRect().width > 0);
const item = items.find(e => (e.innerText || '').toLowerCase().split('\n')[0].includes(wanted));
if (item) { item.click(); return (item.innerText || '').split('\n')[0]; }
document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
return '';
"""


class GeminiWebError(RuntimeError):
    pass


def chrome_executable():
    """Đường dẫn chrome.exe trên Windows (hoặc chrome trên máy khác)."""
    candidates = []
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    candidates += [Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"),
                   Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise GeminiWebError("Không tìm thấy Google Chrome trên máy. Cài Chrome rồi thử lại.")


def login_url(email=""):
    query = {"continue": GEMINI_URL}
    if email:
        query["Email"] = email
    return "https://accounts.google.com/ServiceLogin?" + urlencode(query)


class GeminiWebSession:
    """Một cửa sổ Chrome đăng nhập Gemini. Giữ mở giữa các lần chạy để không phải đăng nhập lại.

    Lớp con (ChatGPTWebSession) chỉ cần đổi tên, địa chỉ và các đoạn JS thao tác trang.
    """
    NAME = "Gemini"
    JS_STATE, JS_FIND_INPUT, JS_SEND = JS_STATE, JS_FIND_INPUT, JS_SEND
    JS_OPEN_MODEL_MENU, JS_PICK_MODEL = JS_OPEN_MODEL_MENU, JS_PICK_MODEL
    URL = GEMINI_URL
    LOGIN_HINT = "Bấm “Đăng nhập Google” để mở Chrome thường, đăng nhập rồi đóng cửa sổ đó, sau đó bấm Chạy lại."

    debug_path = None

    def __init__(self, profile):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service

        profile = Path(profile).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        options = webdriver.ChromeOptions()
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--lang=vi-VN")
        options.add_argument("--window-size=1280,1000")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        try:
            self.driver = webdriver.Chrome(options=options, service=Service(log_output=os.devnull))
            self.driver.set_page_load_timeout(90)
        except Exception as error:
            raise GeminiWebError(f"Không mở được Chrome cho {self.NAME}. Đóng cửa sổ {self.NAME} cũ của tool (nếu có) rồi thử lại. "
                                 f"Chi tiết: {error}") from error

    def alive(self):
        try:
            self.driver.current_url
            return True
        except Exception:
            return False

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def state(self):
        return self.driver.execute_script(self.JS_STATE) or {}

    def new_chat(self):
        self.driver.get(self.URL)

    def wait_ready(self, status, stop, email="", timeout=900):
        """Chờ ô nhập xuất hiện; nếu chưa đăng nhập thì chờ người dùng đăng nhập trong cửa sổ."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop.is_set():
                raise GeminiWebError("Đã dừng.")
            try:
                page = self.state()
                field = self.driver.execute_script(self.JS_FIND_INPUT)
            except Exception:
                page, field = {}, None
            if page.get("signIn") or page.get("rejected") or page.get("loggedOut"):
                # Google không cho nhập mật khẩu trong Chrome đang bị điều khiển tự động.
                raise GeminiWebError(f"Chưa đăng nhập {self.NAME} trong hồ sơ của tool. {self.LOGIN_HINT}")
            if field:
                if email and page.get("email") and page["email"] != email.lower():
                    raise GeminiWebError(f"Cửa sổ {self.NAME} đang đăng nhập {page['email']}, không phải {email}. "
                                         "Đổi tài khoản trong cửa sổ đó rồi bấm lại.")
                return field
            if page.get("challenge"):
                status(f"{self.NAME} đang hiện trang xác minh. Bấm ô “Xác minh bạn là con người” trong cửa sổ "
                       f"{self.NAME}; tool chờ rồi tự chạy tiếp.")
            else:
                status(f"Đang chờ {self.NAME} tải xong…")
            time.sleep(2)
        raise GeminiWebError(f"Hết thời gian chờ {self.NAME} sẵn sàng.")

    def choose_model(self, name):
        if not name:
            return ""
        try:
            if self.driver.execute_script(self.JS_OPEN_MODEL_MENU) is None:
                return ""
            time.sleep(1)
            return self.driver.execute_script(self.JS_PICK_MODEL, name) or ""
        except Exception:
            return ""

    def sent_early(self, before):
        return int(self.state().get("count") or 0) > before

    def checked(self, field, text):
        time.sleep(0.8)
        return self.driver.execute_script(JS_CHECK, field, text) or {}

    def fill(self, field, text, before):
        """Điền yêu cầu như người dùng: ưu tiên Ctrl+V thật, rồi gõ từng dòng với Shift+Enter, cuối cùng chèn bằng JS.

        Ô của Gemini coi Enter là Gửi, nên không bao giờ gửi ký tự xuống dòng như phím Enter.
        """
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        result = {}
        # 1) Dán thật: ghi clipboard rồi bấm Ctrl+V vào ô nhập.
        try:
            from urllib.parse import urlsplit
            url = urlsplit(self.driver.current_url)
            origin = f"{url.scheme}://{url.netloc}"
            self.driver.execute_cdp_cmd("Browser.grantPermissions", {
                "origin": origin, "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]})
        except Exception:
            pass
        try:
            self.driver.execute_script(JS_CLEAR, field)
            field.click()
            if self.driver.execute_async_script(JS_CLIPBOARD, text):
                ActionChains(self.driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
                result = self.checked(field, text)
        except Exception:
            result = {}
        if self.sent_early(before):
            raise GeminiWebError(f"{self.NAME} đã gửi yêu cầu trước khi tool điền xong. Thử lại.")
        if result.get("ok"):
            return result
        # 2) Gõ như bàn phím: chèn từng dòng, xuống dòng bằng Shift+Enter (Enter = Gửi trong Gemini).
        try:
            self.driver.execute_script(JS_CLEAR, field)
            field.click()
            lines = text.split("\n")
            for index, line in enumerate(lines):
                if line:
                    self.driver.execute_cdp_cmd("Input.insertText", {"text": line})
                if index < len(lines) - 1:
                    ActionChains(self.driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
                    if self.sent_early(before):  # Shift+Enter cũng bị gửi: dừng ngay, không gửi tiếp từng dòng.
                        break
            result = self.checked(field, text)
        except Exception:
            result = {}
        if self.sent_early(before):
            raise GeminiWebError(f"{self.NAME} đã gửi yêu cầu trước khi tool điền xong. Thử lại.")
        if result.get("ok"):
            return result
        # 3) Chèn bằng JS (dán giả lập / dựng đoạn văn / gộp một dòng).
        result = self.driver.execute_script(JS_INSERT, field, text) or {}
        time.sleep(1)
        if self.sent_early(before):
            raise GeminiWebError(f"{self.NAME} đã gửi yêu cầu trước khi tool điền xong. Thử lại.")
        return result

    def wait_idle(self, status, stop, timeout=900):
        """Không gửi yêu cầu mới khi Gemini còn đang viết câu trước."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop.is_set():
                raise GeminiWebError("Đã dừng.")
            if not self.state().get("busy"):
                return
            status(f"{self.NAME} còn đang viết câu trả lời trước, chờ xong rồi mới gửi tiếp…")
            time.sleep(2)
        raise GeminiWebError(f"{self.NAME} đang viết quá lâu, chưa gửi tiếp được. Xem cửa sổ {self.NAME}.")

    def dump(self, reason, answer=""):
        """Lưu trang Gemini hiện tại để chẩn đoán (không chứa mật khẩu; có nội dung cuộc trò chuyện)."""
        if not self.debug_path:
            return ""
        try:
            path = Path(self.debug_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            page = self.state()
            html = self.driver.execute_script(
                "const r=[...document.querySelectorAll('model-response, [data-message-author-role=\"assistant\"], [data-turn=\"assistant\"], [data-content-search-unit-key$=\":assistant\"]')].pop() || document.querySelector('main') ;"
                "return (r ? r.outerHTML : document.body.outerHTML).slice(0, 300000);")
            path.write_text(f"LÝ DO: {reason}\nURL: {self.driver.current_url}\nSTATE: "
                            f"{ {k: v for k, v in page.items() if k != 'text'} }\n\n--- TEXT TOOL ĐỌC ĐƯỢC ---\n"
                            f"{answer or page.get('text', '')}\n\n--- HTML CÂU TRẢ LỜI CUỐI ---\n{html}", encoding="utf-8")
            return str(path)
        except Exception:
            return ""

    def ask(self, text, status, stop, email="", timeout=1200, label="Gemini đang viết"):
        field = self.wait_ready(status, stop, email)
        self.wait_idle(status, stop)
        field = self.wait_ready(status, stop, email)
        before = int(self.state().get("count") or 0)
        written = self.fill(field, text, before)
        if not written.get("ok"):
            raise GeminiWebError(f"Không điền đủ yêu cầu vào ô nhập của {self.NAME} (mới vào {written.get('length', 0)} ký tự). "
                                 f"Giao diện {self.NAME} có thể đã thay đổi.")
        if not self.driver.execute_script(self.JS_SEND):
            from selenium.webdriver.common.keys import Keys
            field.send_keys(Keys.ENTER)
        deadline, started = time.monotonic() + timeout, time.monotonic()
        last_text, stable_since, dumped = None, time.monotonic(), time.monotonic()
        take = getattr(self, "take_now", None)
        if take:
            take.clear()
        while time.monotonic() < deadline:
            if stop.is_set():
                raise GeminiWebError("Đã dừng.")
            time.sleep(2)
            page = self.state()
            current = page.get("text") or ""
            if take and take.is_set():
                # Người dùng bấm "Lấy kết quả ngay": đọc câu trả lời hiện có, không chờ thêm.
                take.clear()
                if current.strip():
                    return current
                where = self.dump("Bấm Lấy kết quả ngay nhưng tool không đọc thấy chữ nào")
                raise GeminiWebError(f"Tool chưa đọc thấy câu trả lời của {self.NAME} trên trang."
                                     + (f" Gửi file chẩn đoán {where} cho người hỗ trợ." if where else ""))
            if time.monotonic() - dumped >= 60:  # Đang chờ lâu: lưu trạng thái trang để chẩn đoán nếu bị kẹt.
                self.dump(f"Đang chờ {self.NAME} (đã {int(time.monotonic() - started)} giây)", current)
                dumped = time.monotonic()
            if int(page.get("count") or 0) <= before:
                if time.monotonic() - started > 120:
                    where = self.dump(f"{self.NAME} chưa nhận yêu cầu sau 2 phút (không thấy tin nhắn trả lời mới)")
                    raise GeminiWebError(f"{self.NAME} chưa nhận yêu cầu sau 2 phút, hoặc tool không nhận ra câu trả lời. "
                                         f"Kiểm tra cửa sổ {self.NAME}."
                                         + (f" Gửi file chẩn đoán {where} cho người hỗ trợ." if where else ""))
                status(f"{label}… chờ {self.NAME} nhận yêu cầu")
                continue
            if current != last_text:
                last_text, stable_since = current, time.monotonic()
            steady = time.monotonic() - stable_since
            status(f"{label}… {len(current):,} ký tự".replace(",", ".") + ("" if current.strip() else f" ({self.NAME} đang suy nghĩ)"))
            # Xong khi: không còn nút Dừng, có chữ, đứng yên; và đã hiện nút Sao chép/Thích (hoặc đứng yên lâu hẳn).
            if not page.get("busy") and current.strip() and (
                    (page.get("done") and steady >= 4) or steady >= 25):
                return current
        raise GeminiWebError(f"{self.NAME} viết quá lâu (quá 20 phút). Xem cửa sổ {self.NAME} rồi thử lại.")


def finish_instruction():
    return (f"\n\nSau dòng cảnh cuối cùng của toàn bộ video, viết đúng một dòng: {END_MARK}. "
            f"Nếu câu trả lời bị giới hạn độ dài, dừng ở cuối một dòng cảnh và KHÔNG viết {END_MARK}; "
            "tôi sẽ nhắn \"viết tiếp\".")


def continue_instruction(count):
    return (f"Viết tiếp danh sách cảnh, bắt đầu từ Bối cảnh {count + 1}, cùng đúng định dạng mỗi cảnh một dòng, "
            f"không lặp lại cảnh đã viết. Khi viết xong cảnh cuối cùng của video, viết dòng {END_MARK}.")


def run_conversation(session, prompt, status, stop, email="", model="", review=False):
    """Gửi yêu cầu, tự nhắn viết tiếp đến khi đủ, trả về kết quả đã đọc (kịch bản, cảnh, vấn đề)."""
    session.new_chat()
    session.wait_ready(status, stop, email)
    if model:
        picked = session.choose_model(model)
        status(f"Đã chọn model {picked}" if picked else f"Không tự chọn được model “{model}”, dùng model đang chọn sẵn")
    answers = [session.ask(prompt + finish_instruction(), status, stop, email,
                           label="Gemini đang kiểm tra" if review else "Gemini đang xem video và viết")]
    try:
        result = content.parse_answer(answers[0])
    except ValueError:
        where = session.dump("Không đọc được cảnh nào trong câu trả lời đầu tiên", answers[0]) if hasattr(session, "dump") else ""
        raise GeminiWebError("Tool không đọc được cảnh nào trong câu trả lời của Gemini nên đã dừng (không gửi lại). "
                             + (f"Gửi file chẩn đoán {where} cho người hỗ trợ." if where else ""))
    for round_number in range(MAX_CONTINUE):
        if END_MARK.lower() in answers[-1].lower():
            break
        count = len(result["scenes"])
        more = session.ask(continue_instruction(count), status, stop, email,
                           label=f"Đã có {count} cảnh · Gemini đang viết tiếp (lần {round_number + 1})")
        answers.append(more)
        try:
            extra = content.parse_answer(more)
        except ValueError:
            break  # Gemini không viết thêm cảnh nào: coi như đã hết.
        known = {tuple(scene.values()) for scene in result["scenes"]}
        new = [scene for scene in extra["scenes"] if tuple(scene.values()) not in known]
        if not new:
            break
        result["scenes"] += new
        result["issues"] += extra["issues"]
    result["scenes"] = result["scenes"][:content.MAX_SCENES]
    result["rounds"] = len(answers)
    return result


MAX_MISSING_ROUNDS = 3


def run_english(session, lines, status, stop, email="", model="", style="", aspect="16:9"):
    """Viết prompt tiếng Anh cho từng cảnh; tự nhắn viết tiếp và hỏi lại những cảnh thiếu/chưa phải tiếng Anh."""
    total = len(lines)
    name = getattr(session, "NAME", "Gemini")
    session.new_chat()
    session.wait_ready(status, stop, email)
    if model:
        picked = session.choose_model(model)
        status(f"Đã chọn model {picked}" if picked else f"Không tự chọn được model “{model}”, dùng model đang chọn sẵn")
    rounds = 1
    answer = session.ask(content.english_request(lines, style=style, aspect=aspect), status, stop, email,
                         label=f"{name} đang viết {total} prompt tiếng Anh")
    found = content.parse_english(answer, total)
    if not found:
        where = session.dump("Không đọc được prompt nào trong câu trả lời đầu tiên", answer) if hasattr(session, "dump") else ""
        raise GeminiWebError(f"Tool không đọc được prompt nào trong câu trả lời của {name} nên đã dừng (không gửi lại). "
                             + (f"Gửi file chẩn đoán {where} cho người hỗ trợ. " if where else "")
                             + f"Xem cửa sổ {name}: nếu {name} đã trả lời đủ thì giao diện đọc kết quả cần cập nhật.")
    # Câu trả lời bị cắt: nhắn viết tiếp từ cảnh sau cùng đã có.
    for _ in range(MAX_CONTINUE):
        if content.END_EN.lower() in answer.lower() or not found or max(found) >= total:
            break
        after = max(found)
        answer = session.ask(content.continue_english(after, total), status, stop, email,
                             label=f"Đã có {len(found)}/{total} prompt · {name} đang viết tiếp")
        rounds += 1
        more = content.parse_english(answer, total, range(after + 1, total + 1))
        if not any(number > after for number in more):
            break
        for number, prompt in more.items():
            if number not in found or not content.is_english(found[number]):
                found[number] = prompt
    # Theo sơ đồ: thiếu cảnh nào thì quay lại hỏi riêng những cảnh đó.
    for attempt in range(MAX_MISSING_ROUNDS):
        missing = content.missing_english(found, total)
        if not missing:
            break
        answer = session.ask(content.english_request(lines, missing, style, aspect), status, stop, email,
                             label=f"Thiếu {len(missing)} prompt · {name} đang viết lại (lần {attempt + 1})")
        rounds += 1
        fixed = 0
        for number, prompt in content.parse_english(answer, total, missing).items():
            if number in missing and (number not in found or content.is_english(prompt)):
                found[number] = prompt
                fixed += content.is_english(prompt)
        if not fixed:
            break  # Hỏi lại mà không có cảnh nào được sửa: dừng, không gửi lặp.
    missing = content.missing_english(found, total)
    return {"english": [found.get(number, "") for number in range(1, total + 1)], "missing": missing,
            "total": total, "rounds": rounds}


class GeminiWebRunner:
    """Chạy Gemini web trong một luồng riêng; giao diện đọc trạng thái qua snapshot()."""

    def __init__(self, profile, session_factory=GeminiWebSession, name="Gemini", login_address=None,
                 login_done="thấy trang Gemini"):
        self.profile, self.factory = Path(profile), session_factory
        self.name, self.login_address, self.login_done = name, login_address or login_url, login_done
        self.lock, self.stop, self.take = threading.Lock(), threading.Event(), threading.Event()
        self.requests = queue.Queue()
        self.session = None
        self.login_process = None
        self.job = {"id": "", "busy": False, "status": f"Chưa mở {name}", "result": None, "error": ""}
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(self.job)

    def set(self, **values):
        with self.lock:
            self.job.update(values)

    def start(self, kind, prompt, email="", model="", lines=None, style=""):
        """kind: "generate"/"review" (prompt = yêu cầu viết cảnh) hoặc "english" (lines = danh sách cảnh)."""
        with self.lock:
            if self.job["busy"]:
                raise ValueError(f"{self.name} đang chạy một yêu cầu khác. Đợi xong hoặc bấm Dừng.")
            job_id = uuid.uuid4().hex
            self.job = {"id": job_id, "kind": kind, "busy": True, "status": f"Đang mở {self.name}…", "result": None, "error": ""}
        self.stop.clear()
        self.requests.put((job_id, kind, prompt, email.strip(), model.strip(), lines, style))
        return job_id

    def cancel(self):
        self.stop.set()

    def take_now(self):
        """Đọc ngay câu trả lời đang hiện trên trang (khi người dùng thấy AI đã viết xong mà tool vẫn chờ)."""
        if not self.snapshot()["busy"]:
            raise ValueError(f"{self.name} không có yêu cầu nào đang chạy.")
        self.take.set()

    def login_window_open(self):
        return bool(self.login_process and self.login_process.poll() is None)

    def open_login(self, email="", launcher=subprocess.Popen):
        """Mở Chrome THƯỜNG (không bị điều khiển) với hồ sơ của tool để người dùng tự đăng nhập Google."""
        with self.lock:
            if self.job["busy"]:
                raise ValueError(f"Đang chạy {self.name}. Bấm Dừng trước khi đăng nhập lại.")
        if self.login_window_open():
            raise ValueError("Cửa sổ đăng nhập đang mở. Đăng nhập xong thì đóng hẳn cửa sổ đó.")
        if self.session:  # Hai Chrome không dùng chung một hồ sơ được.
            self.session.close()
            self.session = None
        self.profile.mkdir(parents=True, exist_ok=True)
        url = self.login_address(email)
        self.login_process = launcher([chrome_executable(), f"--user-data-dir={self.profile}",
                                       "--profile-directory=Default", "--no-first-run", "--new-window", url])
        self.set(status=f"Đã mở Chrome để đăng nhập {self.name}. Đăng nhập xong, {self.login_done} thì đóng hẳn cửa sổ đó, rồi bấm Chạy.",
                 error="")

    def worker(self):
        while True:
            item = self.requests.get()
            if item is None:
                return
            job_id, kind, prompt, email, model, lines, style = item
            status = lambda text: self.set(status=text)
            try:
                if self.login_window_open():
                    raise GeminiWebError("Cửa sổ Chrome đăng nhập vẫn đang mở. Đóng hẳn cửa sổ đó rồi bấm Chạy lại.")
                if not self.session or not self.session.alive():
                    if self.session:
                        self.session.close()
                    status(f"Đang mở Chrome cho {self.name}…")
                    self.session = self.factory(self.profile)
                    try:
                        self.session.debug_path = self.profile.parent / f"{self.name.lower()}-debug.txt"
                        self.session.take_now = self.take
                    except Exception:
                        pass
                if kind == "english":
                    style, aspect = style if isinstance(style, tuple) else (style, "16:9")
                    result = run_english(self.session, lines, status, self.stop, email, model, style, aspect)
                    done = result["total"] - len(result["missing"])
                    self.set(busy=False, result=result,
                             status=f"Xong: {done}/{result['total']} prompt tiếng Anh sau {result['rounds']} lượt"
                                    + (f" · còn thiếu {len(result['missing'])}" if result["missing"] else ""))
                    continue
                result = run_conversation(self.session, prompt, status, self.stop, email, model, kind == "review")
                result["prompts"] = content.format_prompts(result["scenes"])
                self.set(busy=False, result=result,
                         status=f"Xong: {len(result['scenes'])} cảnh sau {result['rounds']} lượt trả lời của {self.name}")
            except (GeminiWebError, ValueError) as error:
                self.set(busy=False, error=str(error), status=str(error))
            except Exception as error:  # Selenium/Chrome lỗi bất ngờ: đóng phiên để lần sau mở lại.
                if self.session:
                    self.session.close()
                self.session = None
                self.set(busy=False, error=f"Lỗi điều khiển Chrome: {error}", status="Lỗi điều khiển Chrome")

    def close(self):
        self.stop.set()
        self.requests.put(None)
        if self.session:
            self.session.close()
