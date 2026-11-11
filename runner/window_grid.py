"""Xếp cửa sổ Chrome của các tài khoản Muse thành lưới trên màn hình khi chạy hàng loạt.

Lưới cố định 3 cột × 2 hàng (tối đa 6 cửa sổ một màn hình), mỗi ô cùng kích thước nhỏ; ít tài khoản hơn thì vẫn
nằm theo hàng từ trái sang phải, trên xuống dưới. Hơn 6 cửa sổ thì xếp tiếp từ ô đầu, lệch xuống một chút để còn
thấy cửa sổ bên dưới. Ô được chọn là ô trống đầu tiên trong số các Chrome đang mở; Chrome đóng thì ô đó trống lại.
"""
import subprocess
import sys

COLUMNS, ROWS = 3, 2
SLOTS = COLUMNS * ROWS
CASCADE = 36  # lệch mỗi vòng khi có hơn 6 cửa sổ
DEFAULT_AREA = (0, 0, 1920, 1040)


def work_area():
    """(trái, trên, rộng, cao) vùng làm việc của màn hình chính (trừ thanh tác vụ), theo đơn vị Chrome dùng."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        elif sys.platform == "darwin":
            out = subprocess.run(["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
                                 capture_output=True, text=True, timeout=5).stdout
            left, top, right, bottom = (int(v) for v in out.replace(" ", "").split(","))
            top = max(top, 25)  # chừa thanh menu của macOS
            return left, top, right - left, bottom - top
    except Exception:
        pass
    return DEFAULT_AREA


def rect(slot, area=None):
    """Vị trí và kích thước (x, y, rộng, cao) của ô `slot` (0, 1, 2… theo hàng)."""
    left, top, width, height = area or work_area()
    cell_w, cell_h = width // COLUMNS, height // ROWS
    index, lap = slot % SLOTS, slot // SLOTS
    column, row = index % COLUMNS, index // COLUMNS
    shift = (lap * CASCADE) % max(1, min(cell_w, cell_h) // 2)
    return left + column * cell_w + shift, top + row * cell_h + shift, cell_w, cell_h


def free_slot(taken):
    """Ô trống đầu tiên (số nhỏ nhất chưa có cửa sổ nào dùng)."""
    taken = set(taken)
    slot = 0
    while slot in taken:
        slot += 1
    return slot


def place(driver, slot, area=None):
    """Đặt cửa sổ Chrome vào ô `slot`. Lỗi (cửa sổ đang phóng to, Chrome đóng…) thì bỏ qua."""
    x, y, width, height = rect(slot, area)
    try:
        driver.set_window_rect(x=x, y=y, width=width, height=height)
        return True
    except Exception:
        return False
