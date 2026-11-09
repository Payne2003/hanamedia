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
import shutil
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


RETRY_WAITS = (2, 5)  # giây chờ giữa các lần thử lại khi mạng chập chờn (VPN vừa bật/tắt, đổi Wi-Fi)


def http_get(url, timeout=20, headers=None):
    """GET qua HTTPS: đọc lại proxy mỗi lần, thử certifi và bỏ proxy khi hỏng (runner/net.py). Luôn xác minh."""
    from .net import urlopen
    return urlopen(urllib.request.Request(url, headers={"User-Agent": "HanaMedia-Updater", **(headers or {})}),
                   timeout=timeout)


def curl_path():
    """curl.exe có sẵn trong Windows 10/11: đi qua mạng và kho chứng chỉ của Windows (giống trình duyệt)."""
    if os.name != "nt":
        return None
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    return str(system) if system.is_file() else shutil.which("curl.exe")


def curl_fetch(url, target, timeout=60, resume=False):
    """Tải `url` về `target` bằng curl.exe. Trả về địa chỉ cuối sau chuyển hướng. Chỉ HTTPS."""
    curl = curl_path()
    if not curl:
        raise OSError("Máy không có curl.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    base = [curl, "-sS", "-L", "--fail", "--proto", "=https", "--proto-redir", "=https", "--retry", "2",
            "--connect-timeout", "20", "--max-time", str(int(timeout)), "-A", "HanaMedia-Updater",
            "-w", "%{url_effective}", "-o", str(target)] + (["-C", "-"] if resume else [])
    # Một số VPN/diệt virus chặn bước kiểm tra thu hồi chứng chỉ của Windows; chứng chỉ vẫn được xác minh đầy đủ.
    for extra in (["--ssl-revoke-best-effort"], []):
        result = subprocess.run(base + extra + [url], capture_output=True, text=True, timeout=timeout + 30,
                                creationflags=flags)
        if result.returncode == 2 and extra:  # curl cũ không biết tùy chọn này
            continue
        if result.returncode:
            raise OSError(f"curl mã {result.returncode}: {result.stderr.strip()[:160]}")
        return result.stdout.strip()
    raise OSError("curl không chạy được")


