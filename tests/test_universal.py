"""Workflow Universal: 8 module, báo lỗi → nâng cấp kỹ năng, nhân vật cần tham chiếu và chặn cảnh thiếu ảnh."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from runner import claude_bridge as bridge
from runner import universal_skills as pack
from runner.store import Store
from runner.workflows import Workflows, WorkflowError


class UniversalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.home = base / "HanaMedia"
        (base / "user").mkdir()
        self.patch = mock.patch("pathlib.Path.home", return_value=base / "user")
        self.patch.start()
        self.flows = Workflows(lambda: self.home)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_created_once_with_eight_modules_and_lessons(self):
        slugs = [w["slug"] for w in self.flows.list()]
        self.assertIn("universal", slugs)
        workflow = self.flows.load("universal")
        self.assertEqual({s["name"] for s in workflow["skills"]},
                         {pack.CONDUCTOR, pack.ANALYZER, pack.STYLE, pack.PLANNER, pack.ASSETS, pack.ROUTER, pack.PROMPTS, pack.QC})
        self.assertEqual(workflow["agents"]["studio"]["skill"], pack.QC)
        lessons = self.home / "workflows" / "universal" / "lessons.md"
        self.assertIn("Luật hiện hành", lessons.read_text(encoding="utf-8"))
        # bản trên máy là bản chính: không ghi đè khi mở lại
        skill = self.home / "workflows" / "universal" / "skills" / pack.PROMPTS / "SKILL.md"
        skill.write_text("ĐÃ SỬA", encoding="utf-8")
        lessons.write_text(lessons.read_text(encoding="utf-8") + "- [2026-11-23] [prompt] Triệu chứng: thừa con | Luật: đếm\n",
                           encoding="utf-8")
        self.flows.list()
        self.assertEqual(skill.read_text(encoding="utf-8"), "ĐÃ SỬA")
        self.assertEqual(len(self.flows.lessons()), 1)
        with self.assertRaises(WorkflowError):
            self.flows.delete("universal")  # workflow mặc định

    def test_old_untouched_skills_are_upgraded_but_edited_ones_are_kept(self):
        self.flows.list()
        folder = self.home / "workflows" / "universal" / "skills"
        old_text, edited = "bản cũ chưa sửa", "người dùng đã sửa tay"
        import hashlib
        conductor, assets = folder / pack.CONDUCTOR / "SKILL.md", folder / pack.ASSETS / "SKILL.md"
        # mô phỏng máy đã có bản v1.1.291 của kỹ năng điều phối (trùng sha cũ) và kỹ năng asset đã sửa tay
        with mock.patch.dict(pack.UPGRADES, {f"{pack.CONDUCTOR}/SKILL.md": (hashlib.sha256(old_text.encode()).hexdigest()[:16],),
                                              f"{pack.ASSETS}/SKILL.md": (hashlib.sha256(old_text.encode()).hexdigest()[:16],)}):
            conductor.write_text(old_text, encoding="utf-8")
            assets.write_text(edited, encoding="utf-8")
            self.flows.ensure_universal()
        self.assertIn("anh-tham-chieu", conductor.read_text(encoding="utf-8"))
        self.assertEqual(assets.read_text(encoding="utf-8"), edited)
        self.assertIn("ẢNH THAM CHIẾU", self.flows.prompt("universal", "run"))  # lệnh chạy luôn nhắc, dù kỹ năng đã bị sửa tay

    def test_every_skill_reads_the_shared_lessons(self):
        for rel, text in pack.files().items():
            self.assertIn(pack.LESSONS, text, rel)

    def test_error_report_becomes_a_labelled_request_and_apply_prompt_upgrades_skills(self):
        self.flows.list()
        requests = self.flows.report_error("universal", "Thừa một con báo con", "Cảnh 3 · prompt: SCENE 3 …")
        self.assertIn("LỖI VIDEO: Thừa một con báo con", requests[0])
        self.assertIn("Cảnh 3", requests[0])
        prompt = self.flows.prompt("universal", "apply")
        self.assertIn("LỖI VIDEO", prompt)
        self.assertIn(pack.LESSONS, prompt)
        self.assertIn("CHANGELOG.md", prompt)
        self.assertIn("KHÔNG chạy workflow", prompt)
        self.assertNotIn("«LỖI VIDEO»", self.flows.prompt("wildlife", "apply"))
        with self.assertRaises(WorkflowError):
            self.flows.report_error("universal", "  ")


class CharacterNeedsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "t.db")
        self.store.add_account("a@tuan89.site", "", 100)
        self.registry = self.store.characters
        self.png = lambda tag: self.store.add_image(b"\x89PNG\r\n\x1a\n" + tag.encode())["token"]
        scenes = [{"prompt": "SCENE 1 | LEOPARD_MOTHER walks", "characters": ["LEOPARD_MOTHER"]},
                  {"prompt": "SCENE 2 | both", "characters": ["LEOPARD_MOTHER", "croc mother"]},
                  {"prompt": "SCENE 3 | river", "characters": []}]
        self.imported = self.registry.import_workflow(
            "Báo và cá sấu", [{"code": "LEOPARD_MOTHER", "name": "Báo mẹ", "description": "lông vàng"},
                              {"code": "CROC_MOTHER", "name": "Cá sấu mẹ", "description": "da xanh"}], scenes)
        self.store.enqueue([s["prompt"] for s in scenes], 1, uuid.uuid4().hex, None, "text", None, self.imported["scene_ids"])

    def tearDown(self):
        self.temp.cleanup()

    def waiting(self):
        return {j["prompt"][:7]: j for j in self.store.snapshot()["jobs"]}

    def test_import_links_scenes_to_characters_and_lists_needs(self):
        self.assertEqual(len(self.imported["scene_ids"]), 3)
        needs = self.store.snapshot()["character_needs"]
        self.assertEqual({(c["code"], c["scenes"], c["master_token"]) for c in needs},
                         {("LEOPARD_MOTHER", 2, None), ("CROC_MOTHER", 1, None)})

    def test_scene_without_reference_image_is_blocked_until_master_is_attached(self):
        job, _ = self.store.claim(1)
        self.assertEqual(job["prompt"], "SCENE 3 | river")  # chỉ cảnh không có nhân vật được giao
        self.store.finish(job["id"], output="x.mp4")
        self.assertIsNone(self.store.claim(1))  # hai cảnh còn lại thiếu ảnh nhân vật
        blocked = self.waiting()
        self.assertIn("Thiếu ảnh nhân vật", blocked["SCENE 1"]["phase"])
        self.assertIn("LEOPARD_MOTHER", blocked["SCENE 1"]["phase"])
        self.assertIn("CROC_MOTHER", blocked["SCENE 2"]["phase"])
        characters = {c["code"]: c["id"] for c in self.store.snapshot()["character_needs"]}
        asset = self.registry.add_asset(characters["LEOPARD_MOTHER"], self.png("leopard"), "library")
        self.registry.approve_asset(asset)
        self.assertEqual(len(self.registry.attach_waiting(characters["LEOPARD_MOTHER"])), 1)  # cảnh 2 còn thiếu cá sấu
        job, _ = self.store.claim(1)
        self.assertEqual((job["prompt"], len(job["images"])), ("SCENE 1 | LEOPARD_MOTHER walks", 1))  # ảnh đã gắn
        self.store.finish(job["id"], output="y.mp4")
        self.assertIsNone(self.store.claim(1))
        asset = self.registry.add_asset(characters["CROC_MOTHER"], self.png("croc"), "library")
        self.registry.approve_asset(asset)
        self.assertEqual(len(self.registry.attach_waiting(characters["CROC_MOTHER"])), 1)
        job, _ = self.store.claim(1)
        self.assertEqual((job["prompt"], len(job["images"])), ("SCENE 2 | both", 2))

    def test_guard_can_be_switched_off(self):
        self.store.save_setting("character_guard", False)
        job, _ = self.store.claim(1)
        self.assertEqual(job["prompt"], "SCENE 1 | LEOPARD_MOTHER walks")  # vẫn chạy khi tắt chặn
        self.assertFalse(self.store.snapshot()["character_guard"])


    def test_reference_images_are_imported_approved_and_attached(self):
        base = Path(self.temp.name)
        folder = base / "refs"
        folder.mkdir()
        png = lambda tag: b"\x89PNG\r\n\x1a\n" + tag.encode()
        (folder / "leopard_mother_master.png").write_bytes(png("a"))   # hậu tố master → khớp mã
        (folder / "Cá sấu mẹ.png").write_bytes(png("b"))                # khớp tên nhân vật
        (folder / "san-bong.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 10)  # không khớp nhân vật nào
        result = self.registry.import_reference_images([(p, None) for p in sorted(folder.iterdir())])
        self.assertEqual(sorted(result["imported"]), ["CROC_MOTHER", "LEOPARD_MOTHER"])
        self.assertEqual(result["unmatched"], ["san-bong.jpg"])
        self.assertEqual(result["attached"], 2)  # cảnh 1 (1 ảnh) và cảnh 2 (2 ảnh); cảnh 3 không có nhân vật
        self.assertTrue(all(c["master_token"] for c in self.store.snapshot()["character_needs"]))
        images = {j["prompt"][:7]: j["image_count"] for j in self.store.snapshot()["jobs"]}
        self.assertEqual((images["SCENE 1"], images["SCENE 2"], images["SCENE 3"]), (1, 2, 0))

    def test_existing_master_is_not_overridden_and_hint_wins(self):
        base = Path(self.temp.name)
        first = base / "x.png"
        first.write_bytes(b"\x89PNG\r\n\x1a\nx")
        other = base / "anything.png"
        other.write_bytes(b"\x89PNG\r\n\x1a\ny")
        self.registry.import_reference_images([(first, "LEOPARD_MOTHER")])     # mã gợi ý từ characters[].image
        again = self.registry.import_reference_images([(other, "LEOPARD_MOTHER")])
        self.assertEqual((again["imported"], again["unmatched"]), ([], ["anything.png"]))  # đã có Master: giữ nguyên


class BridgeCharactersTests(unittest.TestCase):
    def test_reference_files_follow_json_paths_and_folder_but_stay_inside_hanamedia(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "HanaMedia"
            out = home / "output" / "p"
            (out / "anh-tham-chieu").mkdir(parents=True)
            (out / "anh-tham-chieu" / "BAO_ME.png").write_bytes(b"x")
            (out / "extra.webp").write_bytes(b"x")
            (out / "notes.txt").write_text("không phải ảnh")
            outside = Path(temp) / "secret.png"
            outside.write_bytes(b"x")
            result = out / "hanamedia_p.json"
            characters = [{"code": "BAO_ME", "image": "anh-tham-chieu/BAO_ME.png"}, {"code": "X", "image": "../../../secret.png"}]
            files = bridge.reference_files(result, characters, home)
        self.assertEqual({p.name for p, _ in files}, {"BAO_ME.png", "extra.webp"})  # không lấy ảnh ngoài thư mục, không lấy .txt
        self.assertIn(("BAO_ME.png", "BAO_ME"), [(p.name, c) for p, c in files])

    def test_parse_reads_characters_and_scene_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "output" / "cuoc-truy-duoi"
            out.mkdir(parents=True)
            (out / "hanamedia_cuoc-truy-duoi.json").write_text(json.dumps({
                "characters": [{"code": "BAO_ME", "name": "Báo mẹ", "description": "lông vàng"}, {"name": ""}],
                "scenes": [{"scene": 1, "duration": 8, "prompt": "SCENE 1 …", "characters": ["BAO_ME", "BAO_ME"]},
                           {"scene": 2, "duration": 6, "prompt": "SCENE 2 …"}]}), encoding="utf-8")
            data = bridge.parse(out / "hanamedia_cuoc-truy-duoi.json")
        self.assertEqual([c["code"] for c in data["characters"]], ["BAO_ME"])
        self.assertEqual([s["characters"] for s in data["scenes"]], [["BAO_ME"], []])


if __name__ == "__main__":
    unittest.main()
