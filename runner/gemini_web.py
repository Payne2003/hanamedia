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
const responses = [...document.querySelectorAll('model-response, [data-test-id="model-response"], .model-response')]
  .filter((element, index, all) => !all.some(other => other !== element && other.contains(element)));
const last = responses[responses.length - 1];
let text = '';
if (last) {
  const body = last.querySelector('message-content, .model-response-text, .markdown') || last;
  text = body.innerText || '';
}
const busy = [...document.querySelectorAll('button')].some(b => {
  const label = (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('mattooltip') || '');
  const box = b.getBoundingClientRect();
  return box.width > 0 && /stop|dừng/i.test(label);
});
const account = [...document.querySelectorAll('a[aria-label*="@"], button[aria-label*="@"]')]
  .map(e => e.getAttribute('aria-label')).join(' ');
const email = (account.match(/[\w.+-]+@[\w-]+\.[\w.-]+/) || [''])[0].toLowerCase();
const signIn = location.hostname.startsWith('accounts.') ||
  [...document.querySelectorAll('a,button')].some(e => /^(đăng nhập|sign in)$/i.test((e.innerText || '').trim()));
return {count: responses.length, busy, text, email, signIn, host: location.hostname,
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
    """Một cửa sổ Chrome đăng nhập Gemini. Giữ mở giữa các lần chạy để không phải đăng nhập lại."""

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
            raise GeminiWebError("Không mở được Chrome cho Gemini. Đóng cửa sổ Gemini cũ của tool (nếu có) rồi thử lại. "
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
        return self.driver.execute_script(JS_STATE) or {}

    def new_chat(self):
        self.driver.get(GEMINI_URL)

    def wait_ready(self, status, stop, email="", timeout=900):
        """Chờ ô nhập xuất hiện; nếu chưa đăng nhập thì chờ người dùng đăng nhập trong cửa sổ."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop.is_set():
                raise GeminiWebError("Đã dừng.")
            try:
                page = self.state()
                field = self.driver.execute_script(JS_FIND_INPUT)
            except Exception:
                page, field = {}, None
            if page.get("signIn") or page.get("rejected"):
                # Google không cho nhập mật khẩu trong Chrome đang bị điều khiển tự động.
                raise GeminiWebError("Chưa đăng nhập Gemini trong hồ sơ của tool. Bấm “Đăng nhập Google” để mở Chrome "
                                     "thường, đăng nhập rồi đóng cửa sổ đó, sau đó bấm Chạy lại.")
            if field:
                if email and page.get("email") and page["email"] != email.lower():
                    raise GeminiWebError(f"Cửa sổ Gemini đang đăng nhập {page['email']}, không phải {email}. "
                                         "Đổi tài khoản trong cửa sổ đó rồi bấm lại.")
                return field
            status("Đang chờ Gemini tải xong…")
            time.sleep(2)
        raise GeminiWebError("Hết thời gian chờ đăng nhập Gemini.")

    def choose_model(self, name):
        if not name:
            return ""
        try:
            if self.driver.execute_script(JS_OPEN_MODEL_MENU) is None:
                return ""
            time.sleep(1)
            return self.driver.execute_script(JS_PICK_MODEL, name) or ""
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
            raise GeminiWebError("Gemini đã gửi yêu cầu trước khi tool điền xong. Thử lại; nếu vẫn lỗi hãy dùng chế độ Tự dán.")
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
            raise GeminiWebError("Gemini đã gửi yêu cầu trước khi tool điền xong. Thử lại; nếu vẫn lỗi hãy dùng chế độ Tự dán.")
        if result.get("ok"):
            return result
        # 3) Chèn bằng JS (dán giả lập / dựng đoạn văn / gộp một dòng).
        result = self.driver.execute_script(JS_INSERT, field, text) or {}
        time.sleep(1)
        if self.sent_early(before):
            raise GeminiWebError("Gemini đã gửi yêu cầu trước khi tool điền xong. Thử lại; nếu vẫn lỗi hãy dùng chế độ Tự dán.")
        return result

    def ask(self, text, status, stop, email="", timeout=1200, label="Gemini đang viết"):
        field = self.wait_ready(status, stop, email)
        before = int(self.state().get("count") or 0)
        written = self.fill(field, text, before)
        if not written.get("ok"):
            raise GeminiWebError(f"Không điền đủ yêu cầu vào ô Hỏi Gemini (mới vào {written.get('length', 0)} ký tự). "
                                 "Giao diện Gemini có thể đã thay đổi; dùng tạm chế độ Tự dán.")
        if not self.driver.execute_script(JS_SEND):
            from selenium.webdriver.common.keys import Keys
            field.send_keys(Keys.ENTER)
        deadline, started = time.monotonic() + timeout, time.monotonic()
        last_text, stable_since = None, time.monotonic()
        while time.monotonic() < deadline:
            if stop.is_set():
                raise GeminiWebError("Đã dừng.")
            time.sleep(2)
            page = self.state()
            if int(page.get("count") or 0) <= before:
                if time.monotonic() - started > 120:
                    raise GeminiWebError("Gemini chưa nhận yêu cầu sau 2 phút. Kiểm tra cửa sổ Gemini (có thể cần bấm Gửi).")
                status(f"{label}… chờ Gemini nhận yêu cầu")
                continue
            current = page.get("text") or ""
            if current != last_text:
                last_text, stable_since = current, time.monotonic()
            status(f"{label}… {len(current):,} ký tự".replace(",", "."))
            if not page.get("busy") and current.strip() and time.monotonic() - stable_since >= 4:
                return current
        raise GeminiWebError("Gemini viết quá lâu (quá 20 phút). Xem cửa sổ Gemini rồi thử lại.")


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
    result = content.parse_answer(answers[0])
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


class GeminiWebRunner:
    """Chạy Gemini web trong một luồng riêng; giao diện đọc trạng thái qua snapshot()."""

    def __init__(self, profile, session_factory=GeminiWebSession):
        self.profile, self.factory = Path(profile), session_factory
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.requests = queue.Queue()
        self.session = None
        self.login_process = None
        self.job = {"id": "", "busy": False, "status": "Chưa mở Gemini", "result": None, "error": ""}
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(self.job)

    def set(self, **values):
        with self.lock:
            self.job.update(values)

    def start(self, kind, prompt, email="", model=""):
        with self.lock:
            if self.job["busy"]:
                raise ValueError("Gemini web đang chạy một yêu cầu khác. Đợi xong hoặc bấm Dừng.")
            job_id = uuid.uuid4().hex
            self.job = {"id": job_id, "kind": kind, "busy": True, "status": "Đang mở Gemini…", "result": None, "error": ""}
        self.stop.clear()
        self.requests.put((job_id, kind, prompt, email.strip(), model.strip()))
        return job_id

    def cancel(self):
        self.stop.set()

    def login_window_open(self):
        return bool(self.login_process and self.login_process.poll() is None)

    def open_login(self, email="", launcher=subprocess.Popen):
        """Mở Chrome THƯỜNG (không bị điều khiển) với hồ sơ của tool để người dùng tự đăng nhập Google."""
        with self.lock:
            if self.job["busy"]:
                raise ValueError("Đang chạy Gemini. Bấm Dừng trước khi đăng nhập lại.")
        if self.login_window_open():
            raise ValueError("Cửa sổ đăng nhập đang mở. Đăng nhập xong thì đóng hẳn cửa sổ đó.")
        if self.session:  # Hai Chrome không dùng chung một hồ sơ được.
            self.session.close()
            self.session = None
        self.profile.mkdir(parents=True, exist_ok=True)
        url = login_url(email)
        self.login_process = launcher([chrome_executable(), f"--user-data-dir={self.profile}",
                                       "--profile-directory=Default", "--no-first-run", "--new-window", url])
        self.set(status="Đã mở Chrome để đăng nhập. Đăng nhập Google, thấy trang Gemini thì đóng hẳn cửa sổ đó, rồi bấm Chạy.",
                 error="")

    def worker(self):
        while True:
            item = self.requests.get()
            if item is None:
                return
            job_id, kind, prompt, email, model = item
            status = lambda text: self.set(status=text)
            try:
                if self.login_window_open():
                    raise GeminiWebError("Cửa sổ Chrome đăng nhập vẫn đang mở. Đóng hẳn cửa sổ đó rồi bấm Chạy lại.")
                if not self.session or not self.session.alive():
                    if self.session:
                        self.session.close()
                    status("Đang mở Chrome cho Gemini…")
                    self.session = self.factory(self.profile)
                result = run_conversation(self.session, prompt, status, self.stop, email, model, kind == "review")
                result["prompts"] = content.format_prompts(result["scenes"])
                self.set(busy=False, result=result,
                         status=f"Xong: {len(result['scenes'])} cảnh sau {result['rounds']} lượt trả lời của Gemini")
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
