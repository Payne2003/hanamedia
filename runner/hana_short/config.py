"""Cấu hình HanaMedia Short trong tool: build_config() lấy từ cài đặt của tool (thư mục dữ liệu, thư mục HanaMedia);
load_config(file) giữ cho bộ test port từ HMS. Đường dẫn tương đối tính theo ROOT = <data>/shorts."""
from __future__ import annotations

import copy
import glob
import json
import os
from pathlib import Path

ROOT = Path.home() / "HanaMediaShort"  # build_config() đặt lại = <thư mục dữ liệu tool>/shorts

DEFAULTS: dict = {
    # Where jobs/<job_id>/ are stored. Relative paths are resolved against the project root.
    "jobs_dir": "jobs",
    # Chrome's download folder (the extension downloads clips here, the host then moves them into the job).
    "downloads_dir": "flow-downloads",
    # Your sound-effects library (wav/mp3/m4a/ogg). Sub-folder names are used as tags.
    "sfx_dir": "",
    # Final exports and edit packages are copied here.
    "export_dir": str(Path.home() / "Videos" / "HanaMediaShort"),
    # Leave empty to auto-detect (PATH, then the ffmpeg.exe that ships with CapCut).
    "ffmpeg": "",
    "ffprobe": "",
    "video": {
        "aspect_ratio": "9:16",
        "scene_seconds": 8,
        "scenes": ["hook", "demo", "cta"],
        "max_words_per_scene": 20,
        "render_width": 1080,
        "render_height": 1920,
        "render_fps": 30,
    },
    "review": {
        "max_retries_per_scene": 2,
        "min_duration_ratio": 0.85,
        "aspect_tolerance": 0.02,
        "transcript_min_similarity": 0.6,
        "storyboard_columns": 4,
        "storyboard_frame_width": 270,
    },
    "flow": {
        "base_url": "https://flow.google.com/",
        "project_url": "",
        "model": "Veo 3.1 - Fast",
        "duration_label": "8 giây",
        "outputs": "x1",
        "mode": "Thành phần",
        "submit_confirm_timeout_sec": 60,
        "generation_timeout_sec": 900,
        "download_resolution_index": 0,
    },
    "sfx": {
        "default_volume": 0.6,
        "dialogue_volume": 1.0,
        "transition_tag": "whoosh",
        "auto_transition": True,
    },
    "hanamedia": {
        # the HanaMedia tool: its data folder (studio.sqlite3 + media/) and its shared HanaMedia folder
        "data_dir": "",
        "home": str(Path.home() / "HanaMedia"),
        "brief_dir": str(Path.home() / "HanaMedia" / "thu-vien" / "brief"),
    },
    "autopilot": {"claude_cmd": "", "model": "", "agent_timeout_sec": 1800, "poll_sec": 60},
    "capcut": {
        "exe": "",
        "draft_root": "",
        "template_file": "capcut_template.json",
        "export_watch_dir": "",
        "export_timeout_sec": 600,
        "export_keys": ["^e", "WAIT:4", "{ENTER}"],
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def resolve_path(p: str | os.PathLike | None) -> Path | None:
    if not p:
        return None
    p = Path(os.path.expandvars(os.path.expanduser(str(p))))
    return p if p.is_absolute() else (ROOT / p)


def config_path() -> Path:
    env = os.environ.get("HANA_CONFIG")
    return Path(env) if env else ROOT / "config.json"


def load_config(path: Path | None = None) -> dict:
    path = path or config_path()
    user = {}
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg = _deep_merge(DEFAULTS, user)
    cfg["_path"] = str(path)
    return cfg


def build_config(data_dir, home, overrides: dict | None = None) -> dict:
    """Cấu hình cho tool: dữ liệu job ở <data>/shorts, thư viện ảnh đọc từ <data>/studio.sqlite3."""
    global ROOT
    ROOT = Path(data_dir) / "shorts"
    ROOT.mkdir(parents=True, exist_ok=True)
    base = {"hanamedia": {"data_dir": str(data_dir), "home": str(home),
                          "brief_dir": str(Path(home) / "thu-vien" / "brief")}}
    cfg = _deep_merge(_deep_merge(DEFAULTS, base), overrides or {})
    cfg["_path"] = ""
    return cfg


def jobs_dir(cfg: dict) -> Path:
    p = resolve_path(cfg["jobs_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def capcut_install_dirs() -> list[Path]:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps"
    dirs = [Path(d) for d in glob.glob(str(base / "*")) if Path(d, "CapCut.exe").exists()]

    def ver(p: Path):
        try:
            return tuple(int(x) for x in p.name.split("."))
        except ValueError:
            return (0,)

    return sorted(dirs, key=ver, reverse=True)


def capcut_draft_root(cfg: dict) -> Path | None:
    if cfg["capcut"].get("draft_root"):
        return resolve_path(cfg["capcut"]["draft_root"])
    meta = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft" / "root_meta_info.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            root = data.get("draft_root_path")
            if not root:
                for d in data.get("all_draft_store", []):
                    if d.get("draft_root_path"):
                        root = d["draft_root_path"]
                        break
            root = root or data.get("root_path")
            if root:
                return Path(root)
        except (OSError, ValueError):
            pass
    return None
