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
| Tạo ảnh | **Tạo ảnh · Google Flow** mở Flow bằng hồ sơ Chrome riêng của tool (`data/profiles/google-flow`, đăng nhập một lần trong **Cài đặt**), điền prompt, và lưu tải xuống Flow vào thư mục dữ liệu của tool. Trong Flow chọn **Hình ảnh**, tạo và tải kết quả; bấm **Nhập ảnh Flow đã tải** để thêm ảnh vào thư viện. |
| Chọn và xóa prompt | Chọn từng prompt hoặc toàn bộ kết quả lọc rồi xóa khỏi hàng đợi. Không xóa tác vụ đang chạy; video đã tải được giữ nguyên trong thư mục lưu. |
| Tự mở các tài khoản, prompt sẵn để dán | Chế độ hỗ trợ mở Chrome và điền sẵn prompt; bạn bấm Gửi và Tải xuống. Mỗi cảnh có thêm nút Chép prompt. |

Cũng có chế độ **Tự gửi prompt & tải video**, dùng menu **ba chấm cạnh video → Tải xuống** đã quan sát trên Muse. Có tùy chọn chạy ẩn Chrome sau khi đăng nhập và kiểm tra thành công có cửa sổ.

## Viết content bằng Gemini Pro (Prompt + video gốc → hàng loạt prompt)

Bấm **✎ Viết content** trên thanh điều hướng để mở trang Viết content (bấm **Studio** hoặc **← Về Studio** để quay lại; địa chỉ `http://127.0.0.1:8765/#content` mở thẳng trang này). Tool dùng gói Gemini của tài khoản Google (ví dụ Google AI Pro) trên gemini.google.com, không cần khóa API.

1. **Đầu vào**: Prompt (yêu cầu cho Gemini, có mẫu sẵn), Link video gốc (YouTube; có thể bỏ trống để Gemini tự viết kịch bản), ngôn ngữ kịch bản, **Tài khoản Google** và **Model** (ví dụ `Pro`, `Argon`; để trống thì dùng model đang chọn trong Gemini).
2. **Lần đầu**: bấm **① Đăng nhập Google (mở Chrome thường)**. Chrome mở bình thường (không bị điều khiển) bằng hồ sơ riêng của tool trong `data/profiles/gemini-web`. Đăng nhập tài khoản Google, thấy trang Gemini thì **đóng hẳn cửa sổ đó**. Google không cho đăng nhập trong cửa sổ Chrome đang bị điều khiển tự động (báo "Trình duyệt hoặc ứng dụng này có thể không an toàn"), nên phải đăng nhập theo cách này. Tool không đọc/lưu mật khẩu.
3. Bấm **Chạy tự động trên Gemini**. Tool mở Chrome với hồ sơ đã đăng nhập, dán yêu cầu (bằng Ctrl+V, nên clipboard bị thay), gửi, chờ Gemini viết xong; nếu câu trả lời bị cắt thì tự nhắn "viết tiếp" (tối đa 12 lần) rồi ghép và tách cảnh. Trong lúc chạy, giữ cửa sổ Gemini mở và không gõ vào đó; bấm **Dừng** để ngừng.
4. Đầu ra gồm **Kịch bản video gốc** và danh sách cảnh, mỗi cảnh có: bối cảnh, góc quay, hành vi nhân vật, nội dung hành động nhân vật. Có thể sửa, thêm, xóa, đổi thứ tự cảnh.
5. Bấm **Kiểm tra thiếu cảnh / lỗi**: Gemini so lại với video gốc, bổ sung cảnh bị bỏ sót và viết lại cảnh dễ bị AI video từ chối/không tạo được (người nổi tiếng, logo, chữ trên màn hình, quá nhiều hành động…). Vấn đề hiện ở khung **Cần xem lại**; tool cũng tự báo cảnh bị trống trường.
6. **Hàng loạt prompt** được ghép theo cấu trúc, mỗi cảnh một dòng:
   `Bối cảnh 1: ..., góc quay: ..., hành vi nhân vật: ..., Nội dung hành động nhân vật: ...;`
   Bấm **Đưa vào Studio** (tool chuyển về trang Studio, prompt đã nằm trong khung tạo video) rồi **Tạo & chạy** như bình thường, hoặc **Chép** / **Tải .txt**.

