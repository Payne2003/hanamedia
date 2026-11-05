"""Hậu kỳ video trên máy: đọc thông tin, phóng lên độ phân giải đích (4K…) và ghép nhiều cảnh thành một video dài.

Dùng ffmpeg đóng gói kèm app (thư viện imageio-ffmpeg; không cần cài thêm trên Windows). Phóng to dùng bộ lọc
lanczos: ảnh lớn hơn nhưng KHÔNG thêm chi tiết như AI upscale. File gốc luôn được giữ; kết quả ghi ra file tạm rồi mới
đổi tên, nên lỗi giữa chừng không để lại file hỏng.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

ASPECTS = ("16:9", "9:16", "1:1", "4:3", "3:4")
RESOLUTIONS = ("720p", "1080p", "4K")
DURATIONS = (4, 5, 6, 8, 10)  # Muse: tối đa 10 giây mỗi cảnh
SHORT_SIDE = {"720p": 720, "1080p": 1080, "4K": 2160}
LABELS = {"720p": "720p (HD)", "1080p": "1080p (Full HD)", "4K": "4K (Ultra HD, 3840×2160)"}


class VideoError(RuntimeError):
    pass


def ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if found:
            return found
    raise VideoError("Không tìm thấy ffmpeg để xử lý video. Cài lại app (bản cài có sẵn ffmpeg).")


def target_size(aspect, resolution):
    """Kích thước đích (rộng, cao) — số chẵn — theo tỉ lệ khung và độ phân giải. 4K 16:9 = 3840×2160."""
    if resolution not in SHORT_SIDE:
        return None
    a, b = (int(x) for x in (aspect if aspect in ASPECTS else "16:9").split(":"))
    short = SHORT_SIDE[resolution]
    if a >= b:
        width, height = round(short * a / b), short
    else:
        width, height = short, round(short * b / a)
    return width - width % 2, height - height % 2


def run(args, timeout):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise VideoError("Xử lý video quá lâu, đã dừng.") from None


def probe(path):
    """{"width", "height", "audio", "duration"} của một file video (đọc bằng ffmpeg -i)."""
    result = run([ffmpeg(), "-hide_banner", "-i", str(path)], 60)
    text = result.stderr
    video = re.search(r"Stream #\S+.*?Video:.*?(\d{2,5})x(\d{2,5})", text)
    if not video:
        raise VideoError(f"Không đọc được video {Path(path).name}.")
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]) if duration else 0.0
    return {"width": int(video[1]), "height": int(video[2]), "audio": bool(re.search(r"Stream #\S+.*?Audio:", text)),
            "duration": seconds}


def size_for(aspect, resolution, info):
    """Kích thước đích cho một video: theo tỉ lệ khung đã chọn; không chọn tỉ lệ thì giữ tỉ lệ thật của video."""
    if resolution not in SHORT_SIDE:
        return None
    if aspect in ASPECTS:
        return target_size(aspect, resolution)
    short = SHORT_SIDE[resolution]
    width, height = info["width"], info["height"]
    if width >= height:
        width, height = round(width * short / height), short
    else:
        width, height = short, round(height * short / width)
    return width - width % 2, height - height % 2


def needs_upscale(info, size):
    return bool(size) and (info["width"] < size[0] or info["height"] < size[1])


def fit(size):
    width, height = size
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1")


def finish(temporary, destination):
    os.replace(temporary, destination)
    return Path(destination)


def upscale(source, destination, size, timeout=3600):
    """Phóng video lên kích thước đích (giữ tỉ lệ, thêm viền nếu khác tỉ lệ). Không ghi đè file gốc."""
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise VideoError("File đích trùng file gốc.")
    temporary = destination.with_name(destination.stem + ".part.mp4")
    args = [ffmpeg(), "-hide_banner", "-y", "-i", str(source), "-vf", fit(size), "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temporary)]
    result = run(args, timeout)
    if result.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise VideoError("ffmpeg không nâng được độ phân giải video.")
    return finish(temporary, destination)


def stitch(sources, destination, size=None, timeout=7200):
    """Ghép các cảnh theo thứ tự thành một video. Các cảnh khác kích thước được đưa về cùng khung (size, hoặc khung
    của cảnh đầu); cảnh không có tiếng được chèn khoảng lặng để ghép liền mạch."""
    sources = [Path(source) for source in sources]
    if len(sources) < 2:
        raise VideoError("Cần ít nhất 2 cảnh đã xong để ghép.")
    infos = [probe(source) for source in sources]
    if not size:
        size = (infos[0]["width"] - infos[0]["width"] % 2, infos[0]["height"] - infos[0]["height"] % 2)
    args = [ffmpeg(), "-hide_banner", "-y"]
    for source in sources:
        args += ["-i", str(source)]
    filters, pairs, silent = [], [], len(sources)
    for index, info in enumerate(infos):
        filters.append(f"[{index}:v]{fit(size)},fps=30,format=yuv420p[v{index}]")
        if info["audio"]:
            filters.append(f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo[a{index}]")
        else:
            args += ["-f", "lavfi", "-t", f"{max(info['duration'], 0.1):.3f}", "-i",
                     "anullsrc=channel_layout=stereo:sample_rate=48000"]
            filters.append(f"[{silent}:a]anull[a{index}]")
            silent += 1
        pairs.append(f"[v{index}][a{index}]")
    filters.append(f"{''.join(pairs)}concat=n={len(sources)}:v=1:a=1[v][a]")
    temporary = Path(destination).with_name(Path(destination).stem + ".part.mp4")
    args += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
             "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temporary)]
    result = run(args, timeout)
    if result.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise VideoError("ffmpeg không ghép được các cảnh.")
    return finish(temporary, destination)


def request_spec(aspect, duration, resolution):
    """Dòng thông số gửi kèm prompt cho Muse (Muse quyết định kết quả thật; tool kiểm tra lại sau khi tải)."""
    parts = []
    if aspect in ASPECTS:
        parts.append(f"tỉ lệ khung {aspect}")
    if duration in DURATIONS:
        parts.append(f"thời lượng {duration} giây")
    if resolution in RESOLUTIONS:
        parts.append(f"độ phân giải {LABELS[resolution]}")
    return ("Thông số video: " + ", ".join(parts) + ".") if parts else ""
