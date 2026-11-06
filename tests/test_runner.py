import concurrent.futures
from contextlib import redirect_stderr
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from app import AppServer
from runner.engine import Engine
from runner.flow import chrome_profile_arguments, default_chrome_profile
from runner.instance import InstanceLock
from runner.muse import MuseBrowser, MuseFailure, compose_request, mask_codes, newest_otp, otp_codes, otp_messages, recent_messages, valid_mp4
from runner.store import Store, mailbox_for


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.db")

    def tearDown(self):
        self.temp.cleanup()

    def account(self, name="a", budget=10):
        self.store.add_account(f"{name}@tuan89.site", "", budget)
        return next(a for a in self.store.snapshot()["accounts"] if a["email"] == f"{name}@tuan89.site")

    def jobs(self, count=1, cost=1):
        self.store.enqueue([f"Cảnh {i}" for i in range(count)], cost, uuid.uuid4().hex)

    def test_concurrent_claims_never_duplicate_or_exceed_budget(self):
        for i in range(10):
            self.account(str(i), 2)
        self.jobs(50)
        completed = set()
        for _ in range(2):
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                claims = list(pool.map(lambda _: self.store.claim(10), range(20)))
            pairs = [p for p in claims if p]
            self.assertEqual(len(pairs), 10)
            self.assertEqual(len({p[1]["id"] for p in pairs}), 10)
            for job, _ in pairs:
                self.assertNotIn(job["id"], completed)
                completed.add(job["id"])
                self.store.submitting(job["id"])
                self.store.finish(job["id"], output="test-only.mp4")
        self.assertIsNone(self.store.claim(10))
        self.assertEqual(len(completed), 20)
        self.assertTrue(all(a["budget"] == 0 for a in self.store.snapshot()["accounts"]))

    def test_global_limit_includes_login_and_review(self):
        account = self.account()
        self.account("b")
        self.jobs(3)
        self.store.reserve_login(account["id"])
        self.assertIsNone(self.store.claim(1))
        self.store.set_account(account["id"], "reviewing")
        self.assertIsNone(self.store.claim(1))
        self.assertIsNotNone(self.store.claim(2))
        self.assertIsNone(self.store.claim(2))

    def test_fairness_and_disabled_accounts(self):
        a = self.account("a")
        b = self.account("b")
        self.jobs(3)
        job, acc = self.store.claim(2)
        self.assertEqual(acc["id"], a["id"])
        self.store.finish(job["id"], error="pre-submit", account_state="ready")
        _, acc = self.store.claim(2)
        self.assertEqual(acc["id"], b["id"])
        self.store.configure_account(a["id"], False, 10)
        self.assertIsNone(self.store.claim(2))

    def test_duplicate_enqueue_is_idempotent_and_conflicts_rejected(self):
        batch = uuid.uuid4().hex
        for _ in range(2):
            self.store.enqueue(["One", "Two"], 1, batch)
        self.assertEqual(self.store.snapshot()["totals"]["waiting"], 2)
        with self.assertRaises(ValueError):
            self.store.enqueue(["different"], 1, batch)

    def test_prompts_are_balanced_and_named_by_line(self):
        accounts = [self.account(str(index)) for index in range(5)]
        prompts = [f"Scene {index}" for index in range(10)]
        self.store.enqueue(prompts, 1, uuid.uuid4().hex)
        jobs = sorted(self.store.snapshot()["jobs"], key=lambda job: job["ordinal"])
        self.assertEqual([job["name"] for job in jobs], [f"Prompt {index}" for index in range(1, 11)])
        assignments = {account["id"]: 0 for account in accounts}
        for job in jobs:
            assignments[job["assigned_account_id"]] += 1
        self.assertEqual(list(assignments.values()), [2, 2, 2, 2, 2])

    def test_single_prompt_is_assigned_to_only_one_account(self):
        first = self.account("first")
        self.account("second")
        self.store.enqueue(["Only scene"], 1, uuid.uuid4().hex)
        jobs = self.store.snapshot()["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["assigned_account_id"], first["id"])
        self.assertEqual(sum(a["state"] == "new" for a in self.store.snapshot()["accounts"]), 2)

    def test_two_prompts_are_split_between_two_accounts(self):
        accounts = [self.account(str(index)) for index in range(5)]
        self.store.enqueue(["First", "Second"], 1, uuid.uuid4().hex)
        assignments = [job["assigned_account_id"] for job in self.store.snapshot()["jobs"]]
        self.assertEqual(len(set(assignments)), 2)
        self.assertTrue(set(assignments).issubset({account["id"] for account in accounts}))

    def test_waiting_jobs_are_rebalanced_when_an_account_is_disabled(self):
        accounts = [self.account(str(index)) for index in range(3)]
        self.store.enqueue([f"Scene {index}" for index in range(6)], 1, uuid.uuid4().hex)
        self.store.configure_account(accounts[0]["id"], False, 10)
        counts = {accounts[1]["id"]: 0, accounts[2]["id"]: 0}
        for job in self.store.snapshot()["jobs"]:
            self.assertNotEqual(job["assigned_account_id"], accounts[0]["id"])
            counts[job["assigned_account_id"]] += 1
        self.assertEqual(list(counts.values()), [3, 3])

    def test_images_are_attached_to_claimed_jobs_and_removed_with_jobs(self):
        image = b"\x89PNG\r\n\x1a\nfixture"
        token = self.store.add_image(image)["token"]
        batch = uuid.uuid4().hex
        self.store.enqueue(["A scene"], 1, batch, [[token]])
        job = self.store.snapshot()["jobs"][0]
        self.assertEqual(job["image_count"], 1)
        self.assertEqual(self.store.job_image(job["id"], 0)[0].read_bytes(), image)
        self.account()
        claimed, _ = self.store.claim(1)
        self.assertEqual(Path(claimed["images"][0]).read_bytes(), image)
        with self.assertRaisesRegex(ValueError, "đang chạy"):
            self.store.delete_jobs([job["id"]])
        output = self.root / "fixture.mp4"
        output.write_bytes(b"saved video")
        self.store.finish(job["id"], output=str(output))
        self.assertEqual(self.store.delete_jobs([job["id"]]), 1)
        self.assertIsNone(self.store.job_image(job["id"], 0))
        self.assertFalse(self.store.image_file(token))
        self.assertTrue(output.is_file())

    def test_library_images_survive_job_deletion_until_removed_from_library(self):
        image = b"\x89PNG\r\n\x1a\nlibrary fixture"
        uploaded = self.store.add_image(image)
        token = uploaded["token"]
        self.store.add_library_image(token, "character.png")
        self.account()
        self.store.enqueue(["Character"], 1, uuid.uuid4().hex, [[token]], "image")
        job = self.store.snapshot()["jobs"][0]
        self.store.delete_jobs([job["id"]])
        self.assertEqual(self.store.library_images()[0]["name"], "character.png")
        self.assertTrue(self.store.image_file(token))
        self.store.delete_library_images([token])
        self.assertFalse(self.store.image_file(token))

    def test_flow_downloads_are_imported_once_into_image_library(self):
        downloads = self.root / "flow-downloads"
        downloads.mkdir()
        image = downloads / "flow-result.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nfirst image")
        self.assertEqual(self.store.import_flow_downloads(downloads), 1)
        self.assertEqual(self.store.import_flow_downloads(downloads), 0)
        self.assertEqual(self.store.library_images()[0]["name"], image.name)
        image.write_bytes(b"\x89PNG\r\n\x1a\nsecond image")
        self.assertEqual(self.store.import_flow_downloads(downloads), 1)
        duplicate = downloads / "another-name.png"
        duplicate.write_bytes(image.read_bytes())
        self.assertEqual(self.store.import_flow_downloads(downloads), 0)
        self.assertEqual(len(self.store.library_images()), 2)

    def test_engine_keeps_browser_open_between_jobs_and_closes_at_shutdown(self):
        account = self.account()
        self.store.set_account(account["id"], "ready")
        self.jobs(2)
        engine = Engine(self.store, self.root / "engine-data")

        class FakeBrowser:
            instances = []

            def __init__(self, profile, downloads, headless, phase):
                self.downloads = Path(downloads)
                self.phase = phase
                self.driver = type("Driver", (), {"current_url": "https://muse.ai", "window_handles": ["main"]})()
                self.login_count = self.generate_count = self.close_count = 0
                self.instances.append(self)

            def login(self, account):
                self.login_count += 1

            def session_ready(self):
                return True

            def set_download_directory(self, downloads):
                self.downloads = Path(downloads)

            def generate(self, prompt, job_id, before_submit, automatic=True, images=None):
                self.generate_count += 1
                before_submit()
                file = self.downloads / (job_id + ".mp4")
                file.write_bytes(b"fixture video")
                return file

            def close(self):
                self.close_count += 1

        try:
            with patch("runner.engine.MuseBrowser", FakeBrowser):
                for _ in range(2):
                    job, claimed_account = self.store.claim(1)
                    engine.work(job, claimed_account, False, str(self.root / "output"), True)
                self.assertEqual(len(FakeBrowser.instances), 1)
                browser = FakeBrowser.instances[0]
                self.assertEqual(browser.login_count, 1)
                self.assertEqual(browser.generate_count, 2)
                self.assertEqual(browser.close_count, 0)
                done_jobs = [job for job in self.store.snapshot()["jobs"] if job["status"] == "done"]
                self.assertEqual(len(done_jobs), 2)
                self.assertEqual({Path(job["output"]).name for job in done_jobs},
                                 {"Prompt 1.mp4", "Prompt 2.mp4"})
                engine.close_browsers()
                self.assertEqual(browser.close_count, 1)
        finally:
            engine.shutdown()

    def test_video_generation_failure_keeps_live_muse_session_for_next_job(self):
        account = self.account()
        self.store.set_account(account["id"], "ready")
        self.jobs(2)
        engine = Engine(self.store, self.root / "engine-data")

        class FakeBrowser:
            instances = []

            def __init__(self, profile, downloads, headless, phase):
                self.downloads = Path(downloads)
                self.driver = type("Driver", (), {"current_url": "https://muse.ai", "window_handles": ["main"]})()
                self.login_count = self.generate_count = self.close_count = 0
                self.instances.append(self)

            def login(self, account):
                self.login_count += 1

            def set_download_directory(self, downloads):
                self.downloads = Path(downloads)

            def session_ready(self):
                return True

            def generate(self, prompt, job_id, before_submit, automatic=True, images=None):
                self.generate_count += 1
                if self.generate_count == 1:
                    raise MuseFailure("Muse chưa tạo được video.")
                before_submit()
                file = self.downloads / (job_id + ".mp4")
                file.write_bytes(b"fixture video")
                return file

            def close(self):
                self.close_count += 1

        try:
            with patch("runner.engine.MuseBrowser", FakeBrowser):
                job, claimed_account = self.store.claim(1)
                engine.work(job, claimed_account, False, str(self.root / "output"), True)
                self.assertEqual(self.store.account(account["id"])["state"], "ready")
                self.assertEqual(self.store.snapshot()["totals"]["error"], 1)

                job, claimed_account = self.store.claim(1)
                engine.work(job, claimed_account, False, str(self.root / "output"), True)
                browser = FakeBrowser.instances[0]
                self.assertEqual(len(FakeBrowser.instances), 1)
                self.assertEqual(browser.login_count, 1)
                self.assertEqual(browser.generate_count, 2)
                self.assertEqual(browser.close_count, 0)
                self.assertEqual(self.store.snapshot()["totals"]["done"], 1)
                engine.close_browsers()
                self.assertEqual(browser.close_count, 1)
        finally:
            engine.shutdown()

    def test_chrome_error_before_submit_requeues_without_pausing_account(self):
        account = self.account()
        self.store.set_account(account["id"], "ready")
        self.jobs(1)
        engine = Engine(self.store, self.root / "engine-data")

        class CrashingBrowser:
            def __init__(self, profile, downloads, headless, phase):
                self.downloads = Path(downloads)
                self.driver = type("Driver", (), {"current_url": "https://muse.ai", "window_handles": ["main"]})()

            def login(self, account):
                pass

            def set_download_directory(self, downloads):
                pass

            def session_ready(self):
                return True

            def generate(self, prompt, job_id, before_submit, automatic=True, images=None):
                raise RuntimeError("stale element reference: element is not attached\nStacktrace: ...")

            def close(self):
                self.driver.window_handles = []

        try:
            with patch("runner.engine.MuseBrowser", CrashingBrowser):
                for attempt in range(3):
                    job, claimed = self.store.claim(1)
                    engine.work(job, claimed, False, str(self.root / "output"), True)
                    state = self.store.account(account["id"])["state"]
                    self.assertEqual(state, "ready")  # lỗi Chrome không làm dừng tài khoản
                    status = [j for j in self.store.snapshot()["jobs"] if j["id"] == job["id"]][0]
                    if attempt < 2:
                        self.assertEqual(status["status"], "waiting")  # chưa gửi: tự chạy lại
                    else:
                        self.assertEqual(status["status"], "error")
                        self.assertIn("RuntimeError: stale element reference", status["error"])
            log = (self.root / "engine-data" / "logs" / "runner.log").read_text(encoding="utf-8")
            self.assertIn("RuntimeError", log)
            self.assertNotIn("Prompt", log.split("RuntimeError")[0])  # không ghi nội dung prompt
        finally:
            engine.shutdown()

    def test_at_most_two_parallel_accounts(self):
        engine = Engine(self.store, self.root / "engine-data")
        try:
            self.store.save_setting("output_folder", str(self.root))
            for limit in (3, 5, 20):
                with self.assertRaisesRegex(ValueError, "tối đa 2"):
                    engine.start(limit, False)
                with self.assertRaisesRegex(ValueError, "tối đa 2"):
                    engine.login_all(limit)
            engine.start(2, False)
            self.assertEqual(engine.limit, 2)
        finally:
            engine.shutdown()

    def test_chrome_error_after_submit_is_never_resent(self):
        account = self.account()
        self.store.set_account(account["id"], "ready")
        self.jobs(1)
        engine = Engine(self.store, self.root / "engine-data")

        class SubmittedThenCrash:
            def __init__(self, profile, downloads, headless, phase):
                self.driver = type("Driver", (), {"current_url": "https://muse.ai", "window_handles": []})()

            def login(self, account):
                pass

            def set_download_directory(self, downloads):
                pass

            def session_ready(self):
                return True

            def generate(self, prompt, job_id, before_submit, automatic=True, images=None):
                before_submit()
                raise RuntimeError("chrome not reachable")

            def close(self):
                pass

        try:
            with patch("runner.engine.MuseBrowser", SubmittedThenCrash):
                job, claimed = self.store.claim(1)
                engine.work(job, claimed, False, str(self.root / "output"), True)
            status = [j for j in self.store.snapshot()["jobs"] if j["id"] == job["id"]][0]
            self.assertEqual(status["status"], "error")
            self.assertEqual(self.store.account(account["id"])["state"], "ready")  # Chrome chết: mở lại lần sau
        finally:
            engine.shutdown()

    def session_browser(self):
        class FakeBrowser:
            instances = []
            ready = True

            def __init__(self, profile, downloads, headless, phase):
                self.downloads, self.headless = Path(downloads), headless
                self.driver = type("Driver", (), {"current_url": "https://muse.ai", "window_handles": ["main"]})()
                self.login_count = self.generate_count = self.close_count = 0
                self.instances.append(self)

            def login(self, account):
                self.login_count += 1
                FakeBrowser.ready = True

            def session_ready(self):
                return FakeBrowser.ready

            def set_download_directory(self, downloads):
                self.downloads = Path(downloads)

            def generate(self, prompt, job_id, before_submit, automatic=True, images=None):
                self.generate_count += 1
                before_submit()
                file = self.downloads / (job_id + ".mp4")
                file.write_bytes(b"fixture video")
                return file

            def close(self):
                self.close_count += 1
                self.driver.window_handles = []
        return FakeBrowser

    def test_login_keeps_chrome_open_and_first_job_reuses_it(self):
        account = self.account()
        self.jobs(1)
        engine = Engine(self.store, self.root / "engine-data")
        FakeBrowser = self.session_browser()
        try:
            with patch("runner.engine.MuseBrowser", FakeBrowser):
                self.store.reserve_login(account["id"])
                engine.login_worker(self.store.account(account["id"]))
                self.assertEqual(self.store.account(account["id"])["state"], "ready")
                browser = FakeBrowser.instances[0]
                self.assertEqual(browser.close_count, 0)
                self.assertIs(engine.browsers[account["id"]], browser)
                job, claimed = self.store.claim(1)
                engine.work(job, claimed, False, str(self.root / "output"), True)
                self.assertEqual(len(FakeBrowser.instances), 1)
                self.assertEqual(browser.login_count, 1)
                self.assertEqual(self.store.snapshot()["totals"]["done"], 1)
        finally:
            engine.shutdown()
            engine.close_browsers()

    def test_login_never_closes_chrome_of_a_running_job(self):
        account = self.account()
        self.store.set_account(account["id"], "ready")
        self.jobs(1)
        engine = Engine(self.store, self.root / "engine-data")
        FakeBrowser = self.session_browser()
        try:
            running = FakeBrowser(None, self.root / "d", False, None)
            engine.browsers[account["id"]] = running
            self.store.claim(1)  # tài khoản chuyển sang busy
            for review in (False, True):
                with self.assertRaises(ValueError):
                    engine.login(account["id"], review=review)
            self.assertEqual(running.close_count, 0)
            self.assertIs(engine.browsers[account["id"]], running)
            self.assertEqual(self.store.account(account["id"])["state"], "busy")
        finally:
            engine.shutdown()

    def test_work_checks_session_and_replaces_dead_chrome(self):
        account = self.account()
        self.store.set_account(account["id"], "ready")
        self.jobs(3)
        engine = Engine(self.store, self.root / "engine-data")
        FakeBrowser = self.session_browser()
        try:
            with patch("runner.engine.MuseBrowser", FakeBrowser):
                job, claimed = self.store.claim(1)
                engine.work(job, claimed, False, str(self.root / "output"), True)
                first = FakeBrowser.instances[0]
                FakeBrowser.ready = False  # phiên Muse hết hạn nhưng Chrome còn mở: đăng nhập lại trên chính Chrome đó
                job, claimed = self.store.claim(1)
                engine.work(job, claimed, False, str(self.root / "output"), True)
                self.assertEqual(len(FakeBrowser.instances), 1)
                self.assertEqual(first.login_count, 2)
                first.driver.window_handles = []  # người dùng tự đóng Chrome: mở Chrome mới
                job, claimed = self.store.claim(1)
                engine.work(job, claimed, False, str(self.root / "output"), True)
                self.assertEqual(len(FakeBrowser.instances), 2)
                self.assertEqual(first.close_count, 1)
                self.assertIs(engine.browsers[account["id"]], FakeBrowser.instances[1])
                self.assertEqual(self.store.snapshot()["totals"]["done"], 3)
        finally:
            engine.shutdown()
            engine.close_browsers()

    def test_login_all_logs_in_every_account_with_limited_chrome_and_closes_them(self):
        ids = [self.account(str(i))["id"] for i in range(7)]
        self.store.set_account(ids[0], "ready")
        engine = Engine(self.store, self.root / "engine-data")
        FakeBrowser = self.session_browser()
        peak, open_now, guard = [0], [0], __import__("threading").Lock()
        original_init, original_close = FakeBrowser.__init__, FakeBrowser.close

        def init(self, *args):
            original_init(self, *args)
            with guard:
                open_now[0] += 1
                peak[0] = max(peak[0], open_now[0])
            time.sleep(0.05)

        def close(self):
            original_close(self)
            with guard:
                open_now[0] -= 1
        FakeBrowser.__init__, FakeBrowser.close = init, close
        try:
            with patch("runner.engine.MuseBrowser", FakeBrowser):
                self.assertEqual(engine.login_all(2), 6)
                with self.assertRaises(ValueError):
                    engine.login_all(2)
                deadline = time.time() + 10
                while engine.login_all_running and time.time() < deadline:
                    time.sleep(0.05)
                states = {a["id"]: a["state"] for a in self.store.snapshot()["accounts"]}
                self.assertTrue(all(state == "ready" for state in states.values()), states)
                self.assertEqual(len(FakeBrowser.instances), 6)
                self.assertLessEqual(peak[0], 2)
                self.assertEqual(open_now[0], 0)
                self.assertEqual(engine.browsers, {})
                self.assertIn("6/6", engine.login_all_status)
                with self.assertRaises(ValueError):
                    engine.login_all(2)  # không còn tài khoản nào cần đăng nhập
        finally:
            engine.shutdown()

    def test_delete_is_atomic_when_a_selected_job_is_running(self):
        self.jobs(2)
        self.account()
        running, _ = self.store.claim(1)
        ids = [job["id"] for job in self.store.snapshot()["jobs"]]
        with self.assertRaisesRegex(ValueError, "đang chạy"):
            self.store.delete_jobs(ids)
        self.assertEqual(self.store.snapshot()["totals"]["running"], 1)
        self.assertEqual(self.store.snapshot()["totals"]["waiting"], 1)
        self.store.finish(running["id"], error="fixture")
        self.assertEqual(self.store.delete_jobs(ids), 2)
        self.assertEqual(self.store.snapshot()["totals"]["error"], 0)

    def test_crash_recovery_refunds_only_before_submission(self):
        a = self.account("a")
        b = self.account("b")
        self.jobs(2, 2)
        first, _ = self.store.claim(2)
        second, _ = self.store.claim(2)
        self.store.submitting(second["id"])
        self.store.recover()
        self.assertEqual(self.store.account(a["id"])["budget"], 10)
        self.assertEqual(self.store.account(b["id"])["budget"], 8)
        self.store.retry(first["id"])
        with self.assertRaises(ValueError):
            self.store.retry(second["id"])

    def test_budget_is_not_double_refunded(self):
        a = self.account()
        self.jobs()
        job, _ = self.store.claim(1)
        self.store.finish(job["id"], error="before submit")
        self.store.finish(job["id"], error="duplicate event")
        self.assertEqual(self.store.account(a["id"])["budget"], 10)

    def test_video_progress_advances_by_real_generation_phases_and_finishes_at_100(self):
        account = self.account()
        self.store.set_account(account["id"], "ready")
        self.jobs()
        job, _ = self.store.claim(1)
        self.assertEqual(job["progress"], 3)
        self.store.phase(job["id"], "Chuẩn bị gửi prompt")
        self.assertEqual(self.store.snapshot()["jobs"][0]["progress"], 15)
        self.store.phase(job["id"], "Muse đang tạo video")
        self.assertEqual(self.store.snapshot()["jobs"][0]["progress"], 20)
        self.store.phase(job["id"], "Đang tải MP4 về thư mục đã chọn")
        self.assertEqual(self.store.snapshot()["jobs"][0]["progress"], 94)
        output = self.root / "video.mp4"
        output.write_bytes(b"fixture video")
        self.store.finish(job["id"], output=str(output))
        completed = self.store.snapshot()["jobs"][0]
        self.assertEqual(completed["progress"], 100)
        self.assertEqual(completed["status"], "done")

    def test_unlimited_accounts_bulk_add_and_bad_budget(self):
        for i in range(12):
            self.account(str(i))
        result = self.store.add_accounts("a1@tuan89.site\nA1@tuan89.site, b2@tuan89.site;khong-phai-email\nc3@gmail.com", 5)
        self.assertEqual((result["added"], result["duplicate"], result["invalid_count"]), (3, 1, 1))
        accounts = {a["email"]: a for a in self.store.snapshot()["accounts"]}
        self.assertEqual(len(accounts), 15)
        self.assertEqual(self.store.account(accounts["a1@tuan89.site"]["id"])["mailbox"], "https://tuan89.site/?email=a1%40tuan89.site")
        self.assertEqual(self.store.account(accounts["c3@gmail.com"]["id"])["mailbox"], "")
        with self.assertRaises(ValueError):
            self.store.add_accounts("x@tuan89.site", float("nan"))
        with self.assertRaises(ValueError):
            self.jobs(cost=float("nan"))

    def test_otp_new_mail_is_detected_even_when_meta_resends_the_same_code(self):
        # Văn bản hộp thư Tuan89 như ảnh chụp thật (thư mới nằm trên, có khung "MÃ OTP MỚI NHẤT").
        before = ("RECEIVING LIVE EMAILS ruby@tuan89.site\nMÃ OTP MỚI NHẤT 2026-11-03 16:26:30\nTừ: Meta — Use code 873023 to log in\n"
                  "873023\nMeta\n2026-11-03 16:26:30\nUse code 873023 to log in\nMeta Hi Mark, Login code 873023 If you did not")
        after = ("RECEIVING LIVE EMAILS ruby@tuan89.site\nMÃ OTP MỚI NHẤT 2026-11-03 16:29:03\nTừ: Meta — Use code 873023 to log in\n"
                 "873023\nMeta Mới nhất\n2026-11-03 16:29:03\nUse code 873023 to log in\nMeta Hi Mark, Login code 873023\n"
                 "Meta\n2026-11-03 16:26:30\nUse code 873023 to log in\nMeta Hi Mark, Login code 873023")
        baseline = otp_messages(before)
        self.assertEqual(baseline, {("2026-11-03 16:26:30", "873023")})
        self.assertEqual(newest_otp(otp_messages(before) - baseline), None)
        self.assertEqual(newest_otp(otp_messages(after) - baseline), "873023")
        newer = "MÃ OTP MỚI NHẤT 2026-11-03 16:31:00\nUse code 111222 to log in\n" + after
        self.assertEqual(newest_otp(otp_messages(newer) - baseline), "111222")
        self.assertEqual(otp_codes("Dùng mã 654321 để đăng nhập"), {"654321"})
        self.assertEqual(otp_messages("Tổng 123456 không phải mã"), set())
        no_time = "Use code 222222 to log in\nUse code 111111 to log in"
        self.assertEqual(newest_otp(otp_messages(no_time) - otp_messages("Use code 111111 to log in")), "222222")

    def test_otp_spaced_timestamps_recent_rule_and_masking(self):
        from datetime import datetime
        text = ("MÃ OTP MỚI NHẤT 2026-11-03 16:44:37\nTừ: Meta — Use code 347832 to log in\n347832\n"
                "Meta Mới nhất 2026-11-03  16:44:37\nUse code 347832 to log in\nMeta Hi George, Login code 347832")
        messages = otp_messages(text)
        self.assertEqual(messages, {("2026-11-03 16:44:37", "347832")})
        clicked = datetime(2026, 11, 3, 16, 44, 30)
        self.assertEqual(recent_messages(messages, clicked), messages)
        self.assertEqual(recent_messages(messages, datetime(2026, 11, 3, 16, 50, 0)), set())  # thư trước khi bấm 5 phút
        self.assertEqual(recent_messages(messages, datetime(2026, 11, 3, 16, 50, 0), before=10), messages)
        self.assertEqual(recent_messages(messages, datetime(2026, 11, 3, 17, 30, 0), before=10), set())
        self.assertNotIn("347832", mask_codes(text))

    def test_instances_are_exclusive(self):
        first = InstanceLock(self.root / "app.lock")
        try:
            with self.assertRaises(OSError):
                InstanceLock(self.root / "app.lock")
        finally:
            first.close()
        InstanceLock(self.root / "app.lock").close()

    def test_existing_database_adds_image_column_without_resetting_jobs(self):
        path = self.root / "legacy.db"
        db = sqlite3.connect(path)
        try:
            db.executescript("""
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
                    phase TEXT NOT NULL DEFAULT 'Đợi tài khoản', account_id TEXT,
                    cost REAL NOT NULL, submitted INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', output TEXT, created REAL NOT NULL,
                    updated REAL NOT NULL, source_url TEXT NOT NULL DEFAULT '',
                    batch TEXT NOT NULL, ordinal INTEGER NOT NULL, UNIQUE(batch, ordinal));
                INSERT INTO jobs(id,prompt,cost,created,updated,batch,ordinal)
                VALUES('legacy','Giữ lại',1,1,1,'legacy-batch',0);
            """)
        finally:
            db.close()
        migrated = Store(path)
        job = migrated.snapshot()["jobs"][0]
        self.assertEqual(job["prompt"], "Giữ lại")
        self.assertEqual(job["image_count"], 0)
        self.assertEqual(job["progress"], 0)


