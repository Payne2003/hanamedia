# HANA MEDIA — AI Studio 0.3

Bản chạy cục bộ cho Windows, viết bằng **Python + Selenium + SQLite**; giao diện **HTML/CSS/JavaScript**. Có thể mở và sửa toàn bộ thư mục trong VS Code. Đây là bộ chạy cục bộ, không phải bản web tĩnh đang lưu trên Sites.

## Đúng phạm vi bạn yêu cầu

| Yêu cầu | Cách thực hiện trong mã nguồn |
| --- | --- |
| Giao diện Studio | Bố cục làm việc kiểu Muse Studio: điều hướng và trạng thái ở đầu trang, hàng đợi chiếm phần lớn màn hình, khung tạo Video/Ảnh cố định bên phải. |
| Nhập hàng loạt prompt | Nhập trực tiếp, TXT hoặc JSON xuất từ Huy Studio; tách theo dòng, dòng trống hoặc `---`; tối đa 200 prompt/lần. |
| Chia prompt cho từng tài khoản | Chia trước hàng đợi theo lượt giữa các tài khoản đang bật, đủ ngân sách; 10 prompt/5 tài khoản sẽ giao 2 prompt mỗi tài khoản. Mỗi phiên Chrome/tab Muse xử lý tuần tự phần đã giao và không đóng/mở sau mỗi video. Mặc định tối đa 2 tài khoản hoạt động đồng thời. |
| Chức năng sáng tạo | Trong khung bên phải chọn **Video** (Prompt → video hoặc Ảnh → video) hoặc **Ảnh** (Google Flow). Hai chức năng video vẫn dùng Muse. |
| Chờ / Đang chạy / Xong / Lỗi | Lưu trạng thái trong SQLite, cập nhật giao diện mỗi 2 giây, có lý do lỗi và bước hiện tại. |
| Xem video trong hàng đợi | Tác vụ đã hoàn thành hiển thị hình thu nhỏ lấy từ video; bấm vào hình hoặc nút xem để mở trình phát. Ảnh thu nhỏ chỉ tải khi hàng hiển thị để giảm tải. |
| Video tự lưu đúng thư mục khi tải trên Muse | Chọn thư mục bằng hộp thoại Windows; Chrome của từng tác vụ tải thẳng vào thư mục con riêng. |
| Gắn video vào đúng cảnh | Mã tác vụ trong prompt, kiểm tra video xuất hiện sau prompt đó, đối chiếu tên file mới, chờ tải hoàn tất rồi gắn đường dẫn vào cảnh. Có nút Xem và Tải MP4. |
| Thư viện ảnh tham chiếu | Ảnh cục bộ được lưu trong thư viện và có thể gắn lại vào từng prompt. Ảnh tải xuống từ Flow có thể nhập vào thư viện. |
| Ảnh → video | Chọn **Ảnh → video** trong khung tạo, gắn tối thiểu 1 và tối đa 10 ảnh cho từng prompt; ảnh được tải lên Muse trước khi gửi prompt và hiện ảnh thu nhỏ trong hàng đợi. |
| Tạo ảnh | **Tạo ảnh · Google Flow** mở Flow bằng profile Chrome `Profile 2` hiện có, điền prompt, và lưu tải xuống Flow vào thư mục dữ liệu của tool. Trong Flow chọn **Hình ảnh**, tạo và tải kết quả; bấm **Nhập ảnh Flow đã tải** để thêm ảnh vào thư viện. |
| Chọn và xóa prompt | Chọn từng prompt hoặc toàn bộ kết quả lọc rồi xóa khỏi hàng đợi. Không xóa tác vụ đang chạy; video đã tải được giữ nguyên trong thư mục lưu. |
| Tự mở các tài khoản, prompt sẵn để dán | Chế độ hỗ trợ mở Chrome và điền sẵn prompt; bạn bấm Gửi và Tải xuống. Mỗi cảnh có thêm nút Chép prompt. |

Cũng có chế độ **Tự gửi prompt & tải video**, dùng menu **ba chấm cạnh video → Tải xuống** đã quan sát trên Muse. Có tùy chọn chạy ẩn Chrome sau khi đăng nhập và kiểm tra thành công có cửa sổ.