### Prompt tiếng Anh (bước 4)

Khung **④ Prompt tiếng Anh · Gemini Pro** ở cuối trang Viết content:

1. **Đầu vào**: bấm **Lấy từ danh sách cảnh** (lấy hàng loạt prompt ở bước 3) hoặc tự dán danh sách `Bối cảnh 1: ..., góc quay: ..., hành vi nhân vật: ..., Nội dung hành động nhân vật: ...;` (mỗi dòng một cảnh, hoặc cả danh sách trong `[ ... ]` nối bằng dấu `;`). Chọn **Thời lượng mỗi cảnh** (mặc định **15 giây**; có 10, 8, 5 giây): mỗi prompt là một shot liền mạch khoảng chừng đó giây và ghi rõ `15 seconds` ở cuối. Chọn **Tỷ lệ khung hình** (mặc định **16:9** ngang; có 9:16 dọc, 1:1 vuông): Gemini viết khung hình theo tỷ lệ đó và kết thúc mỗi prompt bằng `16:9 aspect ratio`. Có thể thêm **Phong cách chung** cho mọi prompt.
2. Bấm **Viết prompt tiếng Anh bằng Gemini**. Gemini viết mỗi cảnh thành một prompt tiếng Anh, đánh số theo cảnh. Câu trả lời bị cắt thì tool nhắn viết tiếp.
3. Theo sơ đồ: cảnh nào **thiếu** hoặc **vẫn còn tiếng Việt**, tool tự hỏi lại Gemini riêng những cảnh đó (tối đa 3 lượt). Nếu vẫn thiếu, khung **Còn thiếu** ghi rõ số cảnh và dòng đó để trống để bạn sửa tay hoặc chạy lại.
4. **Đầu ra**: mỗi dòng 1 prompt tiếng Anh, đúng thứ tự cảnh, sửa được trực tiếp. Bấm **Đưa vào Studio**, **Chép** hoặc **Tải .txt**.
5. **Prompt chuẩn đủ trường** (bật sẵn): Gemini/ChatGPT phải viết mỗi cảnh đúng một dòng theo khung
   `N. Setting: …; Camera: …; Character behavior: …; Action: …; Style: …; 15 seconds; 16:9 aspect ratio.`
   Thuật toán: (1) yêu cầu kèm khung và một dòng ví dụ; (2) tool tìm vị trí các nhãn Setting / Camera / Character behavior / Action / Style trong mỗi dòng và cắt đoạn chữ giữa hai nhãn liên tiếp làm giá trị trường (bỏ dấu `;` và đuôi tỷ lệ khung hình); (3) cảnh nào thiếu dòng, còn tiếng Việt **hoặc thiếu một trong 4 trường** bị coi là thiếu và được hỏi lại riêng (tối đa 3 lượt); (4) prompt gửi lên Muse là cả dòng có nhãn, Prompt Studio tách các trường thẳng từ prompt này. Tắt ô này nếu muốn prompt văn xuôi tự do (khi đó các trường lấy từ dòng cảnh tiếng Việt như mục trên).
6. **Chọn AI**: bấm **Gemini Pro** hoặc **ChatGPT** phía trên nút viết. Kết quả của mỗi bên được lưu riêng: chuyển qua lại để so sánh, chọn bên tốt hơn rồi **Đưa vào Studio**. Hai bên chạy được cùng lúc (mỗi bên một Chrome riêng).

**ChatGPT**: tool điều khiển chatgpt.com giống Gemini, với hồ sơ Chrome riêng `data/profiles/chatgpt-web`. Lần đầu bấm **Đăng nhập ChatGPT (mở Chrome thường)**, đăng nhập tài khoản ChatGPT của bạn, thấy ô nhắn tin thì **đóng hẳn cửa sổ đó**. Ô **Model ChatGPT** (ví dụ `Thinking`, `Instant`) để trống thì dùng model đang chọn. Nếu ChatGPT hiện trang xác minh "tôi không phải người máy", bạn tự hoàn tất trong cửa sổ ChatGPT, tool chờ rồi chạy tiếp. Lỗi đọc kết quả được lưu vào `data/profiles/chatgpt-debug.txt`.

