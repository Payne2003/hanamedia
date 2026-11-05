"""Cookie Manager: kiểm tra định dạng, chỉ giữ cookie muse.ai, mã hóa khi lưu, không lộ giá trị, nhập cookie ≠ LIVE."""
import http.client
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app import AppServer
from runner.cookies import CookieError, Vault, parse
from runner.engine import Engine
from runner.health import HealthMonitor
from runner.muse import MuseBrowser, MuseFailure
from runner.store import Store

SECRET = "SeCrEt-session-0123456789abcdef"
FUTURE = time.time() + 86400 * 30
EDITOR_JSON = json.dumps([
    {"name": "muse_session", "value": SECRET, "domain": ".muse.ai", "path": "/", "secure": True, "httpOnly": True,
     "sameSite": "no_restriction", "expirationDate": FUTURE},
    {"name": "csrf", "value": "c1", "domain": "muse.ai", "hostOnly": True, "session": True},
    {"name": "_ga", "value": "x", "domain": ".google.com"},
    {"name": "old", "value": "o", "domain": ".muse.ai", "expirationDate": time.time() - 10},
])


class ParseTests(unittest.TestCase):
    def test_cookie_editor_json(self):
        cookies, stats = parse(EDITOR_JSON)
        self.assertEqual((stats["count"], stats["foreign"], stats["expired"], stats["source"]), (2, 1, 1, "json"))
        session = next(c for c in cookies if c["name"] == "muse_session")
        self.assertEqual((session["sameSite"], session["secure"], session["httpOnly"], session["domain"]),
                         ("None", True, True, ".muse.ai"))
        self.assertAlmostEqual(session["expires"], FUTURE)
        self.assertNotIn("expires", next(c for c in cookies if c["name"] == "csrf"))
        self.assertEqual(parse(json.dumps({"cookies": json.loads(EDITOR_JSON)}))[1]["count"], 2)

    def test_header_string(self):
        cookies, stats = parse(f"Cookie: muse_session={SECRET}; csrf=c1;")
        self.assertEqual(stats["source"], "header")
        self.assertEqual([(c["name"], c["domain"], c["path"]) for c in cookies],
                         [("muse_session", ".muse.ai", "/"), ("csrf", ".muse.ai", "/")])
        self.assertEqual(parse(f"muse_session={SECRET}")[1]["count"], 1)

    def test_invalid_input_errors_never_echo_values(self):
        cases = ["", "   ", "[not json", '{"x": 1}', "[1]", json.dumps([{"name": "bad name", "value": SECRET}]),
                 json.dumps([{"name": "a", "value": SECRET + ";evil"}]), json.dumps([{"name": "a", "value": 5}]),
                 json.dumps([{"name": "a", "value": SECRET, "domain": ".evil.com"}]),
                 json.dumps([{"name": "a", "value": SECRET, "domain": "muse.ai.evil.com"}]),
                 json.dumps([{"name": "a", "value": SECRET, "expirationDate": 1}]),
                 json.dumps([{"name": "a", "value": SECRET, "path": "x"}]),
                 f"no-equals-{SECRET}", "x" * 100_001, json.dumps([{"name": f"n{i}", "value": "v"} for i in range(201)])]
        for text in cases:
            with self.subTest(text=text[:40]), self.assertRaises(CookieError) as caught:
                parse(text)
            self.assertNotIn(SECRET, str(caught.exception))


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    @unittest.skipIf(os.name == "nt", "Windows dùng DPAPI")
    def test_fernet_roundtrip_key_permissions_and_other_key_fails(self):
        vault = Vault(self.root)
        blob = vault.encrypt(SECRET.encode())
        self.assertTrue(blob.startswith(b"fernet:"))
        self.assertNotIn(SECRET.encode(), blob)
        self.assertEqual(vault.decrypt(blob), SECRET.encode())
        self.assertEqual(stat.S_IMODE((self.root / "cookie.key").stat().st_mode), 0o600)
        with self.assertRaises(CookieError):
            Vault(Path(tempfile.mkdtemp())).decrypt(blob)
        with self.assertRaises(CookieError):
            vault.decrypt(b"garbage")

    @unittest.skipUnless(os.name == "nt", "chỉ chạy trên Windows")
    def test_dpapi_roundtrip(self):
        vault = Vault(self.root)
        blob = vault.encrypt(SECRET.encode())
        self.assertTrue(blob.startswith(b"dpapi:"))
        self.assertNotIn(SECRET.encode(), blob)
        self.assertEqual(vault.decrypt(blob), SECRET.encode())
        self.assertFalse((self.root / "cookie.key").exists())


