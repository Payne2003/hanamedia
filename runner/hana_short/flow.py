"""Flow bridge: the state machine the Chrome extension talks to (through the native host).

Duplicate-submission guard
  READY -> CLAIMED          extension got the task and is filling the form (nothing sent yet)
  CLAIMED -> SUBMITTING     extension asks permission right before clicking Generate; host persists this FIRST
  SUBMITTING -> GENERATING  extension saw the new tile (proof Flow accepted it)
  SUBMITTING -> BLOCKED     no proof within the timeout, or the extension/host restarted mid-submit
                            (UNKNOWN_SUBMIT). A BLOCKED scene is never re-sent automatically: the user checks
                            Flow and runs `hana resolve` (retry / await-download).
A global block (login / quota / captcha / ui_changed) stops all claims until `hana flow-unblock`.
"""
from __future__ import annotations

import base64
import mimetypes
import shutil
import time
from pathlib import Path

from . import jobs as J
from .config import jobs_dir, resolve_path
from .jobs import Job, JobError, now_iso, read_json, write_json_atomic

GLOBAL_REASONS = {"login", "quota", "captcha", "ui_changed"}


def _state_file(cfg) -> Path:
    return jobs_dir(cfg) / "_flow_state.json"


def global_block(cfg) -> dict | None:
    f = _state_file(cfg)
    if f.exists():
        st = read_json(f)
        return st.get("block")
    return None


def set_global_block(cfg, reason: str, detail: str, job_id=None, scene_id=None) -> None:
    write_json_atomic(_state_file(cfg), {"block": {"reason": reason, "detail": detail, "job_id": job_id,
                                                   "scene_id": scene_id, "at": now_iso()}})


def clear_global_block(cfg) -> None:
    write_json_atomic(_state_file(cfg), {"block": None, "cleared_at": now_iso()})


def _all_jobs(cfg):
    for p in sorted(jobs_dir(cfg).glob("*/job.json")):
        yield Job(cfg, p.parent.name)


def recover_orphans(cfg, active_token: str | None) -> list[dict]:
    """Called on extension hello: scenes left mid-flight by a crashed extension/host."""
    changed = []
    for job in _all_jobs(cfg):
        try:
            with job.edit() as data:
                for sc in data["scenes"]:
                    if sc.get("token") and sc["token"] == active_token:
                        continue
                    if sc["status"] == J.CLAIMED:
                        # nothing was clicked yet -> safe to put back
                        sc["status"], sc["token"] = J.READY, None
                        if sc["attempts"] and not sc["attempts"][-1].get("submitted_at"):
                            sc["attempts"].pop()
                        changed.append({"job": job.id, "scene": sc["scene_id"], "to": J.READY})
                    elif sc["status"] == J.SUBMITTING:
                        sc["status"] = J.BLOCKED
                        sc["block"] = {"reason": "unknown_submit", "at": now_iso(),
                                       "detail": "Tool / Chrome dừng giữa lúc bấm Tạo. Không rõ Flow đã nhận hay chưa - xem Flow rồi chọn Chờ tải hoặc Tạo lại."}
                        changed.append({"job": job.id, "scene": sc["scene_id"], "to": J.BLOCKED})
        except JobError:
            continue
    return changed


def _ref_payload(path: str, name: str) -> dict:
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return {"name": name, "filename": f"hana_{name}{p.suffix.lower()}", "mime": mime,
            "b64": base64.b64encode(p.read_bytes()).decode("ascii")}


def build_prompt(script: dict, scene: dict, retry_hint: str = "") -> str:
    parts = [scene["veo_prompt"].strip()]
    if retry_hint:
        parts.append("Important correction from the previous attempt: " + retry_hint.strip())
    bible = (script.get("character_bible") or "").strip()
    if bible and bible[:40] not in parts[0]:
        parts.append("Character (keep identical to the reference image): " + bible)
    if scene.get("dialogue"):
        parts.append(f'Dialogue (spoken by the character, {script.get("language", "Vietnamese")}): "{scene["dialogue"].strip()}"')
    parts.append("Vertical 9:16 framing, subject centered, no on-screen text, no subtitles, no watermark.")
    return "\n".join(parts)


def generating_resumes(cfg) -> list[dict]:
    """Scenes in GENERATING (Flow accepted) whose clip has not arrived: extension can resume watching."""
    out = []
    for job in _all_jobs(cfg):
        try:
            data = job.load()
        except JobError:
            continue
        for sc in data["scenes"]:
            if sc["status"] == J.GENERATING and sc["attempts"]:
                att = sc["attempts"][-1]
                out.append({"resume": True, "job_id": job.id, "scene_id": sc["scene_id"], "attempt": att["n"],
                            "token": sc.get("token") or att.get("token"), "evidence": att.get("evidence") or {},
                            "project_url": att.get("project_url")})
    return out


