"""Gemini qua API key (Google AI Studio) thay cho điều khiển trình duyệt.

GeminiAPISession có cùng các hàm mà quy trình viết content gọi trên phiên Chrome (new_chat, attach_images, ask…),
nên toàn bộ quy trình (FFmpeg cắt cảnh → Gemini phân tích → prompt tiếng Anh → kiểm duyệt) chạy y như cũ,
chỉ khác là gọi thẳng API: không phụ thuộc giao diện web, không cần Chrome.

- Video gốc/ảnh khung hình tải lên bằng Files API (resumable), chờ Google xử lý xong (ACTIVE) rồi mới hỏi.
- Không tải được video mà có link YouTube: gửi link YouTube cho Gemini xem trực tiếp.
- Hội thoại nhiều lượt: giữ lịch sử (kể cả chữ ký suy nghĩ của model) để "viết tiếp", hỏi lại cảnh thiếu.
- Khóa API chỉ gửi tới generativelanguage.googleapis.com; không ghi vào log, không trả về giao diện.
"""
import json
import mimetypes
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

from .ai_api import APIError, NoRedirect

BASE = "https://generativelanguage.googleapis.com"
KEY_PAGE = "https://aistudio.google.com/apikey"
DEFAULT_MODEL = "gemini-flash-latest"
THINKING = {"low": "Thấp", "medium": "Trung bình", "high": "Cao"}
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}")
YOUTUBE = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/)|youtu\.be/)[A-Za-z0-9_-]{6,}[^\s]*")
RETRY_WAIT = (15, 45, 90)  # 429/500/503: yêu cầu chưa được xử lý, đợi rồi gửi lại
UPLOAD_HOSTS = ("generativelanguage.googleapis.com",)


class GeminiAPIError(APIError):
    pass


def opener():
    from .net import urlopen
    return urlopen


def http(method, url, key="", payload=None, data=None, headers=None, timeout=600, want_headers=False):
    """Gọi API Google. Khóa chỉ gắn khi đích là máy chủ Gemini; không theo chuyển hướng."""
    host = urlsplit(url).hostname or ""
    if host not in UPLOAD_HOSTS:
        raise GeminiAPIError("Địa chỉ tải lên bất thường; dừng để không gửi tệp sang nơi khác.")
    headers = dict(headers or {})
    if key:
        headers["x-goog-api-key"] = key
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener()(request, timeout, handlers=(NoRedirect(),)) as response:
            body = response.read(20_000_001)
            got = {k.lower(): v for k, v in response.headers.items()}
        if len(body) > 20_000_000:
            raise GeminiAPIError("Phản hồi API quá lớn.")
        result = json.loads(body) if body.strip() else {}
        return (result, got) if want_headers else result
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read(200000) or b"{}").get("error", {}).get("message", "")
        except Exception:
            detail = ""
        raise HTTPFailure(error.code, detail) from None
    except APIError:
        raise
    except Exception as error:
        raise GeminiAPIError(f"Không kết nối được Gemini API ({type(error).__name__}). Kiểm tra mạng.") from None


class HTTPFailure(GeminiAPIError):
    MESSAGES = {400: "Gemini API không nhận yêu cầu", 401: "Gemini API không chấp nhận khóa. Kiểm tra lại API key",
                403: "API key không có quyền dùng Gemini API (hoặc chưa bật API trong dự án)",
                404: "Không tìm thấy model này. Bấm “Tải danh sách model” và chọn lại",
                429: "Đã chạm hạn mức/giới hạn tốc độ của API key", 500: "Máy chủ Gemini lỗi tạm thời",
                503: "Gemini đang quá tải"}

    def __init__(self, code, detail=""):
        self.code, self.detail = code, str(detail or "")[:300]
        text = self.MESSAGES.get(code, f"Gemini API trả lỗi HTTP {code}")
        super().__init__(text + (f": {self.detail}" if self.detail else "") + ".")


def list_models(key):
    """Các model hỗ trợ generateContent của API key này: [{id, name}]."""
    if not key:
        raise GeminiAPIError("Chưa nhập API key Gemini.")
    models, token = [], ""
    for _ in range(10):
        url = f"{BASE}/v1beta/models?pageSize=200" + (f"&pageToken={quote(token)}" if token else "")
        result = http("GET", url, key, timeout=30)
        for item in result.get("models") or []:
            if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                continue
            name = str(item.get("name") or "").removeprefix("models/")
            if MODEL_NAME.fullmatch(name) and not re.search(r"embedding|aqa|imagen|veo|tts|image-generation|live", name):
                models.append({"id": name, "name": str(item.get("displayName") or name)[:80],
                               "thinking": bool(item.get("thinking"))})
        token = result.get("nextPageToken") or ""
        if not token:
            break
    return models


