"""Thanh hỗ trợ trên cùng: mặc định SĐT 0983105651, QR trống; lưu lời nhắn/SĐT/ảnh QR; ảnh QR bị xóa thì về trống."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from app import AppServer
from runner.store import Store

PNG = b"\x89PNG\r\n\x1a\nqr-fixture"


class SupportBarTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "t.db")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_defaults_save_and_missing_qr(self):
        self.assertEqual(self.store.support_bar(), {"message": "Mời cafe HANA MEDIA", "phone": "0983105651", "qr": None})
        token = self.store.add_image(PNG)["token"]
        saved = self.store.save_support_bar("  Ủng hộ   HANA  ", "0983 105 651", token)
        self.assertEqual(saved, {"message": "Ủng hộ HANA", "phone": "0983 105 651", "qr": token})
        self.assertEqual(Store(self.root / "t.db").support_bar()["qr"], token)  # còn sau khi khởi động lại
        self.assertEqual(self.store.save_support_bar("", "0983105651", None)["message"], "Mời cafe HANA MEDIA")
        for phone in ("", "abc", "12", "0983105651<script>"):
            with self.assertRaises(ValueError):
                self.store.save_support_bar("x", phone, None)
        with self.assertRaises(ValueError):
            self.store.save_support_bar("x", "0983105651", "f" * 32)
        self.store.save_support_bar("x", "0983105651", token)
        self.store.image_file(token)[0].unlink()
        self.assertIsNone(self.store.support_bar()["qr"])

    def test_api_requires_key_and_state_exposes_bar(self):
        engine = type("E", (), {"snapshot": lambda e: {}, "changed": threading.Event()})()
        server = AppServer(0, self.store, engine)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def call(method, path, body=None, key=True):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                headers = {"Origin": server.origin, "Content-Type": "application/json"}
                if key:
                    headers["X-Runner-Key"] = server.secret
                connection.request(method, path, json.dumps(body) if body is not None else None, headers)
                response = connection.getresponse()
                result = (response.status, json.loads(response.read()))
                connection.close()
                return result
            self.assertEqual(call("GET", "/api/state")[1]["support"]["phone"], "0983105651")
            self.assertEqual(call("POST", "/api/settings/support", {"message": "A", "phone": "0983105651"}, key=False)[0], 403)
            status, data = call("POST", "/api/settings/support", {"message": "Mời cafe", "phone": "0901234567", "qr": None})
            self.assertEqual((status, data["support"]["phone"]), (200, "0901234567"))
            self.assertEqual(call("POST", "/api/settings/support", {"phone": "x"})[0], 400)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
