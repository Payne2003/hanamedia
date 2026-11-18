# YÊU CẦU: Tích hợp "Tạo YouTube Short hàng loạt bằng Veo" vào tool HanaMedia

> Dán toàn bộ file này vào đoạn chat mới. Mã nguồn tool: https://github.com/Payne2003/hanamedia (Python, UI web
> local, đóng gói Windows bằng PyInstaller). Mã tham khảo đã chạy được (gọi tắt **HMS**) nằm ở nhánh
> `claude/hana-short-veo`, thư mục `hana-short/` của kho này (bản gốc trên máy: `C:\Users\Admin\HanaMediaShort`)
> - port logic từ đó vào `runner/` + `web/` của tool, không viết lại từ đầu. Đường dẫn `HMS/...` bên dưới = `hana-short/...`.

## 1. Mục tiêu
Trong tool HanaMedia, người dùng chọn **ảnh Nhân vật** + **ảnh Sản phẩm** có sẵn trong *Thư viện Video & Ảnh*
→ chọn số video → tool tự sản xuất hàng loạt YouTube Short 9:16:
1. Claude (skill `hana-short`) viết kịch bản 3 cảnh × 8 giây: **Hook → Demo → CTA** (lời thoại tiếng Việt ≤ 20 từ/cảnh,
   prompt Veo tiếng Anh, `character_bible` nhất quán, tiêu chí duyệt riêng từng cảnh).
2. Tạo từng cảnh bằng **Veo trên Google Flow** (flow.google.com) với ảnh tham chiếu, 9:16, 8 giây, x1.
3. Duyệt từng clip bằng storyboard FFmpeg + Claude → PASS / RETRY / REVIEW_REQUIRED, chỉ tạo lại cảnh lỗi, giới hạn số lần.
4. Dựng thành phẩm: nối 3 clip + SFX từ thư viện âm thanh → MP4 1080×1920 (FFmpeg, tự kiểm tra) + draft CapCut để chỉnh tay.
5. Hiện tiến độ trong tool (Studio/hàng đợi + Không gian Agent).

**Brief KHÔNG bắt buộc** - chỉ cần ảnh sản phẩm. Không có brief → kịch bản không được nêu công dụng, thông số, chất liệu,
giá, ưu đãi, con số (chỉ cảm xúc, cách dùng nhìn thấy được, CTA chung). Có brief → chỉ được dùng tuyên bố có NGUYÊN VĂN
trong brief.

## 2. Hiện trạng tool (đã đọc mã nguồn)
- `runner/media.py`: bảng `media` trong `data/studio.sqlite3`; `category IN ('character','product','reference','other')`;
  `media_id` 32 hex = token ảnh dùng chung với `jobs.images`; file = `data/<relative_path>` khi `storage_root=''`.
  Hiện máy người dùng chưa có ảnh nào gắn `character`/`product` (14 ảnh `reference`).
- `runner/store.py`: bảng `jobs` (prompt, status, images JSON, generation_mode, aspect, duration, resolution,
  character_ids, folder, batch, ordinal, auto_retries, output…), `store.enqueue(prompts, cost, batch, images, mode,
  sources, scene_ids, options)`; `batch_options` (stitch). `accounts` = tài khoản cho Studio.
- **Studio hiện tạo video bằng Meta Muse** (`runner/muse.py`). **Chưa có Veo.**
- `runner/flow.py`: `FlowImageSession` (Selenium, profile riêng `data/profiles/google-flow`) - hiện chỉ để TẠO ẢNH
  nhân vật; đã có tìm ô prompt, bấm "Dự án mới", phát hiện đăng nhập, `flow-debug.txt`.
- `runner/workflows.py` + `runner/autorun.py` + `runner/claude_bridge.py`: workflow cắm ngoài ở
  `<HanaMedia>/workflows/<slug>/` (workflow.json + skills/), chạy `claude -p` trong thư mục HanaMedia với tool giới hạn
  (python/ffmpeg…), tiến độ ở `output/_status/<slug>.json`; cầu nối `hanamedia_<tên>.json` chỉ nhận prompt + duration.
