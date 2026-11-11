"""Chatbot hỗ trợ khách hàng trong HANA MEDIA.

Thứ tự trả lời (đúng sơ đồ: FAQ ưu tiên → Quota Manager → Gemini hoặc Zalo):
1. FAQ & Knowledge Base có sẵn trong app: khớp câu hỏi thường gặp thì trả lời ngay, không tốn lượt, không gọi API.
2. Quota Manager: mỗi máy được DAILY_LIMIT câu hỏi AI mỗi ngày; chưa có bản quyền, hết lượt, hoặc khóa Gemini hết
   hạn mức → chuyển khách sang Zalo hỗ trợ.
3. Còn lượt → Gemini API (gói miễn phí) trả lời dựa trên kiến thức về tool.

Khóa Gemini của chủ tool KHÔNG nằm trong mã nguồn: bản cài được build với khóa nạp từ GitHub Secret
SUPPORT_GEMINI_API_KEY (packaging/write_support_key.py tạo runner/support_key.py lúc build, file này không commit).
Khóa nằm trong file cài nên vẫn có thể bị lấy ra: chỉ dùng khóa gói miễn phí, không bật thanh toán.
"""
import base64
import hashlib
import os
import re
import threading
import time
import unicodedata

ZALO = "0983105651"
ZALO_LINK = f"https://zalo.me/{ZALO}"
DAILY_LIMIT = 20
MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest")  # Flash-Lite có hạn mức miễn phí cao hơn: dự phòng
MAX_MESSAGE = 1000
SALT = b"hanamedia-support-v1"

ZALO_TEXT = (f"Bạn liên hệ Zalo hỗ trợ {ZALO} để được nhân viên hướng dẫn trực tiếp nhé.")

