"""Register HanaMedia Short as a workflow of the HanaMedia tool (Không gian Agent).

Writes <HanaMedia>/workflows/hana-short/{workflow.json, skills/hana-short-workflow/SKILL.md,
skills/hana-short/SKILL.md} and creates <HanaMedia>/thu-vien/{brief,lo}/. The tool then lists the workflow, syncs the
skills into <HanaMedia>/.claude/skills and runs it with `claude -p` inside <HanaMedia> (python/ffmpeg allowed).
Nothing in the tool's database or source is changed.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .config import ROOT, resolve_path
from .jobs import write_json_atomic

WORKFLOW = {
    "name": "HanaMedia Short",
    "builtin": False,
    "command": "Chạy hana-short-workflow",
    "description": "Thư viện Nhân vật × Sản phẩm → kịch bản Hook/Demo/CTA → Veo 3 trên Flow → duyệt storyboard → "
                   "dựng 9:16 (FFmpeg + draft CapCut).",
    "agents": {
        "conductor": {"name": "Master Agent", "role": "Điều phối lô Short", "skill": "hana-short-workflow"},
        "analyst": {"name": "Agent 1", "role": "Đọc thư viện + brief", "skill": "hana-short-workflow"},
        "writer": {"name": "Agent 2", "role": "Kịch bản + prompt Veo", "skill": "hana-short"},
        "studio": {"name": "Agent 3", "role": "Duyệt clip + dựng", "skill": "hana-short"},
    },
}

CONDUCTOR = """---
name: hana-short-workflow
description: "Chạy lô YouTube Short quảng cáo của HanaMedia Short: đọc file lô trong thu-vien/lo/, viết kịch bản cho job mới, duyệt clip Veo đã tải về, dựng thành phẩm 9:16. Dùng khi tool gọi 'Chạy hana-short-workflow'."
---

# hana-short-workflow (chạy trong thư mục HanaMedia)

Công cụ: `{PY} "{CLI}" <lệnh>` (chỉ dùng Bash với python). Dữ liệu job nằm ở `{JOBS}` (ngoài thư mục này:
KHÔNG đọc/ghi trực tiếp ở đó - luôn đi qua CLI và các thư mục chép sang bên dưới). Quy tắc viết kịch bản và duyệt:
đọc `.claude/skills/hana-short/SKILL.md` (bản sao skill hana-short).

Ghi tiến độ vào `output/_status/hana-short.json` sau mỗi bước (CLI `batch step` cũng tự ghi).

## 1. Tạo lô
Với mỗi file `.csv`/`.json` trong `thu-vien/lo/` (bỏ qua thư mục con `xong/`):
`{PY} "{CLI}" batch create --file "thu-vien/lo/<file>"` - chạy lại nhiều lần không tạo trùng. Lỗi (thiếu ảnh trong
thư viện) → ghi rõ vào báo cáo, bỏ qua dòng đó. Brief không bắt buộc: sản phẩm chỉ có ảnh thì kịch bản không nêu
tuyên bố/con số nào về sản phẩm (xem skill hana-short).

## 2. Viết kịch bản cho job ở stage `script`
`{PY} "{CLI}" batch status` → với từng job `stage: script`:
1. `{PY} "{CLI}" export-inputs --job <id> --to "output/hana-short/<id>"` → Read ảnh character/product và brief.md
   ở thư mục đó.
2. Viết `output/hana-short/<id>/script.json` theo Bước 1 của skill hana-short (3 cảnh 8 giây Hook → Demo → CTA,
   ≤ 20 từ/cảnh, chỉ tuyên bố có nguyên văn trong brief, prompt Veo tiếng Anh, character_bible, tiêu chí duyệt).
   Các video cùng sản phẩm trong lô phải khác góc (xem `batch` → `variant`, `note`).
3. `{PY} "{CLI}" validate-script --job <id> --script "output/hana-short/<id>/script.json"` → sửa tới khi ok →
   `set-script` cùng tham số.

## 3. Duyệt clip đã tải (cảnh `DOWNLOADED`)
`{PY} "{CLI}" review-prep --job <id> --copy-to "output/hana-short/<id>/review"` → Read từng storyboard và ảnh gốc
đã chép → `{PY} "{CLI}" review-set --job <id> --scene <s> --verdict PASS|RETRY|REVIEW_REQUIRED --reason "..."
[--fix-hint "..."]` theo Bước 4 của skill hana-short.