- `runner/production_sounds.py` + bảng `production_sounds`: thư viện SFX (name, tags, filename, duration).
- Đã đăng ký sẵn (tạm thời, không sửa mã tool) workflow `C:\Users\Admin\HanaMedia\workflows\hana-short\` gọi CLI HMS.

## 3. Việc cần làm trong tool
### 3.1 Màn hình "Short hàng loạt"
- Chọn nhiều cặp Nhân vật × Sản phẩm từ thư viện (lọc `category`), số video mỗi cặp (1–20), ghi chú bối cảnh,
  brief tuỳ chọn (ô text hoặc file). Nhập/xuất CSV `nhan_vat,san_pham,so_video,ghi_chu,brief`.
- Mỗi video = một "Short job" có `job_id`, 3 `scene_id` (s1_hook, s2_demo, s3_cta); lưu bảng mới (vd `shorts`,
  `short_scenes`) hoặc mở rộng `projects/scenes/jobs` - giữ được trạng thái từng cảnh để tiếp tục khi tắt app.
- Video cùng sản phẩm trong một lô phải khác góc (truyền tiêu đề/lời thoại các video trước vào lời gọi Claude).

### 3.2 Kịch bản (Claude)
- Port skill `HMS/.claude/skills/hana-short/SKILL.md` vào bộ kỹ năng của tool. Gọi Claude qua cơ chế sẵn có
  (`agent_cli` / `claude -p`) cho từng job; Claude ghi `script.json` rồi tool kiểm tra bằng logic
  `HMS/hana/jobs.py::validate_script` (9:16, đúng 8 s, ≤ 20 từ, ≥ 2 tiêu chí duyệt, `scene_id` hợp lệ, claims nguyên văn
  trong brief; không brief → claims rỗng và lời thoại không có chữ số). Schema script: xem SKILL.md mục 1.

### 3.3 Động cơ Veo trên Flow (mới)
Thêm `generation_mode`/engine "veo-flow" cho Studio, chạy bằng Selenium với profile `google-flow` của tool (mở rộng
`runner/flow.py`). Port từ `HMS/extension/content.js` + `background.js`. Giao diện Flow đã kiểm tra ngày 18/11/2026
(tiếng Việt):
- Ô prompt: `flow-prompt-box [role=textbox].ProseMirror` (aria "Bạn muốn tạo những gì?"); nhập bằng
  `execCommand('insertText')`, kiểm tra lại nội dung sau khi nhập.
- Nút Tạo: `flow-generate-icon-button button` (aria "Bắt đầu tạo").
- Cài đặt: `button.settings-trigger-button` (aria "Điều kiện kích hoạt cài đặt") → popover `[role=radio][aria-checked]`:
  Video, **Thành phần** (ingredients), **9:16**, **8 giây**, **x1**; mô hình qua nút aria "Chọn nhóm mô hình" →
  `[role=menuitem]`: Omni 1.2 Flash, Omni 1.1 Flash, Veo 3.1 - Lite, Veo 3.1 - Fast, Veo 3.1 - Quality
  (Fast/Quality/Omni 1.1 báo ngừng 5/12 → mô hình phải là cấu hình). Popover có dòng "Quá trình tạo sẽ tốn N tín dụng" → ghi lại.
- Ảnh tham chiếu: nút aria "Thêm thành phần vào ô nhập câu lệnh" → "Tải nội dung nghe nhìn lên" tạo
  `input[type=file]` (accept png/jpg/webp/…, multiple). Với Selenium dùng `send_keys(path)` vào input đó (hoặc chặn
  `click()` của input như HMS). Xác nhận ảnh đã thành chip trong ô prompt trước khi tạo.
- Kết quả: `flow-grid-tile-container[role=group][aria-label]` → `[data-media-id]`; menu "Tuỳ chọn khác" →
  "Tải xuống" → menu con độ phân giải ("Kích thước gốc" / "Đã tăng độ phân giải").
- Trang có reCAPTCHA: thấy thử thách → DỪNG, báo người dùng. **Không giải/vượt CAPTCHA.**
- CHƯA kiểm tra trên lần tạo thật: hiển thị % tiến độ của ô đang tạo, ảnh upload có tự gắn chip không, các lựa chọn
  tải video. Viết sao cho sai giả định thì dừng an toàn TRƯỚC khi bấm Tạo (trạng thái `ui_changed`).

**Chống gửi trùng (bắt buộc, port `HMS/hana/flow.py`)**: READY → CLAIMED (đang điền form, chưa gửi) → **SUBMITTING được
ghi DB TRƯỚC khi bấm Tạo** → GENERATING khi thấy ô kết quả mới (bằng chứng) → DOWNLOADED. Mất bằng chứng / app tắt giữa
chừng ở SUBMITTING → `BLOCKED unknown_submit`, KHÔNG tự gửi lại; người dùng chọn "chờ tải" hoặc "tạo lại". Chặn toàn
cục (đăng nhập / hết tín dụng / CAPTCHA / UI đổi) dừng mọi cảnh tới khi người dùng gỡ chặn. Dùng được `accounts`,
`limited_until` sẵn có nếu muốn nhiều tài khoản Google.

### 3.4 Duyệt clip
Port `HMS/hana/review.py` + `media.py::storyboard`: FFmpeg `fps=1,scale=270:-2,tile=4x2` từ 0,5 s (1 khung/giây,
mốc giây lưu kèm vì FFmpeg của tool có thể thiếu drawtext); kiểm tra kỹ thuật (9:16 ±2 %, ≥ 85 % thời lượng, có âm
thanh, so lời thoại với transcript nếu có - difflib ≥ 0,6). Claude xem storyboard + ảnh gốc → verdict kèm lý do theo
khung. PASS bị chặn nếu kỹ thuật lỗi; RETRY quá `max_retries_per_scene` (mặc định 2) → REVIEW_REQUIRED; RETRY mang
`fix_hint` vào prompt lần sau; cảnh đã PASS giữ nguyên.

### 3.5 Dựng
Port `HMS/hana/timeline.py`: `timeline.json` (thứ tự, in/out, âm lượng, mốc SFX khớp tag với `production_sounds` hoặc
thư mục SFX), render FFmpeg 1080×1920 30fps (encoder: libx264 nếu có, không thì h264_mf/nvenc), verify giải mã + 9:16;
tự thêm whoosh chuyển cảnh (cấu hình). Có thể tận dụng `batch_options.stitch` sẵn có.

CapCut (tuỳ chọn, port `HMS/hana/capcut.py`): CapCut 9.8 dùng Qt/QML, **UI Automation không thấy control nào** → không
bấm theo tên được. Cách đã chạy thật: tạo draft JSON (`draft_content.json` + `draft_meta_info.json`, micro-giây) từ
template lấy (chỉ đọc) từ một draft có video+audio của người dùng → CapCut tự nhận draft; người dùng mở draft; chỉ khi
thư mục draft có `.locked` mới gửi Ctrl+E rồi Enter → CapCut xuất vào **Tải xuống** → tool phát hiện file mới, verify.
**Bẫy đã gặp:** `track.attribute = 1` là track **tắt tiếng** → luôn đặt `attribute = 0` và `segment.track_attribute = 0`,
nếu không SFX biến mất khi xuất. Không bấm nút "Chia sẻ" TikTok/YouTube sau khi xuất.

### 3.6 Tiến độ & thư mục
- Trạng thái từng job/cảnh trong UI (Studio), nút: Tạo lại cảnh, Chờ tải, Duyệt tay, Nhập clip tay, Gỡ chặn Flow.
- Thành phẩm: `<export_dir>/<tên lô>/<job_id>.mp4`; gói dựng (clip + SFX + timeline + HUONG-DAN-DUNG.txt) để dựng tay.
- Ghi `output/_status/hana-short.json` cho Không gian Agent.

## 4. Quy tắc bắt buộc
- Không bao giờ gửi một cảnh lên Flow hai lần khi chưa chắc lần trước thất bại.
- Không giải CAPTCHA, không vượt chặn bot, không tự đăng nhập; gặp yêu cầu đăng nhập/hết lượt → dừng và báo rõ.
- Chỉ đọc draft CapCut có sẵn của người dùng; chỉ tạo thư mục draft mới, không ghi đè.
- Không nêu tuyên bố sản phẩm ngoài brief; không có brief → không tuyên bố, không con số.
- Đánh dấu rõ mọi clip mô phỏng (testsrc2) - không trình bày như video thật.

## 5. Kiểm thử & nghiệm thu
- Port bộ test `HMS/tests/` (unittest, FFmpeg thật, không cần Chrome): kiểm tra script/claims, máy trạng thái chống gửi
  trùng, chặn toàn cục, storyboard, giới hạn retry, chỉ tạo lại cảnh lỗi, timeline + SFX, render 9:16, draft CapCut
  (attribute = 0), lô từ thư viện (DB mở read-only), không brief → chặn claims/con số. HMS hiện: 10/10 đạt.
- Nghiệm thu trên máy thật theo thứ tự: (1) 1 cảnh Veo thật qua Flow tải về đúng 9:16 8 s; (2) đưa clip + 1 SFX vào
  CapCut, xuất, đo SFX có trong file; (3) 1 Short đủ 3 cảnh từ thư viện; (4) lô 3 video cùng sản phẩm, 3 góc khác nhau.
  Báo rõ phần nào chạy thật, phần nào mô phỏng, phần nào người dùng phải thao tác.

## 6. File tham khảo trong HMS
`hana/jobs.py` (job, trạng thái, validate), `hana/flow.py` (chống gửi trùng), `extension/content.js` (bộ chọn Flow),
`extension/background.js` (trình tự điền form → pre_submit → bấm Tạo → xác nhận → theo dõi → tải),
`hana/review.py`, `hana/media.py`, `hana/timeline.py`, `hana/capcut.py`, `hana/library.py` (đọc thư viện tool),
`hana/batch.py` (lô + autopilot), `.claude/skills/hana-short/SKILL.md`, `docs/GHI-CHU-TICH-HOP.md`, `tests/`.
