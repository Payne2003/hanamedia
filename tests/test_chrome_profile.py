"""Chỉ đóng Chrome đang giữ ĐÚNG hồ sơ của tool; không đụng Chrome cá nhân hay hồ sơ cùng tiền tố."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from runner import chrome_profile
from runner.chrome_profile import _matches, short_error


class ChromeProfileTests(unittest.TestCase):
    def test_matches_only_exact_profile(self):
        root = Path("/data/profiles/gemini-web")
        self.assertTrue(_matches(f'chrome --user-data-dir={root} --profile-directory=Default', root))
        self.assertTrue(_matches(f'chrome "--user-data-dir={root}" --x', root))
        self.assertTrue(_matches(f'chrome --user-data-dir="{root}" --x', root))
        self.assertTrue(_matches(f'chrome --type=renderer --user-data-dir={root}', root))
        self.assertFalse(_matches(f'chrome --user-data-dir={root}-old', root))
        self.assertFalse(_matches('chrome --profile-directory=Default', root))  # Chrome cá nhân của người dùng
        self.assertFalse(_matches(f'notepad {root}', root))

    def test_free_profile_kills_matching_and_clears_locks(self):
        import tempfile, shutil
        folder = Path(tempfile.mkdtemp()).resolve()
        try:
            (folder / "SingletonLock").write_text("x")
            processes = [(11, f"chrome --user-data-dir={folder}"), (12, "chrome --user-data-dir=/home/me/.config/google-chrome")]
            killed = []
            source = "_windows_processes" if os.name == "nt" else "_posix_processes"
            with patch.object(chrome_profile, source, lambda: iter(processes)), \
                    patch.object(chrome_profile.os, "kill", lambda pid, sig: killed.append(pid)), \
                    patch.object(chrome_profile.subprocess, "run", lambda args, **kw: killed.append(int(args[2]))), \
                    patch.object(chrome_profile.time, "sleep", lambda s: None):
                self.assertEqual(chrome_profile.free_profile(folder), 1)
            self.assertEqual(killed, [11])
            self.assertFalse((folder / "SingletonLock").exists())
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_short_error_drops_stacktrace(self):
        error = Exception("Message: session not created: Chrome instance exited. Examine ChromeDriver verbose log.; "
                          "For documentation on this error, please visit: https://x\nStacktrace: Symbols not available.")
        self.assertEqual(short_error(error),
                         "session not created: Chrome instance exited. Examine ChromeDriver verbose log.")


if __name__ == "__main__":
    unittest.main()
