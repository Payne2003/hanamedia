# PyInstaller: đóng gói HANA MEDIA.
# - Windows: thư mục dist/HanaMedia (HanaMedia.exe + thư viện); dạng thư mục để khởi động nhanh, ít bị diệt virus báo nhầm.
# - macOS: dist/HanaMedia.app (đóng thành .dmg trong workflow).
import re
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
mac = sys.platform == "darwin"
icon = root / "packaging" / ("hanamedia.icns" if mac else "hanamedia.ico")
version = re.search(r'APP_VERSION = "([^"]+)"', (root / "runner" / "version.py").read_text(encoding="utf-8")).group(1)

a = Analysis(
    [str(root / "desktop.py")],
    pathex=[str(root)],
    # web/: giao diện; selenium: gồm selenium-manager.exe để tự tải ChromeDriver khớp với Chrome trên máy.
    # imageio_ffmpeg: ffmpeg.exe để nâng độ phân giải và ghép video trên máy.
    datas=[(str(root / "web"), "web")] + collect_data_files("selenium") + collect_data_files("imageio_ffmpeg") + collect_data_files("certifi"),
    # Selenium nạp các phần con khi cần (lazy import) nên PyInstaller không tự thấy: gom hết để Chrome mở được trong bản .exe.
    hiddenimports=["app", "folder_picker", "webview", "imageio_ffmpeg", "cryptography.hazmat.primitives.asymmetric.ed25519", "certifi", "openpyxl"] + collect_submodules("selenium") + collect_submodules("yt_dlp"),
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
if mac:
    app = BUNDLE(
        coll,
        name="HanaMedia.app",
        icon=str(icon) if icon.exists() else None,
        bundle_identifier="media.hana.studio",
        version=version,
        info_plist={
            "CFBundleDisplayName": "HANA MEDIA",
            "CFBundleName": "HANA MEDIA",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            # Tool điều khiển Chrome và mở cửa sổ chọn file/thư mục.
            "NSAppleEventsUsageDescription": "HANA MEDIA điều khiển Chrome để tạo video và mở hộp chọn file.",
        },
    )
