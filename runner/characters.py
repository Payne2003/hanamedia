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
    # Nhân vật chọn riêng cho prompt (JSON). Trống = dùng nhân vật của cảnh gốc (scene_id).
    "character_ids": "TEXT NOT NULL DEFAULT '[]'",
}
MAX_REFERENCE_IMAGES = 10  # giới hạn ảnh đầu vào mỗi prompt của chế độ Ảnh → video trên Muse

def migrate(db):
    """Cột thêm sau khi bảng đã tạo (dữ liệu của bản trước giữ nguyên)."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(scenes)")}
    if "manual_characters" not in columns:  # 1 = người dùng tự chọn nhân vật cho cảnh: AI không ghi đè
        db.execute("ALTER TABLE scenes ADD COLUMN manual_characters INTEGER NOT NULL DEFAULT 0")


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
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_runs'").fetchone():
                if db.execute("SELECT 1 FROM production_runs WHERE project_id=? AND status='running'", (project_id,)).fetchone():
                    raise ValueError("Dự án đang có agent xử lý. Dừng và đợi agent kết thúc trước khi xóa.")
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
            db.execute("UPDATE scenes SET manual_characters=?,updated=? WHERE id=?",
                       (int(source == "manual"), time.time(), scene_id))
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
                                     JOIN scenes s ON s.id=sc.scene_id WHERE s.project_id=? ORDER BY sc.rowid""", (project_id,)):
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
            scene["manual"] = bool(scene.pop("manual_characters", 0))
            scene["jobs"] = jobs.get(scene["id"], [])
        return {**dict(project), "characters": characters, "scenes": scenes}


# ================= Character Extractor (giai đoạn 2) =================
# AI (Gemini/ChatGPT) đọc kịch bản + danh sách cảnh, liệt kê nhân vật DUY NHẤT với Character ID, mô tả ngoại hình cố định,
# prompt ảnh Master tiếng Anh, và Character ID xuất hiện trong từng cảnh. Định dạng dòng văn bản để đọc chắc chắn.
END_CHARACTERS = "HẾT DANH SÁCH NHÂN VẬT"
NONE_WORDS = {"", "KHONG", "KHONG_CO", "NONE", "NO", "NULL", "N_A", "NA"}
MAX_CHARACTERS = 60
_LEAD = r"^\s*(?:\d+[.)]\s*)?[-*•–\s]*"
NV_LINE = re.compile(_LEAD + r"NV\s*\|\s*(.+)$", re.IGNORECASE)
SCENE_LINK = re.compile(_LEAD + r"C(?:[ảa]nh)?\s*(\d{1,4})\s*[|:：]\s*(.*)$", re.IGNORECASE)
LABEL_DESC = re.compile(r"^\s*m[ôo]\s*t[ảa]\s*[:：]\s*", re.IGNORECASE)
LABEL_PROMPT = re.compile(r"^\s*(?:prompt\s*[ảa]nh|image\s*prompt)\s*[:：]\s*", re.IGNORECASE)


def scene_line(number, scene):
    parts = [("Bối cảnh", scene.get("setting")), ("góc quay", scene.get("camera")),
             ("hành vi nhân vật", scene.get("behavior")), ("hành động", scene.get("action"))]
    body = ", ".join(f"{label}: {text(value, 1500)}" for label, value in parts if text(value))
    return f"C{number}: {body or text(scene.get('source') or scene.get('prompt'), 3000)}"


