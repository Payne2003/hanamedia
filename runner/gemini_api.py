"""Gemini qua API key (Google AI Studio) thay cho điều khiển trình duyệt.

GeminiAPISession có cùng các hàm mà quy trình viết content gọi trên phiên Chrome (new_chat, attach_images, ask…),
nên toàn bộ quy trình (FFmpeg cắt cảnh → Gemini phân tích → prompt tiếng Anh → kiểm duyệt) chạy y như cũ,
chỉ khác là gọi thẳng API: không phụ thuộc giao diện web, không cần Chrome.

- Video gốc/ảnh khung hình tải lên bằng Files API (resumable), chờ Google xử lý xong (ACTIVE) rồi mới hỏi.
- Không tải được video mà có link YouTube: gửi link YouTube cho Gemini xem trực tiếp.
- Hội thoại nhiều lượt: giữ lịch sử (kể cả chữ ký suy nghĩ của model) để "viết tiếp", hỏi lại cảnh thiếu.
- Khóa API chỉ gửi tới generativelanguage.googleapis.com; không ghi vào log, không trả về giao diện.
"""
import hashlib
import json
import mimetypes
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

from .ai_api import APIError, NoRedirect, Pacer

BASE = "https://generativelanguage.googleapis.com"
KEY_PAGE = "https://aistudio.google.com/apikey"
DEFAULT_MODEL = "gemini-flash-latest"
THINKING = {"low": "Thấp", "medium": "Trung bình", "high": "Cao"}
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}")
YOUTUBE = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/)|youtu\.be/)[A-Za-z0-9_-]{6,}[^\s]*")
RETRY_WAIT = (15, 45, 90)  # 429 (giới hạn theo phút): yêu cầu chưa được xử lý, đợi rồi gửi lại
REQUEST_TIMEOUT = 300  # giây chờ tối đa mỗi lần gửi (trước đây tới 20 phút); quá thì thử lại / đổi model
BUSY_WAIT = (20, 40, 60)
BUSY_COOLDOWN = 600  # model vừa báo 503 "high demand": tránh dùng nó trong 10 phút (cả ở đoạn / lượt chạy sau)
BUSY_MODELS = {}  # model -> thời điểm (time.monotonic) hết bị tránh; dùng chung mọi phiên trong app  # mọi model đều quá tải: đợi rồi thử lại cả vòng model
OVERLOAD_WAIT = (5, 15)  # 500/503 (Google quá tải): thử lại nhanh 2 lần rồi đổi sang model khác còn chỗ
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
            info = json.loads(error.read(200000) or b"{}").get("error", {})
        except Exception:
            info = {}
        raise HTTPFailure(error.code, info.get("message", ""), info.get("details") or []) from None
    except APIError:
        raise
    except Exception as error:
        import http.client
        import socket
        reason = getattr(error, "reason", None)
        if isinstance(error, (TimeoutError, socket.timeout)) or isinstance(reason, (TimeoutError, socket.timeout)):
            # Google không trả lời trong thời gian cho phép: coi như quá tải để thử lại / đổi model, không dừng hẳn.
            raise HTTPFailure(504, "Google không trả lời kịp") from None
        if isinstance(error, (ConnectionResetError, http.client.RemoteDisconnected, http.client.IncompleteRead)) or \
                isinstance(reason, ConnectionResetError):
            raise HTTPFailure(503, "kết nối bị ngắt giữa chừng") from None
        raise GeminiAPIError(f"Không kết nối được Gemini API ({type(error).__name__}). Kiểm tra mạng.") from None


def model_busy(model):
    until = BUSY_MODELS.get(model)
    if until and until > time.monotonic():
        return True
    BUSY_MODELS.pop(model, None)
    return False


class Overloaded(GeminiAPIError):
    """Mọi model đã thử đều quá tải / lỗi máy chủ: phía gọi có thể chia nhỏ yêu cầu rồi gửi lại."""


