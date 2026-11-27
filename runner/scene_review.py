"""Agent Phân tích xin XEM KỸ một cảnh khó; HanaMedia trích khung hình liên tiếp từ video gốc rồi trả ảnh cho Agent.

Luồng (chạy thật, người dùng không cắt ảnh hay sao chép kết quả bằng tay):
  1. Lượt đầu Agent xem tờ ảnh tổng quan (sheets/) như cũ.
  2. Cảnh khó (hai con áp sát, giao nhau, bị che khuất, chưa rõ chủ thể / hướng / kết quả tiếp xúc): Agent ghi yêu cầu vào
     input/<ten>_pack/review_request.json (số cảnh + khoảng giây nghi vấn + câu hỏi).
  3. HanaMedia (luồng nền, vài giây một lần) đọc lại video gốc, trích dải khung liên tiếp ĐÚNG TRONG cảnh đó, ảnh lớn có nhãn
     "C<cảnh> · giây thật", ghi vào input/<ten>_pack/review/<mã>/ và review_status.json. Ảnh đã trích được dùng lại khi chạy lại.
  4. Agent xem ảnh, ghi kết luận vào review_conclusions.json (kèm mức chắc chắn), rồi sửa bản phân tích.
  5. HanaMedia ghép review_handoff.md cho Agent Viết prompt. Khi còn yêu cầu chưa có kết luận, bảng Agent 3D vẫn để bước
     phân tích ở "đang làm" và kết quả chưa tự vào Studio.
Giới hạn: tối đa MAX_REQUESTS yêu cầu / dự án, MAX_FRAMES ảnh / yêu cầu, MAX_SPAN giây / yêu cầu. motion.txt chỉ gợi ý chỗ
cần xem (máy lia / rung làm cả khung đổi; con vật chuyển động nhỏ vẫn có thể quan trọng dù điểm thấp).
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import frames as framelib
from .reverse_engineer import clock

REQUEST_FILE = "review_request.json"
STATUS_FILE = "review_status.json"
CONCLUSION_FILE = "review_conclusions.json"
HANDOFF_FILE = "review_handoff.md"
PTS_FILE = "frame_pts.json"
MAX_REQUESTS = 8
MAX_FRAMES = 12
DEFAULT_FRAMES = 8
MAX_SPAN = 6.0
REVIEW_WIDTH = 1280
VIDEO_TYPES = (".mp4", ".mov", ".mkv", ".webm")
CERTAINTY = ("chắc chắn", "có thể", "chưa xác định")


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    os.replace(temp, path)


def load_shots(pack):
    """{số cảnh: (đầu, cuối)} đọc từ shots.txt của gói (cột: cảnh · đầu · cuối · dài)."""
    shots = {}
    try:
        text = (Path(pack) / "shots.txt").read_text(encoding="utf-8")
    except OSError:
        return shots
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].strip().isdigit():
            try:
                shots[int(parts[0])] = (float(parts[1]), float(parts[2]))
            except ValueError:
                continue
    return shots


def find_video(pack):
    """Video gốc của gói input/<ten>_pack/ → input/<ten>.<đuôi>."""
    pack = Path(pack)
    name = pack.name[:-5] if pack.name.endswith("_pack") else pack.name
    for suffix in VIDEO_TYPES:
        candidate = pack.parent / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def video_signature(video):
    stat = Path(video).stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def frame_times(pack, video, stop=None):
    """Mốc mọi khung hình của video, ghi nhớ trong gói (frame_pts.json) để không giải mã lại."""
    cache = Path(pack) / PTS_FILE
    saved = read_json(cache, {})
    signature = video_signature(video)
    if isinstance(saved, dict) and saved.get("video") == signature and saved.get("times"):
        return saved["times"]
    times = framelib.frame_pts(video, stop)
    if times:
        write_json(cache, {"video": signature, "times": times})
    return times


# ---- yêu cầu của Agent ------------------------------------------------------------------------------------------------
def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) < 1e7 else None


def normalize(raw, shots, index):
    """Một yêu cầu thô → dict đã kiểm tra. status="invalid" kèm lý do nếu sai (Agent sửa file rồi tool xử lý lại)."""
    item = raw if isinstance(raw, dict) else {}
    rid = str(item.get("id") or f"r{index}").strip()
    rid = rid if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", rid) else f"r{index}"
    out = {"id": rid, "question": str(item.get("question") or item.get("reason") or "")[:300],
           "notes": []}
    scene = item.get("scene")
    scene = int(scene) if isinstance(scene, (int, float)) and not isinstance(scene, bool) and float(scene).is_integer() else None
    if scene not in shots:
        return dict(out, scene=scene, status="invalid", note=f"Không có cảnh {scene!r} trong shots.txt (có {len(shots)} cảnh).")
    start, end = shots[scene]
    low, high = number(item.get("from")), number(item.get("to"))
    low = start if low is None else low
    high = end if high is None else high
    if high < low:
        return dict(out, scene=scene, status="invalid", note="Khoảng giây sai: 'to' nhỏ hơn 'from'.")
    clamped_low, clamped_high = max(low, start), min(high, end)
    if clamped_high < clamped_low:
        return dict(out, scene=scene, status="invalid",
                    note=f"Khoảng {low:g}–{high:g}s nằm ngoài cảnh {scene} ({start:g}–{end:g}s). Ảnh xem kỹ không vượt điểm cắt.")
    if (clamped_low, clamped_high) != (low, high):
        out["notes"].append(f"Khoảng yêu cầu {low:g}–{high:g}s đã cắt về trong cảnh {scene} ({start:g}–{end:g}s).")
    if clamped_high - clamped_low > MAX_SPAN:
        clamped_high = clamped_low + MAX_SPAN
        out["notes"].append(f"Khoảng dài quá {MAX_SPAN:g}s: chỉ lấy {clamped_low:g}–{clamped_high:g}s. Muốn xem phần sau, gửi yêu cầu khác.")
    count = item.get("frames")
    count = int(count) if isinstance(count, (int, float)) and not isinstance(count, bool) and count >= 1 else DEFAULT_FRAMES
    crop = item.get("crop")
    if crop is not None:
        values = [number(v) for v in crop] if isinstance(crop, (list, tuple)) and len(crop) == 4 else []
        if (len(values) != 4 or any(v is None for v in values) or values[2] < .05 or values[3] < .05 or values[0] < 0
                or values[1] < 0 or values[0] + values[2] > 1.0001 or values[1] + values[3] > 1.0001):
            return dict(out, scene=scene, status="invalid", note="'crop' phải là [x, y, rộng, cao] theo tỉ lệ 0–1 của khung hình.")
        crop = [round(v, 4) for v in values]
    return dict(out, scene=scene, status="pending", **{"from": round(clamped_low, 3), "to": round(clamped_high, 3),
                                                       "frames": min(MAX_FRAMES, count), "crop": crop})


def read_requests(pack, shots):
    raw = read_json(Path(pack) / REQUEST_FILE)
    if raw is None:
        return None
    items = raw.get("requests") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    seen, out = set(), []
    for index, item in enumerate(items, 1):
        request = normalize(item, shots, index)
        while request["id"] in seen:  # trùng mã: đánh số lại để mỗi yêu cầu có kết luận riêng
            request["id"] += "_"
        seen.add(request["id"])
        out.append(request)
    return out


# ---- xử lý một yêu cầu --------------------------------------------------------------------------------------------------
def cache_key(request, video):
    payload = json.dumps([request["scene"], request["from"], request["to"], request["frames"], request["crop"],
                          video_signature(video)], sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def run_request(pack, video, request, shots, times, stop=None):
    """Trích dải khung liên tiếp của một yêu cầu. Trả về dict kết quả (đã có status)."""
    pack = Path(pack)
    start, end = shots[request["scene"]]
    key = cache_key(request, video)
    folder = pack / "review" / key
    manifest = read_json(folder / "index.json")
    if manifest and all((folder / f["file"]).is_file() for f in manifest.get("frames", [])):
        return dict(request, status="done", reused=True, dir=f"review/{key}", frames=manifest["frames"],
                    note=" ".join(request["notes"] + manifest.get("notes", [])))
    chosen = framelib.pick_range(times, start, end, request["from"], request["to"], request["frames"])
    notes = list(request["notes"])
    if not chosen:
        return dict(request, status="error", reused=False, frames=[],
                    note=" ".join(notes + [f"Không có khung hình nào trong {request['from']:g}–{request['to']:g}s của cảnh {request['scene']}."]))
    if folder.exists():
        for old in folder.glob("*.jpg"):
            old.unlink(missing_ok=True)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for position, moment in enumerate(chosen, 1):
        if stop is not None and stop.is_set():
            break
        target = folder / f"f{position:02d}_t{moment:08.3f}.jpg"
        real = framelib.extract(video, moment, target, width=REVIEW_WIDTH, crop=request["crop"], quality=2, fit="width")
        if real is None:
            notes.append(f"Không trích được khung tại {moment:.3f}s.")
            continue
        if not (start - framelib.EPS <= real < end - framelib.EPS):  # không bao giờ nhận ảnh của cảnh bên cạnh
            target.unlink(missing_ok=True)
            notes.append(f"Bỏ khung {moment:.3f}s: ảnh trích ra thuộc giây {real:.3f}, ngoài cảnh {request['scene']}.")
            continue
        framelib.label(target, f"C{request['scene']} · {clock(real)} · {position}/{len(chosen)}", size=max(18, REVIEW_WIDTH // 40))
        out.append({"file": target.name, "time": round(real, 3), "index": position, "scene": request["scene"]})
    write_json(folder / "index.json", {"scene": request["scene"], "from": request["from"], "to": request["to"],
                                       "crop": request["crop"], "frames": out, "notes": notes[len(request["notes"]):]})
    status = "done" if out else "error"
    return dict(request, status=status, reused=False, dir=f"review/{key}", frames=out, note=" ".join(notes))


def process(pack, stop=None):
    """Xử lý review_request.json của một gói. Trả về review_status.json đã ghi, hoặc None nếu không có yêu cầu / video."""
    pack = Path(pack)
    video = find_video(pack)
    shots = load_shots(pack)
    requests = read_requests(pack, shots)
    if requests is None or video is None:
        return None
    times = frame_times(pack, video, stop) if requests else []
    results = []
    accepted = 0
    for request in requests:
        if request["status"] == "invalid":
            results.append(dict(request, frames=[], reused=False))
            continue
        if accepted >= MAX_REQUESTS:
            results.append(dict(request, status="limit", frames=[], reused=False,
                                note=f"Đã đủ {MAX_REQUESTS} lượt xem lại cho dự án này. Ghi kết luận 'chưa xác định' cho cảnh này."))
            continue
        accepted += 1
        results.append(run_request(pack, video, request, shots, times, stop) if times else
                       dict(request, status="error", frames=[], reused=False,
                            note="Không đọc được mốc khung hình của video gốc (file còn trong input/ không?)."))
    status = {"updated": int(time.time()), "request_sig": request_signature(pack),
              "limits": {"requests": MAX_REQUESTS, "frames": MAX_FRAMES, "seconds": MAX_SPAN}, "requests": results}
    write_json(pack / STATUS_FILE, status)
    write_handoff(pack)
    return status


def request_signature(pack):
    try:
        stat = (Path(pack) / REQUEST_FILE).stat()
    except OSError:
        return ""
    return f"{stat.st_size}:{stat.st_mtime_ns}"


# ---- kết luận và bàn giao -----------------------------------------------------------------------------------------------
def conclusions(pack):
    """{mã yêu cầu: {conclusion, certainty, …}} từ review_conclusions.json (chấp nhận dạng dict hoặc danh sách)."""
    raw = read_json(Path(pack) / CONCLUSION_FILE, {})
    if isinstance(raw, dict) and isinstance(raw.get("conclusions"), (dict, list)):
        raw = raw["conclusions"]
    out = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                out[str(key)] = value
            elif isinstance(value, str) and value.strip():
                out[str(key)] = {"conclusion": value}
    elif isinstance(raw, list):
        for value in raw:
            if isinstance(value, dict) and value.get("id"):
                out[str(value["id"])] = value
    return {k: v for k, v in out.items() if str(v.get("conclusion") or "").strip()}


def pending(pack):
    """Mã các yêu cầu xem lại CHƯA có kết luận. File yêu cầu hỏng (đang ghi dở) tính là còn chờ."""
    pack = Path(pack)
    if not (pack / REQUEST_FILE).is_file():
        return []
    shots = load_shots(pack)
    requests = read_requests(pack, shots)
    if requests is None:
        return ["?"]
    answered = conclusions(pack)
    return [r["id"] for r in requests if r["id"] not in answered]


def write_handoff(pack):
    """review_handoff.md: bản giao cho Agent Viết prompt — kết luận đã xem lại, mức chắc chắn và các mốc làm căn cứ."""
    pack = Path(pack)
    status = read_json(pack / STATUS_FILE)
    if not status:
        return None
    answered = conclusions(pack)
    lines = ["# Bàn giao cho Agent Viết prompt — các cảnh đã xem lại kỹ",
             "",
             "Quy tắc: prompt chỉ mô tả ĐÚNG mức chắc chắn dưới đây. «chưa xác định» = mô tả điều thấy được (vd cú nhảy, vật che) "
             "và KHÔNG thêm kết quả không ai nhìn thấy (vd ngoạm được con mồi). Camera cắt sang góc khác = không suy ra hành động tiếp diễn.",
             ""]
    open_ids = []
    for request in status.get("requests", []):
        rid = request["id"]
        conclusion = answered.get(rid)
        lines.append(f"## Cảnh {request.get('scene')} · yêu cầu {rid} · {request.get('from', '?')}–{request.get('to', '?')}s")
        if request.get("question"):
            lines.append(f"- Câu hỏi của Agent Phân tích: {request['question']}")
        frames = request.get("frames") or []
        if frames:
            lines.append("- Ảnh xem kỹ (giây thật): " + ", ".join(f"{f['time']:.3f}s" for f in frames)
                         + f" — thư mục {request.get('dir')}")
        if request.get("note"):
            lines.append(f"- Lưu ý của tool: {request['note']}")
        if conclusion:
            certainty = str(conclusion.get("certainty") or "chưa ghi mức chắc chắn")
            lines.append(f"- Kết luận ({certainty}): {str(conclusion['conclusion']).strip()}")
            for key, title in (("contact", "Tiếp xúc"), ("evidence", "Căn cứ")):
                if conclusion.get(key):
                    value = conclusion[key]
                    lines.append(f"- {title}: {', '.join(map(str, value)) if isinstance(value, list) else value}")
        else:
            open_ids.append(rid)
            lines.append("- **CHƯA CÓ KẾT LUẬN**: Agent Phân tích phải ghi review_conclusions.json trước khi viết prompt cho cảnh này.")
        lines.append("")
    if open_ids:
        lines.insert(2, f"> CÒN {len(open_ids)} yêu cầu chưa có kết luận ({', '.join(open_ids)}). Chưa viết prompt cho các cảnh đó.")
    (pack / HANDOFF_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return open_ids


def scan(home, stop=None):
    """Xử lý mọi gói có yêu cầu mới / đã đổi trong <home>/input/. Trả về danh sách tên gói đã xử lý. Gọi định kỳ."""
    root = Path(home) / "input"
    done = []
    try:
        packs = [p for p in root.iterdir() if p.is_dir() and p.name.endswith("_pack")]
    except OSError:
        return done
    for pack in packs:
        if not (pack / REQUEST_FILE).is_file():
            continue
        status = read_json(pack / STATUS_FILE, {})
        if isinstance(status, dict) and status.get("request_sig") == request_signature(pack):
            if (pack / CONCLUSION_FILE).is_file():  # kết luận mới → cập nhật bản bàn giao
                try:
                    if (pack / HANDOFF_FILE).stat().st_mtime < (pack / CONCLUSION_FILE).stat().st_mtime:
                        write_handoff(pack)
                except OSError:
                    write_handoff(pack)
            continue
        try:
            if process(pack, stop) is not None:
                done.append(pack.name)
        except Exception:  # một gói lỗi không chặn gói khác; Agent thấy status cũ và có thể tự trích bằng ffmpeg
            continue
    return done


def protocol(pack_name, video_name, ffmpeg_path):
    """REVIEW.md trong gói: Agent đọc để biết cách xin xem lại và cách tự trích khung nếu HanaMedia không chạy."""
    return f"""# Xem kỹ cảnh khó (gói {pack_name})

