"""Viết content: Prompt + link video gốc -> Gemini Pro (gemini.google.com) -> danh sách cảnh -> hàng loạt prompt.

Luồng (theo sơ đồ):
  1. Đầu vào: yêu cầu (prompt) + link video gốc (YouTube, có thể bỏ trống).
  2. web_prompt() soạn yêu cầu; runner/gemini_web.py dán vào Gemini và lấy câu trả lời.
  3. parse_answer() tách kịch bản video gốc và các cảnh:
     bối cảnh, góc quay, hành vi nhân vật, nội dung hành động nhân vật.
  4. Kiểm tra lại (web_prompt kind="review"): thiếu cảnh, sai thứ tự, cảnh khó/không tạo được.
  5. format_prompts() ghép mỗi cảnh thành một dòng prompt để đưa vào hàng đợi video.
"""
import json
import re
from urllib.parse import parse_qs, urlparse

VERSION = "content-20"
MAX_SCENES = 200
FIELD_LIMIT = 2000
FIELDS = ("setting", "camera", "behavior", "action")
FIELD_NAMES = {"setting": "bối cảnh", "camera": "góc quay", "behavior": "hành vi nhân vật",
               "action": "nội dung hành động nhân vật"}
LANGUAGES = {"vi": "tiếng Việt", "en": "tiếng Anh (English)"}

DEFAULT_SECONDS = 15  # Thời lượng mặc định của mỗi cảnh / mỗi video.
SECONDS_CHOICES = (5, 8, 10, 15)

SCENE_RULES = f"""Quy tắc cho từng cảnh:
- Mỗi cảnh là một shot liền mạch, đủ để AI video tạo trong khoảng {DEFAULT_SECONDS} giây.
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
                         "Với TikTok/Facebook…, bấm “⬇ Tải video từ link” để tool tải về và đính kèm file cho Gemini.")
    return "https://www.youtube.com/watch?v=" + video_id


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
    part = {field: re.sub(r"[\s.,;]+$", "", scene[field]) for field in FIELDS}
    return (f"Bối cảnh {index}: {part['setting']}, góc quay: {part['camera']}, "
            f"hành vi nhân vật: {part['behavior']}, Nội dung hành động nhân vật: {part['action']};")


def format_prompts(scenes):
    return [scene_prompt(index, scene) for index, scene in enumerate(scenes, 1)]


# ---- Chế độ "Gemini trên web": tool soạn yêu cầu, người dùng tự dán vào gemini.google.com rồi dán kết quả về ----
WEB_FORMAT = """Trả lời theo đúng định dạng sau, không thêm bảng, không dùng markdown:
KỊCH BẢN VIDEO GỐC:
(kịch bản video gốc, kể theo thứ tự thời gian)

