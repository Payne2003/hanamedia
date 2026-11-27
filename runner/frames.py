"""Khung hình THỰC TẾ của video (mốc thời gian đọc từ chính file), dùng cho gói dữ liệu của Agent Phân tích.

Vì sao không lấy theo "giây danh nghĩa": cảnh 0,5 giây với mốc đầu +0,3 / giữa / cuối −0,35 cùng rơi vào giây 0,25 → ba ảnh
giống hệt nhau; video có tốc độ khung hình biến đổi thì "giây ×  fps" còn trỏ nhầm sang cảnh khác. Ở đây:
- frame_pts(): mốc hiển thị (pts) của TỪNG khung hình, đọc bằng showinfo (không phụ thuộc fps danh nghĩa);
- scene_frames_in(): các khung nằm đúng trong một cảnh [đầu, cuối), theo thứ tự thời gian;
- pick_overview(): tối đa 3 khung khác nhau (đầu · giữa · cuối) của cảnh, ít hơn nếu cảnh chỉ có 1–2 khung;
- extract(): trích đúng khung tại mốc đó và xác nhận lại mốc thật của ảnh vừa trích.
"""
import re
import subprocess
import threading
from pathlib import Path

from . import video_tools as video

EPS = 0.0007        # giây: dung sai khi so mốc khung hình với điểm cắt (điểm cắt làm tròn 3 chữ số thập phân: sai số ≤ 0,0005)
SHOWINFO = re.compile(r"\bn:\s*(\d+)\b.*?\bpts_time:\s*(-?[\d.]+)")
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def frame_pts(path, stop=None, timeout=1800):
    """Danh sách mốc thời gian (giây) của mọi khung hình, theo thứ tự hiển thị. [] nếu không đọc được."""
    process = subprocess.Popen([video.ffmpeg(), "-hide_banner", "-nostats", "-v", "info", "-i", str(path), "-map", "0:v:0",
                                "-vf", "scale=16:16,showinfo", "-f", "null", "-"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=FLAGS)
    times = []

    def read():
        for raw in process.stderr:
            match = SHOWINFO.search(raw.decode("utf-8", "replace"))
            if match:
                times.append(float(match.group(2)))
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        waited = 0.0
        while process.poll() is None:
            if stop is not None and stop.is_set():
                process.kill()
                raise RuntimeError("Đã dừng.")
            try:
                process.wait(0.5)
            except subprocess.TimeoutExpired:
                waited += 0.5
                if waited > timeout:
                    process.kill()
                    break
    finally:
        if process.poll() is None:
            process.kill()
        reader.join(10)
        process.stderr.close()
    times = sorted(t for t in times if t >= 0)
    return times


def scene_frames_in(times, start, end):
    """Mốc các khung thuộc cảnh [start, end): khung đầu tiên của cảnh có mốc đúng bằng điểm cắt `start`."""
    return [t for t in times if start - EPS <= t < end - EPS]


def pick_overview(times, start, end):
    """Tối đa 3 khung KHÁC NHAU theo thứ tự thời gian (đầu · giữa · cuối) của một cảnh.
    Cảnh có ≥ 6 khung: lùi vào trong 1 khung ở hai đầu (tránh khung chuyển cảnh / điểm cắt lệch một khung).
    Cảnh có 1 hoặc 2 khung: trả đúng số khung có được. Không có khung hợp lệ: []."""
    inside = scene_frames_in(times, start, end)
    count = len(inside)
    if count == 0:
        return []
    if count <= 2:
        picks = list(range(count))
    else:
        low, high = (1, count - 2) if count >= 6 else (0, count - 1)
        picks = sorted({low, (low + high) // 2, high})
    return [inside[i] for i in picks]


def pick_range(times, start, end, low, high, limit):
    """Các khung liên tiếp trong [low, high] ∩ cảnh [start, end). Nhiều hơn `limit` khung thì lấy cách đều (có cả đầu và cuối)."""
    inside = [t for t in scene_frames_in(times, start, end) if low - EPS <= t <= high + EPS]
    if len(inside) <= limit:
        return inside
    if limit <= 1:
        return inside[:1]
    return [inside[round(i * (len(inside) - 1) / (limit - 1))] for i in range(limit)]


def extract(path, moment, target, width=256, height=144, crop=None, quality=4, fit="pad"):
    """Trích đúng khung hình tại mốc `moment` (giây). Trả về mốc thật của ảnh (đọc từ showinfo) hoặc None nếu không trích được.
    fit="pad": nền đen cho đủ width×height (ô lát tờ ảnh); fit="width": giữ tỉ lệ, chỉ ép chiều rộng (ảnh xem kỹ).
    crop=(x, y, w, h) tỉ lệ 0–1 của khung gốc: phóng to vùng nghi vấn (điểm tiếp xúc)."""
    target = Path(target)
    filters = []
    if crop:
        x, y, w, h = crop
        filters.append(f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f}")
    if fit == "width":
        filters.append(f"scale={width}:-2:flags=lanczos")
    else:
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:-1:-1")
    filters.append("showinfo")
    # -copyts giữ mốc gốc của file nên showinfo báo đúng giây thật của khung (không bị tính lại từ điểm tua)
    result = video.run([video.ffmpeg(), "-hide_banner", "-nostats", "-v", "info", "-y", "-ss", f"{max(moment - EPS, 0):.4f}",
                        "-copyts", "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-vf", ",".join(filters),
                        "-q:v", str(quality), str(target)], 90)
    if not target.is_file() or not target.stat().st_size:
        return None
    match = SHOWINFO.search(result.stderr or "")
    return float(match.group(2)) if match else float(moment)


def black_tile(target, width, height):
    video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={width}x{height}",
               "-frames:v", "1", str(target)], 30)


def label(image_path, text, size=None):
    """Ghi nhãn (số cảnh · giây thực) lên góc ảnh để Agent đối chiếu. Không có Pillow thì bỏ qua (mốc vẫn nằm trong tên file
    và bảng frames.txt)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    try:
        with Image.open(image_path) as source:
            picture = source.convert("RGB")
        size = size or max(10, picture.width // 22)
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:  # Pillow cũ: chỉ có phông bitmap nhỏ
            font = ImageFont.load_default()
        draw = ImageDraw.Draw(picture)
        box = draw.textbbox((0, 0), text, font=font)
        width, height = box[2] - box[0] + 6, box[3] - box[1] + 6
        draw.rectangle((0, 0, width, height), fill=(0, 0, 0))
        draw.text((3, 1), text, fill=(255, 255, 0), font=font)
        picture.save(image_path, quality=92)
        return True
    except Exception:
        return False
