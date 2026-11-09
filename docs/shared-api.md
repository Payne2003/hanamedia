# Dùng API của chủ tool qua máy chủ trung gian

Mỗi người dùng nhận **URL HTTPS + mã truy cập riêng**. Khóa OpenAI/Gemini/Anthropic chỉ được lưu trên máy chủ. Không đóng gói các khóa đó trong `.exe`, file cấu hình gửi khách, JavaScript hoặc hồ sơ dự án. Với mô hình này, chủ máy chủ trả phí API cho các yêu cầu được cấp quyền.

Mã nguồn đã có client API, gateway và kiểm thử HTTP cục bộ. Chưa có VPS/tên miền trong phiên làm việc này nên chưa triển khai công khai; chưa xác minh các khóa thật hay phát sinh cuộc gọi trả phí đến nhà cung cấp. Các khóa đã chia sẻ trong cuộc trò chuyện nên được thu hồi và thay mới trước khi dùng.

## Quy trình cho người dùng tool

1. Mở Wildlife → **Kết nối AI**.
2. Nhập địa chỉ máy chủ HTTPS và mã do chủ máy chủ cấp; bấm Lưu. Không cần nhập khóa nhà cung cấp.
3. Bấm **Kiểm tra mã & hạn mức**. Thao tác này đọc thông tin tài khoản trên gateway, không gọi AI.
4. Trong Cấu hình phòng ban, chọn **Máy chủ dùng chung** hoặc bấm áp dụng cho tất cả phòng ban. Dự án mới mặc định dùng gateway; cấu hình cũ không có trường transport vẫn dùng trình duyệt để giữ tương thích.
5. Giao tác vụ cho agent. Mỗi lượt đề xuất/phản biện là một yêu cầu riêng và trừ hạn mức tương ứng.

Để trống model riêng của agent nếu dùng model do chủ máy chủ chọn. Gateway chỉ cho dùng model đã cấu hình cho provider, không cho client đổi sang model đắt hơn tùy ý.

## Chuẩn bị máy chủ

Gateway chỉ chuyển tiếp yêu cầu AI; video vẫn được xử lý bằng Muse/FFmpeg trên máy chạy tool. Một VPS Linux nhỏ có Python 3.12, đĩa lưu cấu hình và kết nối HTTPS đến API là đủ để bắt đầu thử nghiệm; cần đo tải thực tế trước khi tăng người dùng.

Các miền outbound cần dùng: `api.openai.com`, `generativelanguage.googleapis.com`, `api.anthropic.com`. Mạng cloud hiện chưa cho phép các miền này; cần thêm vào cấu hình mạng nếu triển khai tại môi trường cloud này. Không tắt TLS để khắc phục lỗi kết nối.

Tại thư mục chứa mã nguồn:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Chọn thư mục dữ liệu **riêng cho máy chủ**, không nằm trong gói phân phối cho khách. Ví dụ Linux: `/var/lib/hanamedia-gateway`.

Nhập từng khóa qua lời nhắc ẩn của CLI; không đặt giá trị khóa vào lệnh hoặc gửi trong chat:

```sh
.venv/bin/python -m runner.ai_gateway --data-dir /var/lib/hanamedia-gateway --set-key chatgpt --model gpt-4.1-mini --max-output-tokens 4096
.venv/bin/python -m runner.ai_gateway --data-dir /var/lib/hanamedia-gateway --set-key gemini --model gemini-2.5-flash --max-output-tokens 4096
.venv/bin/python -m runner.ai_gateway --data-dir /var/lib/hanamedia-gateway --set-key claude --model claude-sonnet-5-5 --max-output-tokens 4096
```

Có thể thay model bằng ID được tài khoản API của bạn hỗ trợ. Các model mặc định là cấu hình khởi đầu, không phải cam kết về model mới nhất hoặc khả dụng trên mọi tài khoản. API là dịch vụ tính phí riêng với gói thuê bao trên website.

Khóa được lưu trong `api-private/credentials.enc`. Windows dùng DPAPI theo tài khoản Windows; Linux dùng Fernet với tệp khóa cục bộ có quyền 0600. Mã hóa cục bộ không bảo vệ khỏi người đã kiểm soát cùng tài khoản hệ điều hành; cần bảo vệ máy chủ và bản sao lưu. Cả dữ liệu gateway và kho khóa phải thuộc tài khoản dịch vụ, không gửi kèm tool cho người dùng.

Hoặc cung cấp bí mật từ hệ thống triển khai qua các biến `HANAMEDIA_OPENAI_API_KEY`, `HANAMEDIA_GEMINI_API_KEY`, `HANAMEDIA_ANTHROPIC_API_KEY`. Biến môi trường có ưu tiên hơn khóa lưu cục bộ.

## Cấp quyền, hạn mức và thu hồi

Tạo người dùng bằng CLI quản trị chạy trên máy chủ:

```sh
.venv/bin/python -m runner.ai_gateway --data-dir /var/lib/hanamedia-gateway --create-user "Thanh vien 01" --daily-tokens 500000 --daily-requests 100
```

Lệnh trả một lần User ID và mã truy cập ngẫu nhiên. Chuyển riêng mã đó cho đúng người; không công bố trong tài liệu chung. Database chỉ lưu SHA-256 của mã, không lưu mã nguyên bản. Mỗi người có mã riêng để có thể thu hồi độc lập.

Xem sử dụng hoặc thay hạn mức, dùng User ID do lệnh tạo trả về:

