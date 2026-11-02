import concurrent.futures
from contextlib import redirect_stderr
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from app import AppServer
from runner.engine import Engine
from runner.flow import chrome_profile_arguments, default_chrome_profile
from runner.instance import InstanceLock
from runner.muse import MuseBrowser, MuseFailure, compose_request, otp_codes, valid_mp4
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
                self.driver = type("Driver", (), {"current_url": "https://muse.ai"})()
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
                self.driver = type("Driver", (), {"current_url": "https://muse.ai"})()
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

    def test_account_limit_and_bad_budget(self):
        for i in range(10):
            self.account(str(i))
        with self.assertRaises(ValueError):
            self.account("eleven")
        with self.assertRaises(ValueError):
            self.jobs(cost=float("nan"))

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
            browser.attach_images([image])
            self.assertEqual(file_input.paths, str(image.resolve()))

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

    def test_default_flow_profile_uses_installed_chrome_profile_2(self):
        local_app_data = self.root / "AppData"
        profile = local_app_data / "Google" / "Chrome" / "User Data" / "Profile 2"
        profile.mkdir(parents=True)
        with patch.dict(os.environ, {
            "LOCALAPPDATA": str(local_app_data),
            "HUMY_FLOW_PROFILE_PATH": "",
        }):
            self.assertEqual(default_chrome_profile(self.root / "data"), profile)

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
