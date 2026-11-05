"""Muse UI adapter. Selectors observed on muse.ai, 2026-11-02.

No private endpoints, shared Chrome profile, OTP logs or stealth flags.
Each driver is created/used/closed by exactly one worker thread.
"""
import os
import re
import struct
import time
from pathlib import Path
from urllib.parse import urlparse


class MuseFailure(Exception):
    def __init__(self, message, state="ready"):
        super().__init__(message)
        self.state = state


# Tiêu đề thư OTP của Meta (tiếng Anh, tiếng Việt, hoặc bản Google Dịch); không bắt số 6 chữ số tùy ý.
OTP_SUBJECT = r"(?:Use (?:the )?code|Dùng mã|Sử dụng mã|Login code|Mã đăng nhập(?: của bạn)?(?: là)?)\s*:?\s*(\d{6})\b"
OTP_STAMP = r"(\d{4}-\d{2}-\d{2}[\sT]+\d{1,2}:\d{2}(?::\d{2})?)"


def otp_codes(text):
    return set(re.findall(OTP_SUBJECT, text, re.I))


def otp_messages(text):
    """Các thư OTP trong hộp thư, mỗi thư là (thời gian, mã). Meta có thể gửi lại CÙNG một mã trong thư mới,
    nên nhận biết thư mới theo thời gian chứ không theo mã. Hộp thư không hiện giờ thì đánh số thư từ cũ đến mới."""
    stamped = {(re.sub(r"[\sT]+", " ", stamp), code)
               for stamp, code in re.findall(OTP_STAMP + r"\D{0,200}?" + OTP_SUBJECT, text, re.I)}
    if stamped:
        return stamped
    codes = re.findall(OTP_SUBJECT, text, re.I)  # thư mới nằm trên cùng
    return {(f"#{index:04d}", code) for index, code in enumerate(reversed(codes))}


def stamp_time(stamp):
    from datetime import datetime
    try:
        return datetime.fromisoformat(stamp.replace(" ", "T"))
    except ValueError:
        return None


def recent_messages(messages, since, before=2, after=10):
    """Thư có giờ nhận sau thời điểm bấm Tiếp tục (dùng khi không đọc được hộp thư trước đó).
    Hộp thư có thể hiện giờ máy hoặc giờ UTC nên chấp nhận cả hai; chỉ lấy thư từ `before` phút trước đến `after` phút sau."""
    from datetime import datetime, timedelta, timezone
    utc_offset = datetime.now(timezone.utc).replace(tzinfo=None) - datetime.now()
    found = set()
    for stamp, code in messages:
        moment = stamp_time(stamp)
        if moment is None:
            continue
        for clock in (since, since + utc_offset):
            if clock - timedelta(minutes=before) <= moment <= clock + timedelta(minutes=after):
                found.add((stamp, code))
    return found


def mask_codes(text):
    return re.sub(r"\b\d{6}\b", "******", text)


def newest_otp(messages):
    return max(messages)[1] if messages else None


# Ô nhập mã OTP: 6 ô một chữ số (giao diện 2026) hoặc một ô 6 chữ số (bản cũ).
JS_OTP_BOXES = r"""
const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !e.disabled &&
  getComputedStyle(e).visibility !== 'hidden'; };
const skip = ['checkbox', 'radio', 'hidden', 'email', 'password', 'submit', 'button', 'search'];
const all = [...document.querySelectorAll('input')].filter(e => !skip.includes((e.type || '').toLowerCase()));
const isOtp = e => e.maxLength === 1 || e.maxLength === 6 || /one-time-code/i.test(e.autocomplete || '') ||
  /numeric/i.test(e.inputMode || '') || /mã|code|chữ số|digit|otp/i.test((e.getAttribute('aria-label') || '') + ' ' +
  (e.name || '') + ' ' + (e.id || '') + ' ' + (e.placeholder || ''));
let boxes = all.filter(e => visible(e) && isOtp(e));
// Trang "Nhập mã để đăng nhập" nhưng ô không có dấu hiệu trên: lấy mọi ô chữ đang hiện (trừ ô email).
const page = ((document.body && document.body.innerText) || '').slice(0, 3000);
if (!boxes.length && /nhập mã|enter (the )?code|mã bảo mật|security code|gửi lại mã|resend code/i.test(page))
  boxes = all.filter(e => visible(e) && !/email|di động/i.test(e.getAttribute('aria-label') || ''));
// Ô thật bị ẩn (opacity 0) phía sau 6 ô vẽ bằng div.
if (!boxes.length) boxes = all.filter(e => (/one-time-code/i.test(e.autocomplete || '') || e.maxLength === 6) && !e.disabled);
return boxes;
"""

JS_OTP_PAGE = r"""
const page = ((document.body && document.body.innerText) || '').slice(0, 3000);
return /nhập mã để đăng nhập|nhập mã|enter (the )?code|mã bảo mật|gửi lại mã|resend code/i.test(page);
"""

