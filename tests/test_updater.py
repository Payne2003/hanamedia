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

    def opener(self, url, timeout=20):
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

    def make(self):
        u = Updater(self.root, busy=lambda: self.busy, exit_app=lambda: self.exited.append(1), repo=REPO,
                    opener=self.opener, launcher=lambda path: self.launched.append(Path(path)), sleep=lambda s: None)
        u.state["supported"] = True
        return u

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


if __name__ == "__main__":
    unittest.main()


class FallbackTests(UpdaterTests):
    def test_web_blocked_falls_back_to_api(self):
        self.publish(tag="v1.1.70")
        del self.redirect[f"{BASE}/latest"]  # trang web bị chặn / lỗi
        self.files[f"https://api.github.com/repos/{REPO}/releases/latest"] = b'{"tag_name":"v1.1.70"}'
        status = self.make().check()
        self.assertEqual((status["latest"], status["available"]), ("1.1.70", True))
