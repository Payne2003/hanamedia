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
        self.assertEqual(self.registry.attach_waiting(characters["LEOPARD_MOTHER"]), 1)  # cảnh 2 còn thiếu cá sấu
        job, _ = self.store.claim(1)
        self.assertEqual((job["prompt"], len(job["images"])), ("SCENE 1 | LEOPARD_MOTHER walks", 1))  # ảnh đã gắn
        self.store.finish(job["id"], output="y.mp4")
        self.assertIsNone(self.store.claim(1))
        asset = self.registry.add_asset(characters["CROC_MOTHER"], self.png("croc"), "library")
        self.registry.approve_asset(asset)
        self.assertEqual(self.registry.attach_waiting(characters["CROC_MOTHER"]), 1)
        job, _ = self.store.claim(1)
        self.assertEqual((job["prompt"], len(job["images"])), ("SCENE 2 | both", 2))

    def test_guard_can_be_switched_off(self):
        self.store.save_setting("character_guard", False)
        job, _ = self.store.claim(1)
        self.assertEqual(job["prompt"], "SCENE 1 | LEOPARD_MOTHER walks")  # vẫn chạy khi tắt chặn
        self.assertFalse(self.store.snapshot()["character_guard"])


class BridgeCharactersTests(unittest.TestCase):
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