# Mô tả các ô nhập (không có giá trị) cho file chẩn đoán.
JS_DESCRIBE_INPUTS = r"""
return [...document.querySelectorAll('input')].map(e => { const r = e.getBoundingClientRect();
  return `type=${e.type} maxlength=${e.maxLength} inputmode=${e.inputMode} autocomplete=${e.autocomplete} ` +
    `aria-label=${e.getAttribute('aria-label')} name=${e.name} id=${e.id} size=${Math.round(r.width)}x${Math.round(r.height)} ` +
    `opacity=${getComputedStyle(e).opacity} filled=${e.value ? e.value.length : 0}`; }).join('\n');
"""

JS_OPEN_INBOX = r"""
const email = arguments[0];
const box = [...document.querySelectorAll('input')].find(e => e.type !== 'hidden' && e.getBoundingClientRect().width > 0);
if (box && box.value.toLowerCase() !== email.toLowerCase()) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(box, email); box.dispatchEvent(new Event('input', {bubbles: true})); box.dispatchEvent(new Event('change', {bubbles: true}));
}
const button = [...document.querySelectorAll('button, a, [role="button"]')].find(b => /open inbox|mở hộp thư|xem hộp thư/i.test(b.innerText || ''));
if (button) { button.click(); return true; }
return false;
"""

JS_REFRESH_INBOX = r"""
const button = [...document.querySelectorAll('button, a, [role="button"]')].find(b => /^\W*(refresh|làm mới|tải lại)/i.test((b.innerText || '').trim()));
if (button) { button.click(); return true; }
return false;
"""


def compose_request(prompt, job_id, use_images=False):
    instruction = ("Tạo video dựa trên các ảnh tham chiếu đã đính kèm theo yêu cầu sau và trả về file video để tải xuống:"
                   if use_images else "Tạo video theo yêu cầu sau và trả về file video để tải xuống:")
    return instruction + "\n\n" + prompt + "\n\nMã tác vụ: " + job_id


def valid_mp4(path):
    """Require a complete ISO BMFF container, including media and metadata."""
    path = Path(path)
    if path.suffix.lower() != ".mp4" or path.stat().st_size < 1024:
        return False
    end, pos, boxes = path.stat().st_size, 0, set()
    with path.open("rb") as f:
        while pos < end:
            if end - pos < 8:
                return False
            f.seek(pos)
            size, kind = struct.unpack(">I4s", f.read(8))
            header = 8
            if size == 1:
                if end - pos < 16:
                    return False
                size = struct.unpack(">Q", f.read(8))[0]
                header = 16
            elif size == 0:
                size = end - pos
            if size < header or pos + size > end:
                return False
            boxes.add(kind)
            pos += size
    return {b"ftyp", b"moov", b"mdat"}.issubset(boxes)