Tool chỉ đọc câu trả lời khi Gemini đã viết xong (không còn nút Dừng, đã hiện nút Sao chép/Thích dưới câu trả lời) và bỏ qua phần "suy nghĩ" của Gemini; không gửi yêu cầu mới khi Gemini còn đang viết. Nếu câu trả lời đầu tiên không đọc ra cảnh/prompt nào, tool **dừng và báo lỗi, không gửi lại**, đồng thời lưu file chẩn đoán `data/profiles/gemini-debug.txt` (chứa nội dung cuộc trò chuyện vừa rồi, không chứa mật khẩu) để gửi cho người hỗ trợ.

Nếu AI đã viết xong trên cửa sổ Chrome mà tool vẫn hiện đang chờ, bấm **Lấy kết quả ngay** cạnh nút Dừng: tool đọc luôn câu trả lời đang hiện. Trong lúc chờ, cứ mỗi phút tool ghi trạng thái trang vào `data/profiles/gemini-debug.txt` hoặc `chatgpt-debug.txt` để gửi cho người hỗ trợ nếu bị kẹt.

Bản nháp (yêu cầu, link, các cảnh) được giữ trên trình duyệt khi chuyển trang hoặc tải lại. Tool dò ô nhập, nút Gửi và câu trả lời theo giao diện Gemini hiện tại; nếu Google đổi giao diện mà tool báo không điền/không đọc được, cần cập nhật tool. Việc điều khiển trình duyệt tự động có thể trái điều khoản sử dụng của Google; dùng ở mức vừa phải trên tài khoản của chính bạn.

## Giao diện

- Thanh bên trái: **Studio · Video Queue** (hàng đợi và tạo video), **Content Studio**, **Character Library** (mở thẳng bước Nhân vật), **Image Library**, **Tài khoản Muse**. Góc dưới hiện trạng thái kết nối, số luồng đang chạy và phiên bản.
- **⚙ Cài đặt** (thanh bên trái): đăng nhập mọi tài khoản tool cần ở một chỗ, không phải đăng nhập trong Viết content nữa.
  - **Gemini Pro**: tài khoản Google, model, nút **Đăng nhập Google · Gemini (mở Chrome thường)**.
  - **ChatGPT**: model, nút **Đăng nhập ChatGPT (mở Chrome thường)**.
  - **Google Flow · Nano Banana**: hiện hồ sơ Chrome dùng cho Flow, nút **Đăng nhập Google Flow (mở Chrome thường)**. Tool đóng cửa sổ Flow đang điều khiển (nếu có) rồi mở Chrome thường với đúng hồ sơ đó; đăng nhập xong thì đóng hẳn cửa sổ.
  - **Tài khoản Muse**: số tài khoản, trạng thái, nút mở bảng quản lý/đăng nhập Muse.
  - **Thư mục lưu video**.
  Content Studio chỉ còn dòng "Tài khoản Gemini: … · Đổi trong Cài đặt" và nút chạy.
- **Content Studio** có thanh 5 bước: **1 Ý tưởng** (yêu cầu + link video, chạy Gemini) → **2 Kịch bản** (kịch bản và các cảnh; tự chuyển sang khi Gemini viết xong) → **3 Nhân vật** (Character Library) → **4 Prompt** (prompt tiếng Anh) → **5 Tổng quan**. Ô **Dự án** ở trên cùng để mở lại dự án đã lưu.

## Character Library: nhân vật của dự án (bước 3 · Nhân vật trong Content Studio)

Mục tiêu: mỗi nhân vật có **một ảnh Master** dùng lại cho mọi cảnh, không tạo ảnh riêng cho từng cảnh.

1. Viết cảnh ở bước 2 như trước, rồi bấm **💾 Lưu danh sách cảnh thành dự án**. Mỗi cảnh được cấp một Scene ID riêng (không dùng số thứ tự cảnh làm khóa).
2. Chọn **Gemini Pro** hoặc **ChatGPT**, bấm **✦ Tìm nhân vật**. AI đọc kịch bản và các cảnh, trả về:
   - danh sách nhân vật **duy nhất**, mỗi nhân vật một **Character ID** (ví dụ `DE_DUC`, `BAO_TUYET`), mô tả ngoại hình cố định và **prompt ảnh Master** tiếng Anh (toàn thân, nhìn chính diện, nền trơn, không có bối cảnh cảnh quay);
   - nhân vật xuất hiện trong **từng cảnh**. Cảnh nào AI bỏ sót, tool tự hỏi lại riêng các cảnh đó (tối đa 3 lần, dừng nếu không có tiến triển).