def extraction_request(script, scenes, numbers=None, known=None):
    """Yêu cầu gửi AI. numbers: chỉ hỏi lại các cảnh này (đánh số 1..N theo thứ tự danh sách gửi đi)."""
    total = len(scenes)
    if numbers:
        codes = ", ".join(known or []) or "(chưa có)"
        wanted = ", ".join(f"C{n}" for n in numbers)
        return ("Còn thiếu dòng nhân vật cho các cảnh: " + wanted + ".\n"
                "Chỉ viết lại các dòng đó, mỗi dòng: C<số> | <Character ID>, <Character ID> (không có nhân vật thì ghi KHONG). "
                "Chỉ dùng các Character ID đã liệt kê: " + codes + ". Nếu thật sự có nhân vật mới, thêm dòng NV như trước.\n"
                + "\n".join(scene_line(n, scenes[n - 1]) for n in numbers)
                + f"\nCuối cùng viết đúng một dòng: {END_CHARACTERS}")
    return (
        "Bạn là trợ lý tiền kỳ cho video. Đọc KỊCH BẢN và DANH SÁCH CẢNH bên dưới rồi làm 3 việc:\n"
        "1. Liệt kê MỌI nhân vật DUY NHẤT (người, con vật, sinh vật có vai trò). Một nhân vật xuất hiện ở nhiều cảnh chỉ liệt kê "
        "MỘT lần. Một nhóm giống hệt nhau không phân biệt từng cá thể (ví dụ “đàn sói”) có thể là một nhân vật nhóm. "
        "Không liệt kê đồ vật, bối cảnh hay thời tiết.\n"
        "2. Mỗi nhân vật: Character ID viết HOA, không dấu, nối bằng dấu _ (ví dụ DE_DUC, BAO_TUYET); tên tiếng Việt; "
        "mô tả ngoại hình CỐ ĐỊNH để mọi cảnh giống nhau (loài/giới tính/tuổi, màu lông hoặc da, vóc dáng, đặc điểm nhận dạng, "
        "trang phục nếu có); và một prompt ảnh tham chiếu TIẾNG ANH cho ảnh Master của nhân vật: chỉ một nhân vật, toàn thân, "
        "nhìn chính diện, đứng yên, nền trơn trung tính, ánh sáng studio đều, ảnh chân thực, không chữ, không logo, "
        "KHÔNG có bối cảnh của cảnh quay.\n"
        "3. Với TỪNG cảnh C1 đến C" + str(total) + ", ghi các Character ID xuất hiện trong cảnh đó; cảnh không có nhân vật ghi KHONG.\n\n"
        "ĐỊNH DẠNG BẮT BUỘC (mỗi mục một dòng, không bảng, không markdown, không giải thích thêm):\n"
        "NV | DE_DUC | Dê đực đầu đàn | Mô tả: dê núi ibex đực trưởng thành, lông xám nâu, cặp sừng cong lớn | "
        "Prompt ảnh: full body photo of an adult male alpine ibex with large curved horns, grey-brown coat, front view, "
        "standing still, plain neutral grey background, even studio lighting, photorealistic, no text\n"
        "C1 | DE_DUC, DE_CON\n"
        "C2 | KHONG\n"
        f"Viết đủ các dòng C1 đến C{total}. Cuối cùng viết đúng một dòng: {END_CHARACTERS}\n\n"
        "KỊCH BẢN:\n" + (str(script or "").strip()[:30000] or "(không có, chỉ dựa vào danh sách cảnh)") + "\n\n"
        "DANH SÁCH CẢNH:\n" + "\n".join(scene_line(n, scene) for n, scene in enumerate(scenes, 1)))


