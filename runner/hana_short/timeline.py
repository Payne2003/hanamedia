"""timeline.json, SFX library scan, edit package and an FFmpeg preview render."""
from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path

from . import jobs as J
from . import media
from .config import resolve_path
from .jobs import Job, JobError, now_iso, write_json_atomic

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
SYNONYMS = {
    "whoosh": {"whoosh", "swoosh", "swish", "transition", "chuyen", "vut"},
    "pop": {"pop", "bubble", "bop", "click"},
    "ding": {"ding", "bell", "chime", "notification", "chuong"},
    "impact": {"impact", "hit", "boom", "thud", "punch", "bam"},
    "riser": {"riser", "rise", "build", "uplifter"},
    "cash": {"cash", "register", "coin", "money", "kaching"},
    "camera": {"camera", "shutter", "snap"},
    "sparkle": {"sparkle", "shine", "glitter", "magic", "twinkle"},
}


def _tokens(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", s) if t and not t.isdigit()}


def scan_sfx(cfg: dict) -> list[dict]:
    """Thư viện âm thanh của tool (production_sounds: tên + tag) + thư mục SFX riêng (tên thư mục con = tag)."""
    lib = []
    for item in cfg.get("sfx_extra") or []:
        if item.get("path") and Path(item["path"]).is_file():
            lib.append({"path": item["path"], "rel": item.get("rel") or Path(item["path"]).name,
                        "tags": sorted(_tokens(" ".join([item.get("name") or "", item.get("tags") or "",
                                                         Path(item["path"]).stem])))})
    root = resolve_path(cfg.get("sfx_dir"))
    if not root or not root.exists():
        return lib
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in AUDIO_EXT and p.is_file():
            rel = p.relative_to(root)
            tags = _tokens(" ".join(rel.parts))
            lib.append({"path": str(p), "rel": str(rel), "tags": sorted(tags)})
    return lib


def match_sfx(lib: list[dict], tag: str) -> dict | None:
    want = _tokens(tag)
    for k, syn in SYNONYMS.items():
        if want & syn or k in want:
            want |= syn | {k}
    best, score = None, 0
    for item in lib:
        s = len(want & set(item["tags"]))
        if s > score or (s == score and s and best and len(item["rel"]) < len(best["rel"])):
            best, score = item, s
    return best if score else None


def build_timeline(cfg: dict, job_id: str) -> dict:
    job = Job(cfg, job_id)
    data = job.load()
    scenes = sorted(data["scenes"], key=lambda s: s["order"])
    not_ok = [f"{s['scene_id']}={s['status']}" for s in scenes if s["status"] != J.APPROVED]
    if not_ok:
        raise JobError("Chưa duyệt đủ cảnh: " + ", ".join(not_ok))
    script = job.script()
    sfxcfg = cfg["sfx"]
    lib = scan_sfx(cfg)
    clips, sfx, unmatched = [], [], []
    t = 0.0
    for i, sc in enumerate(scenes):
        sscene = job.script_scene(sc["scene_id"])
        info = media.probe(cfg, sc["approved_clip"])
        target = float(sscene.get("duration", cfg["video"]["scene_seconds"]))
        cut_in = float(sscene.get("trim_in", 0.0))
        cut_out = min(info["duration"], cut_in + target, float(sscene.get("trim_out", 1e9)))
        length = round(cut_out - cut_in, 3)
        clips.append({"order": sc["order"], "scene_id": sc["scene_id"], "role": sc.get("role"),
                      "path": sc["approved_clip"], "in": cut_in, "out": round(cut_out, 3),
                      "timeline_start": round(t, 3), "duration": length,
                      "volume": float(sscene.get("dialogue_volume", sfxcfg["dialogue_volume"])),
                      "has_audio": info["has_audio"], "width": info.get("width"), "height": info.get("height")})
        cues = list(sscene.get("sfx_cues") or [])
        if sfxcfg.get("auto_transition") and i > 0 and not any(float(c.get("at", 1)) < 0.3 for c in cues):
            cues.append({"at": 0.0, "tag": sfxcfg["transition_tag"], "auto": True})
        for cue in cues:
            m = match_sfx(lib, cue.get("tag", ""))
            at = round(t + float(cue.get("at", 0)), 3)
            if at >= t + length:
                continue
            entry = {"scene_id": sc["scene_id"], "tag": cue.get("tag"), "at": at,
                     "volume": float(cue.get("volume", sfxcfg["default_volume"])), "auto": bool(cue.get("auto"))}
            if m:
                entry.update({"path": m["path"], "file": m["rel"],
                              "duration": media.probe(cfg, m["path"])["duration"]})
                sfx.append(entry)
            else:
                unmatched.append(entry)
        t += length
    tl = {"job_id": job_id, "title": script.get("title", data.get("title", "")), "created_at": now_iso(),
          "aspect_ratio": cfg["video"]["aspect_ratio"],
          "canvas": {"width": cfg["video"]["render_width"], "height": cfg["video"]["render_height"],
                     "fps": cfg["video"]["render_fps"]},
          "duration": round(t, 3), "clips": clips, "sfx": sfx, "sfx_unmatched": unmatched,
          "sfx_library": {"dir": cfg.get("sfx_dir"), "files": len(lib)}}
    out = job.dir / "edit" / "timeline.json"
    write_json_atomic(out, tl)
    with job.edit() as d:
        d["edit"]["timeline"] = str(out)
        d["stage"] = "edit"
    job.log("timeline_built", clips=len(clips), sfx=len(sfx), unmatched=len(unmatched))
    return tl


def build_package(cfg: dict, job_id: str) -> Path:
    """Self-contained folder you can drag into CapCut by hand: clips, sfx, timeline (relative paths)."""
    job = Job(cfg, job_id)
    tl_file = job.dir / "edit" / "timeline.json"
    if not tl_file.exists():
        build_timeline(cfg, job_id)
    tl = J.read_json(tl_file)
    pkg = job.dir / "edit" / "edit_package"
    if pkg.exists():
        shutil.rmtree(pkg)
    (pkg / "clips").mkdir(parents=True)
    (pkg / "sfx").mkdir()
    rel = {"clips": [], "sfx": []}
    for c in tl["clips"]:
        name = f"{c['order']:02d}_{c['scene_id']}.mp4"
        shutil.copy2(c["path"], pkg / "clips" / name)
        rel["clips"].append({**c, "path": f"clips/{name}"})
    for i, s in enumerate(tl["sfx"], 1):
        name = f"{i:02d}_{Path(s['path']).name}"
        shutil.copy2(s["path"], pkg / "sfx" / name)
        rel["sfx"].append({**s, "path": f"sfx/{name}"})
    write_json_atomic(pkg / "timeline.json", {**tl, **rel, "paths": "relative to this folder"})
    lines = [f"GÓI DỰNG HanaMedia Short - job {job_id}", "",
             f"Canvas: {tl['canvas']['width']}x{tl['canvas']['height']} ({tl['aspect_ratio']}), "
             f"tổng {tl['duration']}s", "", "VIDEO (track chính, nối liền):"]
    for c in rel["clips"]:
        lines.append(f"  {c['timeline_start']:>6.2f}s  {c['path']}  cắt {c['in']}s → {c['out']}s  âm lượng {c['volume']}")
    lines += ["", "SFX (track âm thanh):"]
    for s in rel["sfx"]:
        lines.append(f"  {s['at']:>6.2f}s  {s['path']}  ({s['tag']})  âm lượng {s['volume']}")
    if tl["sfx_unmatched"]:
        lines += ["", "SFX chưa tìm được file phù hợp:"] + [f"  {u['at']}s tag='{u['tag']}'" for u in tl["sfx_unmatched"]]
    lines += ["", "Cách dựng tay trong CapCut: Dự án mới → Nhập toàn bộ thư mục clips/ và sfx/ → kéo clip theo thứ tự",
              "lên track chính → đặt SFX đúng mốc giây ở trên → Xuất (1080x1920, 30fps)."]
    (pkg / "HUONG-DAN-DUNG.txt").write_text("\n".join(lines), encoding="utf-8")
    with job.edit() as d:
        d["edit"]["package"] = str(pkg)
    job.log("package_built", path=str(pkg))
    return pkg


def render_preview(cfg: dict, job_id: str, out: Path | None = None) -> dict:
    """FFmpeg render of timeline.json (same cuts/SFX). Used to check the edit; CapCut stays the main editor."""
    job = Job(cfg, job_id)
    tl_file = job.dir / "edit" / "timeline.json"
    tl = J.read_json(tl_file) if tl_file.exists() else build_timeline(cfg, job_id)
    W, H, FPS = tl["canvas"]["width"], tl["canvas"]["height"], tl["canvas"]["fps"]
    out = Path(out) if out else job.dir / "edit" / "preview_ffmpeg.mp4"
    args, fc, vlabels, alabels = [], [], [], []
    idx = 0
    for c in tl["clips"]:
        args += ["-i", c["path"]]
        v = idx
        idx += 1
        fc.append(f"[{v}:v]trim={c['in']}:{c['out']},setpts=PTS-STARTPTS,scale={W}:{H}:force_original_aspect_ratio=decrease,"
                  f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v{v}]")
        if c["has_audio"]:
            fc.append(f"[{v}:a]atrim={c['in']}:{c['out']},asetpts=PTS-STARTPTS,aresample=48000,"
                      f"aformat=channel_layouts=stereo,volume={c['volume']}[a{v}]")
        else:
            fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{c['duration']}[a{v}]")
        vlabels.append(f"[v{v}][a{v}]")
    fc.append(f"{''.join(vlabels)}concat=n={len(tl['clips'])}:v=1:a=1[vout][amain]")
    mix = ["[amain]"]
    for k, s in enumerate(tl["sfx"]):
        args += ["-i", s["path"]]
        ms = int(round(s["at"] * 1000))
        fc.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={s['volume']},adelay={ms}|{ms}[s{k}]")
        mix.append(f"[s{k}]")
        idx += 1
    if len(mix) > 1:
        fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[aout]")
        alabel = "[aout]"
    else:
        alabel = "[amain]"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [media.ffmpeg_bin(cfg), "-hide_banner", "-y", *args, "-filter_complex", ";".join(fc),
           "-map", "[vout]", "-map", alabel, *media.pick_h264_encoder(cfg), "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-t", str(tl["duration"]), str(out)]
    media.run(cmd, timeout=1800)
    ver = media.verify_output(cfg, out, tl["aspect_ratio"])
    with job.edit() as d:
        d["edit"]["preview"] = {"path": str(out), "verify": ver}
    job.log("preview_rendered", path=str(out), ok=ver["ok"])
    return ver
