# Ghi chú tích hợp (kiểm tra thực tế trên máy, 18/11/2026)

## Google Flow (flow.google.com - đã chuyển từ labs.google/fx/tools/flow)
Kiểm tra trên tab Chrome đã đăng nhập, giao diện tiếng Việt, chỉ đọc (không bấm Tạo):
- Ô prompt: `flow-prompt-box [role=textbox].ProseMirror`, aria-label "Bạn muốn tạo những gì?".
- Nút Tạo: `flow-generate-icon-button button`, aria-label "Bắt đầu tạo".
- Nút cài đặt: `button.settings-trigger-button` (aria "Điều kiện kích hoạt cài đặt"), chữ dạng
  "Video · 720p · 10 giây crop_16_9 x2". Popover dùng `[role=radio][aria-checked]`: Hình ảnh/Video,
  Khung hình/Thành phần, 16:9/9:16, 360p/720p, 4/6/8/10 giây, x1–x4, dòng "Quá trình tạo sẽ tốn N tín dụng".
- Mô hình (nút aria "Chọn nhóm mô hình", menu `[role=menuitem]`): Omni 1.2 Flash, Omni 1.1 Flash,
  Veo 3.1 - Lite, Veo 3.1 - Fast, Veo 3.1 - Quality (Omni 1.1 / Veo 3.1 Fast / Quality báo "ngừng hoạt động 5/12").
  → Sau 5/12 cần đổi `flow.model` trong config (vd. "Veo 3.1 - Lite" hoặc "Omni 1.2 Flash").
- Thêm ảnh: nút aria "Thêm thành phần vào ô nhập câu lệnh" → "Tải nội dung nghe nhìn lên" tạo
  `input[type=file]` (accept png/jpg/webp/…/mp4, multiple) rồi gọi `.click()`. Extension chặn hộp chọn file trong
  MAIN world và gán file bằng DataTransfer.
- Ô kết quả: `flow-grid-tile-container[role=group][aria-label=<tiêu đề>]` → `[data-media-id]`. Menu "Tuỳ chọn khác":
  Thêm vào câu lệnh, **Tải xuống** (menu con độ phân giải: "Kích thước gốc", "Đã tăng độ phân giải"), …
- Trang có reCAPTCHA (`g-recaptcha-response`). Extension không giải CAPTCHA; thấy thử thách là dừng và báo.
- **Chưa kiểm tra được trên giao diện thật** (cần một lần tạo thật): ô đang tạo hiển thị % / spinner thế nào,
  ảnh tải lên có tự gắn chip vào ô prompt không, menu con tải video có những lựa chọn nào. Mã đã viết để dừng an toàn
  (`ui_changed`, trước khi gửi) nếu các giả định này sai.

## CapCut Desktop 9.8.0.4213
- UI Automation: chỉ thấy 4 cửa sổ (`MainWindow_QMLTYPE`, `EditPanel_QMLTYPE`, `QQuickWindow`, `AgentChatView`),
  **0 control con** kể cả sau khi truy vấn lặp lại → không thể bấm Import/Export theo tên. (`hana capcut probe`)
- Draft là JSON thường: `D:\Capcut\CapCut Drafts\<tên>\draft_content.json` (version 360000, new_version 193.0.0),
  thời gian tính bằng micro-giây, segment tham chiếu material qua `material_id` + `extra_material_refs`
  (speeds, placeholder_infos, canvases, sound_channel_mappings, material_colors, vocal_separations; audio có beats).
- Template được lấy (chỉ đọc) từ draft `1117`, đường dẫn file của bạn đã được xoá khỏi template.
- Draft do công cụ tạo (`HanaShort_TEST_capcut-sim`) được CapCut **tự đăng ký** vào `root_meta_info.json`
  (thời lượng 24s, không ẩn) mà không cần sửa file của CapCut.
- **Đã chạy thật (job mô phỏng `capcut-sim`)**: CapCut mở draft do công cụ tạo, timeline hiển thị đúng 3 clip + SFX;
  `hana capcut export` gửi Ctrl+E (mở cửa sổ "Xuất-<tên draft>") + Enter (bấm Xuất); CapCut lưu MP4 vào
  **Tải xuống**; công cụ tự thấy file, chép sang `export_dir`, verify 1080x1920 HEVC/AAC 24,0s, đọc được.
- Lỗi đã sửa: `track.attribute = 1` nghĩa là track bị **tắt tiếng**; draft mẫu 1117 có track audio tắt tiếng nên lần
  xuất đầu mất SFX. Bộ dựng giờ luôn đặt `attribute = 0`. Đo dải 900 Hz tại 0,5s: −24,5 dB (có SFX) so với −39,8 dB.
- Mở draft vẫn là thao tác tay: phải về màn hình chính CapCut và mở đúng draft; chỉ gửi phím Xuất khi thư mục draft
  có `.locked` (đang mở). Sau khi xuất, CapCut hiện màn hình chia sẻ TikTok/YouTube - công cụ không bấm "Chia sẻ".
- FFmpeg đi kèm CapCut: không có drawtext/libx264; có h264_mf/nvenc/qsv/amf, aac, tile, amix, adelay.
