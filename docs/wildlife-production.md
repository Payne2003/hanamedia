# Wildlife Production — phòng ban AI trong HANA MEDIA

Bản này mở rộng ứng dụng Python/SQLite và hàng đợi Muse hiện có. Các agent dùng **máy chủ API chung, API riêng hoặc tài khoản Gemini/ChatGPT/Claude trên trình duyệt**; không cần Zalo. Để chia sẻ chi phí API cho nhiều người bằng mã riêng và hạn mức, xem [hướng dẫn gateway](shared-api.md). Người dùng có thể vận hành thủ công hoặc giao từng công đoạn cho agent; chưa có chế độ tự chạy toàn bộ dự án không giám sát.

## Cách dùng

1. Mở **Wildlife · Sản xuất phim**, tạo dự án từ phác thảo cảnh hoặc dùng dự án Content Studio. Nên thử một dự án nhỏ trước.
2. Chọn cách kết nối trong **Cấu hình phòng ban**. Với máy chủ/API, cấu hình ở **Kết nối AI** theo hướng dẫn gateway. Nếu dùng trình duyệt, trong **Phòng điều phối AI** bấm đăng nhập từng dịch vụ, đăng nhập trên Chrome thường rồi đóng hẳn cửa sổ đó. Với Gemini/ChatGPT đang dùng hồ sơ chung, đăng nhập trong Cài đặt → Đăng nhập tài khoản chung. Claude dùng hồ sơ riêng `data/profiles/claude-web`.
3. Bấm **Cấu hình phòng ban**. Chọn agent chính, một hoặc hai agent phản biện, model nếu cần, số vòng phản biện 0–2 và giới hạn 1–10 lượt AI/tác vụ. Để trống model nếu muốn dùng model đang chọn trên website. Tên model được yêu cầu rõ ràng nhưng không chọn được thì tác vụ dừng, không âm thầm thay model.
4. Bấm **Giao agent** ở công đoạn sẵn sàng. Tool mở trình duyệt, gửi yêu cầu và lưu từng lượt hội ý. Nếu website yêu cầu đăng nhập/xác minh, người dùng tự hoàn tất trong cửa sổ đó. Tool không vượt CAPTCHA.
5. Đọc kết quả và bấm **Duyệt kết quả** khi có yêu cầu. Có thể **Trả lại tác vụ**, kiểm tra nguyên nhân rồi thử lại; không tự gửi lại yêu cầu AI bị gián đoạn.
6. Đến công đoạn video, nhận xử lý và giao prompt qua Studio. Kiểm tra ảnh nhân vật và thông số trước khi chạy Muse. Khi có take hoàn thành, dùng **Đồng bộ take Muse**; nếu có nhiều take cần chọn đúng job bằng thao tác thủ công.
7. QC cảnh xong mới mở dựng phim. Duyệt timeline để bắt đầu FFmpeg render, xem bản dựng rồi duyệt bản xem trước. Sau QC phim, xem/nghe toàn bộ và xác nhận âm thanh trước khi duyệt. Bước bàn giao tạo manifest có SHA-256.

Nội dung dự án và ảnh bằng chứng được gửi đến tài khoản AI đã chọn khi chạy agent. Tool không đọc mật khẩu. Trạng thái đăng nhập hiển thị là lần quan sát gần nhất, không đảm bảo phiên website hiện còn hiệu lực.

## Các phòng ban và mức tự động hóa

| Phòng ban | Đã triển khai | Điều kiện duyệt |
| --- | --- | --- |
| Nghiên cứu | Agent lập đề xuất từ brief, kịch bản, URL tham chiếu; tối đa hai AI phản biện | Luôn cần người duyệt; chưa xác minh rằng AI đã xem video tham chiếu |
| Đạo diễn & biên kịch | JSON bối cảnh/góc máy/hành vi/hành động, lưu vào Scene ID hiện có | Tự áp dụng nếu định dạng hợp lệ và không còn yêu cầu xem lại |
| Prompt | Agent viết prompt và phản biện; lưu `scenes.prompt` | Như biên kịch |
| Tạo video | Tái sử dụng hàng đợi/tài khoản Muse, liên kết take đúng Scene ID | Không tự gửi lại video; chỉ đồng bộ job đã hoàn thành |
| QC cảnh | Giải mã toàn clip bằng FFmpeg, đọc metadata, trích bốn frame và đính kèm cho AI | Luôn cần người duyệt; ảnh mẫu không xác minh toàn bộ chuyển động/âm thanh |
| Auto Edit | Mỗi cảnh được phân tích với frame mẫu để đề xuất in/out/speed; kiểm tra Scene/Job ID và giới hạn cắt; FFmpeg dựng timeline đã duyệt | Duyệt kế hoạch trước render và bản xem trước sau render |
| QC phim | Kiểm tra file dựng và đánh giá frame mẫu qua AI | Luôn cần người xem/nghe toàn bộ, xác nhận không lời thoại/nhạc/phụ đề |
| Bàn giao | Manifest gắn Scene ID/Job ID, báo cáo QC, đường dẫn phim và SHA-256 | Chỉ mở sau QC phim |