class GeminiAPISession:
    """Phiên "giả trình duyệt" chạy bằng Gemini API (cùng giao diện với GeminiWebSession)."""
    api = True

    def __init__(self, key, model=DEFAULT_MODEL, thinking="", max_output=65536):
        if not key:
            raise GeminiAPIError("Chưa nhập API key Gemini trong Cài đặt.")
        if not MODEL_NAME.fullmatch(model or ""):
            raise GeminiAPIError("Tên model Gemini API không hợp lệ.")
        self.key, self.model, self.thinking, self.max_output = key, model.removeprefix("models/"), thinking, max_output
        self.history, self.pending, self.uploaded = [], [], []
        self.debug_path = None
        self.take_now = None
        self.account_seen = None
        self.closed = False

    # ---- các hàm quy trình gọi như phiên Chrome ----
    def alive(self):
        return not self.closed

    def close(self):
        self.closed = True
        self.cleanup()

    def new_chat(self):
        self.history, self.pending = [], []

    def wait_ready(self, status=None, stop=None, email="", timeout=0):
        return True

    def state(self):
        return {"count": len(self.history) // 2}

    def choose_model(self, name):
        return True  # model lấy từ Cài đặt → Gemini API

    def dump(self, reason, answer=""):
        return ""

    def attach_images(self, files, status, stop, email=""):
        for file in files:
            self.pending.append(self.upload(Path(file), status, stop))

    def ask(self, text, status, stop, email="", timeout=1200, label="Gemini đang viết"):
        parts = list(self.pending)
        if not self.history and not parts:
            link = YOUTUBE.search(text or "")
            if link:  # không có file video: cho Gemini xem thẳng link YouTube
                parts.append({"fileData": {"fileUri": link.group(0), "mimeType": "video/*"}})
        parts.append({"text": text})
        self.pending = []
        contents = self.history + [{"role": "user", "parts": parts}]
        status(f"{label} (Gemini API · {self.model})…")
        response = self.generate(contents, status, stop, timeout)
        candidate = (response.get("candidates") or [{}])[0]
        content = candidate.get("content") or {}
        answer = "".join(p.get("text", "") for p in content.get("parts") or [] if not p.get("thought"))
        reason = candidate.get("finishReason") or ""
        if not answer.strip():
            block = (response.get("promptFeedback") or {}).get("blockReason") or reason or "không rõ"
            raise GeminiAPIError(f"Gemini API không trả nội dung (lý do: {block}).")
        # Giữ nguyên phần trả lời (cả chữ ký suy nghĩ) để lượt sau "viết tiếp" đúng ngữ cảnh.
        self.history = contents + [{"role": "model", "parts": content.get("parts") or [{"text": answer}]}]
        if reason == "MAX_TOKENS":
            status("Câu trả lời chạm giới hạn độ dài · tool sẽ nhắn viết tiếp")
        return answer

    # ---- gọi API ----
    def config(self, thinking=True):
        config = {"maxOutputTokens": self.max_output, "temperature": 0.7}
        if thinking and self.thinking in THINKING:
            config["thinkingConfig"] = {"thinkingLevel": self.thinking}
        return config

    def generate(self, contents, status, stop, timeout):
        url = f"{BASE}/v1beta/models/{quote(self.model, safe='')}:generateContent"
        thinking, attempt = True, 0
        while True:
            if stop.is_set():
                raise GeminiAPIError("Đã dừng.")
            try:
                return self.call(url, {"contents": contents, "generationConfig": self.config(thinking)}, timeout, stop)
            except HTTPFailure as error:
                if error.code == 400 and thinking and re.search(r"thinking", error.detail, re.IGNORECASE):
                    thinking = False  # model không hỗ trợ cấp độ tư duy: hỏi lại không kèm (yêu cầu trước bị từ chối)
                    status("Model này không chỉnh được cấp độ tư duy · dùng mặc định của model")
                    continue
                if error.code == 400 and self.max_output > 8192 and re.search(r"max.?output|token", error.detail, re.IGNORECASE):
                    self.max_output = 8192  # model giới hạn độ dài trả lời thấp hơn: hỏi lại với giới hạn của model
                    continue
                if error.code in (429, 500, 503) and attempt < len(RETRY_WAIT):
                    wait = RETRY_WAIT[attempt]
                    attempt += 1
                    status(f"{error} Gửi lại sau {wait} giây (lần {attempt}/{len(RETRY_WAIT)})…")
                    if stop.wait(wait):
                        raise GeminiAPIError("Đã dừng.") from None
                    continue
                raise

    def call(self, url, payload, timeout, stop):
        """Chạy yêu cầu ở luồng phụ để nút Dừng có tác dụng ngay cả khi Gemini đang suy nghĩ lâu."""
        box = {}

        def run():
            try:
                box["result"] = http("POST", url, self.key, payload=payload, timeout=max(60, timeout))
            except Exception as error:  # chuyển lỗi về luồng chính
                box["error"] = error

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        while worker.is_alive():
            if stop.wait(0.5):
                raise GeminiAPIError("Đã dừng.")
        if "error" in box:
            raise box["error"]
        return box["result"]

    def upload(self, file, status, stop):
        """Files API: tải tệp lên (resumable), chờ trạng thái ACTIVE. Trả về phần fileData để gửi kèm câu hỏi."""
        if not file.is_file():
            raise GeminiAPIError(f"Không thấy tệp {file.name}.")
        size = file.stat().st_size
        mime = mimetypes.guess_type(file.name)[0] or ("video/mp4" if file.suffix.lower() == ".mp4" else "application/octet-stream")
        is_video = mime.startswith("video/")
        status(f"Đang tải {'video gốc' if is_video else file.name} lên Gemini API ({size / 1048576:.1f} MB)…")
        _, headers = http("POST", f"{BASE}/upload/v1beta/files", self.key, payload={"file": {"display_name": file.name[:120]}},
                          headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                                   "X-Goog-Upload-Header-Content-Length": str(size),
                                   "X-Goog-Upload-Header-Content-Type": mime}, timeout=60, want_headers=True)
        target = headers.get("x-goog-upload-url")
        if not target:
            raise GeminiAPIError("Gemini API không trả địa chỉ tải tệp lên.")
        with open(file, "rb") as stream:  # địa chỉ tải lên đã có mã phiên: không cần gửi lại khóa
            result = http("POST", target, data=stream, timeout=max(300, size // 200_000),
                          headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                                   "X-Goog-Upload-Command": "upload, finalize"})
        info = result.get("file") or {}
        name, uri = info.get("name") or "", info.get("uri") or ""
        if not name or not uri:
            raise GeminiAPIError("Gemini API không xác nhận tệp đã tải lên.")
        self.uploaded.append(name)
        deadline = time.monotonic() + 900
        while (info.get("state") or "ACTIVE") == "PROCESSING":
            if time.monotonic() > deadline:
                raise GeminiAPIError("Google xử lý video quá lâu (hơn 15 phút). Thử video nhẹ hơn.")
            status("Google đang xử lý video trên Gemini API…" if is_video else f"Đang xử lý {file.name}…")
            if stop.wait(3):
                raise GeminiAPIError("Đã dừng.")
            info = http("GET", f"{BASE}/v1beta/{name}", self.key, timeout=30)
        if info.get("state") == "FAILED":
            raise GeminiAPIError(f"Google không xử lý được tệp {file.name}.")
        if is_video:
            status("Đã tải video gốc lên Gemini API")
        return {"fileData": {"fileUri": uri, "mimeType": info.get("mimeType") or mime}}

    def cleanup(self):
        """Xóa tệp đã tải lên (Google tự xóa sau 48 giờ; xóa sớm cho gọn). Lỗi thì bỏ qua."""
        names, self.uploaded = self.uploaded, []
        for name in names:
            try:
                http("DELETE", f"{BASE}/v1beta/{name}", self.key, timeout=15)
            except Exception:
                pass


def test_key(key, model, thinking=""):
    """Gửi một câu hỏi rất ngắn để kiểm tra khóa + model."""
    session = GeminiAPISession(key, model or DEFAULT_MODEL, thinking, max_output=256)
    stop = threading.Event()
    answer = session.ask("Trả lời đúng một chữ: OK", lambda text: None, stop, label="Kiểm tra")
    return {"ok": True, "model": session.model, "answer": answer.strip()[:40]}