## Viết content bằng Gemini (Prompt + video gốc → hàng loạt prompt)

Bấm **✎ Viết content** trên thanh điều hướng để mở trang Viết content (bấm **Studio** hoặc **← Về Studio** để quay lại; địa chỉ `http://127.0.0.1:8765/#content` mở thẳng trang này).

1. **Cài đặt Gemini** (một lần): dán khóa API lấy tại https://aistudio.google.com/apikey và chọn model (mặc định `gemini-3.8-flash`; Google không cho khóa mới dùng `gemini-2.5-*` nữa). Khóa chỉ lưu trong `data/` trên máy và chỉ gửi tới Google Gemini; giao diện không hiển thị lại khóa.
2. **Đầu vào**: (1) Prompt — yêu cầu cho Gemini (đã có mẫu sẵn, sửa tùy ý); (2) Link video gốc — link **YouTube** công khai hoặc không công khai (`youtube.com/watch`, `youtu.be`, `/shorts`). Có thể bỏ trống link để Gemini tự viết kịch bản theo yêu cầu. Gemini không đọc trực tiếp link TikTok/Facebook: đăng video lên YouTube rồi dán link.
3. Bấm **Phân tích bằng Gemini**. Video dài hơn khoảng 1,5 phút được tự chia thành từng đoạn 1 phút: Gemini xem lần lượt từng đoạn, thanh tiến độ hiện "Đoạn 3/10", cảnh hiện dần bên phải. Độ dài video được đọc từ YouTube; nếu không đọc được, điền ô **Độ dài video** (ví dụ `9:13`) hoặc ghi mốc thời gian trong prompt. Gemini quá tải hoặc lỗi giữa chừng thì các cảnh đã xong được giữ lại; bấm **Tiếp tục phân tích** để chạy tiếp từ đoạn lỗi, hoặc **Dừng** để ngừng sau đoạn hiện tại. Đầu ra gồm **Kịch bản video gốc** và danh sách cảnh, mỗi cảnh có: bối cảnh, góc quay, hành vi nhân vật, nội dung hành động nhân vật. Có thể sửa, thêm, xóa, đổi thứ tự cảnh.
4. Bấm **Kiểm tra thiếu cảnh / lỗi**: Gemini so lại với video gốc, bổ sung cảnh bị bỏ sót và viết lại cảnh dễ bị AI video từ chối/không tạo được (người nổi tiếng, logo, chữ trên màn hình, quá nhiều hành động…). Danh sách vấn đề hiện ở khung **Cần xem lại**. Lặp lại đến khi không còn vấn đề. Tool cũng tự báo cảnh bị trống trường.
5. **Hàng loạt prompt** được ghép theo cấu trúc, mỗi cảnh một dòng:
   `Bối cảnh 1: ..., góc quay: ..., hành vi nhân vật: ..., Nội dung hành động nhân vật: ...;`
   Bấm **Đưa vào Studio** (tool chuyển về trang Studio, prompt đã nằm trong khung tạo video) rồi **Tạo & chạy** như bình thường, hoặc **Chép** / **Tải .txt**.

Bản nháp (yêu cầu, link, các cảnh) được giữ trên trình duyệt khi chuyển trang hoặc tải lại. Phân tích video dài có thể mất vài phút và dùng hạn mức Gemini của khóa API. Đã kiểm thử bằng Gemini giả lập; cần một khóa thật để kiểm tra với video thật.

## Mức độ đã xác minh — đọc trước khi chạy