DANH SÁCH CẢNH:
Bối cảnh 1: ..., góc quay: ..., hành vi nhân vật: ..., Nội dung hành động nhân vật: ...;
Bối cảnh 2: ..., góc quay: ..., hành vi nhân vật: ..., Nội dung hành động nhân vật: ...;
(mỗi cảnh đúng một dòng, đánh số liên tục, viết đủ tất cả cảnh, không tóm tắt, không bỏ dòng nào)"""


# Gemini có thể nhớ các cuộc trò chuyện trước: mỗi yêu cầu nói rõ là độc lập để không lẫn kịch bản/cảnh cũ.
ISOLATION = ("ĐÂY LÀ YÊU CẦU MỚI, ĐỘC LẬP. Chỉ dùng video gốc và nội dung trong tin nhắn này. KHÔNG dùng nhân vật, cảnh, "
             "kịch bản hay prompt từ bất kỳ cuộc trò chuyện, dự án hoặc video nào trước đó.")


def shot_section(shots, duration=0):
    """Bảng điểm cắt do tool đo trên file video: Gemini phải viết đúng 1 cảnh cho mỗi shot (không bỏ sót)."""
    shots = list(shots or [])[:MAX_SCENES]
    if not shots:
        return ""
    clock = lambda s: f"{int(s // 60):02d}:{s % 60:05.2f}"
    rows = "\n".join(f"Shot {i}: {clock(s['start'])} – {clock(s['end'])} ({s['end'] - s['start']:.1f} giây)"
                      for i, s in enumerate(shots, 1))
    return (f"BẢNG ĐIỂM CẮT (tool đo trên từng khung hình của file video{f', dài {clock(duration)}' if duration else ''}): "
            f"video có {len(shots)} shot.\n{rows}\n"
            f"Viết ĐÚNG {len(shots)} cảnh: Bối cảnh 1 = Shot 1, Bối cảnh 2 = Shot 2 … Bối cảnh {len(shots)} = Shot {len(shots)}. "
            "Mỗi shot đúng một dòng cảnh, đúng thứ tự thời gian; không gộp shot, không bỏ shot nào kể cả shot rất ngắn. "
            "Mô tả đúng những gì thấy trong khoảng thời gian của shot đó.")


def web_prompt(kind, brief, video_url="", language="vi", scenes=None, attached=False):
    """Yêu cầu để dán vào ứng dụng Gemini trên web. attached=True: file video gốc được đính kèm vào tin nhắn."""
    language = LANGUAGES.get(language, LANGUAGES["vi"])
    if attached:
        try:
            video_url = youtube_url(video_url)
        except ValueError:
            video_url = ""  # link TikTok/Facebook… đã được tải về và đính kèm file: không cần link nữa
    else:
        video_url = youtube_url(video_url)
    has_video = attached or bool(video_url)
    brief = str(brief or "").strip()[:20000]
    if kind == "review":
        scenes = clean_scenes(scenes or [])
        if not scenes:
            raise ValueError("Chưa có cảnh để kiểm tra.")
        task = ("Bạn là người kiểm duyệt kịch bản cho video tạo bằng AI. "
                + ("Xem lại video gốc và so sánh với danh sách cảnh bên dưới: tìm đoạn bị bỏ sót, cảnh sai thứ tự, mô tả sai. "
                   if has_video else "So sánh danh sách cảnh với yêu cầu: tìm phần còn thiếu hoặc mạch truyện bị đứt. ")
                + "Đồng thời sửa cảnh mà AI video khó hoặc không tạo được (trường trống, quá nhiều hành động, người nổi tiếng, "
                "logo, chữ trên màn hình, nội dung dễ bị từ chối). Thêm cảnh còn thiếu vào đúng vị trí.")
        body = (f"{task}\n\nYêu cầu gốc:\n{brief or '(không có)'}\n\nDanh sách cảnh hiện tại:\n"
                + "\n".join(format_prompts(scenes))
                + "\n\nĐầu câu trả lời, ghi các vấn đề đã sửa dưới tiêu đề VẤN ĐỀ: (mỗi vấn đề một dòng bắt đầu bằng dấu -). "
                "Sau đó viết lại TOÀN BỘ danh sách cảnh đã sửa.")
    else:
        if not brief:
            raise ValueError("Nhập yêu cầu (prompt) cho Gemini.")
        source = ("Xem kỹ FILE VIDEO GỐC ĐÍNH KÈM trong tin nhắn này từ giây đầu tiên đến giây cuối cùng, viết lại kịch bản "
                  "video gốc và chia toàn bộ video thành các cảnh." if attached else
                  "Xem kỹ video gốc từ đầu đến cuối, viết lại kịch bản video gốc và chia toàn bộ video thành các cảnh."
                  if video_url else "Không có video gốc. Tự viết kịch bản theo yêu cầu rồi chia thành các cảnh.")
        body = f"Bạn là biên kịch và đạo diễn hình ảnh cho video tạo bằng AI. {source}\n\nYêu cầu:\n{brief}"
    parts = [ISOLATION, body]
    if attached:
        parts.append("Video gốc: file video đính kèm trong tin nhắn này.")
    elif video_url:
        parts.append(f"Video gốc: {video_url}")
    parts += [SCENE_RULES.replace('Trường "setting"', "Bối cảnh").replace('Trường "camera"', "Góc quay")
              .replace('Trường "behavior"', "Hành vi nhân vật").replace('Trường "action"', "Nội dung hành động nhân vật"),
              f"Viết bằng {language}.", WEB_FORMAT]
    return "\n\n".join(parts)


SCENE_LINE = re.compile(
    r"b[ốo]i\s*c[ảa]nh\s*(\d+)\s*[:：]\s*(?P<setting>.*?)[,;]\s*g[óo]c\s*quay\s*[:：]\s*(?P<camera>.*?)[,;]\s*"
    r"h[àa]nh\s*vi(?:\s*nh[âa]n\s*v[ậa]t)?\s*[:：]\s*(?P<behavior>.*?)[,;]\s*"
    r"n[ộo]i\s*dung(?:\s*h[àa]nh\s*[đd][ộo]ng)?(?:\s*nh[âa]n\s*v[ậa]t)?\s*[:：]\s*(?P<action>.*?)\s*;?\s*$",
    re.IGNORECASE)


def parse_answer(text):
    """Đọc câu trả lời dán từ Gemini web: kịch bản, vấn đề và các dòng cảnh (hoặc JSON)."""
    text = str(text or "").replace("\r", "")[:2_000_000]
    if not text.strip():
        raise ValueError("Dán câu trả lời của Gemini vào ô trước.")
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            scenes = clean_scenes(data.get("scenes"))
            if scenes:
                return {"original_script": str(data.get("original_script") or "").strip()[:40000], "scenes": scenes,
                        "issues": [clean_text(i, 500) for i in data.get("issues") or [] if clean_text(i, 500)][:50]}
        except (ValueError, AttributeError):
            pass
    # Nhiều cảnh dính trên một dòng ("...; Bối cảnh 2: ...") khi trang gộp các dòng: tách trước mỗi "Bối cảnh N:".
    text = re.sub(r"(?<=\S)[ \t]*(?=b[ốo]i\s*c[ảa]nh\s*\d+\s*[:：])", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\S)[ \t]*(?=(?:HẾT DANH SÁCH|END OF LIST|DANH SÁCH CẢNH\s*[:：]))", "\n", text)
    scenes, script, issues, section = [], [], [], "script"
    for raw in text.split("\n"):
        line = re.sub(r"[*_#`>|]+", "", raw).strip()
        line = re.sub(r"^[-•–]\s*", "", line) if not line.startswith("- ") or section != "issues" else line
        if not line:
            continue
        match = SCENE_LINE.search(line)
        if match:
            scenes.append({field: clean_text(match.group(field)) for field in FIELDS})
            section = "scenes"
            continue
        head = line.lower()
        if re.match(r"^v[ấa]n\s*[đd][ềe]", head):
            section = "issues"
            rest = re.sub(r"^[^:：]*[:：]\s*", "", line) if re.search(r"[:：]", line) else ""
            if rest:
                issues.append(clean_text(rest, 500))
            continue
        if re.match(r"^(k[ịi]ch\s*b[ảa]n|danh\s*s[áa]ch\s*c[ảa]nh)", head):
            section = "scenes" if head.startswith("danh") else "script"
            if section == "script" and not scenes:
                script = []  # Bỏ câu dẫn trước tiêu đề "KỊCH BẢN".
            rest = re.sub(r"^[^:：]*[:：]\s*", "", line) if re.search(r"[:：]", line) else ""
            if rest and section == "script":
                script.append(rest)
            continue
        if section == "issues":
            issues.append(clean_text(line.lstrip("- "), 500))
        elif section == "script" and not scenes:
            script.append(line)
    if not scenes:
        raise ValueError("Không tìm thấy dòng cảnh nào. Mỗi cảnh cần dạng: Bối cảnh 1: ..., góc quay: ..., "
                         "hành vi nhân vật: ..., Nội dung hành động nhân vật: ...;")
    return {"original_script": "\n".join(script).strip()[:40000], "scenes": scenes[:MAX_SCENES],
            "issues": [i for i in issues if i][:50]}


# ---- Prompt tiếng Anh: danh sách "Bối cảnh N: ..." -> mỗi cảnh một prompt tiếng Anh (theo sơ đồ thứ hai) ----
END_EN = "END OF LIST"
MAX_EN_LINES = 300
VIET_CHARS = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.IGNORECASE)


SHOT_HEADER = re.compile(r"^\W*(?:shot|c[ảa]nh|scene)\s*\d{1,3}\s*[—–:\-.]", re.IGNORECASE)


def split_scene_lines(text):
    """Tách đầu vào thành từng cảnh: mỗi dòng một cảnh, hoặc nhiều "Bối cảnh N:" nối bằng dấu ;"""
    text = str(text or "").replace("\r", "").strip()
    text = re.sub(r"^\s*(input\s*:)?\s*\[", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\]\s*$", "", text)
    text = re.sub(r";\s*(?=b[ốo]i\s*c[ảa]nh\s*\d)", ";\n", text, flags=re.IGNORECASE)
    raw = [line.strip() for line in text.split("\n")]
    if any(SHOT_HEADER.match(line) for line in raw):
        # Báo cáo phân tích theo shot (MASTER PROMPT V3.0): mỗi khối "SHOT N — …" nhiều dòng là MỘT cảnh.
        blocks = []
        for line in raw:
            if SHOT_HEADER.match(line) or not blocks:
                blocks.append([])
            if line:
                blocks[-1].append(re.sub(r"^[*•\-–]\s*", "", line))
        lines = [" | ".join(block) for block in blocks if block]
    else:
        lines = [line for line in raw if line]
    lines = [re.sub(r"\s+", " ", line).strip() for line in lines]
    if not lines:
        raise ValueError("Chưa có cảnh nào. Dán danh sách “Bối cảnh 1: …” hoặc lấy từ danh sách cảnh.")
    if len(lines) > MAX_EN_LINES:
        raise ValueError(f"Tối đa {MAX_EN_LINES} cảnh mỗi lần.")
    return [line[:FIELD_LIMIT * 2] for line in lines]


def is_english(line):
    """Prompt coi là tiếng Anh khi gần như không còn chữ có dấu tiếng Việt."""
    return len(VIET_CHARS.findall(line)) <= 2


ASPECTS = {"16:9": "16:9 widescreen (horizontal) frame", "9:16": "9:16 vertical frame", "1:1": "1:1 square frame"}


# Khung prompt chuẩn: mỗi prompt tự chứa đủ trường có nhãn, để tool tách lại trường từ chính prompt.
# MASTER PROMPT 08 — VIẾT PROMPT MUSE: các trường AI phải viết (theo đúng thứ tự). STYLE, DURATION, ASPECT RATIO do tool
# tự ghi theo thông số chuẩn để không bao giờ sai.
STRUCT_FIELDS = (("setting", "SETTING"), ("characters", "CHARACTERS"), ("camera", "CAMERA"),
                 ("behavior", "CHARACTER BEHAVIOR"), ("action", "ACTION"), ("ending", "ENDING FRAME"))
MUSE_STYLE = ("Ultra-photorealistic, natural wildlife documentary footage, authentic animal anatomy and behavior, natural "
              "daylight, real-time 1x, non-cinematic, natural ambient ASMR only, no narration, no music, no slow motion, "
              "no artificial effects, no text, no watermark")
STRUCT_LABEL = re.compile(r"(?:(?<=^)|(?<=[\s;|.,(\[]))(setting|camera(?:\s*angle)?|character\s*behaviou?r|behaviou?r|characters?"
                          r"|action|ending\s*frame|style|duration|aspect\s*ratio)\s*[:：]", re.IGNORECASE)
# Đuôi kỹ thuật cuối prompt: "15 seconds", "16:9 aspect ratio" (một hoặc cả hai, theo thứ tự bất kỳ).
ASPECT_TAIL = re.compile(r"(?:[\s;,.]*(?:\d{1,3}\s*(?:seconds?|secs?|s|giây)|\d{1,2}\s*:\s*\d{1,2}\s*aspect\s*ratio))+\.?\s*$",
                         re.IGNORECASE)


def parse_structured(prompt):
    """Tách "Setting: ...; Camera: ...; Character behavior: ...; Action: ...; Style: ..." thành các trường.

    Thuật toán: tìm vị trí mọi nhãn đã biết, giá trị của một nhãn là đoạn chữ từ sau dấu ":" đến trước nhãn kế
    tiếp; bỏ dấu ngăn cách (; | ,) và đuôi "16:9 aspect ratio". Trả về {} nếu không có nhãn nào.
    """
    text = str(prompt or "")
    marks = list(STRUCT_LABEL.finditer(text))
    fields = {}
    for index, mark in enumerate(marks):
        label = re.sub(r"\s+", " ", mark.group(1).lower())
        key = ("camera" if label.startswith("camera") else "behavior" if "behavio" in label else
               "characters" if label.startswith("character") else "ending" if label.startswith("ending") else
               "aspect" if label.startswith("aspect") else label)
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        value = ASPECT_TAIL.sub("", text[mark.end():end]).strip(" \t;|,")
        if value and key not in fields:
            fields[key] = value[:FIELD_LIMIT * 2]
    return fields


def structured_complete(prompt):
    fields = parse_structured(prompt)
    return all(fields.get(key) for key, _ in STRUCT_FIELDS)


def muse_prompt(prompt, seconds=8, aspect="16:9", style=""):
    """Dựng lại prompt Muse theo đúng cấu trúc MASTER PROMPT 08 từ các trường AI viết; STYLE/DURATION/ASPECT RATIO chuẩn.
    Thiếu trường bắt buộc thì trả nguyên văn (tool sẽ hỏi lại riêng cảnh đó)."""
    fields = parse_structured(prompt)
    if not all(fields.get(key) for key, _ in STRUCT_FIELDS):
        return prompt
    parts = [f"{label}: {fields[key].strip().rstrip('.;')}" for key, label in STRUCT_FIELDS]
    extra = str(style or "").strip().rstrip(".;")
    parts.append("STYLE: " + MUSE_STYLE + (f", {extra}" if extra else ""))
    if seconds:
        parts.append(f"DURATION: {seconds} seconds")
    if aspect in ASPECTS:
        parts.append(f"ASPECT RATIO: {aspect}")
    return "; ".join(parts) + "."


ANGLE_CHOICES = (0, 3)  # 0: theo kịch bản (tối đa 3 góc, Master Prompt 08); 3: đổi góc máy mỗi 2–3 giây
SEGMENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*s?\s*(?:[–—\-]|to)\s*(\d+(?:[.,]\d+)?)\s*(?:s|sec|seconds?)\b", re.IGNORECASE)


def camera_segments(prompt):
    """Các mốc góc máy trong trường CAMERA: [(bắt đầu, kết thúc), …] theo thứ tự xuất hiện."""
    camera = parse_structured(prompt).get("camera", "")
    return [(float(a.replace(",", ".")), float(b.replace(",", "."))) for a, b in SEGMENT.findall(camera)]


def angle_problem(prompt, seconds, step=3):
    """Kiểm tra chế độ đổi góc mỗi 2–3 giây: mốc phủ kín 0→hết clip, nối liền, mỗi góc 2–3 giây (góc cuối có thể
    ngắn hơn một chút). Trả về lý do sai, hoặc "" nếu đúng."""
    segments = camera_segments(prompt)
    if len(segments) < 2:
        return "CAMERA chưa chia góc máy theo mốc thời gian"
    if abs(segments[0][0]) > .25 or abs(segments[-1][1] - seconds) > .25:
        return f"mốc góc máy không phủ đủ 0–{seconds}s"
    for (start, end), following in zip(segments, segments[1:] + [None]):
        if end <= start:
            return "mốc góc máy sai thứ tự"
        if following and abs(following[0] - end) > .25:
            return "các góc máy không nối liền nhau"
        if end - start > step + .5 or (end - start < 1.5 and following):
            return f"có góc máy dài {end - start:g}s (cần 2–{step}s)"
    return ""


ISSUE_LINE = re.compile(r"^\W*ISSUE\s*[:：]\s*(.+)$", re.IGNORECASE)


def parse_issues(text):
    """Mâu thuẫn AI báo theo mục 9 của MASTER PROMPT 08 (dòng "ISSUE: Scene N — ...")."""
    issues = []
    for raw in str(text or "").replace("\r", "").split("\n"):
        match = ISSUE_LINE.match(re.sub(r"[*_`#>]+", "", raw).strip())
        if match and match.group(1).strip() not in issues:
            issues.append(match.group(1).strip()[:500])
    return issues


def angle_plan(seconds, step=3):
    """Mốc gợi ý chia clip thành các góc 2–3 giây, ví dụ 10s → 0–3s, 3–5s, 5–8s, 8–10s."""
    marks, at = [0], 0
    while seconds - at > step:
        at += 3 if (seconds - at - 3) != 1 and len(marks) % 2 else 2
        marks.append(at)
    marks.append(seconds)
    return ", ".join(f"{a:g}–{b:g}s" for a, b in zip(marks, marks[1:]))


def english_request(lines, numbers=None, style="", aspect="16:9", structured=True, seconds=8, angles=0):
    """Yêu cầu gửi Gemini/ChatGPT: viết prompt Muse tiếng Anh theo MASTER PROMPT 08 (numbers=None: tất cả cảnh)."""
    numbers = numbers or list(range(1, len(lines) + 1))
    partial = len(numbers) < len(lines)
    head = ("Some scene prompts are missing, incomplete or not in English. Write prompts ONLY for the scenes listed below, "
            "keeping their original numbers." if partial else
            f"Below is the approved detailed script: {len(lines)} scenes, written in Vietnamese.")
    timing = (f"0–3s first angle (initial action), 3–5s second angle (movement or reaction), 5–8s third angle (result and end "
              f"state)" if seconds == 8 else f"split the {seconds} seconds proportionally across the angles")
    rules = (
        "MASTER PROMPT 08 — WRITE MUSE PROMPTS TO RECONSTRUCT A WILDLIFE VIDEO.\n"
        "Role: prompt engineering expert for Muse, writing realistic wildlife documentary video prompts.\n"
        "Video target: 4K UHD, 16:9, real-time 1x, ultra-photorealistic BBC Natural History-style wildlife documentary, "
        "natural light, natural ambient sound only; no cinematic effects, no slow motion, no music, no dialogue or "
        "narration, no subtitles, text or watermark.\n"
        f"{head}\n"
        "If a scene is a source-shot analysis (SHOT N with time range, setting, characters, camera, opening/middle/"
        "ending action, transition, link to next shot): reconstruct THAT shot faithfully; the clip is longer than the "
        "source shot, so continue the same state and movement for the whole clip without adding events, and use the "
        "ending action and the link to the next shot for ENDING FRAME. Unknown facts (e.g. screen direction) must not be "
        "invented: write \"[unverified: ...]\" in the field and add an ISSUE line.\n"
        "Task: turn EACH approved scene into ONE English Muse prompt. Do not create a new story or change the script; if "
        "important data is missing, do not invent it as if observed.\n"
        "Reconstruction rules: keep the script content and the verified action order; do not add or remove animals; keep "
        "species, count, size and identifying marks; no unreasonable change of terrain, light or weather; keep left/right/"
        "front/back positions; state movement direction and distance between animals; never swap predator and prey; no "
        "unrealistic action or wrong anatomy; no added climax, conflict or different ending.\n"
        "Action rules: state the initial state, who acts, the main movement, direction and speed, reactions of the other "
        "animals and the end state. Prefer ONE clear main action per clip. Runs, chases and attacks move at the natural "
        "speed of the species, no slow motion.\n"
        + (("Camera rules: CHANGE THE CAMERA ANGLE EVERY 2–3 SECONDS. The CAMERA field MUST be a timeline covering the whole "
            f"clip from 0 to {seconds}s, each angle 2–3 seconds, consecutive, written like \"{angle_plan(seconds, angles)}\" "
            "with one angle per mark separated by \" / \" (for example \"0–3s: wide shot, static, from the left / 3–5s: "
            "side tracking medium shot …\"). Use a different angle for each mark (extreme wide, wide, medium, close-up, "
            "extreme close-up, low-angle, high-angle, frontal, side tracking, rear tracking, aerial wide). ACTION follows "
            "the same time marks so each angle shows a different moment of the same continuous action. A new angle must "
            "never reverse movement direction or move the animals.\n") if angles else
           ("Camera rules: at most 3 angles, only when the script needs them (extreme wide, wide, medium, close-up, extreme "
            "close-up, low-angle, high-angle, frontal, side tracking, rear tracking, aerial wide). With 3 angles: " + timing + ". "
            "For continuous action prefer \"One continuous shot, no cuts\". A new angle must never reverse movement direction "
            "or move the animals.\n")) +
        "Continuity locks — compare each scene with the previous and next scene: CHARACTER LOCK (species, count, looks, "
        "marks), ENVIRONMENT LOCK (terrain, plants, rocks, water, light, fixed objects), POSITION LOCK (positions, order, "
        "direction, distance), ACTION LOCK (start pose, movement, reaction, end state), CONTINUITY LOCK (the end of the "
        "previous scene matches the start of this one). Animals never appear, vanish, turn or teleport by themselves.\n"
        "Output format — exactly ONE line per scene, these labels in this order, all required, separated by \"; \":\n"
        "  SCENE N — SETTING: <environment, terrain, time, light, fixed objects>; CHARACTERS: <species, count, looks, "
        "position, distance>; CAMERA: <shot size, angle, camera position and movement, time marks if several angles>; "
        "CHARACTER BEHAVIOR: <posture, reactions, body movement, biologically correct behavior>; ACTION: <the action in "
        f"clear order from start to end, fitting {seconds} seconds>; ENDING FRAME: <position, posture, direction and state "
        "of each animal at the end of the clip>\n"
        "- Do not write STYLE, DURATION or ASPECT RATIO: the tool adds the standard ones. Never use a label inside "
        "another field. Concise, specific, action-focused.\n"
        "- Quality check before answering: script followed, reference followed, species and count, consistent looks, "
        "direction and distance, clear and plausible action, not too many actions, camera does not move animals, end "
        "state connects to the next scene, nothing invented. If you find a contradiction or a scene too complex for one "
        "clip, keep the scene and add a line after the last scene: ISSUE: Scene N — <problem>.\n"
        "- Do not merge, split, skip or renumber scenes. No headings, no markdown, no comments.\n"
        f"- After the last line write exactly: {END_EN}")
    if aspect in ASPECTS:
        rules += f"\n- Compose every shot for a {ASPECTS[aspect]}."
    if style.strip():
        rules += f"\nExtra style notes from the user (apply inside the fields, keep the rules above): {style.strip()[:2000]}"
    if not structured:
        rules = rules.replace("exactly ONE line per scene, these labels", "exactly ONE line per scene, preferably these labels")
    scenes = "\n".join(f"{number}. {lines[number - 1]}" for number in numbers)
    return f"{rules}\n\nScenes:\n{scenes}"


def continue_english(after, total):
    return (f"Continue with scene {after + 1} to scene {total}, exactly the same format (one line per scene, starting with "
            f"SCENE and its number, same labels in the same order). Do not repeat earlier scenes. After scene {total} write "
            f"exactly: {END_EN}")


EN_LINE = re.compile(r"^\W*(?:scene|prompt|c[ảa]nh|b[ốo]i\s*c[ảa]nh)?\s*(\d{1,3})\s*[.):：\-–—]\s*(.+?)\s*$", re.IGNORECASE)


def parse_english(text, total, numbers=None):
    """Đọc câu trả lời: {số cảnh: prompt}. Bỏ markdown, dòng ngoài 1..total và dòng trống.

    Nếu câu trả lời không có số thứ tự (Gemini có thể hiển thị danh sách không kèm số), các đoạn văn được gán
    lần lượt cho các cảnh đã hỏi (numbers; mặc định 1..total) theo đúng thứ tự.
    """
    found = {}
    for raw in str(text or "").replace("\r", "").split("\n"):
        line = re.sub(r"[*_`#>]+", "", raw).strip()
        match = EN_LINE.match(line)
        if not match:
            continue
        number, prompt = int(match.group(1)), match.group(2).strip()
        prompt = re.sub(r"^(?:scene|prompt)\s*\d+\s*[:.\-–]\s*", "", prompt, flags=re.IGNORECASE).strip()
        if not (1 <= number <= total and len(prompt) >= 15):
            continue
        # Giữ dòng đầu tiên; nhưng nếu dòng đã có còn là tiếng Việt (Gemini chép lại đề) thì thay bằng bản tiếng Anh.
        if number not in found or (not is_english(found[number]) and is_english(prompt)):
            found[number] = prompt[:FIELD_LIMIT * 2]
    if found:
        return found
    order = list(numbers or range(1, total + 1))
    paragraphs = []
    for raw in str(text or "").replace("\r", "").split("\n"):
        line = re.sub(r"[*_`#>]+", "", raw).strip()
        if len(line) < 40 or END_EN.lower() in line.lower() or line.endswith(":"):
            continue  # bỏ dòng dẫn ("Here are the prompts:"), tiêu đề, dòng kết thúc
        paragraphs.append(line[:FIELD_LIMIT * 2])
    return {number: prompt for number, prompt in zip(order, paragraphs)}


def missing_english(found, total, structured=False, seconds=0, angles=0):
    """Số cảnh còn thiếu, chưa phải tiếng Anh, thiếu trường Master Prompt 08, hoặc (chế độ đổi góc) sai mốc góc máy."""
    return [number for number in range(1, total + 1)
            if number not in found or not is_english(found[number])
            or (structured and not structured_complete(found[number]))
            or (angles and seconds and angle_problem(found[number], seconds, angles))]
