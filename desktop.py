"""HANA MEDIA dạng app Windows (điểm khởi động của HanaMedia.exe).

Mở bộ chạy (app.py) ở luồng nền và hiện giao diện trong một cửa sổ app riêng (pywebview + WebView2 có sẵn trên
Windows 10/11). Đóng cửa sổ = tắt tool: dừng nhận tác vụ mới, đóng mọi Chrome của tool.
Lần đầu chạy, tự chép dữ liệu cũ của bản mã nguồn (ví dụ D:\\Huy-Muse-Runner\\data) sang
%LOCALAPPDATA%\\HanaMedia\\data, gồm cả các hồ sơ Chrome đã đăng nhập.

Chạy thử không có cửa sổ: HanaMedia.exe --server-only (mở trong trình duyệt, Ctrl+C để tắt).
"""
import os
import sys
import threading
import webbrowser


def message(text, title="HANA MEDIA", question=False):
    """Hộp thông báo của Windows (bản .exe không có cửa sổ dòng lệnh để in lỗi)."""
    if sys.platform == "darwin":  # macOS: hộp thoại của hệ thống qua AppleScript
        try:
            import subprocess
            escape = lambda value: str(value).replace("\\", "\\\\").replace('"', '\\"')
            buttons = '{"Không", "Có"} default button "Có"' if question else '{"OK"} default button "OK"'
            result = subprocess.run(["osascript", "-e", f'display dialog "{escape(text)}" with title "{escape(title)}" '
                                     f"buttons {buttons}"], capture_output=True, text=True, timeout=600)
            return "Có" in result.stdout
        except Exception:
            print(text)
            return False
    try:
        import ctypes
        flags = 0x40 | (0x04 if question else 0x00) | 0x40000  # thông tin | Có/Không | luôn ở trên
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags) == 6
    except Exception:
        print(text)
        return False


def self_check():
    """HanaMedia.exe --self-check <file>: nạp mọi thư viện cần để điều khiển Chrome và mở cửa sổ app, ghi kết quả ra file.
    Dùng sau khi đóng gói để bắt lỗi thiếu thư viện (chạy được giao diện nhưng không mở được Chrome)."""
    import traceback
    index = sys.argv.index("--self-check")
    output = sys.argv[index + 1] if len(sys.argv) > index + 1 else None
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.action_chains import ActionChains  # noqa: F401
        from selenium.webdriver.common.keys import Keys  # noqa: F401
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        Service(log_output=None)
        from selenium.webdriver.common.selenium_manager import SeleniumManager
        manager = SeleniumManager()._get_binary()
        if not manager.exists():
            raise RuntimeError(f"Thiếu selenium-manager: {manager}")
        import openpyxl  # noqa: F401 — xuất Excel báo cáo phân tích ngược video
        import yt_dlp  # noqa: F401 — tải video gốc từ link
        import certifi  # bộ chứng chỉ dự phòng cho HTTPS (cập nhật app, thu hồi bản quyền)
        if not os.path.isfile(certifi.where()):
            raise RuntimeError("thiếu bộ chứng chỉ certifi")
        from runner.video_tools import ffmpeg, run
        if run([ffmpeg(), "-version"], 30).returncode != 0:  # nâng 4K / ghép video cần ffmpeg chạy được
            raise RuntimeError("ffmpeg không chạy được")
        from runner import license as licensing  # bản quyền: ký và kiểm tra key Ed25519 phải chạy được trong .exe
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        raw = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                          serialization.NoEncryption())
        public = licensing.public_from_private(raw)
        token = licensing.sign(raw, {"kid": licensing.key_id(public), "id": "self-check"})
        licensing.verify(token, {licensing.key_id(public): public})
        import tkinter  # noqa: F401  hộp chọn thư mục
        import webview  # noqa: F401  cửa sổ app
        import app  # noqa: F401
        from runner import characters, engine, gemini_web, muse  # noqa: F401
        result, code = "ok", 0
    except Exception:
        result, code = traceback.format_exc(), 1
    if output:
        from pathlib import Path
        Path(output).write_text(result, encoding="utf-8")
    else:
        print(result)
    return code


def main():
    if "--self-check" in sys.argv:
        return self_check()
    if "--pick-folder" in sys.argv or "--pick-video" in sys.argv:
        import app
        return app.main()
    import app
    from runner.paths import data_root, frozen, import_old_data
    from runner.version import APP_VERSION

    if frozen():
        source = import_old_data(data_root())
        if source:
            message(f"Đã chép dữ liệu cũ từ:\n{source}\n\nTài khoản, hàng đợi và các Chrome đã đăng nhập được giữ nguyên.\n"
                    f"Dữ liệu mới nằm ở:\n{data_root()}")
    try:
        server, engine, instance = app.start_runner()
    except RuntimeError as error:
        message(str(error))
        return 1
    except OSError:
        message("Không mở được bộ chạy. Kiểm tra quyền ghi thư mục dữ liệu:\n" + str(data_root()))
        return 1
    threading.Thread(target=server.serve_forever, daemon=True).start()
    title = f"HANA MEDIA {APP_VERSION}"
    try:
        if "--server-only" in sys.argv:
            webbrowser.open(server.origin)
            print(f"{title}: {server.origin} · dữ liệu {data_root()} · Ctrl+C để tắt")
            threading.Event().wait()
        try:
            import webview
        except Exception:
            webview = None
        if webview is None:
            webbrowser.open(server.origin)
            message(f"Không mở được cửa sổ app (máy thiếu Microsoft Edge WebView2). Tool đang mở trong trình duyệt:\n"
                    f"{server.origin}\n\nBấm OK để TẮT tool.")
        else:
            webview.create_window(title, server.origin, width=1360, height=900, min_size=(960, 640),
                                  confirm_close=True, text_select=True)
            webview.start(localization={
                "global.quitConfirmation": "Tắt HANA MEDIA? Tác vụ đang chạy sẽ dừng; tác vụ đã gửi cần kiểm tra lại trên Muse.",
                "global.ok": "Tắt", "global.cancel": "Không"})
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_runner(server, engine, instance, wait=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
