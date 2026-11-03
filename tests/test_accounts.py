from pathlib import Path
import tempfile
import unittest
import uuid

from runner.engine import Engine
from runner.store import Store


class DeleteAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.db")

    def tearDown(self):
        self.temp.cleanup()

    def account(self, name, budget=10):
        self.store.add_account(f"{name}@tuan89.site", "", budget)
        return next(a for a in self.store.snapshot()["accounts"] if a["email"] == f"{name}@tuan89.site")

    def test_waiting_prompts_move_to_remaining_account(self):
        a, b = self.account("a"), self.account("b")
        self.store.enqueue([f"Cảnh {i}" for i in range(4)], 1, uuid.uuid4().hex)
        self.assertEqual(self.store.delete_account(a["id"]), "a@tuan89.site")
        snapshot = self.store.snapshot()
        self.assertEqual([x["email"] for x in snapshot["accounts"]], ["b@tuan89.site"])
        self.assertTrue(all(job["assigned_account_id"] == b["id"] for job in snapshot["jobs"]))

    def test_busy_or_running_account_cannot_be_deleted(self):
        a = self.account("a")
        self.store.enqueue(["Cảnh"], 1, uuid.uuid4().hex)
        job, account = self.store.claim(2)
        with self.assertRaisesRegex(ValueError, "đang chạy"):
            self.store.delete_account(account["id"])
        self.store.finish(job["id"], error="lỗi", account_state="attention")
        self.store.delete_account(a["id"])  # tài khoản "Cần kiểm tra" (hết hạn) xóa được
        self.assertEqual(self.store.snapshot()["accounts"], [])
        with self.assertRaises(ValueError):
            self.store.delete_account(a["id"])

    def test_engine_closes_browser_and_removes_profile(self):
        a = self.account("a")
        engine = Engine(self.store, self.root)
        try:
            profile = self.root / "profiles" / a["id"]
            (profile / "Default").mkdir(parents=True)
            closed = []
            engine.browsers[a["id"]] = type("B", (), {"close": lambda self: closed.append(True)})()
            engine.remove_account(a["id"])
            self.assertEqual(closed, [True])
            self.assertFalse(profile.exists())
            self.assertNotIn(a["id"], engine.browsers)
        finally:
            engine.shutdown()
