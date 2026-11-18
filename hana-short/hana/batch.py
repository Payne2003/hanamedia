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
from .config import ROOT, jobs_dir, resolve_path
from .jobs import Job, JobError, now_iso, read_json, write_json_atomic

CLAUDE_TOOLS = ",".join(["Read", "Write", "Edit", "Glob", "Grep", "Skill",
                         "Bash(hana.bat:*)", "Bash(./hana.bat:*)", "Bash(python:*)", "Bash(ffprobe:*)", "Bash(ls:*)"])
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
    batch = library.slug(name or batch)
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
    digest = hashlib.sha1(Path(file).read_bytes()).hexdigest()
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
        manifest.setdefault("sources", {})[digest] = {"file": str(file), "at": now_iso()}
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
def claude_exe(cfg: dict) -> list[str]:
    override = os.environ.get("HANA_CLAUDE_CMD") or cfg.get("autopilot", {}).get("claude_cmd")
    if override:
        return json.loads(override) if override.strip().startswith("[") else [override]
    for cand in (shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude.exe"),
                 os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")):
        if cand and Path(cand).exists():
            return [cand]
    raise JobError("Không tìm thấy Claude Code (lệnh claude) trên máy")


def run_agent(cfg: dict, job: Job, step: str, prompt: str) -> dict:
    cmd = claude_exe(cfg) + ["-p", prompt, "--permission-mode", "acceptEdits", "--allowedTools", CLAUDE_TOOLS]
    model = cfg.get("autopilot", {}).get("model")
    if model:
        cmd += ["--model", model]
    log = job.dir / f"autopilot_{step}_{time.strftime('%Y%m%d-%H%M%S')}.log"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HANA_JOB": job.id}
    try:
        ffdir = str(Path(media.ffmpeg_bin(cfg)).parent)
        env["PATH"] = ffdir + os.pathsep + str(ROOT) + os.pathsep + env.get("PATH", "")
    except media.MediaError:
        pass
    t0 = time.time()
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        proc = subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, env=env,
                              timeout=int(cfg.get("autopilot", {}).get("agent_timeout_sec", 1800)),
                              creationflags=media.CREATE_NO_WINDOW)
    job.log("autopilot_agent", step=step, code=proc.returncode, seconds=round(time.time() - t0), log=str(log))
    return {"code": proc.returncode, "log": str(log)}


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


def script_prompt(cfg, job: Job, data: dict) -> str:
    b = data.get("batch") or {}
    sib = _siblings(cfg, job, data)
    return "\n".join([
        f"Dùng skill hana-short. CHỈ làm Bước 1 (viết kịch bản) cho job_id {job.id} đã được tạo sẵn; không init lại.",
        f"Đầu vào: jobs/{job.id}/inputs/ (character.*, product.*, brief.md). Nhân vật: {b.get('character')}; "
        f"sản phẩm: {b.get('product')}.",
        "" if data["inputs"].get("brief_provided", True) else
        "KHÔNG CÓ BRIEF: chỉ dựa vào ảnh sản phẩm; claims = []; lời thoại không nêu công dụng, thông số, chất liệu, "
        "giá, ưu đãi hay con số nào - chỉ cảm xúc, cách dùng nhìn thấy được và lời kêu gọi chung.",
        f"Ghi chú của lô: {b.get('note') or '(không có)'}. Đây là video {b.get('variant')}/{b.get('variants')} cho sản phẩm này.",
        ("Các video khác cùng sản phẩm đã dùng góc sau - chọn hook/bối cảnh/lời thoại KHÁC hẳn:\n- " + "\n- ".join(sib))
        if sib else "Chọn một góc quảng cáo rõ ràng, tự nhiên.",
        f"Ghi jobs/{job.id}/script.json, chạy `hana.bat validate-script --job {job.id} --script jobs/{job.id}/script.json`"
        f" tới khi ok rồi `hana.bat set-script ...`. Không hỏi lại người dùng, không làm bước khác.",
    ])


def review_prompt(job: Job) -> str:
    return "\n".join([
        f"Dùng skill hana-short. CHỈ làm Bước 4 (duyệt clip) cho job_id {job.id}.",
        f"Chạy `hana.bat review-prep --job {job.id}`, Read từng storyboard, so với jobs/{job.id}/inputs/character.* và "
        "product.*, tiêu chí và technical_checks, rồi `hana.bat review-set` cho TỪNG cảnh với lý do cụ thể theo khung/mốc giây.",
        "Nghi ngờ sai sản phẩm, sai tuyên bố hay khuôn mặt khác → REVIEW_REQUIRED. Không hỏi lại người dùng, không làm bước khác.",
    ])


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
            r = run_agent(cfg, job, "script", script_prompt(cfg, job, data))
            ok = job.load()["stage"] != "script"
            _bump(job, "script", None if ok else f"exit {r['code']}, xem {r['log']}")
            (summary["script"] if ok else summary["errors"]).append(job.id if ok else f"{job.id}: script chưa nạp ({r['log']})")
        elif stage == "review" or any(s["status"] == J.DOWNLOADED for s in scenes):
            if not agents:
                summary["review"].append(job.id)
                continue
            key = "review:" + ",".join(f"{s['scene_id']}{len(s['attempts'])}" for s in scenes if s["status"] == J.DOWNLOADED)
            if _tries(job, key) >= MAX_AGENT_TRIES:
                summary["needs_user"].append(f"{job.id}: Claude duyệt lỗi - xem autopilot_review_*.log")
                continue
            r = run_agent(cfg, job, "review", review_prompt(job))
            left = [s for s in job.load()["scenes"] if s["status"] == J.DOWNLOADED]
            _bump(job, key, None if not left else f"còn {len(left)} cảnh chưa duyệt ({r['log']})")
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
