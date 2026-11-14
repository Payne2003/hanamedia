"""Cầu nối với workflow "hanamedia-wildlife-workflow" chạy trong Claude desktop (không dùng Gemini / ChatGPT).

Workflow trao đổi qua một thư mục chung (mặc định ~/HanaMedia), được kết nối với Claude desktop:
- Tool chép video gốc vào  input/<ten-ngan>.mp4  (kèm  input/<ten-ngan>_cuts.txt  = điểm cắt FFmpeg của tool).
- Claude chạy workflow, ghi kết quả vào  output/<ten-ngan>/hanamedia_<ten-ngan>.json  (+ Wildlife_Prompts_EN_<ten-ngan>.txt).
- Tool đọc kết quả, đưa từng prompt vào hàng đợi Studio đúng thứ tự cảnh, đúng số giây từng cảnh.
Tool không gửi gì lên mạng ở bước này; chỉ đọc / ghi tệp trong thư mục chung.
"""
import hashlib
import json
import re
import shutil
import unicodedata
import uuid
from pathlib import Path

VIDEO_TYPES = (".mp4", ".mov", ".mkv", ".webm")
MAX_SCENES = 2000
MAX_JSON = 20_000_000
_NAMESPACE = uuid.UUID("6f1c2b8e-4d1a-4c55-9a0e-6a2f3c1b9d70")


class BridgeError(ValueError):
    pass


def default_folder():
    return Path.home() / "HanaMedia"


def short_name(filename):
    """<ten-ngan> theo đúng quy ước của workflow: tên file bỏ đuôi, viết thường, không dấu, khoảng trắng → '-'."""
    stem = Path(str(filename)).stem.replace("đ", "d").replace("Đ", "D")
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii").lower()
    stem = re.sub(r"\s+", "-", stem.strip())
    stem = re.sub(r"[^a-z0-9._-]", "", stem).strip(".-_")
    return stem[:80] or "video"


def ensure(folder):
    root = Path(folder).expanduser()
    for sub in ("input", "output"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def write_cuts(target, cuts):
    """Bảng điểm cắt: mỗi dòng một số giây (dấu chấm thập phân)."""
    values = sorted({round(float(c), 3) for c in cuts if float(c) > 0})
    text = "# Điểm cắt cảnh do HanaMedia đo bằng FFmpeg (giây)\n" + "".join(f"{v:g}\n" for v in values)
    Path(target).write_text(text, encoding="utf-8", newline="\n")
    return len(values)


def send(folder, video, cuts=None):
    """Chép video vào input/ (tên chuẩn của workflow). cuts: danh sách giây đổi cảnh (None = để Claude tự dò)."""
    source = Path(video)
    if not source.is_file():
        raise BridgeError("Không thấy file video đã chọn.")
    if source.suffix.lower() not in VIDEO_TYPES:
        raise BridgeError("Workflow nhận video " + ", ".join(VIDEO_TYPES) + ".")
    root = ensure(folder)
    name = short_name(source.name)
    target = root / "input" / f"{name}{source.suffix.lower()}"
    try:
        same = target.exists() and target.resolve() == source.resolve()
    except OSError:
        same = False
    if not same:
        for old in root.glob(f"input/{name}.*"):  # bản cũ cùng tên (đuôi khác) → bỏ để workflow không chạy trùng
            if old.suffix.lower() in VIDEO_TYPES and old != target:
                old.unlink(missing_ok=True)
        temporary = target.with_name(target.name + ".copying")
        shutil.copyfile(source, temporary)
        temporary.replace(target)
    cuts_file = root / "input" / f"{name}_cuts.txt"
    count = None
    if cuts is not None:
        count = write_cuts(cuts_file, cuts)
    else:
        cuts_file.unlink(missing_ok=True)
    return {"name": name, "video": str(target), "cuts": count, "folder": str(root)}


def pending(folder):
    """Video đang chờ Claude xử lý (có trong input/ mà chưa có kết quả trong output/)."""
    root = Path(folder).expanduser()
    items = []
    for path in sorted((root / "input").glob("*")) if (root / "input").is_dir() else []:
        if path.is_file() and path.suffix.lower() in VIDEO_TYPES:
            name = short_name(path.name)
            if not (root / "output" / name / f"hanamedia_{name}.json").is_file():
                items.append({"name": name, "video": path.name, "cuts": (root / "input" / f"{name}_cuts.txt").is_file()})
    return items


def _number(value):
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) < 1e7 else None


def _prompt_lines(text):
    return [line.strip() for line in str(text).replace("\r\n", "\n").split("\n") if line.strip()]