class FakeBrowser:
    def __init__(self, world, account_id):
        self.world, self.account_id = world, account_id

    def apply_cookies(self, cookies):
        self.world["applied"].setdefault(self.account_id, []).extend(c["name"] for c in cookies)
        return len(cookies)

    def check_session(self):
        names = self.world["applied"].get(self.account_id, [])
        return ("LIVE", "") if "muse_session" in names else ("LOGIN_REQUIRED", "Phiên Muse đã đăng xuất.")

    def close(self):
        pass


class CookieStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "t.db")
        self.store.add_accounts("a@tuan89.site\nb@tuan89.site", 10)
        self.a, self.b = [x["id"] for x in self.store.snapshot()["accounts"]]

    def raw_db(self):
        return (self.root / "t.db").read_bytes() + b"".join(
            p.read_bytes() for p in self.root.glob("t.db-*") if p.is_file())

    def test_saved_encrypted_and_never_exposed(self):
        before = self.store.account(self.a)
        stats = self.store.cookies.save(self.a, EDITOR_JSON)
        self.assertEqual(stats["count"], 2)
        self.assertNotIn(SECRET.encode(), self.raw_db())
        self.assertEqual([c["value"] for c in self.store.cookies.load(self.a) if c["name"] == "muse_session"], [SECRET])
        snapshot = json.dumps(self.store.snapshot(), ensure_ascii=False)
        self.assertNotIn(SECRET, snapshot)
        account = next(x for x in self.store.snapshot()["accounts"] if x["id"] == self.a)
        self.assertEqual((account["cookie_count"], account["cookie_applied_at"]), (2, None))
        after = self.store.account(self.a)
        self.assertEqual((after["state"], after["health"]), (before["state"], before["health"]))  # nhập ≠ LIVE
        self.assertEqual(next(x for x in self.store.snapshot()["accounts"] if x["id"] == self.b)["cookie_count"], 0)

    def test_replace_delete_and_cascade_with_account(self):
        self.store.cookies.save(self.a, EDITOR_JSON)
        self.store.cookies.save(self.a, "muse_session=new")
        self.assertEqual([c["value"] for c in self.store.cookies.load(self.a)], ["new"])
        self.assertEqual(self.store.cookies.delete(self.a), 1)
        self.assertIsNone(self.store.cookies.load(self.a))
        self.store.cookies.save(self.b, EDITOR_JSON)
        self.store.delete_account(self.b)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM account_cookies").fetchone()[0], 0)
        with self.assertRaises(CookieError):
            self.store.cookies.save("0" * 32, EDITOR_JSON)

    def test_restart_keeps_encrypted_cookies(self):
        self.store.cookies.save(self.a, EDITOR_JSON)
        self.assertEqual(len(Store(self.root / "t.db").cookies.load(self.a)), 2)

    def test_apply_then_health_decides_live(self):
        world = {"applied": {}}
        engine = Engine(self.store, self.root)
        try:
            monitor = HealthMonitor(engine, lambda profile, d: FakeBrowser(world, Path(profile).name), parallel=1, spacing=0)
            self.assertEqual(monitor.check(self.a, apply_cookies=True), "UNKNOWN")  # chưa có cookie
            self.assertIn("chưa có cookie", self.store.account(self.a)["last_error"])
            self.store.cookies.save(self.a, EDITOR_JSON)
            self.store.cookies.save(self.b, "csrf=only")
            self.assertEqual(monitor.check(self.a, apply_cookies=True), "LIVE")
            self.assertEqual(monitor.check(self.b, apply_cookies=True), "LOGIN_REQUIRED")
            accounts = {x["id"]: x for x in self.store.snapshot()["accounts"]}
            self.assertEqual((accounts[self.a]["state"], accounts[self.a]["cookie_result"]), ("ready", "LIVE"))
            self.assertEqual((accounts[self.b]["state"], accounts[self.b]["cookie_result"]), ("attention", "LOGIN_REQUIRED"))
            self.assertIsNotNone(accounts[self.a]["cookie_applied_at"])
            self.store.enqueue(["Cảnh"], 1, "batch-cookie-busy-0001")
            job, busy = self.store.claim(1)
            self.assertEqual(monitor.request([busy["id"]], apply_cookies=True), {"queued": 0, "busy": [busy["email"]]})
        finally:
            engine.shutdown()


