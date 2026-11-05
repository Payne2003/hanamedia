"""Account Health Monitor: không chen vào tác vụ đang chạy, không đánh dấu hỏng khi lỗi mạng/429, giữ dữ liệu khi nâng cấp DB."""
import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from app import AppServer
from runner.engine import Engine
from runner.health import HealthMonitor
from runner.muse import MuseBrowser
from runner.store import Store


class FakeBrowser:
    """Chrome giả cho kiểm tra phiên: ghi lại số Chrome mở cùng lúc."""
    lock = threading.Lock()

    def __init__(self, world, account_id, result=("LIVE", "")):
        self.world, self.account_id, self.result, self.closed = world, account_id, result, False
        self.driver = type("D", (), {"window_handles": ["main"]})()

    def check_session(self):
        with FakeBrowser.lock:
            self.world["open"] += 1
            self.world["peak"] = max(self.world["peak"], self.world["open"])
        time.sleep(0.05)
        with FakeBrowser.lock:
            self.world["open"] -= 1
            self.world["checked"].append(self.account_id)
        result = self.world["results"].get(self.account_id, self.result)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True
        self.world["closed"].append(self.account_id)


class HealthStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.db")

    def tearDown(self):
        self.temp.cleanup()

    def account(self, name="a"):
        self.store.add_account(f"{name}@tuan89.site", "", 10)
        return next(a for a in self.store.snapshot()["accounts"] if a["email"] == f"{name}@tuan89.site")

    def test_migration_keeps_accounts_jobs_and_assignments(self):
        path = self.root / "old.db"
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, mailbox TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, budget REAL NOT NULL, state TEXT NOT NULL DEFAULT 'new',
                detail TEXT NOT NULL DEFAULT '', last_used REAL NOT NULL DEFAULT 0);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
                phase TEXT NOT NULL DEFAULT 'Đợi tài khoản', account_id TEXT, cost REAL NOT NULL,
                submitted INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', output TEXT,
                created REAL NOT NULL, updated REAL NOT NULL, source_url TEXT NOT NULL DEFAULT '',
                batch TEXT NOT NULL, ordinal INTEGER NOT NULL, assigned_account_id TEXT, UNIQUE(batch, ordinal));
            INSERT INTO accounts(id,email,mailbox,budget,state,detail) VALUES('a1','x@tuan89.site','',5,'ready','cũ');
            INSERT INTO jobs(id,prompt,cost,created,updated,batch,ordinal,assigned_account_id)
                VALUES('j1','Cảnh 1',1,1,1,'b',0,'a1');
        """)
        db.commit()
        db.close()
        store = Store(path)
        account = store.snapshot()["accounts"][0]
        self.assertEqual((account["id"], account["email"], account["state"], account["detail"], account["budget"]),
                         ("a1", "x@tuan89.site", "ready", "cũ", 5))
        self.assertEqual((account["health"], account["last_checked"], account["jobs_assigned"]), ("UNKNOWN", 0, 1))
        job = store.snapshot()["jobs"][0]
        self.assertEqual((job["id"], job["assigned_account_id"]), ("j1", "a1"))
        Store(path)  # mở lần hai: không lỗi cột trùng

    def test_check_never_reserves_running_account(self):
        a = self.account()
        self.store.enqueue(["Cảnh"], 1, uuid.uuid4().hex)
        job, _ = self.store.claim(2)
        self.assertIsNone(self.store.begin_check(a["id"]))
        self.assertEqual(self.store.account(a["id"])["state"], "busy")
        self.store.finish(job["id"], output="x.mp4")
        account = self.store.snapshot()["accounts"][0]
        self.assertEqual((account["state"], account["health"], account["jobs_completed"]), ("ready", "LIVE", 1))
        self.assertGreater(account["last_checked"], 0)

    def test_checking_account_gets_no_job_login_or_delete_but_keeps_assignment(self):
        a = self.account()
        self.store.enqueue(["Cảnh"], 1, uuid.uuid4().hex)
        self.assertEqual(self.store.begin_check(a["id"])["state"], "new")
        self.assertIsNone(self.store.begin_check(a["id"]))  # không kiểm tra chồng
        self.assertIsNone(self.store.claim(2))
        self.assertEqual(self.store.snapshot()["jobs"][0]["assigned_account_id"], a["id"])
        with self.assertRaises(ValueError):
            self.store.reserve_login(a["id"])
        with self.assertRaises(ValueError):
            self.store.delete_account(a["id"])
        with self.assertRaises(ValueError):
            self.store.configure_account(a["id"], True, 5)
        self.store.end_check(a["id"], "new", "LIVE")
        self.assertIsNotNone(self.store.claim(2))

    def test_network_rate_limit_and_unknown_do_not_mark_account_dead(self):
        a = self.account()
        self.store.set_account(a["id"], "ready", "Đã xác minh")
        for health in ("NETWORK_ERROR", "RATE_LIMITED", "UNKNOWN"):
            before = self.store.begin_check(a["id"])
            self.store.end_check(a["id"], before["state"], health, "lỗi tạm")
            row = self.store.account(a["id"])
            self.assertEqual((row["state"], row["health"], row["last_error"]), ("ready", health, "lỗi tạm"))
        self.assertEqual(self.store.account(a["id"])["check_failures"], 3)

    def test_suspended_moves_to_attention_and_gets_no_jobs(self):
        a = self.account()
        self.store.set_account(a["id"], "ready")
        self.store.enqueue(["Cảnh"], 1, uuid.uuid4().hex)
        before = self.store.begin_check(a["id"])
        self.store.end_check(a["id"], before["state"], "SUSPENDED", "Muse đã ngắt kết nối")
        row = self.store.account(a["id"])
        self.assertEqual((row["state"], row["health"]), ("attention", "SUSPENDED"))
        self.assertIsNone(self.store.claim(2))

    def test_login_required_moves_to_attention_and_ready_recovers(self):
        a = self.account()
        before = self.store.begin_check(a["id"])
        self.store.end_check(a["id"], before["state"], "LOGIN_REQUIRED", "Phiên Muse đã đăng xuất.")
        row = self.store.account(a["id"])
        self.assertEqual((row["state"], row["health"], row["detail"]), ("attention", "LOGIN_REQUIRED", "Phiên Muse đã đăng xuất."))
        before = self.store.begin_check(a["id"])
        self.store.end_check(a["id"], before["state"], "LIVE")
        row = self.store.account(a["id"])
        self.assertEqual((row["state"], row["health"], row["last_error"], row["check_failures"]), ("ready", "LIVE", "", 0))

    def test_restart_during_check_restores_account(self):
        a = self.account()
        self.store.set_account(a["id"], "ready")
        self.store.begin_check(a["id"])
        restarted = Store(self.root / "test.db")
        restarted.recover()
        row = restarted.account(a["id"])
        self.assertEqual((row["state"], row["health"]), ("new", "LIVE"))


class HealthMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.db")
        self.engine = Engine(self.store, self.root)
        self.world = {"open": 0, "peak": 0, "checked": [], "closed": [], "results": {}}

    def tearDown(self):
        self.engine.shutdown()
        self.engine.close_browsers()
        self.temp.cleanup()

    def accounts(self, count):
        self.store.add_accounts("\n".join(f"u{i}@tuan89.site" for i in range(count)), 10)
        return [a["id"] for a in self.store.snapshot()["accounts"]]

    def monitor(self, parallel, factory=None):
        def make(profile, downloads):
            return FakeBrowser(self.world, Path(profile).name)
        monitor = HealthMonitor(self.engine, factory or make, parallel=parallel, spacing=0)
        self.engine.health = monitor
        return monitor

    def wait_idle(self, monitor, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = monitor.status()
            if not status["queued"] and not status["checking"]:
                return
            time.sleep(0.05)
        self.fail("Kiểm tra chưa xong")

    def test_ten_accounts_respect_parallel_limit(self):
        for parallel in (2, 3, 4):
            with self.subTest(parallel=parallel):
                self.world.update(open=0, peak=0, checked=[], closed=[])
                self.store = Store(self.root / f"p{parallel}.db")
                self.engine.store = self.store
                ids = self.accounts(10)
                monitor = self.monitor(parallel)
                monitor.store = self.store
                self.assertEqual(monitor.check_all(), {"queued": 10, "busy": []})
                self.wait_idle(monitor)
                self.assertEqual(sorted(self.world["checked"]), sorted(ids))
                self.assertLessEqual(self.world["peak"], parallel)
                self.assertGreater(self.world["peak"], 1)
                self.assertEqual(sorted(self.world["closed"]), sorted(ids))  # Chrome ẩn tự mở đều được đóng
                self.assertTrue(all(a["state"] == "ready" and a["health"] == "LIVE"
                                    for a in self.store.snapshot()["accounts"]))

    def test_running_account_is_skipped_and_its_browser_untouched(self):
        ids = self.accounts(3)
        self.store.enqueue(["Cảnh"], 1, uuid.uuid4().hex)
        job, busy = self.store.claim(1)
        running = FakeBrowser(self.world, busy["id"])
        self.engine.browsers[busy["id"]] = running
        monitor = self.monitor(2)
        result = monitor.check_all()
        self.assertEqual((result["queued"], result["busy"]), (2, [busy["email"]]))
        self.wait_idle(monitor)
        self.assertNotIn(busy["id"], self.world["checked"])
        self.assertFalse(running.closed)
        self.assertEqual(self.store.account(busy["id"])["state"], "busy")
        self.assertEqual(monitor.check(busy["id"]), "BUSY")
        self.assertEqual(len(ids), 3)

    def test_reuses_idle_engine_browser_without_closing_it(self):
        (account_id,) = self.accounts(1)
        kept = FakeBrowser(self.world, account_id)
        self.engine.browsers[account_id] = kept
        monitor = self.monitor(1, factory=lambda *a: self.fail("Không được mở Chrome thứ hai cho cùng hồ sơ"))
        self.assertEqual(monitor.check(account_id), "LIVE")
        self.assertFalse(kept.closed)
        self.assertIs(self.engine.browsers[account_id], kept)

    def test_closed_chrome_is_dropped_then_checked_with_fresh_browser(self):
        (account_id,) = self.accounts(1)
        dead = FakeBrowser(self.world, account_id)
        dead.driver = type("D", (), {"window_handles": property(lambda self: (_ for _ in ()).throw(RuntimeError()))})()
        self.engine.browsers[account_id] = dead
        monitor = self.monitor(1)
        self.assertEqual(monitor.check(account_id), "LIVE")
        self.assertTrue(dead.closed)
        self.assertNotIn(account_id, self.engine.browsers)

    def test_profile_lock_or_chrome_failure_is_unknown_not_dead(self):
        (account_id,) = self.accounts(1)
        self.store.set_account(account_id, "ready")

        def locked(*args):
            raise RuntimeError("user data directory is already in use")
        monitor = self.monitor(1, factory=locked)
        self.assertEqual(monitor.check(account_id), "UNKNOWN")
        row = self.store.account(account_id)
        self.assertEqual(row["state"], "ready")
        self.assertIn("đang mở ở cửa sổ khác", row["last_error"])

    def test_network_loss_expiry_and_crash(self):
        a, b, c, d = self.accounts(4)
        for account_id in (a, b, c):
            self.store.set_account(account_id, "ready")
        self.world["results"] = {a: ("NETWORK_ERROR", "mạng"), b: ("LOGIN_REQUIRED", "đăng xuất"),
                                 c: RuntimeError("chrome crashed"), d: ("LOGIN_REQUIRED", "đăng xuất")}
        monitor = self.monitor(2)
        self.assertEqual([monitor.check(x) for x in (a, b, c, d)], ["NETWORK_ERROR", "EXPIRED", "UNKNOWN", "LOGIN_REQUIRED"])
        rows = {x: self.store.account(x) for x in (a, b, c, d)}
        self.assertEqual([rows[x]["state"] for x in (a, b, c, d)], ["ready", "attention", "ready", "attention"])
        self.assertEqual(sorted(self.world["closed"]), sorted([a, b, c, d]))


class Element:
    def __init__(self, name="", text="", displayed=True):
        self.accessible_name, self.text, self.displayed = name, text, displayed

    def is_displayed(self):
        return self.displayed


class Driver:
    def __init__(self, url="https://muse.ai/", elements=(), error=None):
        self.current_url, self.items, self.error, self.visited = url, list(elements), error, []

    def get(self, url):
        self.visited.append(url)
        if self.error:
            raise self.error

    def find_elements(self, by, selector):
        return self.items


class CheckSessionTests(unittest.TestCase):
    def browser(self, driver):
        browser = MuseBrowser.__new__(MuseBrowser)
        browser.driver = driver
        return browser

    def test_page_states(self):
        cases = [
            (Driver(elements=[Element("Nhắn tin")]), "LIVE"),
            (Driver(elements=[Element("Số di động hoặc email")]), "LOGIN_REQUIRED"),
            (Driver(elements=[Element(text="Too Many Requests")]), "RATE_LIMITED"),
            (Driver(elements=[Element(text="Verify you are human")]), "LOGIN_REQUIRED"),
            (Driver(elements=[Element(text="Đã ngắt kết nối Muse"),
                              Element(text="Hãy gửi phiếu yêu cầu hỗ trợ để kết nối lại.")]), "SUSPENDED"),
            (Driver(url="chrome-error://chromewebdata/"), "NETWORK_ERROR"),
            (Driver(error=RuntimeError("unknown error: net::ERR_INTERNET_DISCONNECTED")), "NETWORK_ERROR"),
            (Driver(error=RuntimeError("timeout: Timed out receiving message from renderer")), "NETWORK_ERROR"),
            (Driver(url="https://evil.example/", elements=[Element("Nhắn tin")]), "UNKNOWN"),
        ]
        for driver, expected in cases:
            with self.subTest(expected=expected, url=driver.current_url):
                health, message = self.browser(driver).check_session(timeout=0)
                self.assertEqual(health, expected)
                self.assertNotRegex(message, r"\d{6}")
        self.assertEqual(cases[0][0].visited, ["https://muse.ai"])  # chỉ trang chủ, không gọi API nội bộ

    def test_disconnected_page_stops_login_and_jobs(self):
        from runner.muse import MuseFailure
        driver = Driver(elements=[Element(text="Đã ngắt kết nối Muse")])
        driver.current_window_handle = "main"
        browser = self.browser(driver)
        browser.phase = lambda text: None
        browser.headless = True
        with self.assertRaises(MuseFailure) as caught:
            browser.login({"email": "a@tuan89.site", "mailbox": ""})
        self.assertEqual(caught.exception.state, "attention")
        self.assertIn("ngắt kết nối", str(caught.exception))
        with self.assertRaises(MuseFailure):
            browser.guard()


class HealthHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")
        self.store.add_accounts("a@tuan89.site\nb@tuan89.site", 10)
        self.requests = []
        health = type("H", (), {
            "request": lambda h, ids: self.requests.append(ids) or {"queued": len(ids), "busy": []},
            "check_all": lambda h: {"queued": 2, "busy": []},
            "set_parallel": lambda h, n: HealthMonitor.set_parallel(h, n),
        })()
        engine = type("E", (), {"snapshot": lambda self: {}, "changed": threading.Event(), "health": health})()
        self.server = AppServer(0, self.store, engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, path, body, key=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("POST", path, body=json.dumps(body), headers={
            "Content-Type": "application/json", "Origin": self.server.origin,
            "X-Runner-Key": self.server.secret if key is None else key})
        response = connection.getresponse()
        data = json.loads(response.read() or b"{}")
        connection.close()
        return response.status, data

    def test_endpoints_require_key_and_valid_input(self):
        account_id = self.store.snapshot()["accounts"][0]["id"]
        self.assertEqual(self.post("/api/accounts/check", {"id": account_id}, key="sai")[0], 403)
        self.assertEqual(self.post("/api/accounts/check", {"id": "../x"})[0], 400)
        self.assertEqual(self.post("/api/accounts/check", {"id": account_id}), (200, {"ok": True, "queued": 1, "busy": []}))
        self.assertEqual(self.requests, [[account_id]])
        self.assertEqual(self.post("/api/accounts/check-all", {"parallel": 9})[0], 400)
        self.assertEqual(self.post("/api/accounts/check-all", {"parallel": 3})[0], 200)

    def test_state_exposes_health_but_no_secrets(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("GET", "/api/state")
        state = json.loads(connection.getresponse().read())
        connection.close()
        account = state["accounts"][0]
        for field in ("health", "last_checked", "last_error", "jobs_assigned", "jobs_completed", "jobs_failed"):
            self.assertIn(field, account)
        self.assertNotIn("mailbox", account)
        self.assertFalse({"cookie", "cookies", "otp", "token"} & set(account))


if __name__ == "__main__":
    unittest.main()
