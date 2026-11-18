# Cài đặt HanaMedia Short (Windows 10/11)

## 1. Yêu cầu
- Python 3.11+ trên PATH (máy này: 3.13). Không cần cài thêm gói pip nào.
- FFmpeg: dùng bản trên PATH nếu có, nếu không tự dùng `ffmpeg.exe`/`ffprobe.exe` đi kèm CapCut
  (`%LOCALAPPDATA%\CapCut\Apps\<phiên bản>\`). Bản kèm CapCut không có libx264 → công cụ dùng `h264_mf`.
- Google Chrome (đã đăng nhập Flow), CapCut Desktop.

Kiểm tra: mở terminal trong thư mục dự án, chạy `hana.bat doctor`.

## 2. Cấu hình `config.json`
| Khoá | Ý nghĩa |
|---|---|
| `jobs_dir` | nơi lưu `jobs/<job_id>/` |
| `downloads_dir` | thư mục Chrome tải clip về (extension lưu vào `Downloads\HanaFlow\`) |
| `sfx_dir` | **thư viện hiệu ứng âm thanh của bạn** (wav/mp3/m4a…). Tên file và thư mục con dùng làm tag: `SFX\whoosh\swoosh_01.wav`. Hiện đang trỏ `sfx_test` (3 file sine để test) - đổi sang thư mục thật. |
| `export_dir` | nơi chép MP4 thành phẩm |
| `ffmpeg`, `ffprobe` | để trống = tự dò |
| `review.max_retries_per_scene` | số lần tạo lại tối đa mỗi cảnh (mặc định 2 → tối đa 3 lượt) |
| `flow.model` / `duration_label` / `outputs` / `mode` | khớp đúng chữ trên giao diện Flow (`Veo 3.1 - Fast`, `8 giây`, `x1`, `Thành phần`) |
| `flow.project_url` | để trống = dùng tab dự án Flow đang mở (hoặc tạo dự án mới) |
| `capcut.draft_root` | để trống = đọc từ CapCut (`D:/Capcut/CapCut Drafts` trên máy này) |
| `capcut.export_watch_dir` | thư mục CapCut xuất video (thêm vào danh sách theo dõi) |

## 3. Chrome Extension + Native Messaging host
1. Chrome → `chrome://extensions` → bật **Chế độ dành cho nhà phát triển** → **Tải tiện ích đã giải nén** →
   chọn thư mục `extension\` trong dự án.
2. Xem **ID** của extension hiện trên thẻ. Với thư mục `C:\Users\Admin\HanaMediaShort\extension` ID phải là
   `ooepceipibhcjgfbobjbgkhelncdjnfj` (đã đăng ký sẵn trên máy này).
3. Chỉ cần làm lại nếu ID khác - mở PowerShell **trong thư mục dự án** và thay ID thật vào (không gõ dấu `< >`):
   ```
   cd C:\Users\Admin\HanaMediaShort
   powershell -ExecutionPolicy Bypass -File native_host\install_native_host.ps1 -ExtensionId abcdefghijklmnopabcdefghijklmnop
   ```
   Script ghi `native_host\com.hanamedia.short.json`, `hana_host.bat` và một khoá registry **HKCU** (chỉ người
   dùng hiện tại): `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.hanamedia.short`.
   Gỡ: thêm `-Uninstall`.
4. Ở `chrome://extensions` bấm ⟳ (tải lại) extension → bấm icon extension → **Kiểm tra host** phải thấy `"ok": true`.
5. Mở một dự án Flow → icon extension → **Kiểm tra giao diện Flow**: báo cáo lưu ở `jobs\_flow_ui_report.json`
   (các bộ chọn `promptBox`, `generateBtn`, `settingsBtn`, `addBtn`, `modelBtn` đều phải `true`).

Nhật ký host: `jobs\_host.log`. Nhật ký từng job: `jobs\<id>\log.jsonl`.

## 4. Gọi từ Claude Code
Mở Claude Code tại thư mục dự án → `/hana-short <ảnh nhân vật> <brief.md> [ảnh sản phẩm]`.

## 4b. Sản xuất hàng loạt từ thư viện tool HanaMedia
1. Trong tool: **Thư viện Video & Ảnh** → gắn nhóm **Nhân vật** / **Sản phẩm** cho ảnh. HanaMedia Short chỉ ĐỌC
   thư viện (`%LOCALAPPDATA%\HanaMedia\data\studio.sqlite3`, mở chế độ read-only) - không sửa dữ liệu của tool.
   Kiểm tra: `hana.bat library --category product`.
2. Brief **không bắt buộc**. Chỉ có ảnh sản phẩm → video giới thiệu sản phẩm theo hình ảnh, không nêu công dụng,
   thông số, giá hay con số (công cụ tự chặn). Muốn nêu tuyên bố → thêm
   `C:\Users\Admin\HanaMedia\thu-vien\brief\<tên sản phẩm không dấu>.md` (vd `hanacup-500ml.md`).
3. File lô: `C:\Users\Admin\HanaMedia\thu-vien\lo\<tên lô>.csv` - cột `nhan_vat,san_pham,so_video,ghi_chu`
   (cột `brief` tuỳ chọn).
4. Chạy (chọn một):
   - Trong tool: Không gian Agent → workflow **HanaMedia Short** (đã đăng ký bằng `hana.bat integrate-hanamedia`).
   - Terminal: `hana.bat batch create --file <csv>` rồi `hana.bat batch run --batch <tên lô>` (tự gọi Claude Code
     viết kịch bản / duyệt, tự dựng; chờ Flow khi cần).
5. Chrome: extension → **Chạy liên tục** để tạo lần lượt mọi cảnh READY của mọi job.
6. Thành phẩm: `export_dir\<tên lô>\<job>.mp4` (FFmpeg 9:16, đã verify) + draft CapCut `HanaShort_<job>` để chỉnh tay.
   Tiến độ hiện trong tool qua `HanaMedia\output\_status\hana-short.json`; xem chi tiết: `hana.bat batch status`.

## 5. Kiểm thử tự động
```
python -m unittest discover -s tests -v
```
Dùng FFmpeg thật và tiến trình host thật, không chạm Chrome/Flow/CapCut UI. Kiểm tra: tuyên bố ngoài brief bị chặn,
lời thoại quá dài bị chặn, chống gửi trùng (CLAIMED/SUBMITTING/unknown_submit), chặn toàn cục khi hết lượt,
giao thức Native Messaging, storyboard, giới hạn retry, chỉ tạo lại cảnh lỗi, timeline + SFX, render 9:16,
tạo draft CapCut.

## 6. Khi gặp sự cố
| Trạng thái | Làm gì |
|---|---|
| `BLOCKED unknown_submit` | Xem Flow. Có clip đang tạo → `hana resolve --action await-download` (rồi bấm extension "Gửi 1 cảnh" để tiếp tục theo dõi, hoặc tải tay + `import-clip`). Không có → `hana resolve --action retry`. |
| chặn `login` / `quota` / `captcha` | Đăng nhập / chờ lượt / tự giải CAPTCHA trong tab → `hana flow-unblock`. Công cụ **không** tự giải CAPTCHA. |
| chặn `ui_changed` | Flow đổi giao diện. Chạy "Kiểm tra giao diện Flow", sửa `extension/content.js` (đối tượng `SEL`), tải lại extension → `hana flow-unblock`. Cảnh vẫn ở READY (chưa gửi gì). |
| `REVIEW_REQUIRED` | Xem `review.json`/storyboard; `hana resolve --action approve` hoặc tạo clip tay + `import-clip`. |
| CapCut không mở được draft | Dùng `jobs\<id>\edit\edit_package\` + `HUONG-DAN-DUNG.txt`, xuất tay, `hana capcut register-export`. |
