"""Trang Cộng đồng: nhóm/video do người dùng thêm (chỉ link https), chỉ mở được link đã lưu."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app import AppServer
from runner.store import Store


class CommunityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "t.db")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_save_validate_and_known_links(self):
        self.assertEqual(self.store.community(), {"groups": [], "videos": []})
        data = self.store.save_community([{"name": " Nhóm 1  Zalo ", "url": "https://zalo.me/g/abc123"}],
                                         [{"name": "Demo", "url": "https://www.facebook.com/reel/1"}])
        self.assertEqual(data["groups"], [{"name": "Nhóm 1 Zalo", "url": "https://zalo.me/g/abc123"}])
        self.assertEqual(Store(self.root / "t.db").community(), data)
        self.assertTrue(self.store.community_link("https://zalo.me/g/abc123"))
        self.assertFalse(self.store.community_link("https://evil.example/"))
        for groups in ([{"name": "x", "url": "http://zalo.me/g/a"}], [{"name": "x", "url": "javascript:alert(1)"}],
                       [{"name": "", "url": "https://zalo.me/g/a"}], [{"name": "x", "url": 'https://a.b/"onmouseover'}],
                       [{"name": "x", "url": "https://a.b"}] * 51, "x"):
            with self.subTest(groups=str(groups)[:40]), self.assertRaises(ValueError):
                self.store.save_community(groups, [])

    def test_api_opens_only_saved_links(self):
        engine = type("E", (), {"snapshot": lambda e: {}, "changed": threading.Event()})()
        server = AppServer(0, self.store, engine)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def call(path, body, key=True):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            headers = {"Origin": server.origin, "Content-Type": "application/json"}
            if key:
                headers["X-Runner-Key"] = server.secret
            connection.request("POST", path, json.dumps(body), headers)
            response = connection.getresponse()
            result = (response.status, json.loads(response.read()))
            connection.close()
            return result
        try:
            group = {"name": "Nhóm 1", "url": "https://zalo.me/g/abc"}
            self.assertEqual(call("/api/settings/community", {"groups": [group], "videos": []}, key=False)[0], 403)
            self.assertEqual(call("/api/settings/community", {"groups": [group], "videos": []})[0], 200)
            with mock.patch("app.webbrowser.open") as opened:
                self.assertEqual(call("/api/open-link", {"url": "https://zalo.me/g/abc"})[0], 200)
                self.assertEqual(call("/api/open-link", {"url": "https://evil.example/"})[0], 400)
                self.assertEqual(call("/api/open-link", {"url": "file:///C:/Windows"})[0], 400)
            opened.assert_called_once_with("https://zalo.me/g/abc")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