def claim_task(cfg, job_filter: str | None = None) -> dict:
    gb = global_block(cfg)
    if gb:
        return {"task": None, "blocked": gb}
    res = [r for r in generating_resumes(cfg) if not job_filter or r["job_id"] == job_filter]
    if res:
        return {"task": res[0]}
    for job in _all_jobs(cfg):
        if job_filter and job.id != job_filter:
            continue
        try:
            data = job.load()
        except JobError:
            continue
        if data.get("simulated"):
            continue
        if any(s["status"] in J.ACTIVE for s in data["scenes"]):
            return {"task": None, "busy": {"job": job.id}}
        cand = sorted([s for s in data["scenes"] if s["status"] == J.READY], key=lambda s: s["order"])
        if not cand:
            continue
        sid = cand[0]["scene_id"]
        hint = cand[0].get("retry_hint") or ""
        script = job.script()
        sscene = job.script_scene(sid)
        token = J.new_token()
        with job.edit() as d:
            sc = Job.scene(d, sid)
            if sc["status"] != J.READY:
                continue
            n = len(sc["attempts"]) + 1
            if n > job.max_attempts():
                sc["status"] = J.REVIEW_REQUIRED
                sc["block"] = {"reason": "retry_limit", "at": now_iso()}
                continue
            sc["attempts"].append({"n": n, "started_at": now_iso(), "source": "flow", "token": token})
            sc["status"], sc["token"] = J.CLAIMED, token
        refs = []
        for r in sscene.get("reference_images") or ["character"]:
            path = data["inputs"].get(r)
            if path:
                refs.append(_ref_payload(path, r))
        fcfg = cfg["flow"]
        task = {"resume": False, "job_id": job.id, "scene_id": sid, "attempt": n, "token": token,
                "prompt": build_prompt(script, sscene, hint), "aspect_ratio": cfg["video"]["aspect_ratio"],
                "duration_label": fcfg["duration_label"], "model": fcfg["model"], "outputs": fcfg["outputs"],
                "mode": fcfg["mode"], "refs": refs, "project_url": fcfg.get("project_url") or "",
                "submit_confirm_timeout_sec": fcfg["submit_confirm_timeout_sec"],
                "generation_timeout_sec": fcfg["generation_timeout_sec"],
                "download_resolution_index": fcfg["download_resolution_index"],
                "title": f"{job.id}__{sid}__a{n}"}
        job.log("flow_claimed", scene=sid, attempt=n, token=token)
        return {"task": task}
    return {"task": None}


def _find_token(cfg, token: str):
    for job in _all_jobs(cfg):
        try:
            data = job.load()
        except JobError:
            continue
        for sc in data["scenes"]:
            if sc.get("token") == token:
                return job, sc["scene_id"]
    raise JobError(f"token không khớp cảnh nào: {token}")


def pre_submit(cfg, token: str) -> dict:
    job, sid = _find_token(cfg, token)
    with job.edit() as d:
        sc = Job.scene(d, sid)
        if sc["status"] != J.CLAIMED:
            return {"ok": False, "error": f"scene status {sc['status']} (không phải CLAIMED) - KHÔNG bấm Tạo"}
        sc["status"] = J.SUBMITTING
        sc["attempts"][-1]["submit_clicked_at"] = now_iso()
    job.log("flow_pre_submit", scene=sid, token=token)
    return {"ok": True}


def submitted(cfg, token: str, evidence: dict, project_url: str = "") -> dict:
    job, sid = _find_token(cfg, token)
    with job.edit() as d:
        sc = Job.scene(d, sid)
        att = sc["attempts"][-1]
        att.update({"submitted_at": now_iso(), "evidence": evidence, "project_url": project_url})
        sc["status"] = J.GENERATING
    job.log("flow_submitted", scene=sid, evidence=evidence)
    return {"ok": True}


def submit_unknown(cfg, token: str, detail: str) -> dict:
    job, sid = _find_token(cfg, token)
    with job.edit() as d:
        sc = Job.scene(d, sid)
        sc["status"] = J.BLOCKED
        sc["block"] = {"reason": "unknown_submit", "detail": detail, "at": now_iso()}
    job.log("flow_submit_unknown", scene=sid, detail=detail)
    return {"ok": True}


def blocked(cfg, reason: str, detail: str, token: str | None = None) -> dict:
    """Extension hit a wall. Before pre_submit the scene goes back to READY (nothing was sent)."""
    job = sid = None
    if token:
        try:
            job, sid = _find_token(cfg, token)
        except JobError:
            pass
    if job:
        with job.edit() as d:
            sc = Job.scene(d, sid)
            if sc["status"] == J.CLAIMED:
                sc["status"], sc["token"] = J.READY, None
                if sc["attempts"] and not sc["attempts"][-1].get("submit_clicked_at"):
                    sc["attempts"].pop()
            elif sc["status"] == J.SUBMITTING:
                sc["status"] = J.BLOCKED
                sc["block"] = {"reason": "unknown_submit", "detail": f"{reason}: {detail}", "at": now_iso()}
            elif sc["status"] == J.GENERATING:
                sc["block"] = {"reason": reason, "detail": detail, "at": now_iso()}  # keep GENERATING
        job.log("flow_blocked", scene=sid, reason=reason, detail=detail)
    if reason in GLOBAL_REASONS:
        set_global_block(cfg, reason, detail, job.id if job else None, sid)
    return {"ok": True}


def gen_failed(cfg, token: str, detail: str) -> dict:
    job, sid = _find_token(cfg, token)
    with job.edit() as d:
        sc = Job.scene(d, sid)
        sc["attempts"][-1]["failed"] = detail
        sc["status"], sc["token"] = J.GEN_FAILED, None
    job.log("flow_generation_failed", scene=sid, detail=detail)
    return {"ok": True}


def downloaded(cfg, token: str, path: str) -> dict:
    job, sid = _find_token(cfg, token)
    src = Path(path)
    # Chrome may still hold the file for a moment
    for _ in range(40):
        if src.exists() and src.stat().st_size > 0:
            break
        time.sleep(0.25)
    if not src.exists():
        dl = resolve_path(cfg["downloads_dir"])
        cand = dl / src.name if dl else None
        if cand and cand.exists():
            src = cand
        else:
            return {"ok": False, "error": f"không thấy file tải về: {path}"}
    dest = job.add_clip(sid, src, source="flow", move=True, flow_file=src.name)
    return {"ok": True, "clip": str(dest)}


def import_clip(cfg, job_id: str, scene_id: str, path: str) -> Path:
    """Manual fallback: user downloaded the clip by hand."""
    return Job(cfg, job_id).add_clip(scene_id, Path(path), source="manual_import", move=False)
