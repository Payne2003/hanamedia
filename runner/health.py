"""Account Health Monitor: kiểm tra phiên Muse của từng tài khoản mà không chen vào tác vụ đang chạy.

- Chỉ kiểm tra tài khoản đang rảnh. Store.begin_check giữ chỗ tài khoản (state 'checking') trong một giao dịch, nên
  bộ chạy không nhận tác vụ, không đăng nhập và không xóa tài khoản đó trong lúc kiểm tra; tài khoản đang chạy trả về BUSY.
- Dùng lại Chrome bộ chạy đang giữ cho tài khoản nếu còn sống; nếu không, mở Chrome ẩn bằng đúng hồ sơ của tài khoản,
  xem trang chủ muse.ai rồi đóng. Không gọi API nội bộ của Muse, không gửi prompt, không tốn credit.
- Lỗi mạng, hết thời gian, 429 và lỗi chưa rõ KHÔNG đánh dấu tài khoản hỏng: giữ state cũ, tăng bộ đếm lỗi.
- Không đọc/lưu OTP, cookie hay mật khẩu; thông báo lỗi chỉ là câu mô tả cố định.
"""
import queue
import random
import threading
import time

from . import muse

HEALTH_STATES = ("LIVE", "CHECKING", "LOGIN_REQUIRED", "EXPIRED", "BUSY", "NETWORK_ERROR", "RATE_LIMITED", "UNKNOWN")


def open_check_browser(profile, downloads):
    return muse.MuseBrowser(profile, downloads, True)


class HealthMonitor:
    def __init__(self, engine, factory=open_check_browser, parallel=2, spacing=2.0):
        self.engine, self.store = engine, engine.store
        self.factory = factory
        self.parallel = parallel
        self.spacing = spacing  # giãn cách giữa hai lần mở muse.ai của cùng một luồng kiểm tra (+ ngẫu nhiên tới 1 giây)
        self.lock = threading.Lock()
        self.requests = queue.Queue()
        self.pending = set()
        self.running = set()
        self.workers = []
        self.owned = set()  # Chrome ẩn do bộ kiểm tra tự mở: đóng khi tắt tool
        self.last_results = {}

    def set_parallel(self, parallel):
        if type(parallel) is not int or not 1 <= parallel <= 4:
            raise ValueError("Số Chrome kiểm tra cùng lúc phải từ 1 đến 4.")
        self.parallel = parallel

    def request(self, account_ids):
        """Xếp hàng kiểm tra. Trả về số tài khoản đã xếp hàng và danh sách tài khoản đang bận (bỏ qua, không đụng vào)."""
        accounts = {a["id"]: a for a in self.store.snapshot()["accounts"]}
        queued, busy = 0, []
        for account_id in account_ids:
            account = accounts.get(account_id)
            if not account:
                continue
            if account["state"] in ("busy", "authenticating", "reviewing"):
                busy.append(account["email"])
                continue
            with self.lock:
                if account_id in self.pending or account_id in self.running:
                    continue
                self.pending.add(account_id)
            self.requests.put(account_id)
            queued += 1
        self.ensure_workers()
        return {"queued": queued, "busy": busy}

    def check_all(self):
        ids = [a["id"] for a in self.store.snapshot()["accounts"] if a["enabled"]]
        if not ids:
            raise ValueError("Chưa có tài khoản nào đang bật để kiểm tra.")
        return self.request(ids)

    def ensure_workers(self):
        with self.lock:
            self.workers = [w for w in self.workers if w.is_alive()]
            while len(self.workers) < min(self.parallel, max(1, len(self.pending))):
                worker = threading.Thread(target=self.worker, daemon=True)
                self.workers.append(worker)
                worker.start()

    def worker(self):
        while not self.engine.closing:
            try:
                account_id = self.requests.get(timeout=2)
            except queue.Empty:
                return
            with self.lock:
                self.pending.discard(account_id)
                self.running.add(account_id)
            try:
                self.last_results[account_id] = self.check(account_id)
            finally:
                with self.lock:
                    self.running.discard(account_id)
                self.engine.changed.set()
            time.sleep(self.spacing + random.random())

    def status(self):
        with self.lock:
            return {"queued": len(self.pending), "checking": len(self.running), "parallel": self.parallel}

    def check(self, account_id):
        """Kiểm tra một tài khoản. Trả về trạng thái sức khỏe đã ghi."""
        before = self.store.begin_check(account_id)
        if before is None:
            return "BUSY"
        health, message, browser, owned = "UNKNOWN", "Không kiểm tra được phiên. Thử lại sau.", None, False
        try:
            with self.engine.lock:
                browser = self.engine.browsers.get(account_id)
            if browser is not None and not self.engine.alive(browser):
                with self.engine.lock:
                    if self.engine.browsers.get(account_id) is browser:
                        self.engine.browsers.pop(account_id)
                browser.close()
                browser = None
            if browser is None:
                data = self.engine.data
                try:
                    browser = self.factory(data / "profiles" / account_id, data / "login-downloads" / account_id)
                except Exception:
                    browser = None
                    message = ("Không mở được Chrome ẩn để kiểm tra. Hồ sơ Chrome của tài khoản có thể đang mở ở cửa sổ khác; "
                               "đóng cửa sổ đó rồi kiểm tra lại.")
                else:
                    owned = True
                    with self.lock:
                        self.owned.add(browser)
            if browser is not None:
                health, message = browser.check_session()
                if health == "LOGIN_REQUIRED" and before["health"] == "LIVE":
                    health, message = "EXPIRED", "Phiên Muse đã hết hạn. Bấm Đăng nhập để nhập OTP."
        except Exception:
            health, message = "UNKNOWN", "Không kiểm tra được phiên. Thử lại sau."
        finally:
            if owned:
                with self.lock:
                    self.owned.discard(browser)
                browser.close()
            if health not in HEALTH_STATES or health in ("CHECKING", "BUSY"):
                health = "UNKNOWN"
            self.store.end_check(account_id, before["state"], health, message)
        return health

    def shutdown(self):
        with self.lock:
            browsers = list(self.owned)
            self.owned.clear()
        for browser in browsers:
            browser.close()
