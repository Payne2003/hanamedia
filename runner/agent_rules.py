"""Quy tắc nhận diện hành động của workflow Wildlife, gắn vào đúng các SKILL.md mà workflow thực sự nạp.

Workflow nạp kỹ năng từ  <HanaMedia>/workflows/wildlife/skills/<tên>/SKILL.md  (rồi chép sang .claude/skills và ~/.codex/skills).
Kỹ năng do người dùng nạp (phan-tich-video-doi-thu, wildlife-motion-prompt-meta, hanamedia-wildlife-workflow) không nằm trong
repo, nên tool ghi MỘT KHỐI quy tắc có mốc BEGIN/END vào cuối từng SKILL.md: ghi lại nhiều lần vẫn chỉ một khối, cập nhật khi
khối đổi, không đụng phần còn lại của kỹ năng.
"""
from pathlib import Path

VERSION = 2
MARK = "HANAMEDIA:action-analysis"


def begin(role):
    return f"<!-- {MARK}:{role} v{VERSION} BEGIN -->"


def end(role):
    return f"<!-- {MARK}:{role} END -->"


ANALYST = """## Nhận diện hành động — bổ sung của HanaMedia (bắt buộc)

Mục tiêu: biết CON NÀO đang làm GÌ, hướng tới CON NÀO, diễn biến THEO THỨ TỰ NÀO và kết quả nào THỰC SỰ nhìn thấy.

1. **Đối chiếu bằng giây thật.** Mỗi ảnh trong `sheets/` có nhãn «C<cảnh> <giây>»; `frames.txt` liệt kê số cảnh, giây thật, tờ, hàng, cột
   của từng ảnh. Cảnh chỉ có 1–2 khung thì chỉ có 1–2 ô (ô còn lại đen) — đừng suy ra khung không có. Sắp diễn biến theo giây thật.
2. **Ghi mọi hành động quan trọng thành một dòng** trong mục «Hành động quan trọng» ở CUỐI file phân tích (không đổi các bảng cũ):
   `Cảnh | Chủ thể (con nào, nhận dạng bằng gì) | Hành động | Đối tượng (hướng tới con nào) | Thứ tự (1→2→3) | Căn cứ (cảnh + giây thật
   của ảnh) | Kết quả quan sát được | Mức chắc chắn (chắc chắn / có thể / chưa xác định)`.
3. **Phân biệt ba mức và chỉ ghi mức nhìn thấy:** «lao về phía» (đang tiến tới, chưa thấy chạm) < «chạm vào» (thấy hai cơ thể tiếp xúc)
   < «bắt giữ» (thấy giữ / ngoạm / kẹp lại SAU tiếp xúc). Không thấy điểm tiếp xúc (bị đá, cỏ, con khác che) → ghi **«chưa xác định tiếp xúc»**,
   tuyệt đối không nâng lên «chạm» hay «bắt giữ».
4. **Camera cắt sang góc khác:** đánh dấu «CẮT CẢNH tại giây X» TRƯỚC khi suy luận. Không suy ra hành động tiếp diễn qua điểm cắt; con vật ở
   cảnh sau chỉ được coi là con ở cảnh trước khi có căn cứ nhận dạng (màu lông, vết, kích cỡ, vị trí).
5. **Cảnh khó thì XIN XEM KỸ, đừng đoán:** hai con áp sát, giao nhau, chồng lên nhau, bị che khuất, hoặc chưa rõ chủ thể / hướng đi / kết quả
   tiếp xúc → đọc `REVIEW.md` trong gói `input/<ten>_pack/`, ghi `review_request.json` (cảnh + khoảng giây nghi vấn + câu hỏi). HanaMedia tự
   trích dải khung liên tiếp từ video gốc vào `review/` và `review_status.json`; xem ảnh rồi ghi `review_conclusions.json` cho từng yêu cầu
   (kết luận + mức chắc chắn + giây làm căn cứ). `motion.txt` chỉ là GỢI Ý chỗ cần xem: máy lia / rung làm cả khung đổi, còn con vật chuyển động
   nhỏ vẫn có thể quan trọng dù điểm thấp — quyết định theo nội dung ảnh, không theo điểm số.
6. **Chỉ báo xong bước phân tích** (ghi `analyst: done`, bàn giao cho Agent Viết prompt) khi MỌI yêu cầu xem kỹ đã có kết luận và đã được
   đưa vào mục «Hành động quan trọng». Còn yêu cầu mở thì bước này vẫn là «working».
7. Giữ nguyên định dạng file đầu ra hiện có (Studio / Muse đọc chúng); chỉ THÊM mục «Hành động quan trọng», không sửa cột hay thứ tự cũ."""

WRITER = """## Giữ đúng mức chắc chắn của bản phân tích — bổ sung của HanaMedia (bắt buộc)

- Đọc mục «Hành động quan trọng» trong file phân tích và `input/<ten>_pack/review_handoff.md` (nếu có). **Còn dòng «CHƯA CÓ KẾT LUẬN» thì
  CHƯA viết prompt cho cảnh đó**; báo Agent Phân tích xử lý xong trước.
- Prompt phải giữ ĐÚNG mức chắc chắn: «chắc chắn» → mô tả đầy đủ; «có thể» → mô tả mềm (hướng tới, dường như), không khẳng định kết quả;
  «chưa xác định» → chỉ mô tả điều thấy được. Ví dụ: bản phân tích ghi «Báo nhảy về phía dê, điểm tiếp xúc bị đá che» thì prompt mô tả cú nhảy
  và tảng đá che khuất; KHÔNG tự thêm «ngoạm được dê», «vồ trúng», «kéo xuống».
- Giữ đúng chủ thể, đối tượng và thứ tự diễn biến của bản phân tích; camera đã cắt sang góc khác thì viết như cảnh mới, không nối hành động
  qua điểm cắt.
- Các quy tắc an toàn Meta / Muse và định dạng của kỹ năng này vẫn áp dụng: mỗi dòng một cảnh, `Wildlife_Prompts_EN_*.txt` / `hanamedia_*.json`
  giữ nguyên cấu trúc để tool đưa vào Studio."""

CONDUCTOR = """## Thứ tự bàn giao — bổ sung của HanaMedia (bắt buộc)

Sau bước phân tích: nếu `input/<ten>_pack/review_request.json` có yêu cầu thì ĐỢI HanaMedia trích ảnh (`review_status.json`) và Agent Phân tích
ghi `review_conclusions.json` cho từng yêu cầu. Chỉ chuyển sang Agent Viết prompt khi `review_handoff.md` không còn dòng «CHƯA CÓ KẾT LUẬN».
Ghi tiến độ `analyst: working` cho tới lúc đó."""

BLOCKS = {"analyst": ANALYST, "writer": WRITER, "conductor": CONDUCTOR}


def render(role):
    return f"{begin(role)}\n{BLOCKS[role]}\n{end(role)}\n"


def apply_block(skill_file, role):
    """Ghi (hoặc cập nhật) khối quy tắc `role` vào cuối SKILL.md. Trả về True nếu file thay đổi."""
    path = Path(skill_file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    block = render(role)
    start = text.find(f"<!-- {MARK}:{role} ")
    stop = text.find(end(role))
    if start != -1 and stop != -1 and stop > start:
        new = text[:start] + block + text[stop + len(end(role)):].lstrip("\n")
    else:
        new = text.rstrip("\n") + "\n\n" + block
    if new == text:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    return True