def parse_characters(answer, total, known=()):
    """Đọc câu trả lời: {"characters": [{code,name,description,image_prompt}], "scenes": {n: [code]}, "unknown": [code]}.
    known: Character ID đã có từ lượt trước (lượt hỏi lại thường chỉ có dòng C…)."""
    characters, scenes, order = {}, {}, []
    for raw in str(answer or "").replace("\r", "").split("\n"):
        line = re.sub(r"[*`#>]+", "", raw).strip()  # giữ dấu _ của Character ID
        match = NV_LINE.match(line)
        if match:
            parts = [part.strip() for part in match.group(1).split("|")]
            code = character_code(parts[0]) if parts and parts[0] else ""
            if not parts or not parts[0].strip():
                continue
            name, description, prompt = (parts[1] if len(parts) > 1 else "") or code.replace("_", " ").title(), "", ""
            for part in parts[2:]:
                if LABEL_PROMPT.match(part):
                    prompt = LABEL_PROMPT.sub("", part)
                elif LABEL_DESC.match(part):
                    description = LABEL_DESC.sub("", part)
                elif not description:
                    description = part
                elif not prompt:
                    prompt = part
            if code not in characters and len(characters) < MAX_CHARACTERS:
                order.append(code)
            if len(characters) < MAX_CHARACTERS or code in characters:
                characters[code] = {"code": code, "name": text(name, 200), "description": text(description, 5000),
                                    "image_prompt": text(prompt, 5000)}
            continue
        match = SCENE_LINK.match(line)
        if match and 1 <= int(match.group(1)) <= total:
            codes = []
            for item in re.split(r"[,;、]+", match.group(2)):
                code = character_code(item) if item.strip() else ""
                if code and code not in NONE_WORDS and code != "NHAN_VAT" and code not in codes:
                    codes.append(code)
            scenes[int(match.group(1))] = codes
    valid = set(characters) | set(known)
    unknown = sorted({code for codes in scenes.values() for code in codes if code not in valid})
    scenes = {n: [code for code in codes if code in valid] for n, codes in scenes.items()}
    return {"characters": [characters[code] for code in order], "scenes": scenes, "unknown": unknown}


def missing_scenes(scenes, total):
    return [n for n in range(1, total + 1) if n not in scenes]


def merge_extraction(result, extra):
    """Gộp lượt hỏi lại: nhân vật mới thêm vào cuối, dòng cảnh mới bổ sung (không ghi đè cảnh đã có)."""
    known = {c["code"] for c in result["characters"]}
    result["characters"] += [c for c in extra["characters"] if c["code"] not in known]
    known = {c["code"] for c in result["characters"]}
    for n, codes in extra["scenes"].items():
        result["scenes"].setdefault(n, [code for code in codes if code in known])
    result["unknown"] = sorted(set(result["unknown"]) | {c for c in extra["unknown"] if c not in known})
    return result


def _apply_extraction(registry, project_id, scene_ids, result):
    """Lưu kết quả phân tích vào dự án. scene_ids: Scene ID theo đúng thứ tự đã gửi cho AI (C1 = scene_ids[0]).
    Nhân vật đã có (cùng Character ID): chỉ cập nhật khi còn bản nháp và chưa khóa. Cảnh người dùng đã tự gán thì giữ nguyên."""
    check_id(project_id, "Dự án")
    with registry.store.connect() as db:
        existing = {row["code"]: dict(row) for row in db.execute("SELECT * FROM characters WHERE project_id=?", (project_id,))}
        manual = {row["id"] for row in db.execute(
            "SELECT id FROM scenes WHERE project_id=? AND manual_characters=1", (project_id,))}
        valid = {row["id"] for row in db.execute("SELECT id FROM scenes WHERE project_id=?", (project_id,))}
    ids, added, updated = {}, 0, 0
    for character in result.get("characters", []):
        old = existing.get(character["code"])
        if old:
            ids[character["code"]] = old["id"]
            if old["status"] == "draft" and not old["locked"]:
                registry.update_character(old["id"], character["name"], character["description"] or old["description"],
                                          character["image_prompt"] or old["image_prompt"])
                updated += 1
        else:
            made = registry.add_character(project_id, character["name"], character["description"],
                                          character["image_prompt"], code=character["code"])
            ids[character["code"]] = made["id"]
            added += 1
    linked = kept = 0
    for number, codes in sorted(result.get("scenes", {}).items(), key=lambda item: int(item[0])):
        number = int(number)
        if not 1 <= number <= len(scene_ids) or scene_ids[number - 1] not in valid:
            continue
        scene_id = scene_ids[number - 1]
        if scene_id in manual:
            kept += 1
            continue
        registry.set_scene_characters(scene_id, [ids[code] for code in codes if code in ids], source="ai")
        linked += 1
    return {"added": added, "updated": updated, "linked": linked, "kept_manual": kept}


