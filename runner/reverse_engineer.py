"""Phân tích ngược video mẫu theo MASTER PROMPT V3.0 (Wildlife Documentary — frame-by-frame reverse engineering).

Phần công cụ (đo đạc được, không suy đoán): xác minh file, tìm ứng viên điểm cắt bằng sai khác khung hình, trích
khung hình kiểm tra cho từng shot, kiểm tra chất lượng timeline, thống kê nhịp dựng và xuất Excel 5 sheet.
Phần AI (agent) chỉ mô tả những gì thấy trong khung hình được gửi; mọi mục thiếu bằng chứng ghi "Chưa xác minh".
"""
import re
import statistics
import subprocess
from pathlib import Path

from . import video_tools as video

UNVERIFIED = "Chưa xác minh"
STATUSES = ("Đã xác minh bằng quan sát video", "Ước lượng từ khung hình", "Phát hiện tự động chưa kiểm chứng", "Chưa đủ thông tin")
SAMPLE = (32, 18)  # khung hình thu nhỏ để đo sai khác giữa các frame
MIN_SHOT = 0.25    # hai điểm cắt gần hơn mức này được gộp (nháy sáng, rung)


def clock(seconds):
    seconds = max(0.0, float(seconds))
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes):02d}:{rest:06.3f}"


