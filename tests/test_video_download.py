"""Tải video gốc từ link: chỉ http(s) công khai, báo lỗi dễ hiểu, chạy nền, trả đường dẫn file mp4."""
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from runner.video_download import DownloadError, VideoDownloader, check_url, friendly


class FakeYDL:
    fail = None

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=True):
        if self.fail:
            raise RuntimeError(self.fail)
        folder = Path(self.options["outtmpl"]).parent
        for hook in self.options["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 5 * 1048576, "total_bytes": 10 * 1048576})
        (folder / "Bear run [abc].mp4").write_bytes(b"video")
        return {"title": "Bear run", "duration": 60}


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        FakeYDL.fail = None
        module = types.SimpleNamespace(YoutubeDL=FakeYDL)
        patcher = patch.dict(sys.modules, {"yt_dlp": module})
        patcher.start()
        self.addCleanup(patcher.stop)

    def wait(self, downloader):
        for _ in range(200):
            if not downloader.status()["busy"]:
                return downloader.status()
            time.sleep(.02)
        self.fail("download did not finish")

    def test_only_public_http_links(self):
        self.assertEqual(check_url(" https://www.youtube.com/watch?v=abc "), "https://www.youtube.com/watch?v=abc")
        for bad in ("file:///etc/passwd", "ftp://x.com/a.mp4", "http://localhost/a.mp4", "http://127.0.0.1/a", "not a link"):
            with self.assertRaises(DownloadError):
                check_url(bad)

    def test_download_returns_mp4_path_and_progress(self):
        downloader = VideoDownloader(Path(self.temp.name) / "sources")
        downloader.start("https://www.tiktok.com/@a/video/1")
        state = self.wait(downloader)
        self.assertEqual(state["error"], "")
        self.assertTrue(state["path"].endswith("Bear run [abc].mp4"))
        self.assertTrue(Path(state["path"]).is_file())
        self.assertEqual((state["title"], state["progress"]), ("Bear run", 100))

    def test_errors_are_explained(self):
        FakeYDL.fail = "ERROR: [youtube] abc: Sign in to confirm your age"
        downloader = VideoDownloader(Path(self.temp.name) / "sources")
        downloader.start("https://www.youtube.com/watch?v=abc")
        self.assertIn("cần đăng nhập", self.wait(downloader)["error"])
        self.assertIn("giới hạn", friendly("HTTP Error 429: Too Many Requests"))
        webpage = ("ERROR: [generic] x: Unable to download webpage: ('Unable to connect to proxy', "
                   "OSError('Tunnel connection failed: 403 Forbidden')); please report this issue")
        self.assertIn("Không kết nối được", friendly(webpage))  # "webpage" không bị hiểu nhầm là giới hạn tuổi
        self.assertIn("dài", VideoDownloader.too_long({"duration": 3600}))
        self.assertIsNone(VideoDownloader.too_long({"duration": 120}))


if __name__ == "__main__":
    unittest.main()
