"""Giải phóng hồ sơ Chrome riêng của tool khi Chrome cũ (treo, chạy ngầm, cửa sổ đăng nhập quên đóng) còn giữ nó.

Chrome không cho hai tiến trình dùng chung một thư mục hồ sơ: lần mở sau báo "session not created: Chrome instance
exited". Chỉ đóng các Chrome có --user-data-dir trỏ ĐÚNG vào thư mục hồ sơ của tool (data/profiles/...), không bao giờ
đụng Chrome cá nhân của người dùng.
"""
import os
import re
import subprocess
import time
from pathlib import Path

LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile")


def _matches(command, profile):
    """Dòng lệnh có --user-data-dir đúng bằng thư mục hồ sơ này (không khớp thư mục khác cùng tiền tố)."""
    if "--user-data-dir" not in command:
        return False
    target = str(profile)
    pattern = re.escape(target) + r"""(?=["'\s\\/]|$)"""
    flags = re.IGNORECASE if os.name == "nt" else 0
    return bool(re.search(r"--user-data-dir=[\"']?" + pattern, command, flags))


def _windows_processes():
    script = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    output = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    for line in output.splitlines():
        pid, _, command = line.partition("\t")
        if pid.strip().isdigit():
            yield int(pid), command


def _posix_processes():
    output = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=15).stdout
    for line in output.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and "chrom" in command.lower():
            yield int(pid), command


def profile_processes(profile):
    profile = Path(profile).resolve()
    try:
        items = _windows_processes() if os.name == "nt" else _posix_processes()
        return [pid for pid, command in items if _matches(command, profile)]
    except (OSError, subprocess.SubprocessError):
        return []


def free_profile(profile):
    """Đóng Chrome còn giữ hồ sơ này và xóa file khóa cũ. Trả về số tiến trình Chrome đã đóng."""
    profile = Path(profile).resolve()
    pids = profile_processes(profile)
    for pid in pids:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.kill(pid, 9)
        except (OSError, subprocess.SubprocessError):
            pass
    if pids:
        time.sleep(1.5)
    for name in LOCK_FILES:
        try:
            (profile / name).unlink()
        except OSError:
            pass
    return len(pids)


def short_error(error):
    """Dòng đầu của lỗi Selenium, bỏ stacktrace dài."""
    text = str(error).strip().replace("Message: ", "")
    return (text.splitlines() or [""])[0].split("; For documentation")[0][:200]
