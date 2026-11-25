from pathlib import Path
import tempfile
import unittest
import uuid

from runner.store import Store

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


class PromptStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "t.db")
        self.store.add_account("a@tuan89.site", "", 10)
        self.account = self.store.snapshot()["accounts"][0]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def job(self, prompt="A wide shot of a cliff."):
        self.store.enqueue([prompt], 1, uuid.uuid4().hex)
        return next(j for j in self.store.snapshot()["jobs"] if j["prompt"] == prompt)

    def test_scene_prompt_fills_detail_fields(self):
        job = self.job("Bối cảnh 1: Vách đá cao, góc quay: Cận cảnh, theo chân, hành vi nhân vật: Báo tuyết leo, "
                       "Nội dung hành động nhân vật: Báo tuyết trượt nhẹ rồi dừng lại;")
        detail = self.store.job_detail(job["id"])
        self.assertEqual({k: v for k, v in detail["details"].items() if v},
                         {"setting": "Vách đá cao", "camera": "Cận cảnh, theo chân",
                          "behavior": "Báo tuyết leo", "action": "Báo tuyết trượt nhẹ rồi dừng lại"})
        self.assertTrue(detail["editable"])

    def test_update_saves_prompt_details_images_type_and_account(self):
        job = self.job()
        token = self.store.add_image(PNG)["token"]
        saved = self.store.update_job(job["id"], "  New English prompt.  ",
                                      {"setting": "Chân vách đá", "camera": "Tracking", "behavior": "Leo", "action": "Trượt"},
                                      [token], "image", self.account)
        self.assertEqual((saved["prompt"], saved["generation_mode"], saved["images"], saved["assigned_account_id"]),
                         ("New English prompt.", "image", [token], self.account))
        self.assertEqual(saved["details"]["camera"], "Tracking")
        with self.assertRaisesRegex(ValueError, "ít nhất một ảnh"):
            self.store.update_job(job["id"], "x", None, [], "image")
        with self.assertRaisesRegex(ValueError, "trống"):
            self.store.update_job(job["id"], "  ", None, [], "text")

    def test_running_job_cannot_be_edited_or_rerun(self):
        job = self.job()
        self.store.claim(2)
        self.assertFalse(self.store.job_detail(job["id"])["editable"])
        with self.assertRaisesRegex(ValueError, "đang chạy"):
            self.store.update_job(job["id"], "Changed", None, [], "text")
        with self.assertRaisesRegex(ValueError, "đang chạy"):
            self.store.rerun(job["id"])

    def test_rerun_done_and_error_jobs(self):
        job = self.job()
        claimed, _ = self.store.claim(2)
        self.store.finish(claimed["id"], output="/tmp/x.mp4")
        self.store.rerun(job["id"])
        detail = self.store.job_detail(job["id"])
        self.assertEqual((detail["status"], detail["output"]), ("waiting", None))
        with self.assertRaisesRegex(ValueError, "đang chờ"):
            self.store.rerun(job["id"])
        claimed, _ = self.store.claim(2)
        self.store.submitting(claimed["id"])
        self.store.finish(claimed["id"], error="Muse lỗi")
        with self.assertRaisesRegex(ValueError, "đã được gửi"):
            self.store.rerun(job["id"])
        self.store.rerun(job["id"], confirmed=True)
        self.assertEqual(self.store.job_detail(job["id"])["status"], "waiting")


if __name__ == "__main__":
    unittest.main()


