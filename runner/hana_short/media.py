"""FFmpeg / FFprobe helpers: locating binaries, probing clips, storyboards, verification."""
from __future__ import annotations

import json
import os
import math
import shutil
import subprocess
from pathlib import Path

from .config import capcut_install_dirs, resolve_path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # Mac/Linux: subprocess không nhận cờ này


class MediaError(RuntimeError):
    pass


def _find(name: str, configured: str | None) -> str:
    if not configured and name == "ffmpeg":
        try:  # ffmpeg đóng gói kèm tool (imageio-ffmpeg)
            from ..video_tools import ffmpeg as tool_ffmpeg
            return tool_ffmpeg()
        except Exception:
            pass
    if configured:
        p = resolve_path(configured)
        if p and p.exists():
            return str(p)
        raise MediaError(f"{name} không tồn tại tại đường dẫn cấu hình: {configured}")
    found = shutil.which(name)
    if found:
        return found
    for d in capcut_install_dirs():
        cand = d / f"{name}.exe"
        if cand.exists():
            return str(cand)
    raise MediaError(f"Không tìm thấy {name}. Cài FFmpeg hoặc đặt đường dẫn '{name}' trong config.json.")


def ffmpeg_bin(cfg: dict) -> str:
    return _find("ffmpeg", cfg.get("ffmpeg"))


def ffprobe_bin(cfg: dict) -> str:
    return _find("ffprobe", cfg.get("ffprobe"))


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise MediaError(f"Lệnh thất bại ({proc.returncode}): {' '.join(cmd[:4])} ...\n{tail}")
    return proc


def probe(cfg: dict, path: str | Path) -> dict:
    """Return a compact description of a media file (ffprobe; tool không kèm ffprobe thì đọc từ `ffmpeg -i`)."""
    try:
        ffprobe_bin(cfg)
    except MediaError:
        return _probe_ffmpeg(cfg, path)
    out = run([ffprobe_bin(cfg), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]).stdout
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(data.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    info = {"path": str(path), "duration": round(dur, 3), "has_video": v is not None, "has_audio": a is not None,
            "format": data.get("format", {}).get("format_name")}
    if v:
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) in (90, 270):
            w, h = h, w
        num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        info.update({"width": w, "height": h, "video_codec": v.get("codec_name"), "fps": round(fps, 3),
                     "aspect": round(w / h, 4) if h else 0})
    if a:
        info.update({"audio_codec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate", 0) or 0)})
    return info