def _delete_character(registry, character_id):
    check_id(character_id, "Nhân vật")
    with registry.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT locked FROM characters WHERE id=?", (character_id,)).fetchone()
        if not row:
            raise ValueError("Không tìm thấy nhân vật.")
        if row["locked"]:
            raise ValueError("Nhân vật đã khóa. Mở khóa trước khi xóa.")
        if db.execute("SELECT 1 FROM image_tasks WHERE character_id=? AND status='running'", (character_id,)).fetchone():
            raise ValueError("Nhân vật đang được tạo ảnh. Đợi xong rồi xóa.")
        db.execute("DELETE FROM characters WHERE id=?", (character_id,))
    registry.store.cleanup_images()


def _scene_inputs(registry, project_id):
    """Danh sách cảnh (thứ tự hiển thị) và Scene ID tương ứng để gửi cho AI."""
    project = registry.project(project_id)
    return project, [scene["id"] for scene in project["scenes"]], project["scenes"]


CharacterRegistry.apply_extraction = _apply_extraction
CharacterRegistry.delete_character = _delete_character
CharacterRegistry.scene_inputs = _scene_inputs


# ================= Nhân vật & ảnh tham chiếu của từng prompt (Prompt Studio) =================
class NeedsConfirm(ValueError):
    """Thao tác cần người dùng xác nhận (ví dụ thay ảnh tự gắn, đổi chế độ video)."""


def _character_rows(db, ids):
    if not ids:
        return {}
    rows = db.execute(f"""SELECT c.*, a.image_token AS master_token FROM characters c
                          LEFT JOIN character_assets a ON a.id=c.approved_asset_id
                          WHERE c.id IN ({','.join('?' * len(ids))})""", list(ids)).fetchall()
    return {row["id"]: dict(row) for row in rows}


def _public(registry, row):
    token = row.get("master_token")
    if token and not registry.store.image_file(token):
        token = None
    return {"id": row["id"], "code": row["code"], "name": row["name"], "status": row["status"],
            "locked": bool(row["locked"]), "project_id": row["project_id"], "master_token": token}


def _job_characters(registry, job_id, project_id=None):
    """Nhân vật của prompt: chọn riêng cho prompt, nếu không thì lấy theo cảnh gốc. Kèm danh sách nhân vật có thể chọn
    (của dự án chứa cảnh/nhân vật, hoặc dự án đang mở ở Content Studio) và trạng thái gắn ảnh Master."""
    check_id(job_id, "Prompt")
    with registry.store.connect() as db:
        job = db.execute("SELECT scene_id, character_ids, images, images_origin, generation_mode FROM jobs WHERE id=?",
                         (job_id,)).fetchone()
        if not job:
            raise ValueError("Không tìm thấy prompt này trong hàng đợi.")
        own = [c for c in json.loads(job["character_ids"] or "[]") if isinstance(c, str) and ID.fullmatch(c)]
        scene = db.execute("SELECT id, project_id, ordinal FROM scenes WHERE id=?", (job["scene_id"],)).fetchone() if job["scene_id"] else None
        scene_ids = [row["character_id"] for row in db.execute(
            "SELECT character_id FROM scene_characters WHERE scene_id=? ORDER BY rowid", (scene["id"],))] if scene else []
        ids, source = (own, "prompt") if own else (scene_ids, "scene") if scene else ([], "none")
        rows = _character_rows(db, ids)
        project = (scene["project_id"] if scene else None) or next((rows[c]["project_id"] for c in ids if c in rows), None)
        if not project and project_id and ID.fullmatch(str(project_id)):
            project = project_id
        project_row = db.execute("SELECT id, name FROM projects WHERE id=?", (project,)).fetchone() if project else None
        options = [dict(row) for row in db.execute(
            """SELECT c.*, a.image_token AS master_token FROM characters c LEFT JOIN character_assets a ON a.id=c.approved_asset_id
               WHERE c.project_id=? ORDER BY c.created, c.rowid""", (project_row["id"],))] if project_row else []
    selected = [_public(registry, rows[c]) for c in ids if c in rows]
    images = json.loads(job["images"] or "[]")
    masters = [c["master_token"] for c in selected if c["master_token"]]
    attached = [token for token in masters if token in images]
    if not selected:
        state = "none"
    elif len(attached) == len(selected) and len(images) == len(attached):
        state = "attached"
    elif any(not c["master_token"] for c in selected):
        state = "missing_master"
    else:
        state = "not_attached"
    return {"source": source, "scene_number": scene["ordinal"] + 1 if scene else None,
            "project": dict(project_row) if project_row else None,
            "selected": selected, "options": [_public(registry, row) for row in options],
            "attached": len(attached), "state": state, "images_origin": job["images_origin"],
            "mode": job["generation_mode"], "limit": MAX_REFERENCE_IMAGES}