3. Sửa tên, mô tả, prompt ảnh của từng nhân vật rồi bấm **Lưu**; thêm nhân vật thủ công nếu thiếu; xóa nhân vật thừa.
4. Ở **Nhân vật trong từng cảnh**, tick nhân vật rồi bấm **Lưu** để sửa tay. Cảnh đã chỉnh tay hiện "đã chỉnh tay" và **không bị AI ghi đè** khi bấm Tìm nhân vật lần nữa. Nhân vật đã duyệt ảnh hoặc đã khóa cũng không bị AI sửa.
5. Mỗi cảnh hiện số nhân vật; mỗi nhân vật hiện số cảnh có mặt. Danh sách nhân vật ở bên trái, bấm một nhân vật để xem và sửa chi tiết bên phải. Nút **Duyệt / Khóa** dùng được khi nhân vật đã có ảnh Master; **Tạo lại** và **Thay ảnh** sẽ hoạt động khi có bước tạo ảnh bằng Google Flow.

**Ảnh Master của nhân vật (bán tự động, Google Flow · Nano Banana):**
1. Chọn nhân vật, bấm **✦ Mở Flow & điền prompt**. Tool mở Google Flow (hồ sơ Chrome riêng của tool) và điền prompt tạo ảnh của nhân vật. Trong Flow bạn chọn **Hình ảnh**, mô hình **Nano Banana**, bấm tạo rồi tải ảnh xuống. (Veo là mô hình tạo **video**; ảnh nhân vật tạo bằng Nano Banana/Imagen.)
2. Bấm **⇄ Thay ảnh → ⇩ Lấy ảnh mới tải từ Flow**, rồi bấm ảnh muốn dùng: ảnh đó thành **ảnh Master đã duyệt**. Cũng có thể **Tải ảnh lên** hoặc chọn ảnh có sẵn trong thư viện.
3. Mỗi ảnh của nhân vật hiện thành ảnh nhỏ dưới ảnh lớn; bấm ảnh nhỏ để đổi ảnh Master. Bấm **🔒 Duyệt / Khóa** để khóa: không đổi ảnh, không sửa, AI không ghi đè.

**Nhân vật và ảnh tham chiếu của từng prompt (Prompt Studio):**
- Prompt đưa từ bước 4 sang Studio (khi dự án có cùng số cảnh) được nối với cảnh gốc, nên tự có danh sách **Nhân vật** của cảnh đó. Prompt khác có thể tick chọn nhân vật của dự án đang mở rồi bấm **Lưu nhân vật**.
- **Ảnh nhân vật tham chiếu** hiện ảnh Master của từng nhân vật trong prompt, kèm trạng thái: đã gắn / chưa gắn / thiếu ảnh Master.
- **🔗 Gắn ảnh nhân vật vào prompt** đặt đúng ảnh Master của các nhân vật trong prompt vào ảnh đầu vào của prompt (tối đa 10 ảnh, giới hạn của Muse). Tool hỏi xác nhận trước khi chuyển prompt sang chế độ **Ảnh → video** hoặc trước khi thay ảnh bạn tự gắn; không gắn nhân vật không có trong prompt, không sửa prompt đang chạy.

Chưa có trong bản này: tự động hoàn toàn bước tạo và tải ảnh trên Flow, và gắn ảnh hàng loạt cho mọi prompt cùng lúc.

## Mức độ đã xác minh — đọc trước khi chạy

