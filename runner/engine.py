import queue
import shutil
import threading
from pathlib import Path
from .flow import FlowImageSession, default_chrome_profile
from .muse import MuseBrowser, MuseFailure


class Engine:
    def __init__(self, store, data):
        self.store, self.data = store, Path(data)
        self.flow_profile = Path(store.setting(
            "flow_profile", str(default_chrome_profile(self.data))))
        self.lock = threading.RLock()
        self.changed = threading.Event()
        self.enabled = False
        self.closing = False
        self.limit = 2
        self.headless = False
        self.automatic = True
        self.threads = set()
        self.review_stops = {}
        self.browsers = {}
        self.flow_downloads = self.data / "flow-downloads"
        self.flow_downloads.mkdir(parents=True, exist_ok=True)
        self.flow_requests = queue.Queue()
        self.flow_busy = False
        self.flow_status = f"Sẵn sàng mở Flow · Chrome profile {self.flow_profile.name}"
        self.flow_thread = threading.Thread(target=self.flow_worker, daemon=True)
        self.flow_thread.start()
        self.store.recover()
        threading.Thread(target=self.supervise, daemon=True).start()

    def launch(self, function, *args):
        def wrapped():
            try:
                function(*args)
            finally:
                with self.lock:
                    self.threads.discard(threading.current_thread())
                self.changed.set()
        thread = threading.Thread(target=wrapped, daemon=True)
        with self.lock:
            self.threads.add(thread)
        thread.start()

    def start(self, limit, headless, automatic=True):
        if type(limit) is not int or not 1 <= limit <= 10 or type(headless) is not bool or type(automatic) is not bool:
            raise ValueError("Số tài khoản chạy đồng thời phải từ 1 đến 10.")
        destination = self.store.setting("output_folder")
        if not destination or not Path(destination).is_dir():
            raise ValueError("Chọn thư mục lưu trước khi chạy.")
        with self.lock:
            if self.closing:
                raise ValueError("Bộ chạy đang tắt.")
            self.limit, self.headless, self.enabled = limit, headless if automatic else False, True
            self.automatic = automatic
            self.changed.set()

    def pause(self):
        with self.lock:
            self.enabled = False

    def create_flow_image(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
            raise ValueError("Nhập một prompt ảnh từ 1 đến 20.000 ký tự.")
        with self.lock:
            if self.closing:
                raise ValueError("Bộ chạy đang tắt.")
            if self.flow_busy:
                raise ValueError("Flow đang chuẩn bị prompt khác. Đợi một chút rồi thử lại.")
            self.flow_busy = True
            self.flow_status = "Đang mở Flow và điền prompt ảnh"
            self.flow_requests.put(prompt.strip())

    def flow_worker(self):
        browser = None
        while not self.closing:
            try:
                prompt = self.flow_requests.get(timeout=0.5)
            except queue.Empty:
                continue
            if prompt is None:
                break
            try:
                if browser is None:
                    browser = FlowImageSession(self.flow_profile, self.flow_downloads)
                self.flow_status = "Flow đang mở; đăng nhập nếu được yêu cầu"
                browser.prompt(prompt)
                self.flow_status = "Prompt đã điền trong Flow. Chọn Hình ảnh, tạo rồi tải ảnh xuống."
            except Exception as error:
                self.flow_status = str(error)
            finally:
                with self.lock:
                    self.flow_busy = False
        if browser:
            browser.close()

    def supervise(self):
        while not self.closing:
            with self.lock:
                pair = self.store.claim(self.limit) if self.enabled else None
                if pair:
                    self.launch(self.work, *pair, self.headless, self.store.setting("output_folder"), self.automatic)
            if not pair:
                self.changed.wait(1)
                self.changed.clear()

    @staticmethod
    def alive(browser):
        """Chrome/ChromeDriver còn sống không (người dùng có thể đã tự đóng cửa sổ)."""
        try:
            return bool(browser.driver.window_handles)
        except Exception:
            return False

    def session(self, job, account, headless, target):
        """Phiên Muse cho tác vụ: dùng lại Chrome đang mở của tài khoản nếu còn sống, kiểm tra phiên trước khi gửi,
        chỉ đăng nhập lại khi phiên đã mất. Một tài khoản chỉ có một Chrome; tài khoản đang 'busy' nên chỉ thread này dùng nó."""
        phase = lambda value, job_id=job["id"]: self.store.phase(job_id, value)
        with self.lock:
            browser = self.browsers.get(account["id"])
        if browser is not None and not self.alive(browser):
            with self.lock:
                if self.browsers.get(account["id"]) is browser:
                    self.browsers.pop(account["id"])
            browser.close()
            browser = None
        if browser is None:
            browser = MuseBrowser(self.data / "profiles" / account["id"], target, headless, phase)
            with self.lock:
                self.browsers[account["id"]] = browser
            browser.phase = phase
            browser.login(account)
            return browser
        browser.phase = phase
        self.store.phase(job["id"], "Kiểm tra phiên Muse")
        try:
            ready = browser.session_ready()
        except Exception:
            ready = False
        if not ready:
            browser.login(account)  # mở lại muse.ai; nếu phiên hết hạn thì đi qua bước OTP như lần đầu
        return browser

    def work(self, job, account, headless, folder, automatic):
        browser = file = None
        try:
            target = Path(folder) / ("huy-" + job["id"])
            target.mkdir(parents=True, exist_ok=True)
            browser = self.session(job, account, headless, target)
            browser.set_download_directory(target)
            self.store.phase(job["id"], "Chuẩn bị gửi prompt", browser.driver.current_url)
            file = browser.generate(job["prompt"], job["id"], lambda: self.store.submitting(job["id"]),
                                    automatic=automatic, images=job["images"])
            final = target / f"Prompt {job['ordinal'] + 1}.mp4"
            if file != final:
                try:
                    file.rename(final)
                except OSError:
                    final = file  # video đã tải về: giữ tên gốc chứ không báo lỗi tác vụ
            self.store.finish(job["id"], output=str(final))
        except Exception as error:
            message = str(error) if isinstance(error, MuseFailure) else "Bộ chạy gặp lỗi Chrome hoặc đọc/ghi file. Kiểm tra Muse và quyền ghi thư mục; không tự gửi lại."
            session_ready = False
            if browser:
                try:
                    session_ready = browser.session_ready()
                except Exception:
                    session_ready = False
            if browser and not session_ready:
                with self.lock:
                    if self.browsers.get(account["id"]) is browser:
                        self.browsers.pop(account["id"])
                browser.close()
            account_state = error.state if isinstance(error, MuseFailure) else "ready"
            if not session_ready:
                account_state = "attention"
            self.store.finish(job["id"], error=message, account_state=account_state)

    def login(self, account_id, review=False):
        with self.lock:
            if self.closing:
                raise ValueError("Bộ chạy đang tắt.")
            if len(self.threads) >= self.limit:
                raise ValueError("Đã đủ số phiên Chrome đồng thời. Đợi một tài khoản rảnh.")
            # Giữ chỗ tài khoản TRƯỚC khi đụng vào Chrome: không bao giờ đóng hay dùng chung Chrome của tác vụ đang chạy.
            existing = self.browsers.get(account_id)
            if review and existing and not getattr(existing, "headless", False) and self.alive(existing):
                self.store.reserve_login(account_id, "reviewing", "Đang mở phiên Muse hiện tại để kiểm tra.")
                stop = threading.Event()
                self.review_stops[account_id] = stop
                self.launch(self.review_worker, account_id, existing, stop)
                return
            self.store.reserve_login(account_id)
            browser = self.browsers.pop(account_id, None)
            stop = threading.Event() if review else None
            if stop:
                self.review_stops[account_id] = stop
            self.launch(self.login_worker, self.store.account(account_id), stop, browser)

    def remove_account(self, account_id):
        """Xóa tài khoản: đóng phiên Chrome đang giữ, xóa khỏi danh sách và xóa hồ sơ Chrome (phiên đăng nhập) trên máy."""
        with self.lock:
            if account_id in self.review_stops:
                raise ValueError("Tài khoản đang mở phiên. Bấm Đóng phiên rồi xóa.")
            email = self.store.delete_account(account_id)
            browser = self.browsers.pop(account_id, None)
        if browser:
            browser.close()
        profile = self.data / "profiles" / account_id
        if profile.resolve().parent == (self.data / "profiles").resolve():  # chỉ xóa đúng thư mục hồ sơ của tài khoản
            shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(self.data / "login-downloads" / account_id, ignore_errors=True)
        self.changed.set()
        return email

    def review_worker(self, account_id, browser, stop):
        while not stop.wait(1) and not self.closing:
            try:
                if not browser.driver.window_handles:
                    break
            except Exception:
                break
        with self.lock:
            self.review_stops.pop(account_id, None)
            if self.browsers.get(account_id) is not browser:
                return
            try:
                available = browser.session_ready()
            except Exception:
                available = False
            if available:
                self.store.set_account(account_id, "ready", "Đã đóng chế độ kiểm tra; phiên đăng nhập vẫn được giữ.")
                return
            self.browsers.pop(account_id, None)
        browser.close()
        self.store.set_account(account_id, "attention", "Phiên Muse đã đóng hoặc cần đăng nhập lại.")

    def close_review(self, account_id):
        with self.lock:
            stop = self.review_stops.get(account_id)
            if stop:
                stop.set()

    def login_worker(self, account, stop=None, old=None):
        if old:
            old.close()  # Chrome cũ (nếu có) đã được tách khỏi danh sách khi giữ chỗ; đóng để mở lại có cửa sổ
        browser = None
        keep = False
        state, detail = "attention", "Không khởi động được phiên Chrome."
        try:
            browser = MuseBrowser(self.data / "profiles" / account["id"], self.data / "login-downloads" / account["id"], False,
                                  lambda p: self.store.set_account(account["id"], "authenticating", p))
            browser.login(account)
            state, detail = "ready", "Đã xác minh phiên đăng nhập; giữ Chrome mở cho tác vụ tiếp theo"
            if stop:
                self.store.set_account(account["id"], "reviewing", "Đang mở phiên để kiểm tra. Bấm Đóng phiên khi xong.")
                while not stop.wait(1) and not self.closing:
                    if not self.alive(browser):
                        break
            # Đăng nhập xong thì GIỮ Chrome này cho tài khoản (không đóng rồi mở lại ở tác vụ đầu tiên).
            try:
                keep = not self.closing and self.alive(browser) and browser.session_ready()
            except Exception:
                keep = False
            if not keep and stop:
                state, detail = "attention", "Phiên Muse đã đóng hoặc cần đăng nhập lại."
        except Exception as error:
            detail = str(error) if isinstance(error, MuseFailure) else "Không đăng nhập được. Kiểm tra Chrome và hộp thư rồi thử lại."
        finally:
            with self.lock:
                self.review_stops.pop(account["id"], None)
                if keep:
                    self.browsers[account["id"]] = browser
            if browser and not keep:
                browser.close()
            self.store.set_account(account["id"], state, detail)
            self.changed.set()

    def snapshot(self):
        with self.lock:
            return {"enabled": self.enabled, "parallel": self.limit, "headless": self.headless, "automatic": self.automatic,
                    "output_folder": self.store.setting("output_folder", ""), "flow_status": self.flow_status}

    def shutdown(self):
        with self.lock:
            self.closing, self.enabled = True, False
            self.changed.set()
            return list(self.threads)

    def close_browsers(self):
        self.flow_requests.put(None)
        if self.flow_thread.is_alive():
            self.flow_thread.join(timeout=10)
        with self.lock:
            browsers = list(self.browsers.values())
            self.browsers.clear()
        for browser in browsers:
            browser.close()