class HTTPFailure(GeminiAPIError):
    MESSAGES = {400: "Gemini API không nhận yêu cầu", 401: "Gemini API không chấp nhận khóa. Kiểm tra lại API key",
                403: "API key không có quyền dùng Gemini API (hoặc chưa bật API trong dự án)",
                404: "Không tìm thấy model này. Bấm “Tải danh sách model” và chọn lại",
                429: "Đã chạm hạn mức/giới hạn tốc độ của API key", 500: "Máy chủ Gemini báo lỗi nội bộ (500)",
                502: "Máy chủ Gemini lỗi tạm thời (502)", 503: "Gemini đang quá tải (503)",
                504: "Gemini xử lý quá lâu, hết thời gian (504)"}

    def __init__(self, code, detail="", details=()):
        self.code, self.detail = code, str(detail or "")[:300]
        # Google ghi loại hạn mức (theo phút / theo ngày) và thời gian nên đợi trong phần details.
        blob = json.dumps(details, ensure_ascii=False)[:4000] + " " + str(detail or "")
        delay = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', blob) or re.search(r"retry in (\d+(?:\.\d+)?)\s*s", blob, re.I)
        self.retry_after = min(300.0, float(delay.group(1))) if delay else None
        self.daily = bool(re.search(r"PerDay|per.?day|daily", blob, re.I))
        self.no_free_tier = bool(re.search(r"limit:\s*0\b", blob))
        self.quota = code == 429 and bool(re.search(r"quota", blob, re.I))
        text = self.MESSAGES.get(code, f"Gemini API trả lỗi HTTP {code}")
        if self.quota:
            text = ("Model này không có hạn mức miễn phí cho API key (cần bật thanh toán)" if self.no_free_tier else
                    "API key đã hết hạn mức trong ngày của model này" if self.daily else
                    "API key chạm giới hạn số yêu cầu/token mỗi phút")
        super().__init__(text + (f" ({self.detail[:160]})" if self.detail else "") + ".")


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


MAX_FPS = 5
LOW_RES = "MEDIA_RESOLUTION_LOW"  # video độ phân giải thấp: ~66 token/khung thay vì ~258 (chi tiết lấy từ ảnh khung hình)
FILE_NOTE = "[Video/ảnh đã gửi ở lượt đầu — không gửi lại để tiết kiệm token; dựa vào phần đã xem và đã viết.]"


def video_parts(part, plan=None):
    """Phần video gửi Gemini. plan = [(giây bắt đầu, giây kết thúc, khung/giây)]: đoạn hành động nhanh xem dày, đoạn
    tĩnh xem 1 khung/giây (cùng một tệp, mỗi đoạn một phần có startOffset/endOffset). Không có plan: 1 khung/giây."""
    if not plan:
        return [dict(part)]
    parts = []
    for start, end, fps in plan:
        item = dict(part)
        meta = {"startOffset": f"{max(0.0, float(start)):.2f}s", "endOffset": f"{float(end):.2f}s"}
        if fps and fps != 1:
            meta["fps"] = float(min(MAX_FPS, fps))
        item["videoMetadata"] = meta
        parts.append(item)
    return parts


