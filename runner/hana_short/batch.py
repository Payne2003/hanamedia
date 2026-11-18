"""Batch production: many Shorts from the HanaMedia library, driven by an autopilot loop.

Batch file (CSV or JSON) - one row per character/product pair:
  CSV  : nhan_vat,san_pham,so_video,ghi_chu,brief
         Linh,HanaCup 500ml,3,"buổi sáng văn phòng",
  JSON : {"batch": "sale-1118", "items": [{"nhan_vat": "Linh", "san_pham": "HanaCup 500ml", "so_video": 3,
                                            "ghi_chu": "...", "brief": "optional path"}]}
`nhan_vat` / `san_pham` = display name (with or without extension), slug or media_id of an image in the tool's
library tagged Nhân vật / Sản phẩm. Brief: <HanaMedia>/thu-vien/brief/<tên sản phẩm>.md unless given.

Autopilot (one pass = `step`, loop = `run`):
  script  -> Claude Code (`claude -p`, skill hana-short step 1) writes + validates script.json
  generate-> the Chrome extension (Flow/Veo) picks READY scenes on its own ("Chạy liên tục")
  review  -> Claude Code (skill step 4) reads storyboards and records PASS/RETRY/REVIEW_REQUIRED
  edit    -> timeline + edit package + FFmpeg final (9:16, verified) + CapCut draft for optional touch-up
Progress is mirrored to <HanaMedia>/output/_status/hana-short.json for the tool's Agent space.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import capcut, library, media, timeline
from . import jobs as J
from .config import jobs_dir, resolve_path
from .jobs import Job, JobError, now_iso, read_json, write_json_atomic

MAX_AGENT_TRIES = 2


# ------------------------------------------------------------------------------------------- batch file
def _rows(path: Path) -> tuple[str, list[dict]]:
    if path.suffix.lower() == ".json":
        data = read_json(path)
        return data.get("batch") or path.stem, list(data.get("items") or [])
    with open(path, encoding="utf-8-sig", newline="") as f:
        return path.stem, [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]


def batches_dir(cfg) -> Path:
    d = jobs_dir(cfg) / "_batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create(cfg: dict, file: Path, name: str | None = None, dry_run: bool = False) -> dict:
    batch, rows = _rows(Path(file))
    return create_rows(cfg, rows, name or batch, dry_run, Path(file).read_bytes(), str(file))


def create_rows(cfg: dict, rows: list[dict], name: str, dry_run: bool = False, source: bytes | None = None,
                source_name: str = "") -> dict:
    """Tạo job cho từng dòng {nhan_vat, san_pham, so_video, ghi_chu, brief} (từ màn hình Short hàng loạt hoặc CSV)."""
    batch = library.slug(name)
    if not rows:
        raise JobError("File lô không có dòng nào")
    plan, errors = [], []
    for i, r in enumerate(rows, 1):
        try:
            ch = library.resolve(cfg, "character", r.get("nhan_vat") or r.get("character"))
            pr = library.resolve(cfg, "product", r.get("san_pham") or r.get("product"))
            brief = library.find_brief(cfg, pr, (r.get("brief") or "").strip() or None)
            n = int(r.get("so_video") or r.get("count") or 1)
            if not 1 <= n <= 20:
                raise JobError("so_video phải từ 1 đến 20")
            plan.append({"row": i, "character": ch, "product": pr, "brief": str(brief) if brief else None, "count": n,
                         "note": (r.get("ghi_chu") or r.get("note") or "").strip()})
        except (JobError, ValueError) as e:
            errors.append(f"dòng {i}: {e}")
    if errors:
        raise JobError("File lô có lỗi:\n- " + "\n- ".join(errors))
    manifest_file = batches_dir(cfg) / f"{batch}.json"
    manifest = read_json(manifest_file) if manifest_file.exists() else {"batch": batch, "created_at": now_iso(), "jobs": []}
    import hashlib
    digest = hashlib.sha1(source if source is not None else json.dumps(rows, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()
    if digest in manifest.get("sources", {}):
        # same file content already turned into jobs (e.g. the tool re-ran the workflow): never duplicate
        return {"batch": batch, "new_jobs": [], "total_jobs": len(manifest["jobs"]), "dry_run": dry_run,
                "note": "File lô này đã được tạo trước đó - không tạo job trùng"}
    existing = {j["job_id"] for j in manifest["jobs"]}
    out = []
    seq = len(manifest["jobs"])
    for p in plan:
        for k in range(1, p["count"] + 1):
            seq += 1
            jid = f"{batch}-{seq:02d}-{p['product']['slug'][:20]}"
            if jid in existing:
                continue
            entry = {"job_id": jid, "character": p["character"]["stem"], "character_id": p["character"]["id"],
                     "product": p["product"]["stem"], "product_id": p["product"]["id"], "variant": k,
                     "variants": p["count"], "note": p["note"], "brief": p["brief"]}
            out.append(entry)
            if dry_run:
                continue
            job = Job(cfg, jid)
            job.create(Path(p["character"]["path"]), Path(p["brief"]) if p["brief"] else None,
                       Path(p["product"]["path"]), title=f"{p['character']['stem']} × {p['product']['stem']} #{k}",
                       product_name=p["product"]["stem"])
            with job.edit() as d:
                d["batch"] = {"name": batch, **{k2: v for k2, v in entry.items() if k2 != "job_id"}}
                d["autopilot"] = {"tries": {}, "error": None}
            manifest["jobs"].append(entry)
    if not dry_run:
        manifest.setdefault("sources", {})[digest] = {"file": source_name or "giao diện", "at": now_iso()}
        write_json_atomic(manifest_file, manifest)
    return {"batch": batch, "new_jobs": out, "total_jobs": len(manifest["jobs"]), "dry_run": dry_run}


def batch_jobs(cfg: dict, batch: str | None) -> list[Job]:
    if batch:
        f = batches_dir(cfg) / f"{library.slug(batch)}.json"
        if not f.exists():
            raise JobError(f"Không có lô '{batch}'")
        return [Job(cfg, j["job_id"]) for j in read_json(f)["jobs"]]
    return [Job(cfg, p.parent.name) for p in sorted(jobs_dir(cfg).glob("*/job.json"))
            if (read_json(p).get("batch"))]


# ------------------------------------------------------------------------------------------- agents
SCRIPT_RULES = """Quy tắc kịch bản (skill hana-short, Bước 1):
- 3 cảnh × 8 giây: Hook (2 giây đầu gây tò mò, nêu vấn đề) → Demo (sản phẩm đang được dùng, thấy rõ) → CTA (lời kêu gọi,
  nhìn thẳng camera). scene_id: s1_hook, s2_demo, s3_cta.