Lượt đầu: xem TẤT CẢ tờ ảnh trong sheets/ (mỗi ô ghi «C<cảnh> · giây thật»; frames.txt liệt kê từng ảnh, cảnh ít khung hình
chỉ có 1–2 ô, ô còn lại để đen). Khi KHÔNG chắc ai làm gì với ai, hãy xin xem kỹ thay vì đoán.

## Khi nào phải xin xem kỹ
- Hai con vật áp sát, giao nhau, chồng lên nhau hoặc một con bị vật khác / con khác che.
- Chưa rõ chủ thể (con nào), hướng di chuyển, đối tượng của hành động hoặc kết quả tiếp xúc (chạm? bắt giữ? trượt?).
- motion.txt chỉ gợi ý đoạn cần xem: máy lia / rung làm cả khung đổi (điểm cao chưa chắc có hành động), còn con vật động nhỏ
  vẫn có thể quan trọng dù điểm thấp. Quyết định theo nội dung ảnh, không theo điểm số.

## Cách xin (HanaMedia tự trích, vài giây)
1. Ghi `review_request.json` trong thư mục gói này:
```json
{{"requests": [{{"id": "r1", "scene": 12, "from": 3.2, "to": 4.8, "question": "Báo có chạm dê không hay chỉ lao tới?",
               "frames": 8, "crop": [0.3, 0.2, 0.4, 0.5]}}]}}
```
   `from`/`to` là giây TRONG VIDEO (cùng thang với shots.txt), phải nằm trong cảnh; `frames` ≤ {MAX_FRAMES}; `crop` (không bắt buộc)
   = [x, y, rộng, cao] tỉ lệ 0–1 để phóng to vùng tiếp xúc. Tối đa {MAX_REQUESTS} yêu cầu mỗi dự án, mỗi yêu cầu ≤ {MAX_SPAN:g} giây.
