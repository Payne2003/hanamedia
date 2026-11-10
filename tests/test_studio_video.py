"""Studio: thiết lập video từng prompt (tỉ lệ, thời lượng ≤10s, độ phân giải), ảnh nhân vật cố định cho mọi cảnh,
chọn nick, nâng độ phân giải sau khi tải (giữ file gốc) và tự ghép các cảnh thành một video dài."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from app import AppServer
from runner import video_tools
from runner.engine import Engine
from runner.muse import INDEPENDENT, compose_request
from runner.store import Store, video_options

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


def make_video(path, size="640x360", seconds=1, audio=True):
    args = [video_tools.ffmpeg(), "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    assert video_tools.run(args, 120).returncode == 0
    return path


class OptionsTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(video_options({"aspect": "9:16", "duration": 10, "resolution": "4K", "stitch": 1}),
                         {"aspect": "9:16", "duration": 10, "resolution": "4K", "stitch": True, "characters": [],
                          "account_id": None, "folder": ""})
        self.assertEqual(video_options({"folder": '  Dê núi: tập/1?  '})["folder"], "Dê núi tập 1")
        self.assertEqual(video_options({"folder": "con"})["folder"], "_con")
        self.assertEqual(video_options({"folder": "a" * 200})["folder"], "a" * 80)
        self.assertEqual(video_options(None)["duration"], 0)
        for bad in ({"duration": 12}, {"duration": "10"}, {"aspect": "21:9"}, {"resolution": "8K"},
                    {"characters": ["x"]}, {"characters": ["a" * 32] * 11}, {"account_id": "../x"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                video_options(bad)

    def test_request_spec_goes_into_muse_message(self):
        spec = video_tools.request_spec("16:9", 8, "4K")
        self.assertEqual(spec, "Thông số video: tỉ lệ khung 16:9, thời lượng 8 giây, độ phân giải 4K (Ultra HD, 3840×2160).")
        message = compose_request("Dê leo vách đá", "job1", True, spec)
        self.assertEqual(message.split("\n\n"), ["Tạo video dựa trên các ảnh tham chiếu đã đính kèm theo yêu cầu sau và trả "
                                                 "về file video để tải xuống:", "Dê leo vách đá", spec, INDEPENDENT,
                                                 "Mã tác vụ: job1"])
        self.assertNotIn("Thông số", compose_request("x", "j"))  # không chọn thông số: gửi như trước
        self.assertEqual(video_tools.request_spec("", 0, ""), "")

    def test_target_sizes(self):
        self.assertEqual(video_tools.target_size("16:9", "4K"), (3840, 2160))
        self.assertEqual(video_tools.target_size("9:16", "4K"), (2160, 3840))
        self.assertEqual(video_tools.target_size("1:1", "1080p"), (1080, 1080))
        self.assertEqual(video_tools.size_for("", "4K", {"width": 720, "height": 1280}), (2160, 3840))


class StudioStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.store = Store(self.root / "t.db")
        self.store.add_accounts("a@tuan89.site\nb@tuan89.site", 100)
        self.a, self.b = [x["id"] for x in self.store.snapshot()["accounts"]]
        self.characters = [self.store.add_image(PNG + bytes([i]))["token"] for i in range(2)]

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def jobs(self):
        return sorted(self.store.snapshot()["jobs"], key=lambda j: j["ordinal"])

    def test_fixed_characters_go_to_every_scene_first_and_switch_to_image_mode(self):
        own = self.store.add_image(PNG + b"own")["token"]
        self.store.enqueue(["Cảnh 1", "Cảnh 2", "Cảnh 3"], 1, uuid.uuid4().hex, [[], [own], [self.characters[1]]], "text",
                           options={"aspect": "9:16", "duration": 8, "resolution": "4K", "characters": self.characters})
        for job, extra in zip(self.jobs(), ([], [own], [])):
            detail = self.store.job_detail(job["id"])
            self.assertEqual(detail["images"], self.characters + extra)  # không lặp ảnh nhân vật
            self.assertEqual((job["generation_mode"], job["aspect"], job["duration"], job["resolution"]),
                             ("image", "9:16", 8, "4K"))
        too_many = [self.store.add_image(PNG + bytes([50 + i]))["token"] for i in range(9)]
        with self.assertRaisesRegex(ValueError, "vượt quá 10"):
            self.store.enqueue(["x"], 1, uuid.uuid4().hex, [too_many], "image", options={"characters": self.characters})

    def test_chosen_nick_gets_all_prompts_and_rotation_spreads(self):
        self.store.enqueue(["1", "2", "3", "4"], 1, uuid.uuid4().hex, options={"account_id": self.b})
        self.assertEqual({j["assigned_account_id"] for j in self.jobs()}, {self.b})
        self.assertTrue(all(j["assigned_manual"] for j in self.jobs()))
        self.store.enqueue(["5", "6"], 1, uuid.uuid4().hex)
        rotated = [j["assigned_account_id"] for j in self.jobs() if not j["assigned_manual"]]
        self.assertTrue(set(rotated) <= {self.a, self.b} and len(rotated) == 2)
        with self.assertRaisesRegex(ValueError, "không còn"):
            self.store.enqueue(["x"], 1, uuid.uuid4().hex, options={"account_id": "f" * 32})

    def test_same_batch_is_idempotent_and_update_job_changes_spec(self):
        batch = uuid.uuid4().hex
        for _ in range(2):
            self.store.enqueue(["1", "2"], 1, batch, options={"resolution": "1080p", "stitch": True})
        self.assertEqual(len(self.jobs()), 2)
        job = self.jobs()[0]
        updated = self.store.update_job(job["id"], "1 sửa", None, [], "text", None,
                                        {"aspect": "1:1", "duration": 5, "resolution": "720p"})
        self.assertEqual((updated["aspect"], updated["duration"], updated["resolution"]), ("1:1", 5, "720p"))
        with self.assertRaises(ValueError):
            self.store.update_job(job["id"], "1", None, [], "text", None, {"duration": 30})
        self.assertTrue(self.store.batch_stitch(batch))
        self.assertEqual(self.store.snapshot()["batches"][0]["scenes"], 2)


class PostProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = Path(tempfile.mkdtemp()).resolve()
        cls.small = make_video(cls.media / "small.mp4", "640x360", 1)
        cls.hd = make_video(cls.media / "hd.mp4", "1280x720", 1, audio=False)
        cls.tall = make_video(cls.media / "tall.mp4", "360x640", 2)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.media, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.store = Store(self.root / "t.db")
        self.store.add_account("a@tuan89.site", "", 100)
        self.engine = Engine(self.store, self.root)
        self.out = self.root / "out"
        self.out.mkdir()

    def tearDown(self):
        self.engine.shutdown()
        self.engine.close_browsers()
        shutil.rmtree(self.root, ignore_errors=True)

    def finish_all(self, videos):
        """Chạy hết tác vụ giả: mỗi tác vụ 'tải' về một video thật trong thư mục huy-<id>."""
        for video in videos:
            job, _ = self.store.claim(1)
            folder = self.out / ("huy-" + job["id"])
            folder.mkdir()
            target = folder / f"Prompt {job['ordinal'] + 1}.mp4"
            shutil.copy(video, target)
            self.store.finish(job["id"], output=str(target))

    def test_upscale_keeps_original_and_skips_when_big_enough(self):
        self.store.enqueue(["nhỏ", "đủ"], 1, uuid.uuid4().hex, options={"aspect": "16:9", "resolution": "720p"})
        self.finish_all([self.small, self.hd])
        self.assertEqual(len(self.store.post_pending()), 2)  # khởi động lại giữa chừng vẫn làm tiếp
        for job_id in self.store.post_pending():
            self.engine.post_job(job_id)
        jobs = sorted(self.store.snapshot()["jobs"], key=lambda j: j["ordinal"])
        self.assertEqual(jobs[0]["post_status"], "upscaled:1280x720")
        self.assertEqual(video_tools.probe(jobs[0]["upscaled_output"])["height"], 720)
        self.assertTrue(Path(jobs[0]["output"]).is_file())  # file gốc vẫn còn
        self.assertTrue(jobs[0]["upscaled_output"].endswith("Prompt 1 (720p).mp4"))
        self.assertEqual((jobs[1]["post_status"], jobs[1]["upscaled_output"]), ("ok:1280x720", None))
        self.assertEqual(self.store.post_pending(), [])

    def test_stitch_waits_for_every_scene_then_joins_in_order(self):
        batch = uuid.uuid4().hex
        self.store.enqueue(["1", "2", "3"], 1, batch, options={"aspect": "16:9", "resolution": "720p", "stitch": True})
        self.finish_all([self.small, self.tall])
        self.assertEqual(self.store.stitch_candidates(), [])  # cảnh 3 chưa xong
        self.finish_all([self.hd])
        for job_id in self.store.post_pending():
            self.engine.post_job(job_id)
        self.assertEqual(self.store.stitch_candidates(), [batch])
        self.engine.stitch_batch(batch)
        row = self.store.snapshot()["batches"][0]
        self.assertEqual(row["stitch_status"], "done", row["stitch_error"])
        info = video_tools.probe(row["stitch_output"])
        self.assertEqual((info["width"], info["height"], info["audio"]), (1280, 720, True))
        self.assertAlmostEqual(info["duration"], 4.0, delta=0.3)  # 1 + 2 + 1 giây
        self.assertEqual(Path(row["stitch_output"]).parent, self.out)
        self.assertEqual(self.store.stitch_candidates(), [])
        # Chạy lại một cảnh: lượt đó sẽ được ghép lại khi xong.
        job = sorted(self.store.snapshot()["jobs"], key=lambda j: j["ordinal"])[0]
        self.store.rerun(job["id"])
        self.assertEqual(self.store.snapshot()["batches"][0]["stitch_status"], "")

    def test_stitch_now_skips_failed_scene(self):
        batch = uuid.uuid4().hex
        self.store.enqueue(["1", "2", "3"], 1, batch, options={"stitch": True})
        self.finish_all([self.small, self.hd])
        job, _ = self.store.claim(1)
        self.store.finish(job["id"], error="Muse lỗi")
        self.assertEqual(self.store.stitch_candidates(), [])
        self.engine.stitch_batch(batch, manual=True)
        row = self.store.snapshot()["batches"][0]
        self.assertEqual((row["stitch_status"], row["failed"]), ("done", 1))
        self.assertAlmostEqual(video_tools.probe(row["stitch_output"])["duration"], 2.0, delta=0.3)

    def test_stitch_needs_two_scenes(self):
        batch = uuid.uuid4().hex
        self.store.enqueue(["1"], 1, batch, options={"stitch": True})
        self.finish_all([self.small])
        self.engine.stitch_batch(batch)
        self.assertEqual(self.store.snapshot()["batches"][0]["stitch_status"], "error")


class StudioHTTPTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.store = Store(self.root / "t.db")
        self.store.add_account("a@tuan89.site", "", 100)
        self.stitched = []
        engine = type("E", (), {"snapshot": lambda e: {}, "changed": threading.Event(),
                                "stitch_now": lambda e, batch: self.stitched.append(batch)})()
        self.server = AppServer(0, self.store, engine)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Origin": self.server.origin, "X-Runner-Key": self.server.secret,
                                    "Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw

    def test_jobs_options_stitch_and_video(self):
        batch = str(uuid.uuid4())
        status, _ = self.call("POST", "/api/jobs", {"prompts": ["1", "2"], "cost": 1, "batch": batch, "mode": "text",
                                                    "options": {"aspect": "9:16", "duration": 10, "resolution": "4K",
                                                                "stitch": True}})
        self.assertEqual(status, 200)
        state = json.loads(self.call("GET", "/api/state")[1])
        self.assertEqual({(j["aspect"], j["duration"], j["resolution"]) for j in state["jobs"]}, {("9:16", 10, "4K")})
        self.assertEqual(state["batches"][0]["batch"], batch)
        self.assertEqual(self.call("POST", "/api/jobs", {"prompts": ["x"], "cost": 1, "batch": str(uuid.uuid4()),
                                                         "options": {"duration": 20}})[0], 400)
        self.assertEqual(self.call("POST", "/api/batch/stitch", {"batch": batch})[0], 200)
        self.assertEqual(self.stitched, [batch])
        self.assertEqual(self.call("POST", "/api/batch/stitch", {"batch": "../x"})[0], 400)
        self.assertEqual(self.call("GET", f"/api/batch/{batch}/video")[0], 404)
        video = make_video(self.root / "ghép.mp4", "320x180", 1)
        self.store.set_stitch(batch, "done", str(video))
        status, raw = self.call("GET", f"/api/batch/{batch}/video")
        self.assertEqual((status, raw), (200, video.read_bytes()))


if __name__ == "__main__":
    unittest.main()
