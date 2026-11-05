"""Thư viện Video & Ảnh (Media Library).

Dùng chung hệ "token" ảnh đã có: media_id của một ảnh CHÍNH LÀ token 32 ký tự mà jobs.images, character_assets và
logic tải ảnh lên Muse đang dùng, nên ảnh trong thư viện gắn thẳng vào prompt/cảnh mà không phải chép hay đổi đường dẫn.

- File nằm trong thư mục dữ liệu của app (runner/paths.data_root): data/media/{uploads,generated,imported}/images/.
  Ảnh cũ ở data/input-images/ giữ nguyên chỗ; bảng image_library cũ được chép sang bảng media (không xóa).
- SQLite chỉ lưu thông tin (không lưu ảnh dạng BLOB). SHA-256 chống tải trùng nội dung.
- Ghi file qua file tạm rồi đổi tên sau khi ghi DB trong cùng giao dịch: lỗi giữa chừng không để lại file/bản ghi hỏng.
- Video do tool tạo (jobs.output) được thêm vào thư viện tại chỗ (không chép, không đổi tên) kèm Job/Scene.
- Xóa an toàn: không xóa ảnh mà tác vụ đang chạy dùng; ảnh còn được prompt/nhân vật dùng thì chỉ ẩn khỏi thư viện
  và giữ file để không tạo đường dẫn hỏng.
"""
import hashlib
import json
import os
import re
import struct
import time
import uuid
from pathlib import Path

