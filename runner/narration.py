"""Thoại phim (chỉ máy Super Admin): gói thể loại, đọc kết quả thoai_<ten>.json của workflow, đưa sang AI Voice
(dự án nháp, mỗi đoạn thoại một dòng) và ghép giọng đã tạo vào video đúng mốc giây."""
import json
import re
import shutil
import time
from pathlib import Path

from . import narration_skills as pack
from . import video_tools as video
from .workflows import slugify

MAX_LINES = 2000
MAX_SAMPLE = 60_000
LANGUAGES = {"vi": "Tiếng Việt", "en": "English"}


class NarrationError(ValueError):
    pass


def genres_dir(home):
    return Path(home) / "workflows" / pack.SLUG / "skills" / pack.WRITER / "the-loai"


def genres(home):
    out = []
    base = genres_dir(home)
    for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        title = folder.name
        try:
            first = (folder / "phong-cach.md").read_text(encoding="utf-8").splitlines()[0]
            title = re.sub(r"^#\s*(Phong cách:)?\s*", "", first).strip() or title
        except (OSError, IndexError):
            pass
        samples = [p for p in (folder / "mau").glob("*.txt")] if (folder / "mau").is_dir() else []
        out.append({"slug": folder.name, "name": title[:60], "samples": len(samples)})
    return out


def genre_folder(home, slug):
    if not re.fullmatch(r"[a-z0-9-]{1,40}", str(slug or "")):
        raise NarrationError("Thể loại không hợp lệ.")
    folder = genres_dir(home) / slug
    if not folder.is_dir():
        raise NarrationError("Không tìm thấy thể loại.")
    return folder


def add_genre(home, name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:60]
    if not name:
        raise NarrationError("Đặt tên thể loại.")
    slug = slugify(name)
    folder = genres_dir(home) / slug
    if folder.exists():
        raise NarrationError("Thể loại này đã có.")
    (folder / "mau").mkdir(parents=True)
    (folder / "phong-cach.md").write_text(pack.GENRE_TEMPLATE_STYLE.format(name=name), encoding="utf-8", newline="\n")
    (folder / "tu-ngu.md").write_text(pack.GENRE_TEMPLATE_WORDS.format(name=name), encoding="utf-8", newline="\n")
    return slug


def add_sample(home, slug, text, title=""):
    """Lưu một kịch bản thoại mẫu vào the-loai/<slug>/mau/ — AI đọc mọi mẫu trước khi viết."""
    folder = genre_folder(home, slug) / "mau"
    text = str(text or "").replace("\r", "").strip()
    if len(text) < 40:
        raise NarrationError("Kịch bản mẫu quá ngắn (dán nguyên một bài thoại).")
    if len(text) > MAX_SAMPLE:
        raise NarrationError("Kịch bản mẫu quá dài (tối đa 60.000 ký tự).")
    folder.mkdir(parents=True, exist_ok=True)
    name = slugify(title) if str(title or "").strip() else "mau"
    target, n = folder / f"{name}.txt", 2
    while target.exists():
        target, n = folder / f"{name}-{n}.txt", n + 1
    target.write_text(text + "\n", encoding="utf-8", newline="\n")
    return target.name


