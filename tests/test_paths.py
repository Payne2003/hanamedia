import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner import paths
from runner.instance import InstanceLock
from runner.store import Store


class PathTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_source_mode_keeps_data_next_to_app(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HANAMEDIA_DATA", None)
            self.assertEqual(paths.data_root(), paths.SOURCE_ROOT / "data")
            self.assertEqual(paths.resource_root(), paths.SOURCE_ROOT)

    def test_installed_app_uses_localappdata_and_bundle(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(self.root / "bundle"), create=True), \
                patch.object(sys, "platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "Local")}):
            os.environ.pop("HANAMEDIA_DATA", None)
            self.assertEqual(paths.data_root(), self.root / "Local" / "HanaMedia" / "data")
            self.assertEqual(paths.resource_root(), self.root / "bundle")
            with patch.dict(os.environ, {"HANAMEDIA_DATA": str(self.root / "custom")}):
                self.assertEqual(paths.data_root(), (self.root / "custom").resolve())

    def test_installed_mac_app_uses_application_support(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "platform", "darwin"), \
                patch.object(paths.Path, "home", return_value=self.root):
            os.environ.pop("HANAMEDIA_DATA", None)
            self.assertEqual(paths.data_root(), self.root / "Library" / "Application Support" / "HanaMedia" / "data")

    def old_data(self):
        old = self.root / "Huy-Muse-Runner" / "data"
        old.mkdir(parents=True)
        store = Store(old / "studio.sqlite3")
        store.add_account("a@tuan89.site", "", 5)
        cookies = old / "profiles" / "abc" / "Default"
        cookies.mkdir(parents=True)
        (cookies / "Cookies").write_bytes(b"session")
        (old / "profiles" / "abc" / "SingletonLock").write_text("x")
        (old / "runner.lock").write_text("0")
        return old

    def test_first_run_copies_accounts_and_chrome_profiles(self):
        old, new = self.old_data(), self.root / "Local" / "HanaMedia" / "data"
        self.assertEqual(paths.import_old_data(new, [self.root / "missing", old]), old.resolve())
        self.assertEqual([a["email"] for a in Store(new / "studio.sqlite3").snapshot()["accounts"]], ["a@tuan89.site"])
        self.assertEqual((new / "profiles" / "abc" / "Default" / "Cookies").read_bytes(), b"session")
        self.assertFalse((new / "profiles" / "abc" / "SingletonLock").exists())
        self.assertFalse((new / "runner.lock").exists())
        self.assertTrue((old / "studio.sqlite3").exists())  # dữ liệu cũ giữ nguyên
        self.assertIsNone(paths.import_old_data(new, [old]))  # đã có dữ liệu: không chép đè

    def test_old_runner_still_open_is_not_copied(self):
        old, new = self.old_data(), self.root / "new"
        lock = InstanceLock(old / "runner.lock")
        try:
            self.assertIsNone(paths.import_old_data(new, [old]))
            self.assertFalse(paths.has_data(new))
        finally:
            lock.close()


if __name__ == "__main__":
    unittest.main()
