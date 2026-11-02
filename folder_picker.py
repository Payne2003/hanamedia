import sys
import tkinter as tk
from tkinter import filedialog

sys.stdout.reconfigure(encoding="utf-8")
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
folder = filedialog.askdirectory(title="Chọn thư mục lưu video Huy Studio", mustexist=True)
root.destroy()
print(folder, flush=True)
