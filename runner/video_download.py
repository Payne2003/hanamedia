"""Tải video gốc từ link (YouTube, TikTok, Facebook…) về máy để phân tích shot (MASTER PROMPT V3.0).

Dùng yt-dlp; ghép hình + tiếng bằng FFmpeg có sẵn trong app. Chỉ nhận link http(s), không dùng cookie hay tài khoản
của người dùng (video riêng tư/cần đăng nhập sẽ báo lỗi). Người dùng chịu trách nhiệm có quyền sử dụng video.
"""
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

MAX_SECONDS = 30 * 60
MAX_BYTES = 3 * 1024 ** 3


class DownloadError(ValueError):
    pass


def check_url(url):
    url = str(url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or any(c.isspace() for c in url) or len(url) > 2000:
        raise DownloadError("Link video không hợp lệ (cần bắt đầu bằng http:// hoặc https://).")
    host = parts.hostname.lower()
    if host in ("localhost",) or re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host) or host.endswith((".local", ".internal")):
        raise DownloadError("Chỉ tải video từ trang web công khai.")
    return url


def friendly(error):
    text = str(error)
    for pattern, message in (
            (r"proxy|timed out|connect|network|ssl|certificate|getaddrinfo|resolve",
             "Không kết nối được trang video. Kiểm tra mạng/VPN rồi thử lại."),
            (r"http error 429|too many", "Trang video đang giới hạn tải. Đợi một lúc rồi thử lại."),
            (r"\bprivate\b|sign in|log ?in\b|cookies|confirm your age|age[- ]restricted",
             "Video riêng tư hoặc cần đăng nhập; tool không dùng tài khoản của bạn để tải."),
            (r"unsupported url|no video formats|is not a valid url", "Trang này không có video tải được hoặc chưa được hỗ trợ."),
            (r"http error 404|not found|unavailable|removed", "Video không còn tồn tại hoặc bị chặn ở khu vực của bạn.")):
        if re.search(pattern, text, re.IGNORECASE):
            return message
    return "Không tải được video: " + re.sub(r"\x1b\[[0-9;]*m", "", text)[:200]


class _Quiet:
    """Không in lỗi yt-dlp ra cửa sổ dòng lệnh; lỗi được báo trên giao diện."""
    def debug(self, message):
        pass

    warning = info = error = debug


class VideoDownloader:
    """Một lượt tải mỗi lúc, chạy nền; giao diện đọc tiến độ qua status()."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.state = dict(id="", busy=False, progress=0, status="", path="", title="", error="", url="")

    def status(self):
        with self.lock:
            return dict(self.state)

    def set(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self, url):
        url = check_url(url)
        with self.lock:
            if self.state["busy"]:
                raise DownloadError("Đang tải một video khác. Đợi xong rồi tải tiếp.")
            job = uuid.uuid4().hex
            self.state = dict(id=job, busy=True, progress=0, status="Đang đọc thông tin video…", path="", title="",
                              error="", url=url)
        self.stop.clear()
        self.thread = threading.Thread(target=self.worker, args=(job, url), daemon=True, name="video-download")
        self.thread.start()
        return self.status()

    def cancel(self):
        self.stop.set()

    def options(self, target):
        from imageio_ffmpeg import get_ffmpeg_exe

        def hook(info):
            if self.stop.is_set():
                raise DownloadError("Đã dừng tải.")
            if info.get("status") == "downloading":
                total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                if total > MAX_BYTES:
                    raise DownloadError("Video lớn hơn 3 GB.")
                done = info.get("downloaded_bytes") or 0
                self.set(progress=min(99, int(done * 100 / total)) if total else 0,
                         status=f"Đang tải {done / 1048576:.0f} MB" + (f" / {total / 1048576:.0f} MB" if total else ""))
            elif info.get("status") == "finished":
                self.set(status="Đang ghép hình và tiếng…")

        return {
            "outtmpl": str(target / "%(title).80s [%(id)s].%(ext)s"), "restrictfilenames": False, "windowsfilenames": True,
            # Ưu tiên MP4 ≤1080p (đủ để phân tích shot), ghép hình + tiếng thành mp4.
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
            "merge_output_format": "mp4", "ffmpeg_location": get_ffmpeg_exe(), "noplaylist": True,
            "quiet": True, "no_warnings": True, "noprogress": True, "progress_hooks": [hook], "socket_timeout": 30,
            "retries": 3, "fragment_retries": 3, "match_filter": self.too_long, "cachedir": False,
            "logger": _Quiet(),
        }

    @staticmethod
    def too_long(info, *args, **kwargs):
        duration = info.get("duration") or 0
        if duration > MAX_SECONDS:
            return f"Video dài {duration // 60:.0f} phút; tối đa {MAX_SECONDS // 60} phút."
        return None

    def worker(self, job, url):
        try:
            import yt_dlp
            target = self.folder / time.strftime("%Y%m%d-%H%M%S")
            target.mkdir(parents=True, exist_ok=True)
            with yt_dlp.YoutubeDL(self.options(target)) as ydl:
                info = ydl.extract_info(url, download=True)
            if not info:
                raise DownloadError("Video bị bỏ qua (quá dài hoặc không tải được).")
            if info.get("_type") == "playlist":
                raise DownloadError("Link là danh sách phát; dán link của một video.")
            files = sorted((p for p in target.iterdir() if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")),
                           key=lambda p: p.stat().st_size, reverse=True)
            if not files:
                raise DownloadError("Không thấy file video sau khi tải (có thể trang chặn tải).")
            self.set(busy=False, progress=100, path=str(files[0]), title=str(info.get("title") or files[0].stem)[:200],
                     status="Đã tải xong video gốc.")
        except Exception as error:  # yt-dlp ném nhiều loại lỗi khác nhau
            message = str(error) if isinstance(error, DownloadError) else friendly(error)
            self.set(busy=False, error=message, status="Lỗi tải video")