class EnglishSourceFieldsTests(unittest.TestCase):
    def test_english_prompts_keep_vietnamese_scene_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.db")
            sources = ["Bối cảnh 1: Sườn núi đá vôi, góc quay: Cận cảnh ngang tầm mắt, hành vi nhân vật: Dê mẹ bình thản, "
                       "Nội dung hành động nhân vật: Dê mẹ nhai cỏ;", "Cảnh tự do không theo mẫu", ""]
            store.enqueue(["Ultra-photorealistic goats grazing, 16:9 aspect ratio.", "A leopard on a ledge.", "A wide cliff."],
                          1, uuid.uuid4().hex, sources=sources)
            jobs = {j["prompt"]: store.job_detail(j["id"]) for j in store.snapshot()["jobs"]}
            first = jobs["Ultra-photorealistic goats grazing, 16:9 aspect ratio."]["details"]
            self.assertEqual({k: v for k, v in first.items() if v},
                             {"setting": "Sườn núi đá vôi", "camera": "Cận cảnh ngang tầm mắt",
                              "behavior": "Dê mẹ bình thản", "action": "Dê mẹ nhai cỏ"})
            self.assertEqual(jobs["A leopard on a ledge."]["details"]["setting"], "Cảnh tự do không theo mẫu")
            self.assertFalse(any(jobs["A wide cliff."]["details"].values()))
            with self.assertRaises(ValueError):
                store.enqueue(["x"], 1, uuid.uuid4().hex, sources=["a", "b"])

    def test_run_english_returns_split_source_lines(self):
        import threading
        from runner import content, gemini_web

        class Session:
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return ""
            def ask(self, text, status, stop, email="", label=""):
                return "1. A long English prompt for scene one.\n2. A long English prompt for scene two.\n" + content.END_EN

        lines = ["Bối cảnh 1: A", "Bối cảnh 2: B"]
        result = gemini_web.run_english(Session(), lines, lambda _: None, threading.Event())
        self.assertEqual(result["lines"], lines)


class PromptFieldsTests(unittest.TestCase):
    PROMPT = ("SCENE 83 | Duration 8 seconds | ENVIRONMENT: sausage tree at the riverbank | CHARACTERS: exactly 3 animals | "
              "ACTION: the leopards wake | CAMERA: 0-4.9s low angle | AUDIO: dry grass rustling | "
              "CONTINUITY: opens on the hatchling; hands off to scene 84 | RENDERING RULES: real-time, no music, 16:9, 4K")

    def setUp(self):
        import tempfile
        import uuid
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "s.db")
        self.store.add_account("a@tuan89.site", "", 100)
        self.store.enqueue([self.PROMPT], 1, uuid.uuid4().hex)
        self.job = self.store.snapshot()["jobs"][0]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_every_labelled_field_is_split_out_of_the_prompt(self):
        d = self.store.job_detail(self.job)["details"]
        self.assertEqual(d["setting"], "sausage tree at the riverbank")  # ENVIRONMENT → setting
        self.assertEqual((d["characters"], d["action"], d["camera"]), ("exactly 3 animals", "the leopards wake", "0-4.9s low angle"))
        self.assertEqual((d["audio"], d["continuity"]), ("dry grass rustling", "opens on the hatchling; hands off to scene 84"))
        self.assertTrue(d["rendering"].startswith("real-time"))
        self.assertEqual(d["ending"], "")  # prompt không có khung cuối

    def test_editing_a_field_assigns_it_back_into_the_prompt(self):
        details = self.store.job_detail(self.job)["details"]
        details.update(audio="wind only, no rustling", ending="leopards at the foot of the tree")
        job = self.store.update_job(self.job, self.PROMPT, details)
        self.assertIn("AUDIO: wind only, no rustling | CONTINUITY:", job["prompt"])  # đúng chỗ, giữ nhãn và dấu |
        self.assertTrue(job["prompt"].endswith("| ENDING FRAME: leopards at the foot of the tree"))  # trường mới thêm cuối
        self.assertIn("ENVIRONMENT: sausage tree", job["prompt"])  # trường khác giữ nguyên
        self.assertEqual(job["details"]["audio"], "wind only, no rustling")

    def test_editing_the_prompt_text_wins_over_fields(self):
        details = self.store.job_detail(self.job)["details"]
        details["audio"] = "ignored"
        job = self.store.update_job(self.job, self.PROMPT.replace("wake", "sleep"), details)
        self.assertIn("the leopards sleep", job["prompt"])
        self.assertIn("AUDIO: dry grass rustling", job["prompt"])  # sửa chữ prompt thì ô trường không đè lên
