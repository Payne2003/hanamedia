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
        # App đã gắn khóa gốc: máy khách không tạo được khóa Super Admin, và dù tự đặt một file khóa vào thư mục dữ liệu
        # thì khóa đó cũng không được tin cậy nên không thành Super Admin.
        rogue = LicenseManager(self.client_store, self.root / "client", trusted=[self.public])
        with self.assertRaisesRegex(LicenseError, "Chỉ máy Super Admin"):
            rogue.init_signing_key()
        rogue.write_key(rogue.key_file)
        self.assertFalse(rogue.issuer_trusted())
        self.assertEqual((rogue.status()["state"], rogue.status()["issuer"]), ("missing", False))

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
        self.assertEqual(self.admin.set_locked(account["id"], True)[0]["state"], "locked")
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


REVOCATION = {"repo": "Owner/app", "branch": "license-revocations", "file": "revoked.txt"}
TOKEN = "github_pat_" + "A1" * 20


class FakeGitHub:
    """Giả lập GitHub: API ghi file (cần token) và raw.githubusercontent.com để máy khách đọc."""

    def __init__(self):
        self.files, self.branches, self.offline, self.calls = {}, {"main"}, False, []

    def __call__(self, method, url, headers=None, body=None, timeout=15):
        self.calls.append((method, url, dict(headers or {})))
        if self.offline:
            raise OSError("mất mạng")
        if url.startswith("https://raw.githubusercontent.com/Owner/app/"):
            branch, _, name = url.split("?")[0][len("https://raw.githubusercontent.com/Owner/app/"):].partition("/")
            text = self.files.get((branch, name))
            return (200, text.encode()) if text is not None else (404, b"404: Not Found")
        if (headers or {}).get("Authorization") != f"Bearer {TOKEN}":
            return 401, b'{"message":"Bad credentials"}'
        path = url[len("https://api.github.com"):]
        if path == "/repos/Owner/app":
            return 200, b'{"default_branch":"main"}'
        if path.startswith("/repos/Owner/app/git/ref/heads/"):
            name = path.rsplit("/", 1)[1]
            return (200, b'{"object":{"sha":"abc"}}') if name in self.branches else (404, b"{}")
        if path == "/repos/Owner/app/git/refs" and method == "POST":
            self.branches.add(json.loads(body)["ref"].rsplit("/", 1)[1])
            return 201, b"{}"
        if path.startswith("/repos/Owner/app/contents/"):
            import base64
            name = path.split("?")[0].rsplit("/", 1)[1]
            if method == "GET":
                branch = path.split("ref=")[1]
                if branch not in self.branches:
                    return 404, b'{"message":"No commit found for the ref"}'
                return (200, b'{"sha":"s1"}') if (branch, name) in self.files else (404, b"{}")
            data = json.loads(body)
            if data["branch"] not in self.branches or (((data["branch"], name) in self.files) != bool(data.get("sha"))):
                return 409, b"{}"
            self.files[(data["branch"], name)] = base64.b64decode(data["content"]).decode()
            return 201, b"{}"
        return 404, b"{}"