# (cụm từ khóa không dấu, câu trả lời). Khớp nhiều cụm nhất thì dùng câu đó.
FAQ = (
    (("cap nhat", "update", "phien ban moi", "ban moi"),
     "Khi có bản mới, app hiện thanh “⟳ Cập nhật ngay”; hoặc vào Cài đặt → Kiểm tra cập nhật. Cập nhật xong, tắt app rồi "
     "mở lại. Bản cài mới nhất (Windows .exe, Mac .dmg) cũng có ở trang Releases của tool."),
    (("lay api key", "api key gemini", "khoa gemini", "google ai studio"),
     "Vào Cài đặt → thẻ Gemini Pro → chọn “Gemini API key” → bấm “🔑 Lấy API key” (mở Google AI Studio) → Create API key → "
     "dán vào ô → “Lưu API key” → “↻ Tải danh sách model” → “Kiểm tra”."),
    (("429", "qua nhieu yeu cau", "gioi han toc do", "rate limit"),
     "Lỗi 429 là gửi nhanh hơn giới hạn/phút của API key. Vào Cài đặt → thẻ Gemini Pro, đặt “Số yêu cầu tối đa mỗi phút” "
     "= 2 (model Pro) hoặc 8–10 (model Flash). Tool sẽ tự đợi giữa các lần gửi."),
    (("exceeded your current quota", "het han muc", "quota"),
     "Hết hạn mức ngày của khóa Gemini miễn phí. Tool tự chuyển Pro → Flash → Flash-Lite; hết cả thì đợi đặt lại (khoảng "
     "14–15 giờ chiều giờ Việt Nam), đổi model, hoặc bật thanh toán cho API key ở Google AI Studio."),
    (("no credits", "het tien", "openai", "credits remaining"),
     "API OpenAI tính tiền riêng, không dùng chung gói ChatGPT Plus. Vào Cài đặt → thẻ ChatGPT, chọn “Gemini API · miễn "
     "phí” hoặc “ChatGPT Plus trên Chrome”."),
    (("chatgpt plus", "thinking", "che do chatgpt"),
     "Dùng gói Plus: Cài đặt → thẻ ChatGPT → “ChatGPT Plus trên Chrome” → “Đăng nhập ChatGPT” (đăng nhập rồi đóng hẳn cửa "
     "sổ) → chọn Chế độ Thinking (kỹ nhất) hoặc Auto (nhanh hơn). Hết lượt Thinking thì tool tự chuyển sang Auto."),
    (("them tai khoan muse", "tai khoan muse", "otp", "dang nhap muse"),
     "Bấm “Mở Tài khoản Muse” → dán email (mỗi dòng một email), đặt ngân sách credit → “+ Thêm tài khoản” → “Đăng nhập tất "
     "cả tài khoản chưa sẵn sàng”. Tool tự lấy OTP từ hộp thư; nếu lỗi thì nhập mã trực tiếp trên cửa sổ Muse."),
    (("chia canh", "cat canh", "goc quay", "ffmpeg"),
     "Ở Content Studio, ô “Chia cảnh”: “Theo góc quay” (mặc định — mỗi lần video gốc đổi góc máy là một cảnh, đúng số giây "
     "của góc đó) hoặc “10 giây một cảnh”. FFmpeg tìm điểm cắt và trích khung hình trên máy trước khi gửi Gemini."),
    (("viet prompt", "prompt chi tiet", "kieu prompt", "cham qua", "lau qua"),
     "Bước Viết prompt tiếng Anh: “Kiểu prompt” mặc định là Chi tiết theo mẫu, gửi lần lượt 3 cảnh mỗi lần, cảnh nào xong "
     "hiện ngay. Muốn nhanh hơn: ChatGPT chế độ Auto, hoặc bật thanh toán Gemini và tăng số yêu cầu/phút."),
    (("kiem duyet", "can kiem tra", "tao lai", "lock"),
     "Bấm “✔ Kiểm duyệt”: tool kiểm tra → tự sửa → kiểm tra lại → gắn KHÓA cuối mỗi prompt. Prompt còn lỗi sau 3 lần nằm "
     "trong “⚠ Cần kiểm tra”: bấm “↻ Tạo lại” prompt đó hoặc sửa tay."),
    (("thu muc", "luu video", "video o dau", "khong thay video", "lay video"),
     "Video tải về thư mục kết quả chọn ở thanh trên cùng của Studio (1.mp4, 2.mp4… theo thứ tự prompt). Video đã tạo mà "
     "không thấy: mở tác vụ trong hàng đợi, bấm “⇩ Lấy video”."),
    (("so luong chay", "bao nhieu luong", "luong chay", "chay nhieu tai khoan", "chay cung luc"),
     "Số luồng = số tài khoản Muse chạy cùng lúc; mỗi luồng là 1 Chrome, cần khoảng 1,5 GB RAM. Tool tự tính mức tối đa "
     "theo RAM và CPU (8 GB → 2 luồng; 16 GB/8 nhân → 4 luồng)."),
    (("ban quyen", "license", "key ban quyen", "ma may", "gia han"),
     "Vào trang Bản quyền & phân quyền: nhập key, bấm “📋 Sao chép mã máy” để gửi khi mua/gia hạn, “↻ Kiểm tra lại” sau "
     "khi gia hạn. Cần mua hoặc gia hạn: " + ZALO_TEXT),
    (("dinh kem", "khong tim thay nut"),
     "Gemini trên web hay đổi giao diện. Dùng Gemini API key (Cài đặt → thẻ Gemini Pro) để không phụ thuộc giao diện."),
)

