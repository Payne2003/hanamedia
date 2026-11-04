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

VERSION = "content-19"
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
                         "Với TikTok/Facebook, hãy đăng video lên YouTube ở chế độ công khai hoặc không công khai rồi dán link.")
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


def web_prompt(kind, brief, video_url="", language="vi", scenes=None):
    """Yêu cầu để dán vào ứng dụng Gemini trên web."""
    language = LANGUAGES.get(language, LANGUAGES["vi"])
    video_url = youtube_url(video_url)
    brief = str(brief or "").strip()[:20000]
    if kind == "review":
        scenes = clean_scenes(scenes or [])
        if not scenes:
            raise ValueError("Chưa có cảnh để kiểm tra.")
        task = ("Bạn là người kiểm duyệt kịch bản cho video tạo bằng AI. "
                + ("Xem lại video gốc và so sánh với danh sách cảnh bên dưới: tìm đoạn bị bỏ sót, cảnh sai thứ tự, mô tả sai. "
                   if video_url else "So sánh danh sách cảnh với yêu cầu: tìm phần còn thiếu hoặc mạch truyện bị đứt. ")
                + "Đồng thời sửa cảnh mà AI video khó hoặc không tạo được (trường trống, quá nhiều hành động, người nổi tiếng, "
                "logo, chữ trên màn hình, nội dung dễ bị từ chối). Thêm cảnh còn thiếu vào đúng vị trí.")
        body = (f"{task}\n\nYêu cầu gốc:\n{brief or '(không có)'}\n\nDanh sách cảnh hiện tại:\n"
                + "\n".join(format_prompts(scenes))
                + "\n\nĐầu câu trả lời, ghi các vấn đề đã sửa dưới tiêu đề VẤN ĐỀ: (mỗi vấn đề một dòng bắt đầu bằng dấu -). "
                "Sau đó viết lại TOÀN BỘ danh sách cảnh đã sửa.")
    else:
        if not brief:
            raise ValueError("Nhập yêu cầu (prompt) cho Gemini.")
        source = ("Xem kỹ video gốc từ đầu đến cuối, viết lại kịch bản video gốc và chia toàn bộ video thành các cảnh."
                  if video_url else "Không có video gốc. Tự viết kịch bản theo yêu cầu rồi chia thành các cảnh.")
        body = f"Bạn là biên kịch và đạo diễn hình ảnh cho video tạo bằng AI. {source}\n\nYêu cầu:\n{brief}"
    parts = [body]
    if video_url:
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


def split_scene_lines(text):
    """Tách đầu vào thành từng cảnh: mỗi dòng một cảnh, hoặc nhiều "Bối cảnh N:" nối bằng dấu ;"""
    text = str(text or "").replace("\r", "").strip()
    text = re.sub(r"^\s*(input\s*:)?\s*\[", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\]\s*$", "", text)
    text = re.sub(r";\s*(?=b[ốo]i\s*c[ảa]nh\s*\d)", ";\n", text, flags=re.IGNORECASE)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
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
STRUCT_FIELDS = (("setting", "Setting"), ("camera", "Camera"), ("behavior", "Character behavior"), ("action", "Action"))
STRUCT_LABEL = re.compile(r"(?:(?<=^)|(?<=[\s;|.,(\[]))(setting|camera(?:\s*angle)?|character\s*behaviou?r|behaviou?r|action|style)\s*[:：]",
                          re.IGNORECASE)
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
        key = ("camera" if label.startswith("camera") else "behavior" if "behavio" in label else label)
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        value = ASPECT_TAIL.sub("", text[mark.end():end]).strip(" \t;|,")
        if value and key not in fields:
            fields[key] = value[:FIELD_LIMIT * 2]
    return fields


def structured_complete(prompt):
    fields = parse_structured(prompt)
    return all(fields.get(key) for key, _ in STRUCT_FIELDS)


def english_request(lines, numbers=None, style="", aspect="16:9", structured=True, seconds=DEFAULT_SECONDS):
    """Yêu cầu gửi Gemini: viết prompt tiếng Anh cho các cảnh (numbers=None: tất cả)."""
    numbers = numbers or list(range(1, len(lines) + 1))
    partial = len(numbers) < len(lines)
    head = ("Some scene prompts are missing or not in English. Write prompts ONLY for the scenes listed below, "
            "keeping their original numbers." if partial else
            f"Below are {len(lines)} scenes of a video, written in Vietnamese.")
    rules = (
        "You are a prompt engineer for AI text-to-video models.\n"
        f"{head}\n"
        "Convert EACH scene into ONE detailed English text-to-video prompt: setting and lighting, camera angle and "
        "movement, character appearance and behavior, and the action that happens. Keep characters, outfits and "
        "locations consistent between scenes. Translate spoken lines into English inside quotes.\n"
        "Rules:\n"
        + ("- Exactly one line per scene in this fixed labelled format, all four labels required, in this order:\n"
           "  N. Setting: <location, time, lighting, props, character appearance>; Camera: <shot size, angle, movement>; "
           "Character behavior: <behavior, emotion, posture>; Action: <what happens in the shot>; Style: <visual style, "
           "sound>" + (f"; {seconds} seconds" if seconds else "") + ("; <aspect ratio>" if aspect in ASPECTS else "") + "\n"
           "  Example: 1. Setting: A quiet beach at dawn, soft golden light, a young woman in a white dress; Camera: Wide "
           "shot, slow dolly-in at eye level; Character behavior: Calm and relaxed, smiling; Action: She walks barefoot "
           "along the waterline as waves wash over her feet; Style: Photorealistic, natural ambient sound"
           + (f"; {seconds} seconds" if seconds else "") + (f"; {aspect} aspect ratio." if aspect in ASPECTS else ".") + "\n"
           "- Never leave a label empty and never use the labels inside the text of another field.\n"
           if structured else
           "- Exactly one line per scene, starting with its number and a period, for example: 1. A quiet beach at dawn...\n")
        + 
        "- Do not merge, split, skip or renumber scenes. No headings, no markdown, no comments.\n"
        f"- After the last line write exactly: {END_EN}")
    if seconds:
        rules += (f"\n- Every scene is ONE continuous shot lasting about {seconds} seconds: plan the action so it fits "
                  f"{seconds} seconds, and write \"{seconds} seconds\" near the end of the prompt.")
    if aspect in ASPECTS:
        rules += (f"\n- Every prompt is composed for a {ASPECTS[aspect]}: describe the framing for that aspect ratio "
                  f"and end the prompt with \"{aspect} aspect ratio\".")
    if style.strip():
        rules += f"\nExtra style for every prompt: {style.strip()[:2000]}"
    scenes = "\n".join(f"{number}. {lines[number - 1]}" for number in numbers)
    return f"{rules}\n\nScenes:\n{scenes}"


def continue_english(after, total):
    return (f"Continue with scene {after + 1} to scene {total}, exactly the same format (one line per scene, starting with "
            f"its number, same labels). Do not repeat earlier scenes. After scene {total} write exactly: {END_EN}")


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


def missing_english(found, total, structured=False):
    """Số cảnh còn thiếu, chưa phải tiếng Anh, hoặc (khi dùng khung chuẩn) thiếu một trong 4 trường."""
    return [number for number in range(1, total + 1)
            if number not in found or not is_english(found[number])
            or (structured and not structured_complete(found[number]))]
