# Wildlife · Sản xuất phim

Một video gốc → phim AI động vật dài 8–9 phút. Dựng bằng CapCut trên máy người dùng. Âm thanh chỉ có tiếng tự nhiên: không thoại, không nhạc, không phụ đề. Không có công đoạn Shorts hay storyboard.

## Luồng công đoạn

| Công đoạn | Agent | Đầu ra | Điểm duyệt |
|---|---|---|---|
| 01 · Phân tích video gốc | Reference Analyst (+ phản biện) | Báo cáo diễn biến có mốc thời gian, mỗi mục dẫn chứng bằng khung hình đã trích (FFmpeg) | Diễn biến có bằng chứng; chỗ chưa rõ được đánh dấu |
| 02 · Đạo diễn & kịch bản | Story Director | Kịch bản toàn phim, chia cảnh 5–10 giây, có mục đích, đánh dấu cảnh khó. Tổng thời lượng phải nằm trong ±15% mục tiêu | Duyệt xong mới tạo danh sách SCENE_001… |
| 03 · Nhân vật & môi trường | Character Agent | Hồ sơ cá thể (mã như `SNOW_LEOPARD_01`) và môi trường cố định, ghi vào Character Registry | Duyệt |
| 03 · Prompt từng cảnh | Prompt Agent | Prompt tiếng Anh cho từng cảnh; gắn cá thể vào cảnh | Duyệt hàng loạt được |
| Tạo thử các cảnh khó | — | Cảnh khó được tạo video và QC trước | Duyệt xong mới chạy hàng loạt |
| 04 · Sản xuất video | Bộ chạy Muse | Video theo mã cảnh. Nút **Giao Muse** và **Đồng bộ take** | — |
| 05 · QC cảnh & nối cảnh | Visual QC | Cảnh đạt, hoặc lỗi kèm bằng chứng, trả về bước sửa prompt / tạo take mới | Duyệt hàng loạt được |
| 06 · Dựng & âm thanh | Editor Agent + Sound Agent | Kế hoạch dựng (file, đoạn dùng, vị trí trên phim, âm thanh), tải được dạng CSV/JSON cho CapCut; bản nháp FFmpeg có trộn lớp tiếng tự nhiên từ thư viện | Kiểm tra kế hoạch: đủ cảnh, đúng file, đúng thời lượng |
| 07 · QC toàn phim | Film Reviewer | Báo cáo nghiệm thu, rồi một trong hai: sửa dựng/âm thanh, hoặc làm lại một cảnh nguồn | Người dùng xác nhận đã nghe: chỉ có âm thanh tự nhiên |
| 08 · Kiểm tra & bàn giao | Delivery Worker | Mở lại file, kiểm tra thông số và thời lượng; lưu manifest và kế hoạch dựng | — |

## AI Manager

- Bật **Tự chạy tới điểm duyệt** để Manager tự giao lần lượt các việc sẵn sàng. Manager dừng ở mỗi điểm cần người duyệt, cũng như trước bước video Muse và tạo thử.
- Có ngân sách lượt AI cho cả dự án. Mỗi phần (từng cảnh, từng đoạn khung hình) cũng có giới hạn lượt gọi riêng.
- Không bao giờ tự gửi lại yêu cầu có thể đã tới AI hoặc Muse.

## Chưa làm (cần kiểm tra trên máy người dùng)

- Adapter điều khiển CapCut: tạo project, nhập file, đặt clip, xuất bản cuối. Hiện tại bản cuối được dựng trong CapCut theo file kế hoạch, bản nháp và bàn giao dùng FFmpeg.
- Phân tích âm thanh của video gốc. Hiện chỉ đoán từ hình và được ghi rõ là đoán.

Nên nghiệm thu một phim ngắn 1 phút (khoảng 6 cảnh), có dựng và âm thanh hoàn chỉnh, trước khi chạy bản 8–9 phút.
