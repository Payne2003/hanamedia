"""Stand-in for `claude -p` in tests: performs the step the autopilot asked for, through the real CLI."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
prompt = sys.argv[sys.argv.index("-p") + 1]
job = os.environ["HANA_JOB"]
cli = [sys.executable, str(ROOT / "hana_cli.py")]
print("FAKE CLAUDE", job, prompt.splitlines()[0])

if "Bước 1" in prompt:
    brief = (ROOT / "jobs").parent  # unused; brief claims come from the test fixture
    script = {"title": f"Fake {job}", "aspect_ratio": "9:16", "language": "Vietnamese",
              "character_bible": "test character", "scenes": [
                  {"scene_id": f"s{i}_{r}", "role": r, "duration": 8, "dialogue": "Câu thử ngắn.",
                   "veo_prompt": "test prompt", "reference_images": ["character", "product"],
                   "claims": ["Giữ nóng 12 giờ"], "review_criteria": ["a", "b"],
                   "sfx_cues": [{"at": 0.4, "tag": "whoosh"}]} for i, r in enumerate(("hook", "demo", "cta"), 1)]}
    jobs_dir = Path(os.environ["HANA_JOBS_DIR"])
    p = jobs_dir / job / "script.json"
    p.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    subprocess.run(cli + ["set-script", "--job", job, "--script", str(p)], check=True)
elif "Bước 4" in prompt:
    out = subprocess.run(cli + ["review-prep", "--job", job], check=True, capture_output=True, text=True,
                         encoding="utf-8")
    for pk in json.loads(out.stdout):
        verdict = "PASS" if pk["technical_ok"] else "RETRY"
        subprocess.run(cli + ["review-set", "--job", job, "--scene", pk["scene_id"], "--verdict", verdict,
                              "--reason", "fake review"], check=True)
