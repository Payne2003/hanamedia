"""End-to-end tests with real FFmpeg and the real native host process (no Chrome, no Flow, no CapCut UI).

Run:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hana import capcut, flow, media, review, timeline  # noqa: E402
from hana import jobs as J  # noqa: E402
from hana.config import load_config  # noqa: E402
from hana.jobs import Job, JobError  # noqa: E402

BRIEF = """# Sản phẩm: Bình giữ nhiệt HanaCup 500ml
- Giữ nóng 12 giờ
- Giữ lạnh 24 giờ
- Thép không gỉ 304
Giá khuyến mãi: 199.000đ
"""


def script(extra_claim: str | None = None) -> dict:
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
        cfg_file = cls.tmp / "config.json"
        cls.sfx = cls.tmp / "sfx"
        cls.drafts = cls.tmp / "drafts"
        cls.drafts.mkdir()
        cfg_file.write_text(json.dumps({
            "jobs_dir": str(cls.tmp / "jobs"), "downloads_dir": str(cls.tmp / "dl"), "sfx_dir": str(cls.sfx),
            "export_dir": str(cls.tmp / "export"), "review": {"max_retries_per_scene": 1},
            "capcut": {"draft_root": str(cls.drafts), "template_file": str(cls.tmp / "capcut_template.json")},
        }), encoding="utf-8")
        os.environ["HANA_CONFIG"] = str(cfg_file)
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
        os.environ.pop("HANA_CONFIG", None)

    def new_job(self, jid):
        job = Job(self.cfg, jid)
        job.create(self.inp / "char.png", self.inp / "brief.md", self.inp / "prod.png")
        p = self.tmp / f"{jid}_script.json"
        p.write_text(json.dumps(script(), ensure_ascii=False), encoding="utf-8")
        job.set_script(p)
        return job


class TestScript(Base):
    def test_rejects_claim_not_in_brief(self):
        job = Job(self.cfg, "claims")
        job.create(self.inp / "char.png", self.inp / "brief.md", self.inp / "prod.png")
        errs = job.validate_script(script("Giảm 50% cân nặng"))
        self.assertTrue(any("Giảm 50%" in e for e in errs), errs)

    def test_rejects_long_dialogue(self):
        job = Job(self.cfg, "words")
        job.create(self.inp / "char.png", self.inp / "brief.md")
        s = script()
        s["scenes"][0]["dialogue"] = " ".join(["từ"] * 30)
        s["scenes"][0]["reference_images"] = ["character"]
        s["scenes"][1]["reference_images"] = ["character"]
        errs = job.validate_script(s)
        self.assertTrue(any("30 từ" in e for e in errs), errs)


class TestFlowStateMachine(Base):
    def test_claim_presubmit_unknown_never_resent(self):
        job = self.new_job("flowsm")
        t = flow.claim_task(self.cfg, "flowsm")["task"]
        self.assertEqual(t["scene_id"], "s1_hook")
        self.assertEqual(len(t["refs"]), 2)
        self.assertIn("9:16", t["prompt"])
        # only one active scene per job
        self.assertIsNone(flow.claim_task(self.cfg, "flowsm")["task"])
        self.assertTrue(flow.pre_submit(self.cfg, t["token"])["ok"])
        self.assertFalse(flow.pre_submit(self.cfg, t["token"])["ok"])  # second click refused
        # extension crashes mid-submit -> hello from a fresh worker
        changed = flow.recover_orphans(self.cfg, active_token=None)
        self.assertEqual(changed[0]["to"], J.BLOCKED)
        self.assertEqual(Job.scene(job.load(), "s1_hook")["block"]["reason"], "unknown_submit")
        # BLOCKED scene is not handed out again, the next one is not either (job has a blocked scene? -> next READY ok)
        t2 = flow.claim_task(self.cfg, "flowsm")["task"]
        self.assertEqual(t2["scene_id"], "s2_demo")
        flow.blocked(self.cfg, "ui_changed", "test", t2["token"])  # before pre_submit -> back to READY
        self.assertEqual(Job.scene(job.load(), "s2_demo")["status"], J.READY)
        self.assertEqual(Job.scene(job.load(), "s2_demo")["attempts"], [])
        flow.clear_global_block(self.cfg)
        # user checked Flow: the first submission did go through -> wait for clip
        job.resolve("s1_hook", "await-download")
        self.assertEqual(flow.claim_task(self.cfg, "flowsm")["task"]["resume"], True)

    def test_global_block_quota(self):
        self.new_job("quota")
        t = flow.claim_task(self.cfg, "quota")["task"]
        flow.pre_submit(self.cfg, t["token"])
        flow.blocked(self.cfg, "quota", "Hết tín dụng", t["token"])
        self.assertEqual(Job.scene(Job(self.cfg, "quota").load(), "s1_hook")["status"], J.BLOCKED)
        self.assertEqual(flow.claim_task(self.cfg)["blocked"]["reason"], "quota")
        flow.clear_global_block(self.cfg)


class TestNativeHost(Base):
    def _host(self, msgs):
        proc = subprocess.Popen([sys.executable, str(ROOT / "native_host" / "hana_host.py")], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, env={**os.environ})
        out = []
        for m in msgs:
            data = json.dumps(m).encode()
            proc.stdin.write(struct.pack("<I", len(data)) + data)
            proc.stdin.flush()
            n = struct.unpack("<I", proc.stdout.read(4))[0]
            out.append(json.loads(proc.stdout.read(n)))
        proc.stdin.close()
        proc.wait(10)
        proc.stdout.close()
        return out

    def test_protocol_full_cycle(self):
        self.new_job("hostjob")
        r = self._host([{"id": 1, "cmd": "hello"}, {"id": 2, "cmd": "claim_task", "job": "hostjob"}])
        self.assertTrue(r[0]["ok"])
        task = r[1]["task"]
        self.assertEqual(r[1]["id"], 2)
        clip = media.make_test_clip(self.cfg, self.tmp / "dl" / "fake_flow.mp4", seconds=8)
        r = self._host([{"id": 3, "cmd": "pre_submit", "token": task["token"]},
                        {"id": 4, "cmd": "submitted", "token": task["token"], "evidence": {"after_count": 1}},
                        {"id": 5, "cmd": "downloaded", "token": task["token"], "path": str(clip)}])
        self.assertTrue(all(x["ok"] for x in r), r)
        sc = Job.scene(Job(self.cfg, "hostjob").load(), "s1_hook")
        self.assertEqual(sc["status"], J.DOWNLOADED)
        self.assertFalse(clip.exists())  # moved into the job


class TestReviewAndEdit(Base):
    def test_full_pipeline_with_retry_limit_and_capcut_draft(self):
        jid = "e2e"
        job = self.new_job(jid)
        # s2 comes back landscape -> technical failure
        for s in ("s1_hook", "s2_demo", "s3_cta"):
            w, h = (1280, 720) if s == "s2_demo" else (720, 1280)
            c = media.make_test_clip(self.cfg, self.tmp / "sim" / f"{s}.mp4", w=w, h=h)
            job.add_clip(s, c, source="SIMULATED")
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
        # retry only s2: claim gives s2 with the hint, approved clips untouched
        t = flow.claim_task(self.cfg, jid)["task"]
        self.assertEqual(t["scene_id"], "s2_demo")
        self.assertIn("keep vertical", t["prompt"])
        flow.pre_submit(self.cfg, t["token"])
        flow.submitted(self.cfg, t["token"], {"x": 1})
        bad = media.make_test_clip(self.cfg, self.tmp / "sim" / "s2b.mp4", w=1280, h=720)
        flow.downloaded(self.cfg, t["token"], str(bad))
        review.prepare(self.cfg, jid, "s2_demo")
        r = review.apply_verdict(self.cfg, jid, "s2_demo", "RETRY", ["vẫn ngang"])
        self.assertEqual(r["verdict"], "REVIEW_REQUIRED")  # max_retries_per_scene=1 -> 2 attempts max
        with self.assertRaises(JobError):
            job.resolve("s2_demo", "retry")
        # human supplies a good clip by hand
        good = media.make_test_clip(self.cfg, self.tmp / "sim" / "s2c.mp4")
        flow.import_clip(self.cfg, jid, "s2_demo", str(good))
        review.prepare(self.cfg, jid, "s2_demo")
        review.apply_verdict(self.cfg, jid, "s2_demo", "PASS", ["clip tay ok"])
        self.assertEqual(job.load()["stage"], "edit")

        tl = timeline.build_timeline(self.cfg, jid)
        self.assertEqual([c["scene_id"] for c in tl["clips"]], ["s1_hook", "s2_demo", "s3_cta"])
        self.assertAlmostEqual(tl["duration"], 24, delta=0.2)
        tags = sorted(s["tag"] for s in tl["sfx"])
        self.assertEqual(tags, ["ding", "pop", "whoosh", "whoosh", "whoosh"])  # 3 cues + 2 auto transitions
        self.assertEqual(tl["sfx_unmatched"], [])
        pkg = timeline.build_package(self.cfg, jid)
        self.assertEqual(len(list((pkg / "clips").glob("*.mp4"))), 3)
        ver = timeline.render_preview(self.cfg, jid)
        self.assertTrue(ver["ok"], ver)
        self.assertEqual((ver["probe"]["width"], ver["probe"]["height"]), (1080, 1920))

        # CapCut draft from a synthetic template (real template capture is tested on the machine)
        tpl = {"captured_from": "synthetic", "new_version": "test",
               "content": {"id": "", "tracks": [], "materials": {"videos": [], "audios": [], "speeds": []},
                           "canvas_config": {"ratio": "original", "width": 1920, "height": 1080}, "duration": 0,
                           "fps": 30.0},
               "meta": {"draft_materials": [{"type": 0, "value": []}]}, "meta_material_sample": {},
               "sample": {
                   "video": {"track": {"type": "video", "flag": 0, "attribute": 0}, "material_kind": "videos",
                             "material": {"type": "video", "path": ""},
                             "segment": {"clip": {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}}},
                             "refs": [{"kind": "speeds", "obj": {"id": "x", "speed": 1.0}}]},
                   # attribute 1 = muted track (as captured from a real draft) -> builder must un-mute
                   "audio": {"track": {"type": "audio", "flag": 0, "attribute": 1}, "material_kind": "audios",
                             "material": {"type": "extract_music", "path": ""}, "segment": {"track_attribute": 1},
                             "refs": []}}}
        Path(self.cfg["capcut"]["template_file"]).write_text(json.dumps(tpl), encoding="utf-8")
        res = capcut.build_draft(self.cfg, jid)
        d = json.loads((Path(res["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
        v = [t for t in d["tracks"] if t["type"] == "video"][0]
        self.assertEqual(len(v["segments"]), 3)
        self.assertEqual(v["segments"][1]["target_timerange"]["start"], 8_000_000)
        self.assertEqual((d["canvas_config"]["width"], d["canvas_config"]["height"]), (1080, 1920))
        self.assertEqual(sum(len(t["segments"]) for t in d["tracks"] if t["type"] == "audio"), 5)
        self.assertEqual(len(d["materials"]["speeds"]), 3)
        self.assertTrue(all(t["attribute"] == 0 for t in d["tracks"]))
        self.assertTrue(all(s["track_attribute"] == 0 for t in d["tracks"] for s in t["segments"]))
        st = job.load()["capcut"]
        self.assertEqual(st["step"], "open_draft")
        # pretend CapCut exported: register + verify
        r = capcut.register_export(self.cfg, jid, Path(ver["path"]))
        self.assertTrue(r["ok"])
        self.assertEqual(job.load()["capcut"]["step"], "open_draft")  # opening was never confirmed


if __name__ == "__main__":
    unittest.main()