class MuseBrowser:
    def __init__(self, profile, downloads, headless, phase=lambda value: None):
        # Import lazily: the queue and HTTP server can be tested without a browser.
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        self.phase = phase
        self.headless = headless
        self.downloads = Path(downloads).resolve()
        self.downloads.mkdir(parents=True, exist_ok=True)
        profile = Path(profile).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        self.profile = profile
        options = webdriver.ChromeOptions()
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument("--lang=vi-VN")
        options.add_argument("--window-size=1280,900")
        # Tab hộp thư nằm phía sau vẫn phải tự làm mới để thấy thư OTP mới.
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-backgrounding-occluded-windows")
        if headless:
            options.add_argument("--headless=new")
        options.add_experimental_option("prefs", {
            "intl.accept_languages": "vi-VN,vi,en",
            "translate": {"enabled": False},  # không để Google Dịch đổi chữ trong thư OTP
            "download.default_directory": str(self.downloads),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
        })
        try:
            self.driver = webdriver.Chrome(options=options, service=Service(log_output=os.devnull))
            self.driver.set_page_load_timeout(60)
            self.driver.set_script_timeout(30)
        except Exception:
            raise MuseFailure("Không khởi động được Chrome. Kiểm tra Chrome, kết nối mạng và đóng phiên tool dùng cùng tài khoản.") from None

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def set_download_directory(self, downloads):
        downloads = Path(downloads).resolve()
        downloads.mkdir(parents=True, exist_ok=True)
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": str(downloads),
        })
        self.downloads = downloads

    def elements(self, selector):
        return self.driver.find_elements("css selector", selector)

    def named(self, selector, names):
        for e in self.elements(selector):
            try:
                if e.is_displayed() and (e.accessible_name or "").strip() in names:
                    return e
            except Exception:
                continue
        return None

    def otp_boxes(self):
        try:
            return self.driver.execute_script(JS_OTP_BOXES) or []
        except Exception:
            return []

    def otp_value(self):
        return "".join((box.get_attribute("value") or "") for box in self.otp_boxes())

    def enter_otp(self, code):
        """Điền mã: (1) gõ từng chữ số vào 6 ô, (2) dán cả mã bằng Ctrl+V vào ô đầu, (3) gõ vào ô Muse đang chọn sẵn.
        Muse tự xác nhận khi đủ 6 số; nếu không thì bấm Tiếp. Trả về True khi đã điền đủ hoặc đã vào Muse."""
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        def done():
            return bool(self.composer()) or self.otp_value() == code or self.verifying()

        def clear(boxes):
            for box in reversed(boxes):
                try:
                    box.click()
                    box.send_keys(Keys.CONTROL, "a")
                    box.send_keys(Keys.BACKSPACE)
                except Exception:
                    pass

        boxes = self.otp_boxes()
        try:
            if len(boxes) >= len(code):
                for box, digit in zip(boxes, code):
                    box.click()
                    box.send_keys(Keys.CONTROL, "a")
                    box.send_keys(digit)
            elif boxes:
                boxes[0].click()
                boxes[0].send_keys(Keys.CONTROL, "a")
                boxes[0].send_keys(code)
        except Exception:
            pass
        time.sleep(0.5)
        if not done():
            boxes = self.otp_boxes()
            clear(boxes)
            try:
                self.driver.execute_cdp_cmd("Browser.grantPermissions", {
                    "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
                    "origin": self.driver.execute_script("return location.origin")})
                self.driver.execute_script("return navigator.clipboard.writeText(arguments[0])", code)
                if boxes:
                    boxes[0].click()
                ActionChains(self.driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
            except Exception:
                pass
            time.sleep(0.5)
        if not done():
            boxes = self.otp_boxes()
            clear(boxes)
            if boxes:
                try:
                    boxes[0].click()
                except Exception:
                    pass
            ActionChains(self.driver).send_keys(code).perform()  # Muse thường chọn sẵn ô đầu tiên
            time.sleep(0.5)
        try:
            self.driver.execute_script("navigator.clipboard && navigator.clipboard.writeText('')")
        except Exception:
            pass
        filled = done()
        time.sleep(2)
        if filled and not self.composer() and not self.verifying():
            next_button = self.named("button,[role='button']", {"Tiếp", "Next", "Continue"})
            if next_button and next_button.is_enabled():
                next_button.click()
        return filled

    def verifying(self):
        try:
            return bool(self.driver.execute_script(
                "return /đang xác nhận|verifying|confirming/i.test(((document.body && document.body.innerText) || '').slice(0, 3000))"))
        except Exception:
            return False

    def composer(self):
        return self.named("textarea,input,[role='textbox']", {"Nhắn tin"})

    def new_chat(self):
        """Mở cuộc trò chuyện Muse mới cho mỗi prompt: prompt sau không dùng ngữ cảnh/ảnh của prompt trước."""
        self.driver.get("https://muse.ai")
        self.wait(lambda: self.composer(), 60, "Không mở được cuộc trò chuyện Muse mới. Kiểm tra phiên Muse.")

    def session_ready(self):
        host = urlparse(self.driver.current_url).hostname or ""
        return (host == "muse.ai" or host.endswith(".muse.ai")) and self.composer() is not None

    def apply_cookies(self, cookies):
        """Đặt cookie muse.ai đã kiểm tra (runner/cookies.parse) vào hồ sơ Chrome của tài khoản qua CDP.
        Không ghi giá trị cookie ra log hay thông báo lỗi. Trả về số cookie Chrome nhận."""
        accepted = 0
        for cookie in cookies:
            if not str(cookie.get("domain", "")).lstrip(".").endswith("muse.ai"):
                continue  # chỉ cookie của muse.ai
            params = {key: cookie[key] for key in ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite",
                                                   "expires") if key in cookie}
            try:
                result = self.driver.execute_cdp_cmd("Network.setCookie", params)
            except Exception:
                continue
            accepted += bool(result.get("success", True)) if isinstance(result, dict) else 1
        if not accepted:
            raise MuseFailure("Chrome không nhận cookie nào. Cookie có thể sai định dạng hoặc đã hết hạn.")
        return accepted

    DISCONNECTED = ("đã ngắt kết nối muse", "gửi phiếu yêu cầu hỗ trợ để kết nối lại", "disconnected muse",
                    "submit a support ticket to reconnect")

    def disconnected(self):
        """Muse đã chủ động ngắt kết nối tài khoản (trang "Đã ngắt kết nối Muse ... Gửi phiếu yêu cầu hỗ trợ").
        Tool không vượt qua được: chỉ báo để người dùng liên hệ hỗ trợ Muse."""
        text = " ".join(e.text for e in self.elements("h1,h2,p,[role='alert']") if e.is_displayed()).lower()
        return any(t in text for t in self.DISCONNECTED)

    def check_session(self, timeout=45):
        """Kiểm tra phiên cho Account Health Monitor: chỉ mở trang chủ muse.ai và nhìn trang (không gọi API nội bộ,
        không gửi prompt, không bấm gì, không tốn credit). Trả về (health, thông báo).
        LIVE: thấy ô soạn tin. LOGIN_REQUIRED: thấy ô email đăng nhập hoặc yêu cầu xác minh (người dùng tự xử lý).
        RATE_LIMITED: Muse báo quá nhiều yêu cầu. NETWORK_ERROR: Chrome không tải được trang. UNKNOWN: còn lại."""
        try:
            self.driver.get("https://muse.ai")
        except Exception as error:
            text = str(error).lower()
            if "net::" in text or "err_" in text or "disconnected" in text:
                return "NETWORK_ERROR", "Không tải được muse.ai (lỗi mạng). Chưa đổi trạng thái tài khoản."
            if "timeout" in text or "timed out" in text:
                return "NETWORK_ERROR", "muse.ai tải quá lâu. Chưa đổi trạng thái tài khoản."
            return "UNKNOWN", "Chrome không mở được muse.ai."
        deadline = time.monotonic() + timeout
        while True:
            try:
                url = self.driver.current_url or ""
                if url.startswith("chrome-error://"):
                    return "NETWORK_ERROR", "Không tải được muse.ai (lỗi mạng). Chưa đổi trạng thái tài khoản."
                if self.session_ready():
                    return "LIVE", ""
                text = " ".join(e.text for e in self.elements("[role='alert'],[role='status'],h1,h2") if e.is_displayed()).lower()
                if any(t in text for t in ("too many requests", "rate limit", "quá nhiều yêu cầu", "429")):
                    return "RATE_LIMITED", "Muse báo quá nhiều yêu cầu (429). Sẽ kiểm tra lại sau."
                if self.disconnected():
                    return "SUSPENDED", ("Muse đã ngắt kết nối tài khoản này để bảo vệ thông tin. Gửi phiếu hỗ trợ cho Muse; "
                                         "tool không giao tác vụ cho tài khoản này.")
                if any(t in text for t in ("captcha", "verify you are human", "xác minh bạn là con người")):
                    return "LOGIN_REQUIRED", "Muse yêu cầu xác minh. Bấm Mở phiên Muse và tự xử lý trên cửa sổ."
                if self.named("input", {"Số di động hoặc email"}):
                    return "LOGIN_REQUIRED", "Phiên Muse đã đăng xuất. Bấm Đăng nhập để nhập OTP."
            except Exception:
                return "UNKNOWN", "Chrome của lần kiểm tra bị đóng hoặc mất kết nối."
            if time.monotonic() >= deadline:
                return "UNKNOWN", "Không nhận diện được trang Muse sau khi tải. Thử lại sau."
            time.sleep(1)

    def wait(self, action, timeout, error):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = action()
            if value:
                return value
            time.sleep(1)
        raise MuseFailure(error)

    def guard(self):
        # Only inspect visible alerts/status, never arbitrary user prompt text.
        text = " ".join(e.text for e in self.elements("[role='alert'],[role='status']") if e.is_displayed()).lower()
        if any(t in text for t in ("captcha", "verify you are human", "xác minh bạn là con người")):
            raise MuseFailure("Muse yêu cầu xác minh. Mở phiên Muse để xử lý.", state="attention")
        if self.disconnected():
            raise MuseFailure("Muse đã ngắt kết nối tài khoản này. Gửi phiếu hỗ trợ cho Muse; không giao thêm tác vụ.",
                              state="attention")
        if any(t in text for t in ("too many requests", "rate limit", "quá nhiều yêu cầu", "hết credit", "không đủ credit")):
            raise MuseFailure("Muse báo giới hạn hoặc hết credit. Tài khoản đã tạm dừng; kiểm tra trên Muse.")

    def mailbox_text(self, email):
        """Chữ của trang hộp thư; tự bấm Open Inbox nếu trang chưa mở đúng hộp thư."""
        body = self.driver.find_element("tag name", "body").text
        if email.lower() not in body.lower():
            try:
                self.driver.execute_script(JS_OPEN_INBOX, email)
            except Exception:
                pass
        return body

    def mailbox_ready(self, body, email):
        if "Đang kết nối hộp thư" in body:
            return False
        if email.lower() in body.lower():
            return True
        try:  # email chỉ nằm trong ô nhập, không nằm trong chữ trang
            return bool(self.driver.execute_script(
                "return [...document.querySelectorAll('input')].some(e => (e.value||'').toLowerCase() === arguments[0].toLowerCase())", email))
        except Exception:
            return False

    def otp_page(self):
        try:
            return bool(self.driver.execute_script(JS_OTP_PAGE))
        except Exception:
            return False

    def write_login_debug(self, account, steps, mailbox, main, reason):
        """data/profiles/<tài khoản>-login-debug.txt: các bước, chữ hộp thư (đã che mọi số 6 chữ số) và các ô nhập
        của trang OTP (không có giá trị). Không chứa mã OTP."""
        lines = [f"Lý do: {reason}", f"Email: {account['email']}", "Các bước:"] + [f"  {step}" for step in steps]
        try:
            if mailbox:
                self.driver.switch_to.window(mailbox)
                body = self.driver.find_element("tag name", "body").text
                lines += ["", f"Hộp thư: {self.driver.current_url}", f"Số thư OTP nhận ra: {len(otp_messages(body))}",
                          "Chữ trang hộp thư (đã che mã):", mask_codes(body[:4000])]
            self.driver.switch_to.window(main)
            lines += ["", f"Trang Muse: {self.driver.current_url}", "Ô nhập trên trang Muse:",
                      self.driver.execute_script(JS_DESCRIBE_INPUTS) or "(không có)",
                      "Chữ trang Muse:", mask_codes(self.driver.find_element("tag name", "body").text[:1500])]
        except Exception as error:
            lines.append(f"(không đọc thêm được trang: {type(error).__name__})")
        try:
            (self.profile.parent / f"{self.profile.name}-login-debug.txt").write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass

    def login(self, account):
        from datetime import datetime
        steps = []

        def phase(text):
            steps.append(time.strftime("%H:%M:%S ") + text)
            self.phase(text)

        phase("Kiểm tra phiên Muse")
        self.driver.get("https://muse.ai")
        main = self.driver.current_window_handle
        self.wait(lambda: self.composer() or self.named("input", {"Số di động hoặc email"}) or self.disconnected(), 60,
                  "Không nhận diện được trang đăng nhập Muse. Giao diện có thể đã thay đổi.")
        if not self.composer() and self.disconnected():
            raise MuseFailure("Muse đã ngắt kết nối tài khoản này để bảo vệ thông tin. Gửi phiếu hỗ trợ cho Muse rồi mới "
                              "đăng nhập lại.", state="attention")
        if self.composer():
            return
        email = account["email"]
        mailbox, baseline, baseline_ok = None, set(), False
        if account["mailbox"]:
            phase("Mở hộp thư, ghi nhận các thư OTP cũ")
            try:
                self.driver.switch_to.new_window("tab")
                mailbox = self.driver.current_window_handle
                self.driver.get(account["mailbox"])
                until = time.monotonic() + (30 if self.headless else 0)  # có cửa sổ: người dùng tự lấy mã
                while time.monotonic() < until:
                    body = self.mailbox_text(email)
                    if self.mailbox_ready(body, email):
                        time.sleep(2)  # đợi danh sách thư tải xong
                        baseline, baseline_ok = otp_messages(self.mailbox_text(email)), True
                        break
                    time.sleep(2)
            except Exception:
                baseline_ok = False
            finally:
                self.driver.switch_to.window(main)
            if self.headless:
                phase(f"Hộp thư sẵn sàng, đã có {len(baseline)} thư OTP cũ (sẽ bỏ qua)" if baseline_ok
                      else "Chưa đọc được hộp thư; chỉ dùng thư có giờ nhận sau khi bấm Tiếp tục")
            else:
                phase("Đã mở hộp thư Tuan89 ở tab bên cạnh")
        email_box = self.named("input", {"Số di động hoặc email"})
        if not email_box:
            raise MuseFailure("Không tìm thấy ô email Muse.")
        email_box.clear()
        email_box.send_keys(email)
        button = self.named("button,[role='button']", {"Tiếp tục"})
        if not button:
            raise MuseFailure("Không tìm thấy nút Tiếp tục của Muse.")
        clicked = datetime.now()
        button.click()
        phase("Đã bấm Tiếp tục; chờ trang nhập mã")
        try:
            self.wait(lambda: self.composer() or self.otp_boxes() or self.otp_page(), 60,
                      "Muse chưa hiển thị ô OTP. Kiểm tra tài khoản hoặc yêu cầu xác minh.")
        except MuseFailure as error:
            self.write_login_debug(account, steps, mailbox, main, str(error))
            raise
        if self.composer():
            return
        if not self.headless:
            # Có cửa sổ: người dùng tự dán OTP. Không chuyển tab qua lại để khỏi làm gián đoạn khi đang dán mã.
            self.driver.switch_to.window(main)
            phase("Mở tab hộp thư Tuan89, bấm Copy OTP rồi dán vào ô mã trên Muse" if mailbox
                  else "Nhập mã OTP trực tiếp trên cửa sổ Muse")
            until = time.monotonic() + 300
            while time.monotonic() < until:
                if self.composer():
                    if mailbox:
                        try:
                            self.driver.switch_to.window(mailbox)
                            self.driver.close()
                        except Exception:
                            pass
                        self.driver.switch_to.window(main)
                    return
                self.guard()
                time.sleep(2)
            raise MuseFailure("Hết 5 phút chờ nhập OTP. Bấm Đăng nhập để thử lại.")
        if not mailbox:
            if self.headless:
                raise MuseFailure("Tài khoản chưa có link hộp thư. Bấm Đăng nhập để mở Chrome và nhập OTP trực tiếp.")
            phase("Không có link hộp thư; nhập OTP trực tiếp trên cửa sổ Muse")
        else:
            phase("Chờ thư OTP mới trong hộp thư")
        attempts, resent, submitted_at, used = 0, False, None, set()
        started = time.monotonic()
        until = started + 180
        last_refresh = started
        while time.monotonic() < until:
            self.driver.switch_to.window(main)
            if self.composer():
                if mailbox:
                    self.driver.switch_to.window(mailbox)
                    self.driver.close()
                    self.driver.switch_to.window(main)
                return
            self.guard()
            waiting = submitted_at is None or time.monotonic() - submitted_at > 20  # mã vừa nhập: chờ Muse xác nhận
            if mailbox and waiting and attempts < 3:
                self.driver.switch_to.window(mailbox)
                try:
                    if time.monotonic() - last_refresh > 9:
                        if not self.driver.execute_script(JS_REFRESH_INBOX):
                            self.driver.refresh()
                        last_refresh = time.monotonic()
                        time.sleep(1)
                    messages = otp_messages(self.mailbox_text(email))
                except Exception:
                    messages = set()
                self.driver.switch_to.window(main)
                fresh = (messages - baseline) if baseline_ok else recent_messages(messages, clicked)
                fresh -= used
                if not fresh and resent and time.monotonic() - started > 90:
                    # Meta không gửi thư mới khi mã cũ còn hạn: dùng thư mới nhất nhận trong 10 phút trước, mỗi thư một lần.
                    recent = recent_messages(messages, clicked, before=10) - used
                    if recent:
                        fresh = {max(recent)}
                        phase("Không có thư mới; dùng mã trong thư gần nhất (còn hạn)")
                code = newest_otp(fresh)
                if code:
                    used |= fresh  # mỗi thư chỉ dùng một lần, không dùng lại mã cũ
                    attempts += 1
                    phase("Đã thấy thư OTP mới; đang dán mã vào Muse" if attempts == 1 else "Thử mã OTP mới hơn")
                    filled = self.enter_otp(code)
                    phase("Đã điền đủ 6 số; chờ Muse xác nhận" if filled else "Không điền được mã vào ô OTP của Muse")
                    if not filled:
                        self.write_login_debug(account, steps, mailbox, main, "Không điền được mã vào ô OTP")
                    submitted_at = time.monotonic()
                elif not resent and submitted_at is None and time.monotonic() - started > 60:
                    resend = self.named("a,button,[role='button'],span,div", {"Gửi lại mã", "Resend code"})
                    if resend:
                        resend.click()  # chưa thấy thư sau 60 giây: bấm Gửi lại mã một lần
                        phase("Chưa thấy thư sau 60 giây; đã bấm Gửi lại mã")
                    resent = True
            time.sleep(3)
        reason = ("Đã thử mã OTP nhưng Muse chưa cho vào." if attempts else
                  "Không thấy thư OTP mới trong hộp thư." if mailbox else "Hết thời gian chờ nhập OTP.")
        self.write_login_debug(account, steps, mailbox, main, reason)
        raise MuseFailure(reason + " Không lưu OTP; xem data/profiles/" + self.profile.name +
                          "-login-debug.txt rồi bấm Đăng nhập để thử lại.")

    def video_cards(self):
        return self.elements("[role='log'][aria-label='Tin nhắn chat'] [data-testid='hatch-chat-attachment-presentation-video']")

    # Vùng ô nhập (khung chứa ô "Nhắn tin"): đếm ảnh xem trước đã đính kèm và dấu hiệu đang tải lên.
    JS_ATTACHMENTS = r"""
const box = arguments[0];
let host = box.closest('form') || box.parentElement;
for (let i = 0; i < 4 && host.parentElement && !host.querySelector('img,[data-testid*="attachment"]'); i++) host = host.parentElement;
const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
const previews = [...host.querySelectorAll('img')].filter(e => visible(e) && e.getBoundingClientRect().width >= 24);
const busy = [...host.querySelectorAll('[role="progressbar"],[aria-busy="true"],[class*="spinner"],[class*="loading"],[class*="upload"] progress')]
  .some(visible);
const removers = [...host.querySelectorAll('button,[role="button"]')].filter(b => visible(b) &&
  /xóa|xoá|bỏ|gỡ|remove|delete|clear/i.test((b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')));
return {count: previews.length, busy, removers: removers.length};
"""
    JS_REMOVE_ATTACHMENT = r"""
const box = arguments[0];
let host = box.closest('form') || box.parentElement;
for (let i = 0; i < 4 && host.parentElement && !host.querySelector('img,[data-testid*="attachment"]'); i++) host = host.parentElement;
const b = [...host.querySelectorAll('button,[role="button"]')].find(b => b.getBoundingClientRect().width > 0 &&
  /xóa|xoá|bỏ|gỡ|remove|delete|clear/i.test((b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')));
if (b) { b.click(); return true; }
return false;
"""

    def attachments(self, box=None):
        box = box or self.composer()
        if not box:
            return {"count": 0, "busy": False, "removers": 0}
        try:
            return self.driver.execute_script(self.JS_ATTACHMENTS, box) or {"count": 0, "busy": False, "removers": 0}
        except Exception:
            return {"count": 0, "busy": False, "removers": 0}

    def clear_attachments(self):
        """Bỏ ảnh còn sót trong ô nhập (từ một lần chạy lỗi trước). Không bỏ được thì dừng trước khi gửi."""
        for _ in range(12):
            box = self.composer()
            state = self.attachments(box)
            if not state["count"]:
                return
            if not box or not self.driver.execute_script(self.JS_REMOVE_ATTACHMENT, box):
                break
            time.sleep(0.7)
        if self.attachments()["count"]:
            raise MuseFailure("Ô nhập Muse còn ảnh đính kèm từ lần trước và tool không gỡ được. "
                              "Mở phiên Muse, xóa ảnh trong ô nhập rồi chạy lại. Chưa gửi prompt.")

    def attach_images(self, paths, timeout=120):
        """Đính kèm ảnh và CHỜ Muse tải lên xong (đủ ảnh xem trước, hết biểu tượng đang tải) rồi mới cho gửi prompt."""
        if not paths:
            return

        def image_file_inputs():
            candidates = self.elements("input[type='file']")
            image_inputs = [
                element for element in candidates
                if any(value in (element.get_attribute("accept") or "").lower()
                       for value in ("image", ".png", ".jpg", ".jpeg", ".gif", ".webp"))
            ]
            if not image_inputs and len(candidates) == 1 and not candidates[0].get_attribute("accept"):
                return candidates
            return image_inputs

        image_inputs = image_file_inputs()
        if not image_inputs:
            # Chỉ bấm nút ĐÍNH KÈM / TẢI LÊN; tránh nút chuyển chế độ "Tạo ảnh".
            for button in self.elements("button,[role='button']"):
                try:
                    label = " ".join((button.accessible_name or "", button.get_attribute("title") or "")).lower()
                    if (button.is_displayed() and re.search(r"đính kèm|attach|upload|tải lên|tải ảnh lên|thêm ảnh|thêm tệp|add (photo|image|file)", label)
                            and not re.search(r"tạo ảnh|create image|generate|imagine", label)):
                        button.click()
                        break
                except Exception:
                    continue
            image_inputs = self.wait(image_file_inputs, 10, "Không tìm thấy nút tải ảnh hoặc ô chọn ảnh trên Muse. Chưa gửi prompt.")
        box = self.composer()
        before = self.attachments(box)["count"]
        files = [str(Path(path).resolve()) for path in paths]
        if len(files) == 1 or image_inputs[0].get_attribute("multiple"):
            image_inputs[0].send_keys("\n".join(files))
        else:  # ô chọn ảnh không nhận nhiều file cùng lúc: tải từng ảnh
            for index, file in enumerate(files):
                inputs = image_inputs if index == 0 else self.wait(image_file_inputs, 10, "Không tìm lại được ô chọn ảnh trên Muse.")
                inputs[0].send_keys(file)
                time.sleep(1.5)
        self.phase(f"Đang chờ Muse tải lên {len(files)} ảnh")
        deadline, settled_since = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            self.guard()
            state = self.attachments(self.composer())
            added = state["count"] - before
            if added >= len(files) and not state["busy"]:
                settled_since = settled_since or time.monotonic()
                if time.monotonic() - settled_since >= 2:  # đứng yên 2 giây: đã tải lên xong
                    return
            else:
                settled_since = None
            time.sleep(1)
        state = self.attachments(self.composer())
        added = state["count"] - before
        raise MuseFailure(f"Muse chưa tải lên xong ảnh nhân vật ({max(added, 0)}/{len(files)} ảnh sau {timeout} giây"
                          f"{', vẫn đang tải' if state['busy'] else ''}). Chưa gửi prompt; có thể chạy lại.")

    @staticmethod
    def card_key(card):
        buttons = card.find_elements("css selector", "[role='button'][aria-label]")
        return buttons[0].get_attribute("aria-label") if buttons else ""

    def generate(self, prompt, job_id, before_submit, automatic=True, timeout=1200, images=None):
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains
        self.guard()
        if self.named("button", {"Dừng"}):
            raise MuseFailure("Muse đang xử lý yêu cầu trước đó. Đợi hoàn tất rồi đăng nhập lại để tiếp tục.")
        box = self.composer()
        if not box:
            raise MuseFailure("Phiên đăng nhập đã hết hạn hoặc ô prompt thay đổi.", state="attention")
        self.clear_attachments()  # không gửi kèm ảnh còn sót của prompt trước
        if images:
            self.phase("Đang tải ảnh nhân vật lên Muse")
            self.attach_images(images)
            box = self.wait(self.composer, 10, "Ảnh đã chọn nhưng không tìm thấy lại ô prompt trên Muse.")
        before = {self.card_key(c) for c in self.video_cards()}
        files_before = set(self.downloads.iterdir())
        marker = "Mã tác vụ: " + job_id
        request = compose_request(prompt, job_id, bool(images))
        box.clear()
        box.click()
        for index, line in enumerate(request.split("\n")):
            if index:
                # Plain newlines in send_keys can trigger the chat's submit handler.
                ActionChains(self.driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
            if line:
                box.send_keys(line)
        entered_text = self.driver.execute_script(
            "const e=arguments[0]; return [e.value,e.innerText,e.textContent].filter(Boolean).join('\\n')", box)
        flat = lambda text: re.sub(r"\s+", " ", str(text or "")).strip()  # ô nhập có thể đổi cách xuống dòng
        if flat(marker) not in flat(entered_text) or flat(prompt) not in flat(entered_text):
            raise MuseFailure("Muse không hiển thị đầy đủ prompt trong ô nhập. Chưa gửi yêu cầu.")
        attached = self.attachments(box)
        if attached["count"] != len(images or []) or attached["busy"]:
            raise MuseFailure(f"Ô nhập Muse có {attached['count']} ảnh đính kèm nhưng prompt cần {len(images or [])} ảnh"
                              f"{' (ảnh vẫn đang tải)' if attached['busy'] else ''}. Chưa gửi yêu cầu.")
        # Record intent only after verifying the text is present, immediately before it can be submitted.
        before_submit()
        if automatic:
            box.send_keys(Keys.ENTER)
        self.phase("Muse đang tạo video" if automatic else "Prompt đã điền; bấm Gửi trên cửa sổ Muse")
        until = time.monotonic() + timeout
        new_card = None
        while time.monotonic() < until:
            self.guard()
            for card in self.video_cards():
                key = self.card_key(card)
                if not key or key in before:
                    continue
                # DOM order binds this result to our submitted prompt, not an earlier video.
                after_prompt = self.driver.execute_script("""
                    const card=arguments[0], marker=arguments[1];
                    const log=card.closest('[role="log"]');
                    return [...log.querySelectorAll('p')].some(p =>
                      p.innerText.includes(marker) &&
                      Boolean(p.compareDocumentPosition(card)&Node.DOCUMENT_POSITION_FOLLOWING));
                """, card, marker)
                if after_prompt:
                    new_card = card
                    break
            if new_card:
                break
            time.sleep(3)
        if not new_card:
            raise MuseFailure("Đã gửi prompt nhưng chưa nhận video sau 20 phút. Kiểm tra Muse trước khi tạo lại.")
        self.phase("Đã có video; mở menu tải xuống" if automatic else "Video sẵn sàng; bấm ba chấm cạnh video → Tải xuống trên Muse")
        video = new_card.find_element("css selector", "video")
        self.wait(lambda: self.driver.execute_script("return Number.isFinite(arguments[0].duration)&&arguments[0].duration>0", video),
                  60, "Video xuất hiện nhưng chưa phát được. Kiểm tra trên Muse.")
        expected = re.search(r"([^\s/]+\.mp4)$", self.card_key(new_card))
        if not expected:
            raise MuseFailure("Không xác định được tên file của cảnh mới; chưa gắn video để tránh nhầm cảnh.")
        expected_stem = Path(expected.group(1)).stem
        def matching_download(p):
            return p.suffix.lower() == ".mp4" and (p.stem == expected_stem or re.fullmatch(re.escape(expected_stem) + r" \(\d+\)", p.stem))
        if automatic and not any(matching_download(p) for p in self.downloads.iterdir() if p not in files_before):
            # Nearest common ancestor of THIS card and its overflow menu; never global last menu.
            host = new_card
            menu = None
            for _ in range(8):
                host = host.find_element("xpath", "..")
                if host.get_attribute("role") == "log":
                    break
                candidates = host.find_elements("css selector", 'button[aria-label="Lựa chọn khác"]')
                if len(candidates) == 1:
                    menu = candidates[0]
                    break
            if menu is None:
                raise MuseFailure("Đã có video nhưng không tìm thấy menu tải của video này. Mở Muse để tải thủ công.")
            ActionChains(self.driver).move_to_element(new_card).perform()
            menu.click()
            download = self.wait(lambda: self.named("[role='menuitem']", {"Tải xuống"}), 15,
                                 "Menu video không có Tải xuống; chưa thể tải tự động.")
            download.click()
            self.phase("Đang tải MP4 về thư mục đã chọn")
        stable, previous = 0, None
        until = time.monotonic() + (180 if automatic else 1200)
        while time.monotonic() < until:
            new_files = [p for p in self.downloads.iterdir() if p not in files_before and p.is_file()]
            partial = any(p.suffix.lower() in (".crdownload", ".tmp", ".part") for p in new_files)
            videos = [p for p in new_files if matching_download(p)]
            if not partial and len(videos) == 1:
                signature = (str(videos[0]), videos[0].stat().st_size)
                stable = stable + 1 if signature == previous else 0
                previous = signature
                if stable >= 2 and valid_mp4(videos[0]):
                    return videos[0]
            time.sleep(2)
        raise MuseFailure("Đã bấm Tải xuống nhưng file MP4 chưa tải hoàn tất. Kiểm tra thư mục; không gửi lại prompt.")
