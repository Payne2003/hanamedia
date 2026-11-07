# PyInstaller: đóng gói HANA MEDIA thành thư mục dist/HanaMedia (HanaMedia.exe + thư viện).
# Dùng dạng thư mục (không phải một file .exe) để khởi động nhanh và ít bị phần mềm diệt virus báo nhầm.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
icon = root / "packaging" / "hanamedia.ico"

a = Analysis(
    [str(root / "desktop.py")],
    pathex=[str(root)],
    # web/: giao diện; selenium: gồm selenium-manager.exe để tự tải ChromeDriver khớp với Chrome trên máy.
    # imageio_ffmpeg: ffmpeg.exe để nâng độ phân giải và ghép video trên máy.
    datas=[(str(root / "web"), "web")] + collect_data_files("selenium") + collect_data_files("imageio_ffmpeg") + collect_data_files("certifi"),
    # Selenium nạp các phần con khi cần (lazy import) nên PyInstaller không tự thấy: gom hết để Chrome mở được trong bản .exe.
    hiddenimports=["app", "folder_picker", "webview", "imageio_ffmpeg", "cryptography.hazmat.primitives.asymmetric.ed25519", "certifi"] + collect_submodules("selenium"),
    excludes=["pytest", "unittest.mock"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="HanaMedia",
    console=False,
    icon=str(icon) if icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="HanaMedia")
