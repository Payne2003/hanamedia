"""Nhiều luồng cho một tài khoản Muse: một Chrome, mỗi tác vụ một tab riêng.

Selenium chỉ điều khiển một tab tại một thời điểm. TabRouter chen vào mọi lệnh gửi tới ChromeDriver
(driver.execute — lệnh của trang lẫn của phần tử đều đi qua đây): trước khi chạy lệnh của luồng nào thì
chuyển sang đúng tab của luồng đó, có khóa để hai luồng không chen nhau giữa chừng một lệnh.

Đoạn nào cần nhiều lệnh liền mạch trên cùng một tab (gõ prompt rồi Enter, bấm Tải xuống rồi chờ file bắt đầu tải
vào đúng thư mục) thì giữ khóa bằng `with router.lock:` để tab khác không xen vào.
"""
import threading

SWITCH = "switchToWindow"
HANDLES = ("w3cGetWindowHandles", "getWindowHandles")
CURRENT = ("w3cGetCurrentWindowHandle", "getCurrentWindowHandle")
NEW_WINDOW = "newWindow"
CLOSE = "close"
QUIT = "quit"
NO_SWITCH = {QUIT, NEW_WINDOW, *HANDLES}


class TabRouter:
    def __init__(self, driver):
        self.driver = driver
        self.lock = threading.RLock()
        self.local = threading.local()
        self.original = driver.execute
        self.current = self.value(CURRENT)
        self.free = [self.current] if self.current else []
        self.busy = set()
        driver.execute = self.execute

    def value(self, commands):
        for command in commands:
            try:
                value = self.original(command, None)["value"]
            except Exception:
                continue
            if value is not None:
                return value
        return None

    def execute(self, command, params=None):
        with self.lock:
            want = getattr(self.local, "handle", None)
            if command == SWITCH:
                result = self.original(command, params)
                handle = (params or {}).get("handle") or (params or {}).get("name")
                self.current = handle
                if want is not None:
                    self.local.handle = handle  # luồng tự chuyển tab (vd tab hộp thư lúc đăng nhập): đi theo
                return result
            if want and want != self.current and command not in NO_SWITCH:
                self.original(SWITCH, {"handle": want})
                self.current = want
            result = self.original(command, params)
            if command == CLOSE:
                self.current = None
            return result

    def acquire(self):
        """Gắn một tab rảnh (hoặc mở tab mới) cho luồng đang gọi. Trả về (handle, tab_mới)."""
        with self.lock:
            handles = set(self.value(HANDLES) or [])
            self.free = [h for h in self.free if h in handles]
            self.busy &= handles
            fresh = not self.free
            if self.free:
                handle = self.free.pop(0)
            else:
                if handles and self.current not in handles:
                    # Tab đang chọn đã bị đóng / treo: chuyển sang một tab còn sống trước, để tab mới mở CÙNG cửa sổ
                    # (không bật thêm một cửa sổ Chrome mới cho cùng tài khoản).
                    alive = sorted(handles)[0]
                    self.original(SWITCH, {"handle": alive})
                    self.current = alive
                handle = self.original(NEW_WINDOW, {"type": "tab"})["value"]["handle"]
            self.busy.add(handle)
            self.local.handle = self.local.acquired = handle
            return handle, fresh

    def release(self):
        """Trả tab của luồng đang gọi về cho tác vụ sau."""
        handle = getattr(self.local, "acquired", None)
        self.local.handle = self.local.acquired = None
        with self.lock:
            if handle in self.busy:
                self.busy.discard(handle)
                self.free.append(handle)

    def active(self):
        with self.lock:
            return len(self.busy)
