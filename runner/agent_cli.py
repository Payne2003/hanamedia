"""Tìm và kiểm tra Claude Code / Codex CLI trên máy (máy chủ và máy khách), mở cửa sổ cài đặt / đăng nhập.

Không đọc hay lưu mật khẩu, mã đăng nhập: đăng nhập luôn do chủ máy tự làm trong cửa sổ dòng lệnh / trình duyệt.
"""
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WINDOWS = os.name == "nt"


def _home():
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())


def candidates(name):
    """Các chỗ hay có claude / codex ngoài PATH (app desktop thường không thấy PATH mới cài)."""
    home = _home()
    out = []
    if WINDOWS:
        appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
        if name == "claude":
            out += [home / ".local" / "bin" / "claude.exe", Path(appdata) / "npm" / "claude.cmd",
                    Path(local) / "AnthropicClaude" / "claude.exe"]
        else:
            out += [Path(appdata) / "npm" / "codex.cmd"]
            # codex.exe đi kèm app Codex: thư mục con đổi tên mỗi lần app cập nhật → lấy bản mới nhất
            found = glob.glob(str(Path(local) / "OpenAI" / "Codex" / "bin" / "**" / "codex.exe"), recursive=True)
            out += sorted((Path(p) for p in found), key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        out += [home / ".local" / "bin" / name, home / ".claude" / "local" / name, home / ".npm-global" / "bin" / name,
                Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]
        if name == "codex":
            out += [Path("/Applications/Codex.app/Contents/Resources/codex"),
                    Path("/Applications/Codex.app/Contents/Resources/bin/codex")]
    return out


def find(name, which=shutil.which):
    """Đường dẫn chạy được của 'claude' hoặc 'codex', hoặc None."""
    if name not in ("claude", "codex"):
        return None
    found = which(name)
    if found:
        return found
    for path in candidates(name):
        try:
            if path.is_file() and (WINDOWS or os.access(path, os.X_OK)):
                return str(path)
        except OSError:
            continue
    return None


def find_python(which=shutil.which):
    for name in (("py", "python", "python3") if WINDOWS else ("python3", "python")):
        path = which(name)
        if path and "WindowsApps" not in path:  # bản giả của Microsoft Store chỉ mở Store, không chạy được
            return path
    return None


def logged_in(name, path, run=subprocess.run):
    """True / False theo lệnh kiểm tra của CLI; None nếu bản CLI này không có lệnh kiểm tra."""
    args = [path, "auth", "status"] if name == "claude" else [path, "login", "status"]
    try:
        result = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
                     stdin=subprocess.DEVNULL, creationflags=FLAGS)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout + result.stderr).lower()
    if "unknown command" in text or "unrecognized" in text or "unexpected argument" in text:
        return None
    if result.returncode != 0:
        return False
    return not any(word in text for word in ("not logged in", "not signed in", "logged out", '"loggedin": false',
                                             "no credentials", "please log in", "please login"))


def status(which=shutil.which, run=subprocess.run, check_login=True):
    out = {"python": bool(find_python(which)), "engines": {}}
    for name in ("claude", "codex"):
        path = find(name, which)
        out["engines"][name] = {"found": bool(path), "path": path or "",
                                "login": logged_in(name, path, run) if path and check_login else None}
    return out


def pick(prefer="auto", which=shutil.which):
    """Engine dùng cho lần chạy tự động: theo lựa chọn, không thì Claude trước rồi Codex."""
    order = [prefer] if prefer in ("claude", "codex") else ["claude", "codex"]
    env = os.environ.get("HANAMEDIA_ENGINE", "").strip().lower()
    if prefer not in ("claude", "codex") and env in ("claude", "codex"):
        order = [env, "codex" if env == "claude" else "claude"]
    for name in order:
        if find(name, which):
            return name
    return None


# ---- cửa sổ cài đặt / đăng nhập (chủ máy tự thao tác) ----
INSTALL = {
    "claude": {"nt": 'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"',
               "posix": "curl -fsSL https://claude.ai/install.sh | bash"},
    "codex": {"nt": "npm install -g @openai/codex", "posix": "npm install -g @openai/codex"},
    "python": {"nt": "winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements",
               "posix": "echo Cai Python 3 tu python.org"},
}


def open_terminal(command, title="HanaMedia"):
    """Mở một cửa sổ dòng lệnh thấy được để chủ máy theo dõi / nhập (đăng nhập, cài đặt)."""
    if WINDOWS:
        subprocess.Popen(f'start "{title}" cmd /k {command}', shell=True)
    elif sys.platform == "darwin":
        script = command.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                          "-e", 'tell application "Terminal" to activate'])
    else:
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                subprocess.Popen([term, "-e", "bash", "-lc", command + "; exec bash"])
                return
        raise OSError("Không mở được cửa sổ dòng lệnh. Chạy tay: " + command)


def install(what):
    if what not in INSTALL:
        raise ValueError("Không có mục cài này.")
    open_terminal(INSTALL[what]["nt" if WINDOWS else "posix"], f"HanaMedia - cai {what}")


def login(name):
    path = find(name)
    if not path:
        raise ValueError(f"Máy chưa có {'Claude Code' if name == 'claude' else 'Codex'}. Bấm Cài trước.")
    quoted = f'"{path}"' if " " in path else path
    open_terminal(f"{quoted} auth login" if name == "claude" else f"{quoted} login", "HanaMedia - dang nhap")
