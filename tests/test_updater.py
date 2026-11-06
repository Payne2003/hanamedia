"""Tự cập nhật: so phiên bản, đọc bản mới nhất trên GitHub Releases, tải bộ cài có kiểm tra SHA-256, không cài khi
đang có tác vụ chạy; chạy bộ cài im lặng rồi tắt app."""
import hashlib
import io
import json
import shutil
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

from runner import updater as module
from runner.updater import Updater, newer, parse_version

REPO = "Owner/app"
BODY = b"MZ fake installer " * 1000


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def release(tag="v1.1.50", body=BODY, digest=True, url=None):
    asset = {"name": f"HanaMedia-Setup-{tag.lstrip('v')}.exe", "size": len(body),
             "browser_download_url": url or f"https://github.com/{REPO}/releases/download/{tag}/HanaMedia-Setup-{tag.lstrip('v')}.exe"}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(body).hexdigest()
    return {"tag_name": tag, "body": "Sửa lỗi", "assets": [asset]}


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.files = {}
        self.launched, self.exited, self.busy = [], [], False

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def opener(self, url, timeout=20):
        if url not in self.files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return Response(self.files[url])

    def make(self, data, payload=BODY):
        self.files[f"https://api.github.com/repos/{REPO}/releases/latest"] = json.dumps(data).encode()
        for asset in data["assets"]:
            self.files[asset["browser_download_url"]] = payload
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

    def test_check_and_install(self):
        u = self.make(release())
        status = u.check()
        self.assertEqual((status["available"], status["latest"], status["error"]), (True, "1.1.50", ""))
        u.start_install()
        self.wait(u)
        self.assertEqual(u.status()["phase"], "installing")
        self.assertEqual(self.launched[0].read_bytes(), BODY)
        self.assertEqual(self.exited, [1])
        u.clean_old()
        self.assertFalse(any(self.root.joinpath("updates").iterdir()))

    def test_tampered_download_is_rejected(self):
        u = self.make(release(), payload=b"evil" * 10)
        u.check()
        u.start_install()
        self.wait(u)
        self.assertEqual(u.status()["phase"], "error")
        self.assertEqual(self.launched, [])

    def test_bad_digest_rejected_even_with_same_size(self):
        data = release()
        data["assets"][0]["digest"] = "sha256:" + "0" * 64
        u = self.make(data)
        u.check()
        u.start_install()
        self.wait(u)
        self.assertIn("mã kiểm tra", u.status()["error"])
        self.assertEqual(self.launched, [])

    def test_foreign_url_old_version_busy_and_unsupported(self):
        u = self.make(release(url="https://evil.example/HanaMedia-Setup-1.1.50.exe"))
        self.assertFalse(u.check()["available"])  # chỉ tải từ đúng kho phát hành
        u = self.make(release(tag="v1.0.1"))
        self.assertFalse(u.check()["available"])
        u = self.make(release())
        u.check()
        self.busy = True
        with self.assertRaisesRegex(ValueError, "tác vụ chạy"):
            u.start_install()
        self.busy = False
        u.state["supported"] = False
        with self.assertRaisesRegex(ValueError, "Windows"):
            u.start_install()

    def test_no_release_yet(self):
        u = Updater(self.root, repo=REPO, opener=self.opener)
        self.assertIn("Chưa có bản phát hành", u.check()["error"])


if __name__ == "__main__":
    unittest.main()
