import os
import time
from pathlib import Path


FLOW_URL = "https://flow.google.com"  # địa chỉ cũ labs.google/fx/tools/flow chuyển về đây

# Màn hình đầu của Flow là danh sách dự án: tìm nút "Dự án mới" / "New project" (thẻ có biểu tượng +).
JS_NEW_PROJECT = r"""
const clean = t => (t || '').replace(/\s+/g, ' ').trim();
const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
const wanted = /(^|\s)(dự án mới|new project|tạo dự án|create (a )?project)$/i;
const hits = [...document.querySelectorAll('button,[role="button"],a,div,span')]
  .filter(e => visible(e) && clean(e.innerText).length < 40 && wanted.test(clean(e.innerText)));
if (!hits.length) return '';
const target = hits.map(e => e.closest('button,[role="button"],a') || e)
  .sort((a, b) => a.getBoundingClientRect().width * a.getBoundingClientRect().height - b.getBoundingClientRect().width * b.getBoundingClientRect().height)[0];
target.scrollIntoView({block: 'center'});
target.click();
return clean(target.innerText) || 'clicked';
"""
JS_FLOW_PAGE = r"""
const text = ((document.body && document.body.innerText) || '').slice(0, 2000);
const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
const signIn = /accounts\.google\./.test(location.hostname) ||
  [...document.querySelectorAll('a,button')].some(e => visible(e) && /^(đăng nhập|sign in)$/i.test((e.innerText || '').trim()));
return {loading: /đang tải|loading/i.test(text) && text.length < 400, signIn, host: location.hostname};
"""
JS_DESCRIBE = r"""
const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
const items = [...document.querySelectorAll('button,[role="button"],textarea,input,[contenteditable="true"],[role="textbox"]')]
  .filter(visible).slice(0, 120).map(e => `${e.tagName.toLowerCase()} role=${e.getAttribute('role') || ''} ` +
    `aria-label=${e.getAttribute('aria-label') || ''} placeholder=${e.getAttribute('placeholder') || ''} text=${(e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60)}`);
return location.href + '\n' + items.join('\n');
"""


def default_chrome_profile(data):
    """Hồ sơ Chrome RIÊNG của tool cho Google Flow (data/profiles/google-flow), giống Gemini/ChatGPT: máy nào cũng dùng được,
    không phụ thuộc hồ sơ Chrome cá nhân. Đăng nhập một lần bằng nút trong Cài đặt.
    HUMY_FLOW_PROFILE_PATH: (nâng cao) dùng một hồ sơ Chrome khác."""
    configured = os.environ.get("HUMY_FLOW_PROFILE_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path(data) / "profiles" / "google-flow" / "Default"


def chrome_profile_arguments(profile):
    profile = Path(profile).expanduser().resolve()
    return f"--user-data-dir={profile.parent}", f"--profile-directory={profile.name}"


class FlowImageSession:
    def __init__(self, profile, downloads):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service

        profile = Path(profile).expanduser().resolve()
        downloads = Path(downloads).resolve()
        self.debug_path = downloads.parent / "profiles" / "flow-debug.txt"
        profile.mkdir(parents=True, exist_ok=True)
        downloads.mkdir(parents=True, exist_ok=True)
        options = webdriver.ChromeOptions()
        for argument in chrome_profile_arguments(profile):
            options.add_argument(argument)
        options.add_argument("--lang=vi-VN")
        options.add_argument("--window-size=1440,1000")
        options.add_experimental_option("prefs", {
            "intl.accept_languages": "vi-VN,vi,en",
            "download.default_directory": str(downloads),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
        })
        driver = None
        try:
            driver = webdriver.Chrome(options=options, service=Service(log_output=os.devnull))
            self.driver = driver
            self.driver.set_page_load_timeout(60)
            self.driver.get(FLOW_URL)
            self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(downloads),
            })
        except Exception as error:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            raise RuntimeError(
                f"Không mở được Flow bằng Chrome profile '{profile.name}'. "
                "Đóng mọi cửa sổ Chrome đang dùng profile này rồi thử lại. "
                f"Chi tiết: {error}"
            ) from error

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def prompt_box(self):
        for element in self.driver.find_elements("css selector", "textarea,input,[role='textbox'],[contenteditable='true']"):
            try:
                if not element.is_displayed() or not element.is_enabled():
                    continue
                label = " ".join((
                    element.get_attribute("aria-label") or "",
                    element.get_attribute("placeholder") or "",
                    element.get_attribute("data-placeholder") or "",
                )).lower()
                if any(word in label for word in ("tạo những gì", "tạo gì", "bạn muốn", "mô tả", "create", "prompt", "describe", "imagine")):
                    return element
            except Exception:
                continue
        return None

    def prompt(self, text, timeout=120):
        """Điền prompt ảnh vào Flow. Đang ở màn hình danh sách dự án thì tự bấm "Dự án mới" rồi mới điền."""
        from selenium.webdriver.common.keys import Keys

        deadline = time.monotonic() + timeout
        clicks, last_click = 0, 0.0
        while time.monotonic() < deadline:
            box = self.prompt_box()
            if box:
                try:
                    box.click()
                    box.send_keys(Keys.CONTROL, "a")
                    box.send_keys(text)
                    return
                except Exception:
                    pass
            try:
                page = self.driver.execute_script(JS_FLOW_PAGE) or {}
            except Exception:
                page = {}
            if page.get("signIn"):
                raise RuntimeError("Flow chưa đăng nhập trong hồ sơ của tool. Vào Cài đặt → Google Flow → Đăng nhập, "
                                   "đăng nhập xong thì đóng hẳn cửa sổ đó rồi thử lại.")
            # Chưa có ô prompt và trang đã tải xong: bấm "Dự án mới" (tối đa 3 lần, cách nhau 10 giây).
            if not page.get("loading") and clicks < 3 and time.monotonic() - last_click > 10:
                try:
                    if self.driver.execute_script(JS_NEW_PROJECT):
                        clicks, last_click = clicks + 1, time.monotonic()
                except Exception:
                    pass
            time.sleep(1)
        self.dump("Không tìm thấy ô prompt" + (" sau khi bấm Dự án mới" if clicks else " và không thấy nút Dự án mới"))
        raise RuntimeError("Flow chưa hiện ô prompt" + (" sau khi bấm Dự án mới" if clicks else "")
                           + ". Kiểm tra cửa sổ Flow (đăng nhập, mở dự án) rồi thử lại. File chẩn đoán: profiles/flow-debug.txt")

    def dump(self, reason):
        try:
            path = self.debug_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"LÝ DO: {reason}\n{self.driver.execute_script(JS_DESCRIBE)}", encoding="utf-8")
        except Exception:
            pass