- Lời thoại tiếng Việt ≤ 20 từ mỗi cảnh, một ý, câu ngắn. Không có chữ trên màn hình.
- Chỉ dùng tuyên bố có NGUYÊN VĂN trong brief, chép vào `claims`. Không "số 1", "tốt nhất", công dụng y tế, so sánh đối thủ,
  không bịa giá / khuyến mãi.
- veo_prompt tiếng Anh 60–120 từ: cỡ cảnh + góc máy + chuyển động máy; lặp lại character_bible rút gọn; sản phẩm theo
  product_bible; hành động chính; bối cảnh, ánh sáng; âm thanh nền; "vertical 9:16 composition, subject centered in frame".
  KHÔNG viết lời thoại trong veo_prompt (tool tự thêm). Không yêu cầu chữ, logo vẽ tay, watermark.
- review_criteria: 2–4 tiêu chí riêng cho cảnh, nhìn được trên storyboard (vd "Bình bạc nằm trong tay phải ở khung 2–6").
- sfx_cues.tag là từ khoá: whoosh, pop, ding, impact, riser, cash, camera, sparkle; at trong 0..8 giây.
- reference_images: "character" và/hoặc "product".
Schema script.json:
{"title": "…", "aspect_ratio": "9:16", "language": "Vietnamese",
 "character_bible": "mô tả tiếng Anh cố định của nhân vật: tuổi, giới, tóc, khuôn mặt, trang phục, phụ kiện, giọng nói",
 "product_bible": "mô tả tiếng Anh hình dạng / màu / nhãn sản phẩm đúng như ảnh",
 "scenes": [{"scene_id": "s1_hook", "role": "hook", "duration": 8, "dialogue": "…", "veo_prompt": "…", "action": "…",
             "reference_images": ["character", "product"], "claims": ["…"], "review_criteria": ["…", "…"],
             "sfx_cues": [{"at": 0.2, "tag": "whoosh"}]}, …]}"""

NO_BRIEF_RULE = ("KHÔNG CÓ BRIEF: chỉ dựa vào ảnh sản phẩm; claims = []; lời thoại KHÔNG nêu công dụng, thông số, chất liệu, "
                 "giá, ưu đãi hay bất kỳ con số / chữ số nào - chỉ cảm xúc, cách dùng nhìn thấy được, vẻ ngoài sản phẩm và "
                 "lời kêu gọi chung (vd \"Thử ngay hôm nay\").")

REVIEW_RULES = """Quy tắc duyệt (skill hana-short, Bước 4) - với từng cảnh:
1. Xem file storyboard (lưới ảnh, đọc trái→phải, trên→dưới; mốc giây của từng khung có trong danh sách frames).
2. Đối chiếu technical_checks (9:16, thời lượng, âm thanh, khớp lời thoại nếu có transcript).
3. So với ảnh gốc inputs/character.* và inputs/product.*: nhân vật nhất quán, sản phẩm đúng, bố cục dọc, hành động chính,
   từng tiêu chí (criteria) và visual_checklist.
