---
name: hana-short
description: Biến ảnh nhân vật quảng cáo + brief sản phẩm (+ ảnh sản phẩm nếu có) thành YouTube Short 9:16 gồm 3 cảnh 8 giây Hook → Demo → CTA. Viết kịch bản, prompt Veo 3, gửi qua Flow bằng extension, duyệt từng clip bằng storyboard, dựng trong CapCut Desktop. Dùng khi người dùng gọi /hana-short hoặc muốn làm video Short quảng cáo từ ảnh nhân vật và brief.
argument-hint: "<ảnh nhân vật> <brief.md> [ảnh sản phẩm] [job_id]"
---

# /hana-short - HanaMedia Short

Dự án nằm ở thư mục gốc chứa `hana.bat`. Mọi lệnh chạy bằng `hana.bat <lệnh>` (Windows) hoặc
`python hana_cli.py <lệnh>`. Trạng thái nằm trong `jobs/<job_id>/job.json`; luôn đọc `hana.bat status --job <id>`
trước khi làm gì để **tiếp tục từ bước dang dở**, không làm lại bước đã xong.

## 0. Xác định job
- Nếu người dùng đưa job_id đã có → `hana.bat status --job <id>` và nhảy tới bước tương ứng với `stage`:
  `script` → bước 1, `generate` → bước 3, `review` → bước 4, `edit` → bước 5, `capcut` → bước 6, `done` → báo kết quả.
- Nếu là job mới: job_id dạng `ten-san-pham-YYYYMMDD` (a-z, 0-9, -, _).
  `hana.bat init --job <id> --character <ảnh> --product <ảnh sp> [--brief <brief.md>] --title "<tên>"`
  Brief không bắt buộc; nếu người dùng đưa brief dạng văn bản → lưu thành file .md trước khi init.

## 1. Viết kịch bản (Claude)
Xem ảnh nhân vật (Read) và ảnh sản phẩm, đọc `jobs/<id>/inputs/brief.md`. Viết `jobs/<id>/script.json`:

```json
{
  "title": "…", "aspect_ratio": "9:16", "language": "Vietnamese",
  "character_bible": "Mô tả tiếng Anh cố định của nhân vật: tuổi, giới, tóc, khuôn mặt, trang phục, phụ kiện, giọng nói",
  "product_bible": "Mô tả tiếng Anh hình dạng/màu/nhãn sản phẩm đúng như ảnh",
  "scenes": [
    {"scene_id": "s1_hook", "role": "hook", "duration": 8,
     "dialogue": "Lời thoại tiếng Việt ≤ 20 từ",
     "veo_prompt": "Prompt tiếng Anh cho Veo 3 (xem quy tắc dưới)",
     "action": "Hành động chính một câu",
     "reference_images": ["character", "product"],
     "claims": ["Câu trích NGUYÊN VĂN từ brief mà lời thoại/hình ảnh dùng"],
     "review_criteria": ["Tiêu chí duyệt cụ thể, quan sát được trên storyboard", "…"],
     "sfx_cues": [{"at": 0.2, "tag": "whoosh"}]},
    {"scene_id": "s2_demo", "role": "demo", …},
    {"scene_id": "s3_cta", "role": "cta", …}
  ]
}
```

Quy tắc:
- Mặc định 3 cảnh × 8 giây: **Hook** (0–2 giây đầu gây tò mò, nêu vấn đề), **Demo** (sản phẩm đang được dùng,
  thấy rõ lợi ích), **CTA** (lời kêu gọi + nhìn thẳng camera).
- **Lời thoại vừa 8 giây**: ≤ 20 từ tiếng Việt (~2,5 từ/giây), một ý, câu ngắn. Không có chữ trên màn hình.
- **Không có brief** (`inputs.brief_provided: false`, brief.md ghi "KHÔNG CÓ BRIEF"): chỉ dựa vào ảnh sản phẩm.
  `claims` để trống; lời thoại KHÔNG nêu công dụng, thông số, chất liệu, giá, ưu đãi hay bất kỳ con số nào; chỉ
  cảm xúc của nhân vật, cách dùng nhìn thấy được, vẻ ngoài sản phẩm và CTA chung ("Thử ngay hôm nay"). Công cụ từ
  chối script có claims hoặc chữ số trong lời thoại.
- **Chỉ dùng tuyên bố có trong brief**. Mỗi con số, công dụng, giá, ưu đãi phải nằm nguyên văn trong `claims`
  và nguyên văn trong brief (công cụ sẽ từ chối nếu không khớp). Không thêm "số 1", "tốt nhất", công dụng y tế,
  so sánh đối thủ, nếu brief không ghi. Không bịa giá/khuyến mãi.
- **veo_prompt** (tiếng Anh, 60–120 từ): shot type + góc máy + chuyển động máy; chủ thể lặp lại nguyên
  `character_bible` (rút gọn) để nhất quán giữa 3 cảnh; sản phẩm theo `product_bible`; hành động chính; bối cảnh,
  ánh sáng; âm thanh nền; "vertical 9:16 composition, subject centered in frame". Lời thoại KHÔNG viết trong
  veo_prompt (công cụ tự thêm dòng Dialogue). Không yêu cầu chữ, logo vẽ tay, watermark.
