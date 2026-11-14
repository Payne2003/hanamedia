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


AGENTS_TEXT = """# Thư mục HanaMedia (dùng chung với tool HanaMedia)

Dành cho Claude hoặc Codex khi được yêu cầu "Chạy hanamedia-wildlife-workflow":
- Video cần làm nằm trong input/ (<ten-ngan>.mp4), kèm <ten-ngan>_cuts.txt (điểm cắt tool đo) nếu có.
- Nếu có thư mục input/<ten-ngan>_pack/ thì tool đã đo sẵn: shots.txt, sheets/*.jpg + sheets.txt (3 khung mỗi cảnh),
  motion.txt, groups.json (gộp cảnh 8–10 giây đúng script Lock 01). Dùng luôn các file này, không cần cài Python /
  opencv. Xem đủ TẤT CẢ các tờ ảnh trong sheets/.
- Ghi kết quả vào output/<ten-ngan>/: Wildlife_Prompts_EN_<ten-ngan>.txt (mỗi dòng một cảnh, dòng N = cảnh N)
  và hanamedia_<ten-ngan>.json nếu làm được. Tool tự đưa prompt vào hàng đợi Studio khi thấy file.
- Không sửa hay xoá video trong input/.
"""


def ensure(folder):
    root = Path(folder).expanduser()
    for sub in ("input", "output"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    agents = root / "AGENTS.md"
    if not agents.exists():
        try:
            agents.write_text(AGENTS_TEXT, encoding="utf-8", newline="\n")
        except OSError:
            pass
    return root


def write_cuts(target, cuts):
    """Bảng điểm cắt: mỗi dòng một số giây (dấu chấm thập phân)."""
    values = sorted({round(float(c), 3) for c in cuts if float(c) > 0})
    text = "# Điểm cắt cảnh do HanaMedia đo bằng FFmpeg (giây)\n" + "".join(f"{v:g}\n" for v in values)
    Path(target).write_text(text, encoding="utf-8", newline="\n")
    return len(values)


def send(folder, video, cuts=None, duration=None, pack=False, stop=None, status=lambda text: None):
    """Chép video vào input/ (tên chuẩn của workflow). cuts: danh sách giây đổi cảnh (None = để Claude tự dò).
    pack=True (cần cuts + duration): làm thêm input/<ten>_pack/ (tờ ảnh, shots, motion) cho Claude / Codex dùng luôn."""
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
    packed = None
    pack_dir = root / "input" / f"{name}_pack"
    if pack and cuts is not None and duration:
        packed = build_pack(target, pack_dir, cuts, float(duration), stop, status)
    elif pack_dir.exists():
        shutil.rmtree(pack_dir, ignore_errors=True)
    return {"name": name, "video": str(target), "cuts": count, "folder": str(root), "pack": packed}


SHEET_SCENES = 14   # 14 cảnh một tờ, mỗi cảnh 3 khung (đầu / giữa / cuối) giống sheets.py của kỹ năng phân tích
FRAME_W, FRAME_H = 256, 144


def scenes_from_cuts(cuts, duration):
    bounds = [0.0] + sorted({round(float(c), 2) for c in cuts if 0.05 < float(c) < duration - 0.05}) + [round(duration, 2)]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def motion_label(value):
    from .reverse_engineer import MOTION_FAST, MOTION_MEDIUM
    if value is None:
        return "không rõ"
    return "nhanh" if value >= MOTION_FAST else "vừa" if value >= MOTION_MEDIUM else "chậm" if value >= MOTION_MEDIUM / 3 else "tĩnh"


def group_shots(shots):
    """Gộp cảnh thành prompt 8–10 giây (prompt cuối tối đa 12, ngắn hơn 8 thì kéo cho đủ 8) — đúng thuật toán script
    Lock 01 của kỹ năng wildlife-motion-prompt-meta, để Codex trên máy không có Python dùng luôn groups.json."""
    shots = [(n + 1, float(a), float(b)) for n, (a, b) in enumerate(shots)]
    if not shots:
        return []
    total = shots[-1][2]
    bounds = sorted({a for _, a, _ in shots} | {total})

    def pick(start, low, high, target):
        candidates = [x for x in bounds if start + low - 1e-6 <= x <= start + high + 1e-6]
        return min(candidates, key=lambda x: abs(x - target)) if candidates else None
    groups, start = [], 0.0
    while True:
        rest = total - start
        if rest <= 12:
            groups.append((start, total))
            break
        if rest < 16:
            end = pick(start, 8, 10, start + 8) or start + 8
        elif rest < 18:
            end = pick(start, 8, rest - 8, start + 8) or start + 8
        elif rest <= 20:
            end = pick(start, 8, 10, start + rest / 2) or start + rest / 2
        else:
            end = pick(start, 8, 10, start + 10) or start + 10
        groups.append((start, end))
        start = end
    out = []
    for index, (gs, ge) in enumerate(groups, 1):
        segments = []
        for n, a, b in shots:
            a2, b2 = max(a, gs), min(b, ge)
            if b2 - a2 > 0.05:
                segments.append({"shot": n, "from": round(a2 - gs, 1), "to": round(b2 - gs, 1),
                                 "split": a < gs - 1e-6 or b > ge + 1e-6})
        real = round(ge - gs, 1)
        duration = max(real, 8.0)
        if duration > real and segments:
            segments[-1]["to"] = duration
        out.append({"prompt": index, "start": round(gs, 2), "end": round(ge, 2), "real_seconds": real, "duration": duration,
                    "padded_seconds": round(duration - real, 1), "source_shots": [x["shot"] for x in segments],
                    "segments": segments})
    return out


def build_pack(source, target, cuts, duration, stop=None, status=lambda text: None):
    """Gói dữ liệu máy đo sẵn cho Claude / Codex (máy khách không cần Python, opencv):
    shots.txt (cảnh · đầu · cuối · dài), allcuts.txt, sheets/sNN.jpg + sheets.txt (vị trí từng cảnh trên tờ ảnh),
    motion.txt (độ chuyển động trong khung của từng cảnh, đo bằng sai khác khung hình)."""
    from . import video_tools as video
    from .reverse_engineer import frame_differences
    target = Path(target)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    (target / "sheets").mkdir(parents=True, exist_ok=True)
    frames = target / "frames"
    frames.mkdir()
    shots = scenes_from_cuts(cuts, duration)
    (target / "allcuts.txt").write_text("".join(f"{s:g}\n" for s, _ in shots[1:]), encoding="utf-8", newline="\n")
    (target / "shots.txt").write_text("\n".join(f"{i + 1}\t{s:.2f}\t{e:.2f}\t{e - s:.2f}" for i, (s, e) in enumerate(shots)) + "\n",
                                      encoding="utf-8", newline="\n")
    (target / "groups.json").write_text(json.dumps(group_shots(shots), ensure_ascii=False, indent=1), encoding="utf-8",
                                        newline="\n")
    index = []
    total = len(shots)
    for number, (start, end) in enumerate(shots):
        if stop is not None and stop.is_set():
            raise BridgeError("Đã dừng.")
        if number % 20 == 0:
            status(f"Trích khung hình cảnh {number + 1}/{total}…")
        moments = [min(start + 0.3, (start + end) / 2), (start + end) / 2, max(end - 0.35, (start + end) / 2)]
        for slot, moment in enumerate(moments):
            out = frames / f"{number * 3 + slot:05d}.jpg"
            video.run([video.ffmpeg(), "-v", "error", "-y", "-ss", f"{max(moment, 0):.3f}", "-i", str(source), "-frames:v", "1",
                       "-vf", f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,pad={FRAME_W}:{FRAME_H}:-1:-1",
                       "-q:v", "4", str(out)], 60)
            if not out.exists():  # khung cuối video đôi khi không đọc được: ô đen
                video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={FRAME_W}x{FRAME_H}",
                           "-frames:v", "1", str(out)], 30)
    per_sheet = SHEET_SCENES * 3
    for sheet, first in enumerate(range(0, total, SHEET_SCENES)):
        group = shots[first:first + SHEET_SCENES]
        name = f"s{sheet + 1:02d}.jpg"
        video.run([video.ffmpeg(), "-v", "error", "-y", "-start_number", str(first * 3), "-i", str(frames / "%05d.jpg"),
                   "-frames:v", "1", "-vf", f"tile=6x{(len(group) + 1) // 2}:padding=4:color=black", "-q:v", "4",
                   str(target / "sheets" / name)], 120)
        for offset, (start, end) in enumerate(group):
            row, half = divmod(offset, 2)
            index.append(f"{first + offset + 1}\t{name}\thàng {row + 1}\t{'cột 1–3 (trái)' if half == 0 else 'cột 4–6 (phải)'}"
                         f"\t{start:.2f}-{end:.2f}s")
    (target / "sheets.txt").write_text(
        "# Mỗi tờ: 2 cảnh một hàng, mỗi cảnh 3 khung liền nhau (đầu · giữa · cuối cảnh). Cột: cảnh · tờ · hàng · vị trí · giây\n"
        + "\n".join(index) + "\n", encoding="utf-8", newline="\n")
    shutil.rmtree(frames, ignore_errors=True)
    status("Đo độ chuyển động từng cảnh…")
    try:
        scores, times = frame_differences(source, stop)
    except Exception:
        scores, times = [], []
    if scores and len(times) < len(scores) + 1:  # không đọc được mốc thời gian: chia đều theo thời lượng
        step = float(duration) / (len(scores) + 1)
        times = [i * step for i in range(len(scores) + 1)]
    lines = ["# cảnh · giây · chuyển động trong khung (sai khác khung hình trung bình / lớn nhất) · mức. Cắt cảnh không tính."]
    for number, (start, end) in enumerate(shots):
        values = [scores[i] for i in range(min(len(scores), len(times) - 1))
                  if start + 0.15 < times[i + 1] < end - 0.15]
        mean = sum(values) / len(values) if values else None
        peak = max(values) if values else None
        lines.append(f"{number + 1}\t{start:.1f}-{end:.1f}\t" + (f"tb={mean:.3f} max={peak:.3f}" if values else "na")
                     + f"\t{motion_label(mean)}")
    (target / "motion.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (target / "README.txt").write_text(
        "Dữ liệu HanaMedia đo sẵn cho video này (không cần Python / opencv):\n"
        "- shots.txt: cảnh, giây đầu, giây cuối, độ dài (thay cho Bước 2–3 của kỹ năng phân tích).\n"
        "- sheets/*.jpg + sheets.txt: tờ ảnh liên hoàn, mỗi cảnh 3 khung đầu/giữa/cuối. Xem TẤT CẢ các tờ.\n"
        "- motion.txt: độ chuyển động trong khung từng cảnh (tĩnh / chậm / vừa / nhanh), hỗ trợ ghi tốc độ hành động.\n"
        "- groups.json: bảng gộp cảnh thành prompt 8–10 giây, tính đúng script Lock 01 (dùng khi máy không có Python).\n",
        encoding="utf-8", newline="\n")
    return {"scenes": total, "sheets": (total + SHEET_SCENES - 1) // SHEET_SCENES}


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


def pack_lengths(shots_file):
    """Độ dài từng cảnh trong shots.txt do tool làm (dùng khi Claude / Codex chỉ trả file prompt .txt)."""
    try:
        rows = Path(shots_file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for row in rows:
        parts = row.split("\t")
        if len(parts) >= 4 and _number(parts[3]) is not None:
            out.append(round(_number(parts[3]), 1))
    return out


def group_lengths(folder, pattern="groups_*.json"):
    """Độ dài từng prompt trong groups_<ten>.json (danh sách, hoặc {"groups": [...]}; mỗi phần tử có duration
    hoặc start/end)."""
    for file in sorted(Path(folder).glob(pattern)):
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        items = data.get("groups") if isinstance(data, dict) else data
        if not isinstance(items, list):
            continue
        out = []
        for item in items:
            if not isinstance(item, dict):
                return []
            value = _number(item.get("duration"))
            if value is None and _number(item.get("start")) is not None and _number(item.get("end")) is not None:
                value = _number(item.get("end")) - _number(item.get("start"))
            out.append(round(value, 1) if value and value > 0 else None)
        return out
    return []


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
        lines = _prompt_lines(raw)[:MAX_SCENES]
        # Số giây từng dòng: groups_<ten>.json (workflow gộp cảnh thành prompt 8–10 giây) → shots.txt của gói tool,
        # nhưng chỉ khi số dòng khớp (không gán số giây của từng góc máy cho prompt đã gộp nhiều cảnh).
        lengths = group_lengths(path.parent)
        if len(lengths) != len(lines) and len(path.parents) > 2:
            lengths = group_lengths(path.parents[2] / "input" / f"{path.parent.name}_pack", "groups.json")
        if len(lengths) != len(lines):
            lengths = pack_lengths(path.parents[2] / "input" / f"{path.parent.name}_pack" / "shots.txt") \
                if len(path.parents) > 2 else []
        if len(lengths) != len(lines):
            lengths = []
        for index, line in enumerate(lines):
            scenes.append({"scene": index + 1, "duration": lengths[index] if index < len(lengths) else None, "prompt": line})
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


def folder_key(path):
    """Khóa theo thư mục kết quả output/<ten>/: đã đưa một file của thư mục này (vd .txt) thì không tự đưa thêm file
    khác của cùng thư mục (vd .json ghi sau) → không nhân đôi prompt trong Studio."""
    return "d" + hashlib.sha1(str(Path(path).resolve().parent).encode()).hexdigest()[:19]


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
                      "updated": stat.st_mtime, "signature": sig, "folder_key": folder_key(path),
                      "imported": bool(imported.get(sig)), "folder_imported": bool(imported.get(folder_key(path)))})
    items.sort(key=lambda item: -item["updated"])
    return items


