import os
import time
from pathlib import Path


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
            self.driver.get("https://labs.google/fx/tools/flow")
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

    def prompt(self, text, timeout=120):
        from selenium.webdriver.common.keys import Keys

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            candidates = self.driver.find_elements(
                "css selector", "textarea,input,[role='textbox'],[contenteditable='true']")
            for element in candidates:
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    label = " ".join((
                        element.get_attribute("aria-label") or "",
                        element.get_attribute("placeholder") or "",
                        element.get_attribute("data-placeholder") or "",
                    )).lower()
                    if "tạo những gì" not in label and "create" not in label and "prompt" not in label:
                        continue
                    element.click()
                    element.send_keys(Keys.CONTROL, "a")
                    element.send_keys(text)
                    return
                except Exception:
                    continue
            time.sleep(1)
        raise RuntimeError("Flow chưa hiện ô prompt. Đăng nhập Google trong cửa sổ Flow rồi bấm Tạo ảnh lại.")