def parse(path):
    """Đọc hanamedia_<ten>.json (hoặc Wildlife_Prompts_EN_<ten>.txt). Trả về {name, scenes:[{scene, duration, prompt}], …}."""
    path = Path(path)
    if path.stat().st_size > MAX_JSON:
        raise BridgeError("File kết quả quá lớn.")
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    scenes = []
    info = {}
    if path.suffix.lower() == ".json":
        try:
            info = json.loads(raw)
        except ValueError:
            raise BridgeError(f"{path.name} không phải JSON hợp lệ (có thể Claude đang ghi dở).") from None
        if not isinstance(info, dict) or not isinstance(info.get("scenes"), list):
            raise BridgeError(f"{path.name} thiếu danh sách scenes.")
        for index, item in enumerate(info["scenes"][:MAX_SCENES]):
            if not isinstance(item, dict):
                continue
            prompt = " ".join(str(item.get("prompt") or "").split())
            if not prompt:
                continue
            duration = _number(item.get("duration"))
            if duration is None:
                start, end = _number(item.get("start")), _number(item.get("end"))
                duration = end - start if start is not None and end is not None else None
            scene = item.get("scene")
            scenes.append({"scene": scene if isinstance(scene, int) else index + 1,
                           "duration": round(duration, 1) if duration and duration > 0 else None, "prompt": prompt})
    else:
        for index, line in enumerate(_prompt_lines(raw)[:MAX_SCENES]):
            scenes.append({"scene": index + 1, "duration": None, "prompt": line})
    if not scenes:
        raise BridgeError(f"{path.name} không có prompt nào.")
    name = path.parent.name
    return {"name": name, "file": str(path), "video": str(info.get("video") or ""), "scene_count": len(scenes),
            "duration": _number(info.get("duration")), "cut_source": str(info.get("cut_source") or ""), "scenes": scenes}


def result_files(folder):
    """Mỗi thư mục output/<ten>/: ưu tiên hanamedia_<ten>.json, không có thì Wildlife_Prompts_EN_<ten>.txt."""
    root = Path(folder).expanduser() / "output"
    found = []
    if not root.is_dir():
        return found
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        candidates = sorted(sub.glob("hanamedia_*.json")) or sorted(sub.glob("Wildlife_Prompts_EN_*.txt"))
        if candidates:
            found.append(candidates[0])
    return found


def signature(path):
    stat = Path(path).stat()
    return hashlib.sha1(f"{Path(path).resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode()).hexdigest()[:20]


def batch_id(path):
    """Mã lượt Studio cố định theo file kết quả (cùng file → cùng mã: đưa lại không bị nhân đôi)."""
    return str(uuid.uuid5(_NAMESPACE, signature(path)))


def studio_prompt(scene):
    """Prompt đưa vào Studio. Có số giây trong JSON mà prompt chưa ghi thì thêm "Duration X seconds" ngay sau số cảnh
    (Studio đọc số giây từ prompt để đặt đúng thời lượng từng video)."""
    from .store import prompt_seconds
    prompt = scene["prompt"]
    duration = scene.get("duration")
    if duration and 1 <= duration <= 10 and not prompt_seconds(prompt):
        text = f"{duration:g}"
        match = re.match(r"(\s*SCENE\s+\d+\s*\|)", prompt, re.IGNORECASE)
        prompt = (match.group(1) + f" Duration {text} seconds |" + prompt[match.end():]) if match else \
            f"Duration {text} seconds. " + prompt
    return prompt


def results(folder, imported=None):
    """Danh sách kết quả của Claude (mới nhất trước) để hiện trên giao diện."""
    imported = imported or {}
    items = []
    for path in result_files(folder):
        try:
            data = parse(path)
            error = ""
        except (OSError, BridgeError) as problem:
            data, error = {"name": path.parent.name, "scene_count": 0, "duration": None, "cut_source": ""}, str(problem)
        stat = path.stat()
        sig = signature(path)
        items.append({"name": data["name"], "file": path.name, "path": str(path), "scenes": data["scene_count"],
                      "duration": data.get("duration"), "cut_source": data.get("cut_source", ""), "error": error,
                      "updated": stat.st_mtime, "signature": sig, "imported": bool(imported.get(sig))})
    items.sort(key=lambda item: -item["updated"])
    return items


def inside(folder, path):
    """Chỉ đọc file nằm trong output/ của thư mục chung."""
    root = (Path(folder).expanduser() / "output").resolve()
    target = Path(path).resolve()
    return target.is_file() and target.is_relative_to(root)
