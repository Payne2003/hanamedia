"""Muse gom mọi prompt của một tài khoản vào MỘT cuộc trò chuyện. Khi hai tab gửi gần nhau, câu trả lời có thể xen kẽ:
video phải được nhận theo mã tác vụ Muse nhắc lại trong lượt trả lời, không theo vị trí. Chạy trên Chromium thật."""
import glob
import unittest

from runner.muse import MuseBrowser

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
CHROME = (sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")) or [None])[-1]


def turn(role, turn_id, html):
    return f'<div data-message-role="{role}" data-message-turn-id="{turn_id}">{html}</div>'


INTERLEAVED = '<div role="log">' + "".join([
    turn("user", "u1", "<p>Tạo video A</p><p>Mã tác vụ: AAA</p>"),
    turn("user", "u2", "<p>Tạo video B</p><p>Mã tác vụ: BBB</p>"),
    turn("assistant", "a2", '<p>Video B xong</p><div id="vb"><video></video></div>'),
    turn("assistant", "a2", "<p>Mã tác vụ: BBB</p>"),
    turn("assistant", "a1", '<p>Video A xong</p><div id="va"><video></video></div>'),
    turn("assistant", "a1", "<p>Mã tác vụ: AAA</p>"),
]) + "</div>"
OLD_STYLE = '<div role="log">' + "".join([  # Muse không nhắc lại mã: dùng vị trí như trước
    turn("user", "u1", "<p>Mã tác vụ: AAA</p>"),
    turn("assistant", "a1", '<div id="va"><video></video></div>'),
    turn("user", "u2", "<p>Mã tác vụ: BBB</p>"),
    turn("assistant", "a2", '<div id="vb"><video></video></div>'),
]) + "</div>"

PROMPT_ONLY = '<div role="log">' + "".join([  # bản mới: tin nhắn chỉ có prompt, không mã tác vụ
    turn("user", "u1", "<p>Generate a 3-second video of a snow leopard walking along a ledge.</p>"),
    turn("assistant", "a1", "<p>Đang tạo video…</p>"),
    turn("assistant", "a1b", '<div id="va"><video></video></div>'),
    turn("user", "u2", "<p>Generate a 2-second video of a goat jumping.</p>"),
    turn("assistant", "a2", '<div id="vb"><video></video></div>'),
]) + "</div>"


@unittest.skipUnless(sync_playwright and CHROME, "Cần Playwright + Chromium")
class SegmentTests(unittest.TestCase):
    def belongs(self, html, card, marker):
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, args=["--headless", "--no-sandbox"])
            try:
                page = browser.new_page()
                page.set_content(html)
                return page.evaluate("([card, marker]) => (function(){" + MuseBrowser.JS_IN_SEGMENT.replace(
                    "const node=arguments[0], marker=arguments[1];", "const node=document.getElementById(card);")
                    + "})()", [card, marker])
            finally:
                browser.close()

    def test_interleaved_replies_follow_the_echoed_task_code(self):
        self.assertTrue(self.belongs(INTERLEAVED, "va", "Mã tác vụ: AAA"))
        self.assertFalse(self.belongs(INTERLEAVED, "vb", "Mã tác vụ: AAA"))
        self.assertTrue(self.belongs(INTERLEAVED, "vb", "Mã tác vụ: BBB"))

    def test_without_echo_position_rule_still_applies(self):
        self.assertTrue(self.belongs(OLD_STYLE, "va", "Mã tác vụ: AAA"))
        self.assertFalse(self.belongs(OLD_STYLE, "vb", "Mã tác vụ: AAA"))

    def test_prompt_marker_finds_the_video_of_that_prompt(self):
        from runner.muse import prompt_marker
        leopard = prompt_marker("Generate a 3-second video of a snow leopard walking along a ledge.")
        goat = prompt_marker("Generate a 2-second   video of a goat jumping.")
        self.assertTrue(self.belongs(PROMPT_ONLY, "va", leopard))  # bỏ qua lượt chữ "Đang tạo video…" ở giữa
        self.assertFalse(self.belongs(PROMPT_ONLY, "vb", leopard))  # video sau prompt kế tiếp không phải của nó
        self.assertTrue(self.belongs(PROMPT_ONLY, "vb", goat))
        self.assertFalse(self.belongs(PROMPT_ONLY, "va", goat))


if __name__ == "__main__":
    unittest.main()
