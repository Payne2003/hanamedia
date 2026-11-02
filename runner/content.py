"""Viết content: Prompt + link video gốc -> Gemini -> danh sách cảnh -> hàng loạt prompt.

Luồng (theo sơ đồ):
  1. Đầu vào: yêu cầu (prompt) + link video gốc (YouTube công khai, có thể bỏ trống).
  2. Gemini phân tích, trả về kịch bản video gốc và danh sách cảnh:
     bối cảnh, góc quay, hành vi nhân vật, nội dung hành động nhân vật.
  3. Kiểm tra lại: thiếu cảnh, sai thứ tự, hay cảnh khó/không tạo được -> Gemini sửa và trả lại đủ cảnh.
  4. Giao diện ghép mỗi cảnh thành một dòng prompt để đưa vào hàng đợi video.

Chỉ dùng thư viện chuẩn (urllib). Khóa API lưu trong SQLite trên máy, không trả về giao diện.
"""
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

VERSION = "content-3"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models/"
DEFAULT_MODEL = "gemini-3.8-flash"
# Dùng lần lượt khi model đang chọn báo quá tải (500/503).
FALLBACK_MODELS = ("gemini-3.5-flash", "gemini-3-flash-preview", "gemini-flash-latest")
MAX_SCENES = 200
FIELD_LIMIT = 2000
FIELDS = ("setting", "camera", "behavior", "action")
FIELD_NAMES = {"setting": "bối cảnh", "camera": "góc quay", "behavior": "hành vi nhân vật",
               "action": "nội dung hành động nhân vật"}
LANGUAGES = {"vi": "tiếng Việt", "en": "tiếng Anh (English)"}

SCENE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "setting": {"type": "STRING", "description": "Bối cảnh: địa điểm, thời gian, ánh sáng, đạo cụ, ngoại hình nhân vật."},
        "camera": {"type": "STRING", "description": "Góc quay và chuyển động máy quay."},
        "behavior": {"type": "STRING", "description": "Hành vi, biểu cảm, trạng thái của nhân vật."},
        "action": {"type": "STRING", "description": "Nội dung hành động nhân vật diễn ra trong cảnh (kèm lời thoại nếu có)."},
    },
    "required": list(FIELDS),
    "propertyOrdering": list(FIELDS),
}
GENERATE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "original_script": {"type": "STRING", "description": "Kịch bản video gốc, kể theo thứ tự thời gian."},
        "scenes": {"type": "ARRAY", "items": SCENE_SCHEMA},
    },
    "required": ["original_script", "scenes"],
    "propertyOrdering": ["original_script", "scenes"],
}
REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "issues": {"type": "ARRAY", "items": {"type": "STRING"},
                   "description": "Các vấn đề tìm thấy và đã sửa. Rỗng nếu không có."},
        "scenes": {"type": "ARRAY", "items": SCENE_SCHEMA},
    },
    "required": ["issues", "scenes"],
    "propertyOrdering": ["issues", "scenes"],
}

SCENE_RULES = """Quy tắc cho từng cảnh:
- Mỗi cảnh là một shot liền mạch, đủ ngắn để AI video tạo trong khoảng 8 giây.
- Chia đủ cảnh theo đúng thứ tự thời gian, không bỏ sót đoạn nào của video.
- Mỗi cảnh phải tự đứng được: lặp lại ngoại hình nhân vật, trang phục, địa điểm để các cảnh nhất quán.
- Trường "setting": bối cảnh (địa điểm, thời gian, ánh sáng, đạo cụ, nhân vật trông như thế nào).
- Trường "camera": góc quay và chuyển động máy (cận cảnh, toàn cảnh, lia, đẩy, theo chân…).
- Trường "behavior": hành vi, biểu cảm, tư thế của nhân vật.
- Trường "action": nội dung hành động diễn ra trong cảnh; ghi lời thoại trong ngoặc kép nếu có.
- Không đưa tên người thật/người nổi tiếng, logo, thương hiệu, chữ trên màn hình; mô tả bằng đặc điểm chung.
- Không viết trường nào trống. Không xuống dòng trong một trường."""