KNOWLEDGE = """Bạn là trợ lý hỗ trợ khách hàng của phần mềm HANA MEDIA (Windows/Mac). Trả lời bằng tiếng Việt, ngắn gọn, \
từng bước, dùng đúng tên nút trong app. Chỉ trả lời về HANA MEDIA. Không bịa tính năng. Không hỏi hay nhận mật khẩu, \
mã OTP, API key của khách. Nếu không chắc hoặc cần xử lý tài khoản/bản quyền/thanh toán, nói khách liên hệ Zalo {zalo}.

KIẾN THỨC VỀ HANA MEDIA:
- Mục đích: từ video gốc (link YouTube hoặc file) tạo hàng loạt video mới trên Muse. Quy trình: Content Studio → cắt \
cảnh FFmpeg → Gemini phân tích cảnh → viết prompt tiếng Anh → kiểm duyệt & khóa → Studio chạy hàng loạt trên nhiều tài \
khoản Muse → video tải về thư mục kết quả.
- Cài đặt → thẻ Gemini Pro: “Gemini API key” (khuyên dùng) hoặc “Gemini trên Chrome”; nút 🔑 Lấy API key (Google AI \
Studio), Lưu, Kiểm tra, Tải danh sách model, Cấp độ tư duy, Số yêu cầu tối đa mỗi phút (khóa miễn phí: 2 cho Pro, 8–10 \
cho Flash). Model gợi ý: gemini-3-flash-preview hoặc gemini-flash-latest.
- Cài đặt → thẻ ChatGPT: “Gemini API · miễn phí” hoặc “ChatGPT Plus trên Chrome” (Đăng nhập ChatGPT, chế độ \
Thinking/Auto/Instant; hết lượt Thinking tự chuyển Auto). Tool không dùng API key OpenAI.
- Tài khoản Muse: email + OTP, không mật khẩu; Đăng nhập tất cả; Kiểm tra tất cả (không tốn credit); trạng thái Hết \
hạn mức thì prompt tự chuyển sang tài khoản khác; cửa sổ Chrome xếp lưới 3×2.
- Content Studio: dán link (Tự chạy ngay khi dán link) hoặc ⚡ Tự động từ link → prompt hoàn chỉnh; Chia cảnh theo góc \
quay hoặc 10 giây; xuất .txt/JSON/Excel; Viết prompt tiếng Anh (kiểu Chi tiết theo mẫu, 3 cảnh mỗi lần); ✔ Kiểm duyệt; \
→ Đưa vào Studio.
- Studio: mỗi dòng một prompt hoặc ↑ .txt; ảnh tham chiếu tối đa 10/prompt; tỉ lệ khung, thời lượng (Muse tối đa 10 \
giây), độ phân giải; xoay vòng nick; tự ghép video dài; số luồng; 🚀 Chạy ngay các luồng; hàng đợi lọc Chờ/Đang chạy/Xong/\
Lỗi; ⇩ Lấy video để tải lại.
- Lỗi: 429 → giảm số yêu cầu/phút; hết hạn mức ngày → tool tự đổi model, hoặc đợi/bật thanh toán; OpenAI no credits → \
dùng Gemini API hoặc ChatGPT Plus; lỗi khác → Cài đặt → 📋 Sao chép nhật ký lỗi rồi gửi Zalo {zalo}.
- Cập nhật: thanh “⟳ Cập nhật ngay” hoặc Cài đặt → Kiểm tra cập nhật, rồi tắt app mở lại."""