def _probe_ffmpeg(cfg: dict, path: str | Path) -> dict:
    import re
    if not Path(path).is_file():
        raise MediaError(f"Không thấy file: {path}")
    proc = subprocess.run([ffmpeg_bin(cfg), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, creationflags=CREATE_NO_WINDOW)
    text = proc.stderr or ""
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    v = re.search(r"Stream #\S+.*?Video: (\w+)[^\n]*?(\d{2,5})x(\d{2,5})[^\n]*", text)
    a = re.search(r"Stream #\S+.*?Audio: (\w+)[^\n]*?(\d+) Hz", text)
    if not v and not a:
        raise MediaError(f"Không đọc được media {Path(path).name}")
    info = {"path": str(path), "duration": round(dur, 3), "has_video": v is not None, "has_audio": a is not None,
            "format": None}
    if v:
        w, h = int(v[2]), int(v[3])
        rot = re.search(r"rotation of (-?\d+)|rotate\s*:\s*(-?\d+)", text)
        if rot and abs(int(rot[1] or rot[2])) in (90, 270):
            w, h = h, w
        fps = re.search(r"(\d+(?:\.\d+)?) fps", v[0])
        info.update({"width": w, "height": h, "video_codec": v[1], "fps": float(fps[1]) if fps else 0.0,
                     "aspect": round(w / h, 4) if h else 0})
    if a:
        info.update({"audio_codec": a[1], "sample_rate": int(a[2])})
    return info


def aspect_ok(info: dict, target: str, tol: float) -> bool:
    tw, _, th = target.partition(":")
    want = float(tw) / float(th)
    return bool(info.get("aspect")) and abs(info["aspect"] - want) / want <= tol


def storyboard(cfg: dict, clip: str | Path, out_jpg: str | Path) -> dict:
    """Grab one frame per second (at t=0.5, 1.5, ...) and tile them into a contact sheet.

    The bundled CapCut FFmpeg has no drawtext, so timestamps go into a sidecar list in reading order.
    """
    info = probe(cfg, clip)
    dur = info["duration"] or 0
    n = max(1, int(math.floor(dur)))  # frames at 0.5 .. n-0.5
    cols = min(cfg["review"]["storyboard_columns"], n)
    rows = math.ceil(n / cols)
    fw = cfg["review"]["storyboard_frame_width"]
    out_jpg = Path(out_jpg)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    vf = f"fps=1,scale={fw}:-2,tile={cols}x{rows}:padding=4:margin=4"
    run([ffmpeg_bin(cfg), "-hide_banner", "-y", "-ss", "0.5", "-i", str(clip), "-vf", vf,
         "-frames:v", "1", "-q:v", "3", str(out_jpg)])
    times = [round(0.5 + i, 2) for i in range(n)]
    grid = [{"index": i + 1, "row": i // cols + 1, "col": i % cols + 1, "t": t} for i, t in enumerate(times)]
    return {"storyboard": str(out_jpg), "columns": cols, "rows": rows, "frames": grid, "probe": info}


def extract_audio(cfg: dict, clip: str | Path, out_wav: str | Path) -> Path:
    run([ffmpeg_bin(cfg), "-hide_banner", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out_wav)])
    return Path(out_wav)


def decode_check(cfg: dict, path: str | Path) -> list[str]:
    """Fully decode the file; return any decoder errors (empty list = clean)."""
    proc = subprocess.run([ffmpeg_bin(cfg), "-hide_banner", "-v", "error", "-i", str(path), "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=CREATE_NO_WINDOW, timeout=900)
    errs = [l for l in (proc.stderr or "").splitlines() if l.strip()]
    if proc.returncode != 0:
        errs.append(f"exit code {proc.returncode}")
    return errs


def verify_output(cfg: dict, path: str | Path, aspect: str = "9:16") -> dict:
    path = Path(path)
    if not path.exists():
        return {"ok": False, "path": str(path), "errors": ["file_missing"]}
    info = probe(cfg, path)
    errors = []
    if not info.get("has_video"):
        errors.append("no_video_stream")
    if not aspect_ok(info, aspect, cfg["review"]["aspect_tolerance"]):
        errors.append(f"aspect_{info.get('width')}x{info.get('height')}_not_{aspect}")
    dec = decode_check(cfg, path)
    if dec:
        errors.append("decode_errors: " + "; ".join(dec[:5]))
    return {"ok": not errors, "path": str(path), "probe": info, "errors": errors}


def pick_h264_encoder(cfg: dict) -> list[str]:
    """Prefer libx264; the CapCut-bundled build only has hardware/MediaFoundation encoders."""
    enc = run([ffmpeg_bin(cfg), "-hide_banner", "-encoders"]).stdout
    if " libx264 " in enc:
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    if " h264_mf " in enc:
        return ["-c:v", "h264_mf", "-b:v", "8M", "-pix_fmt", "nv12"]
    if " h264_nvenc " in enc:
        return ["-c:v", "h264_nvenc", "-b:v", "8M", "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p"]


def make_test_clip(cfg: dict, out: str | Path, seconds: float = 8, w: int = 720, h: int = 1280,
                   freq: int = 440, label_color: str = "0x2266aa") -> Path:
    """Synthetic clip used ONLY by the simulator / tests (never presented as a real generation)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg_bin(cfg), "-hide_banner", "-y",
         "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=24:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:d={seconds}",
         *pick_h264_encoder(cfg), "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)])
    return out


def make_test_sfx(cfg: dict, out: str | Path, seconds: float = 0.6, freq: int = 1200) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg_bin(cfg), "-hide_banner", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:d={seconds}",
         "-c:a", "pcm_s16le", str(out)])
    return out
