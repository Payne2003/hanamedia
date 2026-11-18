"""Command line: `hana <command>` (hana.bat wraps `python -m hana`)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import capcut, flow, media, review, timeline
from . import jobs as J
from .config import load_config, resolve_path
from .jobs import Job, JobError


def _print(obj) -> None:
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(obj)


def cmd_doctor(cfg, a):
    out = {"config": cfg["_path"], "jobs_dir": str(resolve_path(cfg["jobs_dir"]))}
    for name in ("ffmpeg", "ffprobe"):
        try:
            out[name] = getattr(media, f"{name}_bin")(cfg)
        except media.MediaError as e:
            out[name] = f"MISSING: {e}"
    try:
        out["h264_encoder"] = media.pick_h264_encoder(cfg)[1]
    except media.MediaError as e:
        out["h264_encoder"] = str(e)
    for k in ("downloads_dir", "sfx_dir", "export_dir"):
        p = resolve_path(cfg.get(k))
        out[k] = {"path": str(p) if p else None, "exists": bool(p and p.exists())}
    out["sfx_files"] = len(timeline.scan_sfx(cfg))
    out["capcut"] = capcut.environment(cfg)
    out["flow_global_block"] = flow.global_block(cfg)
    _print(out)


def cmd_init(cfg, a):
    data = Job(cfg, a.job).create(Path(a.character), Path(a.brief) if a.brief else None,
                                  Path(a.product) if a.product else None, a.title, a.product_name or "")
    _print({"job_id": data["job_id"], "dir": str(Job(cfg, a.job).dir), "inputs": data["inputs"]})


def cmd_set_script(cfg, a):
    data = Job(cfg, a.job).set_script(Path(a.script))
    _print({"job_id": a.job, "stage": data["stage"], "scenes": {s["scene_id"]: s["status"] for s in data["scenes"]}})


def cmd_validate_script(cfg, a):
    job = Job(cfg, a.job)
    errs = job.validate_script(J.read_json(Path(a.script)))
    _print({"ok": not errs, "errors": errs})
    return 0 if not errs else 2


def cmd_status(cfg, a):
    if not a.job:
        _print({"jobs": J.list_jobs(cfg), "flow_global_block": flow.global_block(cfg)})
        return
    d = Job(cfg, a.job).load()
    scenes = []
    for s in sorted(d["scenes"], key=lambda x: x["order"]):
        last = s["attempts"][-1] if s["attempts"] else {}
        scenes.append({"scene": s["scene_id"], "status": s["status"], "attempts": len(s["attempts"]),
                       "last_verdict": last.get("verdict"), "reasons": last.get("reasons"),
                       "block": s.get("block"), "clip": last.get("clip"), "approved": s.get("approved_clip")})
    _print({"job_id": d["job_id"], "stage": d["stage"], "simulated": d.get("simulated"), "scenes": scenes,
            "capcut": {k: v for k, v in (d.get("capcut") or {}).items() if k != "history"},
            "output": d.get("output"), "flow_global_block": flow.global_block(cfg)})


def cmd_import_clip(cfg, a):
    _print({"clip": str(flow.import_clip(cfg, a.job, a.scene, a.file))})


def cmd_resolve(cfg, a):
    d = Job(cfg, a.job).resolve(a.scene, a.action, a.note or "")
    _print({s["scene_id"]: s["status"] for s in d["scenes"]})


def cmd_flow_unblock(cfg, a):
    flow.clear_global_block(cfg)
    _print({"ok": True, "note": "Đã gỡ chặn toàn cục. Cảnh BLOCKED riêng lẻ vẫn cần `hana resolve`."})


def _copy_to(dest: str, files: dict) -> dict:
    """Copy files into a folder the calling agent may read (the HanaMedia tool runs Claude inside its own folder)."""
    import shutil
    d = Path(dest)
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, src in files.items():
        if src and Path(src).exists():
            target = d / (name + Path(src).suffix.lower())
            shutil.copy2(src, target)
            out[name] = str(target)
    return out


def cmd_export_inputs(cfg, a):
    d = Job(cfg, a.job).load()
    files = {k: v for k, v in d["inputs"].items() if v}
    _print({"job": a.job, "batch": d.get("batch"), "copied": _copy_to(a.to, files),
            "script_target": str(Path(a.to) / "script.json")})


def cmd_integrate(cfg, a):
    from . import integrate
    _print(integrate.install(cfg))


def cmd_review_prep(cfg, a):
    pk = review.prepare(cfg, a.job, a.scene, a.transcript)
    if a.copy_to:
        inputs = Job(cfg, a.job).load()["inputs"]
        for p in pk:
            p["storyboard"] = _copy_to(a.copy_to, {f"storyboard_{p['scene_id']}_a{p['attempt']}": p["storyboard"]})[
                f"storyboard_{p['scene_id']}_a{p['attempt']}"]
        _copy_to(a.copy_to, {"character": inputs.get("character"), "product": inputs.get("product")})
    _print([{k: p[k] for k in ("scene_id", "attempt", "storyboard", "technical_ok", "technical_checks", "criteria",
                               "visual_checklist", "dialogue", "attempts_used", "attempts_max", "grid")} for p in pk])


def cmd_review_set(cfg, a):
    _print(review.apply_verdict(cfg, a.job, a.scene, a.verdict, a.reason, a.fix_hint or ""))


def cmd_timeline(cfg, a):
    tl = timeline.build_timeline(cfg, a.job)
    _print({"timeline": str(Job(cfg, a.job).dir / "edit" / "timeline.json"), "duration": tl["duration"],
            "clips": [(c["scene_id"], c["in"], c["out"]) for c in tl["clips"]],
            "sfx": [(s["at"], s["tag"], s["file"]) for s in tl["sfx"]],
            "sfx_unmatched": tl["sfx_unmatched"], "sfx_library_files": tl["sfx_library"]["files"]})


def cmd_package(cfg, a):
    _print({"package": str(timeline.build_package(cfg, a.job))})


def cmd_preview(cfg, a):
    _print(timeline.render_preview(cfg, a.job, Path(a.out) if a.out else None))


def cmd_verify(cfg, a):
    r = media.verify_output(cfg, a.file, a.aspect)
    _print(r)
    return 0 if r["ok"] else 3


def cmd_capcut(cfg, a):
    if a.sub == "probe":
        _print({"environment": capcut.environment(cfg), "ui_automation": capcut.probe_ui(cfg)})
    elif a.sub == "template":
        src = Path(a.source) if a.source else capcut.find_template_source(cfg)
        if not src:
            raise JobError("Không tìm thấy draft có cả video và audio; truyền --from")
        _print({"template": str(capcut.capture_template(cfg, src)), "from": str(src)})
    elif a.sub == "build":
        _print(capcut.build_draft(cfg, a.job, a.name))
    elif a.sub == "opened":
        capcut.record_step(cfg, a.job, "open_draft", "manual_ok", "Người dùng xác nhận đã mở draft trong CapCut")
        _print({"ok": True})
    elif a.sub == "export":
        _print(capcut.try_export(cfg, a.job))
    elif a.sub == "register-export":
        _print(capcut.register_export(cfg, a.job, Path(a.file)))
    elif a.sub == "step":
        capcut.record_step(cfg, a.job, a.step, a.status, a.detail or "")
        _print({"ok": True})


def cmd_simulate_flow(cfg, a):
    """Produce SYNTHETIC clips for READY scenes (pipeline test only - marks the job simulated)."""
    job = Job(cfg, a.job)
    d = job.load()
    made = []
    for s in sorted(d["scenes"], key=lambda x: x["order"]):
        if s["status"] != J.READY:
            continue
        tmp = job.dir / "_sim" / f"{s['scene_id']}.mp4"
        w, h = (1280, 720) if a.landscape == s["scene_id"] else (720, 1280)
        media.make_test_clip(cfg, tmp, seconds=a.seconds, w=w, h=h, freq=300 + 110 * s["order"])
        job.add_clip(s["scene_id"], tmp, source="SIMULATED", move=True)
        made.append(s["scene_id"])
    with job.edit() as dd:
        dd["simulated"] = True
    _print({"simulated_clips": made, "warning": "Clip giả lập (testsrc2) - không phải video thật từ Flow"})


def cmd_make_test_sfx(cfg, a):
    out = Path(a.dir)
    for tag, f in (("whoosh", 900), ("pop", 1500), ("ding", 2000)):
        media.make_test_sfx(cfg, out / tag / f"{tag}_test.wav", freq=f)
    _print({"sfx_dir": str(out)})


def cmd_library(cfg, a):
    from . import library
    items = library.list_images(cfg, a.category, a.query or "")
    _print({"data_dir": str(library.data_dir(cfg)), "brief_dir": str(library.brief_dir(cfg)), "count": len(items),
            "items": [{k: i[k] for k in ("id", "name", "category", "path")} for i in items[:200]]})


def cmd_batch(cfg, a):
    from . import batch
    if a.sub == "create":
        _print(batch.create(cfg, Path(a.file), a.name, a.dry_run))
    elif a.sub == "step":
        _print(batch.step(cfg, a.batch, agents=not a.no_agents))
    elif a.sub == "run":
        _print(batch.run(cfg, a.batch, poll=int(cfg["autopilot"]["poll_sec"]), max_hours=a.hours))
    elif a.sub == "status":
        rows = []
        for job in batch.batch_jobs(cfg, a.batch):
            d = job.load()
            rows.append({"job": job.id, "stage": d["stage"],
                         "scenes": " ".join(f"{s['scene_id']}={s['status']}" for s in d["scenes"]),
                         "final": (d.get("output") or {}).get("final"),
                         "error": (d.get("autopilot") or {}).get("error")})
        _print(rows)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hana", description="HanaMedia Short job manager")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("doctor", help="kiểm tra FFmpeg, thư mục, CapCut").set_defaults(fn=cmd_doctor)

    x = sp.add_parser("init", help="tạo job mới")
    x.add_argument("--job", required=True)
    x.add_argument("--character", required=True)
    x.add_argument("--brief", help="không bắt buộc: thiếu brief thì kịch bản không được nêu tuyên bố sản phẩm")
    x.add_argument("--product")
    x.add_argument("--product-name", help="tên sản phẩm (mặc định = tên file ảnh)")
    x.add_argument("--title", default="")
    x.set_defaults(fn=cmd_init)

    x = sp.add_parser("validate-script")
    x.add_argument("--job", required=True)
    x.add_argument("--script", required=True)
    x.set_defaults(fn=cmd_validate_script)

    x = sp.add_parser("set-script", help="nạp script.json (giữ nguyên cảnh đã duyệt nếu prompt không đổi)")
    x.add_argument("--job", required=True)
    x.add_argument("--script", required=True)
    x.set_defaults(fn=cmd_set_script)

    x = sp.add_parser("status")
    x.add_argument("--job")
    x.set_defaults(fn=cmd_status)

    x = sp.add_parser("import-clip", help="nhập clip tải tay từ Flow")
    x.add_argument("--job", required=True)
    x.add_argument("--scene", required=True)
    x.add_argument("--file", required=True)
    x.set_defaults(fn=cmd_import_clip)

    x = sp.add_parser("resolve", help="gỡ cảnh BLOCKED/REVIEW_REQUIRED")
    x.add_argument("--job", required=True)
    x.add_argument("--scene", required=True)
    x.add_argument("--action", required=True, choices=["retry", "approve", "await-download"])
    x.add_argument("--note")
    x.set_defaults(fn=cmd_resolve)

    sp.add_parser("flow-unblock", help="gỡ chặn toàn cục (sau khi đã đăng nhập / có lượt)").set_defaults(
        fn=cmd_flow_unblock)

    x = sp.add_parser("review-prep", help="storyboard + kiểm tra kỹ thuật cho cảnh chờ duyệt")
    x.add_argument("--job", required=True)
    x.add_argument("--scene")
    x.add_argument("--transcript")
    x.add_argument("--copy-to", help="chép storyboard + ảnh gốc sang thư mục này (khi agent chạy trong thư mục khác)")
    x.set_defaults(fn=cmd_review_prep)

    x = sp.add_parser("export-inputs", help="chép ảnh nhân vật/sản phẩm + brief của job sang thư mục khác")
    x.add_argument("--job", required=True)
    x.add_argument("--to", required=True)
    x.set_defaults(fn=cmd_export_inputs)

    x = sp.add_parser("integrate-hanamedia", help="đăng ký workflow hana-short vào tool HanaMedia")
    x.set_defaults(fn=cmd_integrate)

    x = sp.add_parser("review-set", help="ghi kết quả duyệt")
    x.add_argument("--job", required=True)
    x.add_argument("--scene", required=True)
    x.add_argument("--verdict", required=True, choices=list(review.VERDICTS))
    x.add_argument("--reason", action="append", required=True)
    x.add_argument("--fix-hint")
    x.set_defaults(fn=cmd_review_set)

    for name, fn in (("timeline", cmd_timeline), ("package", cmd_package)):
        x = sp.add_parser(name)
        x.add_argument("--job", required=True)
        x.set_defaults(fn=fn)

    x = sp.add_parser("preview", help="render FFmpeg từ timeline.json để kiểm tra")
    x.add_argument("--job", required=True)
    x.add_argument("--out")
    x.set_defaults(fn=cmd_preview)

    x = sp.add_parser("verify")
    x.add_argument("file")
    x.add_argument("--aspect", default="9:16")
    x.set_defaults(fn=cmd_verify)

    x = sp.add_parser("capcut")
    x.add_argument("sub", choices=["probe", "template", "build", "opened", "export", "register-export", "step"])
    x.add_argument("--job")
    x.add_argument("--from", dest="source")
    x.add_argument("--name")
    x.add_argument("--file")
    x.add_argument("--step", choices=capcut.STEPS)
    x.add_argument("--status", choices=["auto_ok", "manual_ok", "failed", "manual_required"])
    x.add_argument("--detail")
    x.set_defaults(fn=cmd_capcut)

    x = sp.add_parser("simulate-flow", help="CHỈ ĐỂ TEST: tạo clip giả lập cho cảnh READY")
    x.add_argument("--job", required=True)
    x.add_argument("--seconds", type=float, default=8)
    x.add_argument("--landscape", help="scene_id sẽ nhận clip 16:9 (để test RETRY)")
    x.set_defaults(fn=cmd_simulate_flow)

    x = sp.add_parser("library", help="xem thư viện Nhân vật / Sản phẩm của tool HanaMedia (chỉ đọc)")
    x.add_argument("--category", choices=["character", "product", "reference", "other"])
    x.add_argument("--query")
    x.set_defaults(fn=cmd_library)

    x = sp.add_parser("batch", help="sản xuất hàng loạt từ thư viện HanaMedia")
    x.add_argument("sub", choices=["create", "step", "run", "status"])
    x.add_argument("--file", help="CSV/JSON danh sách nhân vật × sản phẩm (create)")
    x.add_argument("--name", help="tên lô (create)")
    x.add_argument("--batch", help="tên lô (step/run/status); bỏ trống = mọi lô")
    x.add_argument("--dry-run", action="store_true")
    x.add_argument("--no-agents", action="store_true", help="step: không gọi Claude, chỉ dựng/báo trạng thái")
    x.add_argument("--hours", type=float, default=12)
    x.set_defaults(fn=cmd_batch)

    x = sp.add_parser("make-test-sfx", help="tạo vài SFX sine để test")
    x.add_argument("--dir", required=True)
    x.set_defaults(fn=cmd_make_test_sfx)
    return p


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    cfg = load_config()
    try:
        rc = a.fn(cfg, a)
        return rc or 0
    except (JobError, media.MediaError) as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1