Âm thanh nguồn được giữ và điều chỉnh theo điểm cắt/tốc độ. Cảnh không có âm thanh được chèn khoảng lặng. Chưa có bộ phân loại nhạc/lời thoại hoặc thư viện bổ sung âm thanh động vật tự động; không tự tuyên bố phim đạt tiêu chí “chỉ tiếng tự nhiên”.

Đây là dựng theo kế hoạch từ các mẫu hình ảnh, chưa phải phát hiện đầy đủ đỉnh hành động, cut boundary và nhịp dựng của video tham chiếu. Không suy luận các chi tiết giữa hai frame mẫu là đã được quan sát.

## Hội ý và giới hạn

Agent chính đề xuất → một/hai agent phản biện → nếu có vấn đề thì agent chính tổng hợp. Tối đa hai vòng, đồng thời bị chặn bởi giới hạn số lượt gọi đã lưu. Nếu vòng cuối vẫn có phản biện hoặc hết ngân sách lượt gọi, kết quả chuyển sang cần xem lại; tác giả không được tự xóa bất đồng bằng cách tự khai báo đã đạt.

Hồ sơ Chrome chung chạy tuần tự. Toàn bộ dịch vụ điều phối nhận một tác vụ tại một thời điểm để tránh tranh chấp Content Studio và hồ sơ đăng nhập. Người dùng có thể chọn ba dịch vụ khác nhau hoặc nhiều vai trò trên cùng một dịch vụ. Claude là adapter mới, cần kiểm tra selectors/upload trên website thật của tài khoản người dùng; hiện đã kiểm thử hợp đồng DOM bằng trang mô phỏng.

Tạm dừng dự án chặn nhận việc/áp dụng kết quả tiếp theo; không hủy job Muse đã gửi. **Dừng agent** hủy chờ trình duyệt và chặn yêu cầu mới. FFmpeg đã chạy có thể vẫn hoàn thành bản dựng cục bộ; kết quả không tự mở công đoạn sau. Không tự gửi lại sau timeout, mất ứng dụng hoặc mất kết quả trình duyệt.

## QC lỗi và làm lại

Báo cáo QC cảnh có thể trả về **Sửa lại prompt cảnh này** hoặc **Yêu cầu take mới**. QC phim có thể yêu cầu **Dựng lại phim**. Tool duyệt đồ thị phụ thuộc, vô hiệu hóa kết quả của nhánh bị ảnh hưởng và giữ kết quả cũ trong nhật ký. Cảnh khác không bị chạy lại nếu không phụ thuộc.

Take cũ trước thời điểm vô hiệu hóa không được nhận lại làm kết quả của lần tạo video mới. File video cũ được giữ. Tạo take mới là thao tác rõ ràng của người dùng qua hàng đợi Muse; không phát sinh yêu cầu trả phí ngầm chỉ vì QC không đạt.

## Thành phần kỹ thuật

| Module | Vai trò |
| --- | --- |
| `runner/production.py` | Bảng công đoạn, phụ thuộc theo Scene ID, trạng thái thủ công và nhật ký |
| `runner/production_agents.py` | Worker, phân công mô hình, hội ý có giới hạn, duyệt, vô hiệu hóa downstream, đồng bộ Muse và bàn giao |
| `runner/ai_api.py`, `runner/ai_gateway.py` | API ba nhà cung cấp, kho khóa cục bộ, xác thực và hạn mức qua máy chủ chung |
| `runner/production_media.py` | Giải mã, frame bằng chứng, xác thực timeline, render và SHA-256 |
| `runner/gemini_web.py` | Thêm job `production`, trả kết quả JSON nguyên bản và đính kèm ảnh có kiểm tra |
| `runner/chatgpt_web.py`, `runner/claude_web.py` | Adapter theo website; không tạo HTTP API key |
| `runner/characters.py`, `runner/store.py`, `runner/engine.py` | Tái sử dụng dự án/nhân vật, SQLite và hàng đợi Muse |
| `web/production.js`, `web/production-agents.js` | Dashboard, cấu hình phòng ban, hội ý, bằng chứng và duyệt kết quả |

Giữ Python/JavaScript/SQLite; chưa cần Redis, Celery, Temporal, React hoặc PostgreSQL. Dữ liệu mới được thêm bằng các bảng riêng, không thay Project/Scene/job ID cũ.

Các lượt gọi lưu transport/model/usage; khóa không nằm trong database dự án. Bảng dữ liệu: `production_projects`, `production_tasks`, `production_events`, `production_agent_settings`, `production_runs`, `production_calls`. Tác vụ dùng trạng thái `waiting → working → done/error`. Lần chạy agent có `running/review/done/rejected/superseded`. Nhận việc/ghi kết quả/invalidation dùng giao dịch SQLite; kết quả AI không được phép tự chỉ định đường dẫn tệp hay lệnh FFmpeg.

