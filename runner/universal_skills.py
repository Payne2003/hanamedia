"""Kỹ năng gốc của workflow "Universal" (chỉ máy Super Admin): 8 module dùng cho mọi thể loại video.

Tool chép vào workflows/universal/skills/ lần đầu; sau đó bản trên máy là bản chính. Khi người dùng báo lỗi video,
AI sửa chính các SKILL.md này và ghi bài học vào workflows/universal/lessons.md (chung cho mọi dự án).

Module 07 (Multi-Engine Video Renderer) chính là tool HanaMedia: đưa prompt vào hàng đợi Studio và tạo video trên Muse.
"""

SLUG = "universal"
CONDUCTOR = "universal-video-workflow"
ANALYZER = "universal-video-analyzer"
STYLE = "dynamic-style-master"
PLANNER = "scene-script-planner"
ASSETS = "universal-asset-master"
ROUTER = "scene-render-router"
PROMPTS = "universal-prompt-generator"
QC = "adaptive-quality-control"
LESSONS = "workflows/universal/lessons.md"

# Bản cũ (chưa sửa tay) của các kỹ năng đã nâng cấp: sha256 16 ký tự đầu. File trên máy trùng bản cũ thì tool thay bằng bản mới.
UPGRADES = {
    f"{CONDUCTOR}/SKILL.md": ("1ace46fa19b76644", "c55b238635204502"),
    f"{ASSETS}/SKILL.md": ("63dbcef80ac2238b",),
}
REFERENCE_RULE = (
    "- ẢNH THAM CHIẾU (bắt buộc): với MỌI mục trong characters[] (nhân vật, đồ vật, bối cảnh cần nhất quán) tạo một ảnh nền "
    "trơn bằng công cụ tạo ảnh bạn có, lưu output/<ten-ngan>/anh-tham-chieu/<CODE>.png (tên file = đúng code) và ghi đường dẫn "
    "tương đối vào characters[].image trong hanamedia_<ten-ngan>.json. Tool HanaMedia tự lấy ảnh, duyệt làm ảnh Master và gắn vào "
    "mọi cảnh có nhân vật đó. Không tạo được ảnh thì vẫn ghi image_prompt cho từng mục.")

WORKFLOW = {
    "name": "Universal", "builtin": True,
    "command": f"Chạy {CONDUCTOR}",
    "description": "Video tham chiếu + kịch bản/chủ đề → phân tích → khóa phong cách → kịch bản cảnh → nhân vật & asset → "
                   "prompt → Muse → QC. Báo lỗi video thì AI tự nâng cấp kỹ năng.",
    "agents": {
        "conductor": {"name": "Master Agent", "role": "Điều phối 8 module", "skill": CONDUCTOR},
        "analyst": {"name": "Agent 1", "role": "Phân tích video & phong cách", "skill": ANALYZER},
        "writer": {"name": "Agent 2", "role": "Prompt từng cảnh", "skill": PROMPTS},
        "studio": {"name": "Agent 3", "role": "Kiểm soát chất lượng", "skill": QC},
    },
}

LESSONS_TEMPLATE = """# Bài học lỗi video — chung cho mọi dự án

Mỗi lần người dùng báo lỗi một video, AI chẩn đoán module gây lỗi, sửa kỹ năng liên quan rồi thêm MỘT dòng vào mục
«Luật hiện hành» theo mẫu:

`- [YYYY-MM-DD] [module] Triệu chứng: … | Nguyên nhân: … | Luật: …`

Mọi kỹ năng của workflow Universal đọc file này ở đầu mỗi lần chạy và PHẢI tuân theo các luật bên dưới.
Luật nào trùng ý thì gộp lại, không để file dài vô tận (tối đa khoảng 60 dòng).

## Luật hiện hành

(chưa có bài học nào)
"""

