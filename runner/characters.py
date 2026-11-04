"""Character Registry: dự án, cảnh, nhân vật, ảnh Master và hàng đợi tạo ảnh nhân vật (lưu bền trong SQLite).

Nguyên tắc:
- Mọi khóa chính là mã ngẫu nhiên (uuid hex), KHÔNG dùng số thứ tự cảnh làm khóa. `ordinal` chỉ để sắp xếp hiển thị.
- Mỗi nhân vật có một Character ID đọc được (`code`, ví dụ DE_DUC) duy nhất trong dự án, và tối đa một ảnh Master
  đã duyệt (`approved_asset_id`). Không có ảnh tham chiếu riêng cho từng cảnh.
- Mỗi cảnh có danh sách Character ID xuất hiện trong cảnh (`scene_characters`).
- Video job vẫn dùng `jobs.images` hiện có; chỉ thêm `jobs.scene_id` (cảnh gốc) và `jobs.images_origin`
  ('manual' = người dùng tự gắn, 'auto' = Auto Character Mapper gắn) để không ghi đè ảnh người dùng tự gắn.
"""
import json
import re
import time
import unicodedata
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, brief TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '', script TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, setting TEXT NOT NULL DEFAULT '', camera TEXT NOT NULL DEFAULT '',
    behavior TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS scenes_project ON scenes(project_id, ordinal);
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    code TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    image_prompt TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
    locked INTEGER NOT NULL DEFAULT 0, approved_asset_id TEXT,
    created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(project_id, code));
CREATE TABLE IF NOT EXISTS scene_characters (
    scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'ai', PRIMARY KEY(scene_id, character_id));