class ParsingTests(unittest.TestCase):
    def test_mailbox_bound_to_account(self):
        email, url = mailbox_for("A@tuan89.site", "")
        self.assertEqual(email, "a@tuan89.site")
        self.assertIn("email=a%40tuan89.site", url)
        for bad in ("https://tuan89.site/?email=other%40tuan89.site",
                    "https://tuan89.site.evil.invalid/?email=a%40tuan89.site",
                    "http://127.0.0.1/", "https://tuan89.site:444/?email=a%40tuan89.site"):
            with self.assertRaises(ValueError):
                mailbox_for(email, bad)

    def test_otp_only_matches_observed_subject(self):
        self.assertEqual(otp_codes("Ngày 202611 tài khoản 999999. Dùng mã 123456 để đăng nhập"), {"123456"})
        self.assertEqual(otp_codes("123456"), set())

    def test_request_has_unique_scene_marker(self):
        self.assertTrue(compose_request("Biển", "unique-scene").endswith("Mã tác vụ: unique-scene"))
        image_request = compose_request("Biển", "image-scene", use_images=True)
        self.assertIn("ảnh tham chiếu đã đính kèm", image_request)
        self.assertTrue(image_request.endswith("Mã tác vụ: image-scene"))

    def test_image_attach_does_not_depend_on_muse_file_input_persistence(self):
        class FileInput:
            def __init__(self):
                self.paths = None

            def get_attribute(self, name):
                return "image/*" if name == "accept" else None

            def send_keys(self, paths):
                self.paths = paths

        class Driver:
            def execute_script(self, *_):
                raise AssertionError("Muse may clear the file input after accepting the image")

        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "character.png"
            image.write_bytes(b"fixture")
            browser = object.__new__(MuseBrowser)
            browser.driver = Driver()
            file_input = FileInput()
            browser.elements = lambda _: [file_input]
            browser.phase, browser.guard, browser.composer = (lambda _: None), (lambda: None), (lambda: "box")
            # Muse hiện ảnh xem trước sau khi tải lên (đọc ô nhập, không đọc lại ô chọn file).
            browser.attachments = lambda box=None: {"count": 1 if file_input.paths else 0, "busy": False, "removers": 1}
            browser.attach_images([image])
            self.assertEqual(file_input.paths, str(image.resolve()))

    def test_image_attach_waits_for_upload_and_fails_before_submit(self):
        class FileInput:
            def __init__(self, multiple):
                self.sent, self.multiple = [], multiple
            def get_attribute(self, name):
                return "image/*" if name == "accept" else ("true" if name == "multiple" and self.multiple else None)
            def send_keys(self, paths):
                self.sent.append(paths)

        with tempfile.TemporaryDirectory() as folder:
            images = []
            for name in ("a.png", "b.png"):
                images.append(Path(folder) / name)
                images[-1].write_bytes(b"fixture")
            browser = object.__new__(MuseBrowser)
            browser.phase, browser.guard, browser.composer = (lambda _: None), (lambda: None), (lambda: "box")
            # Ô chọn ảnh không nhận nhiều file: tải từng ảnh; ảnh xem trước hiện dần, có lúc đang tải.
            single = FileInput(multiple=False)
            browser.elements = lambda _: [single]
            import itertools
            states = itertools.chain([{"count": 0, "busy": False}, {"count": 1, "busy": True}, {"count": 2, "busy": True}],
                                     itertools.repeat({"count": 2, "busy": False}))
            browser.attachments = lambda box=None: next(states)
            with patch("runner.muse.time.sleep", lambda _: None):
                browser.attach_images(images)
            self.assertEqual(single.sent, [str(images[0].resolve()), str(images[1].resolve())])
            # Không thấy ảnh xem trước: báo lỗi TRƯỚC khi gửi prompt (không gửi prompt thiếu ảnh).
            browser.elements = lambda _: [FileInput(multiple=True)]
            browser.attachments = lambda box=None: {"count": 0, "busy": False}
            with self.assertRaisesRegex(MuseFailure, "Chưa gửi prompt"):
                browser.attach_images(images, timeout=0.01)

    def test_leftover_attachments_are_removed_or_block_submit(self):
        browser = object.__new__(MuseBrowser)
        left = {"count": 2}

        class Driver:
            def execute_script(self, script, *args):
                if left["count"]:
                    left["count"] -= 1
                    return True
                return False
        browser.driver, browser.composer = Driver(), (lambda: "box")
        browser.attachments = lambda box=None: {"count": left["count"], "busy": False}
        with patch("runner.muse.time.sleep", lambda _: None):
            browser.clear_attachments()
        self.assertEqual(left["count"], 0)
        stuck = object.__new__(MuseBrowser)
        stuck.driver = type("D", (), {"execute_script": lambda self, *a: False})()
        stuck.composer = lambda: "box"
        stuck.attachments = lambda box=None: {"count": 1, "busy": False}
        with self.assertRaisesRegex(MuseFailure, "còn ảnh đính kèm"):
            stuck.clear_attachments()

    def test_truncated_download_is_not_valid(self):
        def box(kind, content):
            return struct.pack(">I4s", 8 + len(content), kind) + content
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "test.mp4"
            content = box(b"ftyp", b"isom0000") + box(b"moov", b"unit-test") + box(b"mdat", bytes(2000))
            file.write_bytes(content)
            self.assertTrue(valid_mp4(file))  # Container validation, not a real playable video.
            file.write_bytes(content[:-20])
            self.assertFalse(valid_mp4(file))
            file.write_bytes(b"<html>not a video</html>" * 100)
            self.assertFalse(valid_mp4(file))


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_flow_profile_is_the_tools_own_profile(self):
        local_app_data = self.root / "AppData"
        (local_app_data / "Google" / "Chrome" / "User Data" / "Profile 2").mkdir(parents=True)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data), "HUMY_FLOW_PROFILE_PATH": ""}):
            # Không dùng hồ sơ Chrome cá nhân (Profile 2) dù máy có: luôn là hồ sơ riêng trong thư mục dữ liệu của tool.
            self.assertEqual(default_chrome_profile(self.root / "data"), self.root / "data" / "profiles" / "google-flow" / "Default")
        with patch.dict(os.environ, {"HUMY_FLOW_PROFILE_PATH": str(self.root / "custom" / "Profile 9")}):
            self.assertEqual(default_chrome_profile(self.root / "data"), self.root / "custom" / "Profile 9")

    def test_flow_session_targets_existing_profile_directory(self):
        profile = self.root / "User Data" / "Profile 2"
        self.assertEqual(chrome_profile_arguments(profile), (
            f"--user-data-dir={profile.parent.resolve()}",
            f"--profile-directory={profile.name}",
        ))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")
        class IdleEngine:
            changed = threading.Event()
            def snapshot(self):
                return {"enabled": False}
        self.server = AppServer(0, self.store, IdleEngine())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_origin_and_key_required_for_mutation(self):
        body = json.dumps({"email": "test@tuan89.site", "budget": 10})
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("/api/accounts", "POST", body, headers)[0], 403)
        headers.update({"Origin": "https://unrelated.invalid", "X-Runner-Key": self.server.secret})
        self.assertEqual(self.request("/api/accounts", "POST", body, headers)[0], 403)
        headers["Origin"] = self.server.origin
        self.assertEqual(self.request("/api/accounts", "POST", body, headers)[0], 200)
        self.assertEqual(len(self.store.snapshot()["accounts"]), 1)

    def test_no_data_files_or_cross_origin_reads(self):
        self.assertEqual(self.request("/../data/studio.sqlite3")[0], 404)
        self.assertEqual(self.request("/data/profiles/a/Cookies")[0], 404)
        self.assertEqual(self.request("/api/state", headers={"Host": "evil.invalid"})[0], 403)
        self.assertEqual(self.request("/api/state", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("/")[0], 200)

    def test_server_ignores_client_disconnects_but_logs_unexpected_errors(self):
        output = io.StringIO()
        with redirect_stderr(output):
            try:
                raise ConnectionAbortedError("client closed connection")
            except ConnectionAbortedError:
                self.server.handle_error(None, ("127.0.0.1", 0))
        self.assertEqual(output.getvalue(), "")

        with redirect_stderr(output):
            try:
                raise RuntimeError("unexpected handler error")
            except RuntimeError:
                self.server.handle_error(None, ("127.0.0.1", 0))
        self.assertIn("RuntimeError: unexpected handler error", output.getvalue())

    def test_video_is_bound_to_done_scene_and_supports_playback_ranges(self):
        self.store.add_account("a@tuan89.site", "", 10)
        self.store.enqueue(["A scene"], 1, uuid.uuid4().hex)
        job, _ = self.store.claim(1)
        self.assertEqual(self.request("/api/media/" + job["id"])[0], 404)
        file = Path(self.temp.name) / "fixture.mp4"
        file.write_bytes(b"0123456789")  # HTTP byte fixture, not a generated video.
        self.store.finish(job["id"], output=str(file))
        self.assertEqual(self.request("/api/media/" + job["id"], headers={"Range": "bytes=2-5"}), (206, b"2345"))
        self.assertEqual(self.request("/api/media/" + job["id"], headers={"Range": "bytes=-3"}), (206, b"789"))
        self.assertEqual(self.request("/api/media/" + job["id"], headers={"Range": "bytes=100-"})[0], 416)
        file.unlink()
        self.assertEqual(self.request("/api/video/" + job["id"])[0], 404)

    def test_image_upload_queue_preview_and_delete(self):
        image = b"\x89PNG\r\n\x1a\nfixture"
        headers = {"Content-Type": "image/png", "Origin": self.server.origin,
                   "X-Runner-Key": self.server.secret}
        status, response = self.request("/api/image-upload", "POST", image, headers)
        self.assertEqual(status, 200)
        token = json.loads(response)["token"]
        batch = uuid.uuid4().hex
        body = json.dumps({"prompts": ["Character scene"], "cost": 1, "batch": batch,
                           "mode": "image", "images": [[token]]})
        json_headers = {"Content-Type": "application/json", "Origin": self.server.origin,
                        "X-Runner-Key": self.server.secret}
        self.assertEqual(self.request("/api/jobs", "POST", body, json_headers)[0], 200)
        replay_image = json.loads(self.request("/api/image-upload", "POST", image, headers)[1])["token"]
        replay = json.dumps({"prompts": ["Character scene"], "cost": 1, "batch": batch,
                             "mode": "image", "images": [[replay_image]]})
        self.assertEqual(self.request("/api/jobs", "POST", replay, json_headers)[0], 200)
        job = self.store.snapshot()["jobs"][0]
        self.assertEqual(self.request(f"/api/image/{job['id']}/0"), (200, image))
        delete = json.dumps({"ids": [job["id"]]})
        self.assertEqual(self.request("/api/jobs/delete", "POST", delete, json_headers)[0], 200)
        self.assertEqual(self.request(f"/api/image/{job['id']}/0")[0], 404)

    def test_library_upload_list_and_image_fetch(self):
        image = b"\x89PNG\r\n\x1a\nlibrary fixture"
        headers = {"Content-Type": "image/png", "Origin": self.server.origin,
                   "X-Runner-Key": self.server.secret, "X-Image-Name": "portrait.png"}
        status, response = self.request("/api/library/image-upload", "POST", image, headers)
        self.assertEqual(status, 200)
        token = json.loads(response)["token"]
        self.assertEqual(self.store.library_images()[0]["name"], "portrait.png")
        self.assertEqual(self.request("/api/library"), (200, json.dumps(
            {"images": self.store.library_images()}, ensure_ascii=False).encode()))
        self.assertEqual(self.request(f"/api/library/image/{token}"), (200, image))

    def test_image_video_mode_requires_reference_images(self):
        body = json.dumps({"prompts": ["Move the character"], "cost": 1,
                           "batch": uuid.uuid4().hex, "mode": "image", "images": [[]]})
        headers = {"Content-Type": "application/json", "Origin": self.server.origin,
                   "X-Runner-Key": self.server.secret}
        self.assertEqual(self.request("/api/jobs", "POST", body, headers)[0], 400)


if __name__ == "__main__":
    unittest.main()


class FlowLoginTests(unittest.TestCase):
    def test_flow_login_opens_normal_chrome_with_the_flow_profile(self):
        root = Path(tempfile.mkdtemp())
        store = Store(root / "studio.sqlite3")
        engine = Engine(store, root)
        launched = []
        engine.flow_launcher = lambda args: launched.append(args)
        try:
            with patch("runner.engine.chrome_executable", lambda: "chrome.exe"):
                engine.open_flow_login()
                with self.assertRaises(ValueError):
                    engine.open_flow_login()  # đang mở: không mở hai lần
                deadline = time.time() + 5
                while engine.flow_busy and time.time() < deadline:
                    time.sleep(0.05)
            self.assertEqual(len(launched), 1)
            self.assertEqual(launched[0][0], "chrome.exe")
            self.assertIn(f"--profile-directory={engine.flow_profile.name}", launched[0])
            self.assertEqual(launched[0][-1], "https://flow.google.com")
            self.assertIn("Đã mở Chrome thường", engine.flow_status)
        finally:
            engine.shutdown()
            engine.close_browsers()
            shutil.rmtree(root, ignore_errors=True)


class RebalanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "studio.sqlite3")
        for email in ("a@x.vn", "b@x.vn"):
            self.store.add_account(email, "", 100)
        self.a, self.b = [acc["id"] for acc in self.store.snapshot()["accounts"]]
        self.store.enqueue([f"Cảnh {i}" for i in range(6)], 1, uuid.uuid4().hex)

    def tearDown(self):
        self.temp.cleanup()

    def assigned(self):
        with self.store.connect() as db:
            return {r["id"]: r["assigned_account_id"] for r in db.execute("SELECT id, assigned_account_id FROM jobs WHERE status='waiting'")}

    def test_idle_account_takes_unsent_prompts_from_a_busy_account(self):
        first, account = self.store.claim(2)  # A nhận prompt đầu tiên
        second, other = self.store.claim(2)
        self.assertNotEqual(account["id"], other["id"])
        # B làm xong rất nhanh 2 prompt của mình; A vẫn đang chạy prompt đầu tiên.
        self.store.finish(second["id"], output="x.mp4")
        for _ in range(2):
            job, acc = self.store.claim(2)
            self.assertEqual(acc["id"], other["id"])
            self.store.finish(job["id"], output="x.mp4")
        # B hết prompt của mình: nhận prompt CHỜ của A (A vẫn bận), không đụng prompt A đang chạy.
        job, acc = self.store.claim(2)
        self.assertEqual(acc["id"], other["id"])
        self.assertNotEqual(job["id"], first["id"])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT status, account_id FROM jobs WHERE id=?", (first["id"],)).fetchone()["account_id"], account["id"])

    def test_prompt_with_user_chosen_account_is_never_moved(self):
        jobs = [j["id"] for j in self.store.snapshot()["jobs"]]
        for job_id in jobs:
            self.store.update_job(job_id, self.store.job_detail(job_id)["prompt"], account_id=self.a)
        job, acc = self.store.claim(2)
        self.assertEqual(acc["id"], self.a)
        self.assertIsNone(self.store.claim(2))  # B rảnh nhưng mọi prompt đã được người dùng gán cho A
        self.assertTrue(all(owner == self.a for owner in self.assigned().values()))


