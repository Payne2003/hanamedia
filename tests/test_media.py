"""Thư viện Video & Ảnh: tải ảnh lên, SHA-256 chống trùng, xem/đổi tên/xóa an toàn, gắn ảnh vào prompt/cảnh,
đồng bộ video do tool tạo, khôi phục sau khi khởi động lại và chặn truy cập file ngoài thư mục thư viện."""
import http.client
import json
import shutil
import struct
import tempfile
import threading
import unittest
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from app import AppServer
from runner.media import MediaError, MediaInUse, image_info
from runner.store import Store


def png(width=32, height=24, seed=b""):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([len(seed) % 256]) * (width * 3) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"tEXt", b"seed\x00" + seed) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(width=640, height=480, seed=b""):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    comment = b"\xff\xfe" + struct.pack(">H", len(seed) + 2) + seed
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + comment + sof + b"\xff\xd9"


def webp(width=300, height=200, seed=b""):
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + \
        (height - 1).to_bytes(3, "little") + seed
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


class MediaBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "studio.sqlite3"
        self.store = Store(self.db)
        self.media = self.store.media

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def files(self):
        return sorted(p.name for p in (self.root / "media").rglob("*") if p.is_file())

    def account(self):
        self.store.add_account("a@tuan89.site", "", 100)


