import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner.characters import character_code
from runner.store import Store

PNG = b"\x89PNG\r\n\x1a\ncharacter master"


class CharacterRegistryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "studio.sqlite3")
        self.reg = self.store.characters

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def image(self, data=PNG):
        return self.store.add_image(data + uuid.uuid4().bytes)["token"]

    def project(self):
        project = self.reg.create_project("Dê núi", brief="Dê núi và báo tuyết")
        scenes = self.reg.add_scenes(project, [
            {"setting": "Vách đá", "camera": "Toàn cảnh", "behavior": "Đứng yên", "action": "Giữ thăng bằng"},
            {"setting": "Sườn núi", "camera": "Cận cảnh", "behavior": "Rình", "action": "Ngẩng đầu"},
            {"setting": "Thung lũng", "camera": "Trung cảnh", "behavior": "Gặm cỏ", "action": "Đi xuống"},
        ])
        return project, scenes

    def test_old_database_is_migrated_without_losing_data(self):
        old = self.root / "old" / "studio.sqlite3"
        old.parent.mkdir()
        images = old.parent / "input-images"
        images.mkdir()
        token = uuid.uuid4().hex
        (images / (token + ".png")).write_bytes(PNG)
        db = sqlite3.connect(old)
        db.executescript("""
            CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, mailbox TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, budget REAL NOT NULL, state TEXT NOT NULL DEFAULT 'new',
                detail TEXT NOT NULL DEFAULT '', last_used REAL NOT NULL DEFAULT 0);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
                phase TEXT NOT NULL DEFAULT 'Đợi tài khoản', account_id TEXT, cost REAL NOT NULL,
                submitted INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', output TEXT, created REAL NOT NULL,
                updated REAL NOT NULL, source_url TEXT NOT NULL DEFAULT '', batch TEXT NOT NULL, ordinal INTEGER NOT NULL,
                assigned_account_id TEXT, images TEXT NOT NULL DEFAULT '[]', generation_mode TEXT NOT NULL DEFAULT 'text',
                progress INTEGER NOT NULL DEFAULT 0, details TEXT NOT NULL DEFAULT '{}', UNIQUE(batch, ordinal));
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE image_library (token TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL);
        """)
        db.execute("INSERT INTO jobs(id,prompt,status,cost,created,updated,batch,ordinal,images,generation_mode) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, "Prompt cũ", "done", 1, time.time(), time.time(), "batch-cu-0000000001", 0,
                    json.dumps([token]), "image"))
        db.execute("INSERT INTO settings(key,value) VALUES('output_folder',?)", (json.dumps("D:/Video"),))
        db.commit()
        db.close()

        store = Store(old)
        job = store.snapshot()["jobs"][0]
        detail = store.job_detail(job["id"])
        self.assertEqual((detail["prompt"], detail["status"], detail["generation_mode"]), ("Prompt cũ", "done", "image"))
        self.assertEqual(json.loads(detail["images"]) if isinstance(detail["images"], str) else detail["images"], [token])
        self.assertIsNone(detail["scene_id"])
        self.assertEqual(detail["images_origin"], "manual")
        self.assertTrue((images / (token + ".png")).exists())  # ảnh của job cũ không bị dọn
        self.assertEqual(store.setting("output_folder"), "D:/Video")
        self.assertEqual(store.characters.projects(), [])
        Store(old)  # mở lại lần hai: migration không lỗi

    def test_character_ids_are_unique_readable_codes(self):
        self.assertEqual(character_code("Dê đực đầu đàn"), "DE_DUC_DAU_DAN")
        self.assertEqual(character_code("Báo tuyết!"), "BAO_TUYET")
        self.assertEqual(character_code("???"), "NHAN_VAT")
        project, _ = self.project()
        first = self.reg.add_character(project, "Báo tuyết", "Lông xám đốm")
        second = self.reg.add_character(project, "Báo  tuyết")
        other = self.reg.add_character(self.reg.create_project("Khác"), "Báo tuyết")
        self.assertEqual((first["code"], second["code"], other["code"]), ("BAO_TUYET", "BAO_TUYET_2", "BAO_TUYET"))
        self.assertNotEqual(first["id"], second["id"])
        with self.assertRaises(ValueError):
            self.reg.add_character(project, "  ")

    def test_scene_ids_are_not_scene_numbers_and_keep_order(self):
        project, scenes = self.project()
        more = self.reg.add_scenes(project, [{"setting": "Đêm", "camera": "Toàn cảnh", "behavior": "Nằm", "action": "Ngủ"}])
        data = self.reg.project(project)
        self.assertEqual([s["id"] for s in data["scenes"]], scenes + more)
        self.assertEqual([s["ordinal"] for s in data["scenes"]], [0, 1, 2, 3])
        self.assertTrue(all(len(s) == 32 and not s.isdigit() for s in scenes))

    def test_scene_characters_only_from_same_project(self):
        project, scenes = self.project()
        goat = self.reg.add_character(project, "Dê đực")["id"]
        leopard = self.reg.add_character(project, "Báo tuyết")["id"]
        stranger = self.reg.add_character(self.reg.create_project("Khác"), "Sói")["id"]
        self.reg.set_scene_characters(scenes[0], [goat])
        self.reg.set_scene_characters(scenes[1], [leopard, goat, leopard])
        with self.assertRaises(ValueError):
            self.reg.set_scene_characters(scenes[2], [stranger])
        self.reg.set_scene_characters(scenes[1], [leopard], source="manual")  # thay toàn bộ
        data = {s["id"]: s["character_ids"] for s in self.reg.project(project)["scenes"]}
        self.assertEqual((data[scenes[0]], data[scenes[1]], data[scenes[2]]), ([goat], [leopard], []))

    def test_master_approve_lock_and_tokens(self):
        project, _ = self.project()
        goat = self.reg.add_character(project, "Dê đực")["id"]
        leopard = self.reg.add_character(project, "Báo tuyết")["id"]
        with self.assertRaises(ValueError):
            self.reg.set_locked(goat, True)  # chưa có ảnh Master duyệt
        first, second = self.image(), self.image()
        a1 = self.reg.add_asset(goat, first, prompt="dê đực")
        a2 = self.reg.add_asset(goat, second)
        self.assertEqual(self.reg.project(project)["characters"][0]["status"], "review")
        self.reg.approve_asset(a1)
        self.reg.approve_asset(a2)
        self.assertEqual(self.reg.master_tokens([goat, leopard]), {goat: second})  # báo tuyết chưa duyệt: không gắn
        self.reg.set_locked(goat, True)
        for blocked in (lambda: self.reg.approve_asset(a1), lambda: self.reg.update_character(goat, name="X"),
                        lambda: self.reg.queue_image(goat, "tạo lại")):
            with self.assertRaises(ValueError):
                blocked()
        character = self.reg.project(project)["characters"][0]
        self.assertTrue(character["locked"])
        self.assertEqual([a["status"] for a in character["assets"]], ["candidate", "approved"])

    def test_character_images_survive_cleanup_and_library_delete(self):
        project, _ = self.project()
        goat = self.reg.add_character(project, "Dê đực")["id"]
        token = self.image()
        self.store.add_library_image(token, "de.png")
        self.reg.add_asset(goat, token, origin="library")
        self.store.delete_library_images([token])
        Store(self.root / "studio.sqlite3")  # khởi động lại: dọn ảnh không dùng
        self.assertIsNotNone(self.store.image_file(token))
        self.reg.delete_project(project)  # xóa dự án: ảnh nhân vật không còn ai dùng thì được dọn
        self.assertIsNone(self.store.image_file(token))

    def test_image_tasks_are_deduplicated_and_recovered_safely(self):
        project, _ = self.project()
        goat = self.reg.add_character(project, "Dê đực")["id"]
        leopard = self.reg.add_character(project, "Báo tuyết")["id"]
        first = self.reg.queue_image(goat, "dê đực, nền trắng", request_key="k1")
        self.assertEqual(self.reg.queue_image(goat, "dê đực khác", request_key="k2"), first)  # đang chờ: không tạo trùng
        self.assertEqual(self.reg.queue_image(leopard, "báo", request_key="k1"), first)  # cùng request_key
        second = self.reg.queue_image(leopard, "báo tuyết", request_key="k3")
        with self.store.connect() as db:
            db.execute("UPDATE image_tasks SET status='running', submitted=0 WHERE id=?", (first,))
            db.execute("UPDATE image_tasks SET status='running', submitted=1 WHERE id=?", (second,))
        self.store.recover()
        with self.store.connect() as db:
            rows = {r["id"]: (r["status"], r["error"]) for r in db.execute("SELECT * FROM image_tasks")}
        self.assertEqual(rows[first], ("waiting", ""))
        self.assertEqual(rows[second][0], "error")
        self.assertIn("không tự gửi lại", rows[second][1])

    def test_jobs_link_to_scenes_and_manual_images_are_marked(self):
        project, scenes = self.project()
        self.store.enqueue(["Prompt 1", "Prompt 2", "Prompt 3"], 1, "batch-scene-000000001", scene_ids=scenes[:2])
        jobs = {j["prompt"]: self.store.job_detail(j["id"]) for j in self.store.snapshot()["jobs"]}
        self.assertEqual((jobs["Prompt 1"]["scene_id"], jobs["Prompt 2"]["scene_id"], jobs["Prompt 3"]["scene_id"]),
                         (scenes[0], scenes[1], None))
        self.assertEqual([j["id"] for j in self.reg.project(project)["scenes"][0]["jobs"]], [jobs["Prompt 1"]["id"]])
        with self.assertRaises(ValueError):
            self.store.enqueue(["X"], 1, "batch-scene-000000002", scene_ids=["không-hợp-lệ"])
        self.store.enqueue(["X"], 1, "batch-scene-000000003", scene_ids=[uuid.uuid4().hex])  # cảnh đã mất: vẫn thêm, bỏ liên kết
        lost = next(j for j in self.store.snapshot()["jobs"] if j["prompt"] == "X")
        self.assertIsNone(self.store.job_detail(lost["id"])["scene_id"])
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET images_origin='auto' WHERE id=?", (jobs["Prompt 1"]["id"],))
        self.store.update_job(jobs["Prompt 1"]["id"], "Prompt 1 sửa")  # không đổi ảnh: giữ 'auto'
        self.assertEqual(self.store.job_detail(jobs["Prompt 1"]["id"])["images_origin"], "auto")
        self.store.update_job(jobs["Prompt 1"]["id"], "Prompt 1 sửa", images=[self.image()])
        self.assertEqual(self.store.job_detail(jobs["Prompt 1"]["id"])["images_origin"], "manual")
        self.reg.delete_project(project)
        self.assertIsNone(self.store.job_detail(jobs["Prompt 1"]["id"])["scene_id"])  # job giữ nguyên, bỏ liên kết cảnh
        self.assertEqual(len(self.store.snapshot()["jobs"]), 4)


