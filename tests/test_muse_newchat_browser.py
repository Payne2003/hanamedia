"""Muse mở lại cuộc trò chuyện cũ khi vào muse.ai: tool bấm nút "Mới" để có cuộc trò chuyện trống. Chạy trên Chromium thật."""
import glob
import unittest

from runner.muse import MuseBrowser

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
CHROME = (sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")) or [None])[-1]
PAGE = """<header><button id="side">Đoạn chat</button><button id="new" onclick="document.querySelector('[role=log]').innerHTML=''">
<svg></svg> Mới</button></header><div role="log"><div data-message-role="user" data-message-turn-id="u1"><p>Cũ</p></div></div>"""


@unittest.skipUnless(sync_playwright and CHROME, "Cần Playwright + Chromium")
class NewChatTests(unittest.TestCase):
    def test_clicks_the_new_chat_button(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, args=["--headless", "--no-sandbox"])
            try:
                page = browser.new_page()
                page.set_content(PAGE)
                self.assertTrue(page.evaluate("() => {" + MuseBrowser.JS_NEW_CHAT + "}"))
                self.assertEqual(page.evaluate("document.querySelectorAll('[data-message-turn-id]').length"), 0)
                page.set_content("<div role='log'></div><button>Đoạn chat</button>")
                self.assertFalse(page.evaluate("() => {" + MuseBrowser.JS_NEW_CHAT + "}"))  # không có nút: không bấm bừa
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
