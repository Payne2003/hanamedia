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
OTP_SUBJECT = r"(?:Use code|Dùng mã|Sử dụng mã)\s+(\d{6})\s+(?:to log ?in|để đăng nhập)"
OTP_STAMP = r"(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}(?::\d{2})?)"


def otp_codes(text):
    return set(re.findall(OTP_SUBJECT, text, re.I))


def otp_messages(text):
    """Các thư OTP trong hộp thư, mỗi thư là (thời gian, mã). Meta có thể gửi lại CÙNG một mã trong thư mới,
    nên nhận biết thư mới theo thời gian chứ không theo mã. Hộp thư không hiện giờ thì đánh số thư từ cũ đến mới."""
    stamped = set(re.findall(OTP_STAMP + r"\D{0,200}?" + OTP_SUBJECT, text, re.I))
    if stamped:
        return stamped
    codes = re.findall(OTP_SUBJECT, text, re.I)  # thư mới nằm trên cùng
    return {(f"#{index:04d}", code) for index, code in enumerate(reversed(codes))}


def newest_otp(messages):
    return max(messages)[1] if messages else None


# Ô nhập mã OTP: 6 ô một chữ số (giao diện 2026) hoặc một ô 6 chữ số (bản cũ).
JS_OTP_BOXES = r"""
const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !e.disabled &&
  getComputedStyle(e).visibility !== 'hidden'; };
const skip = ['checkbox', 'radio', 'hidden', 'email', 'password', 'submit', 'button', 'search'];
return [...document.querySelectorAll('input')].filter(e => visible(e) && !skip.includes((e.type || '').toLowerCase()) &&
  (e.maxLength === 1 || e.maxLength === 6 || /one-time-code/i.test(e.autocomplete || '') || /numeric/i.test(e.inputMode || '') ||
   /mã|code|chữ số|digit|otp/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.name || '') + ' ' + (e.id || ''))));
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
        options = webdriver.ChromeOptions()
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument("--lang=vi-VN")
        options.add_argument("--window-size=1280,900")
        if headless:
            options.add_argument("--headless=new")
        options.add_experimental_option("prefs", {
            "intl.accept_languages": "vi-VN,vi,en",
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
        """Nhập mã vào 6 ô (mỗi ô một chữ số) hoặc một ô; Muse tự xác nhận khi đủ 6 số, nếu không thì bấm Tiếp."""
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys
        boxes = self.otp_boxes()
        if len(boxes) >= len(code):
            for box, digit in zip(boxes, code):
                box.click()
                box.send_keys(Keys.CONTROL, "a")
                box.send_keys(digit)
        elif boxes:
            boxes[0].click()
            boxes[0].send_keys(Keys.CONTROL, "a")
            boxes[0].send_keys(code)
        if self.otp_boxes() and self.otp_value() != code:  # ô tự nhảy sang ô kế: gõ cả mã từ ô đầu
            boxes = self.otp_boxes()
            boxes[0].click()
            for _ in range(len(code)):
                ActionChains(self.driver).send_keys(Keys.BACKSPACE).perform()
            ActionChains(self.driver).send_keys(code).perform()
        time.sleep(2)
        if not self.composer():
            next_button = self.named("button,[role='button']", {"Tiếp"})
            if next_button and next_button.is_enabled():
                next_button.click()

    def composer(self):
        return self.named("textarea,input,[role='textbox']", {"Nhắn tin"})

    def session_ready(self):
        host = urlparse(self.driver.current_url).hostname or ""
        return (host == "muse.ai" or host.endswith(".muse.ai")) and self.composer() is not None

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
        if any(t in text for t in ("too many requests", "rate limit", "quá nhiều yêu cầu", "hết credit", "không đủ credit")):
            raise MuseFailure("Muse báo giới hạn hoặc hết credit. Tài khoản đã tạm dừng; kiểm tra trên Muse.")

    def login(self, account):
        from selenium.webdriver.common.keys import Keys
        self.phase("Kiểm tra phiên Muse")
        self.driver.get("https://muse.ai")
        main = self.driver.current_window_handle
        email_box = self.wait(lambda: self.composer() or self.named("input", {"Số di động hoặc email"}), 60,
                              "Không nhận diện được trang đăng nhập Muse. Giao diện có thể đã thay đổi.")
        if self.composer():
            return
        mailbox, baseline, baseline_ok = None, set(), False
        if account["mailbox"]:
            self.phase("Kiểm tra hộp thư trước khi yêu cầu OTP")
            try:
                self.driver.switch_to.new_window("tab")
                mailbox = self.driver.current_window_handle
                self.driver.get(account["mailbox"])
                until = time.monotonic() + 30
                while time.monotonic() < until:
                    body = self.driver.find_element("tag name", "body").text
                    if account["email"].lower() in body.lower() and "Đang kết nối hộp thư" not in body:
                        baseline, baseline_ok = otp_messages(body), True
                        break
                    time.sleep(2)
            except Exception:
                baseline_ok = False
            finally:
                self.driver.switch_to.window(main)
        email_box = self.named("input", {"Số di động hoặc email"})
        if not email_box:
            raise MuseFailure("Không tìm thấy ô email Muse.")
        email_box.clear()
        email_box.send_keys(account["email"])
        button = self.named("button,[role='button']", {"Tiếp tục"})
        if not button:
            raise MuseFailure("Không tìm thấy nút Tiếp tục của Muse.")
        button.click()
        self.wait(lambda: self.composer() or self.otp_boxes(), 60,
                  "Muse chưa hiển thị ô OTP. Kiểm tra tài khoản hoặc yêu cầu xác minh.")
        if self.composer():
            return
        self.phase("Chờ thư OTP mới" if baseline_ok else "Hộp thư chưa sẵn sàng; nhập OTP trực tiếp trên cửa sổ Muse")
        if not baseline_ok and self.headless:
            raise MuseFailure("Không đọc được hộp thư. Bấm Đăng nhập để mở Chrome và nhập OTP trực tiếp.")
        attempts, resent, submitted_at = 0, False, None
        started = time.monotonic()
        until = started + 180
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
            if baseline_ok and mailbox and waiting and attempts < 3:
                self.driver.switch_to.window(mailbox)
                body = self.driver.find_element("tag name", "body").text
                fresh = otp_messages(body) - baseline if account["email"].lower() in body.lower() else set()
                self.driver.switch_to.window(main)
                code = newest_otp(fresh)
                if code and self.otp_boxes():
                    baseline |= fresh  # mỗi thư chỉ dùng một lần, không dùng lại mã cũ
                    attempts += 1
                    self.phase("Đã nhận OTP mới; đang nhập mã" if attempts == 1 else "Thử mã OTP mới hơn")
                    self.enter_otp(code)
                    submitted_at = time.monotonic()
                elif not resent and submitted_at is None and time.monotonic() - started > 60:
                    resend = self.named("a,button,[role='button'],span", {"Gửi lại mã"})
                    if resend:
                        resend.click()  # chưa thấy thư sau 60 giây: bấm Gửi lại mã một lần
                        self.phase("Chưa thấy thư; đã bấm Gửi lại mã")
                    resent = True
            time.sleep(3)
        raise MuseFailure("Hết thời gian chờ đăng nhập. Không lưu OTP; bấm Đăng nhập để thử lại.")

    def video_cards(self):
        return self.elements("[role='log'][aria-label='Tin nhắn chat'] [data-testid='hatch-chat-attachment-presentation-video']")

    def attach_images(self, paths):
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
            for button in self.elements("button,[role='button']"):
                try:
                    label = " ".join((button.accessible_name or "", button.get_attribute("title") or "")).lower()
                    if button.is_displayed() and re.search(
                            r"attach|upload|image|photo|picture|đính kèm|tải ảnh|thêm ảnh", label):
                        button.click()
                        break
                except Exception:
                    continue
            image_inputs = self.wait(image_file_inputs, 10, "Không tìm thấy nút tải ảnh hoặc ô chọn ảnh trên Muse.")
        file_input = image_inputs[0]
        if len(paths) > 1 and not file_input.get_attribute("multiple"):
            raise MuseFailure("Ô tải ảnh trên Muse hiện không nhận nhiều ảnh cùng lúc. Hãy thử lại với một ảnh.")
        file_input.send_keys("\n".join(str(Path(path).resolve()) for path in paths))

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
        if marker not in entered_text or prompt not in entered_text:
            raise MuseFailure("Muse không hiển thị đầy đủ prompt trong ô nhập. Chưa gửi yêu cầu.")
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
