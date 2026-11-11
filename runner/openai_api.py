"""ChatGPT qua API key (OpenAI) thay cho điều khiển trình duyệt — dùng cho Viết prompt tiếng Anh và Kiểm duyệt.

OpenAIAPISession có cùng các hàm mà quy trình gọi trên phiên Chrome (new_chat, ask…), nên vòng
Viết prompt → Kiểm tra → Tự sửa → Kiểm tra lại → Khóa prompt chạy y như cũ, chỉ khác là gọi thẳng API.
Khóa chỉ gửi tới api.openai.com; không ghi log, không trả về giao diện.
"""
import re
import threading
import urllib.error
import urllib.request
import json
from urllib.parse import urlsplit

from .ai_api import APIError, NoRedirect, image_parts

BASE = "https://api.openai.com/v1"
KEY_PAGE = "https://platform.openai.com/api-keys"
DEFAULT_MODEL = "gpt-4.1-mini"
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}")
REASONING = {"low": "Thấp", "medium": "Trung bình", "high": "Cao"}
RETRY_WAIT = (15, 45, 90)  # 429/500/502/503: yêu cầu chưa được xử lý, đợi tăng dần rồi gửi lại


class OpenAIAPIError(APIError):
    pass


class HTTPFailure(OpenAIAPIError):
    MESSAGES = {400: "ChatGPT API không nhận yêu cầu", 401: "ChatGPT API không chấp nhận khóa. Kiểm tra lại API key",
                403: "API key không có quyền dùng model này", 404: "Không tìm thấy model này. Bấm “Tải danh sách model” và chọn lại",
                429: "Đã chạm hạn mức/giới hạn tốc độ hoặc tài khoản OpenAI hết tiền", 500: "Máy chủ OpenAI lỗi tạm thời",
                502: "Máy chủ OpenAI lỗi tạm thời", 503: "OpenAI đang quá tải"}

    def __init__(self, code, detail=""):
        self.code, self.detail = code, str(detail or "")[:300]
        super().__init__(self.MESSAGES.get(code, f"ChatGPT API trả lỗi HTTP {code}") + (f": {self.detail}" if self.detail else "") + ".")


def opener():
    from .net import urlopen
    return urlopen


def http(method, url, key, payload=None, timeout=600):
    if urlsplit(url).hostname != "api.openai.com":
        raise OpenAIAPIError("Địa chỉ API bất thường; dừng để không gửi khóa sang nơi khác.")
    headers = {"Authorization": "Bearer " + key}
    data = None
    if payload is not None:
        data, headers["Content-Type"] = json.dumps(payload).encode(), "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener()(request, timeout, handlers=(NoRedirect(),)) as response:
            body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise OpenAIAPIError("Phản hồi API quá lớn.")
        return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read(200000) or b"{}").get("error", {}).get("message", "")
        except Exception:
            detail = ""
        raise HTTPFailure(error.code, detail) from None
    except APIError:
        raise
    except Exception as error:
        raise OpenAIAPIError(f"Không kết nối được ChatGPT API ({type(error).__name__}). Kiểm tra mạng.") from None


def list_models(key):
    """Model chat/văn bản của API key: [{id, name}] (bỏ model ảnh, âm thanh, embedding…)."""
    if not key:
        raise OpenAIAPIError("Chưa nhập API key ChatGPT.")
    items = http("GET", f"{BASE}/models", key, timeout=30).get("data") or []
    names = sorted({str(m.get("id") or "") for m in items}, reverse=True)
    keep = [n for n in names if MODEL_NAME.fullmatch(n) and re.match(r"(gpt-|o\d|chatgpt)", n)
            and not re.search(r"audio|realtime|tts|transcribe|image|search|embedding|moderation|instruct|codex", n)]
    return [{"id": n, "name": n} for n in keep]