Fingerprint của brief/cảnh/đầu vào công đoạn ngăn áp dụng báo cáo cũ sau khi dữ liệu đổi. SHA-256 của take, bằng chứng QC và bản dựng phát hiện tệp thay đổi trước duyệt/render. Mất ứng dụng chuyển lần chạy đang dở sang `review`, không khởi chạy lại. Tệp tạo nằm trong `data/production/<project_id>/<run_id>/`; `HANAMEDIA_DATA` vẫn hỗ trợ vị trí dữ liệu khác.

`SCENE_001` là nhãn hiển thị trong dự án; UUID `scenes.id` là khóa xuyên suốt. Xuất hồ sơ JSON hiện chứa 100 sự kiện và 50 lần chạy gần nhất, mỗi lần có các lượt hội ý. Nhật ký đầy đủ vẫn nằm trong SQLite.

## API nội bộ

Các mutation dùng kiểm tra Host/Origin và `X-Runner-Key`. Chạy agent/duyệt áp dụng giữ kiểm tra quyền chạy tác vụ. Giao video vẫn đi qua `/api/jobs` để giữ quota và quy tắc Muse.

| Endpoint | Payload |
| --- | --- |
| GET `/api/production` | Danh sách dự án |
| GET `/api/production/{project_id}` | Snapshot, cấu hình và các lần chạy gần nhất |
| POST `/api/production/create` | `name`, `brief`, `source_url`, `scenes: [{action,...}]` |
| POST `/api/production/enable` | `project_id` |
| POST `/api/production/control` | `project_id`, `action: pause/resume` |
| POST `/api/production/task` | `task_id`, `action: start/complete/fail/retry`, `output`, `job_id` |
| POST `/api/production/agent/configure` | `project_id`, `config` với agents, discussion_rounds, max_calls |
| POST `/api/production/agent/login` | `provider: gemini/chatgpt/claude` |
| POST `/api/production/agent/run` | `task_id` |
| POST `/api/production/agent/approve` | `run_id`, `audio_confirmed: true` khi duyệt QC phim |
| POST `/api/production/agent/reject` | `run_id` |
| POST `/api/production/agent/rework` | `run_id`, `target: prompt/video/edit` theo loại QC |
| POST `/api/production/agent/stop` | `{}` |
| POST `/api/production/agent/sync-muse` | `task_id`, `job_id` tùy chọn khi chỉ có một take phù hợp |
| GET `/api/production/run/{run_id}/film` | Phim MP4 có hỗ trợ Range |
| GET `/api/production/run/{run_id}/manifest` | Manifest JSON |
| GET `/api/production/run/{run_id}/frame/{1..4}` | Bằng chứng của lần QC |

Dịch vụ cần kết nối website và các miền đăng nhập/tải tệp liên quan. Cloud hiện không có các phiên AI đăng nhập sẵn và chưa kiểm chứng truy cập website thật. Khi chạy trên máy người dùng, cần kiểm tra thao tác gửi/đính kèm/đọc kết quả với từng tài khoản và phiên bản giao diện hiện tại.

## Kiểm thử và giới hạn bằng chứng

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_production*.py' -v
.venv/bin/python -m unittest discover -s tests -v
```

Các test bao phủ đồ thị công đoạn, nhiều người nhận cùng tác vụ, pause/restart, giới hạn lượt AI, phản biện ba provider, JSON sai, model không chọn được, kết quả cũ, QC trả về đúng nhánh, không dùng lại take cũ, API và quyền mutation. Một pipeline thử nghiệm chạy từ video thật qua decode/frame/QC/timeline/render/duyệt âm thanh/manifest; **phản hồi AI trong pipeline được mô phỏng**. FFmpeg thực thi thật.

Đã kiểm tra dashboard bằng Chromium thật: tạo dự án, cấu hình reviewer thứ hai, hiển thị ba lượt hội ý mô phỏng, duyệt kết quả, chuyển trang và màn hình nhỏ. Adapter Claude được kiểm tra bằng DOM fixture. Chưa kiểm thử đăng nhập, gửi ảnh hoặc chất lượng trả lời trên tài khoản ChatGPT/Gemini/Claude thật, chưa tạo video thật trên Muse trong phiên phát triển này.

Các bước còn lại để tiến tới tự động toàn bộ: kiểm chứng adapter website thật, phân tích video tham chiếu có bằng chứng, kiểm tra toàn bộ chuyển động/continuity và audio, kế hoạch âm thanh tự nhiên, điều phối cả dự án với lease/heartbeat và ngân sách theo thực chi. Không gọi bản hiện tại là hệ thống sản xuất phim hoàn toàn tự động.
