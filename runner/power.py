"""Giữ máy không tự ngủ khi bộ chạy đang tạo video (màn hình vẫn được tắt).

Máy ngủ/ngủ đông giữa đêm làm Chrome mất kết nối với tool (lỗi "invalid session id"), tác vụ đang chờ video bị
gián đoạn. Windows: SetThreadExecutionState (gắn với LUỒNG gọi — phải gọi từ cùng một luồng sống lâu).
macOS: tiến trình `caffeinate -i` đi theo app. Máy khác: không làm gì.
"""
import os
import subprocess
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    def __init__(self):
        self.on = False
        self.process = None

    def set(self, wanted):
        wanted = bool(wanted)
        if wanted == self.on:
            return self.on
        try:
            if sys.platform == "win32":
                import ctypes
                flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if wanted else 0)
                ctypes.windll.kernel32.SetThreadExecutionState(flags)
            elif sys.platform == "darwin":
                if wanted:
                    self.process = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif self.process:
                    self.process.terminate()
                    self.process = None
        except Exception:
            return self.on  # không giữ được thì thôi, không làm hỏng bộ chạy
        self.on = wanted
        return self.on
