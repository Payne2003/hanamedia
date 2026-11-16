"""Kỹ năng gốc của workflow "Thoại phim" (chỉ máy Super Admin). Tool chép vào workflows/thoai-phim/skills/ lần đầu;
sau đó bản trên máy là bản chính (AI sửa theo ô "Yêu cầu thay đổi", gói thể loại do người dùng nạp mẫu)."""

SLUG = "thoai-phim"
WRITER = "bien-kich-thoai"

WORKFLOW = {
    "name": "Thoại phim", "builtin": True,
    "command": "Chạy thoai-phim-workflow",
    "description": "Video đã dựng → phân tích cảnh → viết thoại theo thể loại, khớp số giây → kiểm duyệt → AI Voice.",
    "agents": {
        "conductor": {"name": "Master Agent", "role": "Điều phối thoại", "skill": "thoai-phim-workflow"},
        "analyst": {"name": "Agent 1", "role": "Phân tích video", "skill": "phan-tich-video-doi-thu"},
        "writer": {"name": "Agent 2", "role": "Biên kịch thoại", "skill": WRITER},
        "studio": {"name": "Agent 3", "role": "Kiểm duyệt thoại", "skill": "kiem-duyet-thoai"},
    },
}

CONDUCTOR = '''---
name: "thoai-phim-workflow"
description: "Điều phối: video đã dựng trong input/ → phân tích cảnh → viết thoại theo gói thể loại → kiểm duyệt → xuất thoai_<ten>.json/.txt/.srt cho tool HanaMedia (AI Voice)."
---
# Thoại phim — điều phối

Đầu vào (thư mục HanaMedia):
- `input/<ten-ngan>.mp4` là video ĐÃ DỰNG XONG cần viết thoại (lời dẫn). Chỉ làm video đang có trong `input/`.
- `input/<ten-ngan>_pack/` (nếu có): `shots.txt` (số giây từng cảnh), `sheets/*.jpg` + `sheets.txt` (3 khung mỗi cảnh),
  `motion.txt`. Dùng luôn, xem ĐỦ mọi tờ ảnh. Không có pack thì tự dò điểm cắt bằng ffmpeg và tự trích khung.
- `workflows/thoai-phim/brief.json`: `genre` (tên thư mục gói thể loại), `language` (vi | en), `note` (ghi chú riêng).

## Bước 1 — Phân tích cảnh (Agent 1)
Nếu có kỹ năng `phan-tich-video-doi-thu` thì dùng nó; không có thì tự lập bảng cảnh: mỗi cảnh một dòng gồm số cảnh,
giây đầu–cuối, nhân vật trong khung (tên cố định), hành động, cảm xúc/cường độ. Ghi `output/<ten-ngan>/phan-tich-canh_<ten-ngan>.md`.
Đã có file này của đúng video thì dùng lại, không phân tích lại.

## Bước 2 — Viết thoại (Agent 2)
Gọi kỹ năng `bien-kich-thoai` với bảng cảnh, thể loại và ngôn ngữ trong brief. Kết quả: danh sách đoạn thoại có mốc giây.

## Bước 3 — Kiểm duyệt (Agent 3)
Gọi kỹ năng `kiem-duyet-thoai`. Đoạn nào không đạt thì trả lại Agent 2 sửa đúng đoạn đó; tối đa 2 vòng.

## Bước 4 — Xuất file cho tool
Ghi vào `output/<ten-ngan>/` (UTF-8 không BOM, xuống dòng LF), tạo bằng script, không gõ tay:
1. `thoai_<ten-ngan>.json`:
```json
{"video": "<ten-ngan>.mp4", "duration": 58.4, "genre": "san-duoi", "language": "vi", "character": "Báo mẹ",
 "lines": [{"n": 1, "start": 0.0, "end": 6.2, "shots": [1, 2], "text": "Câu thoại…"}]}
```
   - `start` < `end`, tăng dần, không chồng nhau, không vượt `duration`. Khoảng lặng có chủ ý thì KHÔNG tạo dòng.
   - `text` là đúng lời sẽ đọc: không ghi chú, không ngoặc, không emoji; số viết bằng chữ.
2. `thoai_<ten-ngan>.txt`: mỗi dòng một `text`, đúng thứ tự `n`.
3. `thoai_<ten-ngan>.srt`: phụ đề theo `start`/`end`.

Trong lúc chạy ghi tiến độ `output/_status/thoai-phim.json`. Không hỏi lại người dùng giữa chừng. Kết thúc: nói ngắn gọn
đã ghi file nào, bao nhiêu đoạn thoại, tổng số giây có thoại.
'''

