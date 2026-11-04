"""Hồ sơ Chrome dùng chung cho các phần dùng tài khoản Google (Gemini, ChatGPT, Google Flow).

Bật "Tài khoản chung" trong Cài đặt: cả ba dùng một hồ sơ Chrome (data/profiles/google-shared), đăng nhập một lần.
Chrome không cho hai cửa sổ mở cùng một hồ sơ, nên mỗi lúc chỉ một phần giữ Chrome của hồ sơ chung:
phần cần dùng sẽ yêu cầu phần đang giữ đóng Chrome của nó nếu đang rảnh; nếu phần đó đang chạy thì báo đợi.
"""
import threading
from pathlib import Path

SHARED_NAME = "google-shared"


def shared_dir(data):
    return Path(data) / "profiles" / SHARED_NAME


class SharedProfile:
    def __init__(self, data):
        self.dir = shared_dir(data)
        self.enabled = False
        self.lock = threading.Lock()
        self.holder = None
        self.releasers = {}
        self.login_process = None

    def register(self, name, release):
        """release(): đóng Chrome của phần đó nếu đang rảnh, trả về True; đang chạy thì trả về False."""
        self.releasers[name] = release

    def login_open(self):
        return bool(self.login_process and self.login_process.poll() is None)

    def take(self, name):
        """Gọi trước khi một phần mở Chrome tự động bằng hồ sơ chung."""
        if not self.enabled:
            return
        if self.login_open():
            raise RuntimeError("Cửa sổ đăng nhập tài khoản chung vẫn đang mở. Đăng nhập xong thì đóng hẳn cửa sổ đó rồi chạy lại.")
        with self.lock:
            holder = self.holder
            if holder and holder != name:
                if not self.releasers.get(holder, lambda: True)():
                    raise RuntimeError(f"{holder} đang dùng hồ sơ Google chung. Đợi {holder} chạy xong rồi bấm lại.")
            self.holder = name

    def drop(self, name):
        with self.lock:
            if self.holder == name:
                self.holder = None

    def release_all(self):
        """Đóng Chrome của mọi phần đang rảnh. Trả về tên các phần đang chạy (không đóng được)."""
        with self.lock:
            busy = [name for name, release in self.releasers.items() if not release()]
            if not busy:
                self.holder = None
            return busy