# ---- kết quả của workflow ----
def _number(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse(path):
    path = Path(path)
    try:
        info = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise NarrationError(f"{path.name} không đọc được (có thể AI đang ghi dở).") from None
    if not isinstance(info, dict) or not isinstance(info.get("lines"), list):
        raise NarrationError(f"{path.name} thiếu danh sách lines.")
    lines, last = [], -1.0
    for item in info["lines"][:MAX_LINES]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        start, end = _number(item.get("start")), _number(item.get("end"))
        if not text or start is None or start < 0:
            continue
        start = max(start, last)  # mốc lùi → dời về sau đoạn trước
        end = end if end is not None and end > start else start
        lines.append({"n": len(lines) + 1, "start": round(start, 2), "end": round(end, 2), "text": text})
        last = start
    if not lines:
        raise NarrationError(f"{path.name} không có đoạn thoại nào.")
    return {"name": path.parent.name, "file": str(path), "video": str(info.get("video") or ""),
            "duration": _number(info.get("duration")), "genre": str(info.get("genre") or "")[:40],
            "language": str(info.get("language") or "")[:8], "character": str(info.get("character") or "")[:60],
            "lines": lines}


def link_file(path):
    return Path(path).parent / "thoai_voice.json"


def link(path):
    try:
        data = json.loads(link_file(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def inside(home, path):
    root = (Path(home) / "output").resolve()
    target = Path(path).resolve()
    return target.is_file() and target.is_relative_to(root) and target.name.startswith("thoai_") and target.suffix == ".json"


def find_video(home, data):
    """Video gốc của bài thoại: input/ hoặc input-done/<ten>/ (theo tên trong JSON, không có thì theo tên thư mục)."""
    from .claude_bridge import VIDEO_TYPES, short_name, source_name
    home = Path(home)
    names = [n for n in (short_name(data.get("video") or ""), source_name(Path(data["file"]).parent)) if n and n != "video"]
    for name in dict.fromkeys(names):
        for folder in (home / "input", home / "input-done" / name):
            for ext in VIDEO_TYPES:
                candidate = folder / f"{name}{ext}"
                if candidate.is_file():
                    return candidate
    return None


def results(home, jobs=()):
    """Các bài thoại trong output/ (mới nhất trước) + trạng thái dự án AI Voice đã tạo từ nó."""
    root = Path(home) / "output"
    items = []
    for path in sorted(root.glob("*/thoai_*.json")) if root.is_dir() else []:
        if path.name == "thoai_voice.json":
            continue
        try:
            data, error = parse(path), ""
        except NarrationError as problem:
            data, error = {"name": path.parent.name, "lines": [], "duration": None, "genre": "", "language": "",
                           "file": str(path)}, str(problem)
        info = link(path)
        group = info.get("group") or ""
        rows = [j for j in jobs if group and j.get("group") == group]
        spoken = sum(max(0.0, l["end"] - l["start"]) for l in data["lines"])
        muxed = path.parent / f"{path.parent.name}_thoai.mp4"
        items.append({"name": data["name"], "path": str(path), "lines": len(data["lines"]), "error": error,
                      "duration": data.get("duration"), "spoken": round(spoken, 1), "genre": data.get("genre", ""),
                      "language": data.get("language", ""), "updated": path.stat().st_mtime,
                      "preview": [l["text"] for l in data["lines"][:3]],
                      "group": group if rows else "", "voice_total": len(rows),
                      "voice_done": sum(1 for j in rows if j.get("status") == "done"),
                      "has_video": bool(not error and find_video(home, data)),
                      "muxed": str(muxed) if muxed.is_file() else "", "auto": bool(info.get("auto")),
                      "mux_error": info.get("mux_error") or "", "auto_error": info.get("auto_error") or ""})
    items.sort(key=lambda item: -item["updated"])
    return items[:60]


def to_voice(voice, path, profile_id, settings=None, start=False, auto=False):
    """Tạo dự án trong AI Voice: mỗi đoạn thoại một dòng (đúng thứ tự). start=False: để nháp cho người dùng sửa rồi bấm
    Tạo giọng nói; start=True: đọc luôn (chế độ tự động)."""
    from .voice import SILENCE
    settings = settings or {}
    data = parse(path)
    text = "\n".join(line["text"] for line in data["lines"])
    made = voice.create_batch(profile_id, text, f"Thoại · {data['name']}"[:80], settings.get("format") or "mp3",
                              settings.get("speed") or 1.0, SILENCE if settings.get("gap") is None else settings["gap"],
                              settings.get("nfe"), start=start)
    if len(made["jobs"]) != len(data["lines"]):
        raise NarrationError("Số dòng trong AI Voice không khớp số đoạn thoại.")
    link_file(path).write_text(json.dumps({"group": made["group"], "created": int(time.time()), "auto": bool(auto)}),
                               encoding="utf-8")
    return {"group": made["group"], "lines": len(made["jobs"]), "title": made["title"]}


def mux(home, voice, path, keep_original=True, original_volume=0.35):
    """Ghép giọng đã tạo vào video: mỗi đoạn bắt đầu đúng mốc start (đoạn trước đọc lố thì đoạn sau lùi lại, không
    chồng tiếng). Tiếng gốc của video (nhạc, tiếng động) được giữ và hạ nhỏ. Xuất output/<ten>/<ten>_thoai.mp4."""
    data = parse(path)
    group = link(path).get("group")
    rows = sorted((j for j in voice.jobs() if group and j.get("group") == group), key=lambda j: j.get("index", 0))
    if not rows:
        raise NarrationError("Chưa đưa bài thoại này sang AI Voice.")
    if len(rows) != len(data["lines"]):
        raise NarrationError(f"Dự án AI Voice có {len(rows)} dòng, bài thoại có {len(data['lines'])} đoạn — "
                             "đừng thêm / xóa dòng trong dự án (sửa chữ thì được). Đưa lại sang AI Voice nếu cần.")
    missing = [str(i + 1) for i, j in enumerate(rows) if j.get("status") != "done"]
    if missing:
        raise NarrationError("Chưa tạo xong giọng cho dòng " + ", ".join(missing[:12]) + ". Bấm Tạo giọng nói trong AI Voice.")
    source = find_video(home, data)
    if source is None:
        raise NarrationError("Không thấy video gốc trong input/ hoặc input-done/.")
    info = video.probe(source)
    out_dir = Path(path).parent
    temp = out_dir / ".thoai-mux"
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True)
    rate = 24000
    try:
        from .voice import audio_seconds
        entries, cursor, late = [], 0.0, []

        def silence(seconds, index):
            target = temp / f"gap_{index:04d}.wav"
            video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
                       "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(target)], 60)
            return target
        for index, (line, job) in enumerate(zip(data["lines"], rows)):
            clip_source = voice.output_file(job["id"])
            clip = temp / f"line_{index:04d}.wav"
            done = video.run([video.ffmpeg(), "-v", "error", "-y", "-i", str(clip_source), "-ac", "1", "-ar", str(rate),
                              "-c:a", "pcm_s16le", str(clip)], 120)
            if done.returncode != 0 or not clip.is_file():
                raise NarrationError(f"Không đọc được file giọng của dòng {index + 1}.")
            gap = line["start"] - cursor
            if gap > 0.02:
                entries.append(silence(gap, index))
                cursor += gap
            elif gap < -0.25:
                late.append(index + 1)
            entries.append(clip)
            cursor += audio_seconds(clip)
        listing = temp / "list.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in entries), encoding="utf-8")
        track = temp / "narration.wav"
        done = video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                          "-c:a", "pcm_s16le", str(track)], 600)
        if done.returncode != 0 or not track.is_file():
            raise NarrationError("Không ghép được các đoạn giọng.")
        target = out_dir / f"{out_dir.name}_thoai.mp4"
        partial = out_dir / f"{out_dir.name}_thoai.part.mp4"
        if keep_original and info.get("audio"):
            volume = max(0.0, min(1.0, float(original_volume)))
            mix = f"[0:a]volume={volume:.2f}[bg];[1:a]apad[nar];[bg][nar]amix=inputs=2:duration=first:normalize=0[a]"
        else:
            mix = "[1:a]apad[a]"
        done = video.run([video.ffmpeg(), "-v", "error", "-y", "-i", str(source), "-i", str(track), "-filter_complex", mix,
                          "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                          "-t", f"{info['duration']:.3f}", "-movflags", "+faststart", str(partial)], 3600)
        if done.returncode != 0 or not partial.is_file():
            partial.unlink(missing_ok=True)
            raise NarrationError("ffmpeg không ghép được giọng vào video.")
        partial.replace(target)
        over = round(cursor - info["duration"], 1)
        return {"output": str(target), "late": late, "over": over if over > 0.3 else 0}
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _update_link(path, **fields):
    data = link(path)
    data.update(fields)
    link_file(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def auto_step(home, voice, settings, since=0.0, log=lambda text: None):
    """Chế độ tự động (gọi định kỳ): bài thoại mới → tạo giọng ngay trong AI Voice; đọc xong đủ mọi dòng → ghép vào video
    đúng mốc giây. Chỉ xử lý bài thoại ghi sau thời điểm bật (since). Ghép lỗi tối đa 2 lần rồi để người dùng xem."""
    profile = str(settings.get("profile") or "")
    root = Path(home) / "output"
    if not root.is_dir() or not profile:
        return []
    done = []
    jobs = None
    for path in sorted(root.glob("*/thoai_*.json")):
        if path.name == "thoai_voice.json":
            continue
        try:
            if path.stat().st_mtime < since:
                continue
            if time.time() - path.stat().st_mtime < 20:
                continue  # AI có thể còn đang ghi file
        except OSError:
            continue
        info = link(path)
        if not info.get("group"):
            if info.get("auto_error"):
                continue
            try:
                if not voice.installed():
                    return done  # chưa cài AI Engine: chờ
                made = to_voice(voice, path, profile, settings, start=True, auto=True)
                done.append(f"Đã tạo giọng cho {path.parent.name} ({made['lines']} dòng)")
                log(done[-1])
            except Exception as error:  # bài thoại hỏng / giọng đã xóa: ghi lại, không thử mãi
                link_file(path).write_text(json.dumps({"auto_error": str(error)[:300]}, ensure_ascii=False), encoding="utf-8")
            continue
        if not info.get("auto") or info.get("muxed_at") or int(info.get("mux_tries") or 0) >= 2:
            continue
        if jobs is None:
            jobs = voice.jobs()
        rows = [j for j in jobs if j.get("group") == info["group"]]
        if not rows or any(j.get("status") != "done" for j in rows):
            continue
        try:
            result = mux(home, voice, path)
            _update_link(path, muxed_at=int(time.time()), mux_error="", late=result["late"], over=result["over"])
            done.append(f"Đã ghép giọng vào video: {Path(result['output']).name}")
            log(done[-1])
        except Exception as error:
            _update_link(path, mux_tries=int(info.get("mux_tries") or 0) + 1, mux_error=str(error)[:300])
    return done
