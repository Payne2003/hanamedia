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


class BatchOfScenesTests(unittest.TestCase):
    def test_three_scenes_per_request_auto_complete_fix_and_report(self):
        sent, partials = [], []
        no_style = sample(10).split("STYLE:")[0]  # thiếu STYLE/NEGATIVE: tool tự thêm, không nhờ viết lại
        no_action = sample(3).replace("MAIN ACTION:", "WHAT HAPPENS")  # thiếu MAIN ACTION: phải nhờ AI viết lại
        replies = [f"SCENE 1:\n{no_style}\n\nSCENE 2:\n{no_action}\n{content.END_EN}",  # bỏ sót cảnh 3
                   f"SCENE 3:\n{sample(3)}\n{content.END_EN}",
                   f"SCENE 2:\n{sample(3)}\n{content.END_EN}",
                   sample(3)]  # nhóm 2 chỉ một cảnh, không ghi "SCENE 4:"

        class Session:
            def new_chat(self): pass
            def wait_ready(self, *a, **k): pass
            def choose_model(self, name): return name
            def ask(self, text, *a, **k):
                sent.append(text)
                return replies.pop(0)

        windows = content.shot_windows([dict(start=0, end=10), dict(start=10, end=13), dict(start=13, end=16),
                                        dict(start=16, end=19)], 19)
        from unittest import mock
        with mock.patch.object(gemini_web, "DETAILED_BATCH", 3):  # kiểm tra cách chia nhóm với nhóm 3 cảnh
            result = gemini_web.run_english(Session(), ["C1", "C2", "C3", "C4"], lambda t: None, threading.Event(),
                                            structured="detailed", windows=windows, on_batch=lambda p: partials.append(p["done"]))
        self.assertEqual(len(sent), 4)
        self.assertIn("SCENES 1–3 of 4", sent[0])
        self.assertIn("You skipped SCENE 3", sent[1])
        self.assertIn("SCENE 2: Thiếu mục MAIN ACTION", sent[2])
        self.assertIn("PREVIOUS SCENE PROMPT", sent[3])
        self.assertTrue(result["english"][0].startswith("Generate a 10-second"))
        self.assertIn("STYLE:", result["english"][0])  # tool tự thêm mục chuẩn
        self.assertEqual([content.detailed_problems(p, d) for p, d in zip(result["english"], [10, 3, 3, 3])], [[]] * 4)
        self.assertTrue(all("\n" not in p for p in result["english"]))
        self.assertEqual(partials, [3, 4])
        self.assertEqual(result["durations"], [10, 3, 3, 3])


if __name__ == "__main__":
    unittest.main()
