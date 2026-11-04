"""Đổi logo HANA MEDIA từ một ảnh PNG (nên là ảnh vuông, ít nhất 256×256, nền trong suốt hoặc bo góc sẵn).

    python packaging/make_icons.py duong-dan/logo.png

Tạo lại: web/logo.png (thanh bên trái), web/favicon.ico (tab trình duyệt), packaging/hanamedia.ico (file .exe,
bộ cài, shortcut Desktop). Sau đó đóng gói lại bằng DONG_GOI_WINDOWS.cmd (hoặc để GitHub tự build).
Cần Pillow: python -m pip install pillow
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main(source):
    from PIL import Image
    image = Image.open(source).convert("RGBA")
    side = min(image.size)  # cắt giữa thành hình vuông nếu ảnh không vuông
    left, top = (image.width - side) // 2, (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    image.resize((256, 256), Image.LANCZOS).save(ROOT / "web" / "logo.png")
    image.resize((256, 256), Image.LANCZOS).save(ROOT / "packaging" / "hanamedia.ico", sizes=SIZES)
    shutil.copyfile(ROOT / "packaging" / "hanamedia.ico", ROOT / "web" / "favicon.ico")
    print("Đã tạo web/logo.png, web/favicon.ico, packaging/hanamedia.ico")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