class RevocationTests(unittest.TestCase):
    """Khóa / xóa trên máy Super Admin thì máy khách ngừng dùng được (danh sách thu hồi ký số trên GitHub)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.clock = Clock(date(2026, 1, 1))
        self.github = FakeGitHub()
        folder = self.root / "root"
        folder.mkdir()
        self.public = LicenseManager(Store(folder / "t.db"), folder, trusted=[]).init_signing_key()
        self.root_m, self.agent, self.customer = (self.manager(n) for n in ("root", "agent", "customer"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def manager(self, name, trusted=None):
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        return LicenseManager(Store(folder / "t.db"), folder, trusted=trusted or [self.public], today=self.clock.today,
                              clock=self.clock.time, revocation=REVOCATION, http=self.github)

    def give(self, issuer, who, role="user", **extra):
        account, token = issuer.save_account({"name": "Khách " + role, "role": role, "plan": "1y",
                                              "start": "2026-01-01", "devices": [who.request_code()], **extra})
        return account, who.activate(token)

    def test_lock_unlock_and_delete_reach_the_customer(self):
        self.assertEqual(self.root_m.set_publisher_token(TOKEN)["has_token"], True)
        self.assertIn("license-revocations", self.github.branches)  # nhánh riêng được tạo ở lần đăng đầu tiên
        account, status = self.give(self.root_m, self.customer)
        self.assertEqual(status["state"], "active")
        _, info = self.root_m.set_locked(account["id"], True)
        self.assertEqual((info["error"], info["count"], info["pending"]), ("", 1, False))
        self.assertNotIn(TOKEN, json.dumps(self.root_m.dashboard()))  # token không bao giờ ra giao diện
        self.assertTrue(self.customer.check_revocations()["ok"])
        status = self.customer.status()
        self.assertEqual((status["state"], status["allowed"]), ("revoked", []))
        self.root_m.set_locked(account["id"], False)
        self.customer.check_revocations()
        self.assertEqual(self.customer.status()["state"], "active")
        self.root_m.delete_account(account["id"])
        self.assertEqual(self.root_m.dashboard()["accounts"], [])
        self.customer.check_revocations()
        self.assertEqual(self.customer.status()["state"], "revoked")
        with self.assertRaises(LicenseError):
            self.root_m.delete_account(account["id"])

    def test_renewing_a_locked_account_unrevokes_it(self):
        self.root_m.set_publisher_token(TOKEN)
        account, _ = self.give(self.root_m, self.customer)
        self.root_m.set_locked(account["id"], True)
        _, token = self.root_m.renew(account["id"], "1m")
        self.customer.activate(token)
        self.assertEqual(self.customer.status()["state"], "active")

    def test_without_token_change_is_pending(self):
        account, _ = self.give(self.root_m, self.customer)
        _, info = self.root_m.set_locked(account["id"], True)
        self.assertTrue(info["pending"])
        self.assertIn("token GitHub", info["error"])
        self.customer.check_revocations()
        self.assertEqual(self.customer.status()["state"], "active")  # chưa đăng thì khách chưa bị chặn
        with self.assertRaisesRegex(LicenseError, "github_pat_"):
            self.root_m.set_publisher_token("mật khẩu")
        info = self.root_m.set_publisher_token(TOKEN)
        self.assertEqual((info["pending"], info["error"]), (False, ""))
        self.customer.check_revocations()
        self.assertEqual(self.customer.status()["state"], "revoked")

    def test_offline_grace_then_recovers(self):
        account, status = self.give(self.root_m, self.customer)
        self.assertEqual(status["state"], "active")  # chưa có danh sách (404): coi như chưa thu hồi gì
        self.github.offline = True
        self.clock.day = date(2026, 1, 3)
        self.assertFalse(self.customer.check_revocations()["ok"])
        self.assertEqual(self.customer.status()["state"], "active")  # còn trong 3 ngày
        self.clock.day = date(2026, 1, 5)
        self.assertEqual(self.customer.status()["state"], "offline")
        self.assertTrue(self.customer.needs_check())
        self.github.offline = False
        self.customer.check_revocations()
        self.assertEqual(self.customer.status()["state"], "active")

    def test_forged_old_or_deleted_lists_are_rejected(self):
        self.root_m.set_publisher_token(TOKEN)
        account, _ = self.give(self.root_m, self.customer)
        self.root_m.set_locked(account["id"], True)
        self.customer.check_revocations()
        signed = self.github.files[("license-revocations", "revoked.txt")]
        from runner.license import REV_PREFIX
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        other = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                           serialization.NoEncryption())
        self.github.files[("license-revocations", "revoked.txt")] = sign(other, {"kid": self.customer_kid(), "seq": 10**15,
                                                                               "ids": []}, REV_PREFIX)
        self.assertIn("không hợp lệ", self.customer.check_revocations()["error"])
        self.assertEqual(self.customer.status()["state"], "revoked")
        self.root_m.set_locked(account["id"], False)
        self.github.files[("license-revocations", "revoked.txt")] = signed  # phát lại bản cũ (vẫn đang thu hồi)
        self.root_m.publish()
        newer = self.github.files[("license-revocations", "revoked.txt")]
        self.customer.check_revocations()
        self.assertEqual(self.customer.status()["state"], "active")
        self.github.files[("license-revocations", "revoked.txt")] = signed
        self.assertIn("cũ hơn", self.customer.check_revocations()["error"])
        self.assertEqual(self.customer.status()["state"], "active")
        del self.github.files[("license-revocations", "revoked.txt")]  # đã từng có danh sách: 404 là lỗi
        self.assertFalse(self.customer.check_revocations()["ok"])
        self.github.files[("license-revocations", "revoked.txt")] = newer

    def customer_kid(self):
        from runner.license import key_id
        return key_id(unb64(self.public))

    def test_revoking_an_agent_stops_its_customers(self):
        self.root_m.set_publisher_token(TOKEN)
        agent_account, status = self.give(self.root_m, self.agent, role="admin")
        self.assertEqual(status["issuer_mode"], "delegate")
        customer_account, status = self.give(self.agent, self.customer)
        self.assertEqual(status["state"], "active")
        _, info = self.agent.set_locked(customer_account["id"], True)
        self.assertTrue(info["local"])  # đại lý khóa: chỉ trên máy đại lý
        with self.assertRaises(LicenseError):
            self.agent.publish()
        self.agent.delete_account(customer_account["id"])
        self.root_m.set_locked(agent_account["id"], True)
        for machine in (self.agent, self.customer):
            machine.check_revocations()
            self.assertEqual(machine.status()["state"], "revoked")
        self.assertFalse(self.agent.status()["issuer"])

    def test_owner_machine_never_checks(self):
        self.assertFalse(self.root_m.needs_check())
        self.assertFalse(self.customer.needs_check())  # chưa kích hoạt key
        self.give(self.root_m, self.customer)
        self.assertFalse(self.customer.needs_check())  # vừa kiểm tra lúc kích hoạt
        self.clock.day = date(2026, 1, 2)
        self.assertTrue(self.customer.needs_check())


class DelegationTests(unittest.TestCase):
    """Super Admin cấp role quản lý cho máy khác; máy đó cấp key cho khách của mình trong giới hạn được giao."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.clock = Clock(date(2026, 1, 1))
        managers = {}
        root_dir = self.root / "root"
        root_dir.mkdir()
        root = LicenseManager(Store(root_dir / "t.db"), root_dir, trusted=[], today=self.clock.today, clock=self.clock.time)
        self.public = root.init_signing_key()
        for name in ("root", "agent", "customer", "third"):
            folder = self.root / name
            folder.mkdir(exist_ok=True)
            managers[name] = LicenseManager(Store(folder / "t.db"), folder, trusted=[self.public],
                                            today=self.clock.today, clock=self.clock.time)
        self.root_m, self.agent, self.customer, self.third = (managers[n] for n in ("root", "agent", "customer", "third"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def grant_agent(self, role="admin", plan="1y", permissions=None):
        data = {"name": "Đại lý A", "role": role, "plan": plan, "start": "2026-01-01",
                "devices": [self.agent.request_code()]}
        if permissions is not None:
            data["permissions"] = permissions
        _, token = self.root_m.save_account(data)
        return self.agent.activate(token)

    def test_request_code_and_root_owner(self):
        code = self.customer.request_code()
        self.assertRegex(code, r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}\.[A-Za-z0-9_-]{43}$")
        self.assertEqual(code, self.customer.request_code())  # ổn định qua các lần gọi
        status = self.root_m.status()
        self.assertEqual((status["state"], status["issuer_mode"]), ("owner", "root"))
        self.assertEqual(self.customer.status()["issuer"], False)

    def test_agent_becomes_its_role_and_issues_keys_within_limits(self):
        status = self.grant_agent()
        self.assertEqual((status["state"], status["role"], status["issuer"], status["issuer_mode"]),
                         ("active", "admin", True, "delegate"))
        scope = self.agent.dashboard()["scope"]
        self.assertEqual(scope["roles"], ["admin", "manager", "user", "viewer"])
        self.assertEqual(scope["max_expires"], "2026-12-31")
        _, token = self.agent.save_account({"name": "Khách B", "role": "user", "plan": "1m", "start": "2026-01-01",
                                            "devices": [self.customer.request_code()]})
        status = self.customer.activate(token)  # máy khách chỉ tin khóa gốc, vẫn chấp nhận vì có chuỗi ủy quyền
        self.assertEqual((status["state"], status["role"], status["issued_by"], status["issuer"]),
                         ("active", "user", "Đại lý A", False))
        with self.assertRaisesRegex(LicenseError, "role từ Admin"):
            self.agent.save_account({"name": "X", "role": "super_admin", "plan": "1m", "start": "2026-02-01"})
        with self.assertRaisesRegex(LicenseError, "Hạn tối đa"):
            self.agent.save_account({"name": "X", "role": "user", "plan": "3y", "start": "2026-02-01"})
        with self.assertRaisesRegex(LicenseError, "những quyền"):
            self.agent.save_account({"name": "X", "role": "user", "plan": "1m", "start": "2026-02-01",
                                     "permissions": ["view_audit"]})

    def test_super_admin_role_can_be_granted_and_grant_on(self):
        self.grant_agent(role="super_admin", plan="3y")
        self.assertIn("super_admin", self.agent.dashboard()["scope"]["roles"])
        _, token = self.agent.save_account({"name": "Super 2", "role": "super_admin", "plan": "1y",
                                            "start": "2026-01-01", "devices": [self.customer.request_code()]})
        self.assertEqual(self.customer.activate(token)["issuer_mode"], "delegate")
        _, grandchild = self.customer.save_account({"name": "Khách C", "role": "viewer", "plan": "1m",
                                                    "start": "2026-01-01"})
        self.assertEqual(self.third.activate(grandchild)["role"], "viewer")  # chuỗi 2 tầng về khóa gốc

    def test_roles_without_issue_rights_or_without_device_key_cannot_issue(self):
        self.grant_agent(role="user")
        self.assertFalse(self.agent.status()["issuer"])
        with self.assertRaises(LicenseError):
            self.agent.dashboard()
        _, token = self.root_m.save_account({"name": "Admin không mã", "role": "admin", "plan": "1y",
                                             "start": "2026-01-01", "devices": [machine_code()]})
        self.agent.activate(token)
        self.assertFalse(self.agent.status()["issuer"])  # không có khóa của máy trong key: chỉ dùng tool

    def test_forged_delegation_is_rejected(self):
        self.grant_agent(role="manager")
        own = self.agent.device_key()
        parent = self.agent.store.setting("license_token")
        from runner.license import key_id, public_from_private
        base = {"v": 1, "kid": key_id(public_from_private(own)), "id": "x", "name": "Giả", "plan": "1m",
                "start": "2026-01-01", "expires": "2026-02-01", "devices": [], "chain": parent}
        for bad in ({"role": "admin", "permissions": ["create_jobs"]},           # role cao hơn người cấp
                    {"role": "user", "permissions": ["manage_roles"]},          # quyền vượt phạm vi
                    {"role": "user", "permissions": [], "expires": "2099-01-01"}):  # hạn vượt
            with self.subTest(bad=bad), self.assertRaises(LicenseError):
                self.customer.activate(sign(own, {**base, **bad}))
        stranger = self.customer.device_key()  # ký bằng khóa không có trong key của người cấp
        with self.assertRaises(LicenseError):
            self.customer.activate(sign(stranger, {**base, "kid": key_id(public_from_private(stranger)),
                                                   "role": "user", "permissions": []}))
        self.assertEqual(self.customer.activate(sign(own, {**base, "role": "user", "permissions": ["create_jobs"]}))["state"],
                         "active")

    def test_default_trust_only_applies_to_packaged_app(self):
        from unittest import mock
        from runner import license as licensing
        folder = self.root / "default"
        folder.mkdir()
        self.assertFalse(LicenseManager(Store(folder / "t.db"), folder).enforced())  # chạy từ mã nguồn / test
        with mock.patch.object(licensing, "frozen", return_value=True):
            self.assertTrue(LicenseManager(Store(folder / "t.db"), folder).enforced())  # bản .exe


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
        status, body = self.call("POST", "/api/license/lock", {"id": account["id"], "locked": True})
        self.assertEqual((status, body["account"]["state"]), (200, "locked"))
        self.assertIn("revocation", body)
        self.assertEqual(self.call("POST", "/api/license/delete", {"id": account["id"]})[0], 400)  # thiếu xác nhận
        self.assertEqual(self.call("POST", "/api/license/delete", {"id": account["id"], "confirm": True})[0], 200)
        self.assertEqual(self.call("GET", "/api/license")[1]["dashboard"]["accounts"], [])
        self.assertEqual(self.call("POST", "/api/license/check", {})[0], 200)


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