class ImageInfoTests(unittest.TestCase):
    def test_reads_real_format_and_size(self):
        self.assertEqual(image_info(png(32, 24)), (".png", "image/png", 32, 24))
        self.assertEqual(image_info(jpeg(640, 480)), (".jpg", "image/jpeg", 640, 480))
        self.assertEqual(image_info(webp(300, 200)), (".webp", "image/webp", 300, 200))
        for bad in (b"GIF89a....", b"hello", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF0000WEBPVP8 "):
            self.assertIsNone(image_info(bad))


class MediaStoreTests(MediaBase):
    def test_upload_one_image_is_stored_on_disk_not_in_sqlite(self):
        item, duplicate = self.media.add_image(png(seed=b"one"), "chan-dung.png")
        self.assertFalse(duplicate)
        self.assertEqual((item["media_type"], item["source_type"], item["width"], item["height"], item["mime_type"]),
                         ("image", "uploaded", 32, 24, "image/png"))
        path, _, row = self.media.file(item["id"])
        self.assertEqual(path.parent, self.root / "media" / "uploaded" / "images")
        self.assertEqual(path.read_bytes(), png(seed=b"one"))
        self.assertEqual(row["relative_path"], f"media/uploaded/images/{item['id']}.png")
        self.assertEqual(self.store.image_file(item["id"])[0], path)  # token dùng chung với jobs.images
        with self.store.connect() as db:
            types = {r[0] for r in db.execute("SELECT typeof(relative_path) FROM media")}
        self.assertEqual(types, {"text"})

    def test_duplicate_content_returns_existing_without_new_file(self):
        first, _ = self.media.add_image(jpeg(seed=b"same"), "a.jpg")
        second, duplicate = self.media.add_image(jpeg(seed=b"same"), "b-khac-ten.jpg")
        self.assertTrue(duplicate)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.media.list()["totals"]["all"], 1)

    def test_invalid_files_leave_no_record_or_temp_file(self):
        self.store.save_setting("media_limits", {"max_file_mb": 1, "min_side": 16})
        cases = [(b"GIF89a" + b"\x00" * 50, "anh.gif"), (b"not an image", "anh.png"), (png(seed=b"x"), "virus.exe"),
                 (png(8, 8), "nho.png"), (b"", "trong.png"), (png(seed=b"x" * 1_100_000), "lon.png"),
                 (b"\x89PNG\r\n\x1a\nstub", "gia.png")]
        for data, name in cases:
            with self.subTest(name=name), self.assertRaises(MediaError):
                self.media.add_image(data, name)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.media.list()["totals"]["all"], 0)

    def test_count_limit(self):
        self.store.save_setting("media_limits", {"max_items": 2})
        self.media.add_image(png(seed=b"1"), "1.png")
        self.media.add_image(png(seed=b"2"), "2.png")
        with self.assertRaisesRegex(MediaError, "đủ 2 file"):
            self.media.add_image(png(seed=b"3"), "3.png")
        self.assertEqual(len(self.files()), 2)

    def test_vietnamese_and_hostile_names_are_kept_safe(self):
        item, _ = self.media.add_image(webp(seed=b"vn"), "Ảnh nhân vật Đạt – góc nghiêng.webp")
        self.assertEqual(item["display_name"], "Ảnh nhân vật Đạt – góc nghiêng.webp")
        evil, _ = self.media.add_image(png(seed=b"evil"), '..\\..\\Windows/<sys>:"32".png')
        self.assertEqual(evil["display_name"], "sys32.png")
        self.assertTrue(all(p.resolve().is_relative_to(self.root / "media") for p in (self.root / "media").rglob("*")))

    def test_restart_keeps_images_and_job_paths(self):
        item, _ = self.media.add_image(png(seed=b"keep"), "giu.png")
        self.account()
        self.store.enqueue(["Cảnh 1"], 1, uuid.uuid4().hex, [[item["id"]]], "image")
        restarted = Store(self.db)
        self.assertEqual([i["id"] for i in restarted.media.list()["items"]], [item["id"]])
        job, _ = restarted.claim(2)
        self.assertEqual(job["images"], [str(self.root / "media" / "uploaded" / "images" / f"{item['id']}.png")])
        self.assertTrue(Path(job["images"][0]).is_file())

    def test_rename_and_category(self):
        item, _ = self.media.add_image(png(seed=b"r"), "r.png")
        updated = self.media.update(item["id"], {"display_name": "  Sản phẩm A  ", "category": "product"})
        self.assertEqual((updated["display_name"], updated["category"]), ("Sản phẩm A", "product"))
        self.assertEqual([i["id"] for i in self.media.list(category="product", query="phẩm")["items"]], [item["id"]])
        for bad in ({"display_name": "  "}, {"category": "x"}, {}):
            with self.assertRaises(MediaError):
                self.media.update(item["id"], bad)

    def test_delete_unused_removes_file_and_record(self):
        item, _ = self.media.add_image(png(seed=b"d"), "d.png")
        self.assertEqual(self.media.delete([item["id"]]), {"deleted": 1, "hidden": 0})
        self.assertEqual(self.files(), [])
        self.assertIsNone(self.store.image_file(item["id"]))

    def test_cannot_delete_image_of_running_job_and_used_images_are_only_hidden(self):
        running, _ = self.media.add_image(png(seed=b"run"), "run.png")
        waiting, _ = self.media.add_image(png(seed=b"wait"), "wait.png")
        self.account()
        self.store.enqueue(["A", "B"], 1, uuid.uuid4().hex, [[running["id"]], [waiting["id"]]], "image")
        job, _ = self.store.claim(1)
        self.assertEqual(job["images"][0].rsplit("/", 1)[-1].split(".")[0], running["id"])
        with self.assertRaisesRegex(MediaError, "đang được tác vụ chạy"):
            self.media.delete([running["id"], waiting["id"]])
        with self.assertRaises(MediaInUse) as caught:
            self.media.delete([waiting["id"]])
        self.assertEqual(caught.exception.dependencies[waiting["id"]]["jobs"][0]["name"], "Prompt 2")
        self.assertEqual(self.media.delete([waiting["id"]], confirmed=True), {"deleted": 0, "hidden": 1})
        self.assertNotIn(waiting["id"], [i["id"] for i in self.media.list()["items"]])
        self.assertTrue(self.store.image_file(waiting["id"]))  # prompt vẫn còn ảnh, không có đường dẫn hỏng
        Store(self.db)
        self.assertTrue(self.store.image_file(waiting["id"]))  # khởi động lại: vẫn còn vì prompt đang dùng
        waiting_job = next(j for j in self.store.snapshot()["jobs"] if j["status"] == "waiting")
        self.store.delete_jobs([waiting_job["id"]])
        Store(self.db)  # không còn ai dùng: lần khởi động sau dọn hẳn
        self.assertIsNone(self.store.image_file(waiting["id"]))

    def test_character_image_is_protected(self):
        item, _ = self.media.add_image(png(seed=b"char"), "de.png")
        project = self.store.characters.create_project("Dự án")
        goat = self.store.characters.add_character(project, "Dê")["id"]
        self.store.characters.add_asset(goat, item["id"], origin="library")
        with self.assertRaises(MediaInUse) as caught:
            self.media.delete([item["id"]])
        self.assertEqual(caught.exception.dependencies[item["id"]]["characters"][0]["name"], "Dê")

    def test_attach_images_to_the_right_prompt_and_scene(self):
        reg = self.store.characters
        project = reg.create_project("Dê núi")
        scenes = reg.add_scenes(project, [{"setting": "Vách đá"}, {"setting": "Thung lũng"}, {"setting": "Suối"}])
        images = [self.media.add_image(png(seed=bytes([i])), f"canh-{i}.png")[0]["id"] for i in range(4)]
        groups = [[images[0], images[1]], [images[2]], [images[3], images[0]]]
        self.store.enqueue(["Cảnh 1", "Cảnh 2", "Cảnh 3"], 1, uuid.uuid4().hex, groups, "image", scene_ids=scenes)
        jobs = sorted(self.store.snapshot()["jobs"], key=lambda j: j["ordinal"])
        for job, group, scene in zip(jobs, groups, scenes):
            detail = self.store.job_detail(job["id"])
            self.assertEqual((detail["images"], detail["scene_id"]), (group, scene))
        edited = self.store.update_job(jobs[1]["id"], "Cảnh 2 đã sửa", None, [images[1], images[2]], "image", None)
        self.assertEqual(edited["images"], [images[1], images[2]])
        restarted = Store(self.db)
        self.assertEqual(restarted.job_detail(jobs[1]["id"])["images"], [images[1], images[2]])
        self.assertEqual(restarted.job_detail(jobs[2]["id"])["scene_id"], scenes[2])
        usage = restarted.media.item(images[0])
        self.assertEqual(sorted(j["name"] for j in usage["jobs"]), ["Prompt 1", "Prompt 3"])

    def test_generated_videos_and_character_images_sync_once(self):
        self.account()
        reg = self.store.characters
        project = reg.create_project("P")
        scene = reg.add_scenes(project, [{"setting": "A"}])[0]
        self.store.enqueue(["Cảnh"], 1, uuid.uuid4().hex, scene_ids=[scene])
        job, _ = self.store.claim(1)
        output = self.root / "ket-qua" / "huy-x" / "Prompt 1.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypmp42 video")
        self.store.finish(job["id"], output=str(output))  # tự đồng bộ sau khi tải xong
        videos = self.media.list(media_type="video")["items"]
        self.assertEqual(len(videos), 1)
        video = videos[0]
        self.assertEqual((video["source_type"], video["linked_job_id"], video["linked_scene_id"], video["display_name"]),
                         ("generated", job["id"], scene, "Prompt 1"))
        self.assertEqual(self.media.file(video["id"])[0], output.resolve())  # không chép, không đổi tên
        token = self.store.add_image(png(seed=b"master"))["token"]
        goat = reg.add_character(project, "Dê")["id"]
        reg.add_asset(goat, token, origin="flow")
        self.assertEqual(self.media.sync_generated(), 1)
        for _ in range(2):
            self.assertEqual(self.media.sync_generated(), 0)
            Store(self.db)
        items = self.media.list()["items"]
        self.assertEqual(len(items), 2)
        master = next(i for i in items if i["media_type"] == "image")
        self.assertEqual((master["linked_character_id"], master["category"], master["source_type"]), (goat, "character", "generated"))
        # Bỏ video khỏi thư viện: KHÔNG xóa file kết quả, làm mới cũng không thêm lại.
        self.assertEqual(self.media.delete([video["id"]]), {"deleted": 0, "hidden": 1})
        self.assertTrue(output.is_file())
        self.media.sync_generated()
        self.assertEqual(self.media.list(media_type="video")["items"], [])

    def test_legacy_library_is_migrated_once(self):
        token = self.store.add_image(png(seed=b"legacy"))["token"]
        with self.store.connect() as db:
            db.execute("INSERT INTO image_library(token,name,created) VALUES(?,?,?)", (token, "cu.png", 1.0))
            db.execute("DELETE FROM media")
        for _ in range(2):
            store = Store(self.db)
        items = store.media.list()["items"]
        self.assertEqual([(i["id"], i["display_name"], i["source_type"]) for i in items], [(token, "cu.png", "imported")])
        self.assertTrue((self.root / "input-images" / f"{token}.png").is_file())  # file cũ giữ nguyên chỗ

    def test_files_outside_media_root_are_never_served(self):
        item, _ = self.media.add_image(png(seed=b"t"), "t.png")
        secret = self.root / "secret.txt"
        secret.write_text("bí mật")
        with self.store.connect() as db:
            db.execute("UPDATE media SET relative_path='../secret.txt' WHERE media_id=?", (item["id"],))
        with self.assertRaises(MediaError):
            self.media.file(item["id"])
        for bad in ("../x", "A" * 32, "", None):
            with self.assertRaises(MediaError):
                self.media.row(bad)


