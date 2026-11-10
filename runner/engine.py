import queue
import time
import shutil
import os
import threading
from pathlib import Path
import subprocess
from .flow import FlowImageSession, chrome_profile_arguments, default_chrome_profile
from .gemini_web import chrome_executable

from .flow import FLOW_URL  # noqa: E402  (flow.google.com)
FLOW_LOGIN = object()  # yêu cầu đặc biệt trong hàng đợi Flow: mở Chrome thường để đăng nhập
FLOW_CLOSE = object()  # đóng Chrome Flow do tool điều khiển (nhường hồ sơ Google chung)
from .health import HealthMonitor

from . import hardware  # noqa: E402

MAX_PARALLEL = hardware.HARD_CAP  # trần tuyệt đối; mỗi máy tự tính mức tối đa của mình (Engine.max_parallel)
from .muse import MuseBrowser, MuseFailure
from .video_tools import VideoError, needs_upscale, probe, size_for, stitch, target_size, upscale
from .video_tools import request_spec


NETWORK_ERRORS = ("ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED", "ERR_NAME_NOT_RESOLVED", "ERR_PROXY_CONNECTION_FAILED",
                  "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_TIMED_OUT", "ERR_CONNECTION_REFUSED",
                  "ERR_ADDRESS_UNREACHABLE", "ERR_TIMED_OUT", "ERR_NETWORK_ACCESS_DENIED", "ERR_TUNNEL_CONNECTION_FAILED")


def network_error(error):
    """Lỗi do mất mạng / đổi mạng (không phải do Muse hay Chrome)."""
    text = f"{type(error).__name__} {error}"
    return any(code in text for code in NETWORK_ERRORS)