if __name__ == "__main__":
    unittest.main()


class JobCharacterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "studio.sqlite3")
        self.reg = self.store.characters
        self.project = self.reg.create_project("Dê núi")
        self.scenes = self.reg.add_scenes(self.project, [{"setting": "A"}, {"setting": "B"}])
        self.goat = self.reg.add_character(self.project, "Dê đực")["id"]
        self.leopard = self.reg.add_character(self.project, "Báo tuyết")["id"]
        self.reg.set_scene_characters(self.scenes[0], [self.goat, self.leopard])
        self.store.enqueue(["Prompt 1", "Prompt 2"], 1, "batch-jobchars-0000001", scene_ids=[self.scenes[0], None])
        jobs = {j["prompt"]: j["id"] for j in self.store.snapshot()["jobs"]}
        self.job, self.free = jobs["Prompt 1"], jobs["Prompt 2"]

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def approve(self, character):
        token = self.store.add_image(PNG + uuid.uuid4().bytes)["token"]
        self.reg.approve_asset(self.reg.add_asset(character, token))
        return token

    def test_scene_characters_flow_into_prompt_and_attach_needs_masters(self):
        info = self.reg.job_characters(self.job)
        self.assertEqual((info["source"], info["scene_number"], info["state"]), ("scene", 1, "missing_master"))
        self.assertEqual([c["code"] for c in info["selected"]], ["DE_DUC", "BAO_TUYET"])
        self.assertEqual(len(info["options"]), 2)
        with self.assertRaisesRegex(ValueError, "DE_DUC, BAO_TUYET"):
            self.reg.attach_job_characters(self.job)
        goat, leopard = self.approve(self.goat), self.approve(self.leopard)
        with self.assertRaisesRegex(ValueError, "chuyển prompt sang chế độ Ảnh → video"):
            self.reg.attach_job_characters(self.job)  # chế độ video thường: phải xác nhận, không tự đổi
        self.assertEqual(self.reg.attach_job_characters(self.job, confirm=True), [goat, leopard])
        detail = self.store.job_detail(self.job)
        self.assertEqual((detail["images"], detail["generation_mode"], detail["images_origin"]), ([goat, leopard], "image", "auto"))
        self.assertEqual(self.reg.job_characters(self.job)["state"], "attached")
        self.assertEqual(self.reg.attach_job_characters(self.job), [goat, leopard])  # gắn lại: không cần hỏi

    def test_prompt_override_and_manual_images_need_confirm(self):
        self.approve(self.goat)
        info = self.reg.job_characters(self.free, project_id=self.project)
        self.assertEqual((info["source"], info["selected"], len(info["options"])), ("none", [], 2))
        with self.assertRaisesRegex(ValueError, "chưa có nhân vật"):
            self.reg.attach_job_characters(self.free)
        self.reg.set_job_characters(self.free, [self.goat])
        self.assertEqual(self.reg.job_characters(self.free)["source"], "prompt")
        mine = self.store.add_image(PNG + b"mine")["token"]
        self.store.update_job(self.free, "Prompt 2", images=[mine], mode="image")
        with self.assertRaisesRegex(ValueError, "thay 1 ảnh bạn tự gắn"):
            self.reg.attach_job_characters(self.free)
        self.assertEqual(self.store.job_detail(self.free)["images"], [mine])  # chưa xác nhận: giữ nguyên
        self.reg.attach_job_characters(self.free, confirm=True)
        self.assertNotIn(mine, self.store.job_detail(self.free)["images"])
        self.reg.set_job_characters(self.free, [])  # bỏ chọn riêng: quay lại theo cảnh (ở đây không có cảnh)
        self.assertEqual(self.reg.job_characters(self.free)["source"], "none")

    def test_running_prompt_is_never_changed_and_limit_is_checked(self):
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job,))
        self.approve(self.goat), self.approve(self.leopard)
        for blocked in (lambda: self.reg.set_job_characters(self.job, [self.goat]),
                        lambda: self.reg.attach_job_characters(self.job, confirm=True)):
            with self.assertRaisesRegex(ValueError, "đang chạy"):
                blocked()
        many = [self.reg.add_character(self.project, f"Sói {n}")["id"] for n in range(11)]
        for character in many:
            self.approve(character)
        self.reg.set_job_characters(self.free, many)
        with self.assertRaisesRegex(ValueError, "tối đa 10"):
            self.reg.attach_job_characters(self.free, confirm=True)