WRITER_SKILL = '''---
name: "bien-kich-thoai"
description: "Viết thoại (lời dẫn) cho video đã dựng theo gói thể loại trong the-loai/<ten>/, mỗi đoạn khớp số giây của cảnh."
---
# Biên kịch thoại

## Trước khi viết
1. Đọc gói thể loại `the-loai/<genre>/`: `phong-cach.md`, `tu-ngu.md` và MỌI file trong `mau/` (kịch bản mẫu chủ kênh ưng).
   Bắt chước nhịp câu, độ dài câu, cách mở và cách kết của mẫu; KHÔNG chép lại câu của mẫu.
   Không có `genre` hoặc thư mục không tồn tại thì dùng `the-loai/tai-lieu/`.
2. Đọc bảng cảnh và xem tờ ảnh: chỉ viết điều NHÌN THẤY trong hình hoặc kiến thức chắc chắn đúng về loài.

## Chia đoạn thoại
- Gộp các cảnh liên tiếp cùng một ý thành một đoạn 4–12 giây. Cảnh dưới 2 giây không đứng riêng.
- Để lặng có chủ ý ở cao trào hình ảnh (vồ mồi, va chạm, cú ngoặt): hình và nhạc tự kể. Tổng thời gian có thoại
  khoảng 55–75% thời lượng video, trừ khi `phong-cach.md` ghi khác.
- Đoạn mở đầu (3–6 giây đầu) phải là câu móc giữ người xem theo đúng kiểu của thể loại.

## Khớp thời lượng (bắt buộc)
- Tiếng Việt: tối đa `số giây × 3,0` âm tiết (mỗi tiếng cách nhau bởi dấu cách là một âm tiết); nên dùng 80–90% mức này.
- Tiếng Anh: tối đa `số giây × 2,4` từ.
- `phong-cach.md` có ghi tốc độ đọc riêng thì theo file đó.
- Đếm bằng script, không ước lượng. Đoạn vượt mức thì viết ngắn lại, không tăng tốc độ đọc.

## Cách viết
- Câu nói, không phải câu văn: ngắn, một ý một câu, đọc lên trôi miệng. Tránh mệnh đề lồng nhau.
- Một nhân vật một tên gọi trong cả video (lấy từ bảng nhân vật).
- Không mô tả lại y nguyên hình ("con báo đang chạy") — thêm lý do, cái được mất, điều sắp xảy ra.
- Không bịa số liệu, địa danh, tên riêng, quan hệ họ hàng không thấy trong hình. Không chắc thì nói chung chung.
- Số, đơn vị viết bằng chữ ("ba mươi mét"). Không viết tắt, không ký hiệu.
- Ghi chú riêng trong brief (`note`) được ưu tiên hơn gói thể loại.

## Kết quả
Bảng đoạn thoại: `n | start | end | shots | số âm tiết (hoặc từ) / mức tối đa | text`. Giao cho điều phối.
'''

REVIEWER = '''---
name: "kiem-duyet-thoai"
description: "Kiểm duyệt thoại trước khi xuất: đúng hình, vừa thời lượng, không bịa, đúng giọng thể loại, đọc được bằng TTS."
---
# Kiểm duyệt thoại

Kiểm từng đoạn, ghi ĐẠT / SỬA kèm lý do một dòng:
1. **Đúng hình**: điều câu thoại nói có trong các cảnh `shots` của đoạn (đối chiếu bảng cảnh + tờ ảnh). Không nói trước
   điều chưa xảy ra trên hình quá 2 giây, không nói về thứ đã qua.
2. **Vừa thời lượng**: đếm lại bằng script. Tiếng Việt ≤ giây × 3,0 âm tiết; tiếng Anh ≤ giây × 2,4 từ
   (hoặc mức trong `phong-cach.md`). Vượt là SỬA.
3. **Không bịa**: số liệu, địa danh, tên riêng, quan hệ giữa các cá thể không có căn cứ → bỏ hoặc nói chung.
4. **Đúng thể loại**: so với `phong-cach.md` và `tu-ngu.md` của gói — có từ cấm không, nhịp có đúng không, câu mở có móc không.
5. **Đọc được bằng TTS**: không ký hiệu, không số viết bằng chữ số, không viết tắt, không ngoặc, không emoji; câu không
   dài quá 28 âm tiết liền một hơi.
6. **Liền mạch**: đọc cả bài một lượt — không lặp ý, không lặp từ mở câu ba lần liên tiếp, tên nhân vật nhất quán,
   có kết.
7. **Mốc giờ**: start < end, tăng dần, không chồng, không vượt thời lượng video.

Trả về danh sách đoạn SỬA cho biên kịch. Sau tối đa 2 vòng mà còn lỗi thời lượng thì tự cắt ngắn câu cho vừa.
'''

