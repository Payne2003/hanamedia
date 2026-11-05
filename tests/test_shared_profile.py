import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import AppServer
from runner.profiles import SharedProfile
from runner.store import Store


class GateTests(unittest.TestCase):
    def test_idle_holder_is_released_busy_holder_blocks(self):
        gate = SharedProfile(tempfile.mkdtemp())
        released, busy = [], {"Gemini": False}
        gate.register("Gemini", lambda: (released.append("Gemini"), not busy["Gemini"])[1])
        gate.register("Flow", lambda: (released.append("Flow"), True)[1])
        gate.take("Gemini")  # tắt: không làm gì
        self.assertIsNone(gate.holder)
        gate.enabled = True
        gate.take("Gemini")
        gate.take("Gemini")  # đang giữ: không tự đóng chính mình
        self.assertEqual((gate.holder, released), ("Gemini", []))
        gate.take("Flow")  # Gemini rảnh: bị đóng, Flow giữ hồ sơ
        self.assertEqual((gate.holder, released), ("Flow", ["Gemini"]))
        gate.take("Gemini")
        busy["Gemini"] = True
        with self.assertRaisesRegex(RuntimeError, "Gemini đang dùng"):
            gate.take("Flow")
        self.assertEqual(gate.holder, "Gemini")
        self.assertEqual(gate.release_all(), ["Gemini"])

    def test_open_login_window_blocks_automation(self):
        gate = SharedProfile(tempfile.mkdtemp())
        gate.enabled = True
        gate.login_process = type("P", (), {"poll": lambda self: None})()
        with self.assertRaisesRegex(RuntimeError, "đăng nhập tài khoản chung vẫn đang mở"):
            gate.take("Gemini")


class SharedAccountServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "studio.sqlite3")

        class Engine:
            snapshot = lambda self: {}
            changed = threading.Event()
            flow_busy = False
            flow_profile = self.root / "profiles" / "google-flow" / "Default"
            released = 0
            def release_flow(self):
                Engine.released += 1
                return not self.flow_busy
        self.engine = Engine()

    def tearDown(self):
        self.temp.cleanup()

    def server(self):
        server = AppServer(0, self.store, self.engine)
        self.addCleanup(server.server_close)
        self.addCleanup(server.gemini_web.close)
        self.addCleanup(server.chatgpt_web.close)
        return server

    def test_toggle_switches_every_google_profile_and_persists(self):
        server = self.server()
        shared = self.root / "profiles" / "google-shared"
        server.apply_shared(True)
        self.assertEqual((server.gemini_web.profile, server.chatgpt_web.profile, self.engine.flow_profile),
                         (shared, shared, shared / "Default"))
        self.assertTrue(server.shared.enabled)
        self.assertGreaterEqual(self.engine.released, 1)
        again = self.server()  # khởi động lại tool: vẫn bật
        self.assertEqual(again.gemini_web.profile, shared)
        again.apply_shared(False)
        self.assertEqual((again.gemini_web.profile.name, again.chatgpt_web.profile.name, self.engine.flow_profile.parent.name),
                         ("gemini-web", "chatgpt-web", "google-flow"))
        self.assertIs(self.store.setting("shared_google_profile"), False)

    def test_toggle_refused_while_something_runs(self):
        server = self.server()
        self.engine.flow_busy = True
        with self.assertRaisesRegex(ValueError, "Flow đang chạy"):
            server.apply_shared(True)
        self.assertFalse(server.shared.enabled)
        self.engine.flow_busy = False
        server.gemini_web.job["busy"] = True
        with self.assertRaisesRegex(ValueError, "Gemini"):
            server.apply_shared(True)

    def test_shared_login_opens_one_window_with_all_services(self):
        server = self.server()
        with self.assertRaisesRegex(ValueError, "Bật"):
            server.open_shared_login()
        server.apply_shared(True)
        launched = []
        with patch("app.chrome_executable", lambda: "chrome.exe"):
            server.open_shared_login("ban@gmail.com", launcher=lambda args: launched.append(args) or type("P", (), {"poll": lambda self: None})())
            with self.assertRaisesRegex(ValueError, "đang mở"):
                server.open_shared_login("ban@gmail.com", launcher=lambda args: None)
        args = launched[0]
        self.assertIn(f"--user-data-dir={self.root / 'profiles' / 'google-shared'}", args)
        self.assertTrue(any("accounts.google.com" in a and "ban%40gmail.com" in a for a in args))
        self.assertTrue(any("chatgpt.com" in a for a in args))
        self.assertTrue(any("flow.google.com" in a for a in args))


if __name__ == "__main__":
    unittest.main()