MEDIA_ID = re.compile(r"[a-f0-9]{32}")
CATEGORIES = ("character", "product", "reference", "other")
SOURCES = ("uploaded", "generated", "imported")
IMAGE_EXTENSIONS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
DEFAULT_LIMITS = {"max_file_mb": 20, "max_batch": 50, "max_items": 10000, "max_side": 16384, "min_side": 16}

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    media_id TEXT PRIMARY KEY CHECK (length(media_id) = 32),
    media_type TEXT NOT NULL CHECK (media_type IN ('image', 'video')),
    source_type TEXT NOT NULL CHECK (source_type IN ('uploaded', 'generated', 'imported')),
    display_name TEXT NOT NULL,
    original_filename TEXT NOT NULL DEFAULT '',
    storage_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    width INTEGER,
    height INTEGER,
    sha256 TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'reference' CHECK (category IN ('character', 'product', 'reference', 'other')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    linked_character_id TEXT,
    linked_job_id TEXT,
    linked_scene_id TEXT,
    hidden INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS media_location ON media(storage_root, relative_path);
CREATE UNIQUE INDEX IF NOT EXISTS media_image_sha ON media(sha256) WHERE media_type = 'image' AND sha256 != '';
CREATE INDEX IF NOT EXISTS media_listing ON media(hidden, media_type, created_at);
CREATE INDEX IF NOT EXISTS media_job ON media(linked_job_id);
CREATE INDEX IF NOT EXISTS media_character ON media(linked_character_id);
"""


class MediaError(ValueError):
    pass


class MediaInUse(MediaError):
    """Ảnh đang được prompt/nhân vật dùng: cần người dùng xác nhận (ảnh chỉ bị ẩn, file được giữ)."""
    def __init__(self, message, dependencies):
        super().__init__(message)
        self.dependencies = dependencies


def image_info(data):
    """(đuôi file, mime, rộng, cao) theo NỘI DUNG file. Chỉ nhận JPEG, PNG, WebP; trả về None nếu không hợp lệ."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            width, height = struct.unpack(">II", data[16:24])
            return ".png", "image/png", width, height
        if data[:3] == b"\xff\xd8\xff":
            position = 2
            while position + 9 < len(data):
                if data[position] != 0xFF:
                    return None
                marker = data[position + 1]
                if marker == 0xFF:
                    position += 1
                    continue
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    position += 2
                    continue
                length = struct.unpack(">H", data[position + 2:position + 4])[0]
                if length < 2:
                    return None
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", data[position + 5:position + 9])
                    return ".jpg", "image/jpeg", width, height
                position += 2 + length
            return None
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", data[26:30])
                return ".webp", "image/webp", width & 0x3FFF, height & 0x3FFF
            if chunk == b"VP8L" and data[20] == 0x2F:
                b0, b1, b2, b3 = data[21:25]
                return ".webp", "image/webp", 1 + (b0 | (b1 & 0x3F) << 8), 1 + (b1 >> 6 | b2 << 2 | (b3 & 0x0F) << 10)
            if chunk == b"VP8X":
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return ".webp", "image/webp", width, height
    except (struct.error, IndexError, ValueError):
        return None
    return None


def clean_name(name, fallback="Ảnh"):
    """Tên hiển thị an toàn: bỏ thư mục, ký tự điều khiển và ký tự cấm của Windows; giữ tiếng Việt."""
    name = Path(str(name or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", "", name).strip(" .")
    return name[:160] or fallback


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MediaLibrary:
    def __init__(self, store):
        self.store = store
        self.data = Path(store.path).resolve().parent
        self.root = self.data / "media"
        with store.connect() as db:
            db.executescript(SCHEMA)
            self.migrate_legacy(db)
        self.remove_partial_files()

    # ---- thư mục và giới hạn -------------------------------------------------------------------------------------
    def folder(self, source):
        return self.root / source / "images"

    def image_dirs(self):
        return [self.folder(source) for source in SOURCES]

    def limits(self):
        configured = self.store.setting("media_limits", {}) or {}
        limits = dict(DEFAULT_LIMITS)
        for key, value in configured.items():
            if key in limits and type(value) is int and value > 0:
                limits[key] = value
        return limits

    def remove_partial_files(self):
        for folder in self.image_dirs():
            if folder.is_dir():
                for path in folder.glob(".*.part"):
                    path.unlink(missing_ok=True)

    # ---- dữ liệu cũ ---------------------------------------------------------------------------------------------
    def migrate_legacy(self, db):
        """Chép bảng image_library cũ (ảnh ở data/input-images) sang bảng media. Chạy lại không tạo bản ghi trùng."""
        known = {row[0] for row in db.execute("SELECT media_id FROM media")}
        for row in db.execute("SELECT token,name,created FROM image_library ORDER BY created").fetchall():
            if row["token"] in known:
                continue
            image = self.store.image_file(row["token"])
            if not image:
                continue
            self.insert_existing(db, row["token"], image[0], "imported", row["name"], row["created"])

    def insert_existing(self, db, media_id, path, source, name, created=None, category="reference",
                        character_id=None, job_id=None, scene_id=None):
        """Thêm bản ghi cho ảnh đã có trên đĩa (không chép file). Bỏ qua nếu trùng nội dung/đường dẫn."""
        path = Path(path).resolve()
        try:
            relative = path.relative_to(self.data).as_posix()
            data = path.read_bytes()
        except (ValueError, OSError):
            return False
        info = self.loose_info(data)
        if not info:
            return False
        now = created or time.time()
        cursor = db.execute(
            """INSERT OR IGNORE INTO media(media_id,media_type,source_type,display_name,original_filename,relative_path,
               mime_type,file_size,width,height,sha256,category,created_at,updated_at,linked_character_id,linked_job_id,
               linked_scene_id) VALUES(?,'image',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (media_id, source, clean_name(name), clean_name(name, ""), relative, info[1], len(data), info[2], info[3],
             hashlib.sha256(data).hexdigest(), category, now, now, character_id, job_id, scene_id))
        return cursor.rowcount == 1

    def loose_info(self, data):
        """Ảnh có sẵn trong tool (dữ liệu cũ, ảnh Flow, ảnh nhân vật): nhận theo phần đầu file như trước đây,
        kích thước để trống nếu không đọc được. Ảnh người dùng tải lên mới dùng kiểm tra chặt (image_info)."""
        info = image_info(data)
        if info:
            return info
        extension, mime = self.store.image_type(data)
        return (extension, mime, None, None) if extension else None

    # ---- tải ảnh lên --------------------------------------------------------------------------------------------
    def add_image(self, data, filename, source="uploaded", category="reference", strict=True, **links):
        """Lưu một ảnh. strict=False chỉ dùng cho ảnh có sẵn trên máy do tool tạo (ví dụ ảnh Flow đã tải). Trả về (bản ghi, đã_có_sẵn). Ảnh trùng nội dung (SHA-256) trả về bản ghi cũ, không ghi file."""
        if source not in SOURCES:
            raise MediaError("Nguồn ảnh không hợp lệ.")
        if category not in CATEGORIES:
            raise MediaError("Phân loại ảnh không hợp lệ.")
        limits = self.limits()
        name = clean_name(filename)
        suffix = Path(name).suffix.lower()
        if strict and suffix and suffix not in IMAGE_EXTENSIONS:
            raise MediaError(f"{name}: chỉ nhận ảnh JPG, JPEG, PNG hoặc WEBP.")
        if not isinstance(data, bytes) or not data:
            raise MediaError(f"{name}: file trống.")
        if len(data) > limits["max_file_mb"] * 1024 * 1024:
            raise MediaError(f"{name}: vượt quá {limits['max_file_mb']} MB.")
        info = image_info(data) if strict else self.loose_info(data)
        if not info:
            raise MediaError(f"{name}: nội dung không phải ảnh JPG, PNG hoặc WEBP hợp lệ.")
        extension, mime, width, height = info
        if strict and not (limits["min_side"] <= width <= limits["max_side"] and limits["min_side"] <= height <= limits["max_side"]):
            raise MediaError(f"{name}: kích thước {width}×{height} px nằm ngoài giới hạn "
                             f"{limits['min_side']}–{limits['max_side']} px.")
        digest = hashlib.sha256(data).hexdigest()
        with self.store.connect() as db:
            existing = db.execute("SELECT * FROM media WHERE media_type='image' AND sha256=?", (digest,)).fetchone()
        if existing:
            if existing["hidden"]:
                with self.store.connect() as db:
                    db.execute("UPDATE media SET hidden=0,updated_at=? WHERE media_id=?", (time.time(), existing["media_id"]))
            return self.item(existing["media_id"]), True
        media_id = uuid.uuid4().hex
        folder = self.folder(source)
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / (media_id + extension)
        partial = folder / f".{media_id}.part"
        moved = False
        try:
            with open(partial, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            now = time.time()
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                again = db.execute("SELECT media_id FROM media WHERE media_type='image' AND sha256=?", (digest,)).fetchone()
                if again:  # vừa có yêu cầu khác tải cùng ảnh
                    media_id, duplicate = again["media_id"], True
                else:
                    count = db.execute("SELECT count(*) FROM media WHERE hidden=0").fetchone()[0]
                    if count >= limits["max_items"]:
                        raise MediaError(f"Thư viện đã đủ {limits['max_items']} file. Xóa bớt rồi tải lên tiếp.")
                    db.execute(
                        """INSERT INTO media(media_id,media_type,source_type,display_name,original_filename,relative_path,
                           mime_type,file_size,width,height,sha256,category,created_at,updated_at,linked_character_id,
                           linked_job_id,linked_scene_id) VALUES(?,'image',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (media_id, source, name, name, final.relative_to(self.data).as_posix(),
                         mime, len(data), width, height, digest, category, now, now, links.get("character_id"),
                         links.get("job_id"), links.get("scene_id")))
                    os.replace(partial, final)  # chỉ hiện file thật khi bản ghi đã chắc chắn được ghi
                    moved, duplicate = True, False
        except Exception:
            if moved:
                final.unlink(missing_ok=True)
            raise
        finally:
            partial.unlink(missing_ok=True)
        return self.item(media_id), duplicate

    # ---- đọc -----------------------------------------------------------------------------------------------------
    def path(self, row):
        """Đường dẫn thật của file, chỉ khi nằm trong thư mục gốc của bản ghi (chống path traversal)."""
        root = Path(row["storage_root"]).resolve() if row["storage_root"] else self.data
        try:
            path = (root / row["relative_path"]).resolve()
            path.relative_to(root)
        except (ValueError, OSError):
            return None
        return path if path.is_file() else None

    def row(self, media_id):
        if not isinstance(media_id, str) or not MEDIA_ID.fullmatch(media_id):
            raise MediaError("Media không hợp lệ.")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM media WHERE media_id=?", (media_id,)).fetchone()
        if not row:
            raise MediaError("Không tìm thấy media.")
        return row

    def file(self, media_id):
        """(đường dẫn, mime, bản ghi) hoặc nêu MediaError."""
        row = self.row(media_id)
        path = self.path(row)
        if not path:
            raise MediaError("File không còn trên máy (đã bị xóa hoặc di chuyển bên ngoài tool).")
        return path, row["mime_type"], row

    def usage(self, db, media_ids):
        """{media_id: {"running": n, "jobs": [...], "characters": [...]}} cho các ảnh đang được dùng."""
        wanted = set(media_ids)
        result = {}
        for job in db.execute("SELECT id,ordinal,status,images,scene_id FROM jobs WHERE images!='[]'"):
            try:
                tokens = set(json.loads(job["images"] or "[]")) & wanted
            except (TypeError, ValueError):
                continue
            for token in tokens:
                entry = result.setdefault(token, {"running": 0, "jobs": [], "characters": []})
                entry["jobs"].append({"id": job["id"], "name": f"Prompt {job['ordinal'] + 1}", "status": job["status"],
                                      "scene_id": job["scene_id"]})
                if job["status"] == "running":
                    entry["running"] += 1
        if wanted:
            marks = ",".join("?" * len(wanted))
            for asset in db.execute(f"""SELECT a.image_token,c.id,c.name FROM character_assets a
                                        JOIN characters c ON c.id=a.character_id WHERE a.image_token IN ({marks})""",
                                    list(wanted)):
                entry = result.setdefault(asset["image_token"], {"running": 0, "jobs": [], "characters": []})
                entry["characters"].append({"id": asset["id"], "name": asset["name"]})
        return result

    def public(self, row, usage=None):
        usage = usage or {}
        return {
            "id": row["media_id"], "media_type": row["media_type"], "source_type": row["source_type"],
            "display_name": row["display_name"], "original_filename": row["original_filename"],
            "mime_type": row["mime_type"], "file_size": row["file_size"], "width": row["width"], "height": row["height"],
            "sha256": row["sha256"], "category": row["category"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "linked_character_id": row["linked_character_id"],
            "linked_job_id": row["linked_job_id"], "linked_scene_id": row["linked_scene_id"],
            "used_by_jobs": len(usage.get("jobs", [])), "used_by_characters": len(usage.get("characters", [])),
            "running": usage.get("running", 0), "available": self.path(row) is not None,
        }

    def item(self, media_id):
        row = self.row(media_id)
        with self.store.connect() as db:
            usage = self.usage(db, [media_id]).get(media_id, {})
        item = self.public(row, usage)
        item["jobs"], item["characters"] = usage.get("jobs", []), usage.get("characters", [])
        return item

    def list(self, media_type=None, category=None, query="", source=None, limit=2000):
        clauses, params = ["hidden=0"], []
        if media_type in ("image", "video"):
            clauses.append("media_type=?")
            params.append(media_type)
        if category in CATEGORIES:
            clauses.append("category=?")
            params.append(category)
        if source in SOURCES:
            clauses.append("source_type=?")
            params.append(source)
        query = str(query or "").strip()[:100]
        if query:
            clauses.append("(display_name LIKE ? ESCAPE '\\' OR original_filename LIKE ? ESCAPE '\\')")
            pattern = "%" + re.sub(r"([%_\\])", r"\\\1", query) + "%"
            params += [pattern, pattern]
        with self.store.connect() as db:
            rows = db.execute(f"SELECT * FROM media WHERE {' AND '.join(clauses)} ORDER BY created_at DESC,rowid DESC LIMIT ?",
                              params + [int(limit)]).fetchall()
            usage = self.usage(db, [row["media_id"] for row in rows if row["media_type"] == "image"])
            totals = {row[0]: row[1] for row in db.execute(
                "SELECT media_type,count(*) FROM media WHERE hidden=0 GROUP BY media_type")}
        return {"items": [self.public(row, usage.get(row["media_id"])) for row in rows],
                "totals": {"all": sum(totals.values()), "image": totals.get("image", 0), "video": totals.get("video", 0)},
                "limits": self.limits()}

    # ---- sửa -----------------------------------------------------------------------------------------------------
    def update(self, media_id, changes):
        if not isinstance(changes, dict):
            raise MediaError("Dữ liệu không hợp lệ.")
        fields = {}
        if "display_name" in changes:
            name = clean_name(changes["display_name"], "")
            if not name:
                raise MediaError("Tên hiển thị không được để trống.")
            fields["display_name"] = name
        if "category" in changes:
            if changes["category"] not in CATEGORIES:
                raise MediaError("Phân loại phải là Nhân vật, Sản phẩm, Tham chiếu hoặc Khác.")
            fields["category"] = changes["category"]
        if not fields:
            raise MediaError("Không có thay đổi nào.")
        self.row(media_id)
        with self.store.connect() as db:
            db.execute(f"UPDATE media SET {','.join(k + '=?' for k in fields)},updated_at=? WHERE media_id=? AND hidden=0",
                       list(fields.values()) + [time.time(), media_id])
        return self.item(media_id)

    # ---- xóa -----------------------------------------------------------------------------------------------------
    def referenced(self, db):
        """Token ảnh mà hệ thống còn dùng ngoài thư viện (prompt, nhân vật, thư viện ảnh cũ)."""
        tokens = set()
        for row in db.execute("SELECT images FROM jobs WHERE images!='[]'"):
            try:
                tokens.update(json.loads(row["images"] or "[]"))
            except (TypeError, ValueError):
                continue
        tokens.update(self.store.characters.referenced_tokens(db))
        return tokens

    def delete(self, media_ids, confirmed=False):
        """Xóa khỏi thư viện. Từ chối ảnh mà tác vụ đang chạy dùng. Ảnh còn được prompt/nhân vật dùng cần confirmed=True;
        khi đó chỉ ẩn khỏi thư viện và giữ file. Video do tool tạo: chỉ bỏ khỏi thư viện, KHÔNG xóa file kết quả."""
        if (not isinstance(media_ids, list) or not 1 <= len(media_ids) <= 500 or
                any(not isinstance(item, str) or not MEDIA_ID.fullmatch(item) for item in media_ids)):
            raise MediaError("Chọn từ 1 đến 500 media hợp lệ để xóa.")
        media_ids = list(dict.fromkeys(media_ids))
        files = []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            marks = ",".join("?" * len(media_ids))
            rows = db.execute(f"SELECT * FROM media WHERE media_id IN ({marks}) AND hidden=0", media_ids).fetchall()
            if len(rows) != len(media_ids):
                raise MediaError("Một số media đã bị xóa hoặc không tồn tại. Bấm Làm mới.")
            usage = self.usage(db, media_ids)
            running = [row["display_name"] for row in rows if usage.get(row["media_id"], {}).get("running")]
            if running:
                raise MediaError("Không xóa được ảnh đang được tác vụ chạy sử dụng: " + ", ".join(running[:5]) +
                                 ". Đợi tác vụ xong rồi thử lại.")
            dependent = {row["media_id"]: usage[row["media_id"]] for row in rows if row["media_id"] in usage}
            if dependent and not confirmed:
                raise MediaInUse(f"{len(dependent)} ảnh đang được prompt hoặc nhân vật dùng. Ảnh sẽ chỉ bị ẩn khỏi thư viện, "
                                 "file được giữ để prompt/nhân vật không bị mất ảnh.", dependent)
            removed = hidden = 0
            for row in rows:
                in_data = not row["storage_root"]
                if row["media_type"] == "image" and in_data and row["media_id"] not in dependent:
                    db.execute("DELETE FROM media WHERE media_id=?", (row["media_id"],))
                    db.execute("DELETE FROM image_library WHERE token=?", (row["media_id"],))
                    path = self.path(row)
                    if path:
                        files.append(path)
                    removed += 1
                else:  # còn được dùng, hoặc là file kết quả ngoài thư mục dữ liệu: chỉ ẩn, không xóa file
                    db.execute("UPDATE media SET hidden=1,updated_at=? WHERE media_id=?", (time.time(), row["media_id"]))
                    db.execute("DELETE FROM image_library WHERE token=?", (row["media_id"],))  # file do prompt/nhân vật giữ
                    hidden += 1
        for path in files:
            path.unlink(missing_ok=True)
        return {"deleted": removed, "hidden": hidden}

    def purge_hidden(self):
        """Khi khởi động: ảnh đã ẩn mà không còn ai dùng thì xóa hẳn bản ghi và file."""
        files = []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = self.referenced(db)
            for row in db.execute("SELECT * FROM media WHERE hidden=1 AND media_type='image' AND storage_root=''").fetchall():
                if row["media_id"] in used:
                    continue
                db.execute("DELETE FROM media WHERE media_id=?", (row["media_id"],))
                db.execute("DELETE FROM image_library WHERE token=?", (row["media_id"],))
                path = self.path(row)
                if path:
                    files.append(path)
        for path in files:
            path.unlink(missing_ok=True)
        return len(files)

    # ---- đồng bộ kết quả do tool tạo -----------------------------------------------------------------------------
    def sync_generated(self):
        """Thêm video Muse đã tải và ảnh nhân vật (Character Master) vào thư viện. Không chép, không đổi tên file.
        Chạy lại nhiều lần không tạo bản ghi trùng (khóa duy nhất theo vị trí file và SHA-256 ảnh)."""
        added = 0
        with self.store.connect() as db:
            known_jobs = {row[0] for row in db.execute("SELECT linked_job_id FROM media WHERE media_type='video' "
                                                       "AND linked_job_id IS NOT NULL")}
            jobs = db.execute("""SELECT id,ordinal,output,scene_id,updated FROM jobs WHERE status='done'
                                 AND output IS NOT NULL AND output!=''""").fetchall()
            assets = db.execute("""SELECT a.image_token,a.origin,a.created,a.character_id,c.name FROM character_assets a
                                   JOIN characters c ON c.id=a.character_id
                                   WHERE a.image_token NOT IN (SELECT media_id FROM media)""").fetchall()
        for job in jobs:
            if job["id"] in known_jobs:
                continue
            path = Path(job["output"])
            if path.suffix.lower() != ".mp4" or not path.is_file():
                continue
            path = path.resolve()
            now = time.time()
            with self.store.connect() as db:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO media(media_id,media_type,source_type,display_name,original_filename,storage_root,
                       relative_path,mime_type,file_size,sha256,category,created_at,updated_at,linked_job_id,linked_scene_id)
                       VALUES(?,'video','generated',?,?,?,?,'video/mp4',?,'','other',?,?,?,?)""",
                    (uuid.uuid4().hex, f"Prompt {job['ordinal'] + 1}", path.name, str(path.parent), path.name,
                     path.stat().st_size, job["updated"] or now, now, job["id"], job["scene_id"]))
                added += cursor.rowcount
        for asset in assets:
            image = self.store.image_file(asset["image_token"])
            if not image:
                continue
            with self.store.connect() as db:
                added += int(self.insert_existing(
                    db, asset["image_token"], image[0], "uploaded" if asset["origin"] == "upload" else "generated",
                    asset["name"], asset["created"], "character", character_id=asset["character_id"]))
        return added