GENRES = {
    "san-duoi": {
        "name": "Săn đuổi",
        "style": """# Phong cách: Săn đuổi

- Giọng: căng, dồn, đứng về phía hồi hộp của cuộc săn; không thương vay khóc mướn.
- Cấu trúc: MÓC (ai săn ai, cái giá nếu thất bại) → RÌNH (chi tiết nhỏ báo hiệu) → BÙNG NỔ (để lặng 1–3 giây lúc vồ)
  → KẾT QUẢ → DƯ ÂM (một câu về quy luật sinh tồn).
- Câu: rất ngắn ở đoạn rượt (3–7 âm tiết), dài hơn ở đoạn rình. Dùng thì hiện tại.
- Tốc độ đọc: tối đa 3,0 âm tiết/giây; đoạn rượt đuổi 2,6 để còn chỗ cho nhạc.
- Tỷ lệ có thoại: 50–65%.
""",
        "words": """# Từ ngữ: Săn đuổi

Nên dùng: khoảng cách, một nhịp, sai một bước, bứt tốc, khóa mục tiêu, cơ hội duy nhất, trả giá.
Cấm: "tuyệt vời", "thật đáng kinh ngạc", "các bạn ơi", "hãy cùng xem", câu hỏi tu từ quá hai lần một video.
""",
    },
    "sinh-ton-me-con": {
        "name": "Sinh tồn · mẹ con",
        "style": """# Phong cách: Sinh tồn · mẹ con

- Giọng: ấm, chậm, gần gũi; kể như kể về một gia đình. Cảm xúc đến từ chi tiết, không từ tính từ.
- Cấu trúc: GIỚI THIỆU gia đình và mối nguy → BÀI HỌC / THỬ THÁCH → KHOẢNH KHẮC yếu lòng → VƯỢT QUA hoặc MẤT MÁT → KẾT mở.
- Câu: vừa (8–14 âm tiết), có khoảng nghỉ. Gọi tên nhân vật thường xuyên.
- Tốc độ đọc: tối đa 2,7 âm tiết/giây.
- Tỷ lệ có thoại: 60–75%.
""",
        "words": """# Từ ngữ: Sinh tồn · mẹ con

Nên dùng: lần đầu tiên, bài học, che chở, chưa đủ lớn, mùa khô, một ngày nữa.
Cấm: nhân hóa quá đà ("bé khóc", "mẹ nói"), "đáng yêu quá", từ ngữ trẻ con, bịa tên riêng cho con vật nếu brief không cho.
""",
    },
    "tai-lieu": {
        "name": "Tài liệu khoa học",
        "style": """# Phong cách: Tài liệu khoa học

- Giọng: điềm tĩnh, chính xác, tò mò; mỗi đoạn cho người xem một điều biết thêm.
- Cấu trúc: CÂU HỎI hoặc sự thật lạ → QUAN SÁT trên hình → GIẢI THÍCH (vì sao loài này làm vậy) → Ý NGHĨA.
- Câu: vừa đến dài (10–18 âm tiết), rõ chủ ngữ. Chỉ nêu kiến thức chắc chắn đúng; không chắc thì không nêu số.
- Tốc độ đọc: tối đa 2,9 âm tiết/giây.
- Tỷ lệ có thoại: 65–80%.
""",
        "words": """# Từ ngữ: Tài liệu khoa học

Nên dùng: tập tính, lãnh thổ, bản năng, thích nghi, chuỗi thức ăn, quan sát cho thấy.
Cấm: giật gân ("sốc", "không thể tin nổi"), số liệu không chắc, "theo các nhà khoa học" khi không có nguồn.
""",
    },
}

GENRE_TEMPLATE_STYLE = """# Phong cách: {name}

- Giọng: (mô tả giọng kể mong muốn)
- Cấu trúc: (mở → giữa → kết)
- Câu: (độ dài, nhịp)
- Tốc độ đọc: tối đa 2,9 âm tiết/giây.
- Tỷ lệ có thoại: 60–75%.

Nạp kịch bản mẫu vào thư mục mau/ — AI sẽ bắt chước nhịp và cách kể của mẫu.
"""
GENRE_TEMPLATE_WORDS = "# Từ ngữ: {name}\n\nNên dùng:\nCấm:\n"


def files():
    """{đường dẫn tương đối trong skills/: nội dung} của bộ kỹ năng gốc."""
    out = {
        "thoai-phim-workflow/SKILL.md": CONDUCTOR,
        f"{WRITER}/SKILL.md": WRITER_SKILL,
        "kiem-duyet-thoai/SKILL.md": REVIEWER,
    }
    for slug, pack in GENRES.items():
        out[f"{WRITER}/the-loai/{slug}/phong-cach.md"] = pack["style"]
        out[f"{WRITER}/the-loai/{slug}/tu-ngu.md"] = pack["words"]
        out[f"{WRITER}/the-loai/{slug}/mau/README.md"] = (
            f"Đặt kịch bản thoại mẫu của thể loại «{pack['name']}» vào thư mục này (mỗi file .txt một kịch bản).\n")
    return out
