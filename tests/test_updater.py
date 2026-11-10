"""Tự cập nhật: tìm bản mới qua trang github.com/<kho>/releases/latest (không dùng API bị giới hạn), tải bộ cài có
kiểm tra SHA-256, không cài khi đang có tác vụ chạy; chạy bộ cài rồi tắt app; nhật ký từng bước."""
import hashlib
import io
import shutil
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

from runner.updater import Updater, newer, parse_version

REPO = "Owner/app"
BASE = f"https://github.com/{REPO}/releases"
BODY = b"MZ fake installer " * 1000


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.files, self.redirect, self.seen = {}, {}, []
        self.launched, self.exited, self.busy = [], [], False

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def opener(self, url, timeout=20, headers=None):
        self.seen.append(url)
        final = self.redirect.get(url, url)
        if final not in self.files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return Response(self.files[final], final)

    def publish(self, tag="v1.1.60", payload=BODY, sha=True, api_digest=None):
        self.redirect[f"{BASE}/latest"] = f"{BASE}/tag/{tag}"
        self.files[f"{BASE}/tag/{tag}"] = b"<html>"
        name = f"HanaMedia-Setup-{tag.lstrip('v')}.exe"
        self.files[f"{BASE}/download/{tag}/{name}"] = payload
        if sha:
            self.files[f"{BASE}/download/{tag}/{name}.sha256"] = f"{hashlib.sha256(BODY).hexdigest()}  {name}".encode()
        if api_digest:
            self.files[f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"] = (
                '{"assets":[{"name":"%s","digest":"sha256:%s"}]}' % (name, api_digest)).encode()

    def make(self, opener=None, curl=None):
        u = Updater(self.root, busy=lambda: self.busy, exit_app=lambda: self.exited.append(1), repo=REPO,
                    opener=opener or self.opener, launcher=lambda path: self.launched.append(Path(path)),
                    sleep=lambda s: None, curl=curl or self.no_curl)
        u.state["supported"] = True
        u.state["manual"] = False  # luồng Windows: tải bộ cài và tự cài (macOS kiểm tra riêng bên dưới)
        return u

    @staticmethod
    def no_curl(*args, **kwargs):
        raise OSError("Máy không có curl.exe")

    def wait(self, u):
        for _ in range(200):
            if self.exited or u.status()["phase"] == "error":
                break
            time.sleep(0.02)

    def test_versions(self):
        self.assertEqual(parse_version("v1.1.45"), (1, 1, 45))
        self.assertTrue(newer("v1.1.45", "1.0.1"))
        self.assertTrue(newer("1.1.10", "1.1.9"))
        self.assertFalse(newer("1.1", "1.1.0"))
        self.assertFalse(newer("v1.0.1", "1.0.1"))
        self.assertFalse(newer("bad", "1.0.1"))

    def test_check_without_github_api_then_install(self):
        self.publish()
        u = self.make()
        status = u.check()
        self.assertEqual((status["available"], status["latest"], status["error"]), (True, "1.1.60", ""))
        self.assertFalse(any("api.github.com" in url for url in self.seen))  # không đụng API bị giới hạn
        u.start_install()
        self.wait(u)
        self.assertEqual(u.status()["phase"], "installing")
        self.assertEqual(self.launched[0].read_bytes(), BODY)
        self.assertEqual(self.exited, [1])
        u.clean_old()
        self.assertEqual([p.name for p in (self.root / "updates").iterdir()], ["pending.json"])
        again = Updater(self.root, repo=REPO, opener=self.opener)  # mở lại mà vẫn là bản cũ
        self.assertEqual(again.status()["phase"], "error")
        self.assertIn("chưa thành công", again.status()["error"])
        log = (self.root / "logs" / "update.log").read_text(encoding="utf-8")
        for step in ("bản mới nhất v1.1.60", "Đã tải và kiểm tra SHA-256", "Đã chạy bộ cài", "cài không thành công"):
            self.assertIn(step, log)

    def test_old_release_without_sha_file_uses_api_digest(self):
        self.publish(sha=False, api_digest=hashlib.sha256(BODY).hexdigest())
        u = self.make()
        u.check()
        self.assertTrue(u.asset["digest"].startswith("sha256:"))

    def test_tampered_download_is_rejected(self):
        self.publish(payload=b"evil" * 10)
        u = self.make()
        u.check()
        u.start_install()
        self.wait(u)
        self.assertIn("mã kiểm tra", u.status()["error"])
        self.assertEqual(self.launched, [])

    def test_same_version_busy_and_unsupported(self):
        self.publish(tag="v1.0.1")
        self.assertFalse(self.make().check()["available"])
        self.publish()
        u = self.make()
        u.check()
        self.busy = True
        with self.assertRaisesRegex(ValueError, "tác vụ chạy"):
            u.start_install()
        self.busy = False
        u.state["supported"] = False
        with self.assertRaisesRegex(ValueError, "Windows"):
            u.start_install()

    def test_no_release_yet(self):
        self.files[f"{BASE}/latest"] = b"<html>"  # không chuyển tới /tag/
        u = Updater(self.root, repo=REPO, opener=self.opener)
        self.assertIn("Chưa có bản phát hành", u.check()["error"])


    def test_mac_update_opens_download_page_instead_of_installing(self):
        from unittest.mock import patch
        self.publish()
        u = self.make()
        u.state["manual"] = True
        u.check()
        with patch("webbrowser.open") as opened:
            status = u.start_install()
        opened.assert_called_once_with(f"https://github.com/{REPO}/releases/latest")
        self.assertEqual(status["phase"], "idle")
        self.assertEqual(self.launched, [])  # không tải/chạy bộ cài Windows trên Mac

    # ---- VPN / đổi mạng --------------------------------------------------------------------------------------------
    def test_network_drop_during_check_is_retried(self):
        self.publish()
        failures = [2]

        def flaky(url, timeout=20, headers=None):
            if failures[0]:
                failures[0] -= 1
                raise urllib.error.URLError(ConnectionResetError("VPN vừa đổi"))
            return self.opener(url, timeout)
        status = self.make(flaky).check()
        self.assertTrue(status["available"])
        self.assertFalse(status["error"])

    def test_download_resumes_after_connection_drop(self):
        self.publish()
        u = self.make()
        u.check()
        url = u.asset["browser_download_url"]
        calls = []

        def dropping(request_url, timeout=20, headers=None):
            calls.append(headers)
            if request_url != url:
                return self.opener(request_url, timeout)
            if not headers:
                response = Response(BODY[:5000], url)
                response.headers = {"Content-Length": str(len(BODY))}  # mạng rớt giữa chừng
                return response
            start = int(headers["Range"].split("=")[1].rstrip("-"))
            response = Response(BODY[start:], url)
            response.status = 206
            return response
        u.opener = dropping
        installer = u.download()
        self.assertEqual(installer.read_bytes(), BODY)
        self.assertEqual(calls, [None, {"Range": "bytes=5000-"}])

    def test_windows_curl_is_used_when_python_cannot_connect(self):
        self.publish()
        tag, name = "v1.1.60", "HanaMedia-Setup-1.1.60.exe"
        used = []

        def offline(url, timeout=20, headers=None):
            raise urllib.error.URLError(TimeoutError("timed out"))

        def curl(url, target, timeout=60, resume=False):
            used.append(url)
            final = self.redirect.get(url, url)
            Path(target).write_bytes(self.files[final])
            return final
        u = self.make(offline, curl)
        status = u.check()
        self.assertTrue(status["available"], status)
        self.assertTrue(u.asset["digest"].startswith("sha256:"))
        self.assertEqual(u.download().read_bytes(), BODY)
        self.assertIn(f"{BASE}/download/{tag}/{name}", used)
        self.assertIn("curl.exe", (self.root / "logs" / "update.log").read_text(encoding="utf-8"))

    def test_curl_download_with_wrong_hash_is_not_installed(self):
        self.publish(payload=b"MZ tampered")

        def offline(url, timeout=20, headers=None):
            raise urllib.error.URLError(TimeoutError("timed out"))

        def curl(url, target, timeout=60, resume=False):
            final = self.redirect.get(url, url)
            Path(target).write_bytes(self.files[final])
            return final
        u = self.make(offline, curl)
        u.check()
        with self.assertRaisesRegex(ValueError, "không khớp"):
            u.download()
        self.assertFalse(list((self.root / "updates").glob("*.exe")))

    def test_offline_without_curl_explains_vpn(self):
        def offline(url, timeout=20, headers=None):
            raise urllib.error.URLError(TimeoutError("timed out"))
        status = self.make(offline).check()
        self.assertFalse(status["available"])
        self.assertIn("VPN", status["error"])


if __name__ == "__main__":
    unittest.main()


class FallbackTests(UpdaterTests):
    def test_web_blocked_falls_back_to_api(self):
        self.publish(tag="v1.1.70")
        del self.redirect[f"{BASE}/latest"]  # trang web bị chặn / lỗi
        self.files[f"https://api.github.com/repos/{REPO}/releases/latest"] = b'{"tag_name":"v1.1.70"}'
        status = self.make().check()
        self.assertEqual((status["latest"], status["available"]), ("1.1.70", True))
