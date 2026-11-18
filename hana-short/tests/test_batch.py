"""Batch + HanaMedia library integration, with a fake `claude -p` and simulated Flow clips."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hana import batch, library, media  # noqa: E402
from hana import jobs as J  # noqa: E402
from hana.config import load_config  # noqa: E402
from hana.jobs import Job, JobError  # noqa: E402

# same columns as runner/media.py SCHEMA in Payne2003/hanamedia
SCHEMA = """CREATE TABLE media (media_id TEXT PRIMARY KEY, media_type TEXT, source_type TEXT, display_name TEXT,
 original_filename TEXT DEFAULT '', storage_root TEXT DEFAULT '', relative_path TEXT, mime_type TEXT,
 file_size INTEGER DEFAULT 0, width INTEGER, height INTEGER, sha256 TEXT DEFAULT '', category TEXT,
 created_at REAL, updated_at REAL, linked_character_id TEXT, linked_job_id TEXT, linked_scene_id TEXT,
 hidden INTEGER DEFAULT 0)"""


class TestBatch(unittest.TestCase):
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
        os.environ["HANA_CONFIG"] = str(cfg_file)
        os.environ["HANA_JOBS_DIR"] = str(cls.tmp / "jobs")
        os.environ["HANA_CLAUDE_CMD"] = json.dumps([sys.executable, str(ROOT / "tests" / "fake_claude.py")])
        cls.cfg = load_config(cfg_file)
        media.make_test_sfx(cls.cfg, cls.tmp / "sfx" / "whoosh" / "w.wav")
        con = sqlite3.connect(data / "studio.sqlite3")
        con.execute(SCHEMA)
        rows = [("a" * 32, "Linh_nhan_vat.png", "character"), ("b" * 32, "HanaCup 500ml.png", "product"),
                ("c" * 32, "Bear_reference.png", "reference")]
        for mid, name, cat in rows:
            rel = f"media/uploaded/images/{mid}.png"
            media.run([media.ffmpeg_bin(cls.cfg), "-y", "-f", "lavfi", "-i", "testsrc2=s=320x480:d=1",
                       "-frames:v", "1", str(data / rel)])
            con.execute("INSERT INTO media(media_id,media_type,source_type,display_name,relative_path,mime_type,category,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,1)", (mid, "image", "uploaded", name, rel,
                                                                              "image/png", cat))
        con.commit()
        con.close()
        (brief_dir / "hanacup-500ml.md").write_text("# HanaCup\n- Giữ nóng 12 giờ\n", encoding="utf-8")
        cls.csv = cls.tmp / "lo-thu.csv"
        cls.csv.write_text("nhan_vat,san_pham,so_video,ghi_chu,brief\nLinh_nhan_vat,HanaCup 500ml,2,văn phòng,\n",
                           encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for k in ("HANA_CONFIG", "HANA_JOBS_DIR", "HANA_CLAUDE_CMD"):
            os.environ.pop(k, None)

    def test_library_read_only_and_resolve(self):
        self.assertEqual([i["category"] for i in library.list_images(self.cfg, "product")], ["product"])
        self.assertEqual(library.resolve(self.cfg, "product", "hanacup 500ml")["id"], "b" * 32)
        self.assertEqual(library.resolve(self.cfg, "character", "a" * 32)["stem"], "Linh_nhan_vat")
        with self.assertRaises(JobError):
            library.resolve(self.cfg, "product", "Bear_reference")  # wrong category
        with self.assertRaises(sqlite3.OperationalError):  # really read-only
            with library._connect(self.cfg) as con:
                con.execute("DELETE FROM media")

    def test_unknown_product_is_reported(self):
        bad = self.tmp / "bad.csv"
        bad.write_text("nhan_vat,san_pham\nLinh_nhan_vat,Không có\n", encoding="utf-8")
        with self.assertRaises(JobError):
            batch.create(self.cfg, bad)

    def test_image_only_product_blocks_claims_and_numbers(self):
        brief = Path(self.cfg["hanamedia"]["brief_dir"]) / "hanacup-500ml.md"
        saved = brief.read_text(encoding="utf-8")
        brief.unlink()
        try:
            f = self.tmp / "nobrief.csv"
            f.write_text("nhan_vat,san_pham,so_video\nLinh_nhan_vat,HanaCup 500ml,1\n", encoding="utf-8")
            jid = batch.create(self.cfg, f)["new_jobs"][0]["job_id"]
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

    def test_full_autopilot(self):
        r = batch.create(self.cfg, self.csv)
        self.assertEqual(len(r["new_jobs"]), 2)
        self.assertEqual(batch.create(self.cfg, self.csv)["new_jobs"], [])  # same file again -> no duplicates
        jid = r["new_jobs"][0]["job_id"]
        self.assertTrue((Job(self.cfg, jid).dir / "inputs" / "product.png").exists())
        # pass 1: Claude (fake) writes scripts -> generate stage
        s = batch.step(self.cfg, "lo-thu")
        self.assertEqual(len(s["script"]), 2, s)
        self.assertEqual(Job(self.cfg, jid).load()["stage"], "generate")
        s = batch.step(self.cfg, "lo-thu")
        self.assertEqual(len(s["generate"]), 2)  # waiting for Flow
        # Flow (simulated) delivers clips
        from hana.cli import main
        for j in r["new_jobs"]:
            self.assertEqual(main(["simulate-flow", "--job", j["job_id"]]), 0)
        s = batch.step(self.cfg, "lo-thu")  # fake Claude reviews -> PASS
        self.assertEqual(len(s["review"]), 2, s)
        s = batch.step(self.cfg, "lo-thu")  # edit -> final
        self.assertEqual(len(s["edit"]), 2, s)
        out = Job(self.cfg, jid).load()["output"]
        self.assertTrue(Path(out["final"]).exists())
        self.assertTrue(out["verify"]["ok"])
        self.assertEqual((out["verify"]["probe"]["width"], out["verify"]["probe"]["height"]), (1080, 1920))
        st = json.loads((self.tmp / "HanaMedia" / "output" / "_status" / "hana-short.json").read_text(encoding="utf-8"))
        self.assertEqual(st["percent"], 100)
        s = batch.step(self.cfg, "lo-thu")
        self.assertEqual(len(s["done"]), 2)


if __name__ == "__main__":
    unittest.main()
