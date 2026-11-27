"""Khung hình thực của gói Agent Phân tích + xem kỹ cảnh khó (runner/frames.py, scene_review.py, build_pack).

Video thử: mỗi cảnh một màu nền; một vạch trắng chạy ngang với x = giây × 120 (tính theo mốc hiển thị thật của từng khung), nên
đọc vạch trên ảnh trích ra là biết ảnh đó thuộc giây nào — kiểm chứng được mà không cần tin vào mốc ghi trong file.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runner import claude_bridge as bridge
from runner import frames as framelib
from runner import scene_review
from runner import video_tools as video

W, H, FPS = 320, 180, 30
COLORS = ["red", "green", "blue", "yellow", "purple", "orange", "cyan"]


def make_video(path, frame_counts, vfr=False):
    """frame_counts: số khung mỗi cảnh (30 fps). vfr=True: mốc khung lệch ±12 ms (tốc độ khung hình biến đổi).
    Màu nền đổi theo SỐ KHUNG (drawbox + enable=between(n,…)) nên cảnh 1–2 khung vẫn có mốc rõ ràng."""
    total = sum(frame_counts)
    chain = [f"color=c=black:s={W}x{H}:r={FPS}", f"trim=end_frame={total}", f"setpts=N/({FPS}*TB)"]
    first = 0
    for i, count in enumerate(frame_counts):
        chain.append(f"drawbox=x=0:y=0:w={W}:h={H}:color={COLORS[i % len(COLORS)]}:t=fill:enable='between(n,{first},{first + count - 1})'")
        first += count
    chain.append("settb=1/90000")
    if vfr:
        chain.append("setpts='PTS+0.012*sin(N*1.7)/TB'")
    # vạch trắng: overlay có biến t (mốc hiển thị của khung chính); drawbox KHÔNG có biến thời gian (t là độ dày nét)
    graph = (",".join(chain) + f"[base];color=c=white:s=8x{H}:r={FPS}[bar];"
             "[base][bar]overlay=x='mod(t*120,300)':y=0:eval=frame:shortest=1[out]")
    subprocess.run([video.ffmpeg(), "-v", "error", "-y", "-filter_complex", graph, "-map", "[out]", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-fps_mode", "passthrough",
                    "-enc_time_base:v", "1/90000", "-video_track_timescale", "90000", str(path)], check=True)


def bar_x(picture, scale):
    """Tâm vạch trắng trên ảnh (px, quy về khung gốc), hoặc None."""
    y = picture.height * 3 // 5  # dưới ô nhãn ở góc trái
    xs = [x for x in range(picture.width) if min(picture.getpixel((x, y))) > 160]
    return ((min(xs) + max(xs) + 1) / 2 / scale) if xs else None


def bar_time(image_path, scale=1.0):
    """Giây của khung ảnh, đọc từ vị trí vạch trắng (tâm vạch x = giây × 120 + 4)."""
    from PIL import Image
    with Image.open(image_path) as picture:
        x = bar_x(picture.convert("RGB"), scale)
    return None if x is None else (x - 4) / 120


def sheet_tile(sheet, row, col):
    """Cắt ô (hàng, cột bắt đầu từ 1) của tờ ảnh: tile 6 cột, mỗi ô 256×144."""
    from PIL import Image
    x, y = (col - 1) * (256 + 4), (row - 1) * (144 + 4)   # ffmpeg tile: margin 0, padding 4 giữa các ô
    with Image.open(sheet) as picture:
        return picture.convert("RGB").crop((x, y, x + 256, y + 144))


def tile_time(tile):
    x = bar_x(tile, 0.8)
    return None if x is None else (x - 4) / 120


def detect_cuts(times, counts):
    """Điểm cắt = mốc khung đầu tiên của từng cảnh (đọc từ chính file video, như tool đo)."""
    cuts, index = [], 0
    for count in counts[:-1]:
        index += count
        cuts.append(round(times[index], 3))
    return cuts


class PackFramesTests(unittest.TestCase):
    def setUp(self):
        try:
            video.ffmpeg()
            import PIL  # noqa: F401
        except Exception:
            self.skipTest("cần ffmpeg và Pillow")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def pack(self, counts, vfr=False):
        source = self.root / ("v.mp4")
        make_video(source, counts, vfr)
        times = framelib.frame_pts(source)
        self.assertEqual(len(times), sum(counts))
        cuts = detect_cuts(times, counts)
        target = self.root / "input" / "v_pack"
        target.parent.mkdir(parents=True, exist_ok=True)
        info = bridge.build_pack(source, target, cuts, round(times[-1] + 1 / FPS, 3))
        self.assertEqual(info["scenes"], len(counts))
        return source, target, times, cuts, json.loads((target / "frames.json").read_text(encoding="utf-8"))

    def test_half_second_scene_has_three_different_real_frames_in_order(self):
        _, pack, times, cuts, data = self.pack([30, 15, 30])  # cảnh 2 dài 0,5 giây (15 khung)
        scene = data["scenes"][1]
        shown = [f["time"] for f in scene["frames"]]
        self.assertEqual(len(shown), 3)
        self.assertEqual(shown, sorted(set(shown)))                      # khác nhau, đúng thứ tự thời gian
        self.assertTrue(all(scene["start"] - .001 <= t < scene["end"] - .001 for t in shown), shown)
        self.assertTrue(all(f["verified"] for f in scene["frames"]))
        self.assertEqual(scene["valid_frames"], 15)
        # ảnh THẬT trên tờ ảnh khớp mốc ghi trong frames.json (đọc vạch trắng), không phải ba ảnh giống nhau
        sheet = pack / "sheets" / "s01.jpg"
        decoded = [tile_time(sheet_tile(sheet, f["row"], f["col"])) for f in scene["frames"]]
        for want, got in zip(shown, decoded):
            self.assertIsNotNone(got)
            self.assertAlmostEqual(got, want, delta=.025)
        self.assertEqual(len({round(d, 2) for d in decoded}), 3)
        text = (pack / "frames.txt").read_text(encoding="utf-8")
        self.assertIn("2\tđầu\t", text)
        self.assertTrue((pack / "REVIEW.md").is_file())

    def test_scene_with_one_or_two_frames_records_exact_count(self):
        _, pack, times, cuts, data = self.pack([30, 1, 2, 30])
        counts = [len(s["frames"]) for s in data["scenes"]]
        self.assertEqual(counts, [3, 1, 2, 3])
        self.assertEqual([s["valid_frames"] for s in data["scenes"]], [30, 1, 2, 30])
        one, two = data["scenes"][1], data["scenes"][2]
        self.assertEqual(one["frames"][0]["slot"], "duy nhất")
        self.assertEqual([f["slot"] for f in two["frames"]], ["đầu", "cuối"])
        self.assertLess(one["frames"][0]["time"], two["frames"][0]["time"])
        self.assertTrue(all(f["verified"] for f in one["frames"] + two["frames"]))
        # ô thiếu khung để đen, không nhân bản khung khác
        sheet = pack / "sheets" / "s01.jpg"
        missing = sheet_tile(sheet, 1, 3 + 1 + 2)  # cảnh 2 nằm nửa phải hàng 1: cột 4,5,6; cột 5 và 6 để trống
        self.assertLess(max(missing.getextrema()[0]), 40)
        self.assertIn("chỉ có 1 khung hợp lệ", (pack / "frames.txt").read_text(encoding="utf-8"))

    def test_variable_frame_rate_frames_belong_to_their_scene(self):
        source, pack, times, cuts, data = self.pack([24, 12, 24, 9], vfr=True)
        deltas = [b - a for a, b in zip(times, times[1:])]
        self.assertGreater(max(deltas) - min(deltas), .008)              # đúng là tốc độ khung hình biến đổi
        sheet = pack / "sheets" / "s01.jpg"
        for scene in data["scenes"]:
            for f in scene["frames"]:
                self.assertTrue(scene["start"] - .001 <= f["time"] < scene["end"] - .001, (scene["scene"], f))
                self.assertAlmostEqual(tile_time(sheet_tile(sheet, f["row"], f["col"])), f["time"], delta=.025)

    def test_old_pack_without_new_files_still_reads(self):
        pack = self.root / "input" / "old_pack"
        pack.mkdir(parents=True)
        (pack / "shots.txt").write_text("1\t0.00\t2.80\t2.80\n2\t2.80\t6.00\t3.20\n", encoding="utf-8")
        (pack / "sheets.txt").write_text("1\ts01.jpg\thàng 1\tcột 1–3 (trái)\t0.00-2.80s\n", encoding="utf-8")
        self.assertEqual(scene_review.load_shots(pack), {1: (0.0, 2.8), 2: (2.8, 6.0)})
        self.assertEqual(scene_review.pending(pack), [])
        self.assertEqual(bridge.review_pending(self.root, "old"), [])
        out = self.root / "output" / "old"
        out.mkdir(parents=True)
        (out / "phan-tich-canh_old.md").write_text("x", encoding="utf-8")
        board = {b["name"]: b for b in bridge.progress(self.root)}
        self.assertEqual(board["old"]["stages"]["analyst"], "done")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        try:
            video.ffmpeg()
            import PIL  # noqa: F401
        except Exception:
            self.skipTest("cần ffmpeg và Pillow")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.counts = [30, 15, 15, 30]   # cảnh 2 và 3 liền nhau, mỗi cảnh 0,5 giây
        self.source = self.root / "input" / "v.mp4"
        self.source.parent.mkdir(parents=True)
        make_video(self.source, self.counts)
        self.times = framelib.frame_pts(self.source)
        self.cuts = detect_cuts(self.times, self.counts)
        self.pack = self.root / "input" / "v_pack"
        bridge.build_pack(self.source, self.pack, self.cuts, round(self.times[-1] + 1 / FPS, 3))
        self.shots = scene_review.load_shots(self.pack)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, *items):
        (self.pack / scene_review.REQUEST_FILE).write_text(json.dumps({"requests": list(items)}), encoding="utf-8")
        return scene_review.process(self.pack)

    def test_review_frames_never_cross_the_cut_between_adjacent_scenes(self):
        start2, end2 = self.shots[2]
        start3, end3 = self.shots[3]
        self.assertAlmostEqual(end2, start3, delta=.011)
        # Agent xin cả khoảng vắt qua điểm cắt giữa cảnh 2 và 3: mỗi yêu cầu chỉ nhận ảnh thuộc đúng cảnh của nó
        status = self.request({"id": "a", "scene": 2, "from": start2 - .3, "to": end2 + .3, "question": "Hai con có chạm nhau không?"},
                              {"id": "b", "scene": 3, "from": start3 - .2, "to": start3 + .2, "frames": 5})
        a, b = status["requests"]
        for item, (start, end) in ((a, (start2, end2)), (b, (start3, end3))):
            self.assertEqual(item["status"], "done", item)
            self.assertTrue(item["frames"])
            for f in item["frames"]:
                self.assertTrue(start - .001 <= f["time"] < end - .001, (item["id"], f, start, end))
                got = bar_time(self.pack / item["dir"] / f["file"], scale=REVIEW_SCALE)
                self.assertAlmostEqual(got, f["time"], delta=.025)   # ảnh trích ra đúng là khung của mốc đó
        self.assertIn("cắt về trong cảnh", a["note"])
        self.assertEqual(b["frames"][0]["time"], round(min(f["time"] for f in b["frames"]), 3))
        # ảnh đủ lớn, có mốc giây trên nhãn + trong tên file
        from PIL import Image
        with Image.open(self.pack / a["dir"] / a["frames"][0]["file"]) as picture:
            self.assertGreaterEqual(picture.width, 1000)
        self.assertRegex(a["frames"][0]["file"], r"^f01_t\d+\.\d{3}\.jpg$")

    def test_range_and_request_limits_and_reuse(self):
        start, end = self.shots[1]
        many = [{"id": f"r{i}", "scene": 1, "from": 0.1 * i, "to": 0.1 * i + 0.4} for i in range(1, 11)]
        status = self.request(*many)
        states = [r["status"] for r in status["requests"]]
        self.assertEqual(states[:scene_review.MAX_REQUESTS], ["done"] * scene_review.MAX_REQUESTS)
        self.assertEqual(states[scene_review.MAX_REQUESTS:], ["limit", "limit"])
        self.assertTrue(all(len(r["frames"]) <= scene_review.MAX_FRAMES for r in status["requests"]))
        wide = self.request({"id": "w", "scene": 1, "from": 0, "to": 99, "frames": 50})["requests"][0]
        self.assertLessEqual(wide["to"] - wide["from"], scene_review.MAX_SPAN)
        self.assertLessEqual(len(wide["frames"]), scene_review.MAX_FRAMES)
        # chạy lại (file yêu cầu đổi nhưng cùng khoảng): dùng lại ảnh, không gọi ffmpeg trích thêm
        calls = []
        real = framelib.extract
        with mock.patch.object(framelib, "extract", side_effect=lambda *a, **k: calls.append(1) or real(*a, **k)):
            again = self.request({"id": "w", "scene": 1, "from": 0, "to": 99, "frames": 50, "question": "đổi chữ"})
        self.assertEqual(calls, [])
        self.assertTrue(again["requests"][0]["reused"])

    def test_invalid_requests_are_reported_not_extracted(self):
        status = self.request({"id": "x", "scene": 99}, {"id": "y", "scene": 2, "from": 10, "to": 20},
                              {"id": "z", "scene": 2, "crop": [0.9, 0.9, 0.5, 0.5]}, {"id": "t", "scene": 2, "from": 1.0, "to": 0.5})
        self.assertEqual([r["status"] for r in status["requests"]], ["invalid"] * 4)
        self.assertIn("ngoài cảnh", status["requests"][1]["note"])

    def test_crop_zooms_into_the_contact_area(self):
        start, end = self.shots[2]
        item = self.request({"id": "c", "scene": 2, "from": start, "to": end, "frames": 3, "crop": [0.0, 0.0, 0.5, 0.5]})["requests"][0]
        self.assertEqual(item["status"], "done")
        from PIL import Image
        with Image.open(self.pack / item["dir"] / item["frames"][0]["file"]) as picture:
            self.assertEqual(picture.width, scene_review.REVIEW_WIDTH)
            self.assertAlmostEqual(picture.height / picture.width, (H * .5) / (W * .5), delta=.01)

    def test_conclusions_unlock_the_analysis_step_and_write_the_handoff(self):
        start, end = self.shots[2]
        board = lambda: {b["name"]: b for b in bridge.progress(self.root)}["v"]["stages"]
        out = self.root / "output" / "v"
        out.mkdir(parents=True)
        (out / "phan-tich-canh_v.md").write_text("bảng", encoding="utf-8")
        self.assertEqual(board()["analyst"], "done")                         # chưa có yêu cầu xem kỹ
        self.request({"id": "r1", "scene": 2, "from": start, "to": end, "question": "Báo có chạm dê không?"})
        self.assertEqual(scene_review.pending(self.pack), ["r1"])
        self.assertEqual(board()["analyst"], "working")                      # còn yêu cầu chưa có kết luận
        self.assertNotEqual(board()["writer"], "working")
        handoff = (self.pack / scene_review.HANDOFF_FILE).read_text(encoding="utf-8")
        self.assertIn("CHƯA CÓ KẾT LUẬN", handoff)
        (out / "Wildlife_Prompts_EN_v.txt").write_text("SCENE 1 | x\n", encoding="utf-8")
        (out / "hanamedia_v.json").write_text(json.dumps({"scenes": [{"scene": 1, "prompt": "x"}]}), encoding="utf-8")
        item = [r for r in bridge.results(self.root) if r["name"] == "v"][0]
        self.assertEqual(item["review_pending"], 1)                          # kết quả chưa tự vào Studio
        self.assertFalse(bridge.project_done(self.root, "v", "wildlife", settle=0))
        (self.pack / scene_review.CONCLUSION_FILE).write_text(json.dumps({"r1": {
            "conclusion": "Báo nhảy về phía dê, điểm tiếp xúc bị đá che", "certainty": "chưa xác định",
            "contact": "chưa xác định tiếp xúc", "evidence": ["1.20s", "1.30s"]}}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(scene_review.pending(self.pack), [])
        self.assertEqual(board()["analyst"], "done")
        scene_review.scan(self.root)                                          # luồng nền cập nhật bản bàn giao
        handoff = (self.pack / scene_review.HANDOFF_FILE).read_text(encoding="utf-8")
        self.assertIn("Kết luận (chưa xác định): Báo nhảy về phía dê, điểm tiếp xúc bị đá che", handoff)
        self.assertNotIn("CHƯA CÓ KẾT LUẬN", handoff)
        self.assertEqual([r for r in bridge.results(self.root) if r["name"] == "v"][0]["review_pending"], 0)

    def test_scan_processes_new_requests_in_the_background_step(self):
        start, end = self.shots[2]
        (self.pack / scene_review.REQUEST_FILE).write_text(json.dumps([{"scene": 2, "from": start, "to": end}]), encoding="utf-8")
        self.assertEqual(scene_review.scan(self.root), ["v_pack"])
        status = json.loads((self.pack / scene_review.STATUS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(status["requests"][0]["status"], "done")
        self.assertEqual(scene_review.scan(self.root), [])                    # không đổi thì không làm lại


REVIEW_SCALE = scene_review.REVIEW_WIDTH / W


class SendToWriterFlowTests(unittest.TestCase):
    """«Gửi video cho agent» → gói dữ liệu → Agent xin xem kỹ → luồng nền của tool trích ảnh → Agent ghi kết luận → Agent Viết prompt
    nhận bản bàn giao. Chạy qua máy chủ thật của tool (luồng review_loop), video thử có cảnh 0,7 giây liền nhau."""

    def setUp(self):
        try:
            video.ffmpeg()
            import PIL  # noqa: F401
        except Exception:
            self.skipTest("cần ffmpeg và Pillow")
        import http.client
        import threading
        from app import AppServer
        from runner.store import Store
        self.http = http.client
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.store = Store(self.root / "studio.sqlite3")
        self.home = self.root / "HanaMedia"
        self.store.save_setting("claude_folder", str(self.home))
        engine = type("Idle", (), {"snapshot": lambda self: {}, "changed": threading.Event()})()
        self.server = AppServer(0, self.store, engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        import shutil
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        shutil.rmtree(self.root, ignore_errors=True)

    def post(self, path, body):
        connection = self.http.HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        connection.request("POST", path, body=json.dumps(body), headers={
            "Origin": self.server.origin, "X-Runner-Key": self.server.secret, "Content-Type": "application/json"})
        response = connection.getresponse()
        data = json.loads(response.read() or b"{}")
        connection.close()
        return response.status, data

    def wait(self, check, seconds=25):
        import time
        end = time.time() + seconds
        while time.time() < end:
            value = check()
            if value:
                return value
            time.sleep(.3)
        self.fail("quá thời gian chờ luồng nền của tool")

    def test_full_flow_from_send_video_to_writer_handoff(self):
        clip = self.root / "Báo săn dê.mp4"
        make_video(clip, [45, 21, 21, 45])
        status, data = self.post("/api/claude/send", {"path": str(clip)})
        self.assertEqual(status, 200, data)
        name = data["sent"]["name"]
        pack = self.home / "input" / f"{name}_pack"
        self.assertEqual(data["sent"]["pack"]["scenes"], 4)
        frames = json.loads((pack / "frames.json").read_text(encoding="utf-8"))
        scene2 = frames["scenes"][1]
        self.assertEqual([len(s["frames"]) for s in frames["scenes"]], [3, 3, 3, 3])
        shown = [f["time"] for f in scene2["frames"]]
        self.assertEqual(shown, sorted(set(shown)))
        # Agent Phân tích đã có bản phân tích nhưng thấy cảnh 2 khó → xin xem kỹ (khoảng vắt qua cả điểm cắt kế tiếp)
        out = self.home / "output" / name
        out.mkdir(parents=True)
        (out / f"phan-tich-canh_{name}.md").write_text("| cảnh | ... |", encoding="utf-8")
        request = {"requests": [{"id": "r1", "scene": 2, "from": scene2["start"] + .1, "to": scene2["end"] + .5,
                                 "question": "Báo chạm dê hay chỉ lao tới?"}]}
        (pack / scene_review.REQUEST_FILE).write_text(json.dumps(request), encoding="utf-8")
        result = self.wait(lambda: scene_review.read_json(pack / scene_review.STATUS_FILE))
        item = result["requests"][0]
        self.assertEqual(item["status"], "done")
        self.assertTrue(item["frames"] and all(scene2["start"] - .001 <= f["time"] < scene2["end"] - .001 for f in item["frames"]))
        self.assertTrue(all((pack / item["dir"] / f["file"]).stat().st_size > 5000 for f in item["frames"]))
        # trong lúc còn yêu cầu chưa có kết luận: bước phân tích vẫn đang làm, kết quả chưa tự vào Studio
        _, board = self.post("/api/claude/board", {})
        stages = [b for b in board["board"] if b["name"] == name][0]["stages"]
        self.assertEqual(stages["analyst"], "working")
        (out / f"Wildlife_Prompts_EN_{name}.txt").write_text("SCENE 1 | x\n", encoding="utf-8")
        (out / f"hanamedia_{name}.json").write_text(json.dumps({"scenes": [{"scene": 1, "prompt": "x"}]}), encoding="utf-8")
        _, listed = self.post("/api/claude/status", {})
        self.assertEqual([r["review_pending"] for r in listed["results"] if r["name"] == name], [1])
        # Agent xem ảnh và ghi kết luận → luồng nền cập nhật bản bàn giao cho Agent Viết prompt
        (pack / scene_review.CONCLUSION_FILE).write_text(json.dumps({"r1": {
            "conclusion": "Báo nhảy về phía dê, điểm tiếp xúc bị đá che", "certainty": "chưa xác định",
            "contact": "chưa xác định tiếp xúc", "evidence": [f"{shown[1]:.2f}s"]}}, ensure_ascii=False), encoding="utf-8")
        handoff = self.wait(lambda: "Kết luận" in (pack / scene_review.HANDOFF_FILE).read_text(encoding="utf-8") and
                            (pack / scene_review.HANDOFF_FILE).read_text(encoding="utf-8"))
        self.assertIn("Báo nhảy về phía dê, điểm tiếp xúc bị đá che", handoff)
        self.assertIn("chưa xác định tiếp xúc", handoff)
        self.assertNotIn("CHƯA CÓ KẾT LUẬN", handoff)
        _, listed = self.post("/api/claude/status", {})
        self.assertEqual([r["review_pending"] for r in listed["results"] if r["name"] == name], [0])
        _, board = self.post("/api/claude/board", {})
        self.assertEqual([b for b in board["board"] if b["name"] == name][0]["stages"]["analyst"], "done")


if __name__ == "__main__":
    unittest.main()
