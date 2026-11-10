"""Chạy hàng loạt: hết hạn mức → chia prompt cho tài khoản khác và báo ngày đặt lại; Muse lỗi → tự chạy lại;
3 tác vụ liền lỗi → tạm dừng tài khoản."""
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from runner import muse
from runner.store import Store


class DetectTests(unittest.TestCase):
    def test_quota_server_and_refusal_are_told_apart(self):
        quota = muse.reply_problem("Gói miễn phí · Đã dùng 100% · Giới hạn hàng tuần sẽ đặt lại vào 17 tháng 11")
        self.assertEqual((quota[1], quota[5]), ("limited", "quota"))
        self.assertEqual(time.strftime("%d/%m", time.localtime(quota[6])), "17/11")
        self.assertEqual(muse.reply_problem("You've reached your weekly limit. It resets on Nov 17.")[5], "quota")
        self.assertEqual(muse.reply_problem("An unexpected error occurred. Please try again.")[5], "server")
        self.assertEqual(muse.reply_problem("Mình không thể tạo video này.")[5], "refusal")
        self.assertIsNone(muse.reply_problem("Video đã xong, đúng 10 giây, 16:9."))
        self.assertEqual(muse.quota_reset("không có ngày"), 0.0)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name) / "t.db")
        self.store.add_account("a@tuan89.site", "", 100)
        self.store.add_account("b@tuan89.site", "", 100)
        self.a, self.b = [x["id"] for x in self.store.snapshot()["accounts"]]

    def tearDown(self):
        self.folder.cleanup()

    def jobs(self):
        return {j["id"]: j for j in self.store.snapshot()["jobs"]}

    def account(self, account_id):
        return [x for x in self.store.snapshot()["accounts"] if x["id"] == account_id][0]

    def test_quota_moves_every_prompt_to_other_accounts_until_reset(self):
        self.store.enqueue(["1", "2", "3", "4"], 1, uuid.uuid4().hex)
        job, acc = self.store.claim(1)
        self.store.submitting(job["id"])
        self.store.mark_limited(acc["id"], time.time() + 3600, "hết hạn mức")
        self.assertTrue(self.store.requeue_failed(job["id"], "hết hạn mức", limit=10 ** 6))
        other = self.b if acc["id"] == self.a else self.a
        waiting = [j for j in self.jobs().values() if j["status"] == "waiting"]
        self.assertEqual(len(waiting), 4)
        self.assertTrue(all(j["assigned_account_id"] == other for j in waiting))
        self.assertEqual(self.account(acc["id"])["state"], "limited")
        self.assertEqual(self.account(acc["id"])["budget"], 100)  # credit trả lại
        import sqlite3
        db = sqlite3.connect(self.store.path)
        db.execute("UPDATE accounts SET limited_until=? WHERE id=?", (time.time() - 1, acc["id"]))
        db.commit()
        db.close()
        self.store.claim(1)
        self.assertNotEqual(self.account(acc["id"])["state"], "limited")  # hạn mức đặt lại: chạy tiếp

    def test_muse_errors_retry_twice_then_stop(self):
        self.store.enqueue(["1"], 1, uuid.uuid4().hex)
        for attempt in range(2):
            job, _ = self.store.claim(2)
            self.store.submitting(job["id"])
            self.assertTrue(self.store.requeue_failed(job["id"], "Muse lỗi"))
            self.assertIn(f"lần {attempt + 1}/2", self.jobs()[job["id"]]["phase"])
        job, _ = self.store.claim(2)
        self.assertFalse(self.store.requeue_failed(job["id"], "Muse lỗi"))

    def test_three_errors_in_a_row_flags_the_account(self):
        self.store.enqueue(["1", "2", "3"], 1, uuid.uuid4().hex)
        self.store.set_account(self.b, "attention")
        for _ in range(3):
            job, acc = self.store.claim(1)
            self.store.finish(job["id"], error="lỗi")
        self.assertTrue(self.store.failing_streak(self.a))


if __name__ == "__main__":
    unittest.main()
