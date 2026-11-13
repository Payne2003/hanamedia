"""Nhiều luồng trên một tài khoản Muse: chia tác vụ (Store.claim) và chia tab (TabRouter)."""
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from runner.store import DEFAULT_BUDGET, Store
from runner.tabs import TabRouter


class AccountThreadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name) / "test.db")
        self.store.add_account("a@tuan89.site", "", DEFAULT_BUDGET)
        self.account = self.store.snapshot()["accounts"][0]
        self.store.enqueue(["Một", "Hai", "Ba"], 1, uuid.uuid4().hex)

    def tearDown(self):
        self.folder.cleanup()

    def state(self):
        return self.store.snapshot()["accounts"][0]

    def test_new_account_defaults_to_one_thread_and_100_credit(self):
        self.assertEqual((self.account["threads"], self.account["budget"]), (1, 100))

    def test_second_tab_only_after_first_prompt_is_sent_and_up_to_thread_count(self):
        self.store.configure_account(self.account["id"], True, 100, 2)
        first, _ = self.store.claim(1)
        self.assertIsNone(self.store.claim(1))  # prompt 1 chưa gửi: chưa mở tab 2
        self.store.submitting(first["id"])
        second, account = self.store.claim(1)  # đủ 1 Chrome nhưng tài khoản còn luồng: mở thêm tab
        self.assertEqual(account["id"], self.account["id"])
        self.store.submitting(second["id"])
        self.assertIsNone(self.store.claim(1))  # 2/2 luồng
        self.assertEqual(self.state()["jobs_running"], 2)
        self.store.finish(first["id"], output="x.mp4")
        self.assertEqual(self.state()["state"], "busy")  # tab còn lại vẫn chạy
        third, _ = self.store.claim(1)
        self.store.finish(second["id"], output="y.mp4")
        self.store.finish(third["id"], output="z.mp4")
        self.assertEqual(self.state()["state"], "ready")
        self.assertEqual(self.state()["budget"], 97)

    def test_upgrade_resets_threads_and_requeues_prompts_failed_while_muse_was_busy(self):
        import sqlite3
        job, _ = self.store.claim(1)
        self.store.finish(job["id"], error="Muse đang xử lý yêu cầu trước đó. Đợi hoàn tất rồi đăng nhập lại để tiếp tục.")
        db = sqlite3.connect(self.store.path)
        db.execute("UPDATE accounts SET threads=2")
        db.execute("DELETE FROM settings WHERE key='threads_reset_v1'")
        db.commit()
        db.close()
        store = Store(self.store.path)  # mở lại app sau khi cập nhật
        self.assertEqual(store.snapshot()["accounts"][0]["threads"], 1)
        self.assertEqual([j["status"] for j in store.snapshot()["jobs"] if j["id"] == job["id"]], ["waiting"])

    def test_one_thread_keeps_old_behaviour(self):
        self.store.configure_account(self.account["id"], True, 50, 1)
        first, _ = self.store.claim(2)
        self.store.submitting(first["id"])
        self.assertIsNone(self.store.claim(2))
        with self.assertRaises(ValueError):
            self.store.configure_account(self.account["id"], True, 50, 5)

    def test_problem_reported_by_one_tab_is_kept_when_the_other_finishes(self):
        self.store.configure_account(self.account["id"], True, 100, 2)
        first, _ = self.store.claim(1)
        self.store.submitting(first["id"])
        second, _ = self.store.claim(1)
        self.store.submitting(second["id"])
        self.store.finish(first["id"], error="Muse báo hết lượt", account_state="attention")
        self.store.finish(second["id"], output="y.mp4")
        self.assertEqual(self.state()["state"], "attention")
        self.assertIsNone(self.store.claim(1))  # tài khoản cần xem lại: không nhận tác vụ mới


