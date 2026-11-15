import unittest

from runner.muse import reply_problem


class MuseServerErrorTests(unittest.TestCase):
    def test_internal_error_is_server_not_refusal(self):
        text = ("Không phải do nội dung prompt của bạn bị chặn đâu. Lỗi mình nhận được là lỗi bất ngờ từ phía máy chủ "
                "tạo video, kiểu lỗi nội bộ, nên không có file nào được tạo ra. Mình không tạo được video lúc này.")
        self.assertEqual(reply_problem(text)[5], "server")
        self.assertEqual(reply_problem("Hệ thống đang gặp trục trặc tạm thời, bạn thử lại sau nhé")[5], "server")

    def test_real_refusal_still_refusal(self):
        self.assertEqual(reply_problem("Mình không thể tạo video này vì nội dung bạo lực.")[5], "refusal")


if __name__ == "__main__":
    unittest.main()