class MediaHTTPTests(MediaBase):
    def setUp(self):
        super().setUp()
        engine = type("Idle", (), {"snapshot": lambda self: {}, "changed": threading.Event()})()
        self.server = AppServer(0, self.store, engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def request(self, method, path, body=None, headers=None, key=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        base = {"Origin": self.server.origin}
        if key:
            base["X-Runner-Key"] = self.server.secret
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
            base["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers={**base, **(headers or {})})
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def upload(self, data, name, key=True):
        status, _, body = self.request("POST", "/api/media/upload", data,
                                       {"X-Image-Name": quote(name), "Content-Type": "image/png"}, key)
        return status, json.loads(body)

    def test_upload_one_and_fetch_thumbnail_file_and_download(self):
        status, data = self.upload(png(seed=b"h"), "Nhân vật chính.png")
        self.assertEqual((status, data["duplicate"], data["item"]["display_name"]), (200, False, "Nhân vật chính.png"))
        media_id = data["item"]["id"]
        status, headers, body = self.request("GET", f"/api/media/item/{media_id}/thumbnail")
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/png", png(seed=b"h")))
        status, headers, _ = self.request("GET", f"/api/media/item/{media_id}/download")
        self.assertEqual(status, 200)
        self.assertIn("attachment;", headers["Content-Disposition"])
        self.assertIn("filename*=UTF-8''Nh%C3%A2n%20v%E1%BA%ADt%20ch%C3%ADnh.png", headers["Content-Disposition"])
        self.assertNotIn("\n", headers["Content-Disposition"])
        status, _, body = self.request("GET", "/api/media?type=image&q=ch%C3%ADnh")
        listing = json.loads(body)
        self.assertEqual((status, [i["id"] for i in listing["items"]], listing["totals"]["image"]), (200, [media_id], 1))
        self.assertEqual(json.loads(self.request("GET", "/api/library")[2])["images"][0]["token"], media_id)

    def test_upload_twenty_images_in_parallel(self):
        with ThreadPoolExecutor(5) as pool:
            results = list(pool.map(lambda i: self.upload(png(seed=f"anh-{i}".encode()), f"Ảnh {i}.png"), range(20)))
        self.assertTrue(all(status == 200 and not data["duplicate"] for status, data in results))
        self.assertEqual(json.loads(self.request("GET", "/api/media")[2])["totals"]["all"], 20)
        self.assertEqual(len(self.files()), 20)

    def test_duplicate_invalid_and_unauthorized_uploads(self):
        first = self.upload(png(seed=b"dup"), "a.png")[1]
        status, again = self.upload(png(seed=b"dup"), "b.png")
        self.assertEqual((status, again["duplicate"], again["item"]["id"]), (200, True, first["item"]["id"]))
        status, error = self.upload(b"MZ\x90\x00 not an image", "anh.png")
        self.assertEqual(status, 400)
        self.assertIn("không phải ảnh", error["error"])
        self.assertEqual(self.upload(png(seed=b"nokey"), "x.png", key=False)[0], 403)
        self.assertEqual(len(self.files()), 1)

    def test_patch_delete_and_batch(self):
        ids = [self.upload(png(seed=bytes([i])), f"{i}.png")[1]["item"]["id"] for i in range(3)]
        status, _, body = self.request("PATCH", f"/api/media/item/{ids[0]}", {"display_name": "Đổi tên", "category": "character"})
        self.assertEqual((status, json.loads(body)["item"]["display_name"]), (200, "Đổi tên"))
        self.assertEqual(self.request("PATCH", f"/api/media/item/{ids[0]}", {"category": "x"})[0], 400)
        self.assertEqual(self.request("PATCH", f"/api/media/item/{ids[0]}", {"display_name": "x"}, key=False)[0], 403)
        self.account()
        self.store.enqueue(["Cảnh"], 1, uuid.uuid4().hex, [[ids[1]]], "image")
        status, _, body = self.request("POST", "/api/media/delete-batch", {"ids": ids[1:]})
        conflict = json.loads(body)
        self.assertEqual((status, conflict["needs_confirm"], list(conflict["dependencies"])), (409, True, [ids[1]]))
        status, _, body = self.request("POST", "/api/media/delete-batch", {"ids": ids[1:], "confirmed": True})
        self.assertEqual((status, json.loads(body)["deleted"], json.loads(body)["hidden"]), (200, 1, 1))
        status, _, body = self.request("DELETE", f"/api/media/item/{ids[0]}", {})
        self.assertEqual((status, json.loads(body)["deleted"]), (200, 1))
        self.assertEqual(self.request("GET", f"/api/media/item/{ids[0]}")[0], 404)

    def test_path_traversal_and_foreign_ids_are_rejected(self):
        media_id = self.upload(png(seed=b"p"), "p.png")[1]["item"]["id"]
        with self.store.connect() as db:
            db.execute("UPDATE media SET relative_path='../../../../etc/passwd' WHERE media_id=?", (media_id,))
        for path in (f"/api/media/item/{media_id}/file", f"/api/media/item/{media_id}/download",
                     "/api/media/item/../../studio.sqlite3", "/api/media/item/%2e%2e%2fstudio.sqlite3/file",
                     "/api/media/item/" + "0" * 32 + "/file"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_old_job_video_route_still_works(self):
        self.account()
        self.store.enqueue(["Cảnh"], 1, uuid.uuid4().hex)
        job, _ = self.store.claim(1)
        output = self.root / "out.mp4"
        output.write_bytes(b"video-bytes")
        self.store.finish(job["id"], output=str(output))
        status, headers, body = self.request("GET", f"/api/media/{job['id']}", headers={"Range": "bytes=0-4"})
        self.assertEqual((status, body), (206, b"video"))


if __name__ == "__main__":
    unittest.main()
