"""Bản quyền ký số: Super Admin tạo/gia hạn key, máy khách kích hoạt; khóa chạy video/tải media khi hết hạn, sai máy,
key giả, chỉnh lùi đồng hồ, hết quota; chưa gắn khóa công khai thì không khóa gì."""
import http.client
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path

from app import AppServer
from runner.engine import Engine
from runner.license import (LicenseError, LicenseManager, add_months, b64, machine_code, sign, unb64)
from runner.store import Store


class Clock:
    def __init__(self, today):
        self.day = today

    def today(self):
        return self.day

    def time(self):
        return time.mktime(self.day.timetuple()) + 12 * 3600


class LicenseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        for folder in ("admin", "client"):
            (self.root / folder).mkdir()
        self.clock = Clock(date(2025, 3, 1))
        self.admin_store = Store(self.root / "admin" / "t.db")
        self.admin = LicenseManager(self.admin_store, self.root / "admin", trusted=[], today=self.clock.today,
                                    clock=self.clock.time)
        self.public = self.admin.init_signing_key()
        self.admin.trusted = {k: v for k, v in [(self.kid(), unb64(self.public))]}
        self.client_store = Store(self.root / "client" / "t.db")
        self.client = self.client_manager()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def kid(self):
        from runner.license import key_id
        return key_id(unb64(self.public))

    def client_manager(self, store=None):
        return LicenseManager(store or self.client_store, self.root / "client", trusted=[self.public],
                              today=self.clock.today, clock=self.clock.time)

    def create(self, **extra):
        data = {"name": "Admin Marketing", "email": "admin@hanamedia.ai", "role": "user", "plan": "1y",
                "start": "2025-03-01", "devices": [machine_code()], "quota": 0, "amount": 1_200_000, **extra}
        return self.admin.save_account(data)

    def test_plan_dates_match_the_spec(self):
        start = date(2025, 3, 1)
        self.assertEqual([add_months(start, n) for n in (1, 12, 36)],
                         [date(2025, 3, 31), date(2026, 2, 28), date(2028, 2, 29)])
        self.assertEqual(add_months(date(2025, 2, 15), 12), date(2026, 2, 14))

    def test_not_enforced_without_trusted_key(self):
        open_manager = LicenseManager(self.client_store, self.root / "client", trusted=[])
        status = open_manager.status()
        self.assertEqual(status["state"], "not_enforced")
        self.assertTrue(open_manager.allows("create_jobs") and open_manager.allows("upload_media"))
        with self.assertRaises(LicenseError):
            open_manager.activate("HANA1.x.y")

    def test_signing_key_is_private_and_single(self):
        key = self.root / "admin" / "license" / "signing.key"
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        with self.assertRaises(LicenseError):
            self.admin.init_signing_key()
        self.assertTrue(self.admin.issuer_trusted())
        self.assertEqual(self.admin.status()["state"], "owner")

    def test_activation_roles_and_expiry(self):
        self.assertEqual(self.client.status()["state"], "missing")
        self.assertFalse(self.client.allows("create_jobs"))
        account, token = self.create()
        self.assertEqual(account["expires"], "2026-02-28")
        status = self.client.activate(token)
        self.assertEqual((status["state"], status["role"], status["allowed"]), ("active", "user", ["create_jobs", "upload_media"]))
        self.assertEqual(self.client_manager(Store(self.root / "client" / "t.db")).status()["state"], "active")  # restart
        self.clock.day = date(2026, 2, 25)
        self.assertEqual(self.client.status()["state"], "expiring")
        self.clock.day = date(2026, 3, 1)
        status = self.client.status()
        self.assertEqual((status["state"], status["allowed"]), ("expired", []))
        with self.assertRaisesRegex(PermissionError, "hết hạn"):
            self.client.check("create_jobs")
        viewer, viewer_token = self.create(name="Viewer", role="viewer", start="2026-03-01")
        self.client.activate(viewer_token)
        self.assertEqual(self.client.status()["allowed"], [])
        with self.assertRaisesRegex(PermissionError, "không có quyền"):
            self.client.check("upload_media")

    def test_wrong_device_forged_and_foreign_keys_are_rejected(self):
        _, token = self.create(devices=["AAAA-BBBB-CCCC-DDDD"])
        with self.assertRaisesRegex(LicenseError, "không cấp cho máy này"):
            self.client.activate(token)
        _, good = self.create()
        head, body, signature = good.split(".")
        payload = json.loads(unb64(body))
        payload["expires"] = "2099-12-31"
        forged = f"{head}.{b64(json.dumps(payload).encode())}.{signature}"
        with self.assertRaisesRegex(LicenseError, "Chữ ký"):
            self.client.activate(forged)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        other = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                           serialization.NoEncryption())
        with self.assertRaisesRegex(LicenseError, "không do HANA MEDIA"):
            self.client.activate(sign(other, {**payload, "kid": "000000000000"}))
        for broken in ("", "hello", "HANA1.abc", good[:-10] + "AAAAAAAAAA"):
            with self.assertRaises(LicenseError):
                self.client.activate(broken)
        # Một khách tự tạo khóa ký riêng trên máy mình: không được app tin cậy nên không thành Super Admin.
        rogue = LicenseManager(self.client_store, self.root / "client", trusted=[self.public])
        rogue.init_signing_key()
        self.assertFalse(rogue.issuer_trusted())
        self.assertEqual(rogue.status()["state"], "missing")

    def test_clock_rollback_and_not_started(self):
        _, token = self.create(start="2025-04-01")
        self.client.activate(token)
        self.assertEqual(self.client.status()["state"], "not_started")
        self.clock.day = date(2025, 5, 1)
        self.assertEqual(self.client.status()["state"], "active")
        self.clock.day = date(2025, 4, 20)
        self.assertEqual(self.client.status()["state"], "clock")

    def test_quota(self):
        _, token = self.create(quota=3)
        self.client.activate(token)
        license_id = self.client.reserve_jobs(2)
        with self.assertRaisesRegex(PermissionError, "Hết quota video: đã dùng 2/3"):
            self.client.reserve_jobs(2)
        self.client.refund_jobs(license_id, 1)
        self.client.reserve_jobs(2)
        self.assertEqual(self.client.status()["used"], 3)

    def test_renew_lock_and_dashboard(self):
        account, _ = self.create()
        self.clock.day = date(2025, 12, 1)
        renewed, token = self.admin.renew(account["id"], "1y", 1_000_000)
        self.assertEqual(renewed["expires"], "2027-02-28")  # cộng tiếp từ ngày hết hạn cũ
        self.clock.day = date(2027, 6, 1)
        late, _ = self.admin.renew(account["id"], "1m")
        self.assertEqual(late["expires"], "2027-06-30")  # đã hết hạn: tính từ hôm nay
        self.assertEqual(self.admin.set_locked(account["id"], True)["state"], "locked")
        self.create(name="Reseller", role="admin", plan="3y", amount=5_000_000)
        dashboard = self.admin.dashboard()
        self.assertEqual((dashboard["stats"]["total"], dashboard["stats"]["admins"]), (2, 1))
        self.assertEqual(dashboard["stats"]["revenue"], 1_200_000 + 1_000_000 + 5_000_000)
        self.assertEqual([e["action"] for e in dashboard["events"][:3]], ["create", "lock", "renew"])
        self.client.activate(token)
        self.assertEqual(self.client.status()["expires"], "2027-02-28")
        with self.assertRaises(LicenseError):
            self.admin.save_account({"name": "", "role": "user", "plan": "1y"})
        with self.assertRaises(LicenseError):
            self.admin.save_account({"name": "x", "role": "god", "plan": "1y"})
        with self.assertRaises(LicenseError):
            self.admin.save_account({"name": "x", "role": "user", "plan": "1y", "devices": "not-a-code"})
        with self.assertRaises(LicenseError):
            self.client.save_account({"name": "x", "role": "user", "plan": "1y"})  # máy khách không có khóa ký


class LicenseHTTPTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        issuer_store = Store(self.root / "admin.db")
        self.issuer = LicenseManager(issuer_store, self.root / "issuer", trusted=[])
        public = self.issuer.init_signing_key()
        self.store = Store(self.root / "t.db")
        self.store.add_account("a@tuan89.site", "", 100)
        engine = type("E", (), {"snapshot": lambda e: {}, "changed": threading.Event()})()
        self.server = AppServer(0, self.store, engine)
        self.server.license = LicenseManager(self.store, self.root, trusted=[public])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, json.dumps(body) if body is not None else None,
                           {"Origin": self.server.origin, "X-Runner-Key": self.server.secret,
                            "Content-Type": "application/json"})
        response = connection.getresponse()
        result = (response.status, json.loads(response.read() or b"{}"))
        connection.close()
        return result

    def jobs(self, n=1):
        return self.call("POST", "/api/jobs", {"prompts": [f"Cảnh {i}" for i in range(n)], "cost": 1,
                                               "batch": str(uuid.uuid4())})

    def test_gate_activate_quota_and_viewer(self):
        status, body = self.jobs()
        self.assertEqual((status, body.get("license")), (403, True))
        self.assertIn("Chưa kích hoạt", body["error"])
        self.assertEqual(self.call("POST", "/api/media/upload", None)[0], 403)
        self.assertEqual(self.call("GET", "/api/state")[1]["license"]["state"], "missing")
        self.assertNotIn("dashboard", self.call("GET", "/api/license")[1])  # máy khách không có sổ bản quyền
        _, token = self.issuer.save_account({"name": "Khách A", "role": "user", "plan": "1m", "quota": 2,
                                              "start": date.today().isoformat()})
        self.assertEqual(self.call("POST", "/api/license/activate", {"key": "HANA1.bad.key"})[0], 400)
        status, body = self.call("POST", "/api/license/activate", {"key": token})
        self.assertEqual((status, body["status"]["state"]), (200, "active"))
        self.assertEqual(self.jobs(2)[0], 200)
        status, body = self.jobs(1)
        self.assertEqual(status, 403)
        self.assertIn("quota", body["error"])
        self.assertEqual(len(self.store.snapshot()["jobs"]), 2)
        _, viewer = self.issuer.save_account({"name": "Xem", "role": "viewer", "plan": "1y",
                                              "start": date.today().isoformat()})
        self.call("POST", "/api/license/activate", {"key": viewer})
        self.assertEqual(self.call("PATCH", "/api/media/item/" + "a" * 32, {"display_name": "x"})[0], 403)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)  # vẫn xem được dữ liệu
        self.assertEqual(self.call("POST", "/api/settings/community", {"groups": [], "videos": []})[0], 200)

    def test_issuer_endpoints(self):
        self.server.license = self.issuer
        status, body = self.call("POST", "/api/license/accounts", {"name": "Manager", "role": "manager", "plan": "3y",
                                                                   "amount": 3_000_000})
        self.assertEqual(status, 200)
        self.assertTrue(body["key"].startswith("HANA1."))
        account = body["account"]
        self.assertEqual(account["permissions"], ["create_users", "renew", "view_revenue", "create_jobs", "upload_media"])
        status, body = self.call("POST", "/api/license/renew", {"id": account["id"], "plan": "1m", "amount": 100_000})
        self.assertEqual(status, 200)
        dashboard = self.call("GET", "/api/license")[1]["dashboard"]
        self.assertEqual((dashboard["stats"]["total"], dashboard["stats"]["revenue"]), (1, 3_100_000))
        self.assertEqual(self.call("POST", "/api/license/renew", {"id": account["id"], "plan": "5y"})[0], 400)
        self.assertEqual(self.call("POST", "/api/license/init-signing", {"confirm": True})[0], 400)


class EngineGateTests(unittest.TestCase):
    def test_engine_does_not_claim_without_license(self):
        root = Path(tempfile.mkdtemp())
        store = Store(root / "t.db")
        store.add_account("a@tuan89.site", "", 100)
        store.enqueue(["Cảnh"], 1, uuid.uuid4().hex)
        engine = Engine(store, root)
        try:
            engine.license_ok = lambda: False
            engine.enabled = True
            engine.changed.set()
            time.sleep(1.5)
            self.assertEqual(store.snapshot()["totals"]["running"], 0)
        finally:
            engine.shutdown()
            engine.close_browsers()
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