class Updater:
    def __init__(self, data, busy=lambda: False, exit_app=None, repo=UPDATE_REPO, opener=http_get, clock=time.time,
                 launcher=None, sleep=time.sleep, curl=curl_fetch):
        self.dir = Path(data) / "updates"
        self.busy, self.exit_app, self.repo = busy, exit_app, repo
        self.opener, self.clock, self.launcher = opener, clock, launcher or self.launch_installer
        self.sleep, self.curl = sleep, curl
        self.lock = threading.Lock()
        self.state = {"current": APP_VERSION, "latest": None, "available": False, "notes": "", "size": 0,
                      "checked_at": None, "error": "", "phase": "idle", "progress": 0,
                      "supported": frozen() and os.name == "nt"}
        self.asset = None
        self.log_file = Path(data) / "logs" / "update.log"
        self.check_previous()

    def log(self, text):
        """Nhật ký cập nhật (data/logs/update.log) để biết bước nào hỏng."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] (đang dùng {APP_VERSION}) {text}\n")
        except OSError:
            pass

    def check_previous(self):
        """Lần trước đã chạy bộ cài bản X mà app vẫn là bản cũ: báo cập nhật không thành công."""
        marker = self.dir / "pending.json"
        try:
            pending = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            marker.unlink()
        except OSError:
            pass
        if newer(pending.get("version"), APP_VERSION):
            self.log(f"Bộ cài bản {pending.get('version')} đã chạy nhưng app vẫn là {APP_VERSION}: cài không thành công.")
            self.state.update(phase="error", error=f"Lần cập nhật lên {pending.get('version')} chưa thành công "
                              "(xem data/logs/update-setup.log). Bấm Tải bộ cài để cài tay.")
        else:
            self.log("Cập nhật thành công.")
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
        """Tìm bản mới nhất qua https://github.com/<kho>/releases/latest (trang này chuyển tới /releases/tag/vX).
        Không dùng API của GitHub: API giới hạn 60 lần/giờ cho mỗi địa chỉ mạng, nhiều máy dùng chung mạng của nhà
        mạng nên hay bị từ chối (app không thấy bản mới)."""
        if not self.repo:
            self.set(error="Bản app này chưa cấu hình nơi cập nhật.")
            return self.status()
        base = f"https://github.com/{self.repo}/releases"
        try:
            tag, via_curl = self.find_latest(base)
            version = tag.lstrip("v")
            name = f"HanaMedia-Setup-{version}.exe"
            digest = self.release_digest(base, tag, name)
            if not digest and via_curl:
                digest = self.curl_digest(base, tag, name)
            asset = {"name": name, "browser_download_url": f"{base}/download/{tag}/{name}", "size": 0,
                     "digest": digest}
            self.asset = asset if ASSET.match(name) else None
            available = bool(self.asset) and newer(tag)
            self.set(latest=version or None, available=available, notes="", size=0, checked_at=self.clock(), error="")
            self.log(f"Kiểm tra: bản mới nhất {tag}, {'có' if available else 'không có'} bản mới"
                     f"{'' if asset['digest'] else ' (không lấy được mã SHA-256)'}.")
        except urllib.error.HTTPError as error:
            message = "Chưa có bản phát hành nào." if error.code == 404 else f"GitHub trả mã {error.code}."
            self.set(checked_at=self.clock(), error=message, available=False)
            self.log("Kiểm tra lỗi: " + message)
        except (OSError, ValueError) as error:
            message = str(error) if "Chưa có bản phát hành" in str(error) else (
                "Không kết nối được GitHub để kiểm tra cập nhật. Nếu đang bật VPN (1.1.1.1…) hoặc vừa đổi mạng, "
                "đợi mạng ổn định rồi bấm Kiểm tra lại.")
            self.set(checked_at=self.clock(), error=message, available=False)
            self.log(f"Kiểm tra lỗi: {type(error).__name__}: {str(error)[:200]}")
        return self.status()

    def find_latest(self, base):
        """Tag mới nhất, thử lại khi mạng chập chờn; cuối cùng hỏi qua curl.exe của Windows. Trả (tag, qua_curl)."""
        error = None
        for wait in (*RETRY_WAITS, None):
            try:
                return self.latest_tag(base), False
            except ValueError as failure:
                if "Chưa có bản phát hành" in str(failure):
                    raise
                error = failure
                self.log(f"Kiểm tra lỗi mạng, thử lại: {str(failure)[:300]}")
            if wait:
                self.sleep(wait)
        try:
            tag = self.curl_latest(base)
        except (OSError, ValueError, subprocess.SubprocessError) as failure:
            self.log(f"curl.exe cũng không kiểm tra được: {str(failure)[:200]}")
            raise error from None
        self.log(f"Kiểm tra qua curl.exe của Windows: {tag}")
        return tag, True

    def curl_latest(self, base):
        self.dir.mkdir(parents=True, exist_ok=True)
        page = self.dir / "latest.html"
        try:
            final = self.curl(f"{base}/latest", page, timeout=40)
        finally:
            page.unlink(missing_ok=True)
        match = re.search(r"/releases/tag/(v?[0-9][0-9.]*)$", final or "")
        if not match:
            raise ValueError("curl không thấy bản phát hành")
        return match.group(1)

    def curl_digest(self, base, tag, name):
        target = self.dir / (name + ".sha256.txt")
        try:
            self.curl(f"{base}/download/{tag}/{name}.sha256", target, timeout=40)
            found = re.search(r"\b([0-9a-fA-F]{64})\b", target.read_text("ascii", "ignore"))
            return "sha256:" + found.group(1).lower() if found else ""
        except (OSError, ValueError, subprocess.SubprocessError):
            return ""
        finally:
            target.unlink(missing_ok=True)

    def latest_tag(self, base):
        """Tag bản mới nhất: trang github.com (không giới hạn), không được thì hỏi API (60 lần/giờ/địa chỉ mạng)."""
        errors = []
        try:
            with self.opener(f"{base}/latest") as response:
                match = re.search(r"/releases/tag/(v?[0-9][0-9.]*)$", response.geturl() or "")
            if match:
                return match.group(1)
            errors.append("trang Releases không chỉ tới bản nào")
        except (OSError, ValueError) as error:
            errors.append(f"github.com: {str(error)[:80]}")
        try:
            with self.opener(f"https://api.github.com/repos/{self.repo}/releases/latest") as response:
                tag = str(json.loads(response.read(2_000_000)).get("tag_name") or "")
            if parse_version(tag):
                return tag
            errors.append("API không có tag")
        except urllib.error.HTTPError as error:
            errors.append("API: " + ("chưa có bản phát hành" if error.code == 404 else f"mã {error.code}"))
        except (OSError, ValueError) as error:
            errors.append(f"API: {str(error)[:80]}")
        if all("chưa có" in e or "không chỉ tới" in e for e in errors):
            raise ValueError("Chưa có bản phát hành nào.")
        raise ValueError("; ".join(errors))

    def release_digest(self, base, tag, name):
        """Mã SHA-256 của bộ cài: file <tên>.sha256 đăng kèm bản phát hành; bản cũ chưa có thì hỏi API (có thể bị
        giới hạn — khi đó chỉ dựa vào HTTPS của github.com)."""
        try:
            with self.opener(f"{base}/download/{tag}/{name}.sha256") as response:
                found = re.search(r"\b([0-9a-fA-F]{64})\b", response.read(4096).decode("ascii", "ignore"))
                if found:
                    return "sha256:" + found.group(1).lower()
        except (OSError, ValueError):
            pass
        try:
            with self.opener(f"https://api.github.com/repos/{self.repo}/releases/tags/{tag}") as response:
                release = json.loads(response.read(2_000_000))
            for item in release.get("assets") or []:
                if item.get("name") == name and str(item.get("digest") or "").startswith("sha256:"):
                    return str(item["digest"])
        except (OSError, ValueError):
            pass
        return ""

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
        """Tải bộ cài: tải tiếp phần dở khi mạng rớt, thử lại; không được thì nhờ curl.exe của Windows.
        Bộ cài chỉ được chạy khi khớp kích thước và mã SHA-256 GitHub công bố."""
        asset = self.asset
        name, size = str(asset["name"]), int(asset.get("size") or 0)
        digest = str(asset.get("digest") or "")
        expected = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else ""
        self.dir.mkdir(parents=True, exist_ok=True)
        target, part = self.dir / name, self.dir / (name + ".part")
        part.unlink(missing_ok=True)
        errors = []
        for wait in (*RETRY_WAITS, None):
            try:
                size = self.download_part(asset["browser_download_url"], part, size)
                break
            except urllib.error.HTTPError:
                raise
            except (OSError, ValueError) as error:
                errors.append(str(error)[:200])
                self.log(f"Tải lỗi, thử lại (giữ phần đã tải): {str(error)[:300]}")
            if wait:
                self.sleep(wait)
        else:
            if not expected:
                part.unlink(missing_ok=True)
                raise ValueError("Không tải được bộ cài (mạng/VPN). Tắt VPN hoặc đợi mạng ổn định rồi bấm Cập nhật lại.")
            try:
                self.set(progress=0)
                self.log("Tải bằng curl.exe của Windows.")
                self.curl(asset["browser_download_url"], part, timeout=1800, resume=part.exists())
            except (OSError, subprocess.SubprocessError) as error:
                self.log(f"curl.exe cũng không tải được: {str(error)[:300]}")
                part.unlink(missing_ok=True)
                raise ValueError("Không tải được bộ cài từ GitHub (đã thử lại nhiều cách). Nếu đang bật VPN "
                                 "(1.1.1.1…), tắt VPN rồi bấm Cập nhật lại.") from None
        done = part.stat().st_size if part.exists() else 0
        if size and done != size:
            part.unlink(missing_ok=True)
            raise ValueError("Tải bộ cài chưa đủ dung lượng. Thử lại.")
        hasher = hashlib.sha256()
        with open(part, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                hasher.update(chunk)
        if expected and hasher.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise ValueError("Bộ cài tải về không khớp mã kiểm tra của GitHub. Đã hủy, không cài.")
        os.replace(part, target)
        return target

    def download_part(self, url, part, size):
        """Tải tiếp vào `part` (Range). Trả về tổng dung lượng nếu biết."""
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else None
        with (self.opener(url, timeout=60, headers=headers) if headers else self.opener(url, timeout=60)) as response:
            resumed = have and getattr(response, "status", None) == 206
            length = int(response.headers.get("Content-Length") or 0)
            total = size or ((have + length) if resumed else length)
            done = have if resumed else 0
            with open(part, "ab" if resumed else "wb") as handle:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if total:
                        self.set(progress=min(99, int(done * 100 / total)))
        if total and done < total:
            raise ValueError(f"Mạng rớt khi đang tải ({done}/{total} byte).")
        return total

    def install_worker(self):
        try:
            self.log(f"Bắt đầu tải {self.asset.get('name')} ({self.asset.get('size')} byte).")
            installer = self.download()
            self.log(f"Đã tải và kiểm tra SHA-256: {installer}")
            if self.busy():
                raise ValueError("Có tác vụ vừa bắt đầu chạy. Đợi chạy xong rồi cập nhật.")
            self.set(phase="installing", progress=100)
            try:
                (self.dir / "pending.json").write_text(json.dumps({"version": self.status()["latest"]}), encoding="utf-8")
            except OSError:
                pass
            self.launcher(installer)
            self.log("Đã chạy bộ cài; tắt app để bộ cài chép đè.")
            if self.exit_app:
                self.sleep(1.5)  # để giao diện kịp hiện "Đang cài…"
                self.exit_app()
        except (OSError, ValueError) as error:
            self.log(f"Cập nhật lỗi: {type(error).__name__}: {str(error)[:300]}")
            self.set(phase="error", error=str(error)[:300] or "Không cập nhật được.")

    def launch_installer(self, installer):
        """Chạy bộ cài im lặng, tách khỏi app (app tắt ngay sau đó); bộ cài tự mở lại app khi xong.
        Bộ cài ghi nhật ký vào data/logs/update-setup.log."""
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        setup_log = self.log_file.parent / "update-setup.log"
        subprocess.Popen([str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                          "/RELAUNCH=1", f"/LOG={setup_log}"], creationflags=flags, close_fds=True)

    def release_page(self):
        return f"https://github.com/{self.repo}/releases/latest"

    def clean_old(self):
        """Xóa bộ cài đã tải của các lần cập nhật trước."""
        try:
            for path in self.dir.glob("HanaMedia-Setup-*"):
                path.unlink(missing_ok=True)
        except OSError:
            pass
