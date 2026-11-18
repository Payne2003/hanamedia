"""CapCut Desktop module.

Findings on CapCut 9.8 (this machine):
  * The UI is Qt/QML and exposes only 4 top-level windows to Windows UI Automation - no buttons, menus or
    fields. Clicking by control name is therefore impossible; only window focus + keyboard shortcuts work.
  * Drafts are plain JSON (draft_content.json / draft_meta_info.json) under the draft root folder.
So: media import + timeline + SFX are done by WRITING A NEW DRAFT (built from a template captured from one of
your own drafts), opening the draft is a manual click, and Export is attempted with keyboard shortcuts and
confirmed by watching the export folder. Every step is recorded in job.json -> capcut.history; the job stays
on the first step that is not confirmed.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from . import media
from . import config as _config
from .config import capcut_draft_root, capcut_install_dirs, resolve_path
from .jobs import Job, JobError, now_iso, read_json, write_json_atomic

STEPS = ["build_draft", "open_draft", "export", "verify"]
US = 1_000_000
PS_SCRIPTS = {"capcut_keys.ps1": 'param([string]$Keys = "^e;WAIT:4;{ENTER}", [string]$Process = "CapCut")\n# Bring the CapCut main window to the front and send a keyboard sequence (SendKeys syntax, \';\'-separated,\n# "WAIT:n" pauses n seconds). CapCut\'s Qt UI exposes no UIA controls, so shortcuts are the only lever.\nAdd-Type @"\nusing System; using System.Runtime.InteropServices;\npublic static class W { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);\n  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);\n  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }\n"@\n$p = Get-Process -Name $Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1\nif (-not $p) { "NO_WINDOW"; exit 0 }\n[void][W]::ShowWindow($p.MainWindowHandle, 9)\n[void][W]::SetForegroundWindow($p.MainWindowHandle)\nStart-Sleep -Milliseconds 600\nif ([W]::GetForegroundWindow() -ne $p.MainWindowHandle) { "NOT_FOREGROUND"; }\n$sh = New-Object -ComObject WScript.Shell\nforeach ($k in $Keys.Split(\';\')) {\n  if ($k -like \'WAIT:*\') { Start-Sleep -Seconds ([double]$k.Substring(5)); continue }\n  $sh.SendKeys($k); "SENT $k"; Start-Sleep -Milliseconds 400\n}\n"DONE"\n',
              "uia_dump.ps1": 'param([string]$Process = "CapCut", [int]$Depth = 6, [int]$Max = 400)\n# Dump the UI Automation tree of every top-level window owned by $Process as JSON.\nAdd-Type -AssemblyName UIAutomationClient, UIAutomationTypes\n$ae = [System.Windows.Automation.AutomationElement]\n$procs = Get-Process -Name $Process -ErrorAction SilentlyContinue\nif (-not $procs) { @{ ok = $false; error = "process_not_running" } | ConvertTo-Json; exit 0 }\n$ids = @($procs | ForEach-Object { $_.Id })\n$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker\n$out = New-Object System.Collections.ArrayList\nfunction Walk($el, $d) {\n  if ($out.Count -ge $Max -or $d -gt $Depth) { return }\n  try { $c = $el.Current } catch { return }\n  $t = ($c.ControlType.ProgrammaticName -replace \'ControlType\\.\', \'\')\n  $r = $c.BoundingRectangle\n  $item = [ordered]@{ d = $d; type = $t; name = $c.Name; aid = $c.AutomationId; cls = $c.ClassName; enabled = $c.IsEnabled;\n    rect = @([int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height) }\n  [void]$out.Add($item)\n  $ch = $walker.GetFirstChild($el)\n  while ($ch -ne $null) { Walk $ch ($d + 1); $ch = $walker.GetNextSibling($ch) }\n}\n$root = $ae::RootElement\n$all = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)\n$wins = @($all | Where-Object { $ids -contains $_.Current.ProcessId })\nforeach ($w in $wins) { Walk $w 0 }\n[ordered]@{ ok = $true; windows = @($wins | ForEach-Object { $_.Current.Name }); elements = $out } | ConvertTo-Json -Depth 6 -Compress\n'}


def _tool(name: str) -> Path:
    """Script PowerShell đi kèm (nhúng trong mã, ghi ra <data>/shorts/tools/ khi cần)."""
    path = _config.ROOT / "tools" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8-sig") != PS_SCRIPTS[name]:
        path.write_text(PS_SCRIPTS[name], encoding="utf-8-sig")
    return path


def _uid() -> str:
    u = str(uuid.uuid4()).upper()
    # CapCut writes the 3rd/4th groups partly lowercase; any valid UUID string is accepted.
    return u


def _ps(script: Path, *args: str, timeout: int = 120) -> str:
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=media.CREATE_NO_WINDOW)
    return (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------------------------------------- probe
def probe_ui(cfg: dict) -> dict:
    """What can Windows UI Automation see inside CapCut right now?"""
    raw = _ps(_tool("uia_dump.ps1"), "-Process", "CapCut", "-Depth", "25", "-Max", "800")
    try:
        data = json.loads(raw[raw.index("{"):])
    except ValueError:
        return {"ok": False, "error": raw[-500:]}
    els = data.get("elements") or []
    named = [e for e in els if e.get("type") not in ("Window", "Pane") and (e.get("name") or e.get("aid"))]
    caps = {
        "running": data.get("ok", False),
        "windows": data.get("windows"),
        "elements_total": len(els),
        "named_controls": len(named),
        "import_media_by_ui": False, "arrange_timeline_by_ui": False, "add_sfx_by_ui": False,
        "export_by_ui": False,
        "sample": named[:30],
    }
    names = " | ".join((e.get("name") or "") for e in named).lower()
    if named:
        caps["import_media_by_ui"] = any(k in names for k in ("import", "nhập"))
        caps["export_by_ui"] = any(k in names for k in ("export", "xuất"))
    caps["note"] = ("UIA không thấy control nào bên trong CapCut (Qt/QML không xuất accessibility) - dùng draft JSON "
                    "+ phím tắt." if not named else "Có control UIA - có thể mở rộng tự động hoá theo tên.")
    return caps


def environment(cfg: dict) -> dict:
    dirs = capcut_install_dirs()
    root = capcut_draft_root(cfg)
    return {"installed_versions": [d.name for d in dirs], "exe": str(dirs[0] / "CapCut.exe") if dirs else None,
            "draft_root": str(root) if root else None, "draft_root_exists": bool(root and root.exists()),
            "template": str(resolve_path(cfg["capcut"]["template_file"])),
            "template_exists": resolve_path(cfg["capcut"]["template_file"]).exists(),
            "export_watch_dirs": [str(p) for p in export_watch_dirs(cfg)]}


# ------------------------------------------------------------------------------------------ template
def _index_materials(content: dict) -> dict:
    idx = {}
    for kind, items in content.get("materials", {}).items():
        if isinstance(items, list):
            for o in items:
                if isinstance(o, dict) and "id" in o:
                    idx[o["id"]] = (kind, o)
    return idx


def capture_template(cfg: dict, draft_dir: Path) -> Path:
    """Read (never modify) one of your drafts that has a video track and an audio track; keep only the schema."""
    draft_dir = Path(draft_dir)
    content = read_json(draft_dir / "draft_content.json")
    meta = read_json(draft_dir / "draft_meta_info.json")
    idx = _index_materials(content)
    sample = {}
    for ttype in ("video", "audio"):
        tr = next((t for t in content["tracks"] if t["type"] == ttype and t["segments"]), None)
        if not tr:
            raise JobError(f"Draft {draft_dir.name} không có track {ttype} - chọn draft có cả video và audio")
        seg = tr["segments"][0]
        mkind, mat = idx[seg["material_id"]]
        refs = []
        for rid in seg.get("extra_material_refs", []):
            if rid in idx:
                k, o = idx[rid]
                refs.append({"kind": k, "obj": o})
        sample[ttype] = {"track": {k: v for k, v in tr.items() if k != "segments"}, "segment": seg,
                         "material_kind": mkind, "material": mat, "refs": refs}
    skel = copy.deepcopy(content)
    skel["tracks"] = []
    skel["materials"] = {k: ([] if isinstance(v, list) else v) for k, v in skel["materials"].items()}
    for k in ("keyframes",):
        if isinstance(skel.get(k), dict):
            skel[k] = {kk: ([] if isinstance(vv, list) else vv) for kk, vv in skel[k].items()}
    for k in ("relationships", "time_marks", "group_container"):
        if isinstance(skel.get(k), list):
            skel[k] = []
    meta_skel = copy.deepcopy(meta)
    mat0 = None
    for g in meta_skel.get("draft_materials", []):
        if g.get("type") == 0 and g.get("value"):
            mat0 = g["value"][0]
        g["value"] = []
    for k in ("draft_materials_copied_info", "draft_segment_extra_info"):
        if isinstance(meta_skel.get(k), list):
            meta_skel[k] = []
    tpl = {"captured_from": draft_dir.name, "captured_at": now_iso(), "new_version": content.get("new_version"),
           "version": content.get("version"), "content": skel, "meta": meta_skel, "meta_material_sample": mat0,
           "sample": sample}
    # scrub the user's own file paths from the template, and un-mute/unlock: track.attribute / segment.track_attribute
    # is a flag field (1 = muted track in CapCut 9.x). Draft 1117 had its audio track muted, which silenced SFX.
    for s in tpl["sample"].values():
        s["track"]["attribute"] = 0
        s["segment"]["track_attribute"] = 0
        s["material"]["path"] = ""
        for key in ("material_name", "name"):
            if key in s["material"]:
                s["material"][key] = ""
    if mat0:
        mat0.update({"file_Path": "", "extra_info": ""})
    out = resolve_path(cfg["capcut"]["template_file"])
    write_json_atomic(out, tpl)
    return out


def find_template_source(cfg: dict) -> Path | None:
    root = capcut_draft_root(cfg)
    if not root or not root.exists():
        return None
    best = None
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "draft_content.json"
        if not f.exists() or d.name.startswith("HanaShort_"):
            continue
        try:
            c = read_json(f)
        except (OSError, ValueError):
            continue
        types = {t["type"] for t in c.get("tracks", []) if t.get("segments")}
        if {"video", "audio"} <= types:
            best = d
            break
    return best


# ---------------------------------------------------------------------------------------- build draft
def _clone_with_refs(content: dict, sample: dict, rid_map: dict | None = None) -> tuple[dict, dict, list[str]]:
    mat = copy.deepcopy(sample["material"])
    mat["id"] = _uid()
    ref_ids = []
    for r in sample["refs"]:
        o = copy.deepcopy(r["obj"])
        o["id"] = _uid()
        content["materials"].setdefault(r["kind"], []).append(o)
        ref_ids.append(o["id"])
    seg = copy.deepcopy(sample["segment"])
    seg["id"] = _uid()
    seg["material_id"] = mat["id"]
    seg["extra_material_refs"] = ref_ids
    for k in ("common_keyframes", "keyframe_refs"):
        if isinstance(seg.get(k), list):
            seg[k] = []
    return mat, seg, ref_ids


def build_draft(cfg: dict, job_id: str, name: str | None = None) -> dict:
    job = Job(cfg, job_id)
    tl_file = job.dir / "edit" / "timeline.json"
    if not tl_file.exists():
        raise JobError("Chưa có timeline.json - chạy `hana timeline` trước")
    tl = read_json(tl_file)
    tpl_file = resolve_path(cfg["capcut"]["template_file"])
    if not tpl_file.exists():
        src = find_template_source(cfg)
        if not src:
            raise JobError("Chưa có template CapCut. Chạy `hana capcut template --from <thư mục draft có video+audio>`")
        capture_template(cfg, src)
    tpl = read_json(tpl_file)
    root = capcut_draft_root(cfg)
    if not root or not root.exists():
        raise JobError("Không xác định được thư mục draft của CapCut (capcut.draft_root)")
    base = name or f"HanaShort_{job_id}"
    folder = root / base
    k = 2
    while folder.exists():
        folder = root / f"{base}_{k}"
        k += 1
    content = copy.deepcopy(tpl["content"])
    content["id"] = _uid()
    W, H, FPS = tl["canvas"]["width"], tl["canvas"]["height"], tl["canvas"]["fps"]
    content["canvas_config"] = {**content.get("canvas_config", {}), "ratio": "original", "width": W, "height": H}
    content["fps"] = float(FPS)
    content["duration"] = int(round(tl["duration"] * US))
    now_us = int(time.time() * US)
    for k2 in ("create_time", "update_time"):
        if k2 in content:
            content[k2] = int(time.time())
    content["name"] = ""
    content["path"] = ""
    # video track
    vs = tpl["sample"]["video"]
    vtrack = {**copy.deepcopy(vs["track"]), "id": _uid(), "segments": []}
    meta_values = []
    for i, c in enumerate(tl["clips"]):
        mat, seg, _ = _clone_with_refs(content, vs)
        dur_src = media.probe(cfg, c["path"])["duration"]
        mat.update({"path": Path(c["path"]).as_posix(), "duration": int(round(dur_src * US)),
                    "width": c["width"], "height": c["height"], "material_name": Path(c["path"]).name,
                    "has_audio": bool(c["has_audio"]), "type": "video"})
        if "local_material_id" in mat:
            mat["local_material_id"] = str(uuid.uuid4())
        for key in ("aigc_history_id", "aigc_item_id", "request_id", "unique_id"):
            if key in mat and isinstance(mat[key], str):
                mat[key] = ""
        content["materials"].setdefault(vs["material_kind"], []).append(mat)
        length = int(round((c["out"] - c["in"]) * US))
        seg.update({"source_timerange": {"start": int(round(c["in"] * US)), "duration": length},
                    "target_timerange": {"start": int(round(c["timeline_start"] * US)), "duration": length},
                    "volume": c["volume"], "last_nonzero_volume": c["volume"] or 1.0, "render_index": 0})
        if isinstance(seg.get("clip"), dict):
            seg["clip"]["scale"] = {"x": 1.0, "y": 1.0}
            seg["clip"]["transform"] = {"x": 0.0, "y": 0.0}
        vtrack["segments"].append(seg)
        meta_values.append(_meta_material(tpl, c["path"], dur_src, c["width"], c["height"], "video"))
    content["tracks"].append(vtrack)
    # audio tracks: greedy allocation so overlapping SFX go on separate tracks
    asamp = tpl["sample"]["audio"]
    atracks: list[dict] = []
    ends: list[float] = []
    for s in tl["sfx"]:
        dur = float(s["duration"])
        start = float(s["at"])
        length = min(dur, max(0.05, tl["duration"] - start))
        ti = next((j for j, e in enumerate(ends) if e <= start + 1e-3), None)
        if ti is None:
            atracks.append({**copy.deepcopy(asamp["track"]), "id": _uid(), "segments": []})
            ends.append(0.0)
            ti = len(atracks) - 1
        mat, seg, _ = _clone_with_refs(content, asamp)
        mat.update({"path": Path(s["path"]).as_posix(), "duration": int(round(dur * US)),
                    "name": Path(s["path"]).name})
        for key in ("music_id", "local_material_id"):
            if key in mat:
                mat[key] = str(uuid.uuid4())
        if "type" in mat and mat["type"] not in ("extract_music", "music", "sound"):
            mat["type"] = "extract_music"
        content["materials"].setdefault(asamp["material_kind"], []).append(mat)
        seg.update({"source_timerange": {"start": 0, "duration": int(round(length * US))},
                    "target_timerange": {"start": int(round(start * US)), "duration": int(round(length * US))},
                    "volume": s["volume"], "last_nonzero_volume": s["volume"] or 1.0,
                    "track_render_index": ti + 1})
        atracks[ti]["segments"].append(seg)
        ends[ti] = start + length
        meta_values.append(_meta_material(tpl, s["path"], dur, 0, 0, "music"))
    content["tracks"].extend(atracks)
    for tr in content["tracks"]:  # never ship muted/locked tracks, whatever the template says
        tr["attribute"] = 0
        for sg in tr["segments"]:
            sg["track_attribute"] = 0
    # meta
    meta = copy.deepcopy(tpl["meta"])
    meta.update({"draft_fold_path": folder.as_posix(), "draft_id": _uid(), "draft_name": folder.name,
                 "draft_root_path": root.as_posix(), "tm_draft_create": now_us, "tm_draft_modified": now_us,
                 "tm_duration": content["duration"], "draft_cover": "", "draft_removable_storage_device":
                     (root.drive or "")})
    for g in meta.get("draft_materials", []):
        if g.get("type") == 0:
            g["value"] = meta_values
    folder.mkdir(parents=True)
    write_json_atomic(folder / "draft_content.json", content)
    write_json_atomic(folder / "draft_meta_info.json", meta)
    (folder / "draft_settings").write_text(
        f"[General]\ndraft_create_time={int(time.time())}\ndraft_last_edit_time={int(time.time())}\n"
        "real_edit_seconds=0\nreal_edit_keys=0\ncloud_last_modify_platform=windows\n", encoding="utf-8")
    result = {"draft_dir": str(folder), "draft_name": folder.name, "clips": len(tl["clips"]), "sfx": len(tl["sfx"]),
              "audio_tracks": len(atracks), "template_from": tpl.get("captured_from"),
              "template_version": tpl.get("new_version")}
    record_step(cfg, job_id, "build_draft", "auto_ok",
                f"Draft '{folder.name}' đã tạo: {len(tl['clips'])} clip trên track video, {len(tl['sfx'])} SFX trên "
                f"{len(atracks)} track audio", extra=result)
    return result


def _meta_material(tpl: dict, path: str, dur: float, w: int, h: int, metetype: str) -> dict:
    base = copy.deepcopy(tpl.get("meta_material_sample") or {})
    t = int(time.time())
    base.update({"create_time": t, "duration": int(round(dur * US)), "extra_info": Path(path).name,
                 "file_Path": Path(path).as_posix(), "height": h, "width": w, "id": str(uuid.uuid4()),
                 "import_time": t, "import_time_ms": int(time.time() * US), "metetype": metetype, "type": 0,
                 "roughcut_time_range": {"duration": int(round(dur * US)), "start": 0}})
    return base


# ------------------------------------------------------------------------------------- steps & export
def record_step(cfg: dict, job_id: str, step: str, status: str, detail: str, extra: dict | None = None) -> None:
    """status: auto_ok | manual_ok | failed | manual_required"""
    job = Job(cfg, job_id)
    with job.edit() as d:
        cc = d.setdefault("capcut", {"history": []})
        if step == "build_draft" and cc.get("history"):
            # a new draft restarts open/export/verify; keep the old run for the record
            cc.setdefault("previous_runs", []).append(cc["history"])
            cc["history"] = []
            d["output"] = {}
        cc.setdefault("history", []).append({"step": step, "status": status, "detail": detail, "at": now_iso(),
                                             **({"data": extra} if extra else {})})
        done = {h["step"] for h in cc["history"] if h["status"] in ("auto_ok", "manual_ok")}
        cc["step"] = next((s for s in STEPS if s not in done), "done")
        cc["status"] = status
        if extra and "draft_dir" in extra:
            cc["draft"] = extra
        d["stage"] = "capcut" if cc["step"] != "done" else "done"
    job.log("capcut_step", step=step, status=status, detail=detail)


def export_watch_dirs(cfg: dict) -> list[Path]:
    dirs = []
    if cfg["capcut"].get("export_watch_dir"):
        dirs.append(resolve_path(cfg["capcut"]["export_watch_dir"]))
    # CapCut 9.8 on this machine exports to Downloads by default
    dirs += [Path.home() / "Downloads", Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Videos",
             Path.home() / "Videos"]
    out = []
    for d in dirs:
        if d and d.exists() and d not in out:
            out.append(d)
    return out


def _snapshot(dirs: list[Path]) -> dict:
    snap = {}
    for d in dirs:
        for p in d.rglob("*.mp4"):
            try:
                snap[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return snap


def wait_for_export(cfg: dict, before: dict, timeout: int, name_hint: str = "") -> Path | None:
    dirs = export_watch_dirs(cfg)
    end = time.time() + timeout
    while time.time() < end:
        now = _snapshot(dirs)
        new = [Path(p) for p, m in now.items() if p not in before or m > before[p] + 1]
        if name_hint:
            new.sort(key=lambda p: (name_hint.lower() not in p.name.lower(), -p.stat().st_mtime))
        for p in new:
            s1 = p.stat().st_size
            time.sleep(2)
            if p.exists() and p.stat().st_size == s1 and s1 > 0:
                return p
        time.sleep(2)
    return None


def try_export(cfg: dict, job_id: str) -> dict:
    """Focus CapCut and send the export shortcut. The draft must already be OPEN in the editor."""
    job = Job(cfg, job_id)
    data = job.load()
    draft = (data.get("capcut") or {}).get("draft") or {}
    ddir = Path(draft["draft_dir"]) if draft.get("draft_dir") else None
    if not ddir or not (ddir / ".locked").exists():
        # CapCut writes .locked into the draft folder it has open; without it Ctrl+E would export another project
        record_step(cfg, job_id, "export", "manual_required",
                    f"Draft '{draft.get('draft_name')}' chưa mở trong CapCut (không có .locked) - không gửi phím Xuất")
        return {"ok": False, "reason": "draft_not_open", "draft": draft.get("draft_name")}
    before = _snapshot(export_watch_dirs(cfg))
    keys = ";".join(cfg["capcut"]["export_keys"])
    out = _ps(_tool("capcut_keys.ps1"), "-Keys", keys, timeout=120)
    if "NO_WINDOW" in out:
        record_step(cfg, job_id, "export", "manual_required", "CapCut chưa mở hoặc không đưa được lên trước")
        return {"ok": False, "reason": "capcut_not_running", "log": out}
    found = wait_for_export(cfg, before, int(cfg["capcut"]["export_timeout_sec"]), draft.get("draft_name", ""))
    if not found:
        record_step(cfg, job_id, "export", "manual_required",
                    "Đã gửi phím tắt Xuất nhưng không thấy file MP4 mới trong thư mục theo dõi: "
                    + ", ".join(str(d) for d in export_watch_dirs(cfg)))
        return {"ok": False, "reason": "no_export_file", "log": out}
    return register_export(cfg, job_id, found, "auto_ok")


def register_export(cfg: dict, job_id: str, path: Path, status: str = "manual_ok") -> dict:
    job = Job(cfg, job_id)
    export_dir = resolve_path(cfg["export_dir"])
    export_dir.mkdir(parents=True, exist_ok=True)
    final = export_dir / f"{job_id}.mp4"
    k = 2
    while final.exists():
        final = export_dir / f"{job_id}_{k}.mp4"
        k += 1
    import shutil
    shutil.copy2(path, final)
    record_step(cfg, job_id, "export", status, f"CapCut xuất {path} → {final}")
    ver = media.verify_output(cfg, final, cfg["video"]["aspect_ratio"])
    record_step(cfg, job_id, "verify", "auto_ok" if ver["ok"] else "failed",
                "MP4 đọc được, đúng 9:16" if ver["ok"] else "; ".join(ver["errors"]), extra={"verify": ver})
    with job.edit() as d:
        d["output"] = {"final": str(final), "capcut_export": str(path), "verify": ver}
    return {"ok": ver["ok"], "final": str(final), "verify": ver}
