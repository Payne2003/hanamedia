"""Nhật ký lỗi trong app: gom phiên bản, cấu hình máy, phần cuối runner.log và update.log để người dùng sao chép."""
import shutil
import tempfile
import unittest
from pathlib import Path

from app import AppServer
from runner.engine import Engine
from runner.store import Store


class LogReportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.store = Store(self.root / "t.db")
        self.engine = Engine(self.store, self.root)
        self.server = AppServer(0, self.store, self.engine)

    def tearDown(self):
        self.server.server_close()
        self.engine.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_report_has_version_hardware_and_recent_errors(self):
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "runner.log").write_text("\n".join(f"dòng {i}" for i in range(500)) + "\n\t(No symbol) [0x7ff]\n",
                                         encoding="utf-8")
        text = self.server.log_report(lines=300)["text"]
        self.assertIn("HANA MEDIA", text)
        self.assertIn("luồng", text)
        self.assertIn("dòng 499", text)
        self.assertNotIn("dòng 100\n", text)  # chỉ phần cuối
        self.assertNotIn("No symbol", text)  # bỏ backtrace địa chỉ bộ nhớ
        self.assertIn("update.log", text)

    def test_report_without_logs(self):
        self.assertIn("(chưa có)", self.server.log_report()["text"])


if __name__ == "__main__":
    unittest.main()
