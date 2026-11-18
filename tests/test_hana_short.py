"""HanaMedia Short trong tool (port bộ test HMS: FFmpeg thật, không cần Chrome / Flow / CapCut)."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from runner.hana_short import batch, capcut, flow, library, media, review, timeline
from runner.hana_short import jobs as J
from runner.hana_short.config import load_config
from runner.hana_short.jobs import Job, JobError

HERE = Path(__file__).resolve().parent
BRIEF = """# Sản phẩm: Bình giữ nhiệt HanaCup 500ml
- Giữ nóng 12 giờ
- Giữ lạnh 24 giờ
- Thép không gỉ 304
Giá khuyến mãi: 199.000đ
"""
SCHEMA = """CREATE TABLE media (media_id TEXT PRIMARY KEY, media_type TEXT, source_type TEXT, display_name TEXT,
 original_filename TEXT DEFAULT '', storage_root TEXT DEFAULT '', relative_path TEXT, mime_type TEXT,
 file_size INTEGER DEFAULT 0, width INTEGER, height INTEGER, sha256 TEXT DEFAULT '', category TEXT,
 created_at REAL, updated_at REAL, linked_character_id TEXT, linked_job_id TEXT, linked_scene_id TEXT,
 hidden INTEGER DEFAULT 0)"""


def script(extra_claim=None):
    sc = [
        {"scene_id": "s1_hook", "role": "hook", "duration": 8, "dialogue": "Cà phê sáng vẫn nóng tới chiều, bạn tin không?",
         "veo_prompt": "Medium close-up, the woman from the reference image holds a steel tumbler...",
         "reference_images": ["character", "product"], "claims": ["Giữ nóng 12 giờ"],
         "review_criteria": ["Nhân vật giống ảnh tham chiếu", "Thấy rõ bình HanaCup trong tay"],
         "sfx_cues": [{"at": 0.3, "tag": "whoosh"}]},
        {"scene_id": "s2_demo", "role": "demo", "duration": 8, "dialogue": "Thép không gỉ 304, giữ lạnh tới 24 giờ.",
         "veo_prompt": "She pours ice water into the tumbler...", "reference_images": ["character", "product"],
         "claims": ["Thép không gỉ 304", "Giữ lạnh 24 giờ"],
         "review_criteria": ["Hành động rót nước rõ", "Bình giữ đúng màu"], "sfx_cues": [{"at": 4.0, "tag": "pop"}]},
        {"scene_id": "s3_cta", "role": "cta", "duration": 8, "dialogue": "Chỉ 199.000đ, đặt ngay hôm nay!",
         "veo_prompt": "She smiles to camera, raises the tumbler...", "reference_images": ["character"],
         "claims": ["199.000đ"], "review_criteria": ["Nhìn thẳng camera", "Kết thúc dứt khoát"],
         "sfx_cues": [{"at": 6.5, "tag": "ding"}]},
    ]
    if extra_claim:
        sc[0]["claims"].append(extra_claim)
    return {"title": "HanaCup test", "aspect_ratio": "9:16", "language": "Vietnamese",
            "character_bible": "Vietnamese woman, late 20s, shoulder-length black hair, white blouse", "scenes": sc}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="hana_test_"))
        cls.sfx = cls.tmp / "sfx"
        (cls.tmp / "drafts").mkdir()
        cfg_file = cls.tmp / "config.json"
        cfg_file.write_text(json.dumps({
            "jobs_dir": str(cls.tmp / "jobs"), "downloads_dir": str(cls.tmp / "dl"), "sfx_dir": str(cls.sfx),
            "export_dir": str(cls.tmp / "export"), "review": {"max_retries_per_scene": 1},
            "ffprobe": str(cls.tmp / "khong-co-ffprobe"),  # như máy khách: tool không kèm ffprobe → đọc bằng ffmpeg
            "capcut": {"draft_root": str(cls.tmp / "drafts"), "template_file": str(cls.tmp / "capcut_template.json")},
        }), encoding="utf-8")
        cls.cfg = load_config(cfg_file)
        inp = cls.tmp / "in"
        inp.mkdir()
        media.run([media.ffmpeg_bin(cls.cfg), "-y", "-f", "lavfi", "-i", "testsrc2=s=512x768:d=1", "-frames:v", "1",
                   str(inp / "char.png")])
        shutil.copy(inp / "char.png", inp / "prod.png")
        (inp / "brief.md").write_text(BRIEF, encoding="utf-8")
        cls.inp = inp
        for tag, f in (("whoosh", 900), ("pop", 1500), ("ding", 2000)):
            media.make_test_sfx(cls.cfg, cls.sfx / tag / f"{tag}_a.wav", freq=f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def new_job(self, jid):
        job = Job(self.cfg, jid)
        job.create(self.inp / "char.png", self.inp / "brief.md", self.inp / "prod.png")
        p = self.tmp / f"{jid}_script.json"
        p.write_text(json.dumps(script(), ensure_ascii=False), encoding="utf-8")
        job.set_script(p)
        return job


class ScriptTests(Base):
    def test_rejects_claim_not_in_brief_and_long_dialogue(self):
        job = Job(self.cfg, "claims")
        job.create(self.inp / "char.png", self.inp / "brief.md", self.inp / "prod.png")
        errs = job.validate_script(script("Giảm 50% cân nặng"))
        self.assertTrue(any("Giảm 50%" in e for e in errs), errs)
        s = script()
        s["scenes"][0]["dialogue"] = " ".join(["từ"] * 30)
        self.assertTrue(any("30 từ" in e for e in job.validate_script(s)))


class FlowStateMachineTests(Base):
    def test_claim_presubmit_unknown_never_resent(self):
        job = self.new_job("flowsm")
        t = flow.claim_task(self.cfg, "flowsm")["task"]
        self.assertEqual(t["scene_id"], "s1_hook")
        self.assertEqual(len(t["refs"]), 2)
        self.assertIn("9:16", t["prompt"])
        self.assertIsNone(flow.claim_task(self.cfg, "flowsm")["task"])  # một cảnh đang làm mỗi job
        self.assertTrue(flow.pre_submit(self.cfg, t["token"])["ok"])
        self.assertFalse(flow.pre_submit(self.cfg, t["token"])["ok"])  # bấm lần hai bị từ chối
        changed = flow.recover_orphans(self.cfg, active_token=None)  # app tắt giữa lúc bấm Tạo
        self.assertEqual(changed[0]["to"], J.BLOCKED)
        self.assertEqual(Job.scene(job.load(), "s1_hook")["block"]["reason"], "unknown_submit")
        t2 = flow.claim_task(self.cfg, "flowsm")["task"]
        self.assertEqual(t2["scene_id"], "s2_demo")
        flow.blocked(self.cfg, "ui_changed", "test", t2["token"])  # trước pre_submit → về READY
        self.assertEqual(Job.scene(job.load(), "s2_demo")["status"], J.READY)
        flow.clear_global_block(self.cfg)
        job.resolve("s1_hook", "await-download")
        self.assertTrue(flow.claim_task(self.cfg, "flowsm")["task"]["resume"])

    def test_global_block_quota(self):
        self.new_job("quota")
        t = flow.claim_task(self.cfg, "quota")["task"]
        flow.pre_submit(self.cfg, t["token"])
        flow.blocked(self.cfg, "quota", "Hết tín dụng", t["token"])
        self.assertEqual(Job.scene(Job(self.cfg, "quota").load(), "s1_hook")["status"], J.BLOCKED)
        self.assertEqual(flow.claim_task(self.cfg)["blocked"]["reason"], "quota")
        flow.clear_global_block(self.cfg)


class FakeDriver:
    """Giả trang Flow: trả lời các hàm window.__hana theo kịch bản của test."""
    def __init__(self, test, confirm=True):
        self.test, self.confirm, self.calls, self.current_url = test, confirm, [], "https://flow.google.com/project/1"

    def set_script_timeout(self, s):
        pass

    def execute_script(self, js, *a):
        return "complete"

    def execute_async_script(self, js, *args):
        if "mainWorldUpload" in js and "window.__hana[name]" not in js:
            self.calls.append("upload")
            self.chips = len(args[0])
            return {"ok": True}
        name, a = args[0], args[1]
        self.calls.append(name)
        res = {"state": {"login": False, "captcha": False, "quota": False, "onProject": True},
               "applySettings": {"ok": True, "summary": "Video crop_9_16 x1", "cost": "20 tín dụng"},
               "promptChips": getattr(self, "chips", 0), "setPrompt": {"ok": True}, "snapshot": {"count": 0, "keys": []},
               "watch": {"state": "done", "tile": {"key": "m1"}}, "download": {"ok": True, "option": "Kích thước gốc"}}
        if name == "clickGenerateAndConfirm":
            sc = Job.scene(Job(self.test.cfg, "veo").load(), "s1_hook")
            self.test.assertEqual(sc["status"], J.SUBMITTING)  # đã ghi SUBMITTING trước khi bấm
            return {"ok": True, "result": {"ok": True, "evidence": {"after_count": 1, "url": self.current_url}}
                    if self.confirm else {"ok": False, "unknown": True, "reason": "no_evidence", "detail": "x"}}
        if name == "download":
            media.make_test_clip(self.test.cfg, Path(self.test.cfg["_downloads"]) / "flow_clip.mp4", seconds=8)
        return {"ok": True, "result": res[name]}


class VeoDriverTests(Base):
    def test_selenium_sequence_and_unknown_submit(self):
        from runner.hana_short.veo import FlowVeo, Stop
        self.cfg["_downloads"] = str(self.tmp / "dl")
        Path(self.cfg["_downloads"]).mkdir(exist_ok=True)
        self.new_job("veo")
        session = type("S", (), {})()
        session.driver = FakeDriver(self)
        session.close = lambda: None
        veo = FlowVeo(self.cfg, lambda: session)
        task = veo.run_once("veo")
        self.assertEqual(task["scene_id"], "s1_hook")
        self.assertEqual(Job.scene(Job(self.cfg, "veo").load(), "s1_hook")["status"], J.DOWNLOADED)
        order = session.driver.calls
        self.assertLess(order.index("upload"), order.index("clickGenerateAndConfirm"))
        # không có bằng chứng Flow nhận → BLOCKED unknown_submit, không gửi lại
        session.driver = FakeDriver(self, confirm=False)
        with self.assertRaises(Stop):
            veo.run_once("veo")
        sc = Job.scene(Job(self.cfg, "veo").load(), "s2_demo")
        self.assertEqual((sc["status"], sc["block"]["reason"]), (J.BLOCKED, "unknown_submit"))
        self.assertEqual(flow.claim_task(self.cfg, "veo")["task"]["scene_id"], "s3_cta")


class ReviewAndEditTests(Base):
    def test_full_pipeline_with_retry_limit_and_capcut_draft(self):
        jid = "e2e"
        job = self.new_job(jid)
        for s in ("s1_hook", "s2_demo", "s3_cta"):
            w, h = (1280, 720) if s == "s2_demo" else (720, 1280)
            job.add_clip(s, media.make_test_clip(self.cfg, self.tmp / "sim" / f"{s}.mp4", w=w, h=h), source="SIMULATED")
        pk = {p["scene_id"]: p for p in review.prepare(self.cfg, jid)}
        self.assertTrue(Path(pk["s1_hook"]["storyboard"]).exists())
        self.assertEqual(len(pk["s1_hook"]["grid"]["frames"]), 8)
        self.assertFalse(pk["s2_demo"]["technical_ok"])
        with self.assertRaises(JobError):
            review.apply_verdict(self.cfg, jid, "s2_demo", "PASS", ["looks fine"])
        review.apply_verdict(self.cfg, jid, "s1_hook", "PASS", ["nhân vật khớp"])
        review.apply_verdict(self.cfg, jid, "s3_cta", "PASS", ["ok"])
        r = review.apply_verdict(self.cfg, jid, "s2_demo", "RETRY", ["khung ngang 16:9"], "keep vertical 9:16")
        self.assertEqual(r["status"], J.READY)
        t = flow.claim_task(self.cfg, jid)["task"]
        self.assertEqual(t["scene_id"], "s2_demo")  # chỉ tạo lại cảnh lỗi
        self.assertIn("keep vertical", t["prompt"])
        flow.pre_submit(self.cfg, t["token"])
        flow.submitted(self.cfg, t["token"], {"x": 1})
        flow.downloaded(self.cfg, t["token"], str(media.make_test_clip(self.cfg, self.tmp / "sim" / "s2b.mp4", w=1280, h=720)))
        review.prepare(self.cfg, jid, "s2_demo")
        r = review.apply_verdict(self.cfg, jid, "s2_demo", "RETRY", ["vẫn ngang"])
        self.assertEqual(r["verdict"], "REVIEW_REQUIRED")  # hết lượt
        with self.assertRaises(JobError):
            job.resolve("s2_demo", "retry")
        flow.import_clip(self.cfg, jid, "s2_demo", str(media.make_test_clip(self.cfg, self.tmp / "sim" / "s2c.mp4")))
        review.prepare(self.cfg, jid, "s2_demo")
        review.apply_verdict(self.cfg, jid, "s2_demo", "PASS", ["clip tay ok"])
        self.assertEqual(job.load()["stage"], "edit")
        tl = timeline.build_timeline(self.cfg, jid)
        self.assertEqual([c["scene_id"] for c in tl["clips"]], ["s1_hook", "s2_demo", "s3_cta"])
        self.assertAlmostEqual(tl["duration"], 24, delta=0.2)
        self.assertEqual(sorted(s["tag"] for s in tl["sfx"]), ["ding", "pop", "whoosh", "whoosh", "whoosh"])
        pkg = timeline.build_package(self.cfg, jid)
        self.assertEqual(len(list((pkg / "clips").glob("*.mp4"))), 3)
        ver = timeline.render_preview(self.cfg, jid)
        self.assertTrue(ver["ok"], ver)
        self.assertEqual((ver["probe"]["width"], ver["probe"]["height"]), (1080, 1920))
        tpl = {"captured_from": "synthetic", "new_version": "test",
               "content": {"id": "", "tracks": [], "materials": {"videos": [], "audios": [], "speeds": []},
                           "canvas_config": {"ratio": "original", "width": 1920, "height": 1080}, "duration": 0, "fps": 30.0},
               "meta": {"draft_materials": [{"type": 0, "value": []}]}, "meta_material_sample": {},
               "sample": {
                   "video": {"track": {"type": "video", "flag": 0, "attribute": 0}, "material_kind": "videos",
                             "material": {"type": "video", "path": ""},
                             "segment": {"clip": {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}}},
                             "refs": [{"kind": "speeds", "obj": {"id": "x", "speed": 1.0}}]},
                   "audio": {"track": {"type": "audio", "flag": 0, "attribute": 1}, "material_kind": "audios",
                             "material": {"type": "extract_music", "path": ""}, "segment": {"track_attribute": 1},
                             "refs": []}}}
        Path(self.cfg["capcut"]["template_file"]).write_text(json.dumps(tpl), encoding="utf-8")
        res = capcut.build_draft(self.cfg, jid)
        d = json.loads((Path(res["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
        v = [t for t in d["tracks"] if t["type"] == "video"][0]
        self.assertEqual(len(v["segments"]), 3)
        self.assertEqual(v["segments"][1]["target_timerange"]["start"], 8_000_000)
        self.assertEqual(sum(len(t["segments"]) for t in d["tracks"] if t["type"] == "audio"), 5)
        self.assertTrue(all(t["attribute"] == 0 for t in d["tracks"]))  # không track nào bị tắt tiếng
        self.assertTrue(all(s["track_attribute"] == 0 for t in d["tracks"] for s in t["segments"]))
        r = capcut.register_export(self.cfg, jid, Path(ver["path"]))
        self.assertTrue(r["ok"])


class BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="hana_batch_"))
        data = cls.tmp / "hm_data"
        (data / "media" / "uploaded" / "images").mkdir(parents=True)
        home = cls.tmp / "HanaMedia"
        (home / "output" / "_status").mkdir(parents=True)
        brief_dir = home / "thu-vien" / "brief"
        brief_dir.mkdir(parents=True)
        cfg_file = cls.tmp / "config.json"
        cfg_file.write_text(json.dumps({
            "jobs_dir": str(cls.tmp / "jobs"), "sfx_dir": str(cls.tmp / "sfx"), "export_dir": str(cls.tmp / "export"),
            "hanamedia": {"data_dir": str(data), "home": str(home), "brief_dir": str(brief_dir)},
            "capcut": {"draft_root": str(cls.tmp / "drafts"), "template_file": str(cls.tmp / "nope.json")},
        }), encoding="utf-8")
        os.environ["HANA_AGENT_CMD"] = json.dumps([sys.executable, str(HERE / "hana_fake_agent.py")])
        cls.cfg = load_config(cfg_file)
        media.make_test_sfx(cls.cfg, cls.tmp / "sfx" / "whoosh" / "w.wav")
        con = sqlite3.connect(data / "studio.sqlite3")
        con.execute(SCHEMA)
        for mid, name, cat in (("a" * 32, "Linh_nhan_vat.png", "character"), ("b" * 32, "HanaCup 500ml.png", "product"),
                               ("c" * 32, "Bear_reference.png", "reference")):
            rel = f"media/uploaded/images/{mid}.png"
            media.run([media.ffmpeg_bin(cls.cfg), "-y", "-f", "lavfi", "-i", "testsrc2=s=320x480:d=1", "-frames:v", "1",
                       str(data / rel)])
            con.execute("INSERT INTO media(media_id,media_type,source_type,display_name,relative_path,mime_type,category,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,1)", (mid, "image", "uploaded", name, rel, "image/png", cat))
        con.commit()
        con.close()
        (brief_dir / "hanacup-500ml.md").write_text("# HanaCup\n- Giữ nóng 12 giờ\n", encoding="utf-8")
        cls.csv = cls.tmp / "lo-thu.csv"
        cls.csv.write_text("nhan_vat,san_pham,so_video,ghi_chu,brief\nLinh_nhan_vat,HanaCup 500ml,2,văn phòng,\n",
                           encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("HANA_AGENT_CMD", None)

    def test_library_read_only_and_resolve(self):
        self.assertEqual([i["category"] for i in library.list_images(self.cfg, "product")], ["product"])
        self.assertEqual(library.resolve(self.cfg, "product", "hanacup 500ml")["id"], "b" * 32)
        with self.assertRaises(JobError):
            library.resolve(self.cfg, "product", "Bear_reference")  # sai nhóm ảnh
        with self.assertRaises(sqlite3.OperationalError):  # mở chỉ đọc thật
            with library._connect(self.cfg) as con:
                con.execute("DELETE FROM media")

    def test_image_only_product_blocks_claims_and_numbers(self):
        rows = [{"nhan_vat": "a" * 32, "san_pham": "b" * 32, "so_video": 1, "brief": ""}]
        brief = Path(self.cfg["hanamedia"]["brief_dir"]) / "hanacup-500ml.md"
        saved = brief.read_text(encoding="utf-8")
        brief.unlink()
        try:
            jid = batch.create_rows(self.cfg, rows, "nobrief")["new_jobs"][0]["job_id"]
        finally:
            brief.write_text(saved, encoding="utf-8")
        job = Job(self.cfg, jid)
        self.assertFalse(job.load()["inputs"]["brief_provided"])
        sc = lambda dialogue, claims: {"title": "t", "aspect_ratio": "9:16", "character_bible": "x", "scenes": [
            {"scene_id": "s1_hook", "duration": 8, "dialogue": dialogue, "veo_prompt": "p",
             "reference_images": ["character", "product"], "claims": claims, "review_criteria": ["a", "b"]}]}
        errs = job.validate_script(sc("Chỉ 199 nghìn thôi!", ["Giữ nóng 12 giờ"]))
        self.assertTrue(any("claims phải để trống" in e for e in errs), errs)
        self.assertTrue(any("con số" in e for e in errs), errs)
        self.assertEqual(job.validate_script(sc("Nhìn xinh quá, thử ngay hôm nay!", [])), [])
        self.assertIn("KHÔNG CÓ BRIEF", batch.script_prompt(self.cfg, job, job.load()))

    def test_full_autopilot(self):
        r = batch.create(self.cfg, self.csv)
        self.assertEqual(len(r["new_jobs"]), 2)
        self.assertEqual(batch.create(self.cfg, self.csv)["new_jobs"], [])  # cùng file → không tạo trùng
        jid = r["new_jobs"][0]["job_id"]
        s = batch.step(self.cfg, "lo-thu")  # agent (giả) viết kịch bản → tool kiểm tra → generate
        self.assertEqual(len(s["script"]), 2, s)
        self.assertEqual(Job(self.cfg, jid).load()["stage"], "generate")
        self.assertIn("KHÁC hẳn", batch.script_prompt(self.cfg, Job(self.cfg, r["new_jobs"][1]["job_id"]),
                                                      Job(self.cfg, r["new_jobs"][1]["job_id"]).load()))  # khác góc
        for j in r["new_jobs"]:  # Flow mô phỏng giao clip
            job = Job(self.cfg, j["job_id"])
            for sc in job.load()["scenes"]:
                job.add_clip(sc["scene_id"], media.make_test_clip(self.cfg, self.tmp / "sim" / f"{j['job_id']}{sc['scene_id']}.mp4"),
                             source="SIMULATED", move=True)
        s = batch.step(self.cfg, "lo-thu")  # agent (giả) duyệt → PASS
        self.assertEqual(len(s["review"]), 2, s)
        s = batch.step(self.cfg, "lo-thu")  # dựng → thành phẩm
        self.assertEqual(len(s["edit"]), 2, s)
        out = Job(self.cfg, jid).load()["output"]
        self.assertTrue(Path(out["final"]).exists())
        self.assertEqual((out["verify"]["probe"]["width"], out["verify"]["probe"]["height"]), (1080, 1920))
        st = json.loads((self.tmp / "HanaMedia" / "output" / "_status" / "hana-short.json").read_text(encoding="utf-8"))
        self.assertEqual(st["percent"], 100)

    def test_bad_script_is_sent_back_with_errors(self):
        rows = [{"nhan_vat": "a" * 32, "san_pham": "b" * 32, "so_video": 1, "ghi_chu": "BROKEN-ONCE"}]
        jid = batch.create_rows(self.cfg, rows, "sua-loi")["new_jobs"][0]["job_id"]
        s = batch.step(self.cfg, "sua-loi")
        self.assertEqual(s["script"], [])
        self.assertIn("30 từ", Job(self.cfg, jid).load()["autopilot"]["script_errors"][0])
        s = batch.step(self.cfg, "sua-loi")  # lần hai gửi kèm lỗi → sửa được
        self.assertEqual(s["script"], [jid])


class ServiceTests(unittest.TestCase):
    def test_ui_rows_with_brief_text_and_status(self):
        from runner.store import Store
        from runner.hana_short.service import ShortStudio
        tmp = Path(tempfile.mkdtemp(prefix="hana_svc_"))
        try:
            store = Store(tmp / "studio.sqlite3")
            media_dir = tmp / "media" / "uploaded" / "images"
            media_dir.mkdir(parents=True)
            con = sqlite3.connect(tmp / "studio.sqlite3")
            con.execute("DROP TABLE IF EXISTS media")
            con.execute(SCHEMA)
            for mid, name, cat in (("a" * 32, "Linh.png", "character"), ("b" * 32, "Cốc.png", "product")):
                media.run([media.ffmpeg_bin({"ffmpeg": ""}), "-y", "-f", "lavfi", "-i", "testsrc2=s=320x480:d=1",
                           "-frames:v", "1", str(media_dir / f"{mid}.png")])
                con.execute("INSERT INTO media(media_id,media_type,source_type,display_name,relative_path,mime_type,category,"
                            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,1)",
                            (mid, "image", "uploaded", name, f"media/uploaded/images/{mid}.png", "image/png", cat))
            con.commit()
            con.close()
            svc = ShortStudio(store, tmp, lambda: tmp / "HanaMedia")
            lib = svc.library()
            self.assertEqual([i["name"] for i in lib["characters"]], ["Linh"])
            made = svc.create("Lô thử", [{"character": "a" * 32, "product": "b" * 32, "count": 2, "note": "bếp",
                                          "brief": "- Giữ nóng 12 giờ"}])
            self.assertEqual(len(made["new_jobs"]), 2)
            st = svc.status()
            self.assertEqual({j["stage"] for j in st["jobs"]}, {"script"})
            self.assertTrue(all(j["brief"] for j in st["jobs"]))
            self.assertTrue(st["settings"]["model"])
            svc.save_settings({"model": "Veo 3.1 - Lite", "max_retries": 1})
            self.assertEqual(svc.cfg()["flow"]["model"], "Veo 3.1 - Lite")
            with self.assertRaises(JobError):
                svc.save_settings({"project_url": "https://example.com/x"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