CONDUCTOR_SKILL = f'''---
name: "{CONDUCTOR}"
description: "Điều phối workflow Universal: video tham chiếu + kịch bản/chủ đề trong input/ → 8 module → hanamedia_<ten>.json cho tool HanaMedia (Studio · Muse)."
---
# Universal — điều phối 8 module

Đầu vào (thư mục HanaMedia):
- `input/<ten-ngan>.mp4` video tham chiếu (có thể không có nếu chỉ có chủ đề trong brief).
- `input/<ten-ngan>_pack/` (nếu có): `shots.txt`, `sheets/*.jpg` + `sheets.txt`, `motion.txt`. Dùng luôn, xem ĐỦ mọi tờ ảnh.
- **Nhiều video trong `input/`:** nếu người dùng nêu tên dự án (vd «cho dự án X») thì CHỈ làm `input/X.*` + `input/X_pack/`
  và ghi kết quả vào `output/X/`; tuyệt đối không đụng các video / dự án khác trong `input/`.
- `workflows/universal/brief.json`: `topic` (chủ đề/kịch bản), `genre`, `language`, `note`.
- **`{LESSONS}`: ĐỌC TRƯỚC TIÊN.** Đây là các luật rút ra từ lỗi video đã gặp; mọi module phải tuân theo.

## Chuỗi module
1. **{ANALYZER}** (Module 01) → `output/<ten-ngan>/phan-tich_<ten-ngan>.md`: timeline, cảnh, chuyển động, nguồn bằng chứng.
2. **{STYLE}** (Module 02) → `style-master.json`: phong cách đã khóa + luật cấm + độ tin cậy.
3. **{PLANNER}** (Module 03) → `kich-ban-canh.json`: danh sách cảnh có mã SCENE_001…, mục đích, số giây.
4. **{ASSETS}** (Module 04) → `nhan-vat.json`: nhân vật / đồ vật / bối cảnh có mã cố định + cảnh nào dùng nhân vật nào.
5. **{ROUTER}** (Module 05) → gắn nhãn từng cảnh: dễ / có nhân vật cần ảnh tham chiếu / cảnh khó.
6. **{PROMPTS}** (Module 06) → prompt từng cảnh đã kiểm tra.
7. **Module 07 — Renderer = tool HanaMedia**: tool tự đưa prompt vào Studio và tạo video trên Muse. Không tự gọi Muse.
8. **{QC}** (Module 08) → `bao-cao-qc.md` khi có video để kiểm (người dùng gửi lại), hoặc kiểm prompt trước khi xuất.

## Kết quả cho tool (bắt buộc)
Ghi `output/<ten-ngan>/hanamedia_<ten-ngan>.json`:
```json
{{
  "video": "<ten-ngan>.mp4", "duration": 120.5, "character": "tên chính (tiếng Việt)",
  "style": "một câu tóm tắt phong cách đã khóa",
  "characters": [{{"code": "LEOPARD_MOTHER", "name": "Báo mẹ", "description": "ngoại hình CỐ ĐỊNH, đủ để vẽ lại",
                   "image_prompt": "prompt tạo ảnh tham chiếu, nền trơn", "image": "anh-tham-chieu/LEOPARD_MOTHER.png"}}],
  "scenes": [{{"scene": 1, "duration": 8, "prompt": "…", "characters": ["LEOPARD_MOTHER"], "route": "character|easy|hard"}}]
}}
```
- `characters[].code`: IN HOA, dấu gạch dưới, duy nhất, dùng đúng mã này trong prompt và trong `scenes[].characters`.
- `characters[]` gồm MỌI thứ cần giữ nhất quán bằng ảnh tham chiếu: nhân vật, đồ vật quan trọng, bối cảnh đặc trưng.
- **Ảnh tham chiếu (BẮT BUỘC làm):** tạo cho mỗi mục một ảnh (nền trơn, thấy rõ toàn thân / toàn cảnh, đúng `description`)
  bằng công cụ tạo ảnh mà bạn có (Codex / Claude / MCP tạo ảnh). Lưu vào `output/<ten-ngan>/anh-tham-chieu/<CODE>.png`
  (tên file = đúng `code`) và ghi đường dẫn tương đối vào `characters[].image`. **Tool HanaMedia tự lấy các ảnh này,
  duyệt làm ảnh Master rồi gắn vào mọi cảnh có nhân vật đó** — bạn không cần làm gì thêm. Không tạo được ảnh thì vẫn ghi
  `image_prompt`: tool hiện nút «Chép prompt ảnh» và «Nhập ảnh từ thư mục» để người dùng tự tạo.
- Cảnh nào có nhân vật xuất hiện thì BẮT BUỘC liệt kê mã trong `scenes[].characters` — tool dựa vào đây để báo bạn cần
  ảnh tham chiếu nào và CHẶN chạy cảnh nếu nhân vật chưa có ảnh.
- Ghi tiến độ vào `output/_status/universal.json` mỗi khi chuyển module.
- Không hỏi lại người dùng giữa chừng. Không sửa / xóa video trong `input/`.
'''

