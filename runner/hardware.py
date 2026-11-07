"""Đo cấu hình máy đang chạy tool để chọn số luồng tối đa hợp lý và bật tăng tốc GPU khi xuất video.

- Số luồng (tài khoản chạy cùng lúc): mỗi luồng là một Chrome đăng nhập Muse, cần khoảng 1,5 GB RAM và một phần
  CPU. Tối đa = ít nhất giữa (RAM - 4 GB cho Windows/ứng dụng khác) / 1,5 GB và số nhân CPU / 2, trong khoảng 1..8.
- GPU: thử bộ mã hóa video phần cứng (NVIDIA NVENC, Intel Quick Sync, AMD AMF) bằng một đoạn video thử rất ngắn;
  chạy được mới dùng để nâng 4K / ghép video, không được thì dùng CPU như cũ.
"""
import ctypes
import os
import subprocess
import threading

PER_CHROME_GB = 1.5   # RAM một Chrome Muse dùng khi tải ảnh tham chiếu / phát video 4K
RESERVE_GB = 4.0      # để lại cho Windows, app và bước xử lý video
HARD_CAP = 8          # trần an toàn dù máy rất mạnh

ENCODERS = {
    "h264_nvenc": ("NVIDIA (NVENC)", ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"]),
    "h264_qsv": ("Intel (Quick Sync)", ["-c:v", "h264_qsv", "-global_quality", "20", "-preset", "medium"]),
    "h264_amf": ("AMD (AMF)", ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20"]),
}
CPU_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]

_lock = threading.Lock()
_gpu = {"done": False, "name": None}


def memory_gb():
    """(Tổng RAM, RAM còn trống) tính bằng GB; không đọc được thì (None, None)."""
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                        ("page_avail", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                        ("virtual_avail", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
        status = Status()
        status.length = ctypes.sizeof(Status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.total / 1024 ** 3, status.avail / 1024 ** 3
        except (AttributeError, OSError):
            pass
        return None, None
    try:
        values = {}
        with open("/proc/meminfo") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                values[key] = int(rest.split()[0]) / 1024 ** 2
        return values.get("MemTotal"), values.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        return None, None


def recommended_threads(ram_gb, cores):
    by_ram = int((ram_gb - RESERVE_GB) // PER_CHROME_GB) if ram_gb else 2
    by_cpu = max(1, (cores or 2) // 2)
    return max(1, min(by_ram, by_cpu, HARD_CAP)), by_ram, by_cpu


def gpu_encoder(ffmpeg_path=None, runner=subprocess.run):
    """Tên bộ mã hóa GPU dùng được (vd 'h264_nvenc') hoặc None. Thử thật một lần rồi nhớ kết quả."""
    with _lock:
        if _gpu["done"]:
            return _gpu["name"]
        found = None
        try:
            if ffmpeg_path is None:
                from .video_tools import ffmpeg
                ffmpeg_path = ffmpeg()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            listed = runner([ffmpeg_path, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20,
                            creationflags=flags).stdout or ""
            for name, (_, args) in ENCODERS.items():
                if name not in listed:
                    continue
                test = runner([ffmpeg_path, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                               "color=c=black:s=1280x720:d=0.5", *args, "-f", "null", "-"],
                              capture_output=True, text=True, timeout=30, creationflags=flags)
                if test.returncode == 0:
                    found = name
                    break
        except Exception:
            found = None
        _gpu.update(done=True, name=found)
        return found


def encoder_args(use_gpu=True):
    """Tham số mã hóa video: GPU nếu có và được bật, không thì CPU (libx264)."""
    name = gpu_encoder() if use_gpu else None
    return (ENCODERS[name][1] if name else CPU_ARGS), name


def capacity():
    ram, free = memory_gb()
    cores = os.cpu_count() or 2
    best, by_ram, by_cpu = recommended_threads(ram, cores)
    return {"ram_gb": round(ram, 1) if ram else None, "free_gb": round(free, 1) if free else None, "cores": cores,
            "max_threads": best, "by_ram": by_ram, "by_cpu": by_cpu}