- Tạo ảnh dùng giao diện Google Flow, không dùng API riêng/không công khai. Flow chạy trong hồ sơ Chrome riêng của tool `data/profiles/google-flow` (giống Gemini/ChatGPT), nên máy nào cũng dùng được: lần đầu vào **Cài đặt → Google Flow → Đăng nhập Google Flow (mở Chrome thường)**, đăng nhập rồi đóng hẳn cửa sổ đó. Tool điền prompt; bạn chọn **Hình ảnh**, bấm tạo và tải ảnh trong Flow, rồi nhập ảnh tải xuống vào thư viện. Muốn dùng một hồ sơ Chrome khác: đặt biến môi trường `HUMY_FLOW_PROFILE_PATH`.
- Đã quan sát đăng nhập Muse bằng email + OTP, gửi một yêu cầu video 5 giây, nhận video thật có thời lượng 5 giây và menu Tải xuống trên tài khoản mẫu trong trình duyệt ngày 02/11/2026.
- Luồng tải ảnh lên Muse phụ thuộc vào ô chọn ảnh của giao diện Muse hiện tại và chưa được xác minh bằng một video ảnh → video thật. Nếu Muse không cung cấp ô tải ảnh tương thích hoặc không nhận nhiều ảnh, tác vụ sẽ báo lỗi trước khi gửi prompt.
- Mã Selenium bám theo các nhãn/DOM vừa quan sát. Đây là bản triển khai để thử trên Windows; **chưa kiểm thử trọn quy trình bằng chính gói này trên Windows và chưa thử nhiều tài khoản thật cùng lúc**. Phiên Chrome riêng, chế độ ẩn, thư mục tải và lấy OTP tự động cần kiểm tra trên máy cài đặt.
- Đã chạy các bài kiểm tra tự động cho hàng đợi, đồng thời, ngân sách, chống thêm trùng, khôi phục sau gián đoạn, OTP/link hộp thư, file MP4 chưa tải xong và bảo vệ HTTP cục bộ. Test không tạo video thật.
- Chưa tự đọc số dư credit Muse. Credit trong tool là **ngân sách dự kiến do bạn nhập**, giữ chỗ khi giao tác vụ. Cập nhật theo số dư/chi phí thực tế trên Muse. Hết ngân sách hoặc tài khoản cần kiểm tra thì không giao thêm việc.
- Tạo ảnh độc lập chưa tích hợp. Ảnh tham chiếu có thể gắn riêng cho từng prompt → video; ảnh được giữ trong `data/input-images/` khi tác vụ còn trong hàng đợi.

## Cài trên Windows

1. Cài Google Chrome và Python 3.11 trở lên. Python chính thức: https://www.python.org/downloads/windows/ . Khi cài Python, chọn **Add Python to PATH**.
2. Giải nén toàn bộ ZIP vào thư mục bạn giữ lâu dài, ví dụ `D:\Huy-Muse-Runner`. Không chạy trực tiếp bên trong file ZIP.
3. Chạy **CAI_DAT_WINDOWS.cmd** một lần để tạo môi trường Python và cài Selenium. Cần Internet; lần mở Chrome đầu có thể cần tải ChromeDriver tương thích.
4. Chạy **CHAY_WINDOWS.cmd**. Giao diện mở tại `http://127.0.0.1:8765`.
5. Giữ cửa sổ bộ chạy mở. Đóng trang giao diện không dừng tác vụ, nhưng tắt bộ chạy hoặc cho máy ngủ sẽ làm gián đoạn.

## App Windows (HanaMedia.exe)

Ngoài cách chạy bằng mã nguồn (`CHAY_WINDOWS.cmd`), tool có bản **app Windows**: cửa sổ riêng "HANA MEDIA", có biểu tượng ở Desktop/Start Menu, không cần cài Python, không có cửa sổ đen. Bên trong vẫn là đúng mã của repo này.