def _set_job_characters(registry, job_id, character_ids):
    """Chọn nhân vật riêng cho prompt (danh sách rỗng = quay lại dùng nhân vật của cảnh gốc)."""
    check_id(job_id, "Prompt")
    if not isinstance(character_ids, list) or len(character_ids) > 50:
        raise ValueError("Danh sách nhân vật không hợp lệ.")
    ids = list(dict.fromkeys(check_id(c, "Nhân vật") for c in character_ids))
    with registry.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("Không tìm thấy prompt này trong hàng đợi.")
        if job["status"] == "running":
            raise ValueError("Prompt đang chạy, không đổi nhân vật được.")
        if ids and db.execute(f"SELECT count(*) FROM characters WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()[0] != len(ids):
            raise ValueError("Có nhân vật không còn tồn tại. Tải lại trang rồi thử lại.")
        db.execute("UPDATE jobs SET character_ids=?,updated=? WHERE id=?", (json.dumps(ids), time.time(), job_id))


def _attach_job_characters(registry, job_id, confirm=False):
    """Auto Character Mapper cho một prompt: gắn ảnh Master đã duyệt của đúng các nhân vật trong prompt vào jobs.images.
    Không gắn nhân vật không có trong prompt; không tạo ảnh mới; không đụng prompt đang chạy. Cần xác nhận khi
    sẽ thay ảnh người dùng tự gắn hoặc khi phải chuyển prompt sang chế độ Ảnh → video."""
    info = registry.job_characters(job_id)
    if not info["selected"]:
        raise ValueError("Prompt chưa có nhân vật. Chọn nhân vật cho prompt (hoặc cho cảnh gốc) trước.")
    missing = [c["code"] for c in info["selected"] if not c["master_token"]]
    if missing:
        raise ValueError("Chưa có ảnh Master đã duyệt cho: " + ", ".join(missing) + ". Duyệt ảnh nhân vật ở Character Library trước.")
    tokens = list(dict.fromkeys(c["master_token"] for c in info["selected"]))
    if len(tokens) > MAX_REFERENCE_IMAGES:
        raise ValueError(f"Prompt có {len(tokens)} nhân vật nhưng chế độ Ảnh → video của Muse chỉ nhận tối đa "
                         f"{MAX_REFERENCE_IMAGES} ảnh. Bỏ bớt nhân vật của prompt này.")
    with registry.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT status, images, images_origin, generation_mode FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job["status"] == "running":
            raise ValueError("Prompt đang chạy, không gắn ảnh được.")
        current = json.loads(job["images"] or "[]")
        if not confirm:
            notes = []
            if current and job["images_origin"] == "manual" and sorted(current) != sorted(tokens):
                notes.append(f"thay {len(current)} ảnh bạn tự gắn bằng {len(tokens)} ảnh nhân vật")
            if job["generation_mode"] != "image":
                notes.append("chuyển prompt sang chế độ Ảnh → video (Muse dùng ảnh nhân vật làm ảnh tham chiếu)")
            if notes:
                raise NeedsConfirm("Gắn ảnh nhân vật sẽ " + " và ".join(notes) + ". Tiếp tục?")
        db.execute("UPDATE jobs SET images=?,generation_mode='image',images_origin='auto',updated=? WHERE id=?",
                   (json.dumps(tokens), time.time(), job_id))
    registry.store.cleanup_images()
    return tokens


CharacterRegistry.job_characters = _job_characters
CharacterRegistry.set_job_characters = _set_job_characters
CharacterRegistry.attach_job_characters = _attach_job_characters