ANALYZER_SKILL = f'''---
name: "{ANALYZER}"
description: "Module 01 — Phân tích video tham chiếu bất kỳ thể loại: timeline, cảnh, chuyển động, nhân vật, nguồn bằng chứng."
---
# Module 01 — Universal Video Analyzer

Đọc `{LESSONS}` trước. Quy tắc bằng chứng: mỗi nhận định phải ghi nguồn (khung ảnh / mốc giây). Không thấy rõ thì ghi
**"chưa xác minh"**, không bịa.

Với mỗi cảnh ghi: số cảnh, giây đầu–cuối, cỡ cảnh, chuyển động máy, chuyển động chủ thể, bối cảnh, ánh sáng, nhân vật
xuất hiện (mô tả ngoại hình đủ để nhận lại), âm thanh nghe được, **độ tin cậy** (cao / vừa / thấp).
Dùng `input/<ten>_pack/` của tool nếu có (xem đủ mọi tờ ảnh); không có thì tự dò điểm cắt bằng ffmpeg.
Ghi `output/<ten>/phan-tich_<ten>.md` (bảng cảnh) và danh sách nhân vật riêng cuối file.
'''

STYLE_SKILL = f'''---
name: "{STYLE}"
description: "Module 02 — Khóa phong cách từ bản phân tích: Style Master + luật asset + luật cấm + độ tin cậy."
---
# Module 02 — Dynamic Style Master

Đọc `{LESSONS}`. Từ bản phân tích, rút ra phong cách bằng chứng-đã-xác-minh rồi KHÓA lại trong `style-master.json`:
- `style`: màu, ánh sáng, độ thực, nhịp, kiểu máy quay, âm thanh — mỗi mục kèm `confidence` và `evidence` (cảnh nào).
- `asset_rules`: nhân vật / đồ vật phải giữ gì cố định xuyên suốt.
- `negative_rules`: những thứ CẤM xuất hiện (gộp từ phân tích + các luật trong lessons.md).
Mục nào `confidence` thấp thì ghi rõ để QC kiểm kỹ, không khẳng định như thật.
'''

PLANNER_SKILL = f'''---
name: "{PLANNER}"
description: "Module 03 — Lập danh sách cảnh có mã SCENE_001…, mục đích, số giây từ kịch bản/chủ đề + phong cách đã khóa."
---
# Module 03 — Intelligent Script & Scene Planner

Đọc `{LESSONS}`, `style-master.json` và brief. Lập `kich-ban-canh.json`: mỗi cảnh có `scene` (số), `code` (SCENE_001…),
`purpose` (cảnh làm gì trong mạch kể), `duration` (giây, **tối đa 10** vì Muse giới hạn), `beats` (diễn biến theo mốc giây).
Cảnh nối nhau phải liền mạch: ghi `opens_on` (khung đầu) và `ends_with` (khung cuối) để cảnh sau nhận đúng.
Tổng thời lượng bám sát video tham chiếu hoặc yêu cầu trong brief.
'''

ASSETS_SKILL = f'''---
name: "{ASSETS}"
description: "Module 04 — Nhân vật, đồ vật, bối cảnh có mã cố định; cảnh nào dùng nhân vật nào (để tool báo ảnh tham chiếu cần có)."
---
# Module 04 — Universal Asset Master

Đọc `{LESSONS}`. Lập `nhan-vat.json`:
- `characters[]`: `code` (IN HOA_GẠCH_DƯỚI, duy nhất), `name`, `description` (ngoại hình CỐ ĐỊNH: màu, kích cỡ, dấu hiệu
  nhận dạng — đủ để một họa sĩ vẽ lại giống hệt), `image_prompt` (prompt tạo ảnh tham chiếu chuẩn, nền trơn).
- `props[]` và `locations[]` tương tự nếu cần giữ nhất quán.
- `scene_map`: với MỖI cảnh, danh sách `code` nhân vật xuất hiện (chỉ nhân vật có mặt trong khung).
**Ảnh tham chiếu:** tạo ảnh cho từng mục trong `characters[]` / `props[]` / `locations[]` bằng công cụ tạo ảnh bạn có, lưu
`output/<ten-ngan>/anh-tham-chieu/<CODE>.png` (tên file = `code`), ghi `image` (đường dẫn tương đối) vào `hanamedia_<ten>.json`.
Tool HanaMedia tự lấy ảnh, duyệt làm Master và gắn vào mọi cảnh có nhân vật đó. Không tạo được ảnh thì giữ `image_prompt`.
Số nhân vật mỗi cảnh phải khớp đúng số nhân vật thấy trong cảnh (lỗi hay gặp: thừa / thiếu con vật hoặc người).
'''