class NewChatTests(unittest.TestCase):
    def test_each_prompt_starts_a_new_muse_chat_on_a_reused_browser(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        store = Store(root / "studio.sqlite3")
        store.add_account("a@x.vn", "", 10)
        account = store.snapshot()["accounts"][0]
        store.set_account(account["id"], "ready")
        store.enqueue(["Cảnh 1", "Cảnh 2"], 1, uuid.uuid4().hex)
        engine = Engine(store, root / "engine")
        calls = []

        class Browser:
            def __init__(self, *args):
                self.driver = type("D", (), {"window_handles": ["w"], "current_url": "https://muse.ai"})()
                self.downloads = root
            def login(self, account):
                calls.append("login")
            def new_chat(self):
                calls.append("new_chat")
            def session_ready(self):
                return True
            def set_download_directory(self, target):
                self.downloads = Path(target)
            def generate(self, prompt, job_id, before, automatic=True, images=None):
                before()
                file = self.downloads / (job_id + ".mp4")
                file.write_bytes(b"x")
                return file
            def close(self):
                pass
        try:
            with patch("runner.engine.MuseBrowser", Browser):
                for _ in range(2):
                    job, acc = store.claim(1)
                    engine.work(job, acc, False, str(root / "out"), True)
            self.assertEqual(calls, ["login", "new_chat"])  # lần 1: đăng nhập mở trang mới; lần 2: trò chuyện mới
        finally:
            engine.shutdown()
            engine.close_browsers()
            temp.cleanup()


class FlowPromptTests(unittest.TestCase):
    def flow(self, page, clicked):
        from runner.flow import FlowImageSession, JS_FLOW_PAGE, JS_NEW_PROJECT
        flow = object.__new__(FlowImageSession)
        flow.debug_path = Path(tempfile.mkdtemp()) / "flow-debug.txt"

        class Driver:
            def find_elements(self, *a):
                return []
            def execute_script(self, script, *a):
                if script is JS_FLOW_PAGE:
                    return page
                if script is JS_NEW_PROJECT:
                    clicked.append(1)
                    return "Dự án mới"
                return "trang flow"
        flow.driver = Driver()
        return flow

    def test_signed_out_flow_asks_to_log_in_from_settings(self):
        clicked = []
        with patch("runner.flow.time.sleep", lambda _: None):
            with self.assertRaisesRegex(RuntimeError, "Cài đặt → Google Flow → Đăng nhập"):
                self.flow({"signIn": True}, clicked).prompt("x", timeout=5)
        self.assertEqual(clicked, [])

    def test_new_project_is_clicked_at_most_three_times_then_dumps(self):
        clicked, clock = [], [0.0]
        flow = self.flow({"loading": False}, clicked)
        with patch("runner.flow.time.sleep", lambda s: clock.__setitem__(0, clock[0] + 1)), \
                patch("runner.flow.time.monotonic", lambda: clock[0]):
            with self.assertRaisesRegex(RuntimeError, "sau khi bấm Dự án mới"):
                flow.prompt("x", timeout=60)
        self.assertEqual(len(clicked), 3)  # không tạo hàng loạt dự án trống
        self.assertIn("Không tìm thấy ô prompt", flow.debug_path.read_text(encoding="utf-8"))
