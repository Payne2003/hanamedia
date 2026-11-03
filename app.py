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
from urllib.parse import unquote, urlsplit

from runner import content
from runner.gemini_web import GeminiWebRunner
from runner.engine import Engine
from runner.store import Store
from runner.instance import InstanceLock

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, store, engine):
        super().__init__(("127.0.0.1", port), Handler)
        self.store, self.engine = store, engine
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.secret = secrets.token_urlsafe(32)
        self.folder_lock = threading.Lock()
        if store.setting("gemini_api_key"):  # Chế độ Gemini API đã bỏ: không giữ khóa API cũ trên máy.
            store.save_setting("gemini_api_key", "")
        self.gemini_web = GeminiWebRunner(Path(store.path).parent / "profiles" / "gemini-web")

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
            data["content"] = {"version": content.VERSION,
                               "web": dict(self.server.gemini_web.snapshot(),
                                           email=self.server.store.setting("gemini_web_email") or "",
                                           model=self.server.store.setting("gemini_web_model") or "")}
            return self.reply(data)
        if path == "/api/library":
            return self.reply({"images": self.server.store.library_images()})
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
        mapping = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        if path not in mapping:
            return self.reply({"error": "Không tìm thấy."}, 404)
        file = ROOT / "web" / mapping[path]
        body = file.read_bytes()
        self.headers_out(200, (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.allowed(True):
            return self.reply({"error": "Yêu cầu không hợp lệ. Mở tool tại địa chỉ 127.0.0.1."}, 403)
        try:
            path = urlsplit(self.path).path
            store, engine = self.server.store, self.server.engine
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
                store.add_account(body["email"], body.get("mailbox", ""), body["budget"])
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
                store.enqueue(body["prompts"], body["cost"], body["batch"],
                              body.get("images"), body.get("mode", "text"))
                engine.changed.set()
            elif path == "/api/jobs/delete":
                store.delete_jobs(body["ids"])
                engine.changed.set()
            elif path == "/api/library/delete":
                store.delete_library_images(body["tokens"])
            elif path == "/api/library/import-flow":
                imported = store.import_flow_downloads(engine.flow_downloads)
                return self.reply({"ok": True, "imported": imported})
            elif path == "/api/flow/create-image":
                engine.create_flow_image(body["prompt"])
            elif path == "/api/start":
                engine.start(body["parallel"], body["headless"], body.get("automatic", True))
            elif path == "/api/pause":
                engine.pause()
            elif path == "/api/retry":
                store.retry(body["id"])
                engine.changed.set()
            elif path == "/api/folder":
                if not self.server.folder_lock.acquire(blocking=False):
                    raise ValueError("Cửa sổ chọn thư mục đang mở.")
                try:
                    # A separate native process avoids Tk's main-thread requirement.
                    result = subprocess.run([sys.executable, str(ROOT / "folder_picker.py")],
                                            capture_output=True, timeout=180, encoding="utf-8")
                    if result.returncode:
                        raise ValueError("Không mở được hộp chọn thư mục. Chạy tool trên Windows có giao diện màn hình.")
                    folder = result.stdout.strip()
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
            elif path == "/api/content/english-run":
                lines = content.split_scene_lines(body.get("text", ""))
                job = self.server.gemini_web.start("english", "", store.setting("gemini_web_email") or "",
                                                   store.setting("gemini_web_model") or "", lines,
                                                   (str(body.get("style") or ""),
                                                    body.get("aspect") if body.get("aspect") in content.ASPECTS else "16:9"))
                return self.reply({"ok": True, "id": job, "total": len(lines)})
            elif path == "/api/content/web-login":
                self.server.gemini_web.open_login(store.setting("gemini_web_email") or "")
            elif path == "/api/content/web-stop":
                self.server.gemini_web.cancel()
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    try:
        instance = InstanceLock(DATA / "runner.lock")
    except OSError:
        print("Tool đã chạy ở cửa sổ khác. Không mở hai bộ chạy với cùng thư mục data.")
        return 1
    store = Store(DATA / "studio.sqlite3")
    engine = Engine(store, DATA)
    try:
        server = AppServer(args.port, store, engine)
    except OSError:
        print("Cổng đang được dùng. Có thể tool đã mở. Kiểm tra http://127.0.0.1:8765")
        return 1
    print("HANA MEDIA: " + server.origin)
    print("Giữ cửa sổ này mở khi đang tạo video. Ctrl+C: dừng nhận thêm và đợi tác vụ đang chạy.")
    if not args.no_open:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Đang đợi tác vụ hiện tại kết thúc. Ctrl+C lần nữa: thoát ngay; phải kiểm tra các tác vụ đã gửi.")
        threads = engine.shutdown()
        try:
            for thread in threads:
                thread.join()
        except KeyboardInterrupt:
            pass
    finally:
        engine.close_browsers()
        server.gemini_web.close()
        server.server_close()
        instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
