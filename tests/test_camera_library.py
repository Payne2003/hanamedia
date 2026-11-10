"""Thư viện góc quay: hàng nghìn góc, mỗi cảnh 10 giây đúng 3 góc theo hành động, không lặp cảnh trước."""
import threading
import unittest

from runner import camera_library as cam
from runner import content


class CameraLibraryTests(unittest.TestCase):
    def test_library_has_thousands_of_distinct_angles(self):
        self.assertGreater(len(cam.LIBRARY), 2000)
        self.assertEqual(len({a["text"] for a in cam.LIBRARY}), len(cam.LIBRARY))
        self.assertFalse([a for a in cam.LIBRARY if "macro" in a["text"] and a["group"] != "close"])

    def test_three_angles_per_scene_wide_to_close_matching_the_action(self):
        chase, fight, rest = cam.plans(["Sư tử đuổi linh dương chạy", "Hai con hổ đánh nhau cắn xé", "Bò xạ gặm cỏ nghỉ"])
        self.assertEqual([a["group"] for a in chase], ["wide", "medium", "close"])
        self.assertEqual([a["group"] for a in fight], ["medium", "close", "close"])
        self.assertTrue(all("chase" in a["kinds"] for a in chase))
        self.assertTrue(all("fight" in a["kinds"] for a in fight))
        self.assertFalse(any("dutch tilt" in a["text"] for a in rest))
        self.assertEqual(cam.plan_text(chase).count("s: "), 3)
        self.assertTrue(cam.plan_text(chase).startswith("0–3s: ") and "3–7s: " in cam.plan_text(chase)
                        and "7–10s: " in cam.plan_text(chase))

    def test_same_input_same_plan_and_no_repeat_from_previous_scene(self):
        texts = ["Sư tử đuổi linh dương"] * 6
        first, second = cam.plans(texts), cam.plans(texts)
        self.assertEqual([[a["text"] for a in p] for p in first], [[a["text"] for a in p] for p in second])
        for before, after in zip(first, first[1:]):
            self.assertFalse({a["text"] for a in before} & {a["text"] for a in after})

    def test_custom_angles_join_the_library(self):
        extra = cam.custom_angles("low angle tracking shot through tall grass\nextreme close-up of the eye\n\n")
        self.assertEqual([a["group"] for a in extra], ["medium", "close"])

    def test_camera_field_is_replaced_with_the_plan(self):
        angles = cam.plans(["đuổi"])[0]
        prompt = "SETTING: tundra; CHARACTERS: muskox; CAMERA: wide shot; ACTION: charges; ENDING FRAME: snow."
        out = cam.apply(prompt, angles)
        self.assertIn("CAMERA: " + cam.plan_text(angles) + "; ACTION: charges", out)
        self.assertIn("CAMERA: ", cam.apply("A wolf runs", angles))
        self.assertEqual(content.prompt_violations(out.replace("wide shot", "")), [])

    def test_english_run_applies_three_library_angles_and_review_keeps_them(self):
        from runner import gemini_web
        line = ("SETTING: Tundra at dawn; CHARACTERS: two muskoxen; CAMERA: medium shot; CHARACTER BEHAVIOR: tense; "
                "ACTION: they charge and clash; ENDING FRAME: horns locked")

        class Session:
            answers = [f"SCENE 1: {line}\nSCENE 2: {line}\n{content.END_EN}"]
            def new_chat(self): pass
            def wait_ready(self, status, stop, email=""): pass
            def choose_model(self, name): return name
            def ask(self, text, status, stop, email="", label=""):
                return self.answers.pop(0)

        result = gemini_web.run_english(Session(), ["Bối cảnh 1: hai con bò xạ húc nhau", "Bối cảnh 2: bò xạ đánh nhau"],
                                        lambda _: None, threading.Event(), seconds=10, library=True)
        for prompt in result["english"]:
            camera = cam.CAMERA_FIELD.search(prompt).group(2)
            self.assertEqual(len(content.SCENE_SEGMENT.findall(camera)), 3, prompt)

        changed = result["english"][0].replace("CAMERA: ", "CAMERA: random handheld shot; ")

        class Reviewer:
            answers = [f"1. {changed}\n{content.END_EN}"]
            def new_chat(self): pass
            def wait_ready(self, status, stop, email=""): pass
            def choose_model(self, name): return name
            def ask(self, text, status, stop, email="", label=""):
                return self.answers.pop(0) if self.answers else ""

        reviewed = gemini_web.run_review(Reviewer(), [result["english"][0]], "", "no music", lambda _: None, threading.Event())
        self.assertNotIn("random handheld", reviewed["english"][0])
        self.assertIn(cam.CAMERA_FIELD.search(result["english"][0]).group(2).strip(), reviewed["english"][0])


if __name__ == "__main__":
    unittest.main()
