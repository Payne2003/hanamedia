"""Review loop: storyboard + technical checks -> Claude looks -> PASS / RETRY / REVIEW_REQUIRED."""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import jobs as J
from . import media
from .jobs import Job, JobError, now_iso, norm_text, read_json, write_json_atomic

VERDICTS = ("PASS", "RETRY", "REVIEW_REQUIRED")


def _words(s: str) -> list[str]:
    return re.findall(r"\w+", norm_text(s))


def transcript_similarity(expected: str, transcript: str) -> float:
    a, b = _words(expected), _words(transcript)
    if not a and not b:
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def technical_checks(cfg: dict, info: dict, target_seconds: float) -> list[dict]:
    rcfg = cfg["review"]
    out = []

    def add(name, ok, detail):
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    add("has_video", info.get("has_video"), info.get("video_codec"))
    add("aspect_9_16", media.aspect_ok(info, cfg["video"]["aspect_ratio"], rcfg["aspect_tolerance"]),
        f"{info.get('width')}x{info.get('height')}")
    add("duration", info.get("duration", 0) >= target_seconds * rcfg["min_duration_ratio"],
        f"{info.get('duration')}s (cần ≥ {round(target_seconds * rcfg['min_duration_ratio'], 2)}s)")
    add("has_audio", info.get("has_audio"), info.get("audio_codec") or "không có âm thanh")
    return out


def prepare(cfg: dict, job_id: str, scene_id: str | None = None, transcript: str | None = None) -> list[dict]:
    """Build review packets for every DOWNLOADED scene (or one scene). Returns the packets."""
    job = Job(cfg, job_id)
    data = job.load()
    packets = []
    for sc in sorted(data["scenes"], key=lambda s: s["order"]):
        if scene_id and sc["scene_id"] != scene_id:
            continue
        if sc["status"] != J.DOWNLOADED:
            if scene_id:
                raise JobError(f"{scene_id} đang {sc['status']}, không có clip chờ duyệt")
            continue
        att = sc["attempts"][-1]
        clip = Path(att["clip"])
        adir = clip.parent
        sscene = job.script_scene(sc["scene_id"])
        sb = media.storyboard(cfg, clip, adir / "storyboard.jpg")
        checks = technical_checks(cfg, sb["probe"], sscene.get("duration", cfg["video"]["scene_seconds"]))
        tr_text, tr_src = None, None
        tfile = Path(transcript) if transcript and scene_id else adir / "transcript.txt"
        if tfile.exists():
            tr_text, tr_src = tfile.read_text(encoding="utf-8-sig"), str(tfile)
        dialogue = None
        if sscene.get("dialogue"):
            sim = transcript_similarity(sscene["dialogue"], tr_text) if tr_text is not None else None
            dialogue = {"expected": sscene["dialogue"], "transcript": tr_text, "transcript_file": tr_src,
                        "similarity": sim,
                        "ok": None if sim is None else sim >= cfg["review"]["transcript_min_similarity"]}
            if sim is not None:
                checks.append({"check": "dialogue_match", "ok": dialogue["ok"], "detail": f"similarity={sim}"})
        packet = {
            "job_id": job_id, "scene_id": sc["scene_id"], "attempt": att["n"], "role": sc.get("role"),
            "clip": str(clip), "storyboard": sb["storyboard"], "grid": {"columns": sb["columns"], "rows": sb["rows"],
                                                                       "frames": sb["frames"]},
            "probe": sb["probe"], "technical_checks": checks,
            "technical_ok": all(c["ok"] for c in checks if c["ok"] is not None),
            "criteria": sscene.get("review_criteria", []),
            "visual_checklist": ["Nhân vật khớp ảnh tham chiếu (khuôn mặt, tóc, trang phục)",
                                 "Sản phẩm đúng hình dạng/màu/nhãn so với ảnh sản phẩm (nếu có)",
                                 "Bố cục dọc 9:16, chủ thể nằm trong vùng an toàn, không bị cắt",
                                 f"Hành động chính: {sscene.get('action', sscene.get('veo_prompt', '')[:160])}",
                                 "Không có chữ/watermark/biến dạng tay mặt"],
            "dialogue": dialogue,
            "attempts_used": len(sc["attempts"]), "attempts_max": job.max_attempts(),
            "prepared_at": now_iso(),
        }
        write_json_atomic(adir / "review_packet.json", packet)
        packets.append(packet)
        job.log("review_prepared", scene=sc["scene_id"], attempt=att["n"], technical_ok=packet["technical_ok"])
    return packets


def apply_verdict(cfg: dict, job_id: str, scene_id: str, verdict: str, reasons: list[str],
                  fix_hint: str = "") -> dict:
    verdict = verdict.upper()
    if verdict not in VERDICTS:
        raise JobError(f"verdict phải là một trong {VERDICTS}")
    if not reasons:
        raise JobError("Cần ít nhất một lý do")
    job = Job(cfg, job_id)
    final = verdict
    with job.edit() as data:
        sc = Job.scene(data, scene_id)
        if sc["status"] != J.DOWNLOADED:
            raise JobError(f"{scene_id} đang {sc['status']}, không ở trạng thái chờ duyệt")
        att = sc["attempts"][-1]
        pkt_file = Path(att["clip"]).parent / "review_packet.json"
        if not pkt_file.exists():
            raise JobError("Chưa chạy review-prep cho cảnh này (chưa có storyboard)")
        pkt = read_json(pkt_file)
        if verdict == "PASS" and not pkt["technical_ok"]:
            bad = [c["check"] for c in pkt["technical_checks"] if c["ok"] is False]
            raise JobError(f"Không thể PASS: kiểm tra kỹ thuật lỗi {bad}. Dùng RETRY hoặc REVIEW_REQUIRED.")
        if verdict == "RETRY" and len(sc["attempts"]) >= job.max_attempts():
            final = "REVIEW_REQUIRED"
            reasons = reasons + [f"đã hết {job.max_attempts()} lượt tạo (giới hạn cấu hình)"]
        att.update({"verdict": final, "requested_verdict": verdict, "reasons": reasons, "fix_hint": fix_hint,
                    "reviewed_at": now_iso()})
        if final == "PASS":
            job._approve(data, sc, att, "; ".join(reasons))
        elif final == "RETRY":
            sc["status"] = J.READY
            sc["retry_hint"] = fix_hint
        else:
            sc["status"] = J.REVIEW_REQUIRED
            sc["block"] = {"reason": "review_required", "detail": "; ".join(reasons), "at": now_iso()}
        job.recompute_stage(data)
        result = {"scene_id": scene_id, "attempt": att["n"], "verdict": final, "reasons": reasons,
                  "status": sc["status"]}
    write_json_atomic(Path(att["clip"]).parent / "review.json", result)
    job.log("review_verdict", **result)
    return result
