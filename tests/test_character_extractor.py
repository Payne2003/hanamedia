import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import AppServer
from runner import characters as chars
from runner import gemini_web
from runner.store import Store

SCENES = [
    {"setting": "Vách đá", "camera": "Toàn cảnh", "behavior": "Đứng yên", "action": "Dê đực giữ thăng bằng"},
    {"setting": "Sườn núi", "camera": "Cận cảnh", "behavior": "Nằm rình", "action": "Báo tuyết ngẩng đầu"},
    {"setting": "Bãi cỏ", "camera": "Trung cảnh", "behavior": "Gặm cỏ", "action": "Dê con và dê đực ăn cỏ"},
    {"setting": "Bầu trời", "camera": "Toàn cảnh", "behavior": "", "action": "Mây trôi"},
]
ANSWER = """Kết quả:
NV | DE_DUC | Dê đực | Mô tả: ibex đực, sừng cong lớn | Prompt ảnh: full body adult male ibex, front view, plain grey background
NV | BAO_TUYET | Báo tuyết | Mô tả: lông xám đốm | Prompt ảnh: full body snow leopard, plain background
NV | DE_CON | Dê con | Mô tả: lông nâu sáng | Prompt ảnh: full body ibex kid, plain background
C1 | DE_DUC
C2 | BAO_TUYET
C4 | KHONG
"""


class ExtractorParsingTests(unittest.TestCase):
    def test_request_lists_every_scene_and_rules(self):
        request = chars.extraction_request("Kịch bản dê núi", SCENES)
        self.assertIn("C1: Bối cảnh: Vách đá", request)
        self.assertIn("C4:", request)
        self.assertIn("đến C4", request)
        self.assertIn("KHÔNG có bối cảnh", request)
        self.assertIn(chars.END_CHARACTERS, request)
        again = chars.extraction_request("x", SCENES, [3], ["DE_DUC", "DE_CON"])
        self.assertIn("C3", again)
        self.assertIn("DE_DUC, DE_CON", again)
        self.assertNotIn("C1:", again)

    def test_parse_handles_markdown_numbering_labels_and_unknown_ids(self):
        answer = ("1. **NV | Dê đực | Dê đực | Mô tả: sừng cong | Prompt ảnh: ibex**\n"
                  "- NV | BAO_TUYET | Báo tuyết | lông xám đốm | snow leopard\n"
                  "NV | DE_DUC | Dê đực (trùng) | Mô tả: sửa lại\n"
                  "C1 | DE_DUC\nCảnh 2: BAO_TUYET, SOI_XAM, BAO_TUYET\nC3 | không\nC9 | DE_DUC")
        result = chars.parse_characters(answer, 3)
        self.assertEqual([c["code"] for c in result["characters"]], ["DE_DUC", "BAO_TUYET"])
        self.assertEqual(result["characters"][0]["description"], "sửa lại")  # dòng sau cùng của cùng ID
        self.assertEqual(result["characters"][1]["image_prompt"], "snow leopard")
        self.assertEqual(result["scenes"], {1: ["DE_DUC"], 2: ["BAO_TUYET"], 3: []})
        self.assertEqual(result["unknown"], ["SOI_XAM"])
        self.assertEqual(chars.missing_scenes(result["scenes"], 4), [4])


class ExtractorConversationTests(unittest.TestCase):
    def test_missing_scenes_are_asked_again_once_each_round(self):
        asked = []

        class Session:
            NAME = "Gemini"
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return name
            def ask(self, text, status, stop, email="", label=""):
                asked.append(text)
                return ANSWER if len(asked) == 1 else "C3 | DE_CON, DE_DUC\n" + chars.END_CHARACTERS

        result = gemini_web.run_characters(Session(), "kịch bản", SCENES, lambda _: None, threading.Event(), model="Pro")
        self.assertEqual(len(asked), 2)
        self.assertIn("C3", asked[1])
        self.assertEqual(result["scenes"][3], ["DE_CON", "DE_DUC"])
        self.assertEqual((result["missing"], result["total"], result["rounds"]), ([], 4, 2))

    def test_no_progress_stops_and_unreadable_answer_raises(self):
        asked = []

        class Session:
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def ask(self, text, status, stop, email="", label=""):
                asked.append(text)
                return ANSWER if len(asked) == 1 else "Xin lỗi."
            def dump(self, reason, text=""):
                return "debug.txt"

        result = gemini_web.run_characters(Session(), "", SCENES, lambda _: None, threading.Event())
        self.assertEqual((len(asked), result["missing"]), (2, [3]))
        Session.ask = lambda self, *a, **k: "Tôi không hiểu."
        with self.assertRaisesRegex(gemini_web.GeminiWebError, "không đọc được nhân vật"):
            gemini_web.run_characters(Session(), "", SCENES, lambda _: None, threading.Event())


class ApplyExtractionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "studio.sqlite3")
        self.reg = self.store.characters
        self.project = self.reg.create_project("Dê núi")
        self.scene_ids = self.reg.add_scenes(self.project, SCENES)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_apply_creates_characters_and_links_scenes_by_id(self):
        result = chars.parse_characters(ANSWER + "C3 | DE_CON, DE_DUC", 4)
        summary = self.reg.apply_extraction(self.project, self.scene_ids, result)
        self.assertEqual(summary, {"added": 3, "updated": 0, "linked": 4, "kept_manual": 0})
        data = self.reg.project(self.project)
        codes = {c["id"]: c["code"] for c in data["characters"]}
        self.assertEqual([[codes[i] for i in s["character_ids"]] for s in data["scenes"]],
                         [["DE_DUC"], ["BAO_TUYET"], ["DE_CON", "DE_DUC"], []])
        self.assertEqual(data["characters"][0]["image_prompt"], "full body adult male ibex, front view, plain grey background")

    def test_rerun_keeps_locked_or_reviewed_characters_and_manual_scenes(self):
        result = chars.parse_characters(ANSWER + "C3 | DE_CON", 4)
        self.reg.apply_extraction(self.project, self.scene_ids, result)
        data = self.reg.project(self.project)
        goat = next(c for c in data["characters"] if c["code"] == "DE_DUC")
        kid = next(c for c in data["characters"] if c["code"] == "DE_CON")
        token = self.store.add_image(b"\x89PNG\r\n\x1a\nmaster")["token"]
        self.reg.approve_asset(self.reg.add_asset(goat["id"], token))
        self.reg.set_locked(goat["id"], True)
        self.reg.set_scene_characters(self.scene_ids[2], [], source="manual")  # người dùng bỏ hết nhân vật cảnh 3
        again = chars.parse_characters(ANSWER.replace("sừng cong lớn", "MÔ TẢ MỚI").replace("lông nâu sáng", "NÂU MỚI")
                                       + "C3 | DE_CON, DE_DUC\nNV | SOI | Sói | Mô tả: sói xám | Prompt ảnh: wolf", 4)
        summary = self.reg.apply_extraction(self.project, self.scene_ids, again)
        self.assertEqual((summary["added"], summary["kept_manual"]), (1, 1))
        data = self.reg.project(self.project)
        by_code = {c["code"]: c for c in data["characters"]}
        self.assertEqual(by_code["DE_DUC"]["description"], "ibex đực, sừng cong lớn")  # đã khóa: giữ nguyên
        self.assertEqual(by_code["DE_CON"]["description"], "NÂU MỚI")  # bản nháp: cập nhật
        self.assertEqual(data["scenes"][2]["character_ids"], [])
        self.assertTrue(data["scenes"][2]["manual"])
        self.assertEqual(kid["id"], by_code["DE_CON"]["id"])  # cùng Character ID: không tạo trùng

    def test_scene_numbers_follow_ids_sent_not_current_order(self):
        result = chars.parse_characters("NV | DE_DUC | Dê đực\nC1 | DE_DUC\nC2 | KHONG", 2)
        sent = [self.scene_ids[3], self.scene_ids[0]]  # đã gửi theo thứ tự khác
        self.reg.apply_extraction(self.project, sent, result)
        data = {s["id"]: s["character_ids"] for s in self.reg.project(self.project)["scenes"]}
        self.assertEqual(len(data[self.scene_ids[3]]), 1)
        self.assertEqual(data[self.scene_ids[0]], [])

    def test_delete_character_rules(self):
        made = self.reg.add_character(self.project, "Dê đực")
        self.reg.set_scene_characters(self.scene_ids[0], [made["id"]])
        self.reg.delete_character(made["id"])
        self.assertEqual(self.reg.project(self.project)["scenes"][0]["character_ids"], [])
        with self.assertRaises(ValueError):
            self.reg.delete_character(made["id"])


class CharacterHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")
        engine = type("Idle", (), {"snapshot": lambda self: {}, "changed": threading.Event()})()
        self.server = AppServer(0, self.store, engine)
        self.server.gemini_web.close()

        class FakeRunner:
            job = {}
            def start(self, kind, prompt, email="", model="", lines=None, style=""):
                FakeRunner.job = {"id": "a" * 32, "kind": kind, "busy": True, "lines": lines, "prompt": prompt}
                return FakeRunner.job["id"]
            def snapshot(self):
                return dict(FakeRunner.job)
            def close(self):
                pass
        self.runner = FakeRunner
        self.server.gemini_web = FakeRunner()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json", "Origin": self.server.origin, "X-Runner-Key": self.server.secret}
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def test_project_extract_apply_and_manual_edit_flow(self):
        status, data = self.call("POST", "/api/projects/create", {"name": "Dê núi", "script": "Kịch bản", "scenes": SCENES})
        self.assertEqual(status, 200)
        project = data["project"]
        self.assertEqual(len(project["scenes"]), 4)
        status, job = self.call("POST", "/api/characters/extract", {"project_id": project["id"], "engine": "gemini"})
        self.assertEqual((status, job["total"]), (200, 4))
        self.assertEqual(self.runner.job["kind"], "characters")
        self.assertEqual(self.runner.job["prompt"], "Kịch bản")
        self.assertEqual(self.call("POST", "/api/characters/apply", {"engine": "gemini", "job_id": job["id"]})[0], 400)  # đang chạy
        self.runner.job.update(busy=False, result=chars.parse_characters(ANSWER, 4) | {"missing": [3]})
        status, applied = self.call("POST", "/api/characters/apply", {"engine": "gemini", "job_id": job["id"]})
        self.assertEqual((status, applied["summary"]["added"], applied["missing"]), (200, 3, [3]))
        self.assertEqual(self.call("POST", "/api/characters/apply", {"engine": "gemini", "job_id": job["id"]})[0], 400)  # đã lưu
        characters = applied["project"]["characters"]
        third = applied["project"]["scenes"][2]["id"]
        self.assertEqual(self.call("POST", "/api/scenes/characters", {"scene_id": third, "character_ids": [characters[2]["id"]]})[0], 200)
        self.assertEqual(self.call("POST", "/api/characters/update", {"id": characters[0]["id"], "name": "Dê đầu đàn"})[0], 200)
        status, added = self.call("POST", "/api/characters/add", {"project_id": project["id"], "name": "Dê đực"})
        self.assertEqual(added["code"], "DE_DUC_2")
        status, full = self.call("GET", f"/api/project/{project['id']}")
        self.assertEqual(full["scenes"][2]["character_ids"], [characters[2]["id"]])
        self.assertTrue(full["scenes"][2]["manual"])
        self.assertEqual(full["characters"][0]["name"], "Dê đầu đàn")
        self.assertEqual(len(self.call("GET", "/api/projects")[1]["projects"]), 1)
        self.assertEqual(self.call("POST", "/api/characters/lock", {"id": characters[0]["id"], "locked": True})[0], 400)  # chưa có ảnh Master
        token = self.store.add_image(b"\x89PNG\r\n\x1a\nmaster")["token"]
        self.store.characters.approve_asset(self.store.characters.add_asset(characters[0]["id"], token))
        self.assertEqual(self.call("POST", "/api/characters/lock", {"id": characters[0]["id"], "locked": True})[0], 200)
        self.assertEqual(self.call("POST", "/api/characters/update", {"id": characters[0]["id"], "name": "X"})[0], 400)  # đã khóa
        self.assertEqual(self.call("POST", "/api/characters/lock", {"id": characters[0]["id"], "locked": False})[0], 200)
        self.assertEqual(self.call("POST", "/api/projects/create", {"scenes": []})[0], 400)
        self.assertEqual(self.call("GET", "/api/project/" + "b" * 32)[0], 404)


if __name__ == "__main__":
    unittest.main()
