"""Luồng thứ 2, 3 của cùng tài khoản mở Đoạn chat phụ của Muse để chạy song song với Đoạn chat chính."""
import unittest

from runner.muse import MuseBrowser


class FakeDriver:
    def __init__(self, side=True):
        self.side, self.visited, self.scripts = side, [], []

    def get(self, url):
        self.visited.append(url)

    def execute_script(self, script):
        self.scripts.append(script)
        if script is MuseBrowser.JS_SIDE_CHAT:
            return "row" if self.side else ""
        if script is MuseBrowser.JS_NEW_CHAT:
            return True
        return 0


def browser(side_chat, has_button=True):
    b = MuseBrowser.__new__(MuseBrowser)
    b.driver = FakeDriver(has_button)
    b.phases = []
    b.phase = b.phases.append
    b.composer = lambda: object()
    b.turn_count = lambda: 0
    b.named = lambda selector, names: None
    b.side_chat = side_chat
    return b


class SideChatTests(unittest.TestCase):
    def test_extra_thread_opens_a_side_chat(self):
        b = browser(True)
        b.new_chat()
        self.assertIn(MuseBrowser.JS_SIDE_CHAT, b.driver.scripts)
        self.assertIn("Đoạn chat phụ", b.phases[-1])

    def test_main_thread_keeps_the_main_chat(self):
        b = browser(False)
        b.new_chat()
        self.assertNotIn(MuseBrowser.JS_SIDE_CHAT, b.driver.scripts)

    def test_falls_back_to_main_chat_without_the_button(self):
        b = browser(True, has_button=False)
        b.new_chat()
        self.assertIn("dùng Đoạn chat chính", b.phases[-1])
        self.assertEqual(b.driver.visited, ["https://muse.ai", "https://muse.ai"])


if __name__ == "__main__":
    unittest.main()