4. Verdict: PASS (đạt) | RETRY (lỗi sửa được bằng prompt - kèm fix_hint tiếng Anh nói rõ cần sửa) | REVIEW_REQUIRED (không
   chắc, sai sản phẩm, sai tuyên bố, khuôn mặt khác, hoặc lỗi nhạy cảm). Lý do luôn cụ thể theo khung / mốc giây.
   technical_ok = false thì không được PASS. Không có transcript → ghi "chưa đối chiếu lời thoại bằng transcript"."""


def agent_engine(cfg: dict) -> str | None:
    if os.environ.get("HANA_AGENT_CMD"):
        return "test"
    from ..agent_cli import pick
    return pick(cfg.get("autopilot", {}).get("engine") or "auto")


def run_agent(cfg: dict, job: Job, step: str, prompt: str, images: list[str] | None = None) -> dict:
    """Gọi Claude Code / Codex (đã đăng nhập trên máy) chạy trong thư mục job: chỉ đọc ảnh và ghi file JSON."""
    from ..agent_cli import find
    engine = agent_engine(cfg)
    if not engine:
        raise JobError("Máy chưa có Claude Code hay Codex (Không gian Agent → Cài đặt workflow → Kiểm tra máy)")
    model = cfg.get("autopilot", {}).get("model")
    stdin = None
    if engine == "test":  # bộ test: chương trình giả thay Claude
        cmd = json.loads(os.environ["HANA_AGENT_CMD"]) + [prompt]
    elif engine == "claude":
        cmd = [find("claude"), "-p", prompt, "--permission-mode", "acceptEdits",
               "--allowedTools", "Read,Write,Edit,Glob,Grep"] + (["--model", model] if model else [])
    else:
        cmd = [find("codex"), "exec", "-s", "workspace-write", "--skip-git-repo-check", "-C", str(job.dir)]
        for image in images or []:
            cmd += ["-i", image]
        cmd += (["-m", model] if model else []) + ["-"]
        stdin = prompt
    log = job.dir / f"autopilot_{step}_{time.strftime('%Y%m%d-%H%M%S')}.log"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HANA_JOB": job.id}
    t0 = time.time()
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        try:
            proc = subprocess.run(cmd, cwd=str(job.dir), stdout=f, stderr=subprocess.STDOUT, env=env, input=stdin,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=int(cfg.get("autopilot", {}).get("agent_timeout_sec", 1800)),
                                  creationflags=media.CREATE_NO_WINDOW)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = -9
            f.write("\n# Quá thời gian, đã dừng\n")
    job.log("autopilot_agent", step=step, engine=engine, code=code, seconds=round(time.time() - t0), log=str(log))
    return {"code": code, "log": str(log)}


def _siblings(cfg: dict, job: Job, data: dict) -> list[str]:
    """Titles/angles already written for the same product in this batch -> ask for a different angle."""
    out = []
    b = data.get("batch") or {}
    for other in batch_jobs(cfg, b.get("name")):
        if other.id == job.id or not (other.dir / "script.json").exists():
            continue
        try:
            od = other.load()
            if (od.get("batch") or {}).get("product_id") == b.get("product_id"):
                s = other.script()
                out.append(f"{s.get('title')}: " + " / ".join(x.get("dialogue", "")[:60] for x in s["scenes"]))
        except (JobError, OSError, ValueError, KeyError):
            continue
    return out


def _inputs(data: dict) -> dict:
    return {k: Path(v).name for k, v in data["inputs"].items() if k in ("character", "product", "brief") and v}


def script_prompt(cfg, job: Job, data: dict, errors: list[str] | None = None) -> str:
    b = data.get("batch") or {}
    sib = _siblings(cfg, job, data)
    files = _inputs(data)
    return "\n".join([
        f"Bạn là biên kịch quảng cáo YouTube Short (skill hana-short). Thư mục hiện tại là job {job.id}.",
        f"Đầu vào: inputs/{files.get('character')} (ảnh nhân vật)"
        + (f", inputs/{files['product']} (ảnh sản phẩm)" if files.get("product") else "")
        + f", inputs/{files.get('brief')} (brief). Xem kỹ cả hai ảnh và đọc brief.",
        f"Nhân vật: {b.get('character') or '?'}; sản phẩm: {b.get('product') or data['inputs'].get('product_name') or '?'}.",
        "" if data["inputs"].get("brief_provided", True) else NO_BRIEF_RULE,
        f"Ghi chú của lô: {b.get('note') or '(không có)'}. Đây là video {b.get('variant', 1)}/{b.get('variants', 1)} cho sản phẩm này.",
        ("Các video khác cùng sản phẩm đã dùng góc sau - chọn hook / bối cảnh / lời thoại KHÁC hẳn:\n- " + "\n- ".join(sib))
        if sib else "Chọn một góc quảng cáo rõ ràng, tự nhiên.",
        SCRIPT_RULES,
        ("Lần trước script.json bị tool từ chối vì:\n- " + "\n- ".join(errors) + "\nSửa đúng các lỗi này.") if errors else "",
        "Ghi kết quả vào file script.json trong thư mục hiện tại (JSON hợp lệ, UTF-8). Không chạy lệnh nào, không sửa file "
        "khác, không hỏi lại người dùng.",
    ])


def review_prompt(job: Job, packets: list[dict]) -> str:
    rows = []
    for p in packets:
        rel = Path(p["storyboard"]).relative_to(job.dir).as_posix()
        rows.append({"scene_id": p["scene_id"], "attempt": p["attempt"], "storyboard": rel, "frames": p["grid"]["frames"],
                     "technical_ok": p["technical_ok"], "technical_checks": p["technical_checks"],
                     "criteria": p["criteria"], "visual_checklist": p["visual_checklist"], "dialogue": p["dialogue"],
                     "attempts_used": p["attempts_used"], "attempts_max": p["attempts_max"]})
    return "\n".join([
        f"Bạn duyệt clip video Veo cho job {job.id} (skill hana-short, Bước 4). Thư mục hiện tại là thư mục job.",
        "Ảnh gốc: inputs/character.* và inputs/product.* . Các cảnh cần duyệt:",
        json.dumps(rows, ensure_ascii=False, indent=1),
        REVIEW_RULES,
        'Ghi kết quả vào file review_result.json trong thư mục hiện tại: [{"scene_id": "…", "verdict": "PASS|RETRY|'
        'REVIEW_REQUIRED", "reasons": ["…"], "fix_hint": "…"}] - đủ mọi cảnh ở trên. Không chạy lệnh nào, không sửa file '
        'khác, không hỏi lại người dùng.',
    ])


def write_script_step(cfg: dict, job: Job, data: dict) -> tuple[bool, str]:
    """Claude / Codex viết script.json → tool kiểm tra (validate_script) → nạp. Lỗi thì lần sau gửi kèm danh sách lỗi."""
    errors = ((data.get("autopilot") or {}).get("script_errors")) or None
    target = job.dir / "script.json"
    target.unlink(missing_ok=True)
    r = run_agent(cfg, job, "script", script_prompt(cfg, job, data, errors),
                  [v for k, v in data["inputs"].items() if k in ("character", "product") and v])
    if not target.exists():
        return False, f"Claude / Codex không ghi script.json (mã {r['code']}, xem {Path(r['log']).name})"
    try:
        job.set_script(target)
    except (JobError, ValueError) as e:
        msg = str(e)
        with job.edit() as d:
            d.setdefault("autopilot", {"tries": {}})["script_errors"] = [x.strip("- ") for x in msg.splitlines()[1:]][:20] or [msg]
        return False, msg[:400]
    with job.edit() as d:
        d.setdefault("autopilot", {"tries": {}})["script_errors"] = None
    return True, ""


def review_step(cfg: dict, job: Job) -> tuple[bool, str]:
    """Storyboard + kiểm tra kỹ thuật (tool) → Claude / Codex xem và ghi verdict → tool áp dụng (giới hạn retry)."""
    from . import review
    packets = review.prepare(cfg, job.id)
    if not packets:
        return True, ""
    result = job.dir / "review_result.json"
    result.unlink(missing_ok=True)
    data = job.load()
    images = [v for k, v in data["inputs"].items() if k in ("character", "product") and v] + [p["storyboard"] for p in packets]
    r = run_agent(cfg, job, "review", review_prompt(job, packets), images)
    try:
        verdicts = read_json(result)
        if isinstance(verdicts, dict):
            verdicts = verdicts.get("scenes") or verdicts.get("results") or []
    except (OSError, ValueError):
        return False, f"Claude / Codex không ghi review_result.json (mã {r['code']}, xem {Path(r['log']).name})"
    done = set()
    for v in verdicts if isinstance(verdicts, list) else []:
        sid = str((v or {}).get("scene_id") or "")
        if sid not in {p["scene_id"] for p in packets} or sid in done:
            continue
        reasons = [str(x) for x in (v.get("reasons") or [v.get("reason") or ""]) if str(x).strip()] or ["(không ghi lý do)"]
        verdict = str(v.get("verdict") or "").upper()
        try:
            review.apply_verdict(cfg, job.id, sid, verdict, reasons, str(v.get("fix_hint") or ""))
        except JobError:
            # PASS bị chặn (kỹ thuật lỗi) hoặc verdict sai → để người dùng quyết
            review.apply_verdict(cfg, job.id, sid, "REVIEW_REQUIRED", reasons + [f"verdict '{verdict}' không áp dụng được"])
        done.add(sid)
    left = [p["scene_id"] for p in packets if p["scene_id"] not in done]
    return not left, (f"còn {len(left)} cảnh chưa có verdict ({', '.join(left)})" if left else "")


# ------------------------------------------------------------------------------------------- edit stage
def finish_edit(cfg: dict, job: Job) -> dict:
    """All scenes approved -> timeline, package, FFmpeg final (verified 9:16), CapCut draft (optional touch-up)."""
    timeline.build_timeline(cfg, job.id)
    timeline.build_package(cfg, job.id)
    ver = timeline.render_preview(cfg, job.id)
    if not ver["ok"]:
        raise JobError("Bản dựng FFmpeg không đạt: " + "; ".join(ver["errors"]))
    export_dir = resolve_path(cfg["export_dir"])
    data = job.load()
    sub = export_dir / ((data.get("batch") or {}).get("name") or "le")
    sub.mkdir(parents=True, exist_ok=True)
    final = sub / f"{job.id}.mp4"
    shutil.copy2(ver["path"], final)
    draft = None
    try:
        draft = capcut.build_draft(cfg, job.id)
    except (JobError, media.MediaError, OSError) as e:
        draft = {"error": str(e)}
    with job.edit() as d:
        d["output"] = {"final": str(final), "source": "ffmpeg", "verify": ver, "capcut_draft": draft,
                       "simulated": bool(d.get("simulated"))}
        d["stage"] = "done"
    job.log("autopilot_done", final=str(final), capcut=(draft or {}).get("draft_name"))
    return {"final": str(final), "capcut_draft": (draft or {}).get("draft_name") or (draft or {}).get("error")}


# ------------------------------------------------------------------------------------------- loop
def _tries(job: Job, step: str) -> int:
    return ((job.load().get("autopilot") or {}).get("tries") or {}).get(step, 0)


def _bump(job: Job, step: str, error: str | None = None) -> None:
    with job.edit() as d:
        ap = d.setdefault("autopilot", {"tries": {}})
        ap.setdefault("tries", {})[step] = ap.get("tries", {}).get(step, 0) + 1
        ap["error"] = error


def step(cfg: dict, batch: str | None = None, agents: bool = True) -> dict:
    """One autopilot pass over the batch. Returns a summary; safe to call repeatedly."""
    summary = {"script": [], "generate": [], "review": [], "edit": [], "done": [], "needs_user": [], "errors": []}
    for job in batch_jobs(cfg, batch):
        try:
            data = job.load()
        except JobError as e:
            summary["errors"].append(str(e))
            continue
        stage = data["stage"]
        scenes = data["scenes"]
        if stage == "done":
            summary["done"].append(job.id)
            continue
        stuck = [s["scene_id"] for s in scenes if s["status"] in (J.BLOCKED, J.REVIEW_REQUIRED, J.GEN_FAILED)]
        if stuck:
            summary["needs_user"].append(f"{job.id}: {', '.join(stuck)}")
        if stage == "script":
            if not agents:
                summary["script"].append(job.id)
                continue
            if _tries(job, "script") >= MAX_AGENT_TRIES:
                summary["needs_user"].append(f"{job.id}: Claude viết kịch bản lỗi {MAX_AGENT_TRIES} lần - xem autopilot_script_*.log")
                continue
            try:
                ok, why = write_script_step(cfg, job, data)
            except JobError as e:
                ok, why = False, str(e)
            _bump(job, "script", None if ok else why)
            (summary["script"] if ok else summary["errors"]).append(job.id if ok else f"{job.id}: {why}")
        elif stage == "review" or any(s["status"] == J.DOWNLOADED for s in scenes):
            if not agents:
                summary["review"].append(job.id)
                continue
            key = "review:" + ",".join(f"{s['scene_id']}{len(s['attempts'])}" for s in scenes if s["status"] == J.DOWNLOADED)
            if _tries(job, key) >= MAX_AGENT_TRIES:
                summary["needs_user"].append(f"{job.id}: Claude duyệt lỗi - xem autopilot_review_*.log")
                continue
            try:
                ok, why = review_step(cfg, job)
            except (JobError, media.MediaError) as e:
                ok, why = False, str(e)
            _bump(job, key, None if ok else why)
            summary["review"].append(job.id)
        elif stage == "edit" or (scenes and all(s["status"] == J.APPROVED for s in scenes)):
            try:
                res = finish_edit(cfg, job)
                summary["edit"].append(f"{job.id} → {res['final']}")
            except (JobError, media.MediaError) as e:
                summary["errors"].append(f"{job.id}: {e}")
        else:
            summary["generate"].append(job.id)
    write_status(cfg, batch, summary)
    return summary


def write_status(cfg: dict, batch: str | None, summary: dict) -> None:
    """Mirror progress into the tool's Agent space: <HanaMedia>/output/_status/hana-short.json."""
    home = resolve_path(cfg.get("hanamedia", {}).get("home") or "C:/Users/Admin/HanaMedia")
    if not home or not home.exists():
        return
    total = sum(len(summary[k]) for k in ("script", "generate", "review", "edit", "done"))
    done = len(summary["done"]) + len(summary["edit"])
    working = "working" if total > done else "done"
    status = {"agents": {"hana-short": working,
                         "hana-short-script": "working" if summary["script"] else "done",
                         "hana-short-review": "working" if summary["review"] else ("waiting" if summary["generate"] else "done")},
              "item": f"Lô {batch or 'tất cả'}", "percent": round(100 * done / total) if total else 0,
              "note": (f"{done}/{total} video xong; đang tạo Veo {len(summary['generate'])}, chờ duyệt "
                       f"{len(summary['review'])}" + (f"; CẦN BẠN: {len(summary['needs_user'])}" if summary["needs_user"] else "")),
              "updated": int(time.time())}
    try:
        write_json_atomic(home / "output" / "_status" / "hana-short.json", status)
    except OSError:
        pass


def run(cfg: dict, batch: str | None, poll: int = 60, max_hours: float = 12) -> dict:
    end = time.time() + max_hours * 3600
    last = {}
    while time.time() < end:
        last = step(cfg, batch)
        print(json.dumps({"at": now_iso(), **{k: len(v) for k, v in last.items()}}, ensure_ascii=False), flush=True)
        pending = sum(len(last[k]) for k in ("script", "generate", "review", "edit"))
        if not pending:
            break
        if not last["script"] and not last["review"] and not last["edit"]:
            time.sleep(poll)  # only waiting on Flow / the user
    return last