CREATE TABLE IF NOT EXISTS character_assets (
    id TEXT PRIMARY KEY, character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    image_token TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'flow', prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'candidate', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS assets_character ON character_assets(character_id, created);
CREATE TABLE IF NOT EXISTS image_tasks (
    id TEXT PRIMARY KEY, character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting', attempts INTEGER NOT NULL DEFAULT 0,
    submitted INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', asset_id TEXT,
    request_key TEXT NOT NULL UNIQUE, created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS image_tasks_status ON image_tasks(status, created);
"""

# Cột thêm vào bảng jobs có sẵn (chạy qua migration của Store, không đổi dữ liệu cũ).
JOB_COLUMNS = {
    "scene_id": "TEXT",
    "images_origin": "TEXT NOT NULL DEFAULT 'manual'",
}

CHARACTER_STATUSES = ("draft", "generating", "review", "approved")
ASSET_ORIGINS = ("flow", "upload", "library")
TASK_ACTIVE = ("waiting", "running")
ID = re.compile(r"[a-f0-9]{32}")


def new_id():
    return uuid.uuid4().hex


def text(value, limit=5000):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def character_code(name):
    """Character ID đọc được từ tên: 'Dê đực đầu đàn' -> 'DE_DUC_DAU_DAN'."""
    value = unicodedata.normalize("NFD", str(name or "")).replace("đ", "d").replace("Đ", "D")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()[:40].strip("_")
    return value or "NHAN_VAT"


def check_id(value, label="Mã"):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f"{label} không hợp lệ.")
    return value


class CharacterRegistry:
    def __init__(self, store):
        self.store = store

    # ---------- Dự án và cảnh ----------
    def create_project(self, name, brief="", source_url="", script=""):
        name = text(name, 200) or "Dự án " + time.strftime("%d/%m %H:%M")
        project_id, now = new_id(), time.time()
        with self.store.connect() as db:
            db.execute("INSERT INTO projects(id,name,brief,source_url,script,created,updated) VALUES(?,?,?,?,?,?,?)",
                       (project_id, name, text(brief, 20000), text(source_url, 500), str(script or "")[:40000], now, now))
        return project_id

    def add_scenes(self, project_id, scenes):
        """scenes: danh sách {setting, camera, behavior, action, source?, prompt?}. Trả về các Scene ID theo thứ tự."""
        check_id(project_id, "Dự án")
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 500:
            raise ValueError("Mỗi lần thêm từ 1 đến 500 cảnh.")
        now, ids = time.time(), []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("Không tìm thấy dự án.")
            start = db.execute("SELECT coalesce(max(ordinal),-1)+1 FROM scenes WHERE project_id=?", (project_id,)).fetchone()[0]
            for offset, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError("Cảnh không hợp lệ.")
                scene_id = new_id()
                db.execute("""INSERT INTO scenes(id,project_id,ordinal,setting,camera,behavior,action,source,prompt,created,updated)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                           (scene_id, project_id, start + offset, text(scene.get("setting")), text(scene.get("camera")),
                            text(scene.get("behavior")), text(scene.get("action")), text(scene.get("source")),
                            text(scene.get("prompt"), 20000), now, now))
                ids.append(scene_id)
            db.execute("UPDATE projects SET updated=? WHERE id=?", (now, project_id))
        return ids

    def set_scene_prompt(self, scene_id, prompt):
        check_id(scene_id, "Cảnh")
        with self.store.connect() as db:
            if db.execute("UPDATE scenes SET prompt=?,updated=? WHERE id=?",
                          (text(prompt, 20000), time.time(), scene_id)).rowcount != 1:
                raise ValueError("Không tìm thấy cảnh.")

    def delete_project(self, project_id):
        """Xóa dự án cùng cảnh, nhân vật, ảnh Master và tác vụ ảnh. Video job đã tạo giữ nguyên (chỉ bỏ liên kết cảnh)."""
        check_id(project_id, "Dự án")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("""SELECT 1 FROM image_tasks t JOIN characters c ON c.id=t.character_id
                             WHERE c.project_id=? AND t.status='running'""", (project_id,)).fetchone():
                raise ValueError("Dự án đang có ảnh nhân vật đang tạo. Đợi xong hoặc hủy trước khi xóa.")
            db.execute("UPDATE jobs SET scene_id=NULL WHERE scene_id IN (SELECT id FROM scenes WHERE project_id=?)", (project_id,))
            if db.execute("DELETE FROM projects WHERE id=?", (project_id,)).rowcount != 1:
                raise ValueError("Không tìm thấy dự án.")
        self.store.cleanup_images()

    # ---------- Nhân vật ----------
    def add_character(self, project_id, name, description="", image_prompt="", code=None):
        """Tạo nhân vật với Character ID duy nhất trong dự án (thêm _2, _3... nếu trùng)."""
        check_id(project_id, "Dự án")
        name = text(name, 200)
        if not name:
            raise ValueError("Nhân vật cần có tên.")
        base = character_code(code or name)
        character_id, now = new_id(), time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("Không tìm thấy dự án.")
            used = {row["code"] for row in db.execute("SELECT code FROM characters WHERE project_id=?", (project_id,))}
            unique, number = base, 2
            while unique in used:
                unique, number = f"{base}_{number}", number + 1
            db.execute("""INSERT INTO characters(id,project_id,code,name,description,image_prompt,created,updated)
                          VALUES(?,?,?,?,?,?,?,?)""",
                       (character_id, project_id, unique, name, text(description, 5000), text(image_prompt, 5000), now, now))
        return {"id": character_id, "code": unique}

    def update_character(self, character_id, name=None, description=None, image_prompt=None):
        check_id(character_id, "Nhân vật")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy nhân vật.")
            if row["locked"]:
                raise ValueError("Nhân vật đã khóa. Mở khóa trước khi sửa.")
            db.execute("UPDATE characters SET name=?,description=?,image_prompt=?,updated=? WHERE id=?",
                       (text(name, 200) or row["name"] if name is not None else row["name"],
                        text(description, 5000) if description is not None else row["description"],
                        text(image_prompt, 5000) if image_prompt is not None else row["image_prompt"],
                        time.time(), character_id))

    def set_scene_characters(self, scene_id, character_ids, source="ai"):
        """Danh sách nhân vật xuất hiện trong cảnh (thay toàn bộ). Chỉ nhận nhân vật cùng dự án với cảnh."""
        check_id(scene_id, "Cảnh")
        if source not in ("ai", "manual"):
            raise ValueError("Nguồn gán nhân vật không hợp lệ.")
        if not isinstance(character_ids, list) or len(character_ids) > 50:
            raise ValueError("Danh sách nhân vật không hợp lệ.")
        character_ids = list(dict.fromkeys(check_id(c, "Nhân vật") for c in character_ids))
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scene = db.execute("SELECT project_id FROM scenes WHERE id=?", (scene_id,)).fetchone()
            if not scene:
                raise ValueError("Không tìm thấy cảnh.")
            if character_ids:
                found = db.execute(f"SELECT count(*) FROM characters WHERE project_id=? AND id IN ({','.join('?' * len(character_ids))})",
                                   [scene["project_id"], *character_ids]).fetchone()[0]
                if found != len(character_ids):
                    raise ValueError("Có nhân vật không thuộc dự án của cảnh này.")
            db.execute("DELETE FROM scene_characters WHERE scene_id=?", (scene_id,))
            db.executemany("INSERT INTO scene_characters(scene_id,character_id,source) VALUES(?,?,?)",
                           [(scene_id, c, source) for c in character_ids])

    # ---------- Ảnh Master ----------
    def add_asset(self, character_id, token, origin="flow", prompt=""):
        """Thêm ảnh ứng viên cho nhân vật (ảnh đã nằm trong thư mục ảnh của tool). Nhân vật chuyển sang chờ duyệt."""
        check_id(character_id, "Nhân vật")
        if origin not in ASSET_ORIGINS:
            raise ValueError("Nguồn ảnh không hợp lệ.")
        if not self.store.image_file(token):
            raise ValueError("Ảnh không còn tồn tại hoặc không hợp lệ.")
        asset_id, now = new_id(), time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM characters WHERE id=?", (character_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy nhân vật.")
            db.execute("INSERT INTO character_assets(id,character_id,image_token,origin,prompt,created) VALUES(?,?,?,?,?,?)",
                       (asset_id, character_id, token, origin, text(prompt, 5000), now))
            if row["status"] in ("draft", "generating"):
                db.execute("UPDATE characters SET status='review',updated=? WHERE id=?", (now, character_id))
        return asset_id

    def approve_asset(self, asset_id):
        """Chọn ảnh này làm ảnh Master đã duyệt của nhân vật. Không đổi được khi nhân vật đã khóa."""
        check_id(asset_id, "Ảnh")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            asset = db.execute("SELECT * FROM character_assets WHERE id=?", (asset_id,)).fetchone()
            if not asset:
                raise ValueError("Không tìm thấy ảnh nhân vật.")
            character = db.execute("SELECT locked FROM characters WHERE id=?", (asset["character_id"],)).fetchone()
            if character["locked"]:
                raise ValueError("Nhân vật đã khóa. Mở khóa trước khi thay ảnh Master.")
            now = time.time()
            db.execute("UPDATE character_assets SET status='candidate' WHERE character_id=? AND status='approved'",
                       (asset["character_id"],))
            db.execute("UPDATE character_assets SET status='approved' WHERE id=?", (asset_id,))
            db.execute("UPDATE characters SET approved_asset_id=?,status='approved',updated=? WHERE id=?",
                       (asset_id, now, asset["character_id"]))

    def set_locked(self, character_id, locked):
        check_id(character_id, "Nhân vật")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT approved_asset_id FROM characters WHERE id=?", (character_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy nhân vật.")
            if locked and not row["approved_asset_id"]:
                raise ValueError("Duyệt một ảnh Master trước khi khóa nhân vật.")
            db.execute("UPDATE characters SET locked=?,updated=? WHERE id=?", (int(bool(locked)), time.time(), character_id))

    def master_tokens(self, character_ids):
        """{character_id: image_token} của ảnh Master đã duyệt (bỏ nhân vật chưa duyệt hoặc ảnh đã mất)."""
        if not character_ids:
            return {}
        with self.store.connect() as db:
            rows = db.execute(f"""SELECT c.id, a.image_token FROM characters c JOIN character_assets a ON a.id=c.approved_asset_id
                                  WHERE c.id IN ({','.join('?' * len(character_ids))})""", list(character_ids)).fetchall()
        return {row["id"]: row["image_token"] for row in rows if self.store.image_file(row["image_token"])}

    # ---------- Hàng đợi tạo ảnh nhân vật (giai đoạn 3 dùng) ----------
    def queue_image(self, character_id, prompt, request_key=None):
        """Thêm tác vụ tạo ảnh Master. request_key chống tạo trùng khi bấm lại; không thêm nếu nhân vật đã có tác vụ đang chờ/chạy."""
        check_id(character_id, "Nhân vật")
        prompt = text(prompt, 5000)
        if not prompt:
            raise ValueError("Prompt ảnh nhân vật đang trống.")
        request_key = str(request_key or new_id())[:80]
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT locked FROM characters WHERE id=?", (character_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy nhân vật.")
            if row["locked"]:
                raise ValueError("Nhân vật đã khóa. Mở khóa trước khi tạo lại ảnh.")
            existing = db.execute("SELECT id FROM image_tasks WHERE request_key=? OR (character_id=? AND status IN ('waiting','running'))",
                                  (request_key, character_id)).fetchone()
            if existing:
                return existing["id"]
            task_id = new_id()
            db.execute("""INSERT INTO image_tasks(id,character_id,prompt,request_key,created,updated) VALUES(?,?,?,?,?,?)""",
                       (task_id, character_id, prompt, request_key, now, now))
            db.execute("UPDATE characters SET status='generating',updated=? WHERE id=? AND status IN ('draft','review')", (now, character_id))
        return task_id

    def recover_image_tasks(self):
        """Khởi động lại: tác vụ đang chạy mà chưa gửi lên Flow thì chờ chạy lại; đã gửi thì báo cần kiểm tra (không tự gửi lại)."""
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE image_tasks SET status='waiting',updated=? WHERE status='running' AND submitted=0", (now,))
            db.execute("""UPDATE image_tasks SET status='error',updated=?,
                          error='Tool bị tắt sau khi đã gửi prompt lên Flow. Kiểm tra Flow; không tự gửi lại.'
                          WHERE status='running' AND submitted=1""", (now,))

    # ---------- Đọc dữ liệu ----------
    def referenced_tokens(self, db):
        """Ảnh mà nhân vật đang dùng: không được dọn khi khởi động hoặc khi xóa khỏi thư viện."""
        return {row["image_token"] for row in db.execute("SELECT image_token FROM character_assets")}

    def projects(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("""SELECT p.id,p.name,p.created,p.updated,
                (SELECT count(*) FROM scenes s WHERE s.project_id=p.id) AS scenes,
                (SELECT count(*) FROM characters c WHERE c.project_id=p.id) AS characters
                FROM projects p ORDER BY p.updated DESC""")]

    def project(self, project_id):
        check_id(project_id, "Dự án")
        with self.store.connect() as db:
            project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ValueError("Không tìm thấy dự án.")
            characters = [dict(row) for row in db.execute(
                "SELECT * FROM characters WHERE project_id=? ORDER BY created, rowid", (project_id,))]
            assets = {}
            for row in db.execute("""SELECT a.* FROM character_assets a JOIN characters c ON c.id=a.character_id
                                     WHERE c.project_id=? ORDER BY a.created""", (project_id,)):
                assets.setdefault(row["character_id"], []).append(dict(row))
            tasks = {}
            for row in db.execute("""SELECT t.id,t.character_id,t.status,t.error,t.attempts FROM image_tasks t
                                     JOIN characters c ON c.id=t.character_id WHERE c.project_id=? ORDER BY t.created""", (project_id,)):
                tasks[row["character_id"]] = dict(row)  # tác vụ mới nhất của nhân vật
            scenes = [dict(row) for row in db.execute(
                "SELECT * FROM scenes WHERE project_id=? ORDER BY ordinal, rowid", (project_id,))]
            links = {}
            for row in db.execute("""SELECT sc.scene_id, sc.character_id, sc.source FROM scene_characters sc
                                     JOIN scenes s ON s.id=sc.scene_id WHERE s.project_id=?""", (project_id,)):
                links.setdefault(row["scene_id"], []).append(row["character_id"])
            jobs = {}
            for row in db.execute("""SELECT j.id, j.scene_id, j.status FROM jobs j JOIN scenes s ON s.id=j.scene_id
                                     WHERE s.project_id=?""", (project_id,)):
                jobs.setdefault(row["scene_id"], []).append({"id": row["id"], "status": row["status"]})
        for character in characters:
            character["assets"] = assets.get(character["id"], [])
            character["task"] = tasks.get(character["id"])
            character["locked"] = bool(character["locked"])
        for scene in scenes:
            scene["character_ids"] = links.get(scene["id"], [])
            scene["jobs"] = jobs.get(scene["id"], [])
        return {**dict(project), "characters": characters, "scenes": scenes}
