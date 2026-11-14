import json
import tempfile
import unittest
from pathlib import Path

from runner import claude_bridge as bridge


class ClaudeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "HanaMedia"

    def tearDown(self):
        self.temp.cleanup()

    def test_short_name_matches_workflow_rule(self):
        self.assertEqual(bridge.short_name("Báo Đốm Săn Mồi 01.MP4"), "bao-dom-san-moi-01")
        self.assertEqual(bridge.short_name("  ./ủa?.mov"), "ua")

    def test_send_copies_video_and_cuts(self):
        video = Path(self.temp.name) / "Sói Xám.mp4"
        video.write_bytes(b"fake")
        sent = bridge.send(self.root, video, [0.0, 3.2, 1.5, 3.2])
        self.assertEqual(sent["name"], "soi-xam")
        self.assertEqual((self.root / "input" / "soi-xam.mp4").read_bytes(), b"fake")
        lines = [l for l in (self.root / "input" / "soi-xam_cuts.txt").read_text(encoding="utf-8").splitlines()
                 if l and not l.startswith("#")]
        self.assertEqual(lines, ["1.5", "3.2"])
        self.assertEqual([p["name"] for p in bridge.pending(self.root)], ["soi-xam"])
        bridge.send(self.root, video, None)  # không gửi điểm cắt: xóa bảng cũ để Claude tự dò
        self.assertFalse((self.root / "input" / "soi-xam_cuts.txt").exists())

    def write_result(self, name, scenes):
        folder = self.root / "output" / name
        folder.mkdir(parents=True)
        path = folder / f"hanamedia_{name}.json"
        path.write_text(json.dumps({"video": f"{name}.mp4", "duration": 5.6, "cut_source": "hanamedia",
                                    "scene_count": len(scenes), "scenes": scenes}), encoding="utf-8")
        return path

    def test_parse_and_studio_prompt_keep_order_and_seconds(self):
        path = self.write_result("soi", [
            {"scene": 1, "start": 0, "end": 2.8, "duration": 2.8, "prompt": "SCENE 1 | Duration 2.8 seconds | ENVIRONMENT: a"},
            {"scene": 2, "start": 2.8, "end": 5.6, "prompt": "SCENE 2 | ENVIRONMENT: b"},
        ])
        data = bridge.parse(path)
        self.assertEqual([s["scene"] for s in data["scenes"]], [1, 2])
        self.assertEqual(data["scenes"][1]["duration"], 2.8)  # tính từ start/end
        first, second = (bridge.studio_prompt(s) for s in data["scenes"])
        self.assertEqual(first, "SCENE 1 | Duration 2.8 seconds | ENVIRONMENT: a")  # đã có số giây: giữ nguyên
        self.assertEqual(second, "SCENE 2 | Duration 2.8 seconds | ENVIRONMENT: b")
        from runner.store import prompt_seconds
        self.assertEqual(prompt_seconds(second), 2.8)
        self.assertNotIn("/", bridge.result_files(self.root)[0].name)
        self.assertEqual(bridge.batch_id(path), bridge.batch_id(path))
        self.assertTrue(bridge.inside(self.root, path))
        self.assertFalse(bridge.inside(self.root, Path(self.temp.name) / "x.json"))
        self.assertEqual(bridge.pending(self.root), [])

    def test_results_list_txt_fallback_and_bad_json(self):
        txt = self.root / "output" / "a"
        txt.mkdir(parents=True)
        (txt / "Wildlife_Prompts_EN_a.txt").write_text("SCENE 1 | x\n\nSCENE 2 | y\n", encoding="utf-8")
        bad = self.root / "output" / "b"
        bad.mkdir(parents=True)
        (bad / "hanamedia_b.json").write_text("{dở", encoding="utf-8")
        items = {i["name"]: i for i in bridge.results(self.root)}
        self.assertEqual(items["a"]["scenes"], 2)
        self.assertEqual(items["a"]["error"], "")
        self.assertIn("JSON", items["b"]["error"])
        sig = items["a"]["signature"]
        self.assertTrue({i["name"]: i for i in bridge.results(self.root, {sig: 1})}["a"]["imported"])
        key = items["a"]["folder_key"]
        self.assertRegex(key, r"^d[a-f0-9]{19}$")
        (txt / "hanamedia_a.json").write_text(json.dumps({"scenes": [{"prompt": "SCENE 1 | x"}]}), encoding="utf-8")
        later = {i["name"]: i for i in bridge.results(self.root, {sig: 1, key: 1})}["a"]
        self.assertEqual(later["file"], "hanamedia_a.json")  # JSON ghi sau: khác file nhưng cùng thư mục đã đưa
        self.assertFalse(later["imported"])
        self.assertTrue(later["folder_imported"])

    def test_txt_result_takes_seconds_from_tool_pack(self):
        pack = self.root / "input" / "a_pack"
        pack.mkdir(parents=True)
        (pack / "shots.txt").write_text("1\t0.00\t2.80\t2.80\n2\t2.80\t6.00\t3.20\n", encoding="utf-8")
        out = self.root / "output" / "a"
        out.mkdir(parents=True)
        (out / "Wildlife_Prompts_EN_a.txt").write_text("SCENE 1 | x\nSCENE 2 | y\n", encoding="utf-8")
        scenes = bridge.parse(out / "Wildlife_Prompts_EN_a.txt")["scenes"]
        self.assertEqual([s["duration"] for s in scenes], [2.8, 3.2])
        self.assertEqual(bridge.studio_prompt(scenes[1]), "SCENE 2 | Duration 3.2 seconds | y")

    def test_build_pack_with_ffmpeg(self):
        from runner import video_tools
        try:
            ffmpeg = video_tools.ffmpeg()
        except Exception:
            self.skipTest("không có ffmpeg")
        import subprocess
        video = Path(self.temp.name) / "v.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc=d=4:s=160x120:r=10", str(video)], check=True)
        sent = bridge.send(self.root, video, [1.5, 3.0], 4.0, pack=True)
        pack = self.root / "input" / "v_pack"
        self.assertEqual(sent["pack"], {"scenes": 3, "sheets": 1})
        self.assertTrue((pack / "sheets" / "s01.jpg").stat().st_size > 0)
        self.assertEqual(len((pack / "shots.txt").read_text(encoding="utf-8").split()), 12)
        self.assertEqual(sum(1 for l in (pack / "motion.txt").read_text(encoding="utf-8").splitlines() if not l.startswith("#")), 3)
        self.assertFalse((pack / "frames").exists())
        self.assertTrue((self.root / "AGENTS.md").is_file())

    def test_merged_prompts_do_not_take_per_shot_seconds(self):
        pack = self.root / "input" / "m_pack"
        pack.mkdir(parents=True)
        (pack / "shots.txt").write_text("".join(f"{i}\t0\t2.5\t2.50\n" for i in range(1, 9)), encoding="utf-8")
        out = self.root / "output" / "m"
        out.mkdir(parents=True)
        (out / "Wildlife_Prompts_EN_m.txt").write_text("SCENE 1 | a\nSCENE 2 | b\n", encoding="utf-8")
        self.assertEqual([s["duration"] for s in bridge.parse(out / "Wildlife_Prompts_EN_m.txt")["scenes"]], [None, None])
        (out / "groups_m.json").write_text(json.dumps([{"start": 0, "end": 10}, {"duration": 9.6}]), encoding="utf-8")
        self.assertEqual([s["duration"] for s in bridge.parse(out / "Wildlife_Prompts_EN_m.txt")["scenes"]], [10.0, 9.6])


if __name__ == "__main__":
    unittest.main()