## 4. Dựng
`{PY} "{CLI}" batch step --no-agents` → job đã duyệt đủ 3 cảnh được dựng FFmpeg 9:16 (đã kiểm tra) + draft CapCut.

## 5. Báo cáo
Liệt kê: job mới, job đang chờ Flow tạo video (người dùng bấm extension HanaMedia Short → "Chạy liên tục"),
job đã xong + đường dẫn MP4, job CẦN NGƯỜI DÙNG (BLOCKED / REVIEW_REQUIRED / thiếu brief). Không hỏi lại giữa chừng.
"""


def install(cfg: dict) -> dict:
    home = resolve_path(cfg.get("hanamedia", {}).get("home"))
    if not home or not (home / "workflows").is_dir():
        raise FileNotFoundError(f"Không thấy thư mục HanaMedia có workflows/: {home}")
    wf = home / "workflows" / "hana-short"
    (wf / "skills" / "hana-short-workflow").mkdir(parents=True, exist_ok=True)
    (wf / "skills" / "hana-short").mkdir(parents=True, exist_ok=True)
    data = dict(WORKFLOW)
    if (wf / "workflow.json").exists():  # keep the user's own edits made in the tool (names/roles)
        try:
            old = json.loads((wf / "workflow.json").read_text(encoding="utf-8"))
            data["agents"] = old.get("agents") or data["agents"]
        except ValueError:
            pass
    write_json_atomic(wf / "workflow.json", data)
    cli = (ROOT / "hana_cli.py").as_posix()
    jobs = resolve_path(cfg["jobs_dir"]).as_posix()
    text = CONDUCTOR.replace("{PY}", "python").replace("{CLI}", cli).replace("{JOBS}", jobs)
    (wf / "skills" / "hana-short-workflow" / "SKILL.md").write_text(text, encoding="utf-8", newline="\n")
    skill = (ROOT / ".claude" / "skills" / "hana-short" / "SKILL.md").read_text(encoding="utf-8")
    skill = skill.replace("`hana.bat ", f"`python \"{cli}\" ").replace("hana.bat ", f"python \"{cli}\" ")
    (wf / "skills" / "hana-short" / "SKILL.md").write_text(skill, encoding="utf-8", newline="\n")
    lib = home / "thu-vien"
    (lib / "brief").mkdir(parents=True, exist_ok=True)
    (lib / "lo").mkdir(parents=True, exist_ok=True)
    sample = lib / "lo" / "MAU-lo-video.csv.txt"
    if not sample.exists():
        sample.write_text("nhan_vat,san_pham,so_video,ghi_chu,brief\n"
                          "Tên ảnh Nhân vật trong thư viện,Tên ảnh Sản phẩm trong thư viện,3,bối cảnh văn phòng buổi sáng,\n",
                          encoding="utf-8-sig")
    readme = lib / "HUONG-DAN.txt"
    readme.write_text(
        "THƯ VIỆN HANAMEDIA SHORT\n\n"
        "1. Trong tool: Thư viện Video & Ảnh → đổi nhóm ảnh thành Nhân vật hoặc Sản phẩm.\n"
        "2. (Không bắt buộc) brief/<tên sản phẩm>.md : tuyên bố được phép dùng (công dụng, thông số, giá, ưu đãi).\n"
        "   Không có brief thì video chỉ giới thiệu sản phẩm theo ảnh, không nêu công dụng/giá/con số.\n"
        "   Tên file = tên ảnh sản phẩm viết không dấu, nối bằng '-' (vd 'HanaCup 500ml' → hanacup-500ml.md).\n"
        "3. lo/<tên lô>.csv : mỗi dòng một cặp nhân vật × sản phẩm (xem MAU-lo-video.csv.txt).\n"
        "4. Chạy workflow 'HanaMedia Short' trong tool (hoặc `hana.bat batch run` trong HanaMediaShort).\n"
        "5. Chrome: extension HanaMedia Short → 'Chạy liên tục' để Flow/Veo tạo các cảnh.\n"
        "Thành phẩm: " + str(resolve_path(cfg["export_dir"])) + "\\<tên lô>\\\n", encoding="utf-8")
    return {"workflow": str(wf), "library": str(lib), "cli": cli, "python": sys.executable}
