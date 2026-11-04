import queue
import shutil
import threading
from pathlib import Path
import subprocess
from .flow import FlowImageSession, chrome_profile_arguments, default_chrome_profile
from .gemini_web import chrome_executable

from .flow import FLOW_URL  # noqa: E402  (flow.google.com)
FLOW_LOGIN = object()  # yêu cầu đặc biệt trong hàng đợi Flow: mở Chrome thường để đăng nhập
FLOW_CLOSE = object()  # đóng Chrome Flow do tool điều khiển (nhường hồ sơ Google chung)
from .muse import MuseBrowser, MuseFailure


class Engine:
    def __init__(self, store, data):
        self.store, self.data = store, Path(data)
        self.flow_profile = default_chrome_profile(self.data)
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
        self.login_all_status = ""
        self.login_all_running = False
        self.flow_downloads = self.data / "flow-downloads"
        self.flow_downloads.mkdir(parents=True, exist_ok=True)
        self.flow_requests = queue.Queue()
        self.flow_launcher = subprocess.Popen
        self.flow_closed = threading.Event()
        self.gate = None  # SharedProfile khi bật "Tài khoản chung"
        self.flow_busy = False
        self.flow_status = ("Sẵn sàng mở Flow · hồ sơ Chrome riêng của tool" if (self.flow_profile.parent / "Local State").exists()
                            else "Chưa đăng nhập Google Flow: vào Cài đặt → Google Flow → Đăng nhập")
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
        if type(limit) is not int or not 1 <= limit <= 20 or type(headless) is not bool or type(automatic) is not bool:
            raise ValueError("Số tài khoản chạy đồng thời phải từ 1 đến 20.")
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

    def release_flow(self):
        """Đóng Chrome Flow nếu đang rảnh (gọi từ hồ sơ Google chung)."""
        with self.lock:
            if self.flow_busy:
                return False
        self.flow_closed.clear()
        self.flow_requests.put(FLOW_CLOSE)
        self.flow_closed.wait(15)
        return True

    def open_flow_login(self):
        with self.lock:
            if self.closing:
                raise ValueError("Bộ chạy đang tắt.")
            if self.flow_busy:
                raise ValueError("Flow đang bận. Đợi một chút rồi thử lại.")
            self.flow_busy = True
            self.flow_status = "Đang mở Chrome thường để đăng nhập Google Flow"
            self.flow_requests.put(FLOW_LOGIN)

    def flow_worker(self):
        browser = None
        while not self.closing:
            try:
                prompt = self.flow_requests.get(timeout=0.5)
            except queue.Empty:
                continue
            if prompt is None:
                break
            if prompt is FLOW_CLOSE:
                if browser:
                    browser.close()
                    browser = None
                self.flow_closed.set()
                continue
            if prompt == FLOW_LOGIN:
                # Đăng nhập: đóng Chrome do tool điều khiển (cùng hồ sơ) rồi mở Chrome thường để Google cho đăng nhập.
                if browser:
                    browser.close()
                    browser = None
                try:
                    self.flow_launcher([chrome_executable(), *chrome_profile_arguments(self.flow_profile),
                                        "--no-first-run", FLOW_URL])
                    self.flow_status = (f"Đã mở Chrome thường với hồ sơ {self.flow_profile.name}. Đăng nhập Google, thấy trang Flow "
                                        "thì đóng hẳn cửa sổ đó.")
                except Exception as error:
                    self.flow_status = f"Không mở được Chrome: {error}"
                finally:
                    with self.lock:
                        self.flow_busy = False
                continue
            try:
                if browser is not None and self.gate and self.gate.enabled and self.gate.holder != "Flow":
                    browser.close()  # phần khác đã dùng hồ sơ chung: Chrome Flow cũ đã bị đóng
                    browser = None
                if browser is None:
                    if self.gate:
                        self.gate.take("Flow")
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
        elif hasattr(browser, "new_chat"):
            self.store.phase(job["id"], "Mở cuộc trò chuyện Muse mới")
            browser.new_chat()  # mỗi prompt một cuộc trò chuyện riêng (đăng nhập ở trên cũng đã mở trang mới)
            if not browser.session_ready():
                browser.login(account)
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

    def login(self, account_id, review=False, keep=True, slots=None):
        with self.lock:
            if self.closing:
                raise ValueError("Bộ chạy đang tắt.")
            if len(self.threads) >= (slots or self.limit):
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
            self.launch(self.login_worker, self.store.account(account_id), stop, browser, keep)

    def login_all(self, parallel):
        """Đăng nhập lần lượt mọi tài khoản đang bật mà chưa sẵn sàng, tối đa `parallel` Chrome cùng lúc.
        Đăng nhập xong thì đóng Chrome (phiên lưu trong hồ sơ riêng) để không mở hàng trăm cửa sổ."""
        if type(parallel) is not int or not 1 <= parallel <= 20:
            raise ValueError("Số Chrome đăng nhập cùng lúc phải từ 1 đến 20.")
        with self.lock:
            if self.closing:
                raise ValueError("Bộ chạy đang tắt.")
            if self.login_all_running:
                raise ValueError("Đang đăng nhập hàng loạt. Đợi xong lượt này.")
            pending = [a["id"] for a in self.store.snapshot()["accounts"] if a["enabled"] and a["state"] in ("new", "attention")]
            if not pending:
                raise ValueError("Không có tài khoản nào cần đăng nhập.")
            self.login_all_running = True
            self.login_all_status = f"Đăng nhập hàng loạt: 0/{len(pending)}"
        threading.Thread(target=self.login_all_worker, args=(pending, parallel), daemon=True).start()
        return len(pending)

    def login_all_worker(self, pending, parallel):
        done = 0
        try:
            for account_id in pending:
                while not self.closing:
                    with self.lock:
                        logins = sum(1 for a in self.store.snapshot()["accounts"] if a["state"] == "authenticating")
                        if logins < parallel and len(self.threads) < max(self.limit, parallel):
                            break
                    self.changed.wait(1)
                if self.closing:
                    break
                try:
                    self.login(account_id, keep=False, slots=max(self.limit, parallel))
                except ValueError:
                    pass  # tài khoản vừa bận/bị xóa: bỏ qua
                done += 1
                self.login_all_status = f"Đăng nhập hàng loạt: {done}/{len(pending)}"
            while not self.closing and any(a["state"] == "authenticating" for a in self.store.snapshot()["accounts"]):
                self.changed.wait(1)
            self.login_all_status = f"Đã chạy đăng nhập {done}/{len(pending)} tài khoản. Xem trạng thái từng tài khoản bên dưới."
        finally:
            with self.lock:
                self.login_all_running = False

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

    def login_worker(self, account, stop=None, old=None, keep_open=True):
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
                if not keep_open and keep and not stop:
                    keep, detail = False, "Đã đăng nhập; phiên được lưu trong hồ sơ Chrome của tài khoản"
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
                    "output_folder": self.store.setting("output_folder", ""), "flow_status": self.flow_status,
                    "flow_profile": str(self.flow_profile), "flow_busy": self.flow_busy,
                    "flow_profile_used": (self.flow_profile.parent / "Local State").exists(),
                    "login_all": self.login_all_status, "login_all_running": self.login_all_running}

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