class ApplyCookiesTests(unittest.TestCase):
    def test_only_muse_cookies_reach_chrome(self):
        calls = []
        driver = type("D", (), {"execute_cdp_cmd": lambda self, cmd, params: calls.append((cmd, params)) or {"success": True}})()
        browser = MuseBrowser.__new__(MuseBrowser)
        browser.driver = driver
        cookies, _ = parse(EDITOR_JSON)
        self.assertEqual(browser.apply_cookies(cookies + [{"name": "x", "value": "y", "domain": ".evil.com"}]), 2)
        self.assertEqual({c[0] for c in calls}, {"Network.setCookie"})
        self.assertTrue(all(c[1]["domain"].endswith("muse.ai") for c in calls))
        failing = type("D", (), {"execute_cdp_cmd": lambda self, cmd, params: {"success": False}})()
        browser.driver = failing
        with self.assertRaises(MuseFailure):
            browser.apply_cookies(cookies)


class CookieHTTPTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "t.db")
        self.store.add_account("a@tuan89.site", "", 10)
        self.account = self.store.snapshot()["accounts"][0]["id"]
        self.requests = []
        health = type("H", (), {"request": lambda h, ids, apply_cookies=False: self.requests.append((ids, apply_cookies))
                                or {"queued": 1, "busy": []}})()
        engine = type("E", (), {"snapshot": lambda e: {}, "changed": threading.Event(), "health": health})()
        self.server = AppServer(0, self.store, engine)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method, path, body=None, key=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Origin": self.server.origin, "Content-Type": "application/json"}
        if key:
            headers["X-Runner-Key"] = self.server.secret
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw

    def test_import_apply_delete_and_no_leak(self):
        self.assertEqual(self.call("POST", "/api/accounts/cookies", {"id": self.account, "cookies": EDITOR_JSON}, key=False)[0], 403)
        status, raw = self.call("POST", "/api/accounts/cookies", {"id": self.account, "cookies": EDITOR_JSON})
        self.assertEqual(status, 200)
        self.assertNotIn(SECRET.encode(), raw)
        self.assertEqual(json.loads(raw)["stats"]["count"], 2)
        self.assertEqual(self.requests, [([self.account], True)])
        status, raw = self.call("GET", "/api/state")
        self.assertNotIn(SECRET.encode(), raw)
        self.assertEqual(json.loads(raw)["accounts"][0]["cookie_count"], 2)
        status, raw = self.call("POST", "/api/accounts/cookies", {"id": self.account, "cookies": f"bad name={SECRET}"})
        self.assertEqual(status, 400)
        self.assertNotIn(SECRET.encode(), raw)
        self.assertEqual(self.call("POST", "/api/accounts/cookies/apply", {"id": self.account})[0], 200)
        self.assertEqual(self.call("POST", "/api/accounts/cookies", {"id": "../x", "cookies": EDITOR_JSON})[0], 400)
        status, raw = self.call("POST", "/api/accounts/cookies/delete", {"id": self.account})
        self.assertEqual((status, json.loads(raw)["deleted"]), (200, 1))
        self.assertEqual(self.call("POST", "/api/accounts/cookies/apply", {"id": self.account})[0], 400)


if __name__ == "__main__":
    unittest.main()