def youtube_url(value):
    """Chuẩn hóa link YouTube công khai. Trả về "" nếu bỏ trống."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        url = urlparse(value if "://" in value else "https://" + value)
    except ValueError:
        raise ValueError("Link video gốc không hợp lệ.")
    host = (url.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    video_id = ""
    if host == "youtu.be":
        video_id = url.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if url.path == "/watch":
            video_id = (parse_qs(url.query).get("v") or [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live|v)/([^/]+)/?", url.path)
            video_id = match.group(1) if match else ""
    if url.scheme not in ("http", "https") or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Gemini chỉ đọc trực tiếp link YouTube công khai (youtube.com/watch, youtu.be, /shorts). "
                         "Với TikTok/Facebook, hãy đăng video lên YouTube ở chế độ công khai hoặc không công khai rồi dán link.")
    return "https://www.youtube.com/watch?v=" + video_id


def model_name(value):
    value = (value or DEFAULT_MODEL).strip().removeprefix("models/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{1,79}", value):
        raise ValueError("Tên model Gemini không hợp lệ, ví dụ: gemini-3.8-flash.")
    return value


def api_key_value(value):
    # Bỏ khoảng trắng, xuống dòng và dấu ngoặc dính theo khi sao chép khóa.
    value = re.sub(r"\s+", "", str(value or "")).strip("\"'`“”‘’<>")
    if value.lower().startswith("key="):
        value = value[4:]
    # Google đã đổi định dạng khóa (AIza..., AQ....): chỉ cần ký tự in được, không khoảng trắng.
    if not re.fullmatch(r"[\x21-\x7e]{20,300}", value):
        raise ValueError("Khóa API Gemini không hợp lệ: khóa phải dài ít nhất 20 ký tự, không dấu tiếng Việt. "
                         "Sao chép lại khóa tại aistudio.google.com/apikey.")
    return value


def clean_text(value, limit=FIELD_LIMIT):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def clean_scenes(scenes):
    if not isinstance(scenes, list):
        raise ValueError("Danh sách cảnh không hợp lệ.")
    result = []
    for scene in scenes[:MAX_SCENES]:
        if not isinstance(scene, dict):
            continue
        item = {field: clean_text(scene.get(field)) for field in FIELDS}
        if any(item.values()):
            result.append(item)
    return result


def local_issues(scenes):
    """Kiểm tra không cần Gemini: cảnh thiếu trường hoặc quá dài."""
    issues = []
    if not scenes:
        issues.append("Chưa có cảnh nào.")
    for index, scene in enumerate(scenes, 1):
        missing = [FIELD_NAMES[field] for field in FIELDS if not scene.get(field)]
        if missing:
            issues.append(f"Cảnh {index} thiếu: {', '.join(missing)}.")
        if sum(len(scene.get(field, "")) for field in FIELDS) > 1800:
            issues.append(f"Cảnh {index} quá dài, nên tách thành 2 cảnh.")
    return issues


def scene_prompt(index, scene):
    """Một dòng prompt theo cấu trúc trong sơ đồ."""
    return (f"Bối cảnh {index}: {scene['setting']}, góc quay: {scene['camera']}, "
            f"hành vi nhân vật: {scene['behavior']}, Nội dung hành động nhân vật: {scene['action']};")


def format_prompts(scenes):
    return [scene_prompt(index, scene) for index, scene in enumerate(scenes, 1)]


def default_transport(url, payload, headers, timeout):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class Gemini:
    def __init__(self, api_key, model=DEFAULT_MODEL, transport=default_transport, timeout=600):
        self.api_key, self.model = api_key_value(api_key), model_name(model)
        self.transport, self.timeout = transport, timeout
        self.sleep, self.retry_delay = time.sleep, 8

    def ask(self, text, schema, video_url=""):
        parts = []
        if video_url:
            parts.append({"file_data": {"file_uri": video_url, "mime_type": "video/*"}})
        parts.append({"text": text})
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json",
                                 "responseSchema": schema},
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        models = [self.model] + [model for model in FALLBACK_MODELS if model != self.model]
        # Hai vòng: nếu mọi model đều quá tải, đợi rồi thử lại một lần.
        for attempt, model in enumerate(models + models):
            if attempt == len(models):
                self.sleep(self.retry_delay)
            try:
                status, body = self.transport(API_ROOT + model + ":generateContent", payload, headers, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ValueError("Không kết nối được Gemini. Kiểm tra Internet rồi thử lại.")
            if status not in (500, 503):
                break
        self.used_model = model
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        if status != 200:
            message = clean_text((data.get("error") or {}).get("message"), 300)
            if status in (401, 403) or "API key" in message:
                raise ValueError("Gemini từ chối khóa API. Kiểm tra lại khóa trong mục Cài đặt Gemini.")
            if status == 404:
                raise ValueError(f"Model {self.model} không dùng được với khóa này. Đổi model trong Cài đặt Gemini "
                                 f"(khuyên dùng {DEFAULT_MODEL}). {message}".strip())
            if status == 429:
                raise ValueError("Gemini báo hết hạn mức (429). Đợi một lúc hoặc dùng khóa/model khác.")
            if status in (500, 503):
                raise ValueError("Gemini đang quá tải (lỗi tạm thời). Đợi một lát rồi bấm lại.")
            if status == 400 and video_url:
                raise ValueError("Gemini không đọc được video. Video phải công khai/không công khai trên YouTube. " + message)
            raise ValueError(f"Gemini lỗi {status}. {message}".strip())
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise ValueError(f"Gemini chặn yêu cầu ({blocked}). Sửa lại yêu cầu hoặc dùng video khác.")
        candidates = data.get("candidates") or []
        if not candidates:
            raise ValueError("Gemini không trả về nội dung. Thử lại.")
        candidate = candidates[0]
        text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", []))
        try:
            result = json.loads(text)
        except ValueError:
            reason = candidate.get("finishReason")
            if reason == "MAX_TOKENS":
                raise ValueError("Kịch bản quá dài so với giới hạn trả lời của Gemini. Thử video ngắn hơn hoặc yêu cầu ít cảnh hơn.")
            raise ValueError("Gemini trả về dữ liệu không đúng định dạng. Bấm thử lại.")
        if not isinstance(result, dict):
            raise ValueError("Gemini trả về dữ liệu không đúng định dạng. Bấm thử lại.")
        return result

    def generate(self, brief, video_url="", language="vi"):
        brief = clean_text(brief, 20000)
        if not brief:
            raise ValueError("Nhập yêu cầu (prompt) cho Gemini.")
        video_url = youtube_url(video_url)
        language = LANGUAGES.get(language, LANGUAGES["vi"])
        source = ("Xem kỹ video gốc đính kèm từ đầu đến cuối. Viết lại kịch bản video gốc và chia toàn bộ video thành các cảnh."
                  if video_url else
                  "Không có video gốc. Tự viết kịch bản video gốc theo yêu cầu rồi chia thành các cảnh.")
        text = (f"Bạn là biên kịch và đạo diễn hình ảnh cho video tạo bằng AI.\n{source}\n\n"
                f"Yêu cầu của người dùng:\n{brief}\n\n{SCENE_RULES}\n\n"
                f"Viết toàn bộ nội dung bằng {language}. Trả về JSON đúng schema: "
                "original_script là kịch bản video gốc; scenes là danh sách cảnh.")
        result = self.ask(text, GENERATE_SCHEMA, video_url)
        scenes = clean_scenes(result.get("scenes"))
        if not scenes:
            raise ValueError("Gemini chưa chia được cảnh nào. Kiểm tra link video hoặc viết yêu cầu rõ hơn.")
        return {"original_script": str(result.get("original_script") or "").strip()[:40000],
                "scenes": scenes, "issues": local_issues(scenes)}

    def review(self, brief, video_url, scenes, language="vi"):
        scenes = clean_scenes(scenes)
        if not scenes:
            raise ValueError("Chưa có cảnh để kiểm tra.")
        video_url = youtube_url(video_url)
        language = LANGUAGES.get(language, LANGUAGES["vi"])
        compare = ("So sánh danh sách cảnh với video gốc đính kèm: tìm đoạn bị bỏ sót, cảnh sai thứ tự, mô tả sai so với video."
                   if video_url else "So sánh danh sách cảnh với yêu cầu: tìm phần còn thiếu hoặc mạch truyện bị đứt.")
        text = (f"Bạn là người kiểm duyệt kịch bản cho video tạo bằng AI.\n{compare}\n"
                "Đồng thời tìm cảnh mà AI video khó hoặc không tạo được: trường bị trống, quá nhiều hành động trong một cảnh, "
                "người thật/người nổi tiếng, logo/thương hiệu, chữ trên màn hình, nội dung bạo lực/nhạy cảm dễ bị từ chối. "
                "Sửa các cảnh đó (viết lại, tách, hoặc thêm cảnh còn thiếu vào đúng vị trí).\n\n"
                f"Yêu cầu gốc của người dùng:\n{clean_text(brief, 20000) or '(không có)'}\n\n"
                f"Danh sách cảnh hiện tại (JSON):\n{json.dumps(scenes, ensure_ascii=False)}\n\n{SCENE_RULES}\n\n"
                f"Viết bằng {language}. Trả về JSON: issues là các vấn đề đã tìm thấy và cách sửa (rỗng nếu không có); "
                "scenes là TOÀN BỘ danh sách cảnh sau khi sửa, theo đúng thứ tự.")
        result = self.ask(text, REVIEW_SCHEMA, video_url)
        fixed = clean_scenes(result.get("scenes")) or scenes
        issues = [clean_text(item, 500) for item in (result.get("issues") or []) if clean_text(item, 500)]
        return {"scenes": fixed, "issues": issues[:50], "local_issues": local_issues(fixed)}