class GeminiAPISession:
    """Phiên "giả trình duyệt" chạy bằng Gemini API (cùng giao diện với GeminiWebSession)."""
    api = True

    def __init__(self, key, model=DEFAULT_MODEL, thinking="", max_output=65536, rpm=0):
        if not key:
            raise GeminiAPIError("Chưa nhập API key Gemini trong Cài đặt.")
        if not MODEL_NAME.fullmatch(model or ""):
            raise GeminiAPIError("Tên model Gemini API không hợp lệ.")
        self.key, self.model, self.thinking, self.max_output = key, model.removeprefix("models/"), thinking, max_output
        self.rpm = int(rpm or 0)  # giới hạn yêu cầu/phút người dùng đặt (0 = không giãn cách)
        self.history, self.pending, self.uploaded = [], [], []
        self.usage = {"input": 0, "output": 0, "requests": 0}  # token Google báo lại (usageMetadata), cộng dồn cả phiên
        self.motion_plan = None  # [(bắt đầu, kết thúc, khung/giây)] cho video kế tiếp; None = 1 khung/giây
        self.low_res = True  # video độ phân giải thấp (tắt tự động nếu model không nhận)
        self.first_model = self.model
        self.simple_media = False  # Google báo lỗi máy chủ với video có tham số: từ đó gửi video dạng đơn giản
        self.busy_rounds = None  # số vòng chờ khi mọi model lỗi (None = đủ BUSY_WAIT); 0 = báo Overloaded ngay để chia nhỏ
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
            part = self.upload(Path(file), status, stop)
            if str(part.get("fileData", {}).get("mimeType", "")).startswith("video/"):
                parts = video_parts(part, None if self.simple_media else self.motion_plan)
                if self.low_res and not self.simple_media:
                    for item in parts:
                        item["mediaResolution"] = {"level": LOW_RES}
                self.pending += parts
            else:
                self.pending.append(part)

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
        # Giữ nguyên phần trả lời (cả chữ ký suy nghĩ) để lượt sau "viết tiếp" đúng ngữ cảnh — nhưng KHÔNG giữ video/ảnh:
        # lượt sau chỉ gửi chữ (video chỉ tính token một lần cho mỗi đoạn).
        self.history = [self.without_files(turn) for turn in contents] + [
            {"role": "model", "parts": content.get("parts") or [{"text": answer}]}]
        self.overload_tried = set()  # gửi thành công: lần quá tải sau được thử lại mọi model
        meta = response.get("usageMetadata") or {}
        used_in = int(meta.get("promptTokenCount") or 0)
        used_out = int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0)
        self.usage["input"] += used_in
        self.usage["output"] += used_out
        self.usage["requests"] += 1
        if used_in or used_out:
            status(f"Lượt này {used_in + used_out:,} token · cả lượt chạy {self.tokens():,} token".replace(",", "."))
        if reason == "MAX_TOKENS":
            status("Câu trả lời chạm giới hạn độ dài · tool sẽ nhắn viết tiếp")
        return answer

    def simplify(self, contents):
        """Bỏ tham số video (khung/giây, khúc, độ phân giải theo phần), gộp các khúc của cùng một video thành một phần.
        Trả về True nếu có thay đổi."""
        changed = False
        for turn in contents:
            parts, seen = [], set()
            for part in turn.get("parts") or []:
                if "fileData" in part and ("videoMetadata" in part or "mediaResolution" in part):
                    changed = True
                    uri = part["fileData"].get("fileUri")
                    if uri in seen:
                        continue
                    seen.add(uri)
                    part = {"fileData": part["fileData"]}
                parts.append(part)
            turn["parts"] = parts
        if changed:
            self.simple_media, self.low_res = True, False
        return changed

    def tokens(self):
        return self.usage["input"] + self.usage["output"]

    @staticmethod
    def without_files(turn):
        if turn.get("role") != "user" or not any("fileData" in p for p in turn.get("parts") or []):
            return turn
        parts = [p for p in turn["parts"] if "fileData" not in p]
        return {"role": "user", "parts": [{"text": FILE_NOTE}] + parts}

    # ---- gọi API ----
    def config(self, thinking=True):
        config = {"maxOutputTokens": self.max_output, "temperature": 0.7}
        if thinking and self.thinking in THINKING:
            config["thinkingConfig"] = {"thinkingLevel": self.thinking}
        return config

    def generate(self, contents, status, stop, timeout):
        url = f"{BASE}/v1beta/models/{quote(self.model, safe='')}:generateContent"
        thinking, attempt, overloaded = True, 0, 0
        if model_busy(self.model):
            fallback = self.overload_model()
            if fallback:
                status(f"{self.model} vừa báo quá tải ít phút trước · dùng {fallback}…")
                self.model = fallback
                url = f"{BASE}/v1beta/models/{quote(self.model, safe='')}:generateContent"
        while True:
            if stop.is_set():
                raise GeminiAPIError("Đã dừng.")
            pace = "gemini:" + hashlib.sha256(self.key.encode()).hexdigest()[:12] + ":" + self.model
            Pacer.wait(pace, self.rpm, status, stop)  # giãn cách để không vượt giới hạn yêu cầu/phút
            try:
                return self.call(url, {"contents": contents, "generationConfig": self.config(thinking)},
                                 min(timeout, REQUEST_TIMEOUT), stop)
            except HTTPFailure as error:
                if error.code == 400 and thinking and re.search(r"thinking", error.detail, re.IGNORECASE):
                    thinking = False  # model không hỗ trợ cấp độ tư duy: hỏi lại không kèm (yêu cầu trước bị từ chối)
                    status("Model này không chỉnh được cấp độ tư duy · dùng mặc định của model")
                    continue
                if error.code == 400 and re.search(r"media.?resolution", error.detail, re.IGNORECASE):
                    # Model không nhận chế độ độ phân giải thấp theo từng phần: gửi lại với độ phân giải mặc định.
                    self.low_res = False
                    for turn in contents:
                        for part in turn.get("parts") or []:
                            part.pop("mediaResolution", None)
                    status("Model này không nhận chế độ video độ phân giải thấp · dùng mặc định")
                    continue
                if error.code == 400 and self.max_output > 8192 and re.search(r"max.?output|token", error.detail, re.IGNORECASE):
                    self.max_output = 8192  # model giới hạn độ dài trả lời thấp hơn: hỏi lại với giới hạn của model
                    continue
                if error.quota and (error.daily or error.no_free_tier):
                    # Hết hạn mức ngày / model không có gói miễn phí: gửi lại vô ích. Đổi sang model dự phòng (một lần).
                    fallback = self.fallback_model()
                    if fallback:
                        status(f"{error} Chuyển sang model {fallback} để chạy tiếp…")
                        self.model = fallback
                        url = f"{BASE}/v1beta/models/{quote(self.model, safe='')}:generateContent"
                        thinking, attempt = True, 0
                        continue
                    raise GeminiAPIError(str(error) + " Đợi hạn mức đặt lại (khoảng 14–15 giờ chiều giờ Việt Nam), chọn model "
                                         "khác trong Cài đặt, hoặc bật thanh toán cho API key ở Google AI Studio.") from None
                if error.code == 404 and self.model != self.first_model and self.__dict__.get("overload_tried"):
                    # Model dự phòng không có với API key này: thử model kế tiếp trong danh sách.
                    fallback = self.overload_model()
                    if fallback:
                        status(f"Không có model {self.model} · thử {fallback}…")
                        self.model, overloaded = fallback, len(OVERLOAD_WAIT)
                        url = f"{BASE}/v1beta/models/{quote(self.model, safe='')}:generateContent"
                        pace = "gemini:" + hashlib.sha256(self.key.encode()).hexdigest()[:12] + ":" + self.model
                        continue
                if error.code in (500, 502, 503, 504) and not error.quota:
                    # Google quá tải (không phải do mình gửi nhiều): không bắt mọi yêu cầu khác cùng đợi.
                    if error.code == 503:
                        # 503 "high demand" = model đang quá đông: thử lại chính nó vài giây sau hầu như vô ích. Ghi nhớ
                        # model này đang quá tải (10 phút) và đổi NGAY sang model khác còn chỗ.
                        BUSY_MODELS[self.model] = time.monotonic() + BUSY_COOLDOWN
                        overloaded = len(OVERLOAD_WAIT)
                    if overloaded < len(OVERLOAD_WAIT):
                        wait = OVERLOAD_WAIT[overloaded]
                        overloaded += 1
                        status(f"{error} Thử lại sau {wait} giây (lần {overloaded}/{len(OVERLOAD_WAIT)})…")
                        if stop.wait(wait):
                            raise GeminiAPIError("Đã dừng.") from None
                        continue
                    if error.code != 503 and not self.simple_media and self.simplify(contents):
                        # Lỗi máy chủ lặp lại có thể do cách gửi video (tham số khung/giây, độ phân giải theo phần):
                        # gửi lại video dạng đơn giản trước khi đổi model; được thì giữ cách này cho cả lượt chạy.
                        status("Lỗi máy chủ lặp lại · thử gửi video dạng đơn giản (không tham số khung/giây, độ phân giải)…")
                        overloaded = len(OVERLOAD_WAIT) - 1
                        continue
                    fallback = self.overload_model()
                    if fallback:
                        status(f"{self.model} vẫn lỗi ({error.code}) · chuyển sang {fallback} để chạy tiếp…")
                        self.model, overloaded = fallback, 0
                        url = f"{BASE}/v1beta/models/{quote(self.model, safe='')}:generateContent"
                        pace = "gemini:" + hashlib.sha256(self.key.encode()).hexdigest()[:12] + ":" + self.model
                        continue
                    if error.code in (500, 502, 503, 504):
                        # Mọi model đã thử đều quá tải: lỗi phía Google, không phải do tool hay API key.
                        rounds = len(BUSY_WAIT) if self.busy_rounds is None else self.busy_rounds
                        if attempt < rounds:
                            wait = BUSY_WAIT[attempt]
                            attempt += 1
                            status(f"Tất cả model Gemini đã thử đều đang quá tải (phía Google) · đợi {wait} giây rồi thử "
                                   f"lại (lần {attempt}/{len(BUSY_WAIT)})…")
                            self.overload_tried = {self.model}  # đợi xong: được thử lại các model dự phòng từ đầu
                            overloaded = len(OVERLOAD_WAIT)  # thử ngay 1 lần rồi đổi model, không đợi nhanh lại
                            if stop.wait(wait):
                                raise GeminiAPIError("Đã dừng.") from None
                            continue
                        self.overload_tried = set()
                        raise Overloaded(f"Máy chủ Gemini của Google đang lỗi/quá tải ở mọi model đã thử ({error.code}). "
                                         "Các cảnh đã phân tích được giữ lại; chạy lại sau ít phút để viết phần còn thiếu.") from None
                if error.code in (429, 500, 502, 503, 504) and attempt < len(RETRY_WAIT):
                    # Giới hạn theo phút / quá tải: đợi (theo gợi ý của Google nếu có) rồi gửi lại.
                    wait = int(error.retry_after + 2) if error.retry_after else (max(RETRY_WAIT[attempt], 60 if RETRY_WAIT[attempt] else 0)
                                                                               if error.quota else RETRY_WAIT[attempt])
                    attempt += 1
                    status(f"{error} Gửi lại sau {wait} giây (lần {attempt}/{len(RETRY_WAIT)})…")
                    if error.code == 429:
                        Pacer.cool_down(pace, wait)  # giới hạn theo phút: yêu cầu kế tiếp (kể cả phiên khác) cũng đợi
                    if stop.wait(wait):
                        raise GeminiAPIError("Đã dừng.") from None
                    continue
                raise

    FALLBACKS = ("gemini-flash-latest", "gemini-flash-lite-latest")
    # Quá tải (500/503): mỗi model chạy trên cụm máy chủ riêng, thử lần lượt nhiều model hơn trước khi phải chờ.
    OVERLOAD_FALLBACKS = ("gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash-lite")

    def overload_model(self):
        """Model kế tiếp để thử khi model đang dùng quá tải. Danh sách đã thử xóa sau mỗi lần gửi thành công, nên đoạn
        sau quá tải lại vẫn được đổi model (kể cả quay về model ban đầu)."""
        tried = self.__dict__.setdefault("overload_tried", set())
        tried.add(self.model)
        names = [n for n in (self.__dict__.setdefault("first_model", self.model),) + self.OVERLOAD_FALLBACKS if n not in tried]
        names.sort(key=model_busy)  # model không vừa báo quá tải lên trước
        if names:
            tried.add(names[0])
            return names[0]
        return None

    def fallback_model(self):
        """Model dự phòng khi model đang dùng hết hạn mức: Pro → Flash → Flash-Lite (mỗi model có hạn mức riêng)."""
        tried = self.__dict__.setdefault("tried", {self.model})
        for name in self.FALLBACKS:
            if name not in tried and not (("lite" in self.model) and "lite" not in name):
                tried.add(name)
                return name
        return None

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
            status("Đã tải video lên Gemini API")
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