class Engine:
    NETWORK_WAIT = 30  # giây chờ sau khi mất mạng rồi mới giao tác vụ tiếp

    def __init__(self, store, data):
        self.store, self.data = store, Path(data)
        self.flow_profile = default_chrome_profile(self.data)
        self.lock = threading.RLock()
        self.place_lock = threading.Lock()  # đặt tên 1.mp4, 2.mp4… không trùng khi nhiều luồng xong cùng lúc
        self.changed = threading.Event()
        self.enabled = False
        self.closing = False
        self.hardware = hardware.capacity()  # RAM / CPU của máy này → số luồng tối đa
        self.max_parallel = self.hardware["max_threads"]
        self.gpu_name = None  # bộ mã hóa GPU dò ở nền (không chặn lúc mở app)
        threading.Thread(target=self.detect_gpu, daemon=True).start()
        self.auto_plan = False  # app thật bật (start_runner): chia prompt cho tài khoản cả khi bộ chạy chưa bật
        self.limit = 2
        self.headless = False
        self.automatic = True
        self.threads = set()
        self.review_stops = {}
        self.browsers = {}
        self.account_locks = {}
        from .power import KeepAwake
        self.awake = KeepAwake()  # máy không tự ngủ khi đang tạo video (chỉ gọi từ luồng supervise)
        self.network_pause = 0.0  # mất mạng (đổi VPN, rớt Wi-Fi): tạm không giao tác vụ mới tới thời điểm này  # mở Chrome / đăng nhập / mở tab của một tài khoản: mỗi lúc một luồng
        self.login_all_status = ""
        self.login_all_running = False
        self.flow_downloads = self.data / "flow-downloads"
        self.flow_downloads.mkdir(parents=True, exist_ok=True)
        self.flow_requests = queue.Queue()
        self.flow_launcher = subprocess.Popen
        self.flow_closed = threading.Event()
        self.gate = None  # SharedProfile khi bật "Tài khoản chung"
        self.license_ok = lambda: True  # AppServer thay bằng kiểm tra bản quyền
        self.flow_busy = False
        self.flow_status = ("Sẵn sàng mở Flow · hồ sơ Chrome riêng của tool" if (self.flow_profile.parent / "Local State").exists()
                            else "Chưa đăng nhập Google Flow: vào Cài đặt → Google Flow → Đăng nhập")
        self.flow_thread = threading.Thread(target=self.flow_worker, daemon=True)
        self.flow_thread.start()
        # Hậu kỳ trên máy (nâng độ phân giải, tự ghép) — một luồng riêng, không chiếm luồng/tài khoản Muse.
        self.post_requests = queue.Queue()
        self.post_status = ""
        for job_id in self.store.post_pending():  # khởi động lại: làm tiếp phần nâng độ phân giải còn dở
            self.post_requests.put(job_id)
        if self.store.stitch_candidates():
            self.post_requests.put("")
        self.post_thread = threading.Thread(target=self.post_worker, daemon=True)
        self.post_thread.start()
        self.store.recover()
        self.health = HealthMonitor(self)
        self.supervisor = threading.Thread(target=self.supervise, daemon=True)
        self.supervisor.start()

    # ---- hậu kỳ --------------------------------------------------------------------------------------------------
    def post_worker(self):
        """Nâng độ phân giải video vừa tải (giữ file gốc) rồi ghép các lượt đã xong hết cảnh. Sau khi khởi động lại,
        tự làm tiếp phần còn dở."""
        while not self.closing:
            try:
                item = self.post_requests.get(timeout=2)
            except queue.Empty:
                continue
            if item is None:
                return
            try:
                if item.startswith("stitch:"):
                    self.stitch_batch(item[7:], manual=True)
                else:
                    if item:
                        self.post_job(item)
                    for batch in self.store.stitch_candidates():
                        self.stitch_batch(batch)
            except Exception:
                self.post_status = "Hậu kỳ gặp lỗi không mong muốn. Video gốc vẫn được giữ."
            finally:
                self.changed.set()

    def post_job(self, job_id):
        """Video tải về nhỏ hơn độ phân giải đã chọn thì phóng lên (file mới "Prompt N (4K).mp4", giữ file gốc)."""
        job = self.store.job_post_info(job_id)
        if not job or job["status"] != "done" or not job["resolution"] or not job["output"]:
            return
        source = Path(job["output"])
        try:
            self.store.set_post(job_id, "running")
            info = probe(source)
            size = size_for(job["aspect"], job["resolution"], info)
            if not needs_upscale(info, size):
                self.store.set_post(job_id, f"ok:{info['width']}x{info['height']}")
                return
            self.post_status = f"Đang nâng {source.name} lên {job['resolution']}"
            output = upscale(source, source.with_name(f"{source.stem} ({job['resolution']}).mp4"), size,
                             video_args=self.video_args())
            self.store.set_post(job_id, f"upscaled:{size[0]}x{size[1]}", str(output))
        except (VideoError, OSError) as error:
            self.store.set_post(job_id, "error:" + (str(error) if isinstance(error, VideoError)
                                                    else "Không đọc/ghi được file video."))
        finally:
            self.post_status = ""

    def stitch_batch(self, batch, manual=False):
        """Ghép các cảnh đã xong của một lượt theo thứ tự cảnh (ưu tiên bản đã nâng độ phân giải)."""
        scenes = [s for s in self.store.batch_scenes(batch) if s["status"] == "done" and s["output"]]
        sources = [Path(s["upscaled_output"] or s["output"]) for s in scenes]
        sources = [path for path in sources if path.is_file()]
        if len(sources) < 2:
            self.store.set_stitch(batch, "error", error="Cần ít nhất 2 cảnh đã xong (còn file trên máy) để ghép.")
            return
        first = scenes[0]
        size = target_size(first["aspect"], first["resolution"]) if first["resolution"] else None
        parent = sources[0].parent  # thư mục dự án / thư mục lưu; video cũ nằm trong huy-<id> thì ghép ra thư mục cha
        root = parent.parent if parent.name.startswith("huy-") else parent
        destination = root / f"Video ghép {time.strftime('%Y-%m-%d %H%M')} ({len(sources)} cảnh).mp4"
        try:
            self.store.set_stitch(batch, "running")
            self.post_status = f"Đang ghép {len(sources)} cảnh thành một video"
            output = stitch(sources, destination, size, video_args=self.video_args())
            self.store.set_stitch(batch, "done", str(output))
        except (VideoError, OSError) as error:
            self.store.set_stitch(batch, "error", error=str(error) if isinstance(error, VideoError)
                                  else "Không đọc/ghi được file video.")
        finally:
            self.post_status = ""

    def stitch_now(self, batch):
        if not any(s["status"] == "done" for s in self.store.batch_scenes(batch)):
            raise ValueError("Lượt này chưa có cảnh nào xong.")
        self.post_requests.put("stitch:" + batch)

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
        if type(limit) is not int or not 1 <= limit <= self.max_parallel or type(headless) is not bool or type(automatic) is not bool:
            raise ValueError(f"Máy này chạy tối đa {self.max_parallel} luồng (tài khoản cùng lúc).")
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

    def detect_gpu(self):
        try:
            self.gpu_name = hardware.gpu_encoder()
        except Exception:
            self.gpu_name = None

    def video_args(self):
        """Mã hóa bằng GPU khi máy có và người dùng không tắt; không thì CPU."""
        if self.gpu_name and self.store.setting("gpu_encode", True) is not False:
            return hardware.ENCODERS[self.gpu_name][1]
        return None

    def supervise(self):
        planned = 0.0
        while not self.closing:
            with self.lock:
                working = bool(self.threads) or self.enabled
            if working and not self.threads:
                try:
                    working = self.store.has_waiting()  # bộ chạy bật và còn prompt chờ (vd đang chờ mạng): vẫn giữ máy thức
                except Exception:
                    pass
            self.awake.set(working)
            with self.lock:
                pair = (self.store.claim(self.limit) if self.enabled and self.license_ok()
                        and time.monotonic() >= self.network_pause else None)
                if pair:
                    self.launch(self.work, *pair, self.headless, self.store.setting("output_folder"), self.automatic)
            if not pair and not self.enabled and self.auto_plan and time.monotonic() - planned > 3:
                # Bộ chạy chưa bật (vừa mở lại app): vẫn chia prompt đang chờ cho các tài khoản để thấy ngay ai chạy gì.
                planned = time.monotonic()
                try:
                    self.store.plan_waiting()
                except Exception:
                    pass  # CSDL bận: lần sau chia lại
            if not pair:
                self.changed.wait(1)
                self.changed.clear()

    @staticmethod
    def alive(browser):
        """Chrome/ChromeDriver còn sống không (người dùng có thể đã tự đóng cửa sổ). Cửa sổ đang điều khiển đã đóng
        nhưng còn tab khác thì chuyển sang tab đó; không còn tab nào thì coi như Chrome đã chết."""
        try:
            handles = browser.driver.window_handles
        except Exception:
            return False
        if not handles:
            return False
        try:
            browser.driver.current_window_handle
            return True
        except AttributeError:
            return True  # driver giả lập trong kiểm thử
        except Exception:
            try:
                browser.driver.switch_to.window(handles[-1])
                return True
            except Exception:
                return False

    def account_lock(self, account_id):
        with self.lock:
            return self.account_locks.setdefault(account_id, threading.Lock())

    def session(self, job, account, headless, target):
        """Phiên Muse cho tác vụ. Một tài khoản chỉ có một Chrome; tài khoản chạy nhiều luồng thì mỗi tác vụ một TAB riêng
        trong Chrome đó (TabRouter). Mở Chrome / đăng nhập / mở tab của cùng tài khoản lần lượt từng luồng."""
        lock = self.account_lock(account["id"])
        with lock:
            root = self.root_session(job, account, headless, target)
            router = getattr(root, "router", None)
            if router is None:
                return root
            try:
                handle, fresh = router.acquire()
            except Exception:
                return root
            import copy
            tab = copy.copy(root)  # tab riêng: phase/url/thư mục tải của tác vụ này, dùng chung Chrome và router
            tab.phase = lambda value, job_id=job["id"]: self.store.phase(job_id, value)
            tab.url_seen = MuseBrowser.url_seen
            tab.root = root
            tab.owns_tab = True
            if fresh or not tab.session_ready():
                self.store.phase(job["id"], "Mở tab Muse mới cho luồng này")
                try:
                    tab.new_chat()
                except Exception:
                    pass
                if not tab.session_ready():
                    tab.login(account)
            elif hasattr(tab, "new_chat") and not getattr(root, "just_logged_in", False):
                self.store.phase(job["id"], "Mở cuộc trò chuyện Muse mới")
                tab.new_chat()
                if not tab.session_ready():
                    tab.login(account)
            root.just_logged_in = False
            return tab

    def release_tab(self, browser):
        """Tác vụ xong: trả tab cho tác vụ sau (Chrome vẫn mở)."""
        if browser is not None and getattr(browser, "owns_tab", False):
            browser.owns_tab = False
            try:
                browser.router.release()
            except Exception:
                pass

    def drop_browser(self, account_id, browser):
        """Phiên hỏng: đóng Chrome của tài khoản — trừ khi tab khác của tài khoản vẫn đang chạy (chỉ trả tab)."""
        root = getattr(browser, "root", browser)
        router = getattr(root, "router", None)
        if router is not None and getattr(browser, "owns_tab", False) and router.active() > 1:
            self.release_tab(browser)
            return
        self.release_tab(browser)
        with self.lock:
            if self.browsers.get(account_id) is root:
                self.browsers.pop(account_id)
        root.close()

    @staticmethod
    def attach_router(browser):
        """Gắn TabRouter (nhiều tab trong một Chrome). Driver giả lập trong kiểm thử: bỏ qua, chạy một tab như cũ."""
        try:
            from .tabs import TabRouter
            from selenium.webdriver.remote.webdriver import WebDriver
            if isinstance(browser.driver, WebDriver):
                browser.router = TabRouter(browser.driver)
                return True
        except Exception:
            pass
        browser.router = None
        return False

    def root_session(self, job, account, headless, target):
        """Chrome của tài khoản (dùng lại nếu còn sống, không thì mở mới và đăng nhập)."""
        phase = lambda value, job_id=job["id"]: self.store.phase(job_id, value)
        with self.lock:
            browser = self.browsers.get(account["id"])
        if browser is not None and getattr(browser, "router", None) is not None and browser.router.active():
            try:
                if browser.driver.window_handles:
                    return browser  # tab khác đang chạy và Chrome còn sống: không chen vào kiểm tra phiên
            except Exception:
                pass  # người dùng đã đóng cả Chrome: mở Chrome mới bên dưới
        if browser is not None and not self.alive(browser):
            with self.lock:
                if self.browsers.get(account["id"]) is browser:
                    self.browsers.pop(account["id"])
            browser.close()
            browser = None
        if browser is not None and getattr(browser, "router", None) is None:
            self.attach_router(browser)  # Chrome mở từ nút Đăng nhập / Kiểm tra: gắn bộ chia tab trước khi chạy
        if browser is None:
            browser = MuseBrowser(self.data / "profiles" / account["id"], target, headless, phase)
            if self.attach_router(browser):
                browser.router.acquire()  # tab đầu tiên: đăng nhập trên tab này
            with self.lock:
                self.browsers[account["id"]] = browser
            browser.phase = phase
            try:
                browser.login(account)
                browser.just_logged_in = True
            finally:
                if getattr(browser, "router", None) is not None:
                    browser.router.release()  # session() gắn lại tab này cho tác vụ
            return browser
        if getattr(browser, "router", None) is not None:
            return browser  # nhiều tab: session() tự kiểm tra phiên trên tab của tác vụ
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
            target = self.download_dir(job)
            browser = self.session(job, account, headless, target)
            browser.set_download_directory(target)
            browser.url_seen = lambda url, job_id=job["id"]: self.store.set_source(job_id, url)
            self.store.phase(job["id"], "Chuẩn bị gửi prompt", browser.driver.current_url)
            spec = request_spec(job.get("aspect"), job.get("duration"), job.get("resolution"))
            # Prompt chọn thông số: gửi kèm, và chờ lâu hơn cho 4K (Muse làm 4K / prompt dài thường mất 20–40 phút).
            extra = {"spec": spec, "timeout": 2700 if job.get("resolution") == "4K" else 1800} if spec else {}
            try:
                file = browser.generate(job["prompt"], job["id"], lambda: self.store.submitting(job["id"]),
                                        automatic=automatic, images=job["images"], **extra)
            except MuseFailure as error:
                if not error.pending or not automatic or not hasattr(browser, "fetch_existing"):
                    raise
                file = self.chase_video(job, browser, error)  # tải được rồi mới nhận prompt tiếp theo
            self.complete(job, target, file, folder)
        except Exception as error:
            known = isinstance(error, MuseFailure)
            technical = self.log_failure(job, account, error) if known else self.log_error(job, account, error)
            message = str(error) if known else (
                f"Bộ chạy gặp lỗi Chrome/trang Muse ({technical}). Chi tiết trong data/logs/runner.log; không tự gửi lại.")
            if known and getattr(error, "unsent", False) and self.store.requeue_unsent(job["id"], str(error), "ready",
                                                                                      count=False):
                return
            if known and getattr(error, "region", False):
                # Muse không tạo video cho tài khoản này ở khu vực của nó: chuyển prompt (và mọi prompt đang chờ) sang
                # tài khoản khác; không giao prompt cho tài khoản này nữa.
                if self.store.reassign_blocked(job["id"], account["id"]):
                    return
                self.store.set_video_blocked(account["id"])
            dead = not known and any(t in f"{type(error).__name__} {error}" for t in (
                "NoSuchWindow", "InvalidSessionId", "invalid session id", "not connected to DevTools",
                "target window already closed", "web view not found", "chrome not reachable"))
            alive = bool(browser) and not dead and self.alive(browser)
            session_ready = False
            if alive:
                try:
                    session_ready = browser.session_ready()
                except Exception:
                    session_ready = False
            if alive:
                try:
                    self.store.set_source(job["id"], browser.driver.current_url)  # để "Lấy video" sau này
                except Exception:
                    pass
            if browser and not session_ready:
                self.drop_browser(account["id"], browser)
                browser = None
            account_state = error.state if known else "ready"
            if alive and not session_ready:
                account_state = "attention"  # Chrome còn mở nhưng Muse đã đăng xuất: cần người dùng đăng nhập lại
            # Mất mạng TRƯỚC khi gửi: không tính là một lần thử (không đốt hết lượt tự chạy lại), chờ mạng rồi chạy tiếp.
            if not known and network_error(error):
                self.network_pause = time.monotonic() + self.NETWORK_WAIT
                if self.store.requeue_unsent(job["id"], "Mất kết nối mạng; tự chạy lại khi có mạng (prompt chưa gửi)",
                                             account_state, count=False):
                    return
            # Chrome bị đóng / treo: lần sau mở Chrome mới, KHÔNG dừng tài khoản.
            if not known and self.store.requeue_unsent(job["id"], f"lỗi Chrome trước khi gửi ({technical})", account_state):
                return
            # Chrome tắt ngang SAU khi đã gửi: mở Chrome mới, vào lại đúng cuộc trò chuyện, tải video (không gửi lại).
            if not known and automatic and not alive and self.recover_after_crash(job, account, headless, target, folder):
                return
            self.store.finish(job["id"], error=message, account_state=account_state)
        finally:
            self.release_tab(browser)

    def download_dir(self, job):
        """Thư mục tạm Chrome tải về, nằm trong dữ liệu app (không hiện trong thư mục lưu của người dùng)."""
        target = self.data / "downloads" / job["id"]
        shutil.rmtree(target, ignore_errors=True)  # bỏ file thừa của lần chạy trước: chỉ nhận video của lần này
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def output_dir(job, folder):
        """Thư mục lưu / <thư mục dự án người dùng đặt>."""
        root = Path(folder) if folder else None
        name = str(job.get("folder") or "").strip()
        return (root / name) if root and name else root

    def place(self, job, target, file, folder=None):
        """Video tải xong: chỉ giữ đúng 1 video của prompt, đặt vào thư mục lưu (hoặc thư mục dự án), tên theo số
        thứ tự prompt (1.mp4, 2.mp4…), rồi xóa sạch thư mục tạm. Không ghi đè: tên đã có thì thêm " (2)", " (3)"…"""
        root = self.output_dir(job, folder) or Path(target).parent
        number = job["ordinal"] + 1
        root.mkdir(parents=True, exist_ok=True)
        with self.place_lock:
            final, copy = root / f"{number}.mp4", 2
            while final.exists() and final != file:
                final, copy = root / f"{number} ({copy}).mp4", copy + 1
            if file != final:
                try:
                    os.replace(file, final)
                except OSError:
                    final = file  # video đã tải về: giữ tên/chỗ cũ chứ không báo lỗi tác vụ
        if Path(target).resolve().is_relative_to((self.data / "downloads").resolve()):
            shutil.rmtree(target, ignore_errors=True)  # thư mục tạm của app: xóa cả file thừa (chỉ giữ 1 video)
        else:
            try:
                Path(target).rmdir()
            except OSError:
                pass
        return final

    def complete(self, job, target, file, folder=None):
        final = self.place(job, target, file, folder)
        self.store.finish(job["id"], output=str(final))
        if job.get("resolution") or self.store.batch_stitch(job["batch"]):
            self.post_requests.put(job["id"])  # nâng độ phân giải / ghép video chạy riêng, không giữ tài khoản Muse

    def recover_after_crash(self, job, account, headless, target, folder=None):
        """Prompt đã gửi, Chrome chết giữa chừng: mở Chrome mới và tự tải video về. True nếu đã xong tác vụ."""
        state = self.store.job_state(job["id"])
        url = str(state.get("source_url") or "")
        if state.get("status") != "running" or not state.get("submitted") or not url.startswith("https://") \
                or url.rstrip("/").endswith(("muse.ai", "muse.ai/")) or self.closing:
            return False
        browser = None
        try:
            self.store.phase(job["id"], "Chrome bị đóng sau khi gửi; mở lại cuộc trò chuyện để lấy video (không gửi lại)")
            browser = self.session(job, account, headless, target)
            browser.set_download_directory(target)
            if not hasattr(browser, "fetch_existing"):
                return False
            file = self.chase_video(job, browser, MuseFailure("Chrome bị đóng sau khi gửi.", pending=True), url=url,
                                    first_now=True)
            self.complete(job, target, file, folder)
            return True
        except Exception as again:
            self.log_failure(job, account, MuseFailure(f"Không lấy lại được video sau khi Chrome bị đóng: {again}"))
            return False
        finally:
            self.release_tab(browser)

    CHASE_ATTEMPTS = 10  # ~25 phút tự kiểm tra thêm sau khi hết thời gian chờ
    chase_wait = 60

    def chase_video(self, job, browser, error, url=None, first_now=False):
        """Prompt ĐÃ GỬI mà chưa tải được video (Muse chậm, khung video bị vẽ lại, nút tải chưa hiện): giữ tài khoản,
        tự mở lại đúng cuộc trò chuyện và kiểm tra lại nhiều lần cho tới khi tải được. KHÔNG gửi lại prompt.
        Chỉ khi vẫn không được mới báo lỗi (khi đó còn nút ⇩ Lấy video)."""
        url = url or browser.driver.current_url
        self.store.set_source(job["id"], url)
        last = error
        for attempt in range(1, self.CHASE_ATTEMPTS + 1):
            if self.closing:
                break
            self.store.phase(job["id"], f"Muse chưa trả video; tự kiểm tra lại lần {attempt}/{self.CHASE_ATTEMPTS} "
                                        "(không gửi lại prompt)")
            if not (first_now and attempt == 1):
                time.sleep(self.chase_wait)
            try:
                return browser.fetch_existing(job["id"], url)
            except MuseFailure as again:
                if not again.pending:
                    raise
                last = again
        raise last

    def fetch(self, job_id):
        """Nút ⇩ Lấy video: mở lại cuộc trò chuyện Muse của tác vụ đã gửi và tải video Muse làm xong muộn."""
        with self.lock:
            if self.closing:
                raise ValueError("Bộ chạy đang tắt.")
            job, account = self.store.begin_fetch(job_id, max(self.limit, 1))
            self.launch(self.fetch_work, job, account, self.headless, self.store.setting("output_folder"))

    def fetch_work(self, job, account, headless, folder):
        browser = None
        try:
            target = self.download_dir(job)
            browser = self.session(job, account, headless, target)
            browser.set_download_directory(target)
            file = browser.fetch_existing(job["id"], job["source_url"])
            final = self.place(job, target, file, folder)
            self.store.finish(job["id"], output=str(final))
            if job.get("resolution") or self.store.batch_stitch(job["batch"]):
                self.post_requests.put(job["id"])
        except Exception as error:
            message = str(error) if isinstance(error, MuseFailure) else (
                f"Không lấy được video ({self.log_error(job, account, error)}). Bấm ⇩ Lấy video lần nữa.")
            if browser and not self.alive(browser):
                self.drop_browser(account["id"], browser)
                browser = None
            self.store.finish(job["id"], error=message, account_state="ready")
        finally:
            self.release_tab(browser)

    def log_failure(self, job, account, error):
        """Lỗi đã có thông báo (MuseFailure): ghi một dòng vào runner.log (giai đoạn + lời nhắn; không prompt/OTP)."""
        try:
            folder = self.data / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            phase = ""
            try:
                row = [j for j in self.store.snapshot()["jobs"] if j["id"] == job["id"]]
                phase = f" · bước: {row[0]['phase']} ({row[0]['progress']}%)" if row else ""
            except Exception:
                pass
            with (folder / "runner.log").open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] tác vụ {job['id']} · tài khoản "
                             f"{account.get('label') or account['id']}{phase} · {str(error)[:400]}\n")
        except OSError:
            pass
        return ""

    def log_error(self, job, account, error):
        """Ghi lỗi kỹ thuật (không ghi prompt, cookie hay OTP) vào data/logs/runner.log; trả về tên lỗi ngắn gọn."""
        import traceback
        name = type(error).__name__
        first = (str(error).strip().splitlines() or [""])[0][:160]
        try:
            folder = self.data / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "runner.log"
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(folder / "runner.old.log")
            stack = "".join(traceback.format_exception(type(error), error, error.__traceback__, limit=12))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] tác vụ {job['id']} · tài khoản "
                             f"{account.get('label') or account['id']} · {name}: {first}\n{stack}")
        except OSError:
            pass
        return f"{name}: {first}" if first else name

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
        if type(parallel) is not int or not 1 <= parallel <= self.max_parallel:
            raise ValueError(f"Máy này mở tối đa {self.max_parallel} Chrome đăng nhập cùng lúc.")
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
            return {"enabled": self.enabled, "parallel": self.limit, "max_parallel": self.max_parallel,
                    "keep_awake": self.awake.on,
                    "hardware": {**self.hardware, "gpu": hardware.ENCODERS[self.gpu_name][0] if self.gpu_name else None,
                                 "gpu_enabled": self.store.setting("gpu_encode", True) is not False}, "headless": self.headless, "automatic": self.automatic,
                    "output_folder": self.store.setting("output_folder", ""), "flow_status": self.flow_status,
                    "flow_profile": str(self.flow_profile), "flow_busy": self.flow_busy,
                    "flow_profile_used": (self.flow_profile.parent / "Local State").exists(),
                    "login_all": self.login_all_status, "login_all_running": self.login_all_running,
                    "post_status": self.post_status,
                    "health": self.health.status()}

    def shutdown(self):
        with self.lock:
            self.closing, self.enabled = True, False
            self.changed.set()
            threads = list(self.threads)
        # Đợi vòng điều phối dừng hẳn (nó có thể đang chia prompt trong CSDL) trước khi đóng / xóa dữ liệu.
        supervisor = getattr(self, "supervisor", None)
        if supervisor and supervisor is not threading.current_thread():
            supervisor.join(5)
        return threads

    def close_browsers(self):
        self.post_requests.put(None)
        self.health.shutdown()
        self.flow_requests.put(None)
        if self.flow_thread.is_alive():
            self.flow_thread.join(timeout=10)
        with self.lock:
            browsers = list(self.browsers.values())
            self.browsers.clear()
        for browser in browsers:
            browser.close()
