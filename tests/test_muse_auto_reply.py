import unittest

from runner.muse import AUTO_REPLY, AUTO_REPLY_TAG, needs_answer, reply_problem


class MuseAutoReplyTests(unittest.TestCase):
    def test_questions_get_answered(self):
        for text in ("Bạn muốn video dài bao lâu, 5 hay 10 giây?",
                     "Mình có thể tạo video này. Bạn có muốn thêm nhạc nền không?",
                     "Would you like me to make it in 16:9?", "Vui lòng xác nhận phong cách bạn thích"):
            self.assertTrue(needs_answer(text), text)

    def test_refusal_quota_and_normal_replies_are_not_answered(self):
        for text in ("Mình không thể tạo video này vì vi phạm chính sách. Bạn muốn thử nội dung khác?",  # từ chối: không ép
                     "Bạn đã hết lượt tạo video hôm nay. Bạn có muốn nâng cấp?",                       # hết hạn mức
                     "Đây là video của bạn.", "", "ok"):
            self.assertFalse(needs_answer(text), text)

    def test_only_text_after_own_reply_counts(self):
        self.assertFalse(needs_answer("Bạn muốn 16:9 hay 9:16? " + AUTO_REPLY + " Đang tạo video cho bạn."))
        self.assertTrue(needs_answer("Bạn muốn 16:9? " + AUTO_REPLY + " Bạn có muốn thêm hiệu ứng chậm không?"))

    def test_own_reply_never_looks_like_an_error(self):
        self.assertIsNone(reply_problem(AUTO_REPLY))
        self.assertTrue(AUTO_REPLY.startswith(AUTO_REPLY_TAG))


if __name__ == "__main__":
    unittest.main()