- **review_criteria**: 2–4 tiêu chí riêng cho cảnh, nhìn được trên storyboard (vd. "Bình HanaCup màu bạc
  nằm trong tay phải ở khung 2–6", "Nhân vật mở miệng nói ở khung 1–7").
- `sfx_cues.tag` là từ khoá (whoosh, pop, ding, impact, riser, cash, camera, sparkle) - công cụ dò file trong
  thư viện SFX theo tên file/thư mục.

Kiểm tra rồi nạp: `hana.bat validate-script --job <id> --script jobs/<id>/script.json` → sửa tới khi `ok: true` →
`hana.bat set-script --job <id> --script jobs/<id>/script.json`. Cho người dùng xem bảng 3 cảnh (lời thoại + ý
prompt) trước khi gửi Flow.

## 2. (Một lần) Kiểm tra máy
`hana.bat doctor` - FFmpeg, thư mục, CapCut. Nếu extension/host chưa cài, chỉ người dùng tới `docs/CAI-DAT.md`.

## 3. Tạo clip trên Flow (extension)
Người dùng mở tab Flow đã đăng nhập → bấm icon extension **HanaMedia Short** → "Gửi 1 cảnh" hoặc "Chạy liên tục".
Extension tự nhận cảnh READY, đặt Video / Thành phần / 9:16 / mô hình / 8 giây / x1, tải ảnh tham chiếu, nhập prompt,
bấm Tạo, theo dõi và tải clip về job. Theo dõi bằng `hana.bat status --job <id>`.
- `BLOCKED` + `unknown_submit`: KHÔNG tự gửi lại. Nhờ người dùng xem Flow: nếu clip đang/đã tạo →
  `hana.bat resolve --job <id> --scene <s> --action await-download` (hoặc tải tay rồi `import-clip`); nếu không có →
  `--action retry`.
- Chặn toàn cục (`login`, `quota`, `captcha`, `ui_changed`): báo rõ lý do, người dùng xử lý xong chạy
  `hana.bat flow-unblock`.
- Clip tải tay: `hana.bat import-clip --job <id> --scene <s> --file <mp4>`.

## 4. Duyệt từng clip (Claude)
`hana.bat review-prep --job <id>` (thêm `--scene s --transcript file.txt` nếu có transcript; hoặc đặt
`transcript.txt` cạnh clip). Với mỗi cảnh:
1. **Read** file `storyboard` (lưới ảnh, đọc trái→phải, trên→dưới; mốc giây trong `grid.frames`).
2. Đối chiếu `technical_checks` (9:16, thời lượng, âm thanh, khớp lời thoại nếu có transcript).
3. So với ảnh nhân vật/sản phẩm gốc: nhân vật nhất quán, sản phẩm đúng, bố cục dọc, hành động chính,
   từng `criteria`.
4. Ghi kết quả - luôn kèm lý do cụ thể theo khung/mốc giây:
   - PASS: `hana.bat review-set --job <id> --scene <s> --verdict PASS --reason "…"`
   - RETRY (lỗi sửa được bằng prompt): thêm `--fix-hint "câu tiếng Anh nói rõ cần sửa"`
   - REVIEW_REQUIRED: khi không chắc, lỗi nhạy cảm (sai tuyên bố, sai sản phẩm, khuôn mặt lạ) hoặc hết lượt.
   Công cụ tự đổi RETRY → REVIEW_REQUIRED khi hết `review.max_retries_per_scene`, không cho PASS nếu kỹ thuật lỗi.
   Chỉ cảnh RETRY quay lại READY; cảnh đã PASS được giữ nguyên.
Không có transcript → nói rõ "chưa đối chiếu lời thoại bằng transcript" trong lý do.

## 5. Dựng (timeline + gói)
Khi cả 3 cảnh APPROVED: `hana.bat timeline --job <id>` → kiểm tra `sfx_unmatched` (báo người dùng nếu thiếu SFX) →
`hana.bat package --job <id>` → `hana.bat preview --job <id>` (bản FFmpeg để kiểm tra nhịp cắt; không phải bản
xuất CapCut).

## 6. CapCut Desktop
1. `hana.bat capcut build --job <id>` - tạo draft `HanaShort_<id>` trong thư mục draft CapCut (clip, cắt, âm lượng,
   SFX đã đặt sẵn). Lần đầu tự lấy template từ một draft có video+audio của người dùng (chỉ đọc).
2. Người dùng mở CapCut → vào draft `HanaShort_<id>` (CapCut không cho UI Automation bấm nút) → báo lại →
   `hana.bat capcut opened --job <id>`.
3. `hana.bat capcut export --job <id>` (gửi Ctrl+E, Enter và chờ file MP4 mới). Nếu báo `manual_required`:
   người dùng bấm Xuất trong CapCut → `hana.bat capcut register-export --job <id> --file <mp4>`.
4. Kết quả tự kiểm tra (đọc được, 9:16). Nếu draft không mở được trong CapCut: dùng `jobs/<id>/edit/edit_package`
   (clip + SFX + timeline.json + HUONG-DAN-DUNG.txt) và giữ job ở bước `open_draft`.

## Báo cáo cuối
Luôn nêu: file MP4 cuối (`output.final`), kết quả verify, cảnh nào tạo lại mấy lần và vì sao, tuyên bố sản phẩm
đã dùng, bước nào người dùng làm tay, và mọi phần đang dùng mô phỏng (`simulated: true` nghĩa là clip giả lập).