ROUTER_SKILL = f'''---
name: "{ROUTER}"
description: "Module 05 — Phân loại từng cảnh để chọn cách tạo: dễ / có nhân vật cần ảnh tham chiếu / cảnh khó."
---
# Module 05 — Scene Classification & Render Router

Đọc `{LESSONS}`. Với mỗi cảnh gán `route`:
- `character`: có nhân vật trong `scene_map` → tool sẽ gắn ảnh tham chiếu (chế độ Ảnh → video) và chặn cảnh nếu thiếu ảnh.
- `easy`: không nhân vật cố định (phong cảnh, đồ vật, chuyển cảnh) → Prompt → video.
- `hard`: nhiều nhân vật tương tác, chuyển động nhanh, cảnh có tay / mặt gần, cảnh trùng lỗi trong lessons.md →
  đánh dấu `difficult` để chạy thử trước và QC kỹ.
Ghi `route` + lý do một câu vào từng cảnh.
'''

PROMPTS_SKILL = f'''---
name: "{PROMPTS}"
description: "Module 06 — Sinh prompt tiếng Anh từng cảnh cho Meta Muse, kiểm tra theo style-master và lessons.md rồi mới xuất."
---
# Module 06 — AI Asset & Prompt Generator

Đọc `{LESSONS}` và `style-master.json` — **mọi luật trong lessons.md phải được áp vào prompt**.
Mỗi prompt: `SCENE n | Duration X seconds | ENVIRONMENT | CHARACTERS (dùng đúng mã + mô tả CỐ ĐỊNH từ nhan-vat.json) |
ACTION (theo mốc giây) | CAMERA | AUDIO | CONTINUITY (mở bằng … / kết bằng … / bàn giao cho cảnh n+1) | RENDERING RULES`.
Trước khi xuất, tự kiểm từng prompt: đúng số nhân vật? đúng mã/mô tả? không vi phạm `negative_rules`? số giây ≤ 10?
Sai thì sửa ngay, không đẩy sang QC. Prompt đưa vào `scenes[].prompt` của `hanamedia_<ten>.json`.
'''

QC_SKILL = f'''---
name: "{QC}"
description: "Module 08 — Kiểm soát chất lượng: kiểm prompt trước khi xuất và kiểm video khi người dùng gửi lại; phân loại lỗi về đúng module."
---
# Module 08 — Adaptive Quality Control

Đọc `{LESSONS}`. Hai việc:
1. **Kiểm prompt trước khi xuất**: so từng prompt với `style-master.json`, `nhan-vat.json`, `negative_rules`, các luật trong
   lessons.md. Ghi `bao-cao-qc.md`: cảnh nào đạt / cần sửa / lý do.
2. **Kiểm video / xử lý báo lỗi**: khi người dùng báo lỗi một video (yêu cầu mang nhãn "LỖI VIDEO" trong `requests.md`),
   chẩn đoán LỖI DO MODULE NÀO (phân tích sai / phong cách lỏng / thiếu mô tả nhân vật / prompt mơ hồ / Muse sinh sai dù
   prompt đúng) rồi sửa đúng kỹ năng đó và thêm một dòng vào mục «Luật hiện hành» của `{LESSONS}`.
Phân loại lỗi phổ biến: sai số lượng nhân vật · nhân vật đổi ngoại hình · chữ/logo lạ trong hình · chuyển động giật hoặc
chậm · sai hướng chuyển động · cảnh sau không nối cảnh trước · vi phạm nội dung an toàn.
'''


def files():
    """{đường dẫn tương đối trong skills/: nội dung} của bộ kỹ năng gốc."""
    return {
        f"{CONDUCTOR}/SKILL.md": CONDUCTOR_SKILL,
        f"{ANALYZER}/SKILL.md": ANALYZER_SKILL,
        f"{STYLE}/SKILL.md": STYLE_SKILL,
        f"{PLANNER}/SKILL.md": PLANNER_SKILL,
        f"{ASSETS}/SKILL.md": ASSETS_SKILL,
        f"{ROUTER}/SKILL.md": ROUTER_SKILL,
        f"{PROMPTS}/SKILL.md": PROMPTS_SKILL,
        f"{QC}/SKILL.md": QC_SKILL,
    }