2. Đợi `review_status.json` có yêu cầu của bạn với status `done` (thường < 30 giây; xem lại file mỗi vài giây, tối đa ~2 phút).
   Ảnh lớn ở `review/<thư mục>/fNN_tGGG.GGG.jpg`, nhãn trên ảnh = cảnh · giây thật · thứ tự. Ảnh chỉ thuộc cảnh bạn hỏi, không vượt điểm cắt.
3. Xem ảnh, rồi ghi `review_conclusions.json` cho TỪNG yêu cầu:
```json
{{"r1": {{"conclusion": "Báo nhảy về phía dê; điểm tiếp xúc bị tảng đá che", "certainty": "chưa xác định",
         "contact": "chưa xác định tiếp xúc", "evidence": ["3.40s", "3.60s"]}}}}
```
   certainty: «chắc chắn» · «có thể» · «chưa xác định». Cần xem thêm thì ghi thêm yêu cầu (còn lượt) rồi lặp lại.
4. Chỉ khi MỌI yêu cầu đã có kết luận mới ghi bước phân tích «done» trong output/_status/ và chuyển sang Agent Viết prompt.
   HanaMedia ghép `review_handoff.md` cho Agent Viết prompt đọc.

## Nếu HanaMedia không phản hồi (tool đã đóng)
Tự trích bằng ffmpeg trong thư mục HanaMedia, ví dụ 1 khung tại giây 3.40 của video input/{video_name}:
`"{ffmpeg_path}" -ss 3.40 -copyts -i input/{video_name} -frames:v 1 -vf scale=1280:-2 -q:v 2 {pack_name}/review/tay_3.40.jpg`
Vẫn chỉ lấy giây nằm trong cảnh (shots.txt) và ghi mốc giây thật khi viết kết luận.
"""