def plain(text):
    """Chữ thường, bỏ dấu tiếng Việt, gộp khoảng trắng."""
    text = unicodedata.normalize("NFD", str(text or "").lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def faq_answer(message):
    words = plain(message)
    best, score = None, 0
    for phrases, answer in FAQ:
        hits = sum(1 for phrase in phrases if phrase in words)
        if hits > score:
            best, score = answer, hits
    return best


def unmask(blob):
    data = base64.b64decode(blob)
    pad = hashlib.sha256(SALT).digest()
    return bytes(b ^ pad[i % len(pad)] for i, b in enumerate(data)).decode()


def mask(key):
    pad = hashlib.sha256(SALT).digest()
    return base64.b64encode(bytes(b ^ pad[i % len(pad)] for i, b in enumerate(key.encode()))).decode()


def built_key():
    """Khóa của chủ tool: biến môi trường (máy chủ tool / thử nghiệm) hoặc khóa nạp lúc build bản cài."""
    if os.environ.get("HANAMEDIA_SUPPORT_KEY"):
        return os.environ["HANAMEDIA_SUPPORT_KEY"].strip()
    try:
        from . import support_key  # tạo lúc build từ GitHub Secret; không có trong mã nguồn
        return unmask(support_key.KEY)
    except Exception:
        return ""


class SupportChat:
    def __init__(self, store, key_source=built_key, allowed=lambda: True):
        self.store, self.key_source, self.allowed = store, key_source, allowed
        self.lock = threading.Lock()

    def today(self):
        return time.strftime("%Y-%m-%d")

    def used(self):
        usage = self.store.setting("support_chat_usage") or {}
        return int(usage.get("count", 0)) if usage.get("day") == self.today() else 0

    def status(self):
        ai = bool(self.key_source())
        return {"ai": ai, "limit": DAILY_LIMIT, "remaining": max(0, DAILY_LIMIT - self.used()) if ai else 0,
                "zalo": ZALO, "zalo_link": ZALO_LINK}

    def zalo(self, text):
        return {"answer": text, "source": "zalo", "zalo": ZALO, "zalo_link": ZALO_LINK, **self.status()}

    def ask(self, message, history=()):
        message = re.sub(r"\s+", " ", str(message or "")).strip()
        if not message:
            raise ValueError("Nhập câu hỏi trước.")
        if len(message) > MAX_MESSAGE:
            raise ValueError(f"Câu hỏi tối đa {MAX_MESSAGE} ký tự.")
        answer = faq_answer(message)
        if answer:  # FAQ trả lời miễn phí, không tính lượt
            return {"answer": answer, "source": "faq", **self.status()}
        key = self.key_source()
        if not key:
            return self.zalo("Trợ lý AI chưa được bật trên bản cài này. " + ZALO_TEXT)
        if not self.allowed():
            return self.zalo("Trợ lý AI dành cho tài khoản có bản quyền còn hạn. " + ZALO_TEXT)
        with self.lock:
            if self.used() >= DAILY_LIMIT:
                return self.zalo(f"Bạn đã dùng hết {DAILY_LIMIT} lượt hỏi trợ lý AI hôm nay. " + ZALO_TEXT)
            text = self.generate(key, message, history)
            if text is None:
                return self.zalo("Trợ lý AI đang quá tải hoặc hết lượt chung. " + ZALO_TEXT)
            self.store.save_setting("support_chat_usage", {"day": self.today(), "count": self.used() + 1})
        return {"answer": text, "source": "ai", **self.status()}

    def generate(self, key, message, history):
        """Gọi Gemini; lỗi/hết hạn mức thì trả None (giao diện chuyển khách sang Zalo, không tính lượt)."""
        from . import gemini_api
        contents = []
        for turn in list(history or [])[-6:]:
            if isinstance(turn, dict) and turn.get("role") in ("user", "model") and isinstance(turn.get("text"), str):
                contents.append({"role": turn["role"], "parts": [{"text": turn["text"][:2000]}]})
        contents.append({"role": "user", "parts": [{"text": message}]})
        body = {"system_instruction": {"parts": [{"text": KNOWLEDGE.format(zalo=ZALO)}]}, "contents": contents,
                "generationConfig": {"maxOutputTokens": 800, "temperature": 0.3}}
        for model in MODELS:
            try:
                result = gemini_api.http("POST", f"{gemini_api.BASE}/v1beta/models/{model}:generateContent", key,
                                         payload=body, timeout=60)
            except gemini_api.HTTPFailure as error:
                if error.code in (429, 404, 500, 503):
                    continue  # hết hạn mức / quá tải model này: thử model dự phòng
                return None
            except Exception:
                return None
            parts = ((result.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            if text:
                return text[:4000]
        return None
