"""Job store: one folder per job_id, one job.json holding per-scene state.

Folder layout
  jobs/<job_id>/
    job.json                     state (atomic writes, cross-process lock)
    inputs/                      character.*, product.*, brief.md
    script.json                  written by Claude (/hana-short)
    scenes/<scene_id>/attempt_<n>/clip.mp4, storyboard.jpg, review_packet.json, review.json
    approved/<order>_<scene_id>.mp4
    edit/timeline.json, edit_package/, preview.mp4
    log.jsonl                    append-only event log
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import shutil
import time
import unicodedata
import uuid
from pathlib import Path

from .config import jobs_dir

# ---- scene states -----------------------------------------------------------------------------
READY = "READY"                    # script approved, waiting to be sent to Flow
CLAIMED = "CLAIMED"                # extension picked it up, preparing the form (nothing sent yet)
SUBMITTING = "SUBMITTING"          # extension is about to press Generate (point of no return)
GENERATING = "GENERATING"          # Flow confirmed: a new tile appeared
DOWNLOADED = "DOWNLOADED"          # clip is in the job folder, waiting for review
APPROVED = "APPROVED"              # PASS
REVIEW_REQUIRED = "REVIEW_REQUIRED"  # needs a human decision
BLOCKED = "BLOCKED"                # stopped on purpose (unknown submit, login, quota, captcha, UI change)
GEN_FAILED = "GEN_FAILED"          # Flow reported a failed generation

ACTIVE = {CLAIMED, SUBMITTING, GENERATING}

NO_BRIEF_TEXT = """# Sản phẩm: {name}
KHÔNG CÓ BRIEF - chế độ chỉ dùng ảnh tham chiếu.
- Không có tuyên bố nào được phép: không nêu công dụng, thông số, chất liệu, giá, ưu đãi, so sánh, con số.
- Chỉ mô tả những gì NHÌN THẤY trong ảnh sản phẩm (hình dáng, màu, cách cầm/dùng) và cảm xúc của nhân vật.
- CTA chung chung (vd "Thử ngay hôm nay", "Xem chi tiết ở link"), không hứa hẹn kết quả.
"""
SCENE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,60}$")


class JobError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---- locking & atomic IO ----------------------------------------------------------------------
@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 20.0):
    lock = path.with_suffix(path.suffix + ".lock")
    start = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            # stale lock (crashed process) after 60 s
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() - start > timeout:
                raise JobError(f"Không lấy được khoá {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for i in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: reader holds the file briefly
            time.sleep(0.05 * (i + 1))
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").casefold()
    s = re.sub(r"[“”\"'‘’`]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def count_words(s: str) -> int:
    return len([w for w in re.split(r"\s+", s or "") if re.search(r"\w", w)])


# ---- Job --------------------------------------------------------------------------------------
class Job:
    def __init__(self, cfg: dict, job_id: str):
        if not JOB_ID_RE.match(job_id or ""):
            raise JobError(f"job_id không hợp lệ: {job_id!r} (chỉ dùng chữ, số, - và _)")
        self.cfg = cfg
        self.id = job_id
        self.dir = jobs_dir(cfg) / job_id
        self.file = self.dir / "job.json"

    # -- basic io
    def exists(self) -> bool:
        return self.file.exists()

    def load(self) -> dict:
        if not self.exists():
            raise JobError(f"Không có job '{self.id}' trong {self.dir.parent}")
        return read_json(self.file)

    @contextlib.contextmanager
    def edit(self):
        """Read-modify-write under a lock."""
        with file_lock(self.file):
            data = self.load()
            yield data
            data["updated_at"] = now_iso()
            write_json_atomic(self.file, data)

    def log(self, event: str, **kw) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        rec = {"t": now_iso(), "event": event, **kw}
        with open(self.dir / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # -- creation
    def create(self, character: Path, brief: Path | None = None, product: Path | None = None, title: str = "",
               product_name: str = "") -> dict:
        """brief is optional. Without it the job is in image-only mode: the script may make NO product claims."""
        if self.exists():
            raise JobError(f"Job '{self.id}' đã tồn tại")
        if not brief and not product:
            raise JobError("Cần ít nhất ảnh sản phẩm (--product) hoặc brief (--brief)")
        for p in [character] + [x for x in (brief, product) if x]:
            if not Path(p).exists():
                raise JobError(f"Không thấy file: {p}")
        inputs = self.dir / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        ch = inputs / ("character" + Path(character).suffix.lower())
        shutil.copy2(character, ch)
        br = inputs / "brief.md"
        if brief:
            shutil.copy2(brief, br)
        else:
            br.write_text(NO_BRIEF_TEXT.format(name=product_name or Path(product).stem), encoding="utf-8")
        pr = None
        if product:
            pr = inputs / ("product" + Path(product).suffix.lower())
            shutil.copy2(product, pr)
        data = {
            "job_id": self.id,
            "title": title,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "inputs": {"character": str(ch), "brief": str(br), "product": str(pr) if pr else None,
                       "brief_provided": bool(brief), "product_name": product_name or (Path(product).stem if product else "")},
            "stage": "script",
            "scenes": [],
            "edit": {},
            "capcut": {"step": None, "status": None, "history": []},
            "output": {},
            "simulated": False,
        }
        write_json_atomic(self.file, data)
        self.log("job_created", inputs=data["inputs"])
        return data

    # -- script
    def validate_script(self, script: dict) -> list[str]:
        vcfg = self.cfg["video"]
        errs: list[str] = []
        data = self.load()
        brief = norm_text(Path(data["inputs"]["brief"]).read_text(encoding="utf-8-sig"))
        has_brief = data["inputs"].get("brief_provided", True)
        if script.get("aspect_ratio", vcfg["aspect_ratio"]) != vcfg["aspect_ratio"]:
            errs.append(f"aspect_ratio phải là {vcfg['aspect_ratio']}")
        if not (script.get("character_bible") or "").strip():
            errs.append("thiếu character_bible (mô tả nhân vật nhất quán)")
        scenes = script.get("scenes") or []
        if not scenes:
            errs.append("script không có scenes")
        seen = set()
        for i, s in enumerate(scenes, 1):
            sid = s.get("scene_id", "")
            tag = f"cảnh {i} ({sid or '?'})"
            if not SCENE_ID_RE.match(sid):
                errs.append(f"{tag}: scene_id không hợp lệ (a-z0-9_-)")
            if sid in seen:
                errs.append(f"{tag}: scene_id trùng")
            seen.add(sid)
            dur = s.get("duration", vcfg["scene_seconds"])
            if dur != vcfg["scene_seconds"]:
                errs.append(f"{tag}: duration={dur}, cấu hình yêu cầu {vcfg['scene_seconds']}s")
            words = count_words(s.get("dialogue", ""))
            if words > vcfg["max_words_per_scene"]:
                errs.append(f"{tag}: lời thoại {words} từ > giới hạn {vcfg['max_words_per_scene']} cho {dur}s")
            if not (s.get("veo_prompt") or "").strip():
                errs.append(f"{tag}: thiếu veo_prompt")
            crit = s.get("review_criteria") or []
            if len(crit) < 2:
                errs.append(f"{tag}: cần ít nhất 2 review_criteria")
            if not has_brief:
                if s.get("claims"):
                    errs.append(f"{tag}: job không có brief → claims phải để trống (không được nêu tuyên bố sản phẩm)")
                if re.search(r"\d", s.get("dialogue", "")):
                    errs.append(f"{tag}: job không có brief → lời thoại không được có con số (giá, thông số, %…)")
            for c in (s.get("claims", []) or []) if has_brief else []:
                if norm_text(c) not in brief:
                    errs.append(f"{tag}: tuyên bố không có nguyên văn trong brief: {c!r}")
            for r in s.get("reference_images", []) or []:
                if r not in ("character", "product"):
                    errs.append(f"{tag}: reference_images chỉ nhận 'character' hoặc 'product'")
                if r == "product" and not data["inputs"].get("product"):
                    errs.append(f"{tag}: dùng ảnh product nhưng job không có ảnh sản phẩm")
            for cue in s.get("sfx_cues", []) or []:
                at = cue.get("at", -1)
                if not (0 <= float(at) <= dur):
                    errs.append(f"{tag}: sfx_cue at={at} nằm ngoài 0..{dur}")
        return errs

    def set_script(self, script_path: Path) -> dict:
        script = read_json(script_path)
        errs = self.validate_script(script)
        if errs:
            raise JobError("Script chưa hợp lệ:\n- " + "\n- ".join(errs))
        dest = self.dir / "script.json"
        if Path(script_path).resolve() != dest.resolve():
            shutil.copy2(script_path, dest)
        with self.edit() as data:
            old = {s["scene_id"]: s for s in data["scenes"]}
            scenes = []
            for order, s in enumerate(script["scenes"], 1):
                prev = old.get(s["scene_id"])
                if prev and prev["status"] == APPROVED and prev.get("prompt_hash") == _hash(s):
                    prev["order"] = order
                    scenes.append(prev)  # keep approved clip untouched
                    continue
                if prev and prev["status"] in ACTIVE:
                    raise JobError(f"Cảnh {s['scene_id']} đang ở {prev['status']} trên Flow, không đổi script lúc này")
                scenes.append({
                    "scene_id": s["scene_id"], "order": order, "role": s.get("role", ""),
                    "status": READY, "attempts": [], "prompt_hash": _hash(s),
                    "token": None, "block": None, "approved_clip": None,
                })
            data["scenes"] = scenes
            data["stage"] = "generate"
        self.log("script_set", scenes=[s["scene_id"] for s in script["scenes"]])
        return self.load()

    def script(self) -> dict:
        return read_json(self.dir / "script.json")

    def script_scene(self, scene_id: str) -> dict:
        for s in self.script()["scenes"]:
            if s["scene_id"] == scene_id:
                return s
        raise JobError(f"Không có cảnh {scene_id} trong script")

    # -- helpers
    @staticmethod
    def scene(data: dict, scene_id: str) -> dict:
        for s in data["scenes"]:
            if s["scene_id"] == scene_id:
                return s
        raise JobError(f"Không có cảnh {scene_id}")

    def attempt_dir(self, scene_id: str, n: int) -> Path:
        return self.dir / "scenes" / scene_id / f"attempt_{n}"

    def max_attempts(self) -> int:
        return 1 + int(self.cfg["review"]["max_retries_per_scene"])

    def recompute_stage(self, data: dict) -> str:
        st = [s["status"] for s in data["scenes"]]
        if not st:
            stage = "script"
        elif all(x == APPROVED for x in st):
            stage = data["stage"] if data["stage"] in ("edit", "capcut", "verify", "done") else "edit"
        elif any(x == DOWNLOADED for x in st):
            stage = "review"
        else:
            stage = "generate"
        data["stage"] = stage
        return stage

    # -- state transitions used by host / CLI
    def add_clip(self, scene_id: str, src: Path, source: str, move: bool = False, **meta) -> Path:
        """Register a clip for the scene's current attempt and move to DOWNLOADED."""
        with self.edit() as data:
            sc = self.scene(data, scene_id)
            if sc["status"] == APPROVED:
                raise JobError(f"{scene_id} đã được duyệt, không ghi đè clip")
            n = len(sc["attempts"])
            if sc["status"] not in (GENERATING, SUBMITTING, BLOCKED) or n == 0:
                n += 1
                sc["attempts"].append({"n": n, "started_at": now_iso(), "source": source})
            att = sc["attempts"][-1]
            dest = self.attempt_dir(scene_id, att["n"]) / "clip.mp4"
            dest.parent.mkdir(parents=True, exist_ok=True)
            (shutil.move if move else shutil.copy2)(str(src), str(dest))
            att.update({"clip": str(dest), "downloaded_at": now_iso(), "source": source, **meta})
            sc["status"] = DOWNLOADED
            sc["token"] = None
            sc["block"] = None
            self.recompute_stage(data)
        self.log("clip_added", scene=scene_id, attempt=att["n"], source=source, path=str(dest))
        return dest

    def resolve(self, scene_id: str, action: str, note: str = "") -> dict:
        """Human decision for BLOCKED / REVIEW_REQUIRED / GEN_FAILED scenes."""
        with self.edit() as data:
            sc = self.scene(data, scene_id)
            if action == "retry":
                if sc["status"] not in (BLOCKED, REVIEW_REQUIRED, GEN_FAILED, READY):
                    raise JobError(f"{scene_id} đang {sc['status']}, không thể retry")
                if len(sc["attempts"]) >= self.max_attempts() and sc["status"] != BLOCKED:
                    raise JobError(f"{scene_id} đã dùng {len(sc['attempts'])} lượt (tối đa {self.max_attempts()})."
                                   " Tăng review.max_retries_per_scene nếu thật sự muốn tạo thêm.")
                if sc["status"] == BLOCKED and sc["attempts"]:
                    sc["attempts"][-1]["abandoned"] = note or "resolved_by_user"
                sc["status"] = READY
            elif action == "approve":
                last = sc["attempts"][-1] if sc["attempts"] else None
                if not last or not last.get("clip"):
                    raise JobError(f"{scene_id} chưa có clip để duyệt")
                self._approve(data, sc, last, f"manual: {note}")
            elif action == "await-download":
                # user confirms the earlier submission DID reach Flow; wait for the clip (import-clip or extension)
                sc["status"] = GENERATING
            else:
                raise JobError("action phải là retry | approve | await-download")
            sc["block"] = None
            sc["token"] = None
            self.recompute_stage(data)
        self.log("scene_resolved", scene=scene_id, action=action, note=note)
        return self.load()

    def _approve(self, data: dict, sc: dict, att: dict, why: str) -> None:
        dest = self.dir / "approved" / f"{sc['order']:02d}_{sc['scene_id']}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(att["clip"], dest)
        sc["status"] = APPROVED
        sc["approved_clip"] = str(dest)
        sc["approved_attempt"] = att["n"]
        att["verdict"] = att.get("verdict") or "PASS"
        att["approved_reason"] = why


def _hash(scene: dict) -> str:
    import hashlib
    key = json.dumps({k: scene.get(k) for k in ("veo_prompt", "dialogue", "reference_images")}, sort_keys=True,
                     ensure_ascii=False)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def new_token() -> str:
    return uuid.uuid4().hex


def list_jobs(cfg: dict) -> list[dict]:
    out = []
    for p in sorted(jobs_dir(cfg).glob("*/job.json")):
        try:
            d = read_json(p)
            out.append({"job_id": d["job_id"], "stage": d["stage"], "updated_at": d.get("updated_at"),
                        "scenes": {s["scene_id"]: s["status"] for s in d["scenes"]},
                        "simulated": d.get("simulated", False)})
        except (OSError, ValueError, KeyError):
            continue
    return out