- Tạo ảnh dùng giao diện Google Flow trên Chrome profile `Profile 2` trong `%LOCALAPPDATA%\Google\Chrome\User Data`, không dùng API riêng/không công khai. Profile này phải được đóng trong các cửa sổ Chrome khác trước khi bấm **Mở Flow và điền prompt**; Chrome không cho hai tiến trình dùng chung profile cùng lúc. Tool điền prompt nhưng người dùng cần chọn **Hình ảnh**, bấm tạo và tải ảnh trong Flow; sau đó nhập ảnh tải xuống vào thư viện. Đăng nhập Google trực tiếp trên cửa sổ Flow; tool không đọc/lưu mật khẩu.
- Đã quan sát đăng nhập Muse bằng email + OTP, gửi một yêu cầu video 5 giây, nhận video thật có thời lượng 5 giây và menu Tải xuống trên tài khoản mẫu trong trình duyệt ngày 02/11/2026.
- Luồng tải ảnh lên Muse phụ thuộc vào ô chọn ảnh của giao diện Muse hiện tại và chưa được xác minh bằng một video ảnh → video thật. Nếu Muse không cung cấp ô tải ảnh tương thích hoặc không nhận nhiều ảnh, tác vụ sẽ báo lỗi trước khi gửi prompt.
- Mã Selenium bám theo các nhãn/DOM vừa quan sát. Đây là bản triển khai để thử trên Windows; **chưa kiểm thử trọn quy trình bằng chính gói này trên Windows và chưa thử 10 tài khoản thật cùng lúc**. Phiên Chrome riêng, chế độ ẩn, thư mục tải và lấy OTP tự động cần kiểm tra trên máy cài đặt.
- Đã chạy các bài kiểm tra tự động cho hàng đợi, đồng thời, ngân sách, chống thêm trùng, khôi phục sau gián đoạn, OTP/link hộp thư, file MP4 chưa tải xong và bảo vệ HTTP cục bộ. Test không tạo video thật.
- Chưa tự đọc số dư credit Muse. Credit trong tool là **ngân sách dự kiến do bạn nhập**, giữ chỗ khi giao tác vụ. Cập nhật theo số dư/chi phí thực tế trên Muse. Hết ngân sách hoặc tài khoản cần kiểm tra thì không giao thêm việc.
- Tạo ảnh độc lập chưa tích hợp. Ảnh tham chiếu có thể gắn riêng cho từng prompt → video; ảnh được giữ trong `data/input-images/` khi tác vụ còn trong hàng đợi.

## Cài trên Windows

1. Cài Google Chrome và Python 3.11 trở lên. Python chính thức: https://www.python.org/downloads/windows/ . Khi cài Python, chọn **Add Python to PATH**.
2. Giải nén toàn bộ ZIP vào thư mục bạn giữ lâu dài, ví dụ `D:\Huy-Muse-Runner`. Không chạy trực tiếp bên trong file ZIP.
3. Chạy **CAI_DAT_WINDOWS.cmd** một lần để tạo môi trường Python và cài Selenium. Cần Internet; lần mở Chrome đầu có thể cần tải ChromeDriver tương thích.
4. Chạy **CHAY_WINDOWS.cmd**. Giao diện mở tại `http://127.0.0.1:8765`.
5. Giữ cửa sổ bộ chạy mở. Đóng trang giao diện không dừng tác vụ, nhưng tắt bộ chạy hoặc cho máy ngủ sẽ làm gián đoạn.

## Thử một video trước

1. Bấm **Chọn thư mục lưu**, chọn thư mục bạn muốn, ví dụ `D:\Video-Muse`.
2. Mở **Tài khoản**, thêm email, ngân sách dự kiến và link hộp thư của chính tài khoản đó. Email `@tuan89.site` tự ghép link hộp thư nếu để trống.
3. Bấm **Đăng nhập**. Một Chrome riêng được mở. Tool nhập email, tìm OTP mới từ hộp thư và đăng nhập. Nếu hộp thư không kết nối hoặc cần xác minh, giao diện ghi rõ; nhập mã trực tiếp trên cửa sổ Muse. Không gửi OTP cho người khác.
4. Ban đầu chọn **1 tài khoản**, bỏ chọn chạy ẩn. Chọn **Điền sẵn prompt để tôi gửi & tải** để kiểm tra điều khiển và thư mục trước.
5. Nhập một prompt: `Bình minh trên mặt biển, máy quay chuyển động chậm, video 5 giây, không có chữ.` Nếu muốn ảnh → video, chọn đúng prompt trong mục **Ảnh nhân vật cho prompt** rồi gắn một hoặc nhiều ảnh.
6. Bấm **Tạo & chạy**. Chọn nhiều ảnh chỉ hoạt động khi Muse cho phép tải nhiều file trong cùng ô chọn ảnh.
7. Tool điền prompt và tải ảnh tham chiếu lên đúng Chrome. Ở chế độ hỗ trợ, bấm Gửi trên Muse. Giữ nguyên dòng mã tác vụ để tool ghép kết quả đúng cảnh.
8. Video xuất hiện: bấm **ba chấm cạnh chính video mới → Tải xuống** như hình bạn đã gửi. Không dùng menu chia sẻ hoặc tải video cũ.
9. Tool chờ tải hoàn tất, kiểm tra MP4, đổi tên theo mã cảnh rồi hiển thị **Xong**. Bấm **Xem** để kiểm tra video đã gắn vào cảnh.
10. Sau khi thử thành công, chọn **Tự gửi prompt & tải video**, nhập nhiều prompt và tăng dần số tài khoản đồng thời. Có thể thử chạy ẩn sau đó.

