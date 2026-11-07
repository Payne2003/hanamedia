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

    def test_region_block_other_wording(self):
        text = ("Mình không thể tiếp tục tạo video này được, James. Lần thử vừa rồi hệ thống đã xác nhận tính năng tạo "
                "video không khả dụng cho tài khoản của bạn tại quốc gia/khu vực hiện tại. Đây là hạn chế lâu dài, nên thử "
                "lại bây giờ cũng sẽ cho kết quả tương tự và không tạo ra được file video để tải xuống.")
        self.assertEqual(reply_problem(text)[1], "attention")
        self.assertEqual(reply_problem("Video creation is unavailable in your region.")[1], "attention")

    def test_content_refusal_fails_job_only(self):
        message, state = reply_problem("Sorry, I can't create this video because it shows a real person.")
        self.assertEqual(state, "ready")
        self.assertIn("không tự gửi lại", message)

    def test_normal_progress_is_ignored(self):
        for text in ("", "Đang tạo video của bạn…", "Here is your video!", "Mình có thể làm ảnh 16:9",
                     "Video bối cảnh khu vực núi Himalaya đã sẵn sàng"):
            self.assertIsNone(reply_problem(text))


if __name__ == "__main__":
    unittest.main()


class CardKeyTests(unittest.TestCase):
    """Muse đổi giao diện: khung video không còn nút có tên file thì nhận diện bằng địa chỉ video."""

    class El:
        def __init__(self, tag="div", attrs=None, children=None):
            self.tag_name, self.attrs, self.children = tag, attrs or {}, children or {}

        def get_attribute(self, name):
            return self.attrs.get(name)

        def find_elements(self, by, selector):
            return self.children.get(selector, [])

    def test_old_and_new_layout(self):
        from runner.muse import MuseBrowser
        old = self.El(children={"[role='button'][aria-label]": [self.El(attrs={"aria-label": "Tải muse_123.mp4"})]})
        self.assertEqual(MuseBrowser.card_key(old), "Tải muse_123.mp4")
        video = self.El("video", {"currentSrc": "https://cdn.muse.ai/v/abc.mp4?sig=1"})
        self.assertEqual(MuseBrowser.card_key(video), "https://cdn.muse.ai/v/abc.mp4?sig=1")
        nested = self.El(children={"video": [self.El("video", children={"source[src]": [self.El(attrs={"src": "blob:x"})]})]})
        self.assertEqual(MuseBrowser.card_key(nested), "blob:x")
        self.assertEqual(MuseBrowser.card_key(self.El()), "")
