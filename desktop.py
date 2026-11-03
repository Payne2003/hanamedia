"""HANA MEDIA dạng app Windows (điểm khởi động của HanaMedia.exe).

Mở bộ chạy (app.py) ở luồng nền và hiện giao diện trong một cửa sổ app riêng (pywebview + WebView2 có sẵn trên
Windows 10/11). Đóng cửa sổ = tắt tool: dừng nhận tác vụ mới, đóng mọi Chrome của tool.
Lần đầu chạy, tự chép dữ liệu cũ của bản mã nguồn (ví dụ D:\\Huy-Muse-Runner\\data) sang
%LOCALAPPDATA%\\HanaMedia\\data, gồm cả các hồ sơ Chrome đã đăng nhập.

Chạy thử không có cửa sổ: HanaMedia.exe --server-only (mở trong trình duyệt, Ctrl+C để tắt).
"""
import sys
import threading
import webbrowser


def message(text, title="HANA MEDIA", question=False):
    """Hộp thông báo của Windows (bản .exe không có cửa sổ dòng lệnh để in lỗi)."""
    try:
        import ctypes
        flags = 0x40 | (0x04 if question else 0x00) | 0x40000  # thông tin | Có/Không | luôn ở trên
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags) == 6
    except Exception:
        print(text)
        return False


def main():
    if "--pick-folder" in sys.argv:
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
