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
        self.assertEqual(detail["details"], {"setting": "Vách đá cao", "camera": "Cận cảnh, theo chân",
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
