"""Lúc build: ghi khóa Gemini cho chatbot hỗ trợ (từ GitHub Secret SUPPORT_GEMINI_API_KEY) vào runner/support_key.py.

Không có secret thì không ghi gì (chatbot chỉ trả lời FAQ và chuyển khách sang Zalo). Khóa được che (không phải mã hóa
mạnh); file này không bao giờ commit. Không in khóa ra nhật ký build.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner.support_chat import mask  # noqa: E402

key = os.environ.get("SUPPORT_KEY", "").strip()
target = Path(__file__).resolve().parents[1] / "runner" / "support_key.py"
if key:
    target.write_text(f'KEY = "{mask(key)}"\n', encoding="utf-8")
    print("Support chat key written.")
else:
    print("No SUPPORT_GEMINI_API_KEY: support chat uses FAQ + Zalo only.")
