"""Short hàng loạt trong tool: tạo lô từ Thư viện (Nhân vật × Sản phẩm), chạy nền Claude/Codex (kịch bản, duyệt) +
Veo trên Flow (Selenium) + dựng FFmpeg / draft CapCut. Trạng thái nằm trong <data>/shorts/jobs/<job_id>/job.json nên
tắt app giữa chừng vẫn chạy tiếp được (cảnh đang SUBMITTING lúc tắt → BLOCKED unknown_submit, không tự gửi lại)."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from . import batch, flow, library
from . import jobs as J
from .config import build_config, jobs_dir, resolve_path
from .jobs import Job, JobError, read_json

MODELS = ["Veo 3.1 - Fast", "Veo 3.1 - Lite", "Veo 3.1 - Quality", "Omni 1.2 Flash"]
SETTINGS_KEY = "short_settings"


class ShortStudio:
    def __init__(self, store, data_dir, home, sounds=None, open_session=None, flow_status=lambda: ""):
        self.store = store
        self.data_dir = Path(data_dir)
        self.home = home                    # () -> thư mục HanaMedia hiện tại
        self.sounds = sounds                # SoundLibrary của tool (SFX theo tên + tag)
        self.open_session = open_session    # (downloads_dir) -> FlowImageSession
        self.flow_status = flow_status      # trạng thái nút đăng nhập Flow của tool
        self.lock = threading.Lock()
        self.thread = None
        self.stopping = False
        self.state = {"running": False, "message": "", "flow": "", "summary": None, "started": None}
        self.recovered = []
        try:
            self.recovered = flow.recover_orphans(self.cfg(), active_token=None)  # app tắt giữa lúc gửi → BLOCKED
        except Exception:
            pass

    # ---- cấu hình ----
    def settings(self):
        saved = self.store.setting(SETTINGS_KEY) or {}
        return {"model": saved.get("model") or "Veo 3.1 - Fast", "max_retries": int(saved.get("max_retries", 2)),
                "export_dir": saved.get("export_dir") or str(Path.home() / "Videos" / "HanaMediaShort"),
                "sfx_dir": saved.get("sfx_dir") or "", "engine": saved.get("engine") or "auto",
                "auto_transition": saved.get("auto_transition", True) is not False,
                "project_url": saved.get("project_url") or ""}

    def save_settings(self, body):
        cur = self.settings()
        if body.get("model") is not None:
            model = str(body["model"]).strip()[:60]
            if not model:
                raise JobError("Chọn mô hình Veo.")
            cur["model"] = model
        if body.get("max_retries") is not None:
            cur["max_retries"] = max(0, min(5, int(body["max_retries"])))
        for key in ("export_dir", "sfx_dir", "project_url"):
            if body.get(key) is not None:
                cur[key] = str(body[key]).strip().strip('"')[:400]
        if cur["project_url"] and not re.match(r"^https://(flow\.google\.com|labs\.google)/", cur["project_url"]):
            raise JobError("Link dự án phải là trang flow.google.com.")
        if body.get("engine") in ("auto", "claude", "codex"):
            cur["engine"] = body["engine"]
        if "auto_transition" in body:
            cur["auto_transition"] = body["auto_transition"] is not False
        self.store.save_setting(SETTINGS_KEY, cur)
        return cur

    def cfg(self):
        s = self.settings()
        cfg = build_config(self.data_dir, self.home(), {
            "export_dir": s["export_dir"], "sfx_dir": s["sfx_dir"],
            "review": {"max_retries_per_scene": s["max_retries"]},
            "flow": {"model": s["model"], "project_url": s["project_url"]},
            "sfx": {"auto_transition": s["auto_transition"]},
            "autopilot": {"engine": s["engine"]}})
        cfg["_downloads"] = str(resolve_path(cfg["downloads_dir"]))
        extra = []
        if self.sounds is not None:
            for item in self.sounds.list():
                try:
                    extra.append({"path": str(self.sounds.path(item["id"])), "name": item["name"], "tags": item["tags"],
                                  "rel": item["name"]})
                except (ValueError, OSError):
                    continue
        cfg["sfx_extra"] = extra
        return cfg

    # ---- thư viện & tạo lô ----
    def library(self):
        cfg = self.cfg()
        try:
            chars, prods = library.list_images(cfg, "character"), library.list_images(cfg, "product")
        except JobError as error:
            return {"characters": [], "products": [], "error": str(error)}
        pick = lambda items: [{"id": i["id"], "name": i["stem"]} for i in items if i["path"]]
        return {"characters": pick(chars), "products": pick(prods)}

    def create(self, name, rows):
        """rows: [{character, product, count, note, brief}] (character/product = media_id hoặc tên ảnh)."""
        cfg = self.cfg()
        name = str(name or "").strip() or time.strftime("lo-%m%d-%H%M")
        if not isinstance(rows, list) or not rows:
            raise JobError("Thêm ít nhất một cặp Nhân vật × Sản phẩm.")
        if len(rows) > 100:
            raise JobError("Tối đa 100 cặp mỗi lô.")
        brief_dir = jobs_dir(cfg) / "_briefs"
        out = []
        for i, row in enumerate(rows, 1):
            brief = str(row.get("brief") or "").strip()
            path = ""
            if brief:
                if len(brief) > 20000:
                    raise JobError(f"Dòng {i}: brief quá dài (tối đa 20.000 ký tự).")
                brief_dir.mkdir(parents=True, exist_ok=True)
                file = brief_dir / f"{library.slug(name)}-{int(time.time())}-{i}.md"
                file.write_text(brief, encoding="utf-8")
                path = str(file)
            out.append({"nhan_vat": row.get("character"), "san_pham": row.get("product"),
                        "so_video": row.get("count") or 1, "ghi_chu": str(row.get("note") or "")[:300], "brief": path})
        return batch.create_rows(cfg, out, name)

    def import_csv(self, name, text):
        import csv
        import io
        rows = [r for r in csv.DictReader(io.StringIO(str(text or "").lstrip("﻿"))) if any((v or "").strip() for v in r.values())]
        if not rows:
            raise JobError("CSV không có dòng nào (cột: nhan_vat,san_pham,so_video,ghi_chu,brief).")
        cfg = self.cfg()
        return batch.create_rows(cfg, rows, name or "lo-csv", source=str(text).encode("utf-8"), source_name="CSV")

    # ---- xem trạng thái ----
    def jobs(self):
        cfg = self.cfg()
        out = []
        for p in sorted(jobs_dir(cfg).glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:300]:
            try:
                d = read_json(p)
            except (OSError, ValueError):
                continue
            b = d.get("batch") or {}
            scenes = []
            for s in sorted(d.get("scenes") or [], key=lambda x: x["order"]):
                last = s["attempts"][-1] if s["attempts"] else {}
                scenes.append({"scene_id": s["scene_id"], "role": s.get("role"), "status": s["status"],
                               "attempts": len(s["attempts"]), "block": s.get("block"),
                               "verdict": last.get("verdict"), "reasons": last.get("reasons") or [],
                               "has_clip": bool(last.get("clip"))})
            out.append({"job_id": d["job_id"], "title": d.get("title") or "", "stage": d["stage"],
                        "batch": b.get("name") or "", "character": b.get("character"), "product": b.get("product"),
                        "brief": bool(d["inputs"].get("brief_provided")), "scenes": scenes,
                        "final": (d.get("output") or {}).get("final"), "simulated": bool(d.get("simulated")),
                        "capcut": ((d.get("output") or {}).get("capcut_draft") or {}).get("draft_name")
                        if isinstance((d.get("output") or {}).get("capcut_draft"), dict) else None,
                        "error": (d.get("autopilot") or {}).get("error"), "updated": d.get("updated_at")})
        return out

    def status(self):
        cfg = self.cfg()
        return {"state": dict(self.state), "block": flow.global_block(cfg), "flow_status": self.flow_status(), "settings": self.settings(), "models": MODELS,
                "jobs": self.jobs(), "recovered": self.recovered, "export_dir": str(resolve_path(cfg["export_dir"]))}

    # ---- thao tác của người dùng ----
    def resolve(self, job_id, scene_id, action, note=""):
        if self.state["running"] and action == "retry" and self.state.get("flow"):
            pass  # vẫn cho phép: cảnh về READY, vòng chạy sẽ nhận ở lượt sau
        return Job(self.cfg(), job_id).resolve(scene_id, action, note)

    def import_clip(self, job_id, scene_id, raw, filename):
        cfg = self.cfg()
        if not raw or len(raw) > 500 * 1024 * 1024:
            raise JobError("File clip trống hoặc lớn hơn 500 MB.")
        tmp = jobs_dir(cfg) / "_import" / f"{int(time.time())}{Path(filename or 'clip.mp4').suffix.lower() or '.mp4'}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(raw)
        try:
            return str(flow.import_clip(cfg, job_id, scene_id, str(tmp)))
        finally:
            tmp.unlink(missing_ok=True)

    def unblock(self):
        flow.clear_global_block(self.cfg())

    def delete(self, job_id):
        job = Job(self.cfg(), job_id)
        data = job.load()
        if any(s["status"] in J.ACTIVE for s in data["scenes"]):
            raise JobError("Job đang có cảnh gửi lên Flow, chưa xóa được.")
        import shutil
        trash = jobs_dir(self.cfg()) / "_deleted" / f"{job_id}-{int(time.time())}"
        trash.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(job.dir), str(trash))  # không xóa hẳn

    def file(self, job_id, kind):
        d = Job(self.cfg(), job_id).load()
        if kind == "final":
            return (d.get("output") or {}).get("final")
        return str(Job(self.cfg(), job_id).dir)

    @staticmethod
    def flow_work(cfg):
        """Còn cảnh READY / CLAIMED / SUBMITTING / GENERATING (việc của Flow) không - cảnh chờ người dùng thì không tính."""
        for p in jobs_dir(cfg).glob("*/job.json"):
            try:
                d = read_json(p)
            except (OSError, ValueError):
                continue
            if d.get("batch") and not d.get("simulated") and any(s["status"] in (J.READY, *J.ACTIVE) for s in d["scenes"]):
                return True
        return False

    # ---- chạy nền ----
    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.status()
            self.stopping = False
            self.state.update(running=True, message="Đang chạy…", flow="", started=time.time())
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()
        return self.status()

    def stop(self):
        self.stopping = True
        self.state["message"] = "Đang dừng sau bước hiện tại…"
        return self.status()

    def loop(self, poll=20):
        from .veo import FlowVeo, Stop
        cfg = self.cfg()
        veo = FlowVeo(cfg, lambda: self.open_session(cfg["_downloads"]),
                      status=lambda text: self.state.update(flow=text), should_stop=lambda: self.stopping)
        try:
            while not self.stopping:
                cfg = self.cfg()
                veo.cfg = cfg
                self.state["message"] = "Claude / Codex: viết kịch bản, duyệt clip, dựng video…"
                summary = batch.step(cfg, None, agents=True)
                self.state["summary"] = {k: len(v) for k, v in summary.items()} | {"needs_user": summary["needs_user"][:20],
                                                                                    "errors": summary["errors"][:20]}
                if self.stopping:
                    break
                task = None
                if summary["generate"] and self.flow_work(cfg):
                    self.state["message"] = "Veo trên Flow: tạo cảnh…"
                    try:
                        task = veo.run_once()
                    except Stop as error:
                        self.state.update(message=f"Đã dừng Flow: {error}", flow="")
                        if flow.global_block(cfg):
                            break  # chặn toàn cục: chờ người dùng gỡ chặn
                flow_work = self.flow_work(cfg)
                pending = sum(len(summary[k]) for k in ("script", "review", "edit")) + (1 if flow_work else 0)
                if not pending and not task:
                    self.state["message"] = "Xong hết các video có thể làm" + (
                        f" · {len(summary['needs_user'])} việc cần bạn xử lý" if summary["needs_user"] else ".")
                    break
                if not task and not summary["script"] and not summary["review"] and not summary["edit"]:
                    for _ in range(poll):  # chỉ còn chờ Flow / người dùng
                        if self.stopping:
                            break
                        time.sleep(1)
        except Exception as error:  # không để luồng chết im lặng
            self.state["message"] = f"Lỗi: {error}"[:400]
        finally:
            veo.close()
            self.state.update(running=False, flow="")
            if self.stopping:
                self.state["message"] = "Đã dừng."
