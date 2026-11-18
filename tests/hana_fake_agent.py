"""Thay Claude / Codex trong test: làm đúng bước tool yêu cầu (đọc prompt ở tham số cuối, chạy trong thư mục job)."""
import json
import sys
from pathlib import Path

prompt = sys.argv[-1]
here = Path.cwd()
print("FAKE AGENT", here.name, prompt.splitlines()[0])
if "biên kịch" in prompt:
    no_brief = "KHÔNG CÓ BRIEF" in prompt
    scenes = []
    for i, role in enumerate(("hook", "demo", "cta"), 1):
        scenes.append({"scene_id": f"s{i}_{role}", "role": role, "duration": 8,
                       "dialogue": "Nhìn xinh quá, thử ngay hôm nay!" if no_brief else "Câu thử ngắn.",
                       "veo_prompt": "test prompt", "reference_images": ["character", "product"],
                       "claims": [] if no_brief else ["Giữ nóng 12 giờ"], "review_criteria": ["a", "b"],
                       "sfx_cues": [{"at": 0.4, "tag": "whoosh"}]})
    if "BROKEN-ONCE" in prompt and "bị tool từ chối" not in prompt:
        scenes[0]["dialogue"] = " ".join(["từ"] * 30)  # lần đầu sai → tool gửi lại kèm lỗi
    (here / "script.json").write_text(json.dumps({"title": f"Fake {here.name}", "aspect_ratio": "9:16",
                                                  "character_bible": "test character", "scenes": scenes},
                                                 ensure_ascii=False), encoding="utf-8")
elif "duyệt clip" in prompt:
    out = []
    for packet in sorted(here.glob("scenes/*/attempt_*/review_packet.json")):
        pk = json.loads(packet.read_text(encoding="utf-8"))
        out.append({"scene_id": pk["scene_id"], "verdict": "PASS" if pk["technical_ok"] else "RETRY",
                    "reasons": ["fake review"], "fix_hint": "keep vertical 9:16"})
    (here / "review_result.json").write_text(json.dumps(out), encoding="utf-8")
