"""Thư mục của app.

- Chạy từ mã nguồn (CHAY_WINDOWS.cmd): giao diện và dữ liệu nằm cạnh app.py như trước (data/).
- Chạy bản cài đặt (HanaMedia.exe): giao diện nằm trong gói app; dữ liệu nằm ở %LOCALAPPDATA%\\HanaMedia\\data,
  tách khỏi thư mục cài đặt để cài bản mới đè lên không mất tài khoản, hàng đợi và các hồ sơ Chrome đã đăng nhập.
- Biến môi trường HANAMEDIA_DATA chỉ định thư mục dữ liệu khác (ví dụ để bản mã nguồn dùng chung dữ liệu với app).
"""
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "HanaMedia"
SOURCE_ROOT = Path(__file__).resolve().parent.parent


def frozen():
    return bool(getattr(sys, "frozen", False))


def resource_root():
    """Thư mục chứa web/ (trong gói PyInstaller là sys._MEIPASS)."""
    if frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return SOURCE_ROOT


def data_root():
    configured = os.environ.get("HANAMEDIA_DATA")
    if configured:
        return Path(configured).expanduser().resolve()
    if frozen():
        if sys.platform == "darwin":  # macOS: ~/Library/Application Support/HanaMedia/data
            return Path.home() / "Library" / "Application Support" / APP_NAME / "data"
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME / "data"
    return SOURCE_ROOT / "data"


def old_data_candidates():
    """Nơi thường có dữ liệu của bản chạy bằng mã nguồn (CHAY_WINDOWS.cmd)."""
    candidates = []
    extra = os.environ.get("HANAMEDIA_IMPORT")
    if extra:
        candidates.append(Path(extra).expanduser())
    for drive in ("D:", "C:", "E:"):
        for folder in ("Huy-Muse-Runner", "hanamedia", "HanaMedia"):
            candidates.append(Path(drive + "\\") / folder / "data")
    home = Path.home()
    for folder in ("Huy-Muse-Runner", "hanamedia", "HanaMedia"):
        for parent in (home, home / "Desktop", home / "Documents", home / "Downloads"):
            candidates.append(parent / folder / "data")
    return candidates


def has_data(folder):
    return (Path(folder) / "studio.sqlite3").is_file()


def import_old_data(target, candidates=None, lock_factory=None):
    """Lần đầu chạy bản cài đặt: chép toàn bộ dữ liệu cũ (tài khoản, hàng đợi, ảnh, hồ sơ Chrome đã đăng nhập)
    sang thư mục mới. Chỉ chép khi thư mục mới chưa có dữ liệu; không sửa hay xóa dữ liệu cũ.
    Bỏ qua thư mục cũ đang được bộ chạy khác dùng (không chép SQLite đang mở). Trả về thư mục đã chép hoặc None."""
    target = Path(target)
    if has_data(target):
        return None
    if lock_factory is None:
        from .instance import InstanceLock as lock_factory
    for source in candidates if candidates is not None else old_data_candidates():
        try:
            source = Path(source).resolve()
            if source == target.resolve() or not has_data(source):
                continue
            try:
                lock = lock_factory(source / "runner.lock")
            except OSError:
                continue  # bản cũ đang chạy: không chép cơ sở dữ liệu đang mở
            try:
                target.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("runner.lock", "Singleton*", "*.tmp"))
            finally:
                lock.close()
            (target / "imported-from.txt").write_text(str(source), encoding="utf-8")
            return source
        except OSError:
            continue
    return None
