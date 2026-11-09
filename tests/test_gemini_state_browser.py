"""Integration: nhận diện trạng thái trang Gemini (giao diện có khung "đang suy nghĩ") trên Chromium thật.
Bỏ qua nếu máy không có Playwright + Chromium."""
import glob
import unittest

from runner.gemini_web import JS_STATE

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
CHROME = (sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")) or [None])[-1]

THINKING = """<model-response><div class="response-container-content has-thoughts"><div class="response-content">
<thinking-overlay><thinking-dots-animation><div class="thinking-dots-animation"><svg width="28" height="28"></svg></div>
</thinking-dots-animation><div class="thinking-container"><div data-test-id="thinking-overlay-content">
<span>Describing more Vietnamese video scenes</span></div></div></thinking-overlay>
<model-response-content><structured-content-container class="model-response-text has-thoughts processing-state-visible">
<message-content><div class="markdown" aria-busy="true"></div></message-content></structured-content-container>
</model-response-content></div></div></model-response>"""
DONE_WITH_LEFTOVER_BUSY = """<model-response><model-response-content><structured-content-container class="model-response-text has-thoughts">
<message-content><div class="markdown" aria-busy="true"><p>1. Setting: Snowy cliff; Camera: wide</p></div></message-content>
</structured-content-container></model-response-content><div class="response-footer"><button aria-label="Sao chép">c</button></div>
</model-response>"""
STREAMING = """<model-response><message-content><div class="markdown" aria-busy="true"><p>1. Setting: Snow</p></div>
</message-content></model-response><button aria-label="Dừng tạo câu trả lời">stop</button>"""


@unittest.skipUnless(sync_playwright and CHROME, "Cần Playwright + Chromium")
class GeminiStateTests(unittest.TestCase):
    def state(self, html):
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, args=["--headless", "--no-sandbox"])
            try:
                page = browser.new_page()
                page.set_content("<html><body>" + html + "</body></html>")
                return page.evaluate("() => {" + JS_STATE + "}")
            finally:
                browser.close()

    def test_thinking_is_busy_and_reports_what_gemini_is_doing(self):
        state = self.state(THINKING)
        self.assertEqual((state["busy"], state["done"], state["text"]), (True, False, ""))
        self.assertEqual(state["thinking"], "Describing more Vietnamese video scenes")

    def test_finished_answer_is_not_held_by_leftover_busy_marker(self):
        state = self.state(DONE_WITH_LEFTOVER_BUSY)
        self.assertEqual((state["busy"], state["done"]), (False, True))
        self.assertEqual(state["text"], "1. Setting: Snowy cliff; Camera: wide")

    def test_streaming_with_stop_button_stays_busy(self):
        state = self.state(STREAMING)
        self.assertEqual((state["busy"], state["done"]), (True, False))


if __name__ == "__main__":
    unittest.main()
