"""Prompt chi tiết theo mẫu: gửi lần lượt từng cảnh, mỗi prompt một dòng, kiểm tra và nhờ viết lại khi lỗi."""
import threading
import unittest

from runner import content, gemini_web
from runner.prompt_example import DETAILED_EXAMPLE


def sample(seconds):
    return DETAILED_EXAMPLE.replace("Generate an 8-second", f"Generate {content.article(seconds)} {seconds}-second")


class DetailedFormatTests(unittest.TestCase):
    def test_example_passes_checks_and_flattens_to_one_line(self):
        line = content.flatten_prompt("Here is the prompt:\n```\n" + DETAILED_EXAMPLE + "\n```")
        self.assertTrue(line.startswith("Generate an 8-second"))
        self.assertNotIn("\n", line)
        self.assertNotIn("────", line)
        self.assertEqual(content.detailed_problems(line, 8), [])
        self.assertIn("Sai thời lượng — phải là 3-second", content.detailed_problems(line, 3))
        self.assertTrue(content.tidy_prompt(line, 3).startswith("Generate a 3-second"))
        self.assertIn("Thiếu mục MAIN ACTION", " ".join(content.detailed_problems(line.replace("MAIN ACTION:", "ACTION NOW"), 8)))

    def test_request_carries_scene_duration_camera_and_previous_prompt(self):
        req = content.detailed_request("Báo tuyết leo khe đá", 2, 5, 3, [(0, 3)], previous="Generate a 3-second … snow leopard")
        self.assertIn("SCENE 2 of 5", req)
        self.assertIn("Generate a 3-second", req)
        self.assertIn("exactly ONE segment \"0–3 SECONDS", req)
        self.assertIn("PREVIOUS SCENE PROMPT", req)
        self.assertIn("STRICT NEGATIVE CONSTRAINTS", req)


class SceneBySceneTests(unittest.TestCase):
    def test_each_scene_sent_alone_fixed_when_wrong_and_reported(self):
        sent, partials = [], []
        replies = [sample(10), sample(8).replace("MAIN ACTION:", "WHAT HAPPENS"), sample(3)]  # cảnh 2: sai giây (tool tự sửa) + thiếu mục (nhờ viết lại)

        class Session:
            def new_chat(self): pass
            def wait_ready(self, *a, **k): pass
            def choose_model(self, name): return name
            def ask(self, text, *a, **k):
                sent.append(text)
                return replies.pop(0)

        windows = content.shot_windows([dict(start=0, end=10), dict(start=10, end=13)], 13)
        result = gemini_web.run_english(Session(), ["Cảnh 1", "Cảnh 2"], lambda t: None, threading.Event(),
                                        structured="detailed", windows=windows, on_batch=lambda p: partials.append(p["done"]))
        self.assertEqual(len(sent), 3)
        self.assertIn("SCENE 1 of 2", sent[0])
        self.assertIn("PREVIOUS SCENE PROMPT", sent[1])
        self.assertIn("Thiếu mục MAIN ACTION", sent[2])
        self.assertTrue(result["english"][0].startswith("Generate a 10-second"))
        self.assertTrue(result["english"][1].startswith("Generate a 3-second"))
        self.assertTrue(all("\n" not in p for p in result["english"]))
        self.assertEqual(partials, [1, 2])
        self.assertEqual(result["durations"], [10, 3])
        self.assertEqual(content.qc_problems(result["english"][1], 3), [])


if __name__ == "__main__":
    unittest.main()