**Lấy bộ cài**
- Mỗi lần `main` được cập nhật, GitHub tự build bộ cài: vào repo trên GitHub → tab **Actions** → lần chạy **Windows app** mới nhất → mục **Artifacts** → tải **HanaMedia-Setup** (file zip chứa `HanaMedia-Setup-x.y.z.exe`).
- Hoặc tự build trên máy: chạy `CAI_DAT_WINDOWS.cmd` một lần, rồi `DONG_GOI_WINDOWS.cmd`. Kết quả ở `dist\HanaMedia\HanaMedia.exe`; nếu máy có [Inno Setup 6](https://jrsoftware.org/isdl.php) thì có thêm `dist\HanaMedia-Setup-x.y.z.exe`.

**Cài và dữ liệu**
- Bộ cài cài vào `%LOCALAPPDATA%\Programs\HanaMedia`, không cần quyền quản trị. Lần đầu Windows có thể báo SmartScreen ("ứng dụng không rõ nguồn gốc") vì app chưa ký số: bấm **Thông tin thêm → Vẫn chạy**.
- Dữ liệu của app nằm riêng ở `%LOCALAPPDATA%\HanaMedia\data`: tài khoản, hàng đợi, ảnh, và các **hồ sơ Chrome đã đăng nhập** (Muse, Gemini, ChatGPT).
- **Lần đầu mở app**, app tự tìm dữ liệu của bản mã nguồn (ví dụ `D:\Huy-Muse-Runner\data`) và **chép** sang, nên các tài khoản và Chrome đã đăng nhập vẫn giữ nguyên, không phải đăng nhập lại. Đóng `CHAY_WINDOWS.cmd` trước khi mở app lần đầu (app không chép dữ liệu đang mở). Thư mục cũ không bị sửa hay xóa. Nếu dữ liệu cũ nằm chỗ khác: đóng app, chạy `set HANAMEDIA_IMPORT=E:\đường-dẫn\data` rồi mở `%LOCALAPPDATA%\Programs\HanaMedia\HanaMedia.exe` từ cùng cửa sổ đó.
- Hồ sơ Chrome dùng cho Flow nằm trong dữ liệu của tool (`profiles/google-flow`), nên được chép sang cùng các hồ sơ khác.

**Cập nhật và sửa tính năng sau khi đã đóng gói**
1. Sửa code trong repo như trước (hoặc nhờ Claude sửa), merge vào `main`.
2. GitHub tự build bộ cài mới (tab **Actions**, khoảng 5–10 phút). Muốn đánh số phiên bản: tăng `APP_VERSION` trong `runner/version.py`, rồi tạo tag `v1.0.1` để bộ cài được đăng ở mục **Releases**.
3. Đóng app, chạy bộ cài mới. Bộ cài chỉ thay phần chương trình; **dữ liệu và các Chrome đã đăng nhập giữ nguyên**. Gỡ cài đặt cũng không xóa dữ liệu.
- Muốn thử nhanh một bản sửa mà không cần đóng gói: chạy bằng mã nguồn `CHAY_WINDOWS.cmd`. Để bản mã nguồn dùng chung dữ liệu với app, đóng app rồi chạy `set HANAMEDIA_DATA=%LOCALAPPDATA%\HanaMedia\data` trước `CHAY_WINDOWS.cmd` trong cùng cửa sổ lệnh. Không mở app và bản mã nguồn cùng lúc trên cùng dữ liệu (tool sẽ báo "đã mở ở cửa sổ khác").

**Khi đóng cửa sổ app**: tool hỏi xác nhận, rồi dừng nhận tác vụ mới và đóng mọi Chrome của tool. Tác vụ đã gửi lên Muse mà chưa tải về sẽ được đánh dấu cần kiểm tra ở lần mở sau (như khi tắt bộ chạy cũ).

**Máy thiếu WebView2** (hiếm trên Windows 10/11): app tự mở giao diện trong trình duyệt và hiện hộp thông báo; bấm OK ở hộp đó là tắt tool. Cài [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) để có cửa sổ app.

## Thử một video trước

1. Bấm **Chọn thư mục lưu**, chọn thư mục bạn muốn, ví dụ `D:\Video-Muse`.
2. Mở **Tài khoản**, thêm email, ngân sách dự kiến và link hộp thư của chính tài khoản đó. Email `@tuan89.site` tự ghép link hộp thư nếu để trống.
3. Bấm **Đăng nhập**. Một Chrome riêng được mở. Tool nhập email, tìm OTP mới từ hộp thư và đăng nhập. Nếu hộp thư không kết nối hoặc cần xác minh, giao diện ghi rõ; nhập mã trực tiếp trên cửa sổ Muse. Không gửi OTP cho người khác.

### Đăng nhập hàng loạt tài khoản (không giới hạn số tài khoản)

- **Thêm nhiều email một lần**: trong **Tài khoản**, dán mỗi dòng một email vào ô *Email Muse* rồi bấm **+ Thêm tài khoản**. Email `@tuan89.site` tự có link hộp thư OTP; email trùng được bỏ qua, email sai định dạng được báo lại.
- **Đăng nhập tất cả tài khoản chưa sẵn sàng**: chọn số Chrome đăng nhập cùng lúc (mặc định 2) rồi bấm nút. Với từng tài khoản tool mở muse.ai → nhập email → bấm **Tiếp tục** → mở hộp thư Tuan89 ở tab bên cạnh, rồi **chờ bạn tự dán mã** (tối đa 5 phút): sang tab Tuan89 bấm **Copy OTP**, quay lại Muse dán vào ô mã. Trong lúc chờ, tool không chuyển tab để không làm gián đoạn. Vào được Muse thì tool tự đóng tab hộp thư, đóng Chrome và lưu phiên trong hồ sơ riêng `data/profiles/<mã tài khoản>`.
- Chỉ khi chạy **ẩn** (không có cửa sổ) tool mới tự đọc hộp thư và điền mã. Nếu không được, xem file `data/profiles/<mã tài khoản>-login-debug.txt` (mọi mã 6 số trong file đều đã được che).
- Tool nhận biết thư mới theo **giờ nhận thư**, vì Meta hay gửi lại đúng mã cũ trong thư mới. Thư đã có trước khi bấm Tiếp tục thì không dùng. Sau 60 giây chưa thấy thư, tool bấm **Gửi lại mã** một lần; sau 3 phút vẫn chưa được thì tài khoản chuyển sang **Cần kiểm tra**, các tài khoản khác vẫn chạy tiếp.
- Nếu Muse hiện xác minh/CAPTCHA, tool không vượt qua: tài khoản đó dừng, bạn mở phiên và tự xử lý.
4. Ban đầu chọn **1 tài khoản**, bỏ chọn chạy ẩn. Chọn **Điền sẵn prompt để tôi gửi & tải** để kiểm tra điều khiển và thư mục trước.
5. Nhập một prompt: `Bình minh trên mặt biển, máy quay chuyển động chậm, video 5 giây, không có chữ.` Nếu muốn ảnh → video, chọn đúng prompt trong mục **Ảnh nhân vật cho prompt** rồi gắn một hoặc nhiều ảnh.
6. Bấm **Tạo & chạy**. Chọn nhiều ảnh chỉ hoạt động khi Muse cho phép tải nhiều file trong cùng ô chọn ảnh.
7. Tool điền prompt và tải ảnh tham chiếu lên đúng Chrome. Ở chế độ hỗ trợ, bấm Gửi trên Muse. Giữ nguyên dòng mã tác vụ để tool ghép kết quả đúng cảnh.
8. Video xuất hiện: bấm **ba chấm cạnh chính video mới → Tải xuống** như hình bạn đã gửi. Không dùng menu chia sẻ hoặc tải video cũ.
9. Tool chờ tải hoàn tất, kiểm tra MP4, đổi tên theo mã cảnh rồi hiển thị **Xong**. Bấm **Xem** để kiểm tra video đã gắn vào cảnh.
10. Sau khi thử thành công, chọn **Tự gửi prompt & tải video**, nhập nhiều prompt và tăng dần số tài khoản đồng thời. Có thể thử chạy ẩn sau đó.

## Prompt Studio: sửa từng prompt trong hàng đợi

Bấm vào **tên hoặc nội dung** một prompt trong **Hàng đợi** để mở **Chi tiết Prompt / Prompt Studio** (địa chỉ `#job=<mã>`; nút trình duyệt Quay lại cũng dùng được).

- Prompt đưa vào từ bước **④ Prompt tiếng Anh** (Gemini hoặc ChatGPT) là một đoạn văn tiếng Anh liền, không chia trường. Khi bấm **Đưa vào Studio**, tool ghép **prompt tiếng Anh số N với dòng cảnh tiếng Việt số N** ở ô Đầu vào của bước ④, nên trong Prompt Studio: ô **Prompt** là câu tiếng Anh (gửi lên Muse), còn **Bối cảnh / Góc quay / Hành vi nhân vật / Nội dung hành động** lấy từ dòng tiếng Việt cùng số cảnh.
- Sửa **Prompt** (nội dung gửi lên Muse) và các ghi chú cảnh: **Bối cảnh**, **Hành vi nhân vật**, **Nội dung hành động nhân vật**, **Góc quay**. Prompt dạng `Bối cảnh N: ..., góc quay: ..., hành vi nhân vật: ..., Nội dung hành động nhân vật: ...;` được tự tách vào các ô này.
- **Ảnh tham chiếu**: kéo thả hoặc **Chọn tệp** (JPG, PNG, WEBP, tối đa 10 MB, tối đa 10 ảnh); bấm ✕ để bỏ ảnh. Ảnh tải lên cũng được lưu vào Thư viện.
- **Loại**: Video (prompt → video) hoặc Ảnh → video (cần ít nhất 1 ảnh). **Tài khoản**: để "Chờ tài khoản" cho tool tự chia, hoặc chọn một tài khoản cụ thể.
- **Lưu** để lưu và quay về hàng đợi; **Hủy** để bỏ thay đổi; **Quay lại hàng đợi** để thoát (hỏi nếu còn thay đổi chưa lưu).
- **Chạy lại** (hoặc chọn Trạng thái "Chờ (chạy lại prompt này)") đưa prompt **Xong** hoặc **Lỗi** về **Chờ**; video cũ vẫn giữ trong thư mục lưu. Prompt lỗi sau khi đã gửi lên Muse cần xác nhận vì có thể đã tốn credit.
- Prompt **Đang chạy** chỉ xem được, không sửa. Prompt Xong/Lỗi mở ở chế độ xem: bấm **Sửa Prompt** để sửa.

## Xóa tài khoản

Trong **Tài khoản**, bấm **✕ Xóa** ở tài khoản cần bỏ (ví dụ đã hết hạn hoặc hết credit) rồi xác nhận. Tool đóng phiên Chrome của tài khoản đó, xóa phiên đăng nhập trong `data/profiles/<mã tài khoản>` và bỏ tài khoản khỏi danh sách. Prompt đang **Chờ** của tài khoản này được chia lại cho các tài khoản còn lại; video đã tải và lịch sử tác vụ giữ nguyên (cột tài khoản hiện **TK đã xóa**). Không xóa được tài khoản đang chạy tác vụ, đang đăng nhập hoặc đang mở phiên: đợi xong hoặc bấm **Đóng phiên** trước.

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
- Mỗi tài khoản chỉ có **một** Chrome. Chrome mở bằng nút **Đăng nhập** được giữ lại cho tác vụ đầu tiên (không đóng rồi mở lại). Trước mỗi tác vụ tool kiểm tra phiên: Chrome bị đóng thì mở Chrome mới; phiên Muse hết hạn thì đăng nhập lại ngay trên Chrome đó. Bấm **Đăng nhập**/**Mở phiên** khi tài khoản đang chạy tác vụ sẽ bị từ chối, không làm đóng Chrome của tác vụ.
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
- `runner/characters.py`: Character Registry (Character Master) — dự án, cảnh (Scene ID), nhân vật (Character ID), ảnh Master đã duyệt/khóa, nhân vật của từng cảnh và hàng đợi tạo ảnh nhân vật. Lưu trong cùng `studio.sqlite3` (bảng `projects`, `scenes`, `characters`, `scene_characters`, `character_assets`, `image_tasks`; thêm `jobs.scene_id`, `jobs.images_origin`). Dữ liệu cũ tự nâng cấp khi mở, không mất job, tài khoản hay ảnh.
- `runner/muse.py`: đăng nhập, điền/gửi prompt, chọn video mới và tải file.
- `app.py`: máy chủ cục bộ và API nội bộ.
- `folder_picker.py`: hộp thoại chọn thư mục Windows.

Chạy kiểm tra: `python -m unittest discover -s tests -v` hoặc `KIEM_TRA.cmd` sau khi cài. Khởi động bằng terminal: `.venv\Scripts\python.exe app.py`.

Dữ liệu và phiên đăng nhập nằm trong `data/`, chỉ trên máy cài đặt. Không đưa `data/` hoặc `.venv/` lên Git, không gửi cho người khác và không thêm vào ZIP chia sẻ. Chỉ dùng tài khoản bạn có quyền truy cập. Server chỉ nghe `127.0.0.1`, kiểm tra Host/Origin và khóa mỗi phiên cho các lệnh thay đổi.

Tài liệu kỹ thuật dùng khi triển khai:
- https://www.selenium.dev/documentation/webdriver/browsers/chrome/
- https://developer.chrome.com/docs/chromedriver/capabilities