class RegionBlockTests(unittest.TestCase):
    def test_blocked_account_hands_its_prompts_to_another_account(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.db")
            store.add_account("a@tuan89.site", "", 100)
            store.enqueue(["Một", "Hai", "Ba"], 1, uuid.uuid4().hex)
            job, account = store.claim(1)
            store.submitting(job["id"])  # Muse nhận prompt rồi báo khu vực không được tạo video
            store.add_account("b@tuan89.site", "", 100)
            self.assertTrue(store.reassign_blocked(job["id"], account["id"]))
            snap = store.snapshot()
            a, b = snap["accounts"]
            self.assertEqual((a["video_blocked"], a["budget"], a["state"]), (1, 100, "ready"))
            moved = [j for j in snap["jobs"] if j["id"] == job["id"]][0]
            self.assertEqual((moved["status"], moved["account_id"]), ("waiting", None))
            waiting = [j for j in snap["jobs"] if j["status"] == "waiting"]
            self.assertEqual(len(waiting), 3)
            self.assertTrue(all(j["assigned_account_id"] == b["id"] for j in waiting))  # cả 3 prompt sang tài khoản B
            again, other = store.claim(1)
            self.assertEqual(other["id"], b["id"])

    def test_without_another_usable_account_the_task_stays_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.db")
            store.add_account("a@tuan89.site", "", 100)
            store.enqueue(["Một"], 1, uuid.uuid4().hex)
            job, account = store.claim(1)
            self.assertFalse(store.reassign_blocked(job["id"], account["id"]))
            self.assertEqual(store.snapshot()["accounts"][0]["video_blocked"], 1)


class NetworkLossTests(unittest.TestCase):
    def test_network_loss_before_sending_does_not_use_up_retries(self):
        from runner.engine import network_error
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.db")
            store.add_account("a@tuan89.site", "", 100)
            store.enqueue(["Một"], 1, uuid.uuid4().hex)
            for _ in range(5):  # rớt mạng nhiều lần liền: vẫn chờ, không báo lỗi tác vụ
                job, _ = store.claim(1)
                self.assertTrue(store.requeue_unsent(job["id"], "Mất kết nối mạng", count=False))
            job, _ = store.claim(1)
            self.assertEqual(job["auto_retries"], 0)
        self.assertTrue(network_error(Exception("unknown error: net::ERR_INTERNET_DISCONNECTED")))
        self.assertFalse(network_error(Exception("no such window")))


class FakeDriver:
    """Ghi lại lệnh gửi tới ChromeDriver và tab đang được điều khiển."""
    def __init__(self):
        self.handles, self.current, self.log, self.made = ["T1"], "T1", [], 1

    def execute(self, command, params=None):
        if command == "w3cGetCurrentWindowHandle":
            return {"value": self.current}
        if command == "w3cGetWindowHandles":
            return {"value": list(self.handles)}
        if command == "newWindow":
            self.made += 1
            handle = f"T{self.made}"
            self.handles.append(handle)
            return {"value": {"handle": handle}}
        if command == "switchToWindow":
            self.current = params["handle"]
            return {"value": None}
        self.log.append((command, self.current, (params or {}).get("who")))
        return {"value": None}


class TabRouterTests(unittest.TestCase):
    def test_each_thread_drives_its_own_tab(self):
        driver = FakeDriver()
        router = TabRouter(driver)
        barrier = threading.Barrier(2)
        tabs = {}

        def worker(name):
            tabs[name] = router.acquire()[0]
            barrier.wait()
            for _ in range(50):
                driver.execute("executeScript", {"who": name})
            router.release()

        threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertNotEqual(tabs["A"], tabs["B"])
        self.assertTrue(all(tab == tabs[who] for _, tab, who in driver.log))  # lệnh nào cũng chạy đúng tab của luồng đó
        self.assertEqual(router.active(), 0)
        self.assertEqual(sorted(router.free), sorted(tabs.values()))  # cả hai tab giữ lại cho tác vụ sau

    def test_freed_tab_is_reused_and_closed_tabs_are_forgotten(self):
        driver = FakeDriver()
        router = TabRouter(driver)
        handle, fresh = router.acquire()
        self.assertEqual((handle, fresh), ("T1", False))
        router.release()
        self.assertEqual(router.acquire(), ("T1", False))
        router.release()
        driver.handles.remove("T1")  # người dùng đóng tab
        handle, fresh = router.acquire()
        self.assertTrue(fresh)
        self.assertNotEqual(handle, "T1")


if __name__ == "__main__":
    unittest.main()


class KeepAwakeTests(unittest.TestCase):
    def test_windows_flags_follow_running_state(self):
        import sys
        import types
        from unittest import mock
        from runner import power
        calls = []
        fake = types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=types.SimpleNamespace(
            SetThreadExecutionState=lambda flags: calls.append(flags))))
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(sys.modules, {"ctypes": fake}):
            awake = power.KeepAwake()
            awake.set(True)
            awake.set(True)  # không gọi lại khi không đổi
            awake.set(False)
        self.assertEqual(calls, [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS])