## Thư mục và cách gắn cảnh

Ví dụ prompt đầu tiên của một lô:

`D:\Video-Muse\huy-<mã-tác-vụ>\Prompt 1.mp4`

Tên file MP4 dùng số thứ tự prompt trong lô (`Prompt 1.mp4`, `Prompt 2.mp4`, ...). Mỗi tác vụ vẫn nằm trong thư mục con riêng để tránh ghi đè giữa các lô. Đổi thư mục trên giao diện chỉ áp dụng cho tác vụ giao tiếp theo; tác vụ đang chạy giữ thư mục ban đầu. Tool nhớ thư mục cho lần mở sau. Trong Chrome riêng của tool, không đổi cài đặt tải sang “Hỏi nơi lưu mỗi lần”.

Nút **Tải MP4** trong bảng là tải thêm một bản từ kết quả đã lưu; không gửi lại yêu cầu tạo video. Nếu di chuyển/xóa file gốc thì tool không còn mở được video đó. Không đổi tên/di chuyển thư mục `huy-...` trong khi đang tải.

## Quy tắc hàng đợi

- Prompt mới → **Chờ**.
- Chọn tài khoản bật, đủ ngân sách và đang rảnh; ưu tiên tài khoản lâu chưa dùng. Giao việc trong một giao dịch SQLite để tránh hai luồng cùng nhận một cảnh.
- Giữ chỗ ngân sách dự kiến; chuyển **Đang chạy**, đăng nhập, gửi/điền prompt, chờ video, tải file.
- Chỉ **Xong** sau khi có đúng file MP4 mới, hết đuôi tải tạm, kích thước ổn định và cấu trúc MP4 đầy đủ. Video chat cũng phải có thời lượng đọc được. Đây không thay thế việc xem nội dung/chất lượng video.
- Khi thêm hàng đợi, các prompt được gán cân bằng theo lượt cho các tài khoản đang bật, đủ ngân sách và có thể nhận việc. Ví dụ 10 prompt với 5 tài khoản phù hợp sẽ được gán 2 prompt/tài khoản; 1 prompt chỉ được gán cho 1 tài khoản. Tên hiển thị lần lượt là **Prompt 1**, **Prompt 2**... theo thứ tự dòng trong mỗi lần thêm.
- Phiên Chrome/tab Muse được giữ mở cho prompt tiếp theo của tài khoản đó, kể cả khi lỗi tạo/tải video, nếu phiên Muse vẫn đăng nhập. Chỉ cần đăng nhập lại khi phiên hết hạn hoặc Chrome/phiên bị đóng; mở **Mở phiên Muse** sẽ dùng lại Chrome đang có nếu khả dụng.
- Gặp lỗi → **Lỗi**, ghi lý do. Nếu phiên còn đăng nhập, tài khoản vẫn sẵn sàng cho tác vụ tiếp theo; lỗi cần xác minh hoặc phiên hết hạn sẽ chuyển sang **Cần kiểm tra**. Không tự chuyển tác vụ đã gửi sang tài khoản khác.
- Lỗi chắc chắn trước khi bắt đầu gửi: hoàn ngân sách dự kiến, cho phép **Thử lại**.
- Đã bắt đầu điền/gửi hoặc bị tắt giữa chừng: giữ trạng thái cần kiểm tra, không tự gửi lại. Trong chế độ hỗ trợ cũng đánh dấu thận trọng vì bạn có thể đã bấm Gửi.
- **Dừng nhận thêm** giữ các tác vụ đang chạy tiếp tục tải xong; không hủy yêu cầu đã gửi lên Muse.
- Chọn checkbox ở đầu hàng để xóa một hoặc nhiều prompt không chạy. Xóa khỏi hàng đợi sẽ xóa ảnh tham chiếu không còn được dùng; file MP4 đã tải vẫn nằm trong thư mục lưu.
- Khởi động lại không tự chạy hàng loạt. Kiểm tra tác vụ bị gián đoạn rồi bấm **Chạy hàng đợi**.

