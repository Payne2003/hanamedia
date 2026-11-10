import sys
from pathlib import Path


def main(output=None, video=False):
    import tkinter as tk
    from tkinter import filedialog
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if video:
        folder = filedialog.askopenfilename(title="Chọn video gốc",
                                            filetypes=[("Video", "*.mp4 *.mov *.mkv *.webm *.m4v *.avi"), ("Tất cả", "*.*")])
    else:
        folder = filedialog.askdirectory(title="Chọn thư mục lưu video HANA MEDIA", mustexist=True)
    root.destroy()
    if output:  # bản cài đặt chạy không có cửa sổ dòng lệnh: trả kết quả qua file
        Path(output).write_text(folder or "", encoding="utf-8")
    else:
        print(folder, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(video="--video" in sys.argv))