```sh
.venv/bin/python -m runner.ai_gateway --data-dir /var/lib/hanamedia-gateway --list-users
.venv/bin/python -m runner.ai_gateway --data-dir /var/lib/hanamedia-gateway --set-limits USER_ID --daily-tokens 200000 --daily-requests 40
.venv/bin/python -m runner.ai_gateway --data-dir /var/lib/hanamedia-gateway --revoke USER_ID
```

Hạn mức đặt lại theo ngày UTC (07:00 giờ Bangkok). Trước mỗi yêu cầu, gateway dự trù số byte UTF-8 của prompt, 8192 token/ảnh và giới hạn token đầu ra; nếu không đủ hạn mức sẽ từ chối trước khi gọi AI. Khi nhận usage thật, thay phần dự trù bằng token do nhà cung cấp báo. Đây là giới hạn vận hành theo token/yêu cầu, **không phải trần chi phí tiền tệ bảo đảm**: giá/token phụ thuộc model, ảnh và cách nhà cung cấp tính usage. Đặt thêm ngân sách/thông báo chi phí tại tài khoản nhà cung cấp.

Yêu cầu không nhận được kết quả rõ ràng giữ phần dự trù và chuyển sang cần kiểm tra; không tự gọi lại hoặc hoàn hạn mức như thể chắc chắn chưa bị tính phí. Mỗi người chỉ có một yêu cầu đang chạy; toàn máy chủ tối đa bốn yêu cầu xử lý đồng thời. Thu hồi chặn yêu cầu mới, không hủy được một yêu cầu đã gửi đến nhà cung cấp.

Mã `request_id` chống gửi trùng: cùng mã và cùng nội dung trả kết quả đã lưu mà không gọi AI lại; cùng mã nhưng nội dung khác bị từ chối. Lần chạy dở sau restart được giữ để kiểm tra, không tự gửi lại. Không xóa database để “sửa lỗi” vì sẽ mất thông tin sử dụng/chống trùng.

## Chạy và đưa lên HTTPS

Thử cục bộ:

```sh
.venv/bin/python -m runner.ai_gateway --data-dir /var/lib/hanamedia-gateway --port 8899
```

Gateway chỉ nghe `127.0.0.1`. `/health` kiểm tra tiến trình; endpoint này không chứng minh provider API đã kết nối được. Client cho phép HTTP **chỉ trên localhost** để kiểm thử; kết nối qua Internet bắt buộc HTTPS.

Khi có VPS/tên miền:

1. Đặt repository/venv tại `/opt/hanamedia`, tạo tài khoản dịch vụ `hanamedia` và thư mục dữ liệu thuộc tài khoản đó.
2. Nhập khóa bằng CLI dưới đúng tài khoản dịch vụ. Windows DPAPI không thể tự chuyển kho khóa sang máy/tài khoản khác; cần nhập lại khóa ở máy chủ.
3. Dùng [systemd unit](../deploy/gateway/hanamedia-gateway.service) để chạy gateway. Unit có `UMask=0077`, thư mục state riêng và chỉ cho ghi vào thư mục dữ liệu.
4. Trỏ DNS tên miền về VPS, mở cổng 80/443 cho reverse proxy. Giữ cổng 8899 ở loopback.
5. Cài Caddy từ nguồn chính thức, cấu hình biến `HANA_GATEWAY_DOMAIN` của dịch vụ Caddy và dùng [Caddyfile](../deploy/gateway/Caddyfile). Caddy cấp chứng chỉ HTTPS; cần tên miền thật và kết nối chứng thực chứng chỉ hoạt động.
6. Kiểm tra HTTPS, cấp một mã thử hạn mức nhỏ, xác minh usage tại nhà cung cấp rồi mới cấp cho nhiều người.

Các file triển khai là mẫu cấu hình, chưa được áp dụng trên VPS thật trong phiên này. Không có giao diện quản trị từ Internet; tạo người dùng, đổi hạn mức và thu hồi thực hiện bằng CLI cục bộ trên máy chủ.

## API và lưu dữ liệu

- `POST /v1/agent`: Bearer token người dùng; body gồm provider, model tùy chọn, prompt, tối đa 8 ảnh PNG/JPEG (tối đa 1024×1024), request_id.
- `GET /v1/me`: trả hạn mức của chính người dùng và model cho phép; không trả khóa, mã truy cập hoặc thông tin người khác.
- `GET /health`: chỉ trạng thái tiến trình.

Không cho client tùy chọn API URL/header của nhà cung cấp. Không chuyển tiếp Authorization qua redirect. API keys không được đặt vào URL hoặc gửi về client. Gateway không ghi header, prompt hoặc response vào access log; database riêng lưu usage, hash payload và phản hồi để chống gọi trùng. Database và sao lưu có thể chứa nội dung AI trả lời, cần giữ riêng trên máy chủ.

## Kiểm chứng đã thực hiện

Các bài kiểm thử dùng khóa và phản hồi mô phỏng: giao thức ba provider, ảnh, token usage, lưu khóa mã hóa, không trả khóa ra API, xác thực gateway, thu hồi, quota, nhận đồng thời, gửi trùng, lỗi chưa rõ kết quả, HTTP relay thật và agent dùng gateway thật qua loopback. Giao diện được kiểm tra bằng Chromium. Chưa thực hiện cuộc gọi có tính phí bằng các khóa người dùng đã gửi.
