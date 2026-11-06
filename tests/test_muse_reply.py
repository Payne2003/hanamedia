"""Muse trả lời bằng chữ thay vì tạo video: báo ngay (không chờ 20 phút), không tự gửi lại."""
import unittest

from runner.muse import reply_problem


class ReplyProblemTests(unittest.TestCase):
    def test_region_block_pauses_account(self):
        text = ("Mình không tạo được video này cho bạn, Isabella. Tính năng tạo video hiện không khả dụng với tài khoản "
                "ở quốc gia/khu vực của bạn, nên thử lại cũng sẽ không được.")
        message, state = reply_problem(text)
        self.assertEqual(state, "attention")
        self.assertIn("quốc gia/khu vực", message)
        message, state = reply_problem("Video generation isn't available in your country yet.")
        self.assertEqual(state, "attention")

    def test_content_refusal_fails_job_only(self):
        message, state = reply_problem("Sorry, I can't create this video because it shows a real person.")
        self.assertEqual(state, "ready")
        self.assertIn("không tự gửi lại", message)

    def test_normal_progress_is_ignored(self):
        for text in ("", "Đang tạo video của bạn…", "Here is your video!", "Mình có thể làm ảnh 16:9"):
            self.assertIsNone(reply_problem(text))


if __name__ == "__main__":
    unittest.main()
