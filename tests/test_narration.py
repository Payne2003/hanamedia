import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runner import narration, video_tools as video
from runner.narration_skills import SLUG
from runner.workflows import Workflows, WorkflowError
from tests.test_voice import FakeStudio, tone


class NarrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "HanaMedia"
        self.patch = mock.patch("pathlib.Path.home", return_value=self.base / "user")
        self.patch.start()
        self.flows = Workflows(lambda: self.home)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def write(self, name, lines, **extra):
        folder = self.home / "output" / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"thoai_{name}.json"
        path.write_text(json.dumps(dict({"video": f"{name}.mp4", "duration": 6.0, "lines": lines}, **extra)), encoding="utf-8")
        return path

    def test_builtin_workflow_skills_genres_and_brief(self):
        slugs = [w["slug"] for w in self.flows.list()]
        self.assertEqual(slugs[0], "wildlife")
        self.assertIn(SLUG, slugs)
        wf = self.flows.load(SLUG)
        self.assertEqual(wf["agents"]["writer"]["skill"], "bien-kich-thoai")
        self.assertEqual({s["name"] for s in wf["skills"]}, {"thoai-phim-workflow", "bien-kich-thoai", "kiem-duyet-thoai"})
        with self.assertRaises(WorkflowError):
            self.flows.delete(SLUG)
        self.assertEqual([g["slug"] for g in narration.genres(self.home)], ["san-duoi", "sinh-ton-me-con", "tai-lieu"])
        # bản trên máy là bản chính: sửa xong không bị ghi đè
        skill = self.home / "workflows" / SLUG / "skills" / "bien-kich-thoai" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nSỬA RIÊNG", encoding="utf-8")
        self.flows.list()
        self.assertIn("SỬA RIÊNG", skill.read_text(encoding="utf-8"))
        slug = narration.add_genre(self.home, "Hài hước động vật")
        self.assertEqual(slug, "hai-huoc-dong-vat")
        with self.assertRaises(narration.NarrationError):
            narration.add_genre(self.home, "Hài hước động vật")
        with self.assertRaises(narration.NarrationError):
            narration.add_sample(self.home, slug, "ngắn")
        with self.assertRaises(narration.NarrationError):
            narration.add_sample(self.home, "../x", "a" * 100)
        self.assertEqual(narration.add_sample(self.home, slug, "Câu mẫu thứ nhất. " * 5, "Mèo rừng"), "meo-rung.txt")
        self.assertEqual(next(g for g in narration.genres(self.home) if g["slug"] == slug)["samples"], 1)
        self.flows.save_brief(SLUG, {"genre": "san-duoi", "language": "vi", "note": "kết mở"})
        prompt = self.flows.prompt(SLUG, "run")
        self.assertIn("genre = san-duoi", prompt)
        self.assertIn("Chạy thoai-phim-workflow", prompt)
        done = self.flows.sync(SLUG)
        self.assertEqual(done["claude"], 3)
        self.assertTrue((self.home / ".claude/skills/bien-kich-thoai/the-loai/san-duoi/phong-cach.md").is_file())

    def test_parse_cleans_lines(self):
        path = self.write("a", [{"start": 0, "end": 2, "text": " Một  hai "}, {"start": "3,5", "end": 5, "text": "Ba"},
                               {"start": 1, "end": 2, "text": "lùi"}, {"start": 5.5, "text": ""}, "rác"])
        data = narration.parse(path)
        self.assertEqual([(l["n"], l["start"], l["text"]) for l in data["lines"]], [(1, 0.0, "Một hai"), (2, 3.5, "Ba"), (3, 3.5, "lùi")])
        self.assertTrue(narration.inside(self.home, path))
        self.assertFalse(narration.inside(self.home, self.base / "x.json"))
        bad = self.write("b", [])
        self.assertIn("không có đoạn thoại", narration.results(self.home)[0]["error"] + narration.results(self.home)[1]["error"])
        self.assertTrue(bad.is_file())

    def test_to_voice_then_mux_places_lines_on_timeline(self):
        try:
            video.ffmpeg()
        except Exception:
            self.skipTest("không có ffmpeg")
        (self.home / "input").mkdir(parents=True)
        clip = self.home / "input" / "phim.mp4"
        done = video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=6",
                          "-f", "lavfi", "-i", "sine=frequency=220:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-shortest", str(clip)], 120)
        self.assertEqual(done.returncode, 0, done.stderr)
        path = self.write("phim", [{"start": 0.5, "end": 2, "text": "Câu một."}, {"start": 0.6, "end": 2.5, "text": "Câu hai."},
                                  {"start": 4.0, "end": 5.5, "text": "Câu ba."}])
        studio = FakeStudio(self.base / "data")
        sample = self.base / "s.wav"
        tone(sample, 5)
        profile = studio.add_profile(sample.read_bytes(), "s.wav", "P", "lời", consent=True)
        with self.assertRaises(narration.NarrationError):
            narration.mux(self.home, studio, path)  # chưa đưa sang AI Voice
        made = narration.to_voice(studio, path, profile["id"], {"format": "wav"})
        self.assertEqual(made["lines"], 3)
        rows = [j for j in studio.jobs() if j.get("group") == made["group"]]
        self.assertEqual({j["status"] for j in rows}, {"draft"})  # dừng cho sửa trước khi đọc
        item = narration.results(self.home, studio.jobs())[0]
        self.assertEqual((item["voice_total"], item["voice_done"], item["has_video"]), (3, 0, True))
        with self.assertRaises(narration.NarrationError):
            narration.mux(self.home, studio, path)  # chưa tạo giọng
        studio.start_group(made["group"])
        for job in sorted(rows, key=lambda j: j["index"]):
            studio.process(job["id"])
        result = narration.mux(self.home, studio, path)
        output = Path(result["output"])
        self.assertEqual(output.name, "phim_thoai.mp4")
        info = video.probe(output)
        self.assertTrue(info["audio"])
        self.assertAlmostEqual(info["duration"], 6.0, delta=0.2)
        self.assertEqual(result["late"], [2])  # dòng 2 đặt mốc 0,6 s nhưng dòng 1 (0,4 s từ 0,5) chưa đọc xong
        self.assertFalse((output.parent / ".thoai-mux").exists())
        self.assertEqual(narration.results(self.home, studio.jobs())[0]["muxed"], str(output))


if __name__ == "__main__":
    unittest.main()