class OpenAIAPISession:
    """Phiên "giả trình duyệt" chạy bằng ChatGPT API (Responses API), giữ lịch sử để viết tiếp / sửa lại."""
    api = True
    NAME = "ChatGPT"

    def __init__(self, key, model=DEFAULT_MODEL, reasoning="", max_output=32000):
        if not key:
            raise OpenAIAPIError("Chưa nhập API key ChatGPT trong Cài đặt.")
        if not MODEL_NAME.fullmatch(model or ""):
            raise OpenAIAPIError("Tên model ChatGPT API không hợp lệ.")
        self.key, self.model, self.reasoning, self.max_output = key, model, reasoning, max_output
        self.history, self.pending = [], []
        self.debug_path = self.take_now = self.account_seen = None
        self.closed = False

    def alive(self):
        return not self.closed

    def close(self):
        self.closed = True

    def new_chat(self):
        self.history, self.pending = [], []

    def wait_ready(self, status=None, stop=None, email="", timeout=0):
        return True

    def state(self):
        return {"count": len(self.history) // 2}

    def choose_model(self, name):
        return True  # model lấy từ Cài đặt → ChatGPT API

    def dump(self, reason, answer=""):
        return ""

    def attach_images(self, files, status, stop, email=""):
        videos = [f for f in files if str(f).lower().endswith((".mp4", ".mov", ".webm", ".mkv"))]
        if videos:
            raise OpenAIAPIError("ChatGPT API không xem được video. Phân tích video bằng Gemini.")
        self.pending += [{"type": "input_image", "image_url": f"data:{mime};base64,{data}", "detail": "low"}
                         for mime, data in image_parts(files)]

    def ask(self, text, status, stop, email="", timeout=1200, label="ChatGPT đang viết"):
        message = {"role": "user", "content": [{"type": "input_text", "text": text}] + self.pending}
        self.pending = []
        conversation = self.history + [message]
        status(f"{label} (ChatGPT API · {self.model})…")
        response = self.generate(conversation, status, stop, timeout)
        answer = "\n".join(c.get("text", "") for item in response.get("output") or [] if item.get("type") == "message"
                           for c in item.get("content") or [] if c.get("type") == "output_text")
        if not answer.strip():
            reason = (response.get("incomplete_details") or {}).get("reason") or (response.get("error") or {}).get("message") or "không rõ"
            raise OpenAIAPIError(f"ChatGPT API không trả nội dung (lý do: {reason}).")
        self.history = conversation + [{"role": "assistant", "content": [{"type": "output_text", "text": answer}]}]
        if response.get("status") == "incomplete":
            status("Câu trả lời chạm giới hạn độ dài · tool sẽ nhắn viết tiếp")
        return answer

    def payload(self, conversation, reasoning=True):
        body = {"model": self.model, "input": conversation, "max_output_tokens": self.max_output, "store": False}
        if reasoning and self.reasoning in REASONING:
            body["reasoning"] = {"effort": self.reasoning}
        return body

    def generate(self, conversation, status, stop, timeout):
        reasoning, attempt = True, 0
        while True:
            if stop.is_set():
                raise OpenAIAPIError("Đã dừng.")
            try:
                return self.call(self.payload(conversation, reasoning), timeout, stop)
            except HTTPFailure as error:
                if error.code == 400 and reasoning and "reasoning" in error.detail.lower():
                    reasoning = False  # model không có chế độ suy luận: hỏi lại không kèm (yêu cầu trước bị từ chối)
                    status("Model này không chỉnh được mức suy luận · dùng mặc định")
                    continue
                if error.code == 400 and self.max_output > 16000 and "max_output_tokens" in error.detail.lower():
                    self.max_output = 16000
                    continue
                if error.code in (429, 500, 502, 503) and attempt < len(RETRY_WAIT) and "quota" not in error.detail.lower():
                    wait = RETRY_WAIT[attempt]
                    attempt += 1
                    status(f"{error} Gửi lại sau {wait} giây (lần {attempt}/{len(RETRY_WAIT)})…")
                    if stop.wait(wait):
                        raise OpenAIAPIError("Đã dừng.") from None
                    continue
                raise

    def call(self, payload, timeout, stop):
        """Chạy yêu cầu ở luồng phụ để nút Dừng có tác dụng ngay."""
        box = {}

        def run():
            try:
                box["result"] = http("POST", f"{BASE}/responses", self.key, payload, timeout=max(60, timeout))
            except Exception as error:
                box["error"] = error

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        while worker.is_alive():
            if stop.wait(0.5):
                raise OpenAIAPIError("Đã dừng.")
        if "error" in box:
            raise box["error"]
        return box["result"]


def test_key(key, model, reasoning=""):
    session = OpenAIAPISession(key, model or DEFAULT_MODEL, reasoning, max_output=200)
    answer = session.ask("Reply with exactly: OK", lambda text: None, threading.Event(), label="Kiểm tra")
    return {"ok": True, "model": session.model, "answer": answer.strip()[:40]}