class MuseBusyTests(unittest.TestCase):
    def test_waits_for_previous_video_instead_of_failing(self):
        from unittest import mock
        from runner import muse
        browser = muse.MuseBrowser.__new__(muse.MuseBrowser)
        busy = [True, True, False]
        browser.named = lambda selector, names: busy.pop(0) if busy else False
        phases = []
        browser.phase = phases.append
        browser.BUSY_POLL = 0
        with mock.patch.object(muse.time, "sleep"):
            browser.wait_previous()
        self.assertIn("chờ xong", phases[0])
        browser.named = lambda selector, names: True
        browser.BUSY_WAIT = 0
        with self.assertRaises(muse.MuseFailure) as caught:
            browser.wait_previous()
        self.assertTrue(caught.exception.unsent)


class LostVideoTests(unittest.TestCase):
    def test_done_jobs_whose_video_was_deleted_from_temp_can_be_fetched_again(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.db")
            store.add_account("a@tuan89.site", "", 100)
            store.enqueue(["Một", "Hai"], 1, uuid.uuid4().hex)
            lost, _ = store.claim(1)
            store.submitting(lost["id"])
            store.set_source(lost["id"], "https://muse.ai/chat/1")
            store.finish(lost["id"], output=str(Path(folder) / "downloads" / lost["id"] / "video.mp4"))  # file đã bị xóa
            kept, _ = store.claim(1)
            store.submitting(kept["id"])
            real = Path(folder) / "1.mp4"
            real.write_bytes(b"x")
            store.finish(kept["id"], output=str(real))
            store = Store(Path(folder) / "t.db")  # mở lại app
            store.recover()
            jobs = {j["id"]: j for j in store.snapshot()["jobs"]}
            self.assertEqual(jobs[lost["id"]]["status"], "error")
            self.assertIn("Lấy video", jobs[lost["id"]]["error"])
            self.assertEqual(jobs[kept["id"]]["status"], "done")


class ClosedTabTests(unittest.TestCase):
    def test_new_tab_opens_in_a_living_window_when_current_tab_was_closed(self):
        driver = FakeDriver()
        driver.handles, driver.current = ["T1", "T2"], "T2"
        router = TabRouter(driver)
        router.acquire()  # luồng 1 giữ T2
        router.release()
        driver.handles.remove("T2")  # tab bị đóng / treo (vd hết RAM)
        router.free = []
        seen = []
        original = router.original  # lệnh gửi thẳng tới ChromeDriver (router đã thay driver.execute)

        def spy(command, params=None):
            seen.append((command, driver.current))
            return original(command, params)
        router.original = spy
        handle, fresh = router.acquire()
        self.assertTrue(fresh)
        self.assertIn(("newWindow", "T1"), seen)  # chuyển sang tab còn sống trước rồi mới mở tab mới
