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

    def test_new_account_defaults_to_two_threads_and_100_credit(self):
        self.assertEqual((self.account["threads"], self.account["budget"]), (2, 100))

    def test_second_tab_only_after_first_prompt_is_sent_and_up_to_thread_count(self):
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

    def test_one_thread_keeps_old_behaviour(self):
        self.store.configure_account(self.account["id"], True, 50, 1)
        first, _ = self.store.claim(2)
        self.store.submitting(first["id"])
        self.assertIsNone(self.store.claim(2))
        with self.assertRaises(ValueError):
            self.store.configure_account(self.account["id"], True, 50, 5)

    def test_problem_reported_by_one_tab_is_kept_when_the_other_finishes(self):
        first, _ = self.store.claim(1)
        self.store.submitting(first["id"])
        second, _ = self.store.claim(1)
        self.store.submitting(second["id"])
        self.store.finish(first["id"], error="Muse báo hết lượt", account_state="attention")
        self.store.finish(second["id"], output="y.mp4")
        self.assertEqual(self.state()["state"], "attention")
        self.assertIsNone(self.store.claim(1))  # tài khoản cần xem lại: không nhận tác vụ mới


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
