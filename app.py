"""Local HTTP UI. Bind only 127.0.0.1; no public automation API."""
import argparse
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from runner import content
from runner.gemini_web import GeminiWebRunner
from runner import gemini_api, openai_api
from runner.chatgpt_web import chatgpt_login_url, chatgpt_runner
from runner.engine import FLOW_URL
from runner.flow import default_chrome_profile
from runner.gemini_web import chrome_executable, login_url
from runner.profiles import SharedProfile
from runner.engine import Engine
from runner.claude_web import claude_runner
from runner.characters import NeedsConfirm
from runner.media import MediaError, MediaInUse
from runner import license as licensing
from runner.store import DEFAULT_BUDGET, Store


def camera_count(custom=None):
    from runner import camera_library
    return len(camera_library.LIBRARY) + (len(camera_library.custom_angles(custom)) if custom else 0)
from runner.instance import InstanceLock
from runner.paths import data_root, frozen, resource_root
from runner.version import APP_VERSION

def open_folder(folder):
    """Mở thư mục trong Explorer (Windows) hoặc Finder (macOS). False nếu máy không hỗ trợ."""
    if os.name == "nt":
        os.startfile(folder)
        return True
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
        return True
    return False


ROOT = resource_root()  # web/ (trong bản cài đặt: bên trong gói app)
DATA = data_root()      # mã nguồn: data/ cạnh app.py; bản cài đặt: %LOCALAPPDATA%\HanaMedia\data


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    # Khóa API (Gemini, ChatGPT): lưu mã hóa trong kho khóa của máy, không trả về giao diện.
    # Import tĩnh (không dùng importlib) để PyInstaller đóng gói đủ hai module vào bản cài.
    API_ENGINES = {"gemini": (gemini_api, "gemini_api", "gemini_mode", "thinking"),
                   "chatgpt": (openai_api, "chatgpt_api", "chatgpt_mode", "reasoning")}

    def api_public(self, engine):
        module, prefix, mode_key, _ = self.API_ENGINES[engine]
        try:
            key, source = self.key_vault.credential(engine)
        except Exception:
            key, source = "", "missing"
        return {"configured": bool(key), "source": source if key else "missing",
                "model": self.store.setting(prefix + "_model") or module.DEFAULT_MODEL,
                "thinking": self.store.setting(prefix + "_thinking") or "",
                "rpm": self.api_rpm(engine),
                "mode": "api" if key and self.store.setting(mode_key, "api") == "api" else "web",
                "key_page": module.KEY_PAGE,
                # ChatGPT: viết/kiểm duyệt prompt bằng OpenAI (trả phí) hay bằng khóa Gemini (miễn phí)
                "provider": (self.store.setting("chatgpt_provider") or "openai") if engine == "chatgpt" else "gemini"}

    # Mặc định yêu cầu/phút: khóa Gemini miễn phí chỉ cho vài yêu cầu mỗi phút (Pro ít nhất); OpenAI cao hơn nhiều.
    DEFAULT_RPM = {"gemini": 5, "chatgpt": 30}

    def api_rpm(self, engine):
        try:
            saved = self.store.setting(self.API_ENGINES[engine][1] + "_rpm")
            value = self.DEFAULT_RPM[engine] if saved in (None, "") else int(saved)
        except ValueError:
            value = self.DEFAULT_RPM[engine]
        return max(0, min(1000, value))

    def api_session(self, engine):
        """Phiên API cho yêu cầu kế tiếp, hoặc None nếu đang dùng trang web trên Chrome."""
        info = self.api_public(engine)
        if engine == "chatgpt" and info["provider"] == "gemini":
            return self.gemini_writer()  # viết prompt bằng khóa Gemini miễn phí
        if info["mode"] != "api":
            return None
        module = self.API_ENGINES[engine][0]
        key, _ = self.key_vault.credential(engine)
        session = module.GeminiAPISession if engine == "gemini" else module.OpenAIAPISession
        session = session(key, info["model"], info["thinking"], rpm=info["rpm"])
        if engine == "chatgpt" and self.api_public("gemini")["configured"]:
            session.fallback = self.gemini_writer  # OpenAI hết tiền/hạn mức: tự chuyển sang Gemini chạy tiếp
        return session

    def gemini_writer(self):
        """Phiên Gemini API dùng cho bước viết/kiểm duyệt prompt (model, cấp độ tư duy, giới hạn/phút của thẻ Gemini)."""
        info = self.api_public("gemini")
        key, _ = self.key_vault.credential("gemini")
        if not key:
            raise ValueError("Chọn viết prompt bằng Gemini nhưng chưa có API key Gemini. Nhập khóa ở Cài đặt → Gemini Pro.")
        return gemini_api.GeminiAPISession(key, info["model"], info["thinking"], rpm=info["rpm"])

    def __init__(self, port, store, engine):
        super().__init__(("127.0.0.1", port), Handler)
        self.store, self.engine = store, engine
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.secret = secrets.token_urlsafe(32)
        self.folder_lock = threading.Lock()
        self.character_jobs = {}  # mã việc phân tích nhân vật -> (dự án, Scene ID theo thứ tự đã gửi)
        if store.setting("gemini_api_key"):  # Chế độ Gemini API đã bỏ: không giữ khóa API cũ trên máy.
            store.save_setting("gemini_api_key", "")
        self.data_dir = Path(store.path).parent
        self.license = licensing.LicenseManager(store, self.data_dir)
        try:  # hết hạn trong lúc đang chạy: bộ chạy ngừng nhận tác vụ mới (tác vụ đang chạy vẫn làm xong)
            engine.license_ok = lambda: self.license.allows("create_jobs")
        except AttributeError:
            pass
        from runner.updater import Updater
        self.updater = Updater(self.data_dir, busy=self.jobs_running, exit_app=self.exit_for_update)
        threading.Thread(target=self.license_watch, name="license-check", daemon=True).start()
        self.gemini_web = GeminiWebRunner(self.data_dir / "profiles" / "gemini-web")
        # Gemini API: khóa lưu mã hóa trong kho khóa của máy (không lưu dạng chữ thường, không trả về giao diện).
        from runner.ai_api import KeyVault
        self.key_vault = KeyVault(self.data_dir)
        self.gemini_web.api_session = lambda: self.api_session("gemini")
        self.chatgpt_web = chatgpt_runner(self.data_dir / "profiles" / "chatgpt-web")
        self.chatgpt_web.api_session = lambda: self.api_session("chatgpt")
        self.claude_web = claude_runner(self.data_dir / "profiles" / "claude-web")
        # Tài khoản chung: Gemini, ChatGPT, Flow dùng một hồ sơ Chrome (đăng nhập Google một lần).
        self.shared = SharedProfile(self.data_dir)
        self.gemini_web.gate = self.chatgpt_web.gate = self.shared
        self.shared.register("Gemini", self.gemini_web.release_session)
        self.shared.register("ChatGPT", self.chatgpt_web.release_session)
        if hasattr(engine, "release_flow"):
            engine.gate = self.shared
            self.shared.register("Flow", engine.release_flow)
        if store.setting("shared_google_profile") is True:
            self.apply_shared(True)
        from runner.production_agents import ProductionAgents
        from runner.video_download import VideoDownloader
        self.agents = ProductionAgents(self)
        self.downloader = VideoDownloader(self.data_dir / "production" / "sources")

    def server_close(self):
        if hasattr(self, "agents"):
            self.agents.close()
        super().server_close()

    def license_watch(self):
        """Máy khách: tải danh sách thu hồi khi mở app rồi mỗi giờ (lỗi mạng thì thử lại sau 10 phút).
        Cùng luồng: kiểm tra bản cập nhật app khi mở app rồi mỗi 6 giờ (chỉ kiểm tra, không tự cài)."""
        stop = threading.Event()
        stop.wait(3)
        self.updater.clean_old()  # bộ cài của lần cập nhật trước (đã cài xong)
        while True:
            try:
                if self.license.needs_check():
                    self.license.check_revocations()
            except Exception:  # không để luồng kiểm tra chết vì lỗi lạ (ví dụ CSDL đang bận)
                pass
            try:
                if self.updater.status()["supported"] and self.updater.due():
                    self.updater.check()
            except Exception:
                pass
            stop.wait(60)

    def log_report(self, lines=300):
        """Nhật ký lỗi để người dùng sao chép gửi hỗ trợ: phiên bản, cấu hình máy, phần cuối runner.log và update.log.
        Các file này không chứa prompt, cookie, mật khẩu hay OTP."""
        folder = self.data_dir / "logs"

        def tail(name, count):
            try:
                text = (folder / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return "(chưa có)"
            # bỏ phần backtrace địa chỉ bộ nhớ dài, vô ích khi đọc lỗi
            kept = [line for line in text.splitlines() if not line.strip().startswith("(No symbol)")]
            return "\n".join(kept[-count:]).strip() or "(trống)"
        hw = getattr(self.engine, "hardware", {}) or {}
        head = (f"HANA MEDIA {APP_VERSION} · Hệ điều hành: {'Windows' if os.name == 'nt' else 'macOS' if sys.platform == 'darwin' else sys.platform} · RAM {hw.get('ram_gb')} GB · "
                f"CPU {hw.get('cores')} nhân · tối đa {getattr(self.engine, 'max_parallel', '?')} luồng")
        text = (f"{head}\n\n===== runner.log (lỗi tác vụ) =====\n{tail('runner.log', lines)}"
                f"\n\n===== update.log (cập nhật) =====\n{tail('update.log', 40)}")
        return {"text": text, "folder": str(folder)}

    def jobs_running(self):
        with self.store.connect() as db:
            return (db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0] > 0 or
                    hasattr(self, "agents") and db.execute("SELECT COUNT(*) FROM production_runs WHERE status='running'").fetchone()[0] > 0)

    def exit_for_update(self):
        """Bộ cài đã chạy: đóng Chrome của tool rồi tắt app để bộ cài chép đè (bộ cài tự mở lại app)."""
        for close in (self.agents.close, self.engine.close_browsers, self.gemini_web.close, self.chatgpt_web.close, self.claude_web.close):
            try:
                close()
            except Exception:
                pass
        os._exit(0)

    def apply_shared(self, enabled):
        """Bật/tắt tài khoản chung. Chỉ đổi khi không phần nào đang chạy; đóng mọi Chrome tự động đang rảnh."""
        busy = [name for name, runner in (("Gemini", self.gemini_web), ("ChatGPT", self.chatgpt_web))
                if runner.snapshot().get("busy")]
        if getattr(self.engine, "flow_busy", False):
            busy.append("Flow")
        busy += [name for name in self.shared.release_all() if name not in busy]
        if busy:
            raise ValueError(", ".join(busy) + " đang chạy. Đợi chạy xong rồi đổi chế độ tài khoản.")
        self.shared.enabled = bool(enabled)
        profiles = self.data_dir / "profiles"
        self.gemini_web.profile = self.shared.dir if enabled else profiles / "gemini-web"
        self.chatgpt_web.profile = self.shared.dir if enabled else profiles / "chatgpt-web"
        if hasattr(self.engine, "flow_profile"):
            self.engine.flow_profile = self.shared.dir / "Default" if enabled else default_chrome_profile(self.data_dir)
        self.store.save_setting("shared_google_profile", bool(enabled))

    def open_shared_login(self, email="", launcher=subprocess.Popen):
        """Một cửa sổ Chrome THƯỜNG với hồ sơ chung: đăng nhập Google, rồi các tab Gemini, ChatGPT, Flow."""
        if not self.shared.enabled:
            raise ValueError("Bật “Dùng một tài khoản cho tất cả” trước.")
        if self.shared.login_open():
            raise ValueError("Cửa sổ đăng nhập đang mở. Đăng nhập xong thì đóng hẳn cửa sổ đó.")
        busy = self.shared.release_all()
        if busy:
            raise ValueError(", ".join(busy) + " đang chạy. Đợi chạy xong rồi đăng nhập.")
        self.shared.dir.mkdir(parents=True, exist_ok=True)
        self.shared.login_process = launcher([chrome_executable(), f"--user-data-dir={self.shared.dir}",
                                              "--profile-directory=Default", "--no-first-run", "--new-window",
                                              login_url(email), chatgpt_login_url(), FLOW_URL])

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not put prompt/account/OTP contents into access logs.

    def headers_out(self, code, kind, size=None, cache_control="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if size is not None:
            self.send_header("Content-Length", str(size))

    def reply(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.headers_out(code, "application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, file, content_type, disposition="inline", filename=None, cache_control="private, max-age=60"):
        """Gửi file (hỗ trợ Range cho video). filename được làm sạch và mã hóa RFC 5987 (chống chèn header)."""
        size = file.stat().st_size
        start, end, code = 0, size - 1, 200
        range_header = self.headers.get("Range")
        if range_header and size:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                return self.reply({"error": "Range không hợp lệ."}, 416)
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                start = max(0, size - int(last))
            if start >= size or start > end:
                return self.reply({"error": "Range vượt quá file."}, 416)
            code = 206
        self.headers_out(code, content_type, max(0, end - start + 1), cache_control)
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if filename:
            safe = re.sub(r'[\x00-\x1f\x7f"\\/;]', "", filename).strip() or "media"
            ascii_name = safe.encode("ascii", "ignore").decode() or "media"
            self.send_header("Content-Disposition", f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe)}")
        self.end_headers()
        try:
            with file.open("rb") as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining > 0 and (chunk := handle.read(min(1024 * 256, remaining))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def media_get(self, path):
        """GET /api/media, /api/media/item/<id>[/thumbnail|/file|/download]. Trả về True nếu đã xử lý."""
        media = self.server.store.media
        if path == "/api/media":
            query = parse_qs(urlsplit(self.path).query)
            pick = lambda name: (query.get(name) or [""])[0]
            try:
                media.sync_generated()
            except Exception:
                pass
            self.reply(media.list(pick("type") or None, pick("category") or None, pick("q"), pick("source") or None))
            return True
        match = re.fullmatch(r"/api/media/item/([a-f0-9]{32})(?:/(thumbnail|file|download))?", path)
        if not match:
            return False
        media_id, part = match.groups()
        try:
            if not part:
                self.reply(media.item(media_id))
                return True
            file, content_type, row = media.file(media_id)
        except MediaError as error:
            self.reply({"error": str(error)}, 404)
            return True
        if part == "thumbnail" and row["media_type"] != "image":
            self.reply({"error": "Video không có ảnh thu nhỏ."}, 404)
            return True
        name = row["display_name"]
        extension = Path(row["relative_path"]).suffix
        if extension and not name.lower().endswith(extension.lower()):
            name += extension
        self.send_file(file, content_type, "attachment" if part == "download" else "inline", name,
                       "private, max-age=3600" if part == "thumbnail" else "private, max-age=60")
        return True

    def media_mutate(self, method, path):
        """PATCH/DELETE /api/media/item/<id>, POST /api/media/{upload,delete-batch,open-folder}."""
        media = self.server.store.media
        try:
            if method == "POST" and path == "/api/media/upload":
                limits = media.limits()
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0:
                    raise MediaError("File trống.")
                if size > limits["max_file_mb"] * 1024 * 1024:
                    raise MediaError(f"Ảnh vượt quá {limits['max_file_mb']} MB.")
                category = self.headers.get("X-Media-Category") or "reference"
                item, duplicate = media.add_image(self.rfile.read(size), unquote(self.headers.get("X-Image-Name", "Ảnh")),
                                                  category=category)
                return self.reply({"ok": True, "duplicate": duplicate, "item": item,
                                   "token": item["id"], "type": item["mime_type"], "name": item["display_name"]})
            if self.headers.get_content_type() != "application/json":
                raise MediaError("Yêu cầu phải là JSON.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 1_000_000:
                raise MediaError("Dữ liệu quá lớn.")
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise MediaError("Dữ liệu không hợp lệ.")
            match = re.fullmatch(r"/api/media/item/([a-f0-9]{32})", path)
            if method == "PATCH" and match:
                return self.reply({"ok": True, "item": media.update(match.group(1), body)})
            if method == "DELETE" and match:
                return self.reply({"ok": True, **media.delete([match.group(1)], body.get("confirmed") is True)})
            if method == "POST" and path == "/api/media/delete-batch":
                return self.reply({"ok": True, **media.delete(body.get("ids"), body.get("confirmed") is True)})
            if method == "POST" and path == "/api/media/open-folder":
                media.root.mkdir(parents=True, exist_ok=True)
                if not open_folder(media.root):
                    raise MediaError(f"Thư mục thư viện: {media.root}")
                return self.reply({"ok": True})
            return self.reply({"error": "Không tìm thấy thao tác."}, 404)
        except MediaInUse as error:
            return self.reply({"error": str(error), "needs_confirm": True, "dependencies": error.dependencies}, 409)
        except MediaError as error:
            return self.reply({"error": str(error)}, 400)
        except (ValueError, TypeError):
            return self.reply({"error": "Dữ liệu không hợp lệ."}, 400)
        except OSError:
            return self.reply({"error": "Không ghi được file vào thư mục dữ liệu. Kiểm tra dung lượng ổ đĩa và quyền ghi."}, 500)

    # Bản quyền: chạy video / tải media cần quyền tương ứng còn hạn. Xem dữ liệu, cài đặt, kích hoạt luôn được phép.
    JOB_PATHS = {"/api/jobs", "/api/start", "/api/jobs/update", "/api/jobs/rerun", "/api/retry", "/api/jobs/fetch", "/api/batch/stitch",
                 "/api/jobs/characters", "/api/jobs/attach-characters", "/api/login", "/api/accounts/login-all",
                 "/api/review", "/api/accounts/check", "/api/accounts/check-all",
                 "/api/production/agent/run", "/api/production/agent/approve", "/api/production/agent/api-test",
                 "/api/production/agent/auto", "/api/production/agent/approve-batch", "/api/production/queue-muse"}
    MEDIA_PATHS = {"/api/production/sounds/upload", "/api/media/upload", "/api/media/delete-batch", "/api/library/image-upload", "/api/image-upload",
                   "/api/library/delete", "/api/library/import-flow"}

    def required_permission(self, method, path):
        if method in ("PATCH", "DELETE") and path.startswith("/api/media/item/"):
            return "upload_media"
        if path in self.MEDIA_PATHS:
            return "upload_media"
        if path in self.JOB_PATHS or path.startswith("/api/content/") and path not in (
                "/api/content/web-settings", "/api/content/web-stop"):
            return "create_jobs"
        return None

    def license_blocked(self, method, path):
        """Trả về True (và đã trả lời 403) nếu bản quyền không cho thao tác này."""
        permission = self.required_permission(method, path)
        if not permission:
            return False
        try:
            self.server.license.check(permission)
        except PermissionError as error:
            self.reply({"error": str(error), "license": True}, 403)
            return True
        return False

    def license_action(self, path):
        """POST /api/license/*: kích hoạt key (máy khách) và sổ bản quyền (máy Super Admin)."""
        manager = self.server.license
        size = int(self.headers.get("Content-Length", "0"))
        if self.headers.get_content_type() != "application/json" or not 0 <= size <= 100_000:
            return self.reply({"error": "Yêu cầu phải là JSON."}, 400)
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise licensing.LicenseError("Dữ liệu không hợp lệ.")
            if path == "/api/license/activate":
                return self.reply({"ok": True, "status": manager.activate(body.get("key"))})
            if path == "/api/license/deactivate":
                manager.deactivate()
                return self.reply({"ok": True})
            if path == "/api/license/init-signing":
                if body.get("confirm") is not True:
                    raise licensing.LicenseError("Cần xác nhận.")
                return self.reply({"ok": True, "public_key": manager.init_signing_key()})
            if path == "/api/license/accounts":
                account, token = manager.save_account(body)
                return self.reply({"ok": True, "account": account, "key": token, "short": manager.share_code(account["id"], token)})
            if path == "/api/license/renew":
                account, token = manager.renew(str(body.get("id")), body.get("plan"), body.get("amount"))
                return self.reply({"ok": True, "account": account, "key": token, "short": manager.share_code(account["id"], token)})
            if path == "/api/license/lock":
                account, revocation = manager.set_locked(str(body.get("id")), body.get("locked") is True)
                return self.reply({"ok": True, "account": account, "revocation": revocation})
            if path == "/api/license/delete":
                if body.get("confirm") is not True:
                    raise licensing.LicenseError("Cần xác nhận.")
                return self.reply({"ok": True, "revocation": manager.delete_account(str(body.get("id")))})
            if path == "/api/license/publisher":
                return self.reply({"ok": True, "revocation": manager.set_publisher_token(body.get("token"))})
            if path == "/api/license/publish":
                return self.reply({"ok": True, "revocation": manager.publish()})
            if path == "/api/license/check":
                result = manager.check_revocations()
                return self.reply({"ok": True, "check": result, "status": manager.status()})
            return self.reply({"error": "Không tìm thấy thao tác."}, 404)
        except (licensing.LicenseError, ValueError, TypeError) as error:
            return self.reply({"error": str(error) if isinstance(error, licensing.LicenseError) else "Dữ liệu không hợp lệ."}, 400)

    def do_PATCH(self):
        if not self.allowed(True):
            return self.reply({"error": "Yêu cầu không hợp lệ. Mở tool tại địa chỉ 127.0.0.1."}, 403)
        if self.license_blocked("PATCH", urlsplit(self.path).path):
            return
        self.media_mutate("PATCH", urlsplit(self.path).path)

    def do_DELETE(self):
        if not self.allowed(True):
            return self.reply({"error": "Yêu cầu không hợp lệ. Mở tool tại địa chỉ 127.0.0.1."}, 403)
        if self.license_blocked("DELETE", urlsplit(self.path).path):
            return
        self.media_mutate("DELETE", urlsplit(self.path).path)

    def allowed(self, mutate=False):
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            return False
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            return False
        if mutate:
            if self.headers.get("Origin") != self.server.origin:
                return False
            if not hmac.compare_digest(self.headers.get("X-Runner-Key", ""), self.server.secret):
                return False
        return True

    def do_GET(self):
        if not self.allowed():
            return self.reply({"error": "Yêu cầu phải đến từ giao diện cục bộ."}, 403)
        path = urlsplit(self.path).path
        if path == "/api/state":
            data = self.server.store.snapshot()
            data["engine"] = self.server.engine.snapshot()
            data["key"] = self.server.secret
            data["app_version"] = APP_VERSION
            status = self.server.license.status()
            data["license"] = {k: status.get(k) for k in ("state", "message", "allowed", "enforced", "role", "name",
                                                           "expires", "days_left", "issuer", "issuer_mode",
                                                           "online_check", "checked_at", "check_error")}
            update = self.server.updater.status()
            data["update"] = {k: update.get(k) for k in ("supported", "available", "latest", "phase", "progress", "error")}
            data["theme"] = self.server.store.setting("theme", "dark")
            data["support"] = self.server.store.support_bar()
            data["community"] = self.server.store.community()
            shared = self.server.shared
            data["shared_account"] = {"enabled": shared.enabled, "profile": str(shared.dir), "login_open": shared.login_open(),
                                      "profile_used": (shared.dir / "Local State").exists()}
            data["content"] = {"version": content.VERSION,
                               "api": self.server.api_public("gemini"),
                               "chatgpt_api": self.server.api_public("chatgpt"),
                               "web": dict(self.server.gemini_web.snapshot(),
                                           email=self.server.store.setting("gemini_web_email") or "",
                                           model=self.server.store.setting("gemini_web_model") or ""),
                               "chatgpt": dict(self.server.chatgpt_web.snapshot(),
                                               model=self.server.store.setting("chatgpt_web_model") or ""),
                               "review": {"rules": self.server.store.setting("review_rules") or content.DEFAULT_REVIEW_RULES,
                                          "lock": self.server.store.setting("review_lock") or content.DEFAULT_LOCK,
                                          "camera_custom": self.server.store.setting("camera_custom") or "",
                                          "camera_count": camera_count(self.server.store.setting("camera_custom") or "")}}
            return self.reply(data)
        job_match = re.fullmatch(r"/api/job/([a-f0-9]{32})", path)
        if job_match:
            try:
                job = self.server.store.job_detail(job_match.group(1))
                project = (parse_qs(urlsplit(self.path).query).get("project") or [None])[0]
                job["characters"] = self.server.store.characters.job_characters(job["id"], project)
                return self.reply(job)
            except ValueError as error:
                return self.reply({"error": str(error)}, 404)
        stitched = re.fullmatch(r"/api/batch/([a-zA-Z0-9-]{16,80})/video", path)
        if stitched:
            with self.server.store.connect() as db:
                row = db.execute("SELECT stitch_output FROM batch_options WHERE batch=? AND stitch_status='done'",
                                 (stitched.group(1),)).fetchone()
            file = Path(row["stitch_output"]) if row and row["stitch_output"] else None
            if not file or not file.is_file() or file.suffix.lower() != ".mp4":
                return self.reply({"error": "Video ghép không còn trên máy."}, 404)
            download = "download" in parse_qs(urlsplit(self.path).query)
            return self.send_file(file, "video/mp4", "attachment" if download else "inline", file.name)
        if path == "/api/logs":
            return self.reply(self.server.log_report())
        if path == "/api/update":
            return self.reply(self.server.updater.status())
        if path == "/api/license":
            manager = self.server.license
            data = {"status": manager.status(), "roles": licensing.ROLE_NAMES, "permissions": licensing.PERMISSION_NAMES,
                    "defaults": licensing.ROLE_DEFAULTS, "plans": licensing.PLAN_NAMES}
            if manager.issuer_context():
                data["dashboard"] = manager.dashboard()
            return self.reply(data)
        if path == "/api/library":
            return self.reply({"images": self.server.store.library_images()})
        if path.startswith("/api/media") and self.media_get(path):
            return
        if path == "/api/production":
            return self.reply({"projects": self.server.store.production.list_projects()})
        if path == "/api/production/download":
            return self.reply(self.server.downloader.status())
        production_match = re.fullmatch(r"/api/production/([a-f0-9]{32})", path)
        if production_match:
            try:
                return self.reply({**self.server.store.production.snapshot(production_match.group(1)),
                                   "agent_state": self.server.agents.public(production_match.group(1)),
                                   "execution_mode": "department_agents_with_manual_review"})
            except ValueError as error:
                return self.reply({"error": str(error)}, 404)
        if path == "/api/projects":
            return self.reply({"projects": self.server.store.characters.projects()})
        project_match = re.fullmatch(r"/api/project/([a-f0-9]{32})", path)
        if project_match:
            try:
                return self.reply(self.server.store.characters.project(project_match.group(1)))
            except ValueError as error:
                return self.reply({"error": str(error)}, 404)
        production_file = re.fullmatch(r"/api/production/run/([a-f0-9]{32})/(film|manifest|plan\.csv|plan\.json|report\.xlsx|"
                                       r"frame/(?:[a-z0-9]{1,32}/)?[a-z0-9-]{1,40})", path)
        if production_file:
            run_id, part = production_file.groups()
            with self.server.store.connect() as db:
                run = db.execute("SELECT project_id FROM production_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                return self.reply({"error": "Không tìm thấy lần chạy."}, 404)
            folder = self.server.agents.root / run["project_id"] / run_id
            files = {"film": ("film.mp4", "video/mp4"), "manifest": ("manifest.json", "application/json"),
                     "plan.csv": ("edit_plan.csv", "text/csv; charset=utf-8"), "plan.json": ("edit_plan.json", "application/json"),
                     "report.xlsx": ("reverse_engineering.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            relative, kind = files.get(part) or ("evidence/" + part.split("/", 1)[1] + ".jpg", "image/jpeg")
            file = (folder / relative).resolve()
            if not file.is_relative_to(folder.resolve()) or not file.is_file():
                return self.reply({"error": "Chưa có tệp đầu ra."}, 404)
            return self.send_file(file, kind, "inline" if kind in ("video/mp4", "image/jpeg") else "attachment", file.name)
        production_sound = re.fullmatch(r"/api/production/sound/([a-f0-9]{32})", path)
        if production_sound:
            try:
                file = self.server.agents.sounds.path(production_sound.group(1))
            except ValueError as error:
                return self.reply({"error": str(error)}, 404)
            kinds = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac"}
            return self.send_file(file, kinds.get(file.suffix.lower(), "application/octet-stream"), "inline", file.name)
        library_image = re.fullmatch(r"/api/library/image/([a-f0-9]{32})", path)
        if library_image:
            image = self.server.store.image_file(library_image.group(1))
            if not image:
                return self.reply({"error": "Không tìm thấy ảnh trong thư viện."}, 404)
            file, content_type = image
            body = file.read_bytes()
            self.headers_out(200, content_type, len(body), "private, max-age=60")
            self.end_headers()
            self.wfile.write(body)
            return
        image_match = re.fullmatch(r"/api/image/([a-f0-9]{32})/(\d{1,2})", path)
        if image_match:
            image = self.server.store.job_image(image_match.group(1), int(image_match.group(2)))
            if not image:
                return self.reply({"error": "Không tìm thấy ảnh của tác vụ."}, 404)
            file, content_type = image
            body = file.read_bytes()
            self.headers_out(200, content_type, len(body), "private, max-age=30")
            self.end_headers()
            self.wfile.write(body)
            return
        if re.fullmatch(r"/api/(video|media)/[a-f0-9]{32}", path):
            job_id = path.rsplit("/", 1)[1]
            with self.server.store.connect() as db:
                job = db.execute("SELECT output FROM jobs WHERE id=? AND status='done'", (job_id,)).fetchone()
            if not job or not job["output"] or not Path(job["output"]).is_file():
                return self.reply({"error": "File video không còn trong thư mục đã lưu."}, 404)
            file = Path(job["output"])
            size = file.stat().st_size
            start, end, code = 0, size - 1, 200
            range_header = self.headers.get("Range")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                if not match or not any(match.groups()):
                    return self.reply({"error": "Range không hợp lệ."}, 416)
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))
                if start >= size or start > end:
                    return self.reply({"error": "Range vượt quá file."}, 416)
                code = 206
            self.headers_out(code, "video/mp4", end - start + 1)
            self.send_header("Accept-Ranges", "bytes")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            disposition = "attachment" if path.startswith("/api/video/") else "inline"
            self.send_header("Content-Disposition", f'{disposition}; filename="{job_id[:12]}.mp4"')
            self.end_headers()
            try:
                with file.open("rb") as f:
                    f.seek(start)
                    remaining = end - start + 1
                    while remaining and (chunk := f.read(min(1024 * 256, remaining))):
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        mapping = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                   "/production.js": "production.js", "/production-agents.js": "production-agents.js",
                   "/logo.png": "logo.png", "/logo.svg": "logo.svg", "/favicon.ico": "favicon.ico"}
        if path not in mapping:
            return self.reply({"error": "Không tìm thấy."}, 404)
        file = ROOT / "web" / mapping[path]
        body = file.read_bytes()
        kind = {".ico": "image/x-icon", ".png": "image/png", ".svg": "image/svg+xml"}.get(file.suffix)
        self.headers_out(200, kind or (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def pick_video(self):
        """Hộp chọn video gốc của Windows (tiến trình riêng vì Tk cần luồng chính). Trả về đường dẫn hoặc ''."""
        if not self.server.folder_lock.acquire(blocking=False):
            raise ValueError("Hộp chọn file đang mở.")
        try:
            if frozen():
                answer = DATA / ".video-choice.txt"
                answer.unlink(missing_ok=True)
                result = subprocess.run([sys.executable, "--pick-video", str(answer)], timeout=300)
                chosen = answer.read_text(encoding="utf-8").strip() if answer.is_file() else ""
                answer.unlink(missing_ok=True)
            else:
                result = subprocess.run([sys.executable, str(ROOT / "folder_picker.py"), "--video"],
                                        capture_output=True, timeout=300, encoding="utf-8")
                chosen = (result.stdout or "").strip()
            if result.returncode:
                raise ValueError("Không mở được hộp chọn file. Chạy tool trên Windows có giao diện màn hình.")
            return chosen
        finally:
            self.server.folder_lock.release()

    def do_POST(self):
        if not self.allowed(True):
            return self.reply({"error": "Yêu cầu không hợp lệ. Mở tool tại địa chỉ 127.0.0.1."}, 403)
        try:
            path = urlsplit(self.path).path
            store, engine = self.server.store, self.server.engine
            if self.license_blocked("POST", path):
                return
            if path.startswith("/api/license/"):
                return self.license_action(path)
            if path == "/api/update/open-page":
                self.rfile.read(min(int(self.headers.get("Content-Length", "0") or 0), 10_000))
                webbrowser.open(self.server.updater.release_page())  # địa chỉ cố định của kho phát hành  # địa chỉ cố định của kho phát hành
                return self.reply({"ok": True, "url": self.server.updater.release_page()})
            if path in ("/api/update/check", "/api/update/install"):
                self.rfile.read(min(int(self.headers.get("Content-Length", "0") or 0), 10_000))
            if path == "/api/update/check":
                return self.reply({"ok": True, "update": self.server.updater.check()})
            if path == "/api/update/install":
                try:
                    return self.reply({"ok": True, "update": self.server.updater.start_install()})
                except ValueError as error:
                    return self.reply({"error": str(error)}, 400)
            if path.startswith("/api/media/"):
                return self.media_mutate("POST", path)
            if path == "/api/production/sounds/upload":
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 80 * 1024 * 1024:
                    raise ValueError("File âm thanh trống hoặc lớn hơn 80 MB.")
                item, duplicate = self.server.agents.sounds.add(self.rfile.read(size), unquote(self.headers.get("X-Sound-Name", "")),
                                                                unquote(self.headers.get("X-Sound-Tags", "")))
                return self.reply({"ok": True, "duplicate": duplicate, "sound": item})
            if path in ("/api/image-upload", "/api/library/image-upload"):
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 10_000_000:
                    raise ValueError("Ảnh phải có dung lượng tối đa 10 MB.")
                image = store.add_image(self.rfile.read(size))
                if path == "/api/library/image-upload":
                    store.add_library_image(image["token"],
                                            unquote(self.headers.get("X-Image-Name", "Ảnh tham chiếu")))
                return self.reply(image)
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Yêu cầu phải là JSON.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 5_000_000:
                raise ValueError("Dữ liệu quá lớn hoặc trống.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Dữ liệu không hợp lệ.")
            if path == "/api/production/agent/api-save":
                return self.reply(self.server.agents.api.vault.save(body.get("provider"),key=body.get("key"),model=body.get("model"),
                    max_output_tokens=body.get("max_output_tokens"),remove=body.get("remove") is True))
            elif path == "/api/production/agent/gateway-info":
                return self.reply(self.server.agents.api.relay_info())
            elif path == "/api/production/agent/gateway-save":
                return self.reply(self.server.agents.api.vault.save_gateway(body.get("url",""),body.get("token"),body.get("remove") is True))
            elif path == "/api/production/agent/api-test":
                if body.get("transport")=="gateway":
                    if not self.server.agents.gateway_enabled:
                        raise ValueError("Máy chủ trung gian chưa mở. Dùng trình duyệt hoặc API riêng trên máy này.")
                    result=self.server.agents.api.relay(body.get("provider"), 'Return only JSON: {"ok":true}.')
                    return self.reply({"ok":True,"model":result.get("model"),"usage":result.get("usage",{})})
                return self.reply(self.server.agents.api.test(body.get("provider")))
            elif path == "/api/production/agent/login":
                provider = body.get("provider")
                if provider not in self.server.agents.runners():
                    raise ValueError("AI không được hỗ trợ.")
                if provider in ("gemini", "chatgpt") and self.server.shared.enabled:
                    raise ValueError("Đang dùng hồ sơ chung. Vào Cài đặt → Đăng nhập tài khoản chung.")
                self.server.agents.runners()[provider].open_login()
                return self.reply({"ok": True})
            elif path == "/api/production/agent/configure":
                return self.reply(self.server.agents.configure(body["project_id"], body.get("config")))
            elif path == "/api/production/agent/run":
                return self.reply(self.server.agents.start(body["task_id"]))
            elif path == "/api/production/agent/approve":
                return self.reply(self.server.agents.apply(body["run_id"], human=True,
                                                          audio_confirmed=body.get("audio_confirmed") is True))
            elif path == "/api/production/agent/rework":
                return self.reply(self.server.agents.rework(body["run_id"], body.get("target"), body.get("scene_id")))
            elif path == "/api/production/agent/approve-batch":
                return self.reply(self.server.agents.approve_batch(body["project_id"], body.get("stage")))
            elif path == "/api/production/agent/auto":
                return self.reply(self.server.agents.set_auto(body["project_id"], body.get("enabled")))
            elif path == "/api/production/agent/sync-all":
                return self.reply(self.server.agents.sync_all(body["project_id"]))
            elif path == "/api/production/queue-muse":
                def enqueue(prompts, batch, scene_ids, options):
                    reserved = self.server.license.reserve_jobs(len(prompts))
                    try:
                        store.enqueue(prompts, 1, batch, None, "text", None, scene_ids, options)
                    except Exception:
                        self.server.license.refund_jobs(reserved, len(prompts))
                        raise
                try:
                    result = self.server.agents.queue_muse(body["project_id"], enqueue)
                except PermissionError as error:
                    return self.reply({"error": str(error), "license": True}, 403)
                engine.changed.set()
                return self.reply(result)
            elif path == "/api/production/sounds/update":
                self.server.agents.sounds.update(body.get("id"), body.get("name"), body.get("tags"))
                return self.reply({"ok": True, "sounds": self.server.agents.sounds.list()})
            elif path == "/api/production/sounds/delete":
                self.server.agents.sounds.delete(body.get("id"))
                return self.reply({"ok": True, "sounds": self.server.agents.sounds.list()})
            elif path == "/api/production/download":
                return self.reply(self.server.downloader.start(body.get("url")))
            elif path == "/api/production/download-cancel":
                self.server.downloader.cancel()
                return self.reply({"ok": True})
            elif path == "/api/production/pick-video":
                return self.reply({"path": self.pick_video()})
            elif path == "/api/production/agent/reject":
                return self.reply(self.server.agents.reject(body["run_id"]))
            elif path == "/api/production/agent/stop":
                return self.reply(self.server.agents.cancel())
            elif path == "/api/production/agent/sync-muse":
                return self.reply(self.server.agents.sync_muse(body["task_id"], body.get("job_id")))
            elif path == "/api/production/create" and "scenes" not in body:
                return self.reply(store.production.create(body.get("name"), body.get("brief", ""), body.get("source_video", ""),
                                                          body.get("target_seconds", 510), body.get("source_url", ""),
                                                          body.get("clip_seconds", 10)))
            elif path == "/api/production/create":
                scenes = body.get("scenes")
                if not isinstance(scenes, list) or not 1 <= len(scenes) <= 200:
                    raise ValueError("Nhập từ 1 đến 200 mô tả cảnh.")
                if any(not isinstance(scene, dict) or not str(scene.get("action", "")).strip() for scene in scenes):
                    raise ValueError("Mỗi cảnh cần mô tả hành động.")
                project_id = store.characters.create_project(body.get("name"), body.get("brief"), body.get("source_url"))
                try:
                    store.characters.add_scenes(project_id, scenes)
                    result = store.production.enable(project_id)
                except Exception:
                    store.characters.delete_project(project_id)
                    raise
                return self.reply(result)
            elif path == "/api/production/enable":
                return self.reply(store.production.enable(body["project_id"]))
            elif path == "/api/production/control":
                return self.reply(store.production.control(body["project_id"], body.get("action")))
            elif path == "/api/production/task":
                return self.reply(store.production.transition(body["task_id"], body.get("action"),
                                                             body.get("output", ""), body.get("job_id")))
            elif path == "/api/accounts":
                if "emails" in body:
                    return self.reply({"ok": True, **store.add_accounts(body["emails"], body.get("budget", DEFAULT_BUDGET))})
                store.add_account(body["email"], body.get("mailbox", ""), body.get("budget", DEFAULT_BUDGET))
            elif path == "/api/accounts/login-all":
                return self.reply({"ok": True, "count": engine.login_all(body.get("parallel", 2))})
            elif path == "/api/accounts/delete":
                if not re.fullmatch(r"[a-f0-9]{32}", str(body.get("id", ""))):
                    raise ValueError("Tài khoản không hợp lệ.")
                return self.reply({"ok": True, "email": engine.remove_account(body["id"])})
            elif path == "/api/accounts/unblock":
                store.set_video_blocked(str(body["id"]), False)
            elif path == "/api/accounts/check":
                if not re.fullmatch(r"[a-f0-9]{32}", str(body.get("id", ""))):
                    raise ValueError("Tài khoản không hợp lệ.")
                return self.reply({"ok": True, **engine.health.request([body["id"]])})
            elif path == "/api/accounts/check-all":
                if "parallel" in body:
                    engine.health.set_parallel(body["parallel"])
                return self.reply({"ok": True, **engine.health.check_all()})
            elif path == "/api/accounts/update":
                with engine.lock:
                    store.configure_account(body["id"], body["enabled"], body["budget"], body.get("threads"))
            elif path == "/api/login":
                engine.login(body["id"])
            elif path == "/api/review":
                engine.login(body["id"], review=True)
            elif path == "/api/review-close":
                engine.close_review(body["id"])
            elif path == "/api/jobs":
                count = len(body["prompts"]) if isinstance(body.get("prompts"), list) else 0
                before = len(store.snapshot()["jobs"])
                try:
                    reserved = self.server.license.reserve_jobs(count)
                except PermissionError as error:
                    return self.reply({"error": str(error), "license": True}, 403)
                try:
                    store.enqueue(body["prompts"], body["cost"], body["batch"],
                                  body.get("images"), body.get("mode", "text"), body.get("sources"),
                                  body.get("scene_ids"), body.get("options"))
                except Exception:
                    self.server.license.refund_jobs(reserved, count)
                    raise
                if len(store.snapshot()["jobs"]) == before:  # gửi lại cùng mã yêu cầu: không tính quota lần nữa
                    self.server.license.refund_jobs(reserved, count)
                engine.changed.set()
            elif path == "/api/batch/stitch":
                if not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", str(body.get("batch", ""))):
                    raise ValueError("Lượt chạy không hợp lệ.")
                engine.stitch_now(body["batch"])
            elif path == "/api/jobs/update":
                account = body.get("account_id") or None
                if account is not None and not re.fullmatch(r"[a-f0-9]{32}", str(account)):
                    raise ValueError("Tài khoản không hợp lệ.")
                job = store.update_job(body["id"], body["prompt"], body.get("details"), body.get("images"),
                                       body.get("mode", "text"), account, body.get("video"))
                engine.changed.set()
                return self.reply({"ok": True, "job": job})
            elif path in ("/api/jobs/characters", "/api/jobs/attach-characters"):
                registry = store.characters
                if path == "/api/jobs/characters":
                    registry.set_job_characters(body["id"], body.get("character_ids", []))
                else:
                    try:
                        registry.attach_job_characters(body["id"], body.get("confirm") is True)
                    except NeedsConfirm as question:
                        return self.reply({"ok": False, "confirm": str(question)})
                    engine.changed.set()
                job = store.job_detail(body["id"])
                job["characters"] = registry.job_characters(body["id"])
                return self.reply({"ok": True, "job": job})
            elif path == "/api/jobs/rerun":
                store.rerun(body["id"], bool(body.get("confirmed")))
                engine.changed.set()
            elif path == "/api/jobs/delete":
                store.delete_jobs(body["ids"])
                engine.changed.set()
            elif path == "/api/library/delete":
                store.delete_library_images(body["tokens"])
            elif path == "/api/library/import-flow":
                imported = store.import_flow_downloads(engine.flow_downloads)
                return self.reply({"ok": True, "imported": imported})
            elif path == "/api/settings/community":
                return self.reply({"ok": True, "community": store.save_community(body.get("groups"), body.get("videos"))})
            elif path == "/api/open-link":
                url = str(body.get("url") or "")
                if not store.community_link(url):
                    raise ValueError("Link không có trong trang Cộng đồng.")
                webbrowser.open(url)  # mở bằng trình duyệt mặc định của máy
            elif path == "/api/settings/support":
                return self.reply({"ok": True, "support": store.save_support_bar(body.get("message"), body.get("phone"),
                                                                                  body.get("qr"))})
            elif path == "/api/logs/open-folder":
                folder = self.server.data_dir / "logs"
                folder.mkdir(parents=True, exist_ok=True)
                if not open_folder(folder):
                    raise ValueError(f"Thư mục nhật ký: {folder}")
            elif path == "/api/settings/theme":
                if body.get("theme") not in ("dark", "light", "system"):
                    raise ValueError("Giao diện không hợp lệ.")
                store.save_setting("theme", body["theme"])
            elif path == "/api/settings/gpu":
                store.save_setting("gpu_encode", body.get("enabled") is True)
            elif path == "/api/settings/shared-account":
                self.server.apply_shared(body.get("enabled") is True)
            elif path == "/api/shared-login":
                self.server.open_shared_login(store.setting("gemini_web_email") or "")
            elif path == "/api/flow/login":
                if self.server.shared.enabled:
                    self.server.open_shared_login(store.setting("gemini_web_email") or "")
                else:
                    engine.open_flow_login()
            elif path == "/api/flow/create-image":
                engine.create_flow_image(body["prompt"])
            elif path == "/api/start":
                engine.start(body["parallel"], body["headless"], body.get("automatic", True))
            elif path == "/api/pause":
                engine.pause()
            elif path == "/api/retry":
                store.retry(body["id"])
            elif path == "/api/jobs/fetch":
                engine.fetch(str(body["id"]))
                engine.changed.set()
            elif path == "/api/folder":
                if not self.server.folder_lock.acquire(blocking=False):
                    raise ValueError("Cửa sổ chọn thư mục đang mở.")
                try:
                    # A separate native process avoids Tk's main-thread requirement.
                    if frozen():
                        answer = DATA / ".folder-choice.txt"
                        answer.unlink(missing_ok=True)
                        result = subprocess.run([sys.executable, "--pick-folder", str(answer)], timeout=180)
                        folder = answer.read_text(encoding="utf-8").strip() if answer.is_file() else ""
                        answer.unlink(missing_ok=True)
                    else:
                        result = subprocess.run([sys.executable, str(ROOT / "folder_picker.py")],
                                                capture_output=True, timeout=180, encoding="utf-8")
                        folder = (result.stdout or "").strip()
                    if result.returncode:
                        raise ValueError("Không mở được hộp chọn thư mục. Chạy tool trên Windows có giao diện màn hình.")
                    if folder:
                        target = Path(folder).resolve(strict=True)
                        if not target.is_dir():
                            raise ValueError("Thư mục không hợp lệ.")
                        probe = target / (".huy-check-" + secrets.token_hex(8))
                        try:
                            probe.write_text("", encoding="utf-8")
                        finally:
                            probe.unlink(missing_ok=True)
                        store.save_setting("output_folder", str(target))
                finally:
                    self.server.folder_lock.release()
            elif path == "/api/content/web-settings":
                email = str(body.get("email") or "").strip().lower()
                if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                    raise ValueError("Email Google không hợp lệ.")
                store.save_setting("gemini_web_email", email[:254])
                store.save_setting("gemini_web_model", str(body.get("model") or "").strip()[:40])
            elif path == "/api/content/export-xlsx":
                folder = self.server.data_dir / "exports"
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / "kich-ban.xlsx"
                content.script_workbook(body.get("scenes"), body.get("windows") if isinstance(body.get("windows"), list) else None,
                                        str(body.get("script") or "")[:40000], target, body.get("subjects"))
                return self.send_file(target, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                      "attachment", "kich-ban.xlsx", cache_control="no-store")
            elif path == "/api/content/web-run":
                kind = "review" if body.get("kind") == "review" else "generate"
                from runner.gemini_web import VIDEO_LIMIT, VIDEO_TYPES
                video_file = str(body.get("video_file") or "").strip()
                if video_file:
                    source = Path(video_file)
                    if not source.is_file():
                        raise ValueError("Không thấy file video đã chọn (có thể đã bị xóa hoặc chuyển chỗ). Chọn lại file.")
                    if source.suffix.lower() not in VIDEO_TYPES:
                        raise ValueError("Gemini chỉ nhận file video " + ", ".join(VIDEO_TYPES) + ". Hãy dùng file .mp4.")
                    if source.stat().st_size > VIDEO_LIMIT:
                        raise ValueError("File video lớn hơn 2 GB, Gemini không nhận. Dùng bản nhẹ hơn (720p/1080p).")
                prompt = content.web_prompt(kind, body.get("brief", ""), body.get("video_url", ""),
                                            body.get("language", "vi"), body.get("scenes"), attached=bool(video_file))
                job = self.server.gemini_web.start(kind, prompt, store.setting("gemini_web_email") or "",
                                                   store.setting("gemini_web_model") or "",
                                                   {"video": str(Path(video_file).resolve()), "measure": body.get("measure") is not False,
                                                    "split": "fixed" if body.get("split") == "fixed" else "shot"}
                                                   if video_file else None)
                return self.reply({"ok": True, "id": job})
            elif path == "/api/content/review-prompts":
                prompts = [line.strip() for line in str(body.get("text") or "").replace("\r", "").split("\n") if line.strip()]
                if not prompts:
                    raise ValueError("Chưa có prompt để kiểm duyệt.")
                if len(prompts) > content.MAX_EN_LINES:
                    raise ValueError(f"Tối đa {content.MAX_EN_LINES} prompt mỗi lần kiểm duyệt.")
                rules = str(body.get("rules") or "").strip()[:6000] or content.DEFAULT_REVIEW_RULES
                lock = str(body.get("lock") or "").strip()[:2000] or content.DEFAULT_LOCK
                store.save_setting("review_rules", rules)
                store.save_setting("review_lock", lock)
                chatgpt = body.get("engine") == "chatgpt"
                runner = self.server.chatgpt_web if chatgpt else self.server.gemini_web
                job = runner.start("promptreview", "", "" if chatgpt else (store.setting("gemini_web_email") or ""),
                                   store.setting("chatgpt_web_model" if chatgpt else "gemini_web_model") or "",
                                   {"prompts": prompts, "rules": rules, "lock": lock,
                                    # chia cảnh theo góc quay: thời lượng riêng từng prompt; không thì một số cho tất cả
                                    "seconds": [int(d) if isinstance(d, int) and 1 <= d <= 60 else None
                                                for d in body["durations"][:len(prompts)]]
                                    if isinstance(body.get("durations"), list) and len(body["durations"]) == len(prompts)
                                    else int(body["seconds"]) if str(body.get("seconds") or "").isdigit()
                                    and 1 <= int(body["seconds"]) <= 60 else None})
                return self.reply({"ok": True, "id": job, "total": len(prompts), "engine": "chatgpt" if chatgpt else "gemini"})
            elif path == "/api/content/camera-custom":
                store.save_setting("camera_custom", str(body.get("text") or "")[:20000])
                return self.reply({"ok": True, "count": camera_count(store.setting("camera_custom") or "")})
            elif path == "/api/content/review-rules":
                store.save_setting("review_rules", str(body.get("rules") or "").strip()[:6000])
                store.save_setting("review_lock", str(body.get("lock") or "").strip()[:2000])
            elif path == "/api/content/chatgpt-settings":
                store.save_setting("chatgpt_web_model", str(body.get("model") or "").strip()[:40])
            elif path == "/api/content/english-run":
                lines = content.split_scene_lines(body.get("text", ""))
                chatgpt = body.get("engine") == "chatgpt"
                runner = self.server.chatgpt_web if chatgpt else self.server.gemini_web
                job = runner.start("english", "", "" if chatgpt else (store.setting("gemini_web_email") or ""),
                                   store.setting("chatgpt_web_model" if chatgpt else "gemini_web_model") or "", lines,
                                                   (str(body.get("style") or ""),
                                                    body.get("aspect") if body.get("aspect") in content.ASPECTS else "16:9",
                                                    body.get("structured") is not False,
                                                    body.get("seconds") if body.get("seconds") in content.SECONDS_CHOICES
                                                    else content.DEFAULT_SECONDS,
                                                    body.get("angles") if body.get("angles") in content.ANGLE_CHOICES else 0,
                                                    body.get("library") is True,
                                                    str(store.setting("camera_custom") or "")[:20000],
                                                    content.clean_windows(body.get("windows"))))
                return self.reply({"ok": True, "id": job, "total": len(lines), "engine": "chatgpt" if chatgpt else "gemini"})
            elif path == "/api/projects/create":
                registry = store.characters
                scenes = body.get("scenes")
                if not isinstance(scenes, list) or not scenes:
                    raise ValueError("Chưa có danh sách cảnh để lưu thành dự án.")
                project_id = registry.create_project(body.get("name", ""), body.get("brief", ""),
                                                     body.get("video_url", ""), body.get("script", ""))
                try:
                    registry.add_scenes(project_id, scenes)
                except ValueError:
                    registry.delete_project(project_id)
                    raise
                return self.reply({"ok": True, "project": registry.project(project_id)})
            elif path == "/api/projects/delete":
                store.characters.delete_project(body["id"])
            elif path == "/api/characters/extract":
                project, scene_ids, scenes = store.characters.scene_inputs(body["project_id"])
                if not scenes:
                    raise ValueError("Dự án chưa có cảnh nào.")
                chatgpt = body.get("engine") == "chatgpt"
                runner = self.server.chatgpt_web if chatgpt else self.server.gemini_web
                job = runner.start("characters", project["script"], "" if chatgpt else (store.setting("gemini_web_email") or ""),
                                   store.setting("chatgpt_web_model" if chatgpt else "gemini_web_model") or "", scenes)
                # Ghi nhớ Scene ID theo đúng thứ tự gửi cho AI: C1 = cảnh đầu tiên lúc gửi, kể cả nếu sau đó sắp xếp lại.
                self.server.character_jobs[job] = (project["id"], scene_ids)
                return self.reply({"ok": True, "id": job, "engine": "chatgpt" if chatgpt else "gemini", "total": len(scenes)})
            elif path == "/api/characters/apply":
                runner = self.server.chatgpt_web if body.get("engine") == "chatgpt" else self.server.gemini_web
                job = runner.snapshot()
                pending = self.server.character_jobs.get(str(body.get("job_id", "")))
                if not pending or job.get("id") != body.get("job_id") or job.get("kind") != "characters":
                    raise ValueError("Không tìm thấy kết quả phân tích nhân vật này (có thể đã lưu rồi).")
                if job.get("busy"):
                    raise ValueError("AI vẫn đang phân tích nhân vật. Đợi xong rồi lưu.")
                if not job.get("result"):
                    raise ValueError(job.get("error") or "Không có kết quả phân tích nhân vật.")
                summary = store.characters.apply_extraction(pending[0], pending[1], job["result"])
                self.server.character_jobs.pop(body["job_id"], None)
                return self.reply({"ok": True, "summary": summary, "missing": job["result"].get("missing", []),
                                   "unknown": job["result"].get("unknown", []), "project": store.characters.project(pending[0])})
            elif path == "/api/characters/add":
                made = store.characters.add_character(body["project_id"], body.get("name", ""), body.get("description", ""),
                                                      body.get("image_prompt", ""))
                return self.reply({"ok": True, **made})
            elif path == "/api/characters/update":
                store.characters.update_character(body["id"], body.get("name"), body.get("description"), body.get("image_prompt"))
            elif path == "/api/characters/asset":
                # Ảnh nhân vật lấy từ thư viện / tải lên / tải từ Flow. approve=True: dùng luôn làm ảnh Master.
                origin = body.get("origin") if body.get("origin") in ("flow", "upload", "library") else "library"
                asset = store.characters.add_asset(body["character_id"], body["token"], origin)
                if body.get("approve") is True:
                    store.characters.approve_asset(asset)
                return self.reply({"ok": True, "asset_id": asset})
            elif path == "/api/characters/approve":
                store.characters.approve_asset(body["asset_id"])
            elif path == "/api/characters/lock":
                store.characters.set_locked(body["id"], body.get("locked") is True)
            elif path == "/api/characters/delete":
                store.characters.delete_character(body["id"])
            elif path == "/api/scenes/characters":
                store.characters.set_scene_characters(body["scene_id"], body.get("character_ids", []), source="manual")
            elif path == "/api/content/web-login" and self.server.shared.enabled:
                self.server.open_shared_login(store.setting("gemini_web_email") or "")
            elif path == "/api/content/web-login":
                if body.get("engine") == "chatgpt":
                    self.server.chatgpt_web.open_login()
                else:
                    self.server.gemini_web.open_login(store.setting("gemini_web_email") or "")
            elif path == "/api/content/web-take":
                (self.server.chatgpt_web if body.get("engine") == "chatgpt" else self.server.gemini_web).take_now()
            elif re.fullmatch(r"/api/(gemini|chatgpt)-api/(settings|key-page|models|test)", path):
                engine, action = re.fullmatch(r"/api/(gemini|chatgpt)-api/(\w+(?:-\w+)?)", path).groups()
                module, prefix, mode_key, _ = self.server.API_ENGINES[engine]
                vault = self.server.key_vault
                if action == "key-page":
                    webbrowser.open(module.KEY_PAGE)  # trang lấy khóa cố định của Google AI Studio / OpenAI
                    return self.reply({"ok": True})
                if action == "models":
                    key, _ = vault.credential(engine)
                    return self.reply({"models": module.list_models(key)})
                if action == "test":
                    key, _ = vault.credential(engine)
                    info = self.server.api_public(engine)
                    return self.reply(module.test_key(key, info["model"], info["thinking"]))
                if body.get("remove"):
                    vault.save(engine, remove=True)
                key = str(body.get("key") or "").strip()
                if key:
                    if not re.fullmatch(r"[A-Za-z0-9._-]{20,300}", key):
                        raise ValueError("API key không hợp lệ (dán đúng chuỗi khóa, không có khoảng trắng).")
                    vault.save(engine, key=key)
                    store.save_setting(mode_key, "api")  # vừa nhập khóa: dùng API ngay
                if "model" in body:
                    model = str(body.get("model") or "").strip().removeprefix("models/")
                    if model and not module.MODEL_NAME.fullmatch(model):
                        raise ValueError("Tên model không hợp lệ.")
                    store.save_setting(prefix + "_model", model)
                if "thinking" in body:
                    level = str(body.get("thinking") or "")
                    store.save_setting(prefix + "_thinking", level if level in ("low", "medium", "high") else "")
                if body.get("mode") in ("api", "web"):
                    store.save_setting(mode_key, body["mode"])
                if engine == "chatgpt" and body.get("provider") in ("openai", "gemini"):
                    store.save_setting("chatgpt_provider", body["provider"])
                if "rpm" in body:
                    rpm = body.get("rpm")
                    if type(rpm) is not int or not 0 <= rpm <= 1000:
                        raise ValueError("Số yêu cầu mỗi phút từ 0 đến 1000 (0 = không giới hạn).")
                    store.save_setting(prefix + "_rpm", str(rpm))
                return self.reply(self.server.api_public(engine))
            elif path == "/api/content/web-stop":
                (self.server.chatgpt_web if body.get("engine") == "chatgpt" else self.server.gemini_web).cancel()
            elif path == "/api/open-folder":
                folder = store.setting("output_folder")
                if not folder or not Path(folder).is_dir():
                    raise ValueError("Chưa có thư mục lưu.")
                if not open_folder(folder):
                    raise ValueError(f"Thư mục lưu: {folder}")
            else:
                return self.reply({"error": "Không tìm thấy thao tác."}, 404)
            self.reply({"ok": True})
        except (ValueError, KeyError, TypeError) as error:
            self.reply({"error": str(error) if isinstance(error, ValueError) else "Thiếu trường hoặc sai kiểu dữ liệu."}, 400)
        except subprocess.TimeoutExpired:
            self.reply({"error": "Hết thời gian chọn thư mục. Bấm Chọn thư mục để thử lại."}, 400)
        except Exception:
            self.reply({"error": "Không thực hiện được. Kiểm tra quyền ghi và trạng thái bộ chạy."}, 500)


def start_runner(port=8765):
    """Mở dữ liệu, bộ chạy và máy chủ giao diện. Trả về (server, engine, instance) hoặc nêu RuntimeError có lời nhắn."""
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        instance = InstanceLock(DATA / "runner.lock")
    except OSError:
        raise RuntimeError("HANA MEDIA đã mở ở cửa sổ khác. Không mở hai bộ chạy với cùng thư mục dữ liệu.") from None
    try:
        store = Store(DATA / "studio.sqlite3")
        engine = Engine(store, DATA)
        engine.auto_plan = True  # mở lại app: tự chia prompt đang chờ cho các tài khoản ngay
        try:
            server = AppServer(port, store, engine)
        except OSError:
            if not port:
                raise
            server = AppServer(0, store, engine)  # cổng 8765 bận: dùng cổng trống bất kỳ
    except Exception:
        instance.close()
        raise
    return server, engine, instance


def stop_runner(server, engine, instance, wait=None):
    """Dừng nhận tác vụ mới, đợi tác vụ đang chạy (tối đa `wait` giây; None = đợi hết), đóng mọi Chrome."""
    import time
    server.agents.close()
    threads = engine.shutdown()
    deadline = None if wait is None else time.monotonic() + wait
    try:
        for thread in threads:
            thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        engine.close_browsers()
        server.gemini_web.close()
        server.chatgpt_web.close()
        server.claude_web.close()
        server.server_close()
        instance.close()


def main():
    for flag in ("--pick-folder", "--pick-video"):  # bản cài đặt: HanaMedia.exe --pick-… mở hộp chọn thư mục / video
        if flag in sys.argv:
            import folder_picker
            index = sys.argv.index(flag)
            return folder_picker.main(sys.argv[index + 1] if len(sys.argv) > index + 1 else None, video=flag == "--pick-video")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    try:
        server, engine, instance = start_runner(args.port)
    except RuntimeError as error:
        print(error)
        return 1
    except OSError:
        print("Không mở được bộ chạy. Có thể tool đã mở; kiểm tra http://127.0.0.1:8765")
        return 1
    print(f"HANA MEDIA {APP_VERSION}: " + server.origin)
    print("Dữ liệu: " + str(DATA))
    print("Giữ cửa sổ này mở khi đang tạo video. Ctrl+C: dừng nhận thêm và đợi tác vụ đang chạy.")
    if not args.no_open:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Đang đợi tác vụ hiện tại kết thúc. Ctrl+C lần nữa: thoát ngay; phải kiểm tra các tác vụ đã gửi.")
    finally:
        stop_runner(server, engine, instance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
