"""Chia cảnh theo góc quay: mỗi lần video gốc đổi góc máy là một cảnh, prompt dài đúng số giây của góc đó."""
import threading
import unittest

from runner import content, gemini_web

SHOTS = [dict(start=0, end=3.0), dict(start=3.0, end=3.4), dict(start=3.4, end=6.2), dict(start=6.2, end=26.2)]


class ShotWindowTests(unittest.TestCase):
    def test_each_angle_is_one_scene_with_its_own_length(self):
        windows = content.shot_windows(SHOTS, 26.2)
        self.assertEqual([(w["start"], w["end"]) for w in windows],
                         [(0.0, 3.4), (3.4, 6.2), (6.2, 16.2), (16.2, 26.2)])  # góc 0,4s gộp; góc 20s chia 2 cảnh 10s
        self.assertEqual([content.scene_seconds(w) for w in windows], [3, 3, 10, 10])
        self.assertTrue(all(w["shot"] and len(w["angles"]) == 1 for w in windows))

    def test_gemini_table_asks_for_one_angle_per_scene(self):
        table = content.scene_section(content.shot_windows(SHOTS, 26.2), 26.2)
        self.assertIn("BẢNG CẢNH THEO GÓC QUAY", table)
        self.assertIn("Viết ĐÚNG 4 cảnh", table)
        self.assertIn("dài 3.4 giây · MỘT góc máy", table)
        self.assertEqual(content.camera_problem("wide shot, static", content.shot_windows(SHOTS, 26.2)[0]), "")

    def test_prompts_get_each_scene_duration_and_keep_source_angle(self):
        windows = content.shot_windows(SHOTS, 26.2)[:2]
        line = ("SETTING: savanna; CHARACTERS: lion; CAMERA: low-angle medium shot, static; CHARACTER BEHAVIOR: tense; "
                "ACTION: the lion runs; ENDING FRAME: lion stops")
        sent = []

        class Session:
            def new_chat(self): pass
            def wait_ready(self, *a, **k): pass
            def choose_model(self, name): return name
            def ask(self, text, *a, **k):
                sent.append(text)
                return f"1. {line}\n2. {line}\n{content.END_EN}"

        result = gemini_web.run_english(Session(), ["Sư tử chạy", "Sư tử dừng"], lambda t: None, threading.Event(),
                                        seconds=10, library=True, windows=windows)
        self.assertIn("[Cảnh dài 3 giây", sent[0])
        self.assertIn("EXACTLY ONE CAMERA ANGLE", sent[0])
        self.assertIn("DURATION: 3 seconds", result["english"][0])
        self.assertIn("low-angle medium shot", result["english"][0])  # không bị thư viện góc quay thay
        self.assertEqual(result["durations"], [3, 3])
        self.assertEqual(result["lines"], ["Sư tử chạy", "Sư tử dừng"])

    def test_chunks_follow_video_seconds(self):
        windows = content.shot_windows([dict(start=i * 2.5, end=(i + 1) * 2.5) for i in range(60)], 150)
        chunks = gemini_web.chunk_windows(windows)
        self.assertTrue(all(len(c) <= gemini_web.CHUNK_MAX_SCENES for c in chunks))
        self.assertEqual(sum(len(c) for c in chunks), len(windows))


if __name__ == "__main__":
    unittest.main()
