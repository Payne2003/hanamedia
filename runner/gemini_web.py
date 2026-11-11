"""Tự động dùng Gemini trên web (gemini.google.com) bằng tài khoản Google của người dùng.

Không dùng API. Tool mở Chrome với hồ sơ riêng (data/profiles/gemini-web); người dùng đăng nhập
Google một lần trong cửa sổ đó (tool không đọc/lưu mật khẩu). Sau đó tool:
  dán yêu cầu -> gửi -> chờ Gemini viết xong -> đọc câu trả lời -> nếu chưa hết thì nhắn "viết tiếp"
  -> ghép các phần -> content.parse_answer().

Phần thao tác trang nằm trong các đoạn JavaScript bên dưới để dễ sửa khi Google đổi giao diện.
"""
import os
import re
import queue
import subprocess
import threading
import sys
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
  const wanted = squash(text).length;
  return value.includes(head) && value.includes(tail) && value.length >= wanted * 0.9 && value.length <= wanted * 1.05 + 30; };
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
const wanted = squash(text).length;
// Không được thừa chữ: ô còn sót yêu cầu cũ (lần chạy trước) thì KHÔNG coi là đã điền đúng.
const ok = value.includes(squash(lines[0]).slice(0, 40)) && value.includes(squash(lines[lines.length - 1]).slice(-40)) &&
  value.length >= wanted * 0.9 && value.length <= wanted * 1.05 + 30;
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
  // Chỉ bỏ khung "suy nghĩ" thật (model-thoughts / thinking-overlay). KHÔNG lọc theo chữ "thought" trong class:
  // câu trả lời thật nằm trong khung có class "has-thoughts" khi Gemini có suy nghĩ.
  const all = [...last.querySelectorAll('message-content, .model-response-text, .markdown')];
  const bodies = all.filter(e => !e.closest('model-thoughts, .model-thoughts, thinking-overlay, [data-test-id*="thought"]'));
  const body = bodies[bodies.length - 1] || all[all.length - 1];
  text = body ? (body.innerText || '') : '';
  // Gemini hiển thị danh sách đánh số bằng <ol><li>: số thứ tự chỉ là hình vẽ, không có trong innerText.
  // Dựng lại chữ theo từng khối và gắn số cho từng <li> (tính cả thuộc tính start của <ol>).
  // Đoạn <p> của Gemini chứa dấu xuống dòng thật bên trong các <span> (mỗi "Bối cảnh N" một dòng) mà innerText có thể
  // gộp thành một dòng: lấy textContent để giữ nguyên các dòng.
  if (body && body.querySelector('li, p')) {
    const lines = [];
    for (const block of body.querySelectorAll('li, p, h1, h2, h3, h4, h5, h6, pre')) {
      if (block.tagName !== 'LI' && block.closest('li')) continue;
      if (block.tagName !== 'LI') {
        for (const part of (block.textContent || '').split('\n')) if (part.trim()) lines.push(part.replace(/\s+/g, ' ').trim());
        continue;
      }
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
const stopping = [...document.querySelectorAll('button, [role="button"]')].some(b => {
  const label = (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('mattooltip') || '') + ' ' + (b.className || '');
  return visible(b) && /stop|dừng|đang tạo|generating/i.test(label);
});
// Giao diện mới: khung "đang suy nghĩ" (thinking-overlay) có chấm động; chữ bên trong cho biết Gemini đang làm gì.
const overlay = last && [...last.querySelectorAll('thinking-overlay, model-thoughts, [data-test-id="thinking-overlay-content"]')].find(visible);
const thinking = overlay ? (overlay.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160) : '';
const marker = Boolean(last && last.querySelector('[aria-busy="true"], .loading, .pending, mat-progress-bar, [class*="spinner"], thinking-dots-animation'));
// Nút Sao chép/Thích chỉ hiện khi đã viết xong: khi đó dấu "aria-busy" còn sót lại không được giữ tool chờ mãi.
const busy = stopping || (marker && !done);
const account = [...document.querySelectorAll('a[aria-label*="@"], button[aria-label*="@"]')]
  .map(e => e.getAttribute('aria-label')).join(' ');
const email = (account.match(/[\w.+-]+@[\w-]+\.[\w.-]+/) || [''])[0].toLowerCase();
const signIn = location.hostname.startsWith('accounts.') ||
  [...document.querySelectorAll('a,button')].some(e => /^(đăng nhập|sign in)$/i.test((e.innerText || '').trim()));
return {count: responses.length, busy, done, text, thinking, email, signIn, host: location.hostname,
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
# Phím tắt dán / chọn tất cả: Cmd trên macOS, Ctrl trên Windows (giá trị phím của Selenium).
MOD_KEY = "\ue03d" if sys.platform == "darwin" else "\ue009"


class GeminiWebError(RuntimeError):
    pass


# File video Gemini web nhận (mkv không được Gemini hỗ trợ). Gemini giới hạn khoảng 2 GB mỗi file.
VIDEO_TYPES = (".mp4", ".mov", ".webm", ".avi", ".mpeg", ".mpg")
VIDEO_LIMIT = 2 * 1024 ** 3


def upload_copy(path, folder):
    """Bản sao tên ngắn của video gốc để đính kèm (tên dài/ký tự lạ có thể bị Gemini cắt, tool không xác nhận được)."""
    import shutil
    source = Path(path)
    folder = Path(folder)
    shutil.rmtree(folder, ignore_errors=True)  # chỉ giữ video của lần chạy này
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("video-goc-" + uuid.uuid4().hex[:6] + source.suffix.lower())
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def start_fresh(session, status, stop, email=""):
    """Mở cuộc trò chuyện MỚI và chắc chắn trang trống (không còn câu trả lời của lần chạy trước)."""
    for attempt in range(3):
        session.new_chat()
        session.wait_ready(status, stop, email)
        try:
            count = int((session.state() or {}).get("count") or 0)
        except Exception:
            return  # phiên không đọc được trạng thái (bản thử): coi như trang mới
        if count == 0:
            return
        status(f"Trang vẫn hiện cuộc trò chuyện cũ · mở lại cuộc trò chuyện mới (lần {attempt + 1})")
        time.sleep(2)
    raise GeminiWebError("Không mở được cuộc trò chuyện mới (trang vẫn hiện cuộc trò chuyện cũ). Chưa gửi để không lẫn kết quả cũ.")


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
        # AI viết lâu (5–20 phút) khi cửa sổ nằm sau: Chrome không được giảm tốc hay tự xóa tab.
        for flag in ("--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                     "--disable-backgrounding-occluded-windows",
                     "--disable-features=IntensiveWakeUpThrottling,CalculateNativeWinOcclusion,HeuristicMemorySaver,"
                     "BatterySaverModeAvailable"):
            options.add_argument(flag)
        options.add_experimental_option("prefs", {"performance_tuning": {"high_efficiency_mode": {"state": 0},
                                                                         "battery_saver_mode": {"state": 0}}})
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        from .chrome_profile import free_profile, short_error
        for attempt in range(2):
            try:
                self.driver = webdriver.Chrome(options=options, service=Service(log_output=os.devnull))
                self.driver.set_page_load_timeout(90)
                break
            except Exception as error:
                # Lần đầu lỗi: thường do Chrome cũ của chính tool còn giữ hồ sơ. Đóng nó rồi thử lại một lần.
                if attempt == 0:
                    free_profile(profile)
                    continue
                raise GeminiWebError(f"Không mở được Chrome cho {self.NAME} (đã thử đóng Chrome cũ đang giữ hồ sơ). "
                                     f"Đóng hết cửa sổ Chrome của tool, khởi động lại app rồi thử lại. "
                                     f"Chi tiết: {short_error(error)}") from error

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

    def report_account(self, logged_in, email):
        callback = getattr(self, "account_seen", None)
        if callback:
            try:
                callback(logged_in, email)
            except Exception:
                pass

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
                self.report_account(False, "")
                # Google không cho nhập mật khẩu trong Chrome đang bị điều khiển tự động.
                raise GeminiWebError(f"Chưa đăng nhập {self.NAME} trong hồ sơ của tool. {self.LOGIN_HINT}")
            if page.get("challenge"):
                status(f"{self.NAME} đang hiện ô xác minh (CAPTCHA). Bấm xác minh trong cửa sổ {self.NAME}; "
                       "tool chờ, xác minh xong sẽ tự dán và gửi tiếp.")
                time.sleep(2)
                continue
            if field:
                self.report_account(True, page.get("email") or "")
                if email and page.get("email") and page["email"] != email.lower():
                    raise GeminiWebError(f"Cửa sổ {self.NAME} đang đăng nhập {page['email']}, không phải {email}. "
                                         "Đổi tài khoản trong cửa sổ đó rồi bấm lại.")
                return field
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
        self.clear_field(field)
        try:
            field.click()
            if self.driver.execute_async_script(JS_CLIPBOARD, text):
                ActionChains(self.driver).key_down(MOD_KEY).send_keys("v").key_up(MOD_KEY).perform()
                result = self.checked(field, text)
        except Exception:
            result = {}
        if self.sent_early(before):
            raise GeminiWebError(f"{self.NAME} đã gửi yêu cầu trước khi tool điền xong. Thử lại.")
        if result.get("ok"):
            return result
        # 2) Gõ như bàn phím: chèn từng dòng, xuống dòng bằng Shift+Enter (Enter = Gửi trong Gemini).
        self.clear_field(field)
        try:
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

    def clear_field(self, field):
        """Xóa sạch ô nhập trước khi dán: chữ cũ còn sót (yêu cầu lần trước, bản nháp Gemini tự khôi phục) không được
        dính vào yêu cầu mới. Không xóa được thì dừng, không gửi."""
        from selenium.webdriver.common.keys import Keys
        for attempt in range(3):
            try:
                left = self.driver.execute_script(JS_CLEAR, field)
            except Exception:
                left = None
            if left == 0:
                return
            try:
                field.click()
                field.send_keys(MOD_KEY + "a")
                field.send_keys(Keys.DELETE)
            except Exception:
                pass
            time.sleep(.3)
        raise GeminiWebError(f"Ô nhập của {self.NAME} còn chữ cũ mà tool không xóa được; chưa gửi để không lẫn yêu cầu cũ. "
                             f"Xóa ô nhập trong cửa sổ {self.NAME} rồi chạy lại.")

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

    def submit(self, field, text, before):
        """Dán yêu cầu vào ô nhập (nếu ô chưa có đúng nội dung đó) rồi bấm Gửi."""
        try:
            already = (self.driver.execute_script(JS_CHECK, field, text) or {}).get("ok")
        except Exception:
            already = False
        if not already:
            written = self.fill(field, text, before)
            if not written.get("ok"):
                raise GeminiWebError(f"Không điền đủ yêu cầu vào ô nhập của {self.NAME} (mới vào {written.get('length', 0)} ký tự). "
                                     f"Giao diện {self.NAME} có thể đã thay đổi.")
        if not self.driver.execute_script(self.JS_SEND):
            from selenium.webdriver.common.keys import Keys
            field.send_keys(Keys.ENTER)

    def attach_images(self, files, status, stop, email=""):
        """Attach local evidence images (hoặc 1 file video gốc). Fail closed if the current page cannot confirm uploads."""
        files = [Path(file).resolve() for file in files]
        videos = [f for f in files if f.suffix.lower() in VIDEO_TYPES]
        images = [f for f in files if f not in videos]
        if (len(videos) > 1 or not 1 <= len(images) + len(videos) <= 9 or len(images) > 8
                or any(not f.is_file() for f in files)
                or any(f.suffix.lower() not in (".jpg", ".jpeg", ".png") for f in images)):
            raise GeminiWebError("Tệp đính kèm không hợp lệ (tối đa 1 video và 8 ảnh).")
        return self.attach_upload(videos + images, status, stop, email, video=bool(videos))

    # Đưa tệp vào ô nhập giống người dùng kéo-thả / dán (Ctrl+V) tệp: không cần tìm nút "+", "Tải tệp lên"…
    # nên giao diện đổi cũng không ảnh hưởng. Tool tạo một ô chọn tệp ẩn của riêng mình, nạp tệp vào (không mở hộp
    # thoại Windows), rồi gửi sự kiện thả/dán chứa tệp đó vào ô nhập của trang.
    JS_DROP_INPUT = r"""
let box = document.getElementById('hana-file-source');
if (box) box.remove();
box = document.createElement('input');
box.type = 'file'; box.id = 'hana-file-source'; box.style.display = 'none';
document.body.appendChild(box);
return box;
"""
    JS_DELIVER = r"""
const [field, how] = arguments;
const box = document.getElementById('hana-file-source');
if (!box || !box.files.length) return 'nofile';
const data = new DataTransfer();
for (const file of box.files) data.items.add(file);
field.focus();
if (how === 'drop') {
  const rect = field.getBoundingClientRect();
  const at = {bubbles: true, cancelable: true, composed: true, dataTransfer: data,
              clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2};
  for (const type of ['dragenter', 'dragover', 'drop']) field.dispatchEvent(new DragEvent(type, at));
} else {
  field.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, composed: true, clipboardData: data}));
}
return 'sent';
"""
    # Dấu hiệu trang đã nhận tệp: tên tệp hiện ra, hoặc có thêm ảnh xem trước / khung tệp / thanh tải.
    JS_ATTACHED = r"""
const name = arguments[0];
const named = (document.body.innerText || '').includes(name) ||
  [...document.querySelectorAll('[aria-label],[title],img')].some(e =>
    [e.getAttribute('aria-label'), e.getAttribute('title'), e.getAttribute('alt')].some(v => v && v.includes(name)));
const previews = document.querySelectorAll('img[src^="blob:"],img[src^="data:"],video,[role=progressbar],[aria-busy=true],' +
  '[class*="file-preview"],[class*="attachment"],[class*="uploader-file"],[data-test-id*="file"]').length;
const busy = !!document.querySelector('[role=progressbar],[aria-busy=true]');
return {named, previews, busy};
"""

    def deliver(self, file, how):
        """Nạp tệp vào ô ẩn của tool rồi thả/dán vào ô nhập. Trả về True nếu trang có phản hồi nhận tệp."""
        field = self.driver.execute_script(self.JS_FIND_INPUT)
        if not field:
            return False
        before = self.driver.execute_script(self.JS_ATTACHED, file.name) or {}
        source = self.driver.execute_script(self.JS_DROP_INPUT)
        source.send_keys(str(file))
        if self.driver.execute_script(self.JS_DELIVER, field, how) != "sent":
            return False
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            time.sleep(.5)
            now = self.driver.execute_script(self.JS_ATTACHED, file.name) or {}
            if now.get("named") or now.get("busy") or (now.get("previews") or 0) > (before.get("previews") or 0):
                return True
        return False

    def attach_upload(self, files, status, stop, email="", video=False):
        noun = "video gốc" if video else "ảnh bằng chứng"
        self.wait_ready(status, stop, email)
        for file in files:
            if stop.is_set():
                raise GeminiWebError(f"Đã dừng tải {noun}.")
            is_video = file.suffix.lower() in VIDEO_TYPES
            if is_video:
                status(f"Đang tải video gốc lên {self.NAME} ({file.stat().st_size / 1048576:.0f} MB)…")
            elif video:
                status(f"Đang tải ảnh khung hình {file.name} lên {self.NAME}…")
            if not (self.deliver(file, "drop") or self.deliver(file, "paste")):
                where = self.dump(f"{self.NAME} không nhận tệp {file.name} khi thả/dán vào ô nhập")
                raise GeminiWebError(f"{self.NAME} không nhận {noun} khi thả vào ô nhập; chưa gửi yêu cầu."
                                     + (f" Gửi file chẩn đoán {where} cho người hỗ trợ." if where else ""))
            deadline = time.monotonic() + (900 if is_video else 120)
            while time.monotonic() < deadline:
                if stop.is_set():
                    raise GeminiWebError(f"Đã dừng tải {noun}.")
                page = self.driver.execute_script(self.JS_ATTACHED, file.name) or {}
                if not page.get("busy"):
                    break
                time.sleep(.5)
            else:
                where = self.dump(f"Không xác nhận được {noun} đã tải lên xong")
                raise GeminiWebError(f"Không xác nhận được {noun} đã tải lên {self.NAME} xong. Dừng trước khi gửi yêu cầu."
                                     + (f" Gửi file chẩn đoán {where} cho người hỗ trợ." if where else ""))
            time.sleep(1)
        try:
            self.driver.execute_script("const b=document.getElementById('hana-file-source'); if (b) b.remove();")
        except Exception:
            pass
        if video:
            status(f"Đã tải video gốc lên {self.NAME}")

    def ask(self, text, status, stop, email="", timeout=1200, label="Gemini đang viết"):
        field = self.wait_ready(status, stop, email)
        self.wait_idle(status, stop)
        field = self.wait_ready(status, stop, email)
        before = int(self.state().get("count") or 0)
        self.submit(field, text, before)
        deadline, started = time.monotonic() + timeout, time.monotonic()
        challenged, resent = False, 0
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
            if page.get("challenge"):
                # Ô xác minh hiện ra (thường ngay sau khi gửi): chờ người dùng tự xác minh, không tính thời gian chờ.
                challenged, started = True, time.monotonic()
                status(f"{self.NAME} đang hiện ô xác minh (CAPTCHA). Bấm xác minh trong cửa sổ {self.NAME}; "
                       "xong tool sẽ tự gửi lại nếu tin nhắn chưa đi.")
                continue
            if challenged and (page.get("busy") or int(page.get("count") or 0) > before):
                challenged = False  # tin nhắn đã đi, đang viết: không gửi lại
            if challenged and time.monotonic() - started < 8:
                status(f"Đã xác minh · kiểm tra {self.NAME} đã nhận yêu cầu chưa…")
                continue  # chờ thêm vài giây cho chắc tin nhắn thật sự chưa đi rồi mới gửi lại (tránh gửi trùng)
            if challenged and int(page.get("count") or 0) <= before:
                challenged = False
                if resent >= 3:
                    raise GeminiWebError(f"{self.NAME} vẫn chưa nhận yêu cầu sau 3 lần gửi lại sau xác minh. Xem cửa sổ {self.NAME}.")
                resent += 1
                status(f"Đã xác minh xong · tool dán và gửi lại yêu cầu (lần {resent})")
                field = self.wait_ready(status, stop, email)
                self.submit(field, text, before)
                started = time.monotonic()
                continue
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
            thinking = str(page.get("thinking") or "").strip()
            status(f"{label}… {len(current):,} ký tự".replace(",", ".") + ("" if current.strip() else
                   f" ({self.NAME} đang suy nghĩ{': ' + thinking if thinking else ''} · {int(time.monotonic() - started)} giây)"))
            # Xong khi: không còn nút Dừng, có chữ, đứng yên; và đã hiện nút Sao chép/Thích (hoặc đứng yên lâu hẳn).
            if not page.get("busy") and current.strip() and (
                    (page.get("done") and steady >= 4) or steady >= 25):
                return current
        raise GeminiWebError(f"{self.NAME} viết quá lâu (quá 20 phút). Xem cửa sổ {self.NAME} rồi thử lại.")


def finish_instruction():
    return (f"\n\nSau dòng cảnh cuối cùng của toàn bộ video, viết đúng một dòng: {END_MARK}. "
            f"Nếu câu trả lời bị giới hạn độ dài, dừng ở cuối một dòng cảnh và KHÔNG viết {END_MARK}; "
            "tôi sẽ nhắn \"viết tiếp\".")


def continue_instruction(count, shots=None):
    shots = list(shots or [])
    if shots and count < len(shots):
        nxt = shots[count]
        return (f"Video có {len(shots)} shot theo BẢNG ĐIỂM CẮT nhưng mới có {count} cảnh. Viết tiếp từ Bối cảnh {count + 1} "
                f"(Shot {count + 1}: {nxt['start']:.2f}s – {nxt['end']:.2f}s) đến Bối cảnh {len(shots)}, cùng đúng định dạng "
                f"mỗi cảnh một dòng, không lặp lại cảnh đã viết. Viết xong Bối cảnh {len(shots)} thì viết dòng {END_MARK}.")
    return (f"Viết tiếp danh sách cảnh, bắt đầu từ Bối cảnh {count + 1}, cùng đúng định dạng mỗi cảnh một dòng, "
            f"không lặp lại cảnh đã viết. Khi viết xong cảnh cuối cùng của video, viết dòng {END_MARK}.")


def run_conversation(session, prompt, status, stop, email="", model="", review=False, files=(), shots=None, windows=None):
    """Gửi yêu cầu, tự nhắn viết tiếp đến khi đủ, trả về kết quả đã đọc (kịch bản, cảnh, vấn đề).
    files: video gốc + ảnh khung hình đính kèm; windows: các cảnh 10 giây kèm góc máy FFmpeg đo được — thiếu cảnh thì tự
    nhắn viết tiếp, góc quay chưa khớp điểm cắt thì nhờ viết lại đúng cảnh đó (Reference Analyst)."""
    windows = list(windows or [])
    shots = windows or list(shots or [])
    start_fresh(session, status, stop, email)
    if model:
        picked = session.choose_model(model)
        status(f"Đã chọn model {picked}" if picked else f"Không tự chọn được model “{model}”, dùng model đang chọn sẵn")
    if files:
        session.attach_images(list(files), status, stop, email)
    answers = [session.ask(prompt + finish_instruction(), status, stop, email,
                           label="Gemini đang kiểm tra" if review else "Gemini đang xem video và viết")]
    try:
        result = content.parse_answer(answers[0])
    except ValueError:
        where = session.dump("Không đọc được cảnh nào trong câu trả lời đầu tiên", answers[0]) if hasattr(session, "dump") else ""
        raise GeminiWebError("Tool không đọc được cảnh nào trong câu trả lời của Gemini nên đã dừng (không gửi lại). "
                             + (f"Gửi file chẩn đoán {where} cho người hỗ trợ." if where else ""))
    for round_number in range(MAX_CONTINUE):
        count = len(result["scenes"])
        if END_MARK.lower() in answers[-1].lower() and (not shots or count >= len(shots)):
            break
        if shots and count >= len(shots):
            break
        more = session.ask(continue_instruction(count, shots), status, stop, email,
                           label=f"Đã có {count}{f'/{len(shots)}' if shots else ''} cảnh · Gemini đang viết tiếp "
                                 f"(lần {round_number + 1})")
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
    if windows:
        result["scenes"] = result["scenes"][:len(windows)]
        result["subjects"] = {}
        for answer in answers:  # dòng "Nhân vật cảnh N" của mọi lượt trả lời (lượt sau ghi đè lượt trước)
            result["subjects"].update(content.parse_subjects(answer))
        rounds = verify_cameras(session, result, windows, status, stop, email)
        rounds += verify_subjects(session, result, windows, status, stop, email)
        answers += [""] * rounds
    if shots and len(result["scenes"]) != min(len(shots), content.MAX_SCENES):
        unit = "cảnh 10 giây" if windows else "shot"
        result["issues"] = list(result.get("issues") or []) + [
            f"Gemini viết {len(result['scenes'])} cảnh nhưng video gốc có {len(shots)} {unit}. "
            "Bấm “Kiểm tra thiếu cảnh / lỗi” hoặc xem lại các cảnh cuối."]
    result["rounds"] = len(answers)
    result["shots"] = len(shots)
    if windows:
        result["windows"] = windows
    return result


CHUNK_SCENES = 6  # Gemini API: gửi lần lượt từng đoạn ~60 giây (6 cảnh 10 giây), lấy kết quả từng đoạn
CHUNK_SECONDS = 60
CHUNK_MAX_SCENES = 20  # cảnh theo góc quay ngắn (2–3 giây): tối đa 20 cảnh mỗi đoạn


def chunk_windows(windows):
    """Chia các cảnh thành từng đoạn khoảng CHUNK_SECONDS giây video (không cắt ngang một cảnh)."""
    chunks, current = [], []
    for window in windows:
        if current and (window["end"] - current[0]["start"] > CHUNK_SECONDS + 1 or len(current) >= CHUNK_MAX_SCENES):
            chunks.append(current)
            current = []
        current.append(window)
    return chunks + ([current] if current else [])


def run_chunked(session, base, source, windows, status, stop, email="", model="", review=False, folder=None):
    """Gửi lần lượt rồi lấy kết quả lần lượt: cắt video gốc thành từng đoạn CHUNK_SCENES cảnh, mỗi đoạn một cuộc trò
    chuyện mới (không kéo theo video/lịch sử các đoạn trước → ít token mỗi yêu cầu, ít chạm hạn mức), ghép kết quả theo
    thứ tự. Một đoạn lỗi giữa chừng (vd hết hạn mức) thì giữ các cảnh đã có và ghi rõ dừng ở đâu."""
    from . import reverse_engineer
    windows = list(windows)
    chunks = chunk_windows(windows)
    folder = Path(folder or Path(source).parent / "hana-doan")
    total = {"original_script": [], "scenes": [], "issues": [], "subjects": [], "rounds": 0}
    done = 0
    for index, part in enumerate(chunks, 1):
        if stop.is_set():
            raise GeminiWebError("Đã dừng.")
        offset, length = part[0]["start"], part[-1]["end"] - part[0]["start"]
        local = [dict(w, start=round(w["start"] - offset, 2), end=round(w["end"] - offset, 2)) for w in part]
        status(f"Đoạn {index}/{len(chunks)}: cảnh {done + 1}–{done + len(part)} (giây {offset:g}–{offset + length:g}) · cắt video…")
        try:
            clip = reverse_engineer.cut_clip(source, offset, length, folder / f"doan-{index}.mp4", stop)
            try:
                sheets = reverse_engineer.contact_sheets(clip, local, folder / f"khung-{index}", stop, status)
            except Exception:
                if stop.is_set():
                    raise
                sheets = []
            note = (f"\n\nLƯU Ý: video đính kèm là ĐOẠN {index}/{len(chunks)} của video gốc (giây {offset:g}–{offset + length:g}), "
                    f"gồm đúng {len(part)} cảnh. Chỉ viết {len(part)} cảnh của đoạn này, đánh số Bối cảnh 1 đến {len(part)}, "
                    "thời gian tính từ đầu đoạn.")
            prompt = base + "\n\n" + content.scene_section(local, length, len(sheets)) + note
            result = run_conversation(session, prompt, status, stop, email, model, review, files=[clip] + sheets, windows=local)
        except Exception as error:
            if stop.is_set() or not total["scenes"]:
                raise
            total["issues"].append(f"Dừng ở đoạn {index}/{len(chunks)} (từ cảnh {done + 1}): {error} Các cảnh trước đã giữ lại; "
                                   "chạy lại sau để viết phần còn thiếu.")
            break
        finally:
            if hasattr(session, "cleanup"):
                session.cleanup()  # xóa đoạn video đã tải lên Google (không cần nữa)
        scenes = list(result["scenes"][:len(part)])
        while len(scenes) < len(part):  # giữ đúng số thứ tự cảnh cho các đoạn sau
            scenes.append({field: "" for field in content.FIELDS} | {"action": "(Gemini chưa viết cảnh này — bấm Kiểm tra thiếu cảnh)"})
        total["scenes"] += scenes
        subjects = list(result.get("subjects") or []) if isinstance(result.get("subjects"), list) else []
        total["subjects"] += (subjects + [[]] * len(part))[:len(part)]  # nhân vật theo thứ tự cảnh
        total["issues"] += [f"Đoạn {index}: {issue}" for issue in result.get("issues") or []]
        if result.get("original_script"):
            total["original_script"].append(result["original_script"])
        total["rounds"] += result.get("rounds", 1)
        done += len(part)
        status(f"Xong đoạn {index}/{len(chunks)} · đã có {done}/{len(windows)} cảnh")
    total["original_script"] = "\n\n".join(total["original_script"])
    total["windows"] = windows
    total["shots"] = len(windows)
    return total


VERIFY_ROUNDS = 2


REGENERATE_ROUNDS = 3  # mỗi prompt còn lỗi được AI tạo lại tối đa 3 lần; vẫn sai thì vào danh sách "Cần kiểm tra"


def run_review(session, prompts, rules, lock, status, stop, email="", model="", seconds=None, on_batch=None):
    """Kiểm duyệt lần lượt từng nhóm PROMPT_BATCH prompt (Character Master tính trên toàn bộ prompt
    để mọi nhóm giữ cùng nhân vật), ghép kết quả theo thứ tự."""
    total = len(prompts)
    if total <= (2 if total and sum(len(str(p or "")) for p in prompts) / total > 1500 else PROMPT_BATCH):
        return review_once(session, prompts, rules, lock, status, stop, email, model, seconds)
    secs = list(seconds) if isinstance(seconds, (list, tuple)) else [seconds] * total
    master = content.character_master([str(p or "") for p in prompts])
    batch = 2 if sum(len(str(p or "")) for p in prompts) / total > 1500 else PROMPT_BATCH  # prompt chi tiết dài: 2 mỗi lần
    merged = {"english": [], "issues": [], "violations": {}, "needs_check": [], "attempts": {}, "missing": [],
              "total": total, "rounds": 0, "reviewed": True}
    for first in range(0, total, batch):
        if stop.is_set():
            raise GeminiWebError("Đã dừng.")
        part = prompts[first:first + batch]
        status(f"Nhóm {first // batch + 1}/{-(-total // batch)}: kiểm duyệt prompt {first + 1}–{first + len(part)}…")
        result = review_once(session, part, rules, lock, status, stop, email, model, secs[first:first + len(part)], master)
        merged["english"] += result["english"]
        merged["issues"] += [re.sub(r"Prompt\s+(\d+)", lambda m: f"Prompt {first + int(m.group(1))}", i) for i in result["issues"]]
        merged["violations"].update({first + n: v for n, v in result["violations"].items()})
        merged["needs_check"] += [first + n for n in result.get("needs_check", [])]
        merged["attempts"].update({first + n: a for n, a in result.get("attempts", {}).items()})
        merged["missing"] += [first + n for n in result["missing"]]
        merged["rounds"] += result["rounds"]
        status(f"Đã kiểm duyệt {len(merged['english'])}/{total} prompt")
        if on_batch:
            on_batch(dict(merged, english=list(merged["english"]), done=len(merged["english"])))
    return merged


def review_once(session, prompts, rules, lock, status, stop, email="", model="", seconds=None, master=None):
    """Quality Control: Viết prompt → Kiểm tra → Tự sửa → Kiểm tra lại → Khóa prompt.

    1. Tool tự sửa lỗi không cần AI (markdown, xuống dòng, thời lượng sai cấu hình).
    2. AI đối chiếu từng prompt với bộ quy tắc + Character Master và viết lại; trả dở thì nhắn viết tiếp; bỏ sót prompt
       nào thì hỏi lại đúng số prompt đó.
    3. Tool kiểm tra máy (trống, còn tiếng Việt, sai định dạng, gộp cảnh, sai thời lượng, từ cấm, thiếu góc quay);
       prompt còn lỗi được AI tạo lại riêng, kèm đúng lỗi, tối đa 3 lần. Vẫn sai → danh sách "Cần kiểm tra".
    4. Gắn KHÓA chung vào cuối mọi prompt. Không bao giờ làm mất prompt (AI không trả thì giữ bản cũ)."""
    total = len(prompts)
    # seconds: một số cho mọi prompt, hoặc danh sách thời lượng riêng từng prompt (chia cảnh theo góc quay)
    secs = [s if isinstance(s, (int, float)) and s > 0 else None
            for s in (list(seconds)[:total] + [None] * total)[:total]] if isinstance(seconds, (list, tuple)) else [seconds] * total
    prompts = [content.tidy_prompt(p, secs[i]) for i, p in enumerate(prompts)]
    name = getattr(session, "NAME", "Gemini")
    if not total:
        raise GeminiWebError("Chưa có prompt để kiểm duyệt.")
    structured = sum(1 for p in prompts if len(content.parse_structured(p)) >= 5) * 2 > total
    check = lambda text, n: content.qc_problems(text, secs[n - 1], structured)
    master = master if master is not None else content.character_master(prompts)
    start_fresh(session, status, stop, email)
    if model:
        session.choose_model(model)
    numbers = list(range(1, total + 1))
    answer = session.ask(content.review_request(prompts, rules, master=master), status, stop, email,
                         label=f"{name} đang kiểm duyệt {total} prompt")
    found = content.parse_english(answer, total)
    issues = content.parse_issues(answer)
    rounds = 1
    for _ in range(MAX_CONTINUE):  # trả kết quả dở: nhắn viết tiếp phần còn lại
        if content.END_EN.lower() in answer.lower() or not found or max(found) >= total:
            break
        after = max(found)
        answer = session.ask(content.continue_review(after, numbers), status, stop, email,
                             label=f"Đã kiểm duyệt {len(found)}/{total} · {name} viết tiếp")
        rounds += 1
        more = content.parse_english(answer, total, range(after + 1, total + 1))
        if not any(n > after for n in more):
            break
        found.update(more)
    found = {n: content.tidy_prompt(t, secs[n - 1]) for n, t in found.items()}
    skipped = [n for n in numbers if not (found.get(n) and content.is_english(found[n]))]
    if skipped:  # thiếu prompt: tạo bổ sung đúng số (Scene ID) bị thiếu
        answer = session.ask(content.review_request(prompts, rules, skipped, master) + "\n\nCHỈ kiểm duyệt và viết lại các prompt "
                             "trên (bạn đã bỏ sót chúng).", status, stop, email,
                             label=f"Thiếu {len(skipped)} prompt · {name} tạo bổ sung")
        rounds += 1
        found.update({n: content.tidy_prompt(t, secs[n - 1]) for n, t in content.parse_english(answer, total, skipped).items()
                      if content.is_english(t)})
        issues += [i for i in content.parse_issues(answer) if i not in issues]
    reviewed = [found.get(n) if found.get(n) and content.is_english(found[n]) else prompts[n - 1] for n in numbers]
    attempts = {n: 0 for n in numbers}
    for attempt in range(REGENERATE_ROUNDS):  # Kiểm tra → Tự sửa → Kiểm tra lại
        bad = [n for n in numbers if check(reviewed[n - 1], n)]
        if not bad:
            break
        for n in bad:
            attempts[n] += 1
        status(f"Kiểm tra máy: {len(bad)} prompt còn lỗi · {name} sửa (lần {attempt + 1}/{REGENERATE_ROUNDS})")
        answer = session.ask(content.review_request(reviewed, rules, bad, master)
                             + "\n\nCHỈ viết lại các prompt trên, sửa đúng các lỗi sau (mỗi prompt một cảnh, tiếng Anh, giữ "
                             "nhãn trường):\n" + "\n".join(f"{n}: {'; '.join(check(reviewed[n - 1], n))}" for n in bad),
                             status, stop, email, label=f"{len(bad)} prompt còn lỗi · {name} tạo lại (lần {attempt + 1})")
        rounds += 1
        for n, text in content.parse_english(answer, total, bad).items():
            text = content.tidy_prompt(text, secs[n - 1])
            if len(check(text, n)) < len(check(reviewed[n - 1], n)) or (not check(text, n) and n in bad):
                reviewed[n - 1] = text
        issues += [i for i in content.parse_issues(answer) if i not in issues]
    from . import camera_library
    for n in numbers:  # góc quay đã chọn từ thư viện (3 mốc giây) giữ nguyên, AI không được đổi
        original = camera_library.CAMERA_FIELD.search(content.strip_lock(prompts[n - 1]))
        if original and len(content.SCENE_SEGMENT.findall(original.group(2))) >= 2 and reviewed[n - 1] != prompts[n - 1]:
            reviewed[n - 1] = camera_library.CAMERA_FIELD.sub(lambda m: m.group(1) + original.group(2).strip(),
                                                             reviewed[n - 1], count=1) \
                if camera_library.CAMERA_FIELD.search(reviewed[n - 1]) else reviewed[n - 1]
    final = [content.apply_lock(p, lock or content.DEFAULT_LOCK) for p in reviewed]
    violations = {n: check(final[n - 1], n) + ([] if found.get(n) else ["AI chưa kiểm duyệt prompt này — bấm Tạo lại"])
                  for n in numbers}
    violations = {n: v for n, v in violations.items() if v}
    return {"english": final, "issues": issues, "violations": violations,
            "needs_check": [n for n in violations if attempts[n] >= REGENERATE_ROUNDS or not found.get(n)],
            "attempts": {n: a for n, a in attempts.items() if a},
            "missing": [n for n in numbers if not found.get(n)], "total": total, "rounds": rounds, "reviewed": True}


def verify_subjects(session, result, windows, status, stop, email=""):
    """AI Scene Analyzer: mỗi cảnh phải có danh sách nhân vật (tên, số lượng, vị trí); số lượng đổi giữa hai cảnh liền
    nhau mà không ai bước vào/rời khung thì nhờ Gemini đếm lại (1 lượt). Còn lại ghi vào mục vấn đề."""
    scenes, subjects, rounds = result["scenes"], result["subjects"], 0
    total = len(scenes)
    bad = content.continuity_problems(subjects, scenes, total)
    if bad:
        answer = session.ask(content.fix_subjects_request(bad, windows, subjects), status, stop, email,
                             label=f"Kiểm tra liên tục nhân vật: {len(bad)} cảnh · Gemini đang đếm lại")
        rounds += 1
        for number, items in content.parse_subjects(answer).items():
            if number in bad and items:
                subjects[number] = items
        for number, scene in content.numbered_scenes(answer).items():
            if number in bad and 1 <= number <= total and not (windows and content.camera_problem(scene["camera"],
                                                                                                  windows[number - 1])):
                scenes[number - 1] = scene
        bad = content.continuity_problems(subjects, scenes, total)
    result["issues"] = list(result.get("issues") or []) + [f"Cảnh {n}: {p}." for n, p in sorted(bad.items())][:50]
    result["subjects"] = [subjects.get(n, []) for n in range(1, total + 1)]  # danh sách theo thứ tự cảnh (cho giao diện)
    return rounds


def verify_cameras(session, result, windows, status, stop, email=""):
    """Reference Analyst: đối chiếu góc quay từng cảnh với điểm cắt đo được; cảnh nào chưa khớp thì nhờ Gemini viết lại
    đúng cảnh đó (tối đa 2 lượt). Cảnh vẫn chưa khớp được ghi vào mục vấn đề. Trả về số lượt đã hỏi."""
    scenes, rounds = result["scenes"], 0
    problems = lambda: {n: p for n, (scene, window) in enumerate(zip(scenes, windows), 1)
                        if (p := content.camera_problem(scene["camera"], window))}
    for attempt in range(VERIFY_ROUNDS):
        bad = problems()
        if not bad:
            break
        answer = session.ask(content.fix_cameras_request(bad, windows), status, stop, email,
                             label=f"Kiểm tra liên tục: {len(bad)} cảnh chưa khớp điểm cắt · Gemini đang viết lại (lần {attempt + 1})")
        rounds += 1
        fixed = 0
        for number, scene in content.numbered_scenes(answer).items():
            if number in bad and not content.camera_problem(scene["camera"], windows[number - 1]):
                scenes[number - 1] = scene
                fixed += 1
        if not fixed:
            break
    left = problems()
    result["issues"] = list(result.get("issues") or []) + [f"Cảnh {n}: {p}." for n, p in sorted(left.items())][:50]
    result["verified"] = len(scenes) - len(left)
    return rounds


MAX_MISSING_ROUNDS = 3


def run_characters(session, script, scenes, status, stop, email="", model=""):
    """Character Extractor: AI liệt kê nhân vật duy nhất + nhân vật của từng cảnh; hỏi lại riêng các cảnh còn thiếu."""
    from . import characters as chars
    total = len(scenes)
    name = getattr(session, "NAME", "Gemini")
    start_fresh(session, status, stop, email)
    if model:
        picked = session.choose_model(model)
        status(f"Đã chọn model {picked}" if picked else f"Không tự chọn được model “{model}”, dùng model đang chọn sẵn")
    answer = session.ask(chars.extraction_request(script, scenes), status, stop, email,
                         label=f"{name} đang tìm nhân vật trong {total} cảnh")
    result = chars.parse_characters(answer, total)
    if not result["characters"] and not result["scenes"]:
        where = session.dump("Không đọc được nhân vật nào trong câu trả lời đầu tiên", answer) if hasattr(session, "dump") else ""
        raise GeminiWebError(f"Tool không đọc được nhân vật nào trong câu trả lời của {name} nên đã dừng (không gửi lại). "
                             + (f"Gửi file chẩn đoán {where} cho người hỗ trợ." if where else ""))
    rounds = 1
    for attempt in range(MAX_MISSING_ROUNDS):
        missing = chars.missing_scenes(result["scenes"], total)
        if not missing:
            break
        before = len(missing)
        answer = session.ask(chars.extraction_request(script, scenes, missing, [c["code"] for c in result["characters"]]),
                             status, stop, email, label=f"Thiếu {before} cảnh · {name} đang bổ sung (lần {attempt + 1})")
        rounds += 1
        result = chars.merge_extraction(result, chars.parse_characters(answer, total, [c["code"] for c in result["characters"]]))
        if len(chars.missing_scenes(result["scenes"], total)) == before:
            break  # hỏi lại mà không thêm được cảnh nào: dừng, không gửi lặp
    result.update(missing=chars.missing_scenes(result["scenes"], total), total=total, rounds=rounds)
    return result


DETAILED_FIXES = 2  # prompt chi tiết của một cảnh còn lỗi: nhờ AI viết lại tối đa 2 lần trong cùng cuộc trò chuyện


def english_detailed(session, lines, status, stop, email="", model="", style="", aspect="16:9",
                     seconds=content.DEFAULT_SECONDS, windows=None, on_batch=None):
    """Prompt chi tiết: gửi LẦN LƯỢT TỪNG CẢNH (mỗi cảnh một cuộc trò chuyện mới, kèm prompt cảnh trước để nối tiếp),
    kiểm tra máy từng prompt (câu Generate đúng số giây, đủ mục, tiếng Anh, không từ cấm) và nhờ viết lại nếu lỗi;
    mỗi prompt gộp thành một dòng; cảnh nào xong hiện ngay."""
    total = len(lines)
    name = getattr(session, "NAME", "Gemini")
    windows = list(windows) if windows and len(windows) == total else None
    shot_mode = bool(windows) and all(w.get("shot") for w in windows)
    english, missing, issues, durations, rounds = [], [], [], [], 0
    for number, line in enumerate(lines, 1):
        if stop.is_set():
            raise GeminiWebError("Đã dừng.")
        window = windows[number - 1] if windows else None
        length = content.scene_seconds(window) if shot_mode else seconds
        spans = ([(0, length)] if shot_mode else
                 [(float(a), float(b)) for a, b in window["angles"]] if window and len(window.get("angles") or []) > 1 else None)
        previous = next((p for p in reversed(english) if p), "")
        try:
            start_fresh(session, status, stop, email)
            if model:
                session.choose_model(model)
            answer = session.ask(content.detailed_request(line, number, total, length, spans, previous, aspect, style),
                                 status, stop, email, label=f"{name} đang viết prompt cảnh {number}/{total}")
            rounds += 1
            prompt = content.tidy_prompt(content.flatten_prompt(answer), length)
            for attempt in range(DETAILED_FIXES):
                problems = content.detailed_problems(prompt, length)
                if not problems:
                    break
                answer = session.ask("Rewrite the WHOLE prompt for this scene, same structure, fixing: " + "; ".join(problems)
                                     + ". Write only the prompt, starting with \"Generate\".", status, stop, email,
                                     label=f"Prompt cảnh {number} còn lỗi · {name} viết lại (lần {attempt + 1})")
                rounds += 1
                fixed = content.tidy_prompt(content.flatten_prompt(answer), length)
                if len(content.detailed_problems(fixed, length)) <= len(problems):
                    prompt = fixed
            left = content.detailed_problems(prompt, length)
            if left:
                issues.append(f"Scene {number} — {'; '.join(left)}")
        except GeminiWebError:
            raise
        except Exception as error:  # vd hết hạn mức giữa chừng: giữ các cảnh đã xong
            if stop.is_set() or not english:
                raise
            issues.append(f"Dừng ở cảnh {number}/{total}: {error} Các prompt trước đã giữ lại; chạy lại để viết phần còn thiếu.")
            missing += list(range(number, total + 1))
            english += [""] * (total - number + 1)
            durations += [None] * (total - number + 1)
            break
        english.append(prompt)
        durations.append(length)
        if not prompt:
            missing.append(number)
        status(f"Đã xong {number}/{total} prompt")
        if on_batch:
            on_batch({"english": list(english), "done": number, "total": total, "missing": list(missing)})
    return {"english": english, "missing": missing, "lines": lines, "issues": issues, "structured": "detailed",
            "total": total, "rounds": rounds, "durations": durations if shot_mode else None}


PROMPT_BATCH = 10  # viết / kiểm duyệt lần lượt từng nhóm 10 prompt, lấy kết quả từng nhóm (giao diện hiện ngay)


def run_english(session, lines, status, stop, email="", model="", style="", aspect="16:9", structured=True,
                seconds=content.DEFAULT_SECONDS, angles=0, library=False, custom_angles="", windows=None, on_batch=None):
    """Viết prompt tiếng Anh: gửi lần lượt từng nhóm PROMPT_BATCH cảnh (API hay Chrome đều vậy; mỗi nhóm một cuộc trò chuyện
    mới, ít token, ít chạm hạn mức) và ghép kết quả theo thứ tự; nhóm sau được biết prompt cuối của nhóm trước để
    nối tiếp nhân vật, bối cảnh, hướng di chuyển."""
    total = len(lines)
    if structured == "detailed":  # prompt chi tiết theo mẫu: gửi lần lượt từng cảnh
        return english_detailed(session, lines, status, stop, email, model, style, aspect, seconds, windows, on_batch)
    if total <= PROMPT_BATCH:
        return english_once(session, lines, status, stop, email, model, style, aspect, structured, seconds, angles,
                            library, custom_angles, windows)
    merged = {"english": [], "missing": [], "lines": [], "issues": [], "structured": structured, "total": total,
              "rounds": 0, "durations": []}
    windows = list(windows) if windows and len(windows) == total else None
    for first in range(0, total, PROMPT_BATCH):
        if stop.is_set():
            raise GeminiWebError("Đã dừng.")
        part = lines[first:first + PROMPT_BATCH]
        status(f"Nhóm {first // PROMPT_BATCH + 1}/{-(-total // PROMPT_BATCH)}: viết prompt cảnh {first + 1}–{first + len(part)}…")
        previous = next((p for p in reversed(merged["english"]) if p), "")
        context = ("CONTINUITY: these scenes continue directly after the previous scene, whose prompt was:\n" + previous +
                   "\nKeep the same animals (species, count, look), place, light and screen direction.") if previous else ""
        result = english_once(session, part, status, stop, email, model, style, aspect, structured, seconds, angles, library,
                              custom_angles, windows[first:first + len(part)] if windows else None, context)
        merged["english"] += result["english"]
        merged["lines"] += result["lines"]
        merged["missing"] += [first + n for n in result["missing"]]
        merged["issues"] += [re.sub(r"(Scene|Prompt|Cảnh)\s+(\d+)", lambda m: f"{m.group(1)} {first + int(m.group(2))}", i)
                             for i in result["issues"]]
        merged["rounds"] += result["rounds"]
        merged["durations"] += result.get("durations") or []
        status(f"Đã xong {len(merged['english'])}/{total} prompt")
        if on_batch:
            on_batch(dict(merged, english=list(merged["english"]), done=len(merged["english"])))  # giao diện hiện ngay nhóm vừa xong
    merged["durations"] = merged["durations"] if len(merged["durations"]) == total else None
    return merged


def english_once(session, lines, status, stop, email="", model="", style="", aspect="16:9", structured=True,
                 seconds=content.DEFAULT_SECONDS, angles=0, library=False, custom_angles="", windows=None, context=""):
    """Viết prompt tiếng Anh cho từng cảnh; tự nhắn viết tiếp và hỏi lại những cảnh thiếu/chưa phải tiếng Anh."""
    total = len(lines)
    name = getattr(session, "NAME", "Gemini")
    # Chia cảnh theo góc quay: mỗi cảnh có thời lượng riêng (đúng số giây của góc máy) và đúng MỘT góc máy.
    shot_mode = bool(windows) and len(windows) == total and all(w.get("shot") for w in windows)
    durations = [content.scene_seconds(w) for w in windows] if shot_mode else [seconds] * total
    original_lines, lines = lines, ([f"{line} [Cảnh dài {d} giây · đúng MỘT góc máy, giữ nguyên góc máy của video gốc]"
                                     for line, d in zip(lines, durations)] if shot_mode else lines)
    start_fresh(session, status, stop, email)
    if model:
        picked = session.choose_model(model)
        status(f"Đã chọn model {picked}" if picked else f"Không tự chọn được model “{model}”, dùng model đang chọn sẵn")
    rounds = 1
    answer = session.ask(content.english_request(lines, style=style, aspect=aspect, structured=structured, seconds=seconds,
                                                 angles=0 if shot_mode else angles, shots=shot_mode)
                         + (f"\n\n{context}" if context else ""), status, stop, email,
                         label=f"{name} đang viết {total} prompt tiếng Anh")
    found = content.parse_english(answer, total)
    issues = content.parse_issues(answer)
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
        issues += [i for i in content.parse_issues(answer) if i not in issues]
        if not any(number > after for number in more):
            break
        for number, prompt in more.items():
            if number not in found or not content.is_english(found[number]):
                found[number] = prompt
    # Theo sơ đồ: thiếu cảnh nào thì quay lại hỏi riêng những cảnh đó.
    for attempt in range(MAX_MISSING_ROUNDS):
        missing = content.missing_english(found, total, structured, seconds, angles)
        if not missing:
            break
        answer = session.ask(content.english_request(lines, missing, style, aspect, structured, seconds,
                                                     0 if shot_mode else angles, shot_mode), status, stop, email,
                             label=f"Thiếu {len(missing)} prompt hoặc thiếu trường · {name} đang viết lại (lần {attempt + 1})")
        rounds += 1
        fixed = 0
        issues += [i for i in content.parse_issues(answer) if i not in issues]
        for number, prompt in content.parse_english(answer, total, missing).items():
            good = (content.is_english(prompt) and (not structured or content.structured_complete(prompt))
                    and not (angles and content.angle_problem(prompt, seconds, angles)))
            if number in missing and (number not in found or good):
                found[number] = prompt
                fixed += good
        if not fixed:
            break  # Hỏi lại mà không có cảnh nào được sửa: dừng, không gửi lặp.
    missing = content.missing_english(found, total, structured, seconds, angles)
    if structured:  # dựng lại đúng cấu trúc MASTER PROMPT 08, STYLE/DURATION/ASPECT RATIO chuẩn
        found = {number: content.muse_prompt(prompt, durations[number - 1], aspect, style) for number, prompt in found.items()}
    if library and not shot_mode:  # thư viện góc quay (chia theo góc quay thì giữ đúng góc máy của video gốc): mỗi cảnh đúng 3 góc, chọn theo hành động, không lặp góc cảnh trước
        from . import camera_library
        plans = camera_library.plans([f"{lines[n - 1]} {found.get(n, '')}" for n in range(1, total + 1)], seconds,
                                     custom_angles, windows)  # góc theo điểm cắt FFmpeg đo được trong từng cảnh
        found = {number: camera_library.apply(prompt, plans[number - 1], seconds) for number, prompt in found.items() if prompt}
    return {"english": [found.get(number, "") for number in range(1, total + 1)], "missing": missing, "lines": original_lines,
            "durations": durations if shot_mode else None,
            "issues": issues,
            "structured": structured,
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
        self.gate = None  # SharedProfile khi bật "Tài khoản chung"
        self.api_session = None  # hàm trả về GeminiAPISession khi dùng API key (None: dùng Chrome)
        self.job = {"id": "", "busy": False, "status": f"Chưa mở {name}", "result": None, "error": ""}
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    def account_file(self):
        return self.profile.parent / f"{self.name.lower()}-account.json"

    def record_account(self, logged_in, email):
        """Kết quả kiểm tra đăng nhập thật lần gần nhất (khi tool mở trang): giữ qua các lần khởi động."""
        import json
        try:
            self.account_file().write_text(json.dumps({"logged_in": bool(logged_in), "email": str(email or "")[:254],
                                                       "checked": time.time()}), encoding="utf-8")
        except OSError:
            pass

    def account(self):
        import json
        try:
            data = json.loads(self.account_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"logged_in": data.get("logged_in"), "email": data.get("email", ""), "checked": data.get("checked"),
                "profile_used": (self.profile / "Local State").exists()}

    def snapshot(self):
        with self.lock:
            job = dict(self.job)
        job["account"] = self.account()
        return job

    def set(self, **values):
        with self.lock:
            self.job.update(values)

    def start(self, kind, prompt, email="", model="", lines=None, style=""):
        """kind: "generate"/"review" (prompt = yêu cầu viết cảnh) hoặc "english" (lines = danh sách cảnh)."""
        with self.lock:
            if self.job["busy"]:
                raise ValueError(f"{self.name} đang chạy một yêu cầu khác. Đợi xong hoặc bấm Dừng.")
            job_id = uuid.uuid4().hex
            self.job = {"id": job_id, "kind": kind, "busy": True, "status": f"Đang mở {self.name}…", "result": None, "error": "", "phase": "", "partial": None}
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

    def release_session(self):
        """Đóng Chrome tự động của phần này nếu đang rảnh (để phần khác dùng hồ sơ chung)."""
        with self.lock:
            if self.job["busy"]:
                return False
            session, self.session = self.session, None
        if session:
            session.close()
        return True

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

    def prepare(self, prompt, lines, status, light=False):
        """FFmpeg + Shot Detection trên video gốc (chưa mở Chrome): điểm cắt → cảnh 10 giây → ảnh khung hình.
        Trả về (prompt có bảng cảnh, (tệp gửi Gemini, các cảnh, điểm cắt))."""
        files, shots, windows, duration = [], None, None, 0
        self.set(phase="ffmpeg")
        if isinstance(lines, dict) and lines.get("video"):
            from . import reverse_engineer
            source = Path(lines["video"])
            if lines.get("measure", True):
                status("FFmpeg + Shot Detection: đang tìm điểm cắt cảnh trong video gốc…")
                try:
                    shots, duration = reverse_engineer.quick_shots(source, self.stop)
                    # Mặc định: mỗi lần video gốc đổi góc máy là một cảnh (đúng số giây của góc đó); "fixed": 10 giây một cảnh.
                    windows = (content.scene_windows(shots, duration) if lines.get("split") == "fixed"
                               else content.shot_windows(shots, duration))
                    status(f"Đã tìm {len(shots or [])} điểm cắt · chia {len(windows or [])} cảnh 10 giây")
                except Exception as error:  # không đo được: vẫn cho Gemini xem file, chỉ bỏ bảng cảnh
                    if self.stop.is_set():
                        raise GeminiWebError("Đã dừng.") from None
                    shots = None
                    status(f"Không đo được điểm cắt ({str(error)[:80]}); Gemini vẫn xem file video")
            if light:  # Gemini API: cắt đoạn + trích khung hình làm theo từng đoạn trong run_chunked
                return prompt, ([source], windows, None if windows else shots)
            files = [upload_copy(source, self.profile.parent / "gemini-uploads")]
            sheets = []
            if windows and lines.get("frames", True):
                try:
                    sheets = reverse_engineer.contact_sheets(source, windows, self.profile.parent / "gemini-uploads" / "frames",
                                                             self.stop, status)
                except Exception:
                    if self.stop.is_set():
                        raise GeminiWebError("Đã dừng.") from None
                    sheets = []  # không trích được khung hình: Gemini vẫn xem video + bảng cảnh
            if windows:
                prompt += "\n\n" + content.scene_section(windows, duration, len(sheets))
            files += sheets
        return prompt, (files, windows, None if windows else shots)

    def worker(self):
        while True:
            item = self.requests.get()
            if item is None:
                return
            job_id, kind, prompt, email, model, lines, style = item
            status = lambda text: self.set(status=text)
            try:
                prepared, base = None, prompt
                api = self.api_session() if self.api_session else None
                if kind in ("generate", "review"):
                    # Bước FFmpeg chạy TRƯỚC khi mở Gemini: tìm điểm cắt (Shot Detection), chia cảnh, trích khung hình.
                    prompt, prepared = self.prepare(prompt, lines, status, light=api is not None)
                self.set(phase="gemini")
                if api is not None:  # Gemini API (API key): không mở Chrome, mỗi yêu cầu một phiên mới
                    if self.session:
                        self.session.close()
                    self.session = api
                elif getattr(self.session, "api", False):
                    self.session.close()
                    self.session = None
                if api is None and self.login_window_open():
                    raise GeminiWebError("Cửa sổ Chrome đăng nhập vẫn đang mở. Đóng hẳn cửa sổ đó rồi bấm Chạy lại.")
                if api is None and (not self.session or not self.session.alive()):
                    if self.session:
                        self.session.close()
                    if self.gate:
                        try:
                            self.gate.take(self.name)  # hồ sơ chung: phần khác đang rảnh thì đóng Chrome của nó
                        except RuntimeError as error:
                            raise GeminiWebError(str(error)) from None
                    status(f"Đang mở Chrome cho {self.name}…")
                    self.session = self.factory(self.profile)
                    try:
                        self.session.account_seen = self.record_account
                        self.session.debug_path = self.profile.parent / f"{self.name.lower()}-debug.txt"
                        self.session.take_now = self.take
                    except Exception:
                        pass
                if kind == "production":
                    start_fresh(self.session, status, self.stop, email)
                    if model and not self.session.choose_model(model):
                        raise GeminiWebError(f"Không chọn được model {model} trên {self.name}. Kiểm tra model trên website.")
                    files = lines.get("files", []) if isinstance(lines, dict) else []
                    if files:
                        self.session.attach_images(files, status, self.stop, email)
                    answer = self.session.ask(prompt, status, self.stop, email, timeout=1200,
                                              label=f"{self.name} đang xử lý công đoạn")
                    self.set(busy=False, result={"answer": answer}, status="Đã nhận kết quả agent")
                    continue
                if kind == "characters":
                    result = run_characters(self.session, prompt, lines, status, self.stop, email, model)
                    self.set(busy=False, result=result,
                             status=f"Xong: {len(result['characters'])} nhân vật, {result['total'] - len(result['missing'])}/{result['total']} cảnh"
                                    + (f" · còn thiếu {len(result['missing'])} cảnh" if result["missing"] else ""))
                    continue
                if kind == "promptreview":
                    result = run_review(self.session, lines["prompts"], lines.get("rules", ""), lines.get("lock", ""), status,
                                        self.stop, email, model, lines.get("seconds"),
                                        on_batch=lambda partial: self.set(partial=partial))
                    left = sum(1 for v in result["violations"].values() if v)
                    self.set(busy=False, result=result,
                             status=f"Đã kiểm duyệt {result['total']} prompt" + (f" · còn {left} prompt cần xem lại" if left else ""))
                    continue
                if kind == "english":
                    defaults = ("", "16:9", True, content.DEFAULT_SECONDS, 0, False, "", None)
                    options = tuple(style) if isinstance(style, tuple) else (style,)
                    style, aspect, structured, seconds, angles, library, custom, windows = options + defaults[len(options):]
                    result = run_english(self.session, lines, status, self.stop, email, model, style, aspect, structured, seconds,
                                         angles, library, custom, windows, on_batch=lambda partial: self.set(partial=partial))
                    done = result["total"] - len(result["missing"])
                    self.set(busy=False, result=result,
                             status=f"Xong: {done}/{result['total']} prompt tiếng Anh sau {result['rounds']} lượt"
                                    + (f" · còn thiếu {len(result['missing'])}" if result["missing"] else ""))
                    continue
                files, windows, shots = prepared
                if api is not None and windows and files:  # Gemini API: gửi lần lượt từng đoạn, lấy kết quả lần lượt
                    result = run_chunked(self.session, base, files[0], windows, status, self.stop, email, model,
                                         kind == "review", self.profile.parent / "gemini-uploads" / "doan")
                    result["prompts"] = content.format_prompts(result["scenes"])
                    self.set(busy=False, result=result,
                             status=f"Xong: {len(result['scenes'])} cảnh, gửi lần lượt {len(chunk_windows(windows))} đoạn")
                    continue
                result = run_conversation(self.session, prompt, status, self.stop, email, model, kind == "review",
                                          files=files, windows=windows, shots=shots)
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