def verify_source(path, stop=None):
    """GIAI ĐOẠN 1: mở file, thời lượng, FPS, độ phân giải, số khung hình, đọc được hình, có audio không."""
    from .production_media import run
    path = Path(path)
    result = video.run([video.ffmpeg(), "-hide_banner", "-i", str(path)], 60)
    text = result.stderr
    info = video.probe(path)
    fps_match = re.search(r"(\d+(?:\.\d+)?) fps", text)
    fps = float(fps_match.group(1)) if fps_match else 0.0
    decode = run([video.ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"], 1800, stop)
    readable = decode.returncode == 0
    checks = [
        dict(item="Mở được file", value="Có", status=STATUSES[0]),
        dict(item="Tổng thời lượng", value=clock(info["duration"]), status=STATUSES[0] if info["duration"] > 0 else STATUSES[3]),
        dict(item="FPS", value=f"{fps:g}" if fps else UNVERIFIED, status=STATUSES[0] if fps else STATUSES[3]),
        dict(item="Độ phân giải", value=f"{info['width']}×{info['height']}", status=STATUSES[0]),
        dict(item="Số khung hình (ước tính = thời lượng × FPS)", value=str(round(info["duration"] * fps)) if fps else UNVERIFIED,
             status=STATUSES[1] if fps else STATUSES[3]),
        dict(item="Đọc được hình ảnh (giải mã toàn bộ)", value="Có" if readable else "Lỗi giải mã",
             status=STATUSES[0] if readable else STATUSES[3]),
        dict(item="Audio track", value="Có" if info["audio"] else "Không", status=STATUSES[0]),
        dict(item="Đủ điều kiện phân tích điểm cắt", value="Có" if readable and info["duration"] > 0 else "Không",
             status=STATUSES[0] if readable else STATUSES[3]),
    ]
    return dict(duration=info["duration"], fps=fps, width=info["width"], height=info["height"], audio=info["audio"],
                frames=round(info["duration"] * fps) if fps else None, readable=readable, checks=checks)


def frame_differences(path, stop=None, timeout=1800):
    """Sai khác trung bình (0–1) giữa từng cặp khung hình liên tiếp, đo trên ảnh thu nhỏ 32×18."""
    width, height = SAMPLE
    size = width * height * 3
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen([video.ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf",
                                f"scale={width}:{height}:flags=area,showinfo", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=flags)
    import threading
    log = []
    reader = threading.Thread(target=lambda: log.append(process.stderr.read().decode("utf-8", "replace")), daemon=True)
    reader.start()
    previous, scores = None, []
    try:
        while True:
            if stop is not None and stop.is_set():
                from .production_media import Stopped
                raise Stopped("Đã dừng tìm điểm cắt theo yêu cầu.")
            chunk = process.stdout.read(size)
            if len(chunk) < size:
                break
            if previous is not None:
                scores.append(sum(abs(a - b) for a, b in zip(chunk, previous)) / (size * 255))
            previous = chunk
        process.wait(timeout)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    reader.join(5)
    process.stderr.close()
    times = [float(t) for t in re.findall(r"pts_time:\s*([\d.]+)", "".join(log))]
    return scores, times


def quick_shots(path, stop=None):
    """Đo điểm cắt nhanh (không giải mã kiểm tra toàn bộ như verify_source): dùng cho Gemini viết kịch bản đủ cảnh."""
    info = video.probe(path)
    text = video.run([video.ffmpeg(), "-hide_banner", "-i", str(path)], 60).stderr
    match = re.search(r"(\d+(?:\.\d+)?) fps", text)
    return detect_shots(path, info["duration"], float(match.group(1)) if match else 0.0, stop), info["duration"]


MOTION_SEGMENT = 10.0  # giây: chia đoạn video thành từng khúc để đo độ chuyển động
MOTION_FAST, MOTION_MEDIUM = .035, .018  # sai khác trung vị giữa hai khung liên tiếp (ảnh 32×18, thang 0–1)


def motion_plan(path, length, stop=None, segment=MOTION_SEGMENT):
    """Số khung/giây Gemini nên xem cho từng khúc ~10 giây: khúc chuyển động mạnh (rượt đuổi, đánh nhau, máy lia nhanh)
    xem 5 khung/giây, khúc vừa 3, khúc tĩnh 1. Dùng trung vị nên vài điểm cắt cảnh không làm khúc bị coi là "nhanh".
    Trả về [(bắt đầu, kết thúc, khung/giây)] đã gộp các khúc liền nhau cùng mức; lỗi đo thì trả [] (xem 1 khung/giây)."""
    try:
        scores, times = frame_differences(path, stop, timeout=300)
    except Exception:
        if stop is not None and stop.is_set():
            raise
        return []
    if not scores:
        return []
    if len(times) < len(scores) + 1:
        step = float(length) / max(1, len(scores))
        times = [i * step for i in range(len(scores) + 1)]
    plan = []
    at = 0.0
    while at < float(length) - .05:
        end = min(float(length), at + segment)
        inside = [score for score, time in zip(scores, times[1:]) if at <= time < end]
        level = statistics.median(inside) if inside else 0
        fps = 5 if level >= MOTION_FAST else 3 if level >= MOTION_MEDIUM else 1
        if plan and plan[-1][2] == fps:
            plan[-1] = (plan[-1][0], round(end, 2), fps)
        else:
            plan.append((round(at, 2), round(end, 2), fps))
        at = end
    return plan


def cut_clip(path, start, length, target, stop=None):
    """Cắt một đoạn video gốc (giây start, dài length) thành file nhẹ 480p không tiếng để gửi AI từng đoạn.
    Mã hóa lại (không copy) để đoạn bắt đầu đúng giây, không lệch về khung khóa trước đó."""
    if stop is not None and stop.is_set():
        raise video.VideoError("Đã dừng.")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = video.run([video.ffmpeg(), "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(path),
                        "-t", f"{max(0.5, length):.3f}", "-vf", "scale=-2:480", "-an", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", str(target)], 600)
    if result.returncode != 0 or not target.is_file() or target.stat().st_size < 1000:
        raise video.VideoError(f"Không cắt được đoạn {start:.0f}–{start + length:.0f}s của video gốc.")
    return target


TILE = (320, 180)


def contact_sheets(path, windows, folder, stop=None, status=lambda text: None, columns=6, max_sheets=8, tile=None):
    """Ảnh khung hình cho Gemini: mỗi hàng một cảnh, mỗi ô một góc máy (khung giữa của góc đó), ô đen = không có góc.
    Gộp thành tối đa `max_sheets` ảnh (Gemini web nhận giới hạn số tệp mỗi tin nhắn). Trả về danh sách ảnh JPG."""
    import math
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if not windows:
        return []
    if all(w.get("shot") for w in windows):
        # Chia cảnh theo góc quay (mỗi cảnh một góc): xếp các cảnh liền nhau từ trái sang phải, mỗi hàng `columns` cảnh.
        windows = [{"start": 0.0, "angles": [(float(w["start"]), float(w["end"])) for w in windows[i:i + columns]]}
                   for i in range(0, len(windows), columns)]
    rows = max(6, math.ceil(len(windows) / max_sheets))
    width, height = tile or TILE
    fit = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    black = folder / "black.jpg"
    video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}",
               "-frames:v", "1", str(black)], 30)
    sheets = []
    for first in range(0, len(windows), rows):
        group = windows[first:first + rows]
        cols = min(columns, max(len(w["angles"]) for w in group))
        tiles = folder / f"tiles-{len(sheets) + 1}"
        tiles.mkdir(exist_ok=True)
        index = 0
        for number, window in enumerate(group, first + 1):
            angles = window["angles"]
            if len(angles) > cols:  # nhiều góc hơn số ô: lấy đều
                angles = [angles[round(i * (len(angles) - 1) / (cols - 1))] for i in range(cols)] if cols > 1 else angles[:1]
            status(f"Đang trích khung hình cảnh {number}/{len(windows)} bằng FFmpeg…")
            for column in range(cols):
                if stop is not None and stop.is_set():
                    raise RuntimeError("Đã dừng.")
                index += 1
                target = tiles / f"{index:04d}.jpg"
                if column < len(angles):
                    a, b = angles[column]
                    moment = window["start"] + (a + b) / 2
                    video.run([video.ffmpeg(), "-v", "error", "-y", "-ss", f"{moment:.2f}", "-i", str(path), "-frames:v", "1",
                               "-vf", fit, "-q:v", "4", str(target)], 60)
                if not target.is_file():
                    target.write_bytes(black.read_bytes())
        sheet = folder / f"khung-hinh-{len(sheets) + 1}.jpg"
        video.run([video.ffmpeg(), "-v", "error", "-y", "-framerate", "1", "-start_number", "1", "-i", str(tiles / "%04d.jpg"),
                   "-vf", f"tile={cols}x{len(group)}", "-frames:v", "1", "-q:v", "3", str(sheet)], 120)
        if sheet.is_file():
            sheets.append(sheet)
    return sheets


def detect_shots(path, duration, fps, stop=None):
    """GIAI ĐOẠN 3: ứng viên điểm cắt = khung hình sai khác mạnh so với cả tuyệt đối lẫn xung quanh.
    Trả về danh sách shot liên tục phủ toàn bộ thời lượng, kèm điểm số và mức tin cậy của điểm cắt đầu shot."""
    scores, times = frame_differences(path, stop)
    if not times or len(times) < len(scores) + 1:
        step = 1 / fps if fps else 1 / 25
        times = [i * step for i in range(len(scores) + 1)]
    cuts = []
    for index, score in enumerate(scores):
        window = scores[max(0, index - 6):index] + scores[index + 1:index + 7]
        local = statistics.median(window) if window else 0
        if score >= .08 and score >= 3 * local + .04:
            cuts.append((times[index + 1], score, local))
    merged = []
    for cut in cuts:
        if merged and cut[0] - merged[-1][0] < MIN_SHOT:
            if cut[1] > merged[-1][1]:
                merged[-1] = cut
            continue
        if cut[0] < MIN_SHOT or duration - cut[0] < MIN_SHOT:
            continue
        merged.append(cut)
    bounds = [0.0] + [c[0] for c in merged] + [float(duration)]
    shots = []
    for number, (start, end) in enumerate(zip(bounds, bounds[1:]), 1):
        cut = merged[number - 2] if number > 1 else None
        score = cut[1] if cut else None
        confidence = ("Đầu video" if cut is None else "Cao" if score >= .25 else "Trung bình" if score >= .14 else "Thấp")
        shots.append(dict(id=f"{number:03d}", start=round(start, 3), end=round(end, 3), duration=round(end - start, 3),
                          cut_score=round(score, 4) if score is not None else None, cut_confidence=confidence))
    return shots


def scene_frames(path, window, folder, stop=None, limit=3):
    """Vài khung hình nhỏ (384×216) của một cảnh: khung giữa từng góc máy (tối đa `limit`). Dùng khi nhờ AI sửa góc
    quay của vài cảnh mà không gửi lại cả video."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    angles = list(window.get("angles") or [(0.0, float(window["end"]) - float(window["start"]))])
    if len(angles) > limit:
        angles = [angles[round(i * (len(angles) - 1) / (limit - 1))] for i in range(limit)] if limit > 1 else angles[:1]
    frames = []
    for index, (a, b) in enumerate(angles, 1):
        if stop is not None and stop.is_set():
            raise RuntimeError("Đã dừng.")
        moment = float(window["start"]) + (float(a) + float(b)) / 2
        target = folder / f"canh-{float(window['start']):.2f}-{index}.jpg"
        video.run([video.ffmpeg(), "-v", "error", "-y", "-ss", f"{moment:.2f}", "-i", str(path), "-frames:v", "1", "-vf",
                   "scale=384:216:force_original_aspect_ratio=decrease", "-q:v", "5", str(target)], 60)
        if target.is_file() and target.stat().st_size:
            frames.append(target)
    return frames


def shot_frames(path, shot, folder, stop=None, margin=.04, tail=.12):
    """Khung hình đầu, 25%, giữa, 75%, cuối của một shot (lệch vào trong vài khung để không dính shot bên cạnh;
    khung cuối lùi thêm vì khung hình cuối cùng của file thường không tua tới được)."""
    from .production_media import run
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    start, end = shot["start"], shot["end"]
    inner = max(0.0, (end - start) - margin - tail)
    frames = []
    for label, fraction in (("dau", 0), ("25", .25), ("giua", .5), ("75", .75), ("cuoi", 1)):
        time = round(start + margin + inner * fraction, 3) if inner else round((start + end) / 2, 3)
        target = folder / f"shot-{shot['id']}-{label}.jpg"
        result = run([video.ffmpeg(), "-v", "error", "-y", "-ss", str(time), "-i", str(path), "-frames:v", "1", "-vf",
                      "scale=512:512:force_original_aspect_ratio=decrease", "-q:v", "4", str(target)], 60, stop)
        if result.returncode == 0 and target.is_file() and target.stat().st_size:
            frames.append(dict(label=label, time=time, file=str(target)))
    return frames


def quality_check(shots, duration, tolerance=.05):
    """GIAI ĐOẠN 8 (phần đo được): tổng thời lượng, khoảng trống, chồng lấn, trùng lặp, thiếu mốc."""
    issues = []
    total = sum(s["end"] - s["start"] for s in shots)
    if abs(total - duration) > max(tolerance, duration * .002):
        issues.append(f"Tổng thời lượng shot {total:.3f}s lệch thời lượng video {duration:.3f}s.")
    for previous, current in zip(shots, shots[1:]):
        gap = current["start"] - previous["end"]
        if gap > .01:
            issues.append(f"Khoảng trống {gap:.3f}s giữa shot {previous['id']} và {current['id']}.")
        if gap < -.01:
            issues.append(f"Chồng lấn {-gap:.3f}s giữa shot {previous['id']} và {current['id']}.")
    seen = set()
    for shot in shots:
        key = (shot["start"], shot["end"])
        if key in seen:
            issues.append(f"Shot {shot['id']} trùng mốc với shot khác.")
        seen.add(key)
        if shot.get("start") is None or shot.get("end") is None or shot["end"] <= shot["start"]:
            issues.append(f"Shot {shot['id']} thiếu hoặc sai mốc bắt đầu/kết thúc.")
    return dict(total=round(total, 3), duration=round(duration, 3), ok=not issues, issues=issues)


def editing_stats(shots):
    """GIAI ĐOẠN 6 (phần đo được): nhịp cắt trung bình, độ biến thiên, shot dài/ngắn nhất."""
    lengths = [s["duration"] for s in shots] or [0]
    return dict(shots=len(shots), average=round(statistics.mean(lengths), 2), median=round(statistics.median(lengths), 2),
                stdev=round(statistics.pstdev(lengths), 2), shortest=round(min(lengths), 2), longest=round(max(lengths), 2),
                cuts_per_minute=round(60 * max(0, len(shots) - 1) / max(sum(lengths), .001), 1))


def text_of(value, default=UNVERIFIED):
    if value is None or value == "" or value == []:
        return default
    if isinstance(value, (list, tuple)):
        return "; ".join(text_of(v, "") for v in value if v not in (None, "")) or default
    if isinstance(value, dict):
        return "; ".join(f"{k}: {text_of(v, '')}" for k, v in value.items() if v not in (None, "", [])) or default
    return str(value)


SHOT_COLUMNS = ("Shot ID", "Chapter", "Start Time", "End Time", "Duration", "Environment", "Characters", "Character Count",
                "Camera Shot Size", "Camera Angle", "Camera Movement", "Character Position", "Movement Direction",
                "Opening Action", "Middle Action", "Ending Action", "Animal Behavior", "Natural Sound", "Music / Voice-over",
                "Transition Type", "Previous Shot Connection", "Next Shot Connection", "Narrative Function",
                "AI Recreation Difficulty", "Continuity Notes", "Verification Status", "Confidence Level", "Reference Frame Time")


def shot_row(shot, chapter):
    a = shot.get("analysis") or {}
    camera, action, environment = a.get("camera") or {}, a.get("action") or {}, a.get("environment") or {}
    characters = a.get("characters") or []
    middle = action.get("middle") or []
    middle_text = "; ".join(f"{clock(m.get('time', shot['start']))} {m.get('description', '')}" if isinstance(m, dict) else str(m)
                            for m in middle) if isinstance(middle, list) else text_of(middle)
    return [shot["id"], chapter or UNVERIFIED, clock(shot["start"]), clock(shot["end"]), round(shot["duration"], 3),
            text_of(environment), text_of([text_of(c) for c in characters] if isinstance(characters, list) else characters),
            a.get("character_count") if isinstance(a.get("character_count"), int) else UNVERIFIED,
            text_of(camera.get("shot_size")), text_of(camera.get("angle")),
            text_of(camera.get("movement")) + ("" if camera.get("movement_evidence") else " (không khẳng định từ ảnh tĩnh)"),
            text_of([c.get("frame_position") for c in characters if isinstance(c, dict)]), text_of(camera.get("screen_direction")),
            text_of(action.get("opening")), middle_text or UNVERIFIED, text_of(action.get("ending")), text_of(a.get("behavior")),
            text_of((a.get("sound") or {}).get("natural")), text_of((a.get("sound") or {}).get("music_voice")),
            text_of((a.get("transition") or {}).get("type")), text_of((a.get("transition") or {}).get("from_previous")),
            text_of(action.get("next_shot_condition")), text_of(a.get("narrative_function")), text_of(a.get("ai_difficulty")),
            text_of(a.get("continuity_notes")), text_of(a.get("verification"), STATUSES[3]), text_of(a.get("confidence")),
            ", ".join(clock(f["time"]) for f in shot.get("frames", [])) or UNVERIFIED]


def write_workbook(report, target):
    """GIAI ĐOẠN 7: file Excel 5 sheet, có bộ lọc, cố định tiêu đề, xuống dòng, màu tiêu đề và màu trạng thái."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    header_fill, header_font = PatternFill("solid", fgColor="1F3A5F"), Font(bold=True, color="FFFFFF")
    fills = {"ok": PatternFill("solid", fgColor="D9EAD3"), "warn": PatternFill("solid", fgColor="FFF2CC"),
             "bad": PatternFill("solid", fgColor="F4CCCC")}

    def status_fill(value):
        value = str(value)
        if value.startswith("Đã xác minh") or value in ("Cao", "Đã kiểm tra"):
            return fills["ok"]
        if value.startswith(("Ước lượng", "Phát hiện tự động")) or value in ("Trung bình", "Cần kiểm tra"):
            return fills["warn"]
        if value.startswith(("Chưa", "Không")) or value in ("Thấp",):
            return fills["bad"]
        return None

    def sheet(book, title, columns, rows, widths=None, status_columns=()):
        ws = book.create_sheet(title)
        ws.append(list(columns))
        for row in rows:
            ws.append(["" if v is None else v for v in row])
        for cell in ws[1]:
            cell.fill, cell.font = header_fill, header_font
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                if cell.column in status_columns:
                    fill = status_fill(cell.value)
                    if fill:
                        cell.fill = fill
        for index, column in enumerate(columns, 1):
            longest = max([len(str(column))] + [min(60, len(str(r[index - 1]))) for r in rows if index - 1 < len(r)])
            ws.column_dimensions[get_column_letter(index)].width = (widths or {}).get(index, max(10, min(48, longest + 2)))
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        return ws

    book = Workbook()
    book.remove(book.active)
    verify, story, stats = report["verification"], report.get("story") or {}, report.get("editing_stats") or {}
    verified = sum(1 for s in report["shots"] if str((s.get("analysis") or {}).get("verification", "")).startswith(("Đã xác minh", "Ước lượng")))
    overview = [("Tên video", report["source_name"]), ("Thời lượng", clock(verify["duration"])),
                ("Độ phân giải", f"{verify['width']}×{verify['height']}"), ("FPS", f"{verify['fps']:g}" if verify["fps"] else UNVERIFIED),
                ("Số khung hình (ước tính)", verify["frames"] or UNVERIFIED), ("Audio track", "Có" if verify["audio"] else "Không"),
                ("Tổng số shot đã xác minh (có phân tích khung hình)", verified),
                ("Số đoạn ứng viên chưa xác minh", len(report["shots"]) - verified),
                ("Nhân vật chính", text_of(story.get("who"))), ("Các bối cảnh", text_of(story.get("where"))),
                ("WHO", text_of(story.get("who"))), ("WHAT", text_of(story.get("what"))), ("WHERE", text_of(story.get("where"))),
                ("WHEN", text_of(story.get("when"))), ("WHY", text_of(story.get("why"))), ("HOW", text_of(story.get("how"))),
                ("Cấu trúc câu chuyện", text_of(story.get("structure"))),
                ("Nhịp cắt trung bình (giây/shot)", stats.get("average", UNVERIFIED)), ("Số lần cắt mỗi phút", stats.get("cuts_per_minute", UNVERIFIED)),
                ("Phân tích ngôn ngữ dựng phim", text_of(story.get("editing")))]
    overview += [(f"Kiểm tra: {c['item']}", f"{c['value']} — {c['status']}") for c in verify["checks"]]
    sheet(book, "OVERVIEW", ("Mục", "Nội dung"), overview, {1: 42, 2: 100})
    chapter_of = {}
    for chapter in story.get("chapters") or []:
        for shot_id in chapter.get("shots") or []:
            chapter_of[str(shot_id)] = chapter.get("name")
    rows = [shot_row(s, chapter_of.get(s["id"])) for s in report["shots"]]
    sheet(book, "SHOT BY SHOT", SHOT_COLUMNS, rows, status_columns=(26, 27))
    sheet(book, "STORY STRUCTURE", ("Chapter", "Thời gian", "Nhân vật", "Diễn biến", "Nguyên nhân – kết quả", "Điểm cao trào",
                                    "Nhịp dựng", "Vai trò câu chuyện"),
          [(c.get("name"), f"{clock(c.get('start', 0))}–{clock(c.get('end', 0))}", text_of(c.get("characters")), text_of(c.get("summary")),
            text_of(c.get("cause_effect")), text_of(c.get("climax")), text_of(c.get("pacing")), text_of(c.get("role")))
           for c in story.get("chapters") or []] or [(UNVERIFIED,) * 8])
    sheet(book, "CONTINUITY MAP", ("Shot trước", "Shot sau", "Nhân vật", "Vị trí", "Hướng di chuyển", "Trạng thái cuối",
                                   "Trạng thái đầu tiếp theo", "Liên kết", "Vấn đề phát hiện", "Đề xuất xử lý khi làm video AI"),
          [(c["previous"], c["next"], text_of(c.get("characters")), text_of(c.get("position")), text_of(c.get("direction")),
            text_of(c.get("end_state")), text_of(c.get("next_start_state")), text_of(c.get("link"), "Không đủ bằng chứng xác định"),
            text_of(c.get("issue"), "Không phát hiện"), text_of(c.get("suggestion"), "—")) for c in report.get("continuity") or []]
          or [(UNVERIFIED,) * 10], status_columns=(8,))
    sheet(book, "VERIFICATION & QC", ("Hạng mục", "Shot / mốc", "Chi tiết", "Trạng thái"),
          [(q["category"], q["where"], q["detail"], q["status"]) for q in report["qc_items"]] or [("Không có", "—", "—", "Đã kiểm tra")],
          {1: 34, 2: 18, 3: 80, 4: 18}, status_columns=(4,))
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    book.save(target)
    reopened = load_workbook(target, read_only=True)  # kiểm tra file mở được và đủ hàng trước khi bàn giao
    counts = {name: reopened[name].max_row - 1 for name in reopened.sheetnames}
    reopened.close()
    if counts.get("SHOT BY SHOT") != len(report["shots"]):
        raise ValueError("File Excel thiếu hàng shot sau khi ghi.")
    return counts
