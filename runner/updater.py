"""Tự cập nhật HANA MEDIA từ GitHub Releases (không phải tải bộ cài thủ công).

- Kiểm tra: đọc bản mới nhất ở https://api.github.com/repos/<UPDATE_REPO>/releases/latest (khi mở app và mỗi 6 giờ).
- Cập nhật (người dùng bấm): tải HanaMedia-Setup-<phiên bản>.exe của bản đó, kiểm tra kích thước và mã SHA-256 GitHub
  công bố, chạy bộ cài ở chế độ im lặng rồi tắt app; bộ cài chép đè và tự mở lại app. Dữ liệu (tài khoản, hàng đợi,
  Chrome đã đăng nhập, bản quyền) ở %LOCALAPPDATA%\\HanaMedia\\data không bị đụng tới.
- Chỉ tải từ đúng kho phát hành qua HTTPS; không cập nhật khi đang có tác vụ chạy.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .paths import frozen
from .version import APP_VERSION, UPDATE_REPO

CHECK_INTERVAL = 6 * 3600
ASSET = re.compile(r"^HanaMedia-Setup-[0-9][0-9.]*\.exe$")


def parse_version(text):
    """'v1.1.45' / '1.0.1' -> (1, 1, 45); không hợp lệ -> None."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", str(text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def newer(candidate, current=APP_VERSION):
    a, b = parse_version(candidate), parse_version(current)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def http_get(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": "HanaMedia-Updater",
                                                   "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, timeout=timeout)


class Updater:
    def __init__(self, data, busy=lambda: False, exit_app=None, repo=UPDATE_REPO, opener=http_get, clock=time.time,
                 launcher=None, sleep=time.sleep):
        self.dir = Path(data) / "updates"
        self.busy, self.exit_app, self.repo = busy, exit_app, repo
        self.opener, self.clock, self.launcher = opener, clock, launcher or self.launch_installer
        self.sleep = sleep
        self.lock = threading.Lock()
        self.state = {"current": APP_VERSION, "latest": None, "available": False, "notes": "", "size": 0,
                      "checked_at": None, "error": "", "phase": "idle", "progress": 0,
                      "supported": frozen() and os.name == "nt"}
        self.asset = None

    def status(self):
        with self.lock:
            return dict(self.state)

    def set(self, **values):
        with self.lock:
            self.state.update(values)

    # ---- kiểm tra bản mới ------------------------------------------------------------------------------------------
    def due(self):
        checked = self.status()["checked_at"]
        return not checked or self.clock() - checked >= CHECK_INTERVAL

    def check(self):
        if not self.repo:
            self.set(error="Bản app này chưa cấu hình nơi cập nhật.")
            return self.status()
        try:
            with self.opener(f"https://api.github.com/repos/{self.repo}/releases/latest") as response:
                release = json.loads(response.read(2_000_000))
            tag = str(release.get("tag_name") or "")
            asset = next((a for a in release.get("assets") or [] if ASSET.match(str(a.get("name") or ""))), None)
            url = str((asset or {}).get("browser_download_url") or "")
            if asset and not url.startswith(f"https://github.com/{self.repo}/releases/download/"):
                asset = None  # chỉ tải bộ cài từ đúng kho phát hành
            self.asset = asset
            available = bool(asset) and newer(tag)
            self.set(latest=tag.lstrip("v") or None, available=available, notes=str(release.get("body") or "")[:2000],
                     size=int((asset or {}).get("size") or 0), checked_at=self.clock(), error="")
        except urllib.error.HTTPError as error:
            message = "Chưa có bản phát hành nào." if error.code == 404 else f"GitHub trả mã {error.code}."
            self.set(checked_at=self.clock(), error=message, available=False)
        except (OSError, ValueError) as error:
            self.set(checked_at=self.clock(), error=f"Không kiểm tra được cập nhật (mạng?): {str(error)[:120]}")
        return self.status()

    # ---- tải và cài --------------------------------------------------------------------------------------------------
    def start_install(self):
        status = self.status()
        if not status["supported"]:
            raise ValueError("Tự cập nhật chỉ có ở bản app đã cài trên Windows.")
        if status["phase"] in ("downloading", "installing"):
            raise ValueError("Đang cập nhật.")
        if not status["available"] or not self.asset:
            raise ValueError("Không có bản mới để cập nhật. Bấm Kiểm tra cập nhật trước.")
        if self.busy():
            raise ValueError("Đang có tác vụ chạy. Đợi chạy xong (hoặc tạm dừng bộ chạy) rồi cập nhật.")
        self.set(phase="downloading", progress=0, error="")
        threading.Thread(target=self.install_worker, daemon=True).start()
        return self.status()

    def download(self):
        asset = self.asset
        name, size = str(asset["name"]), int(asset.get("size") or 0)
        digest = str(asset.get("digest") or "")
        expected = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else ""
        self.dir.mkdir(parents=True, exist_ok=True)
        target, part = self.dir / name, self.dir / (name + ".part")
        hasher, done = hashlib.sha256(), 0
        with self.opener(asset["browser_download_url"], timeout=60) as response, open(part, "wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                if size:
                    self.set(progress=min(99, int(done * 100 / size)))
        if size and done != size:
            part.unlink(missing_ok=True)
            raise ValueError("Tải bộ cài chưa đủ dung lượng. Thử lại.")
        if expected and hasher.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise ValueError("Bộ cài tải về không khớp mã kiểm tra của GitHub. Đã hủy, không cài.")
        os.replace(part, target)
        return target

    def install_worker(self):
        try:
            installer = self.download()
            if self.busy():
                raise ValueError("Có tác vụ vừa bắt đầu chạy. Đợi chạy xong rồi cập nhật.")
            self.set(phase="installing", progress=100)
            self.launcher(installer)
            if self.exit_app:
                self.sleep(1.5)  # để giao diện kịp hiện "Đang cài…"
                self.exit_app()
        except (OSError, ValueError) as error:
            self.set(phase="error", error=str(error)[:300] or "Không cập nhật được.")

    @staticmethod
    def launch_installer(installer):
        """Chạy bộ cài im lặng, tách khỏi app (app tắt ngay sau đó); bộ cài tự mở lại app khi xong."""
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                          "/RELAUNCH=1"], creationflags=flags, close_fds=True)

    def clean_old(self):
        """Xóa bộ cài đã tải của các lần cập nhật trước."""
        try:
            for path in self.dir.glob("HanaMedia-Setup-*"):
                path.unlink(missing_ok=True)
        except OSError:
            pass
