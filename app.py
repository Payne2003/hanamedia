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
from runner.chatgpt_web import chatgpt_login_url, chatgpt_runner
from runner.engine import FLOW_URL
from runner.flow import default_chrome_profile
from runner.gemini_web import chrome_executable, login_url
from runner.profiles import SharedProfile
from runner.engine import Engine
from runner.characters import NeedsConfirm
from runner.media import MediaError, MediaInUse
from runner import license as licensing
from runner.store import Store
from runner.instance import InstanceLock
from runner.paths import data_root, frozen, resource_root
from runner.version import APP_VERSION

ROOT = resource_root()  # web/ (trong bản cài đặt: bên trong gói app)
DATA = data_root()      # mã nguồn: data/ cạnh app.py; bản cài đặt: %LOCALAPPDATA%\HanaMedia\data


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

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
        self.chatgpt_web = chatgpt_runner(self.data_dir / "profiles" / "chatgpt-web")
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

    def jobs_running(self):
        with self.store.connect() as db:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0] > 0

    def exit_for_update(self):
        """Bộ cài đã chạy: đóng Chrome của tool rồi tắt app để bộ cài chép đè (bộ cài tự mở lại app)."""
        for close in (self.engine.close_browsers, self.gemini_web.close, self.chatgpt_web.close):
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
                if os.name != "nt":
                    raise MediaError(f"Thư mục thư viện: {media.root}")
                os.startfile(media.root)
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
                 "/api/review", "/api/accounts/check", "/api/accounts/check-all"}
    MEDIA_PATHS = {"/api/media/upload", "/api/media/delete-batch", "/api/library/image-upload", "/api/image-upload",
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
                return self.reply({"ok": True, "account": account, "key": token})
            if path == "/api/license/renew":
                account, token = manager.renew(str(body.get("id")), body.get("plan"), body.get("amount"))
                return self.reply({"ok": True, "account": account, "key": token})
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
            data["support"] = self.server.store.support_bar()
            data["community"] = self.server.store.community()
            shared = self.server.shared
            data["shared_account"] = {"enabled": shared.enabled, "profile": str(shared.dir), "login_open": shared.login_open(),
                                      "profile_used": (shared.dir / "Local State").exists()}
            data["content"] = {"version": content.VERSION,
                               "web": dict(self.server.gemini_web.snapshot(),
                                           email=self.server.store.setting("gemini_web_email") or "",
                                           model=self.server.store.setting("gemini_web_model") or ""),
                               "chatgpt": dict(self.server.chatgpt_web.snapshot(),
                                               model=self.server.store.setting("chatgpt_web_model") or "")}
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
        if path == "/api/projects":
            return self.reply({"projects": self.server.store.characters.projects()})
        project_match = re.fullmatch(r"/api/project/([a-f0-9]{32})", path)
        if project_match:
            try:
                return self.reply(self.server.store.characters.project(project_match.group(1)))
            except ValueError as error:
                return self.reply({"error": str(error)}, 404)
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
                   "/logo.png": "logo.png", "/logo.svg": "logo.svg", "/favicon.ico": "favicon.ico"}
        if path not in mapping:
            return self.reply({"error": "Không tìm thấy."}, 404)
        file = ROOT / "web" / mapping[path]
        body = file.read_bytes()
        kind = {".ico": "image/x-icon", ".png": "image/png", ".svg": "image/svg+xml"}.get(file.suffix)
        self.headers_out(200, kind or (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

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
            if path == "/api/accounts":
                if "emails" in body:
                    return self.reply({"ok": True, **store.add_accounts(body["emails"], body["budget"])})
                store.add_account(body["email"], body.get("mailbox", ""), body["budget"])
            elif path == "/api/accounts/login-all":
                return self.reply({"ok": True, "count": engine.login_all(body.get("parallel", 2))})
            elif path == "/api/accounts/delete":
                if not re.fullmatch(r"[a-f0-9]{32}", str(body.get("id", ""))):
                    raise ValueError("Tài khoản không hợp lệ.")
                return self.reply({"ok": True, "email": engine.remove_account(body["id"])})
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
                    store.configure_account(body["id"], body["enabled"], body["budget"])
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
            elif path == "/api/content/web-run":
                kind = "review" if body.get("kind") == "review" else "generate"
                prompt = content.web_prompt(kind, body.get("brief", ""), body.get("video_url", ""),
                                            body.get("language", "vi"), body.get("scenes"))
                job = self.server.gemini_web.start(kind, prompt, store.setting("gemini_web_email") or "",
                                                   store.setting("gemini_web_model") or "")
                return self.reply({"ok": True, "id": job})
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
                                                    else content.DEFAULT_SECONDS))
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
            elif path == "/api/content/web-stop":
                (self.server.chatgpt_web if body.get("engine") == "chatgpt" else self.server.gemini_web).cancel()
            elif path == "/api/open-folder":
                folder = store.setting("output_folder")
                if not folder or not Path(folder).is_dir():
                    raise ValueError("Chưa có thư mục lưu.")
                if os.name != "nt":
                    raise ValueError("Nút mở thư mục này dành cho Windows.")
                os.startfile(folder)
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
        server.server_close()
        instance.close()


def main():
    if "--pick-folder" in sys.argv:  # bản cài đặt: HanaMedia.exe --pick-folder mở hộp chọn thư mục
        import folder_picker
        index = sys.argv.index("--pick-folder")
        return folder_picker.main(sys.argv[index + 1] if len(sys.argv) > index + 1 else None)
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