def inside(folder, path):
    """Chỉ đọc file nằm trong output/ của thư mục chung."""
    root = (Path(folder).expanduser() / "output").resolve()
    target = Path(path).resolve()
    return target.is_file() and target.is_relative_to(root)


# ---- Không gian Agent 3D: tiến độ từng video qua các agent (đọc từ tệp trong input/ và output/) ----
AGENTS = (
    {"id": "tool", "name": "HanaMedia Tool", "role": "Chuẩn bị: chép video, đo điểm cắt, tờ ảnh, chuyển động, bảng gộp"},
    {"id": "analyst", "name": "Agent Phân tích", "skill": "phan-tich-video-doi-thu", "role": "Video gốc → bảng cảnh tiếng Việt"},
    {"id": "writer", "name": "Agent Viết prompt", "skill": "wildlife-motion-prompt-meta", "role": "Bảng cảnh → prompt 8–10 giây cho Meta Muse"},
    {"id": "conductor", "name": "Agent Điều phối", "skill": "hanamedia-wildlife-workflow", "role": "Nối hai agent, xuất file cho tool"},
    {"id": "studio", "name": "Studio · Muse", "role": "Hàng đợi tạo video trên Muse"},
)


def progress(folder, imported=None, jobs=None):
    """Mỗi video: trạng thái từng agent (waiting / working / done / error). jobs: {batch: {status: số}} của Studio."""
    root = Path(folder).expanduser()
    imported, jobs = imported or {}, jobs or {}
    names = {}
    if (root / "input").is_dir():
        for path in (root / "input").iterdir():
            if path.is_file() and path.suffix.lower() in VIDEO_TYPES:
                names.setdefault(short_name(path.name), {})["video"] = path
    if (root / "output").is_dir():
        for path in (root / "output").iterdir():
            if path.is_dir():
                names.setdefault(path.name, {})["out"] = path
    board = []
    for name, found in names.items():
        video, out = found.get("video"), found.get("out") or root / "output" / name
        has = lambda pattern: out.is_dir() and any(out.glob(pattern))
        analysis = has("phan-tich-canh_*.md")
        prompts = has("Wildlife_Prompts_EN_*.txt")
        final = has("hanamedia_*.json")
        started = out.is_dir() and any(out.iterdir())
        stages = {}
        stages["tool"] = "done" if video or analysis else "waiting"
        stages["analyst"] = "done" if analysis else "working" if started else "waiting"
        stages["writer"] = "done" if prompts else "working" if analysis or has("groups_*.json") else "waiting"
        stages["conductor"] = "done" if final or (prompts and not video) else "working" if started else "waiting"
        files = result_files(root)
        result = next((f for f in files if f.parent.name == name), None)
        studio = {"total": 0, "done": 0, "running": 0, "error": 0, "waiting": 0}
        if result is not None:
            counts = jobs.get(batch_id(result), {})
            for key in studio:
                studio[key] = sum(counts.values()) if key == "total" else counts.get(key, 0)
            sig_done = imported.get(signature(result)) or imported.get(folder_key(result))
            if studio["total"] and studio["done"] + studio["error"] >= studio["total"]:
                stages["studio"] = "error" if studio["error"] and not studio["done"] else "done"
            elif studio["running"] or studio["total"]:
                stages["studio"] = "working"
            else:
                stages["studio"] = "working" if sig_done else "waiting"
        else:
            stages["studio"] = "waiting"
        updated = max((p.stat().st_mtime for p in [video, out] if p and p.exists()), default=0)
        order = ["tool", "analyst", "writer", "conductor", "studio"]
        done = sum(1 for k in order if stages[k] == "done")
        board.append({"name": name, "video": video.name if video else "", "stages": stages, "studio": studio,
                      "percent": round(100 * (done + 0.5 * sum(1 for k in order if stages[k] == "working")) / len(order)),
                      "updated": updated})
    board.sort(key=lambda item: -item["updated"])
    return board[:40]
