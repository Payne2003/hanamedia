"""Integration: Account Health Monitor trên Chromium thật (bỏ qua nếu máy không có Playwright + Chromium).

Chromium mở đúng https://muse.ai; nội dung trang được thay bằng trang mô phỏng (đã đăng nhập, trang đăng nhập, 429,
CAPTCHA, trang tải chậm) hoặc bị cắt mạng. Chạy đúng HealthMonitor.check -> MuseBrowser.check_session -> SQLite.
"""
import glob
import re
import tempfile
import unittest
from pathlib import Path

from runner.engine import Engine
from runner.health import HealthMonitor
from runner.muse import MuseBrowser
from runner.store import Store

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
CHROME = (sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")) or [None])[-1]
MUSE = re.compile(r"^https://muse\.ai(/.*)?$")
PAGES = {
    "live": '<textarea aria-label="Nhắn tin"></textarea>',
    "login": '<input aria-label="Số di động hoặc email"><button>Tiếp tục</button>',
    "expired": '<input aria-label="Số di động hoặc email"><button>Tiếp tục</button>',
    "rate": '<div role="alert">Too Many Requests</div>',
    "captcha": '<div role="alert">Verify you are human</div>',
    "slow": '<div id="app">…</div><script>setTimeout(()=>{document.getElementById("app").innerHTML='
            '\'<textarea aria-label="Nhắn tin"></textarea>\'},2000)</script>',
}


class El:
    def __init__(self, handle):
        self.h = handle

    def is_displayed(self):
        return self.h.is_visible()

    @property
    def accessible_name(self):
        return self.h.evaluate("e => e.getAttribute('aria-label') || e.innerText || ''")

    @property
    def text(self):
        return self.h.inner_text()


class Driver:
    def __init__(self, page):
        self.page = page

    def get(self, url):
        self.page.goto(url, timeout=15000)

    @property
    def current_url(self):
        return self.page.url

    def find_elements(self, by, selector):
        return [El(h) for h in self.page.query_selector_all(selector)]


@unittest.skipUnless(sync_playwright and CHROME, "cần Playwright và Chromium")
class HealthBrowserTests(unittest.TestCase):
    def test_real_chromium_pages(self):
        root = Path(tempfile.mkdtemp())
        store = Store(root / "t.db")
        names = ["live", "login", "expired", "rate", "captcha", "slow", "offline"]
        store.add_accounts("\n".join(f"{n}@tuan89.site" for n in names), 10)
        ids = {a["email"].split("@")[0]: a["id"] for a in store.snapshot()["accounts"]}
        for name in ("expired", "offline"):
            store.set_account(ids[name], "ready")  # đã từng LIVE
        engine = Engine(store, root)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=CHROME)

                def factory(profile, downloads):
                    name = next(n for n, i in ids.items() if i == Path(profile).name)
                    page = browser.new_page()
                    if name == "offline":
                        page.route(MUSE, lambda route, *_: route.abort("internetdisconnected"))
                    else:
                        page.route(MUSE, lambda route, *_, body=PAGES[name], name=name: route.fulfill(
                            status=429 if name == "rate" else 200, content_type="text/html; charset=utf-8",
                            body=f"<!doctype html><meta charset=utf-8><title>Muse</title>{body}"))
                    muse = MuseBrowser.__new__(MuseBrowser)
                    muse.driver = Driver(page)
                    muse.close = page.close
                    return muse

                monitor = HealthMonitor(engine, factory, parallel=1, spacing=0)
                results = {name: monitor.check(ids[name]) for name in names}
                browser.close()
        finally:
            engine.shutdown()
        self.assertEqual(results, {"live": "LIVE", "login": "LOGIN_REQUIRED", "expired": "EXPIRED", "rate": "RATE_LIMITED",
                                   "captcha": "LOGIN_REQUIRED", "slow": "LIVE", "offline": "NETWORK_ERROR"})
        states = {name: store.account(ids[name])["state"] for name in names}
        self.assertEqual(states, {"live": "ready", "login": "attention", "expired": "attention", "rate": "new",
                                  "captcha": "attention", "slow": "ready", "offline": "ready"})  # mất mạng: không "chết"


if __name__ == "__main__":
    unittest.main()