## Khi lỗi

- **OTP/hộp thư:** dùng nút Đăng nhập để mở có cửa sổ. Tool chỉ nhận OTP mới so với lần đọc trước khi yêu cầu mã; không cố dùng mã cũ, không lưu OTP vào cơ sở dữ liệu/log.
- **Cần xem đúng tài khoản:** bấm **Mở phiên Muse** ở cảnh lỗi hoặc ở mục Tài khoản. Phiên đó được khóa khỏi hàng đợi trong lúc mở kiểm tra. Bấm **Đóng phiên** trong Tài khoản khi xong.
- **Đã có video nhưng lỗi tải:** mở đúng phiên để kiểm tra. Không tạo lại chỉ vì tải lỗi. File hoàn tất đã nằm trong thư mục tác vụ vẫn được giữ, không xóa.
- **Muse đổi giao diện/ngôn ngữ:** bản hiện tại nhận diện các nhãn tiếng Việt (`Nhắn tin`, `Lựa chọn khác`, `Tải xuống`). Chuyển Muse sang tiếng Việt nếu cần. Nếu vẫn khác, phải cập nhật adapter; tool báo lỗi thay vì đoán nút.
- **Timeout:** đợi video tối đa 20 phút; tải tự động tối đa 3 phút; chế độ hỗ trợ đợi thao tác tải tối đa 20 phút. Không tự gửi lại sau timeout.
- **Quá nhiều yêu cầu/cần xác minh:** xử lý trên Muse, đợi hạn mức phục hồi. Tool không vượt CAPTCHA hoặc chuyển tài khoản để gửi lại yêu cầu đã bị chặn.
- **Chrome không mở:** kiểm tra Chrome, Internet cho Selenium Manager và đóng Chrome cũ do tool mở nếu đã tắt cưỡng bức. Không mở hai bộ chạy trên cùng thư mục dữ liệu.

## Tiếp tục dự án cũ

Trong bản Huy Studio cũ, xuất dự án JSON. Trong bản này, bấm **Nhập .txt / .json**. Tool nhập các prompt loại `text` chưa có video, kèm quy tắc chung và thông số mong muốn. Sau khi nhập chúng trở thành các cảnh trong hàng đợi cục bộ; tài khoản và kết quả không tự đồng bộ trở lại website cũ.

## VS Code và kiểm tra

Mở thư mục này trong VS Code. Các phần chính:

- `web/`: giao diện.
- `runner/store.py`: hàng đợi và ngân sách dự kiến, lưu SQLite.
- `runner/engine.py`: chia công việc và quản lý phiên.
- `runner/muse.py`: đăng nhập, điền/gửi prompt, chọn video mới và tải file.
- `app.py`: máy chủ cục bộ và API nội bộ.
- `folder_picker.py`: hộp thoại chọn thư mục Windows.

Chạy kiểm tra: `python -m unittest discover -s tests -v` hoặc `KIEM_TRA.cmd` sau khi cài. Khởi động bằng terminal: `.venv\Scripts\python.exe app.py`.

Dữ liệu và phiên đăng nhập nằm trong `data/`, chỉ trên máy cài đặt. Không đưa `data/` hoặc `.venv/` lên Git, không gửi cho người khác và không thêm vào ZIP chia sẻ. Chỉ dùng tài khoản bạn có quyền truy cập. Server chỉ nghe `127.0.0.1`, kiểm tra Host/Origin và khóa mỗi phiên cho các lệnh thay đổi.

Tài liệu kỹ thuật dùng khi triển khai:
- https://www.selenium.dev/documentation/webdriver/browsers/chrome/
- https://developer.chrome.com/docs/chromedriver/capabilities
