import json
import hashlib
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from .content import parse_structured
from .cookies import CookieStore
from .media import SOURCES as MEDIA_SOURCES, MediaLibrary
from .characters import JOB_COLUMNS, SCHEMA as CHARACTER_SCHEMA, CharacterRegistry, migrate as migrate_characters


def number(value, minimum=0):
    value = float(value)
    if not math.isfinite(value) or value < minimum or value > 1_000_000:
        raise ValueError("Số credit không hợp lệ.")
    return value


def mailbox_for(email, value):
    email = email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
        raise ValueError("Email không hợp lệ.")
    if not value and email.endswith("@tuan89.site"):
        value = "https://tuan89.site/?" + urlencode({"email": email})
    if value:
        url = urlparse(value)
        if (url.scheme != "https" or url.hostname != "tuan89.site" or
                url.username or url.password or url.port or url.path not in ("", "/") or
                parse_qs(url.query).get("email") != [email]):
            raise ValueError("Link hộp thư phải là https://tuan89.site/?email= đúng email này.")
        value = "https://tuan89.site/?" + urlencode({"email": email})
    return email, value


# Trạng thái tài khoản đang giữ Chrome của nó: không cấu hình, xóa, đăng nhập hay kiểm tra chồng lên.
LOCKED_STATES = ("busy", "authenticating", "reviewing", "checking")
ACCOUNT_COLUMNS = {
    "health": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
    "last_checked": "REAL NOT NULL DEFAULT 0",
    "last_error": "TEXT NOT NULL DEFAULT ''",
    "check_failures": "INTEGER NOT NULL DEFAULT 0",
    "next_check": "REAL NOT NULL DEFAULT 0",
    "health_before": "TEXT NOT NULL DEFAULT ''",
}


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.image_dir = Path(self.path).parent / "input-images"
        # Ảnh của Thư viện Video & Ảnh: data/media/<uploaded|generated|imported>/images/<token>.<đuôi>
        self.media_dirs = [Path(self.path).parent / "media" / source / "images" for source in MEDIA_SOURCES]
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, mailbox TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, budget REAL NOT NULL,
                state TEXT NOT NULL DEFAULT 'new', detail TEXT NOT NULL DEFAULT '',
                last_used REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
                phase TEXT NOT NULL DEFAULT 'Đợi tài khoản', account_id TEXT,
                cost REAL NOT NULL, submitted INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', output TEXT, created REAL NOT NULL,
                updated REAL NOT NULL, source_url TEXT NOT NULL DEFAULT '',
                batch TEXT NOT NULL, ordinal INTEGER NOT NULL,
                assigned_account_id TEXT,
                UNIQUE(batch, ordinal));
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS image_library (
                token TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL);
            """)
            db.executescript(CHARACTER_SCHEMA)  # dự án, cảnh, nhân vật, ảnh Master, hàng đợi ảnh nhân vật
            migrate_characters(db)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            migrations = {
                "images": "TEXT NOT NULL DEFAULT '[]'",
                "generation_mode": "TEXT NOT NULL DEFAULT 'text'",
                "assigned_account_id": "TEXT",
                "progress": "INTEGER NOT NULL DEFAULT 0",
                # 1 = người dùng tự chọn tài khoản trong Prompt Studio: không chuyển sang tài khoản khác.
                "assigned_manual": "INTEGER NOT NULL DEFAULT 0",
                # Ghi chú cảnh cho Prompt Studio: bối cảnh, góc quay, hành vi nhân vật, nội dung hành động (JSON).
                "details": "TEXT NOT NULL DEFAULT '{}'",
                **JOB_COLUMNS,  # scene_id, images_origin (Character Master)
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            # Account Health Monitor: tình trạng phiên Muse đo được, tách khỏi state (state vẫn điều phối tác vụ).
            account_columns = {row["name"] for row in db.execute("PRAGMA table_info(accounts)")}
            for name, declaration in ACCOUNT_COLUMNS.items():
                if name not in account_columns:
                    db.execute(f"ALTER TABLE accounts ADD COLUMN {name} {declaration}")
            db.execute("UPDATE accounts SET health='LIVE' WHERE health='READY'")  # tên cũ của bản thử đầu
        self.characters = CharacterRegistry(self)
        self.media = MediaLibrary(self)
        self.cookies = CookieStore(self)  # Cookie Manager: chỉ lưu bản mã hóa
        self.cleanup_images()
        self.media.purge_hidden()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for job in db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
                if not job["submitted"]:
                    db.execute("UPDATE accounts SET budget=budget+? WHERE id=?", (job["cost"], job["account_id"]))
                detail = ("Bộ chạy bị tắt sau khi bắt đầu gửi. Kiểm tra Muse; không tự gửi lại."
                          if job["submitted"] else "Bộ chạy bị tắt trước khi gửi. Có thể thử lại.")
                db.execute("UPDATE jobs SET status='error',phase='Bị gián đoạn',error=?,updated=? WHERE id=?",
                           (detail, time.time(), job["id"]))
            db.execute("UPDATE accounts SET state='new',detail='Cần kiểm tra lại phiên' WHERE state IN ('busy','authenticating','reviewing','checking')")
            db.execute("UPDATE accounts SET health=CASE WHEN health_before!='' THEN health_before ELSE 'UNKNOWN' END,"
                       "health_before='' WHERE health='CHECKING'")
        self.characters.recover_image_tasks()

    def add_account(self, email, mailbox, budget):
        email, mailbox = mailbox_for(email, mailbox)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT INTO accounts(id,email,mailbox,budget) VALUES(?,?,?,?)",
                           (uuid.uuid4().hex, email, mailbox, number(budget)))
            except sqlite3.IntegrityError:
                raise ValueError("Email này đã có trong danh sách.") from None
        self.plan_waiting()

    def add_accounts(self, text, budget):
        """Thêm hàng loạt: mỗi dòng (hoặc cách nhau bởi dấu phẩy/khoảng trắng) một email. Email @tuan89.site tự có link
        hộp thư OTP. Bỏ qua email trùng; báo email sai định dạng."""
        emails = [e for e in re.split(r"[\s,;]+", str(text or "")) if e]
        if not emails:
            raise ValueError("Nhập ít nhất một email.")
        if len(emails) > 2000:
            raise ValueError("Mỗi lần thêm tối đa 2.000 email.")
        number(budget)
        added, duplicate, invalid, seen = 0, 0, [], set()
        for raw in emails:
            try:
                email, mailbox = mailbox_for(raw, "")
            except ValueError:
                invalid.append(raw[:80])
                continue
            if email in seen:
                duplicate += 1
                continue
            seen.add(email)
            with self.connect() as db:
                try:
                    db.execute("INSERT INTO accounts(id,email,mailbox,budget) VALUES(?,?,?,?)",
                               (uuid.uuid4().hex, email, mailbox, number(budget)))
                    added += 1
                except sqlite3.IntegrityError:
                    duplicate += 1
        self.plan_waiting()
        return {"added": added, "duplicate": duplicate, "invalid": invalid[:20], "invalid_count": len(invalid)}

    def account(self, account_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy tài khoản.")
            return dict(row)

    def set_account(self, account_id, state, detail=""):
        with self.connect() as db:
            db.execute("UPDATE accounts SET state=?,detail=? WHERE id=?", (state, detail, account_id))
            if state == "ready":  # chỉ gọi 'ready' sau khi đã thấy ô soạn tin Muse: đó là bằng chứng phiên còn sống
                db.execute("UPDATE accounts SET health='LIVE',last_checked=?,last_error='',check_failures=0 WHERE id=?",
                           (time.time(), account_id))

    def begin_check(self, account_id):
        """Giữ chỗ tài khoản để kiểm tra phiên. Trả về {state, health} trước đó, hoặc None nếu tài khoản đang chạy/đăng nhập/
        đang mở phiên/đang được kiểm tra (không bao giờ chen vào tác vụ đang chạy)."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,health FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row or row["state"] in LOCKED_STATES:
                return None
            db.execute("UPDATE accounts SET state='checking',health_before=health,health='CHECKING' WHERE id=?", (account_id,))
            return dict(row)

    def end_check(self, account_id, previous, health, error=""):
        """Ghi kết quả kiểm tra. Chỉ LIVE / LOGIN_REQUIRED / EXPIRED đổi state điều phối; lỗi mạng, 429 và lỗi chưa rõ
        giữ nguyên state cũ (không đánh dấu tài khoản hỏng) và tăng bộ đếm lỗi để lịch kiểm tra giãn ra."""
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE id=? AND state='checking'", (account_id,)).fetchone()
            if not row:
                return
            if health == "LIVE":
                state, detail, failures = "ready", "Đã kiểm tra: phiên Muse còn đăng nhập", 0
            elif health in ("LOGIN_REQUIRED", "EXPIRED"):
                state, detail, failures = "attention", error, 0
            else:
                state, detail, failures = previous, row["detail"], row["check_failures"] + 1
            db.execute("""UPDATE accounts SET state=?,detail=?,health=?,last_checked=?,last_error=?,check_failures=?,
                          health_before='' WHERE id=?""",
                       (state, detail, health, now, "" if health == "LIVE" else error, failures, account_id))

    def configure_account(self, account_id, enabled, budget):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row or row["state"] in LOCKED_STATES:
                raise ValueError("Tài khoản đang bận hoặc không tồn tại.")
            db.execute("UPDATE accounts SET enabled=?,budget=? WHERE id=?", (int(bool(enabled)), number(budget), account_id))
        self.plan_waiting()

    def delete_account(self, account_id):
        """Xóa tài khoản khỏi danh sách. Prompt đang chờ được chia lại cho tài khoản khác; video đã tải giữ nguyên."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy tài khoản.")
            if row["state"] in LOCKED_STATES or db.execute(
                    "SELECT 1 FROM jobs WHERE status='running' AND account_id=?", (account_id,)).fetchone():
                raise ValueError("Tài khoản đang chạy, đăng nhập hoặc đang mở phiên. Đợi xong hoặc đóng phiên rồi xóa.")
            db.execute("UPDATE jobs SET assigned_account_id=NULL WHERE status='waiting' AND assigned_account_id=?",
                       (account_id,))
            db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            email = row["email"]
        self.plan_waiting()
        return email

    def reserve_login(self, account_id, state="authenticating", detail="Mở Chrome"):
        """Giữ chỗ tài khoản cho đăng nhập/mở phiên. Từ chối nếu tài khoản đang chạy tác vụ hoặc đang đăng nhập."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE accounts SET state=?,detail=? WHERE id=? AND state NOT IN ('busy','authenticating','reviewing','checking')",
                                (state, detail, account_id))
            if cursor.rowcount != 1:
                raise ValueError("Tài khoản đang bận hoặc không tồn tại.")

    def scene_details(self, source):
        """Tách "Bối cảnh N: ..., góc quay: ..., hành vi nhân vật: ..., Nội dung hành động nhân vật: ...;" thành các trường."""
        source = re.sub(r"\s+", " ", str(source or "")).strip()[:5000]
        if not source:
            return {}
        match = self.SCENE_PROMPT.match(source)
        if match:
            return {field: match.group(field).strip() for field in self.DETAIL_FIELDS}
        return {"setting": source}

    def prompt_details(self, prompt):
        """Trường lấy thẳng từ prompt: khung chuẩn tiếng Anh (Setting/Camera/Character behavior/Action) hoặc
        dạng "Bối cảnh N: ..., góc quay: ..., ..." tiếng Việt. {} nếu prompt không có trường nào."""
        fields = parse_structured(prompt)
        if sum(bool(fields.get(field)) for field in self.DETAIL_FIELDS) >= 2:
            return {field: fields.get(field, "") for field in self.DETAIL_FIELDS}
        match = self.SCENE_PROMPT.match(str(prompt or ""))
        if match:
            return {field: match.group(field).strip() for field in self.DETAIL_FIELDS}
        return {}

    def enqueue(self, prompts, cost, batch, images=None, mode="text", sources=None, scene_ids=None):
        """sources: (không bắt buộc) dòng cảnh tiếng Việt gốc của từng prompt, để Prompt Studio hiện đủ các trường.
        scene_ids: (không bắt buộc) Scene ID của từng prompt (cảnh trong dự án Content Studio)."""
        if not isinstance(prompts, list) or not 1 <= len(prompts) <= 200:
            raise ValueError("Mỗi lần thêm từ 1 đến 200 prompt.")
        if not isinstance(batch, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", batch):
            raise ValueError("Mã yêu cầu không hợp lệ.")
        if any(not isinstance(p, str) or not p.strip() or len(p) > 20000 for p in prompts):
            raise ValueError("Prompt trống hoặc dài quá 20.000 ký tự.")
        if mode not in ("text", "image"):
            raise ValueError("Chế độ tạo video không hợp lệ.")
        if images is None:
            images = [[] for _ in prompts]
        if (not isinstance(images, list) or len(images) != len(prompts) or
                any(not isinstance(group, list) or len(group) > 10 for group in images)):
            raise ValueError("Danh sách ảnh không khớp prompt hoặc vượt quá 10 ảnh mỗi prompt.")
        for group in images:
            if any(not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token)
                   or not self.image_file(token) for token in group):
                raise ValueError("Ảnh đầu vào không còn tồn tại hoặc không hợp lệ.")
        if mode == "image" and any(not group for group in images):
            raise ValueError("Chế độ ảnh → video yêu cầu ít nhất một ảnh cho mỗi prompt.")
        sources = sources if isinstance(sources, list) else []
        if len(sources) > len(prompts) or any(not isinstance(item, (str, type(None))) for item in sources):
            raise ValueError("Danh sách cảnh gốc không khớp prompt.")
        scene_ids = scene_ids if isinstance(scene_ids, list) else []
        if len(scene_ids) > len(prompts) or any(
                item is not None and (not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{32}", item)) for item in scene_ids):
            raise ValueError("Danh sách cảnh không khớp prompt.")
        scene_ids = scene_ids + [None] * (len(prompts) - len(scene_ids))
        cost = number(cost, 0.01)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            wanted = list({item for item in scene_ids if item})
            existing = {row["id"] for row in db.execute(
                f"SELECT id FROM scenes WHERE id IN ({','.join('?' * len(wanted))})", wanted)} if wanted else set()
            scene_ids = [item if item in existing else None for item in scene_ids]  # cảnh đã xóa: bỏ liên kết, vẫn thêm prompt
            previous = db.execute("SELECT prompt,cost,images,generation_mode FROM jobs WHERE batch=? ORDER BY ordinal",
                                  (batch,)).fetchall()
            if previous:
                previous_images = [
                    [self._image_digest(token) for token in json.loads(row["images"] or "[]")]
                    for row in previous
                ]
                current_images = [[self._image_digest(token) for token in group] for group in images]
                if ([(r["prompt"], r["cost"], r["generation_mode"]) for r in previous] !=
                        [(p.strip(), cost, mode) for p in prompts] or previous_images != current_images):
                    raise ValueError("Mã yêu cầu đã được dùng cho nội dung khác.")
                return
            if db.execute("SELECT count(*) FROM jobs").fetchone()[0] + len(prompts) > 10000:
                raise ValueError("Đã đạt giới hạn 10.000 tác vụ trong dữ liệu này.")
            now = time.time()
            # Ưu tiên trường có sẵn trong chính prompt (khung chuẩn); không có thì lấy từ dòng cảnh gốc.
            details = [self.prompt_details(p) or self.scene_details(sources[n] if n < len(sources) else "")
                       for n, p in enumerate(prompts)]
            db.executemany("INSERT INTO jobs(id,prompt,cost,created,updated,batch,ordinal,images,generation_mode,details,scene_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           [(uuid.uuid4().hex, p.strip(), cost, now, now, batch, n, json.dumps(images[n]), mode,
                             json.dumps(details[n], ensure_ascii=False), scene_ids[n])
                            for n, p in enumerate(prompts)])
        self.plan_waiting()

    @staticmethod
    def image_type(data):
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png", "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg", "image/jpeg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return ".gif", "image/gif"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp", "image/webp"
        return None, None

    def add_image(self, data):
        if not isinstance(data, bytes) or not data or len(data) > 10_000_000:
            raise ValueError("Ảnh phải có dung lượng tối đa 10 MB.")
        extension, content_type = self.image_type(data)
        if not extension:
            raise ValueError("Chỉ nhận ảnh PNG, JPG, GIF hoặc WebP hợp lệ.")
        self.image_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        (self.image_dir / (token + extension)).write_bytes(data)
        return {"token": token, "type": content_type}

    def add_library_image(self, token, name):
        """Giữ cho mã cũ: đưa một ảnh đã có (token) vào Thư viện Video & Ảnh."""
        image = self.image_file(token)
        if not image:
            raise ValueError("Ảnh không còn tồn tại hoặc không hợp lệ.")
        name = Path(str(name)).name.strip()[:160] or "Ảnh tham chiếu"
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO image_library(token,name,created) VALUES(?,?,?)",
                       (token, name, time.time()))
            self.media.insert_existing(db, token, image[0], "uploaded", name)
        return token

    def library_images(self):
        """Ảnh trong Thư viện Video & Ảnh theo dạng cũ {token, name, created, type} (hộp chọn ảnh cho prompt)."""
        return [{"token": item["id"], "name": item["display_name"], "created": item["created_at"], "type": item["mime_type"]}
                for item in self.media.list("image")["items"] if item["available"]]

    def delete_library_images(self, tokens):
        """Xóa khỏi thư viện; ảnh còn được prompt/nhân vật dùng chỉ bị ẩn và giữ file."""
        if (not isinstance(tokens, list) or not 1 <= len(tokens) <= 200 or
                any(not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token)
                    for token in tokens)):
            raise ValueError("Chọn từ 1 đến 200 ảnh hợp lệ để xóa.")
        tokens = list(dict.fromkeys(tokens))
        with self.connect() as db:
            present = [row[0] for row in db.execute(
                f"SELECT media_id FROM media WHERE hidden=0 AND media_id IN ({','.join('?' * len(tokens))})", tokens)]
        if not present:
            return 0
        result = self.media.delete(present, confirmed=True)
        return result["deleted"] + result["hidden"]

    def import_flow_downloads(self, folder):
        """Nhập ảnh Flow đã tải vào thư viện (nguồn: generated). Trùng nội dung (SHA-256) thì bỏ qua."""
        folder = Path(folder)
        if not folder.is_dir():
            return 0
        imported = 0
        for path in sorted(folder.iterdir(), key=lambda item: item.stat().st_mtime):
            if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                continue
            try:
                _, duplicate = self.media.add_image(path.read_bytes(), path.name, source="generated", strict=False)
            except (ValueError, OSError):
                continue
            imported += not duplicate
        return imported

    def image_file(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            return None
        for extension, content_type in ((".png", "image/png"), (".jpg", "image/jpeg"),
                                        (".gif", "image/gif"), (".webp", "image/webp")):
            for folder in (self.image_dir, *self.media_dirs):
                path = folder / (token + extension)
                if path.is_file():
                    return path, content_type
        return None

    def _image_digest(self, token):
        image = self.image_file(token)
        if not image:
            raise ValueError("Ảnh đầu vào không còn tồn tại hoặc không hợp lệ.")
        return hashlib.sha256(image[0].read_bytes()).hexdigest()

    def job_image(self, job_id, index):
        with self.connect() as db:
            row = db.execute("SELECT images FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        try:
            images = json.loads(row["images"])
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(images, list) or not 0 <= index < len(images):
            return None
        return self.image_file(images[index])

    def cleanup_images(self):
        if not self.image_dir.is_dir():
            return
        with self.connect() as db:
            referenced = {token for row in db.execute("SELECT images FROM jobs")
                          for token in json.loads(row["images"] or "[]")}
            referenced.update(row["token"] for row in db.execute("SELECT token FROM image_library"))
            referenced.update(self.characters.referenced_tokens(db))
            referenced.update(row[0] for row in db.execute("SELECT media_id FROM media WHERE hidden=0"))
        for path in self.image_dir.iterdir():
            if path.is_file() and path.stem not in referenced and re.fullmatch(r"[a-f0-9]{32}", path.stem):
                path.unlink()

    def delete_jobs(self, job_ids):
        if (not isinstance(job_ids, list) or not 1 <= len(job_ids) <= 2000 or
                any(not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id)
                    for job_id in job_ids)):
            raise ValueError("Chọn từ 1 đến 2.000 prompt hợp lệ để xóa.")
        job_ids = list(dict.fromkeys(job_ids))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"SELECT id,status,images FROM jobs WHERE id IN ({','.join('?' for _ in job_ids)})",
                job_ids).fetchall()
            if len(rows) != len(job_ids):
                raise ValueError("Một số prompt đã bị xóa hoặc không còn tồn tại. Làm mới hàng đợi.")
            if any(row["status"] == "running" for row in rows):
                raise ValueError("Không thể xóa prompt đang chạy. Đợi tác vụ kết thúc rồi thử lại.")
            tokens = {token for row in rows for token in json.loads(row["images"] or "[]")}
            db.executemany("DELETE FROM jobs WHERE id=?", [(job_id,) for job_id in job_ids])
            remaining = {token for row in db.execute("SELECT images FROM jobs")
                         for token in json.loads(row["images"] or "[]")}
            remaining.update(row["token"] for row in db.execute("SELECT token FROM image_library"))
            remaining.update(row[0] for row in db.execute("SELECT media_id FROM media WHERE hidden=0"))
            remaining.update(self.characters.referenced_tokens(db))
        for token in tokens - remaining:
            image = self.image_file(token)
            if image:
                image[0].unlink(missing_ok=True)
        return len(job_ids)

    def claim(self, limit):
        self.plan_waiting()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM accounts WHERE state IN ('busy','authenticating','reviewing')").fetchone()[0] >= limit:
                return None
            pick = """SELECT j.id AS job_id, a.id AS account_id FROM jobs j JOIN accounts a
                ON a.id=j.assigned_account_id AND a.enabled=1 AND a.state IN ('new','ready')
                AND a.budget+0.000001>=j.cost
                WHERE j.status='waiting' ORDER BY j.created,j.rowid LIMIT 1"""
            pair = db.execute(pick).fetchone()
            if not pair and self.rebalance(db):
                pair = db.execute(pick).fetchone()
            if not pair:
                return None
            job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (pair["job_id"],)).fetchone())
            image_files = [self.image_file(token) for token in json.loads(job["images"] or "[]")]
            if any(not image for image in image_files):
                db.execute("UPDATE jobs SET status='error',phase='Thiếu ảnh tham chiếu',error=?,updated=? WHERE id=?",
                           ("Ảnh tham chiếu không còn trong thư mục dữ liệu. Gắn lại ảnh bằng một prompt mới.",
                            time.time(), job["id"]))
                return None
            job["images"] = [str(image[0]) for image in image_files]
            account = dict(db.execute("SELECT * FROM accounts WHERE id=?", (pair["account_id"],)).fetchone())
            db.execute("UPDATE jobs SET status='running',phase='Đăng nhập Muse',progress=3,account_id=?,updated=? WHERE id=?",
                       (account["id"], time.time(), job["id"]))
            db.execute("UPDATE accounts SET state='busy',detail='Đang chạy tác vụ',budget=budget-?,last_used=? WHERE id=?",
                       (job["cost"], time.time(), account["id"]))
            job["account_id"] = account["id"]
            job["progress"] = 3
            return job, account

    def rebalance(self, db):
        """Tài khoản rảnh mà hết prompt của mình: nhận một prompt CHƯA GỬI (đang chờ) từ tài khoản còn nhiều prompt chờ nhất,
        lấy từ cuối hàng đợi của tài khoản đó. Không chuyển prompt người dùng đã tự chọn tài khoản. Trả về True nếu đã chuyển."""
        free = db.execute("""SELECT a.id, a.budget FROM accounts a WHERE a.enabled=1 AND a.state IN ('new','ready')
                             AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.status='waiting' AND j.assigned_account_id=a.id)
                             ORDER BY a.rowid""").fetchall()
        moved = False
        for account in free:
            donor = db.execute("""SELECT j.assigned_account_id AS id, COUNT(*) AS waiting FROM jobs j
                                  WHERE j.status='waiting' AND j.assigned_manual=0 AND j.assigned_account_id IS NOT NULL
                                  AND j.assigned_account_id!=? GROUP BY j.assigned_account_id
                                  ORDER BY waiting DESC LIMIT 1""", (account["id"],)).fetchone()
            if not donor:
                break
            job = db.execute("""SELECT id FROM jobs WHERE status='waiting' AND assigned_manual=0 AND assigned_account_id=?
                                AND cost<=?+0.000001 ORDER BY created DESC, rowid DESC LIMIT 1""",
                             (donor["id"], account["budget"])).fetchone()
            if job:
                db.execute("UPDATE jobs SET assigned_account_id=? WHERE id=?", (account["id"], job["id"]))
                moved = True
        return moved

    def plan_waiting(self):
        """Balance unassigned prompts across eligible accounts and retain each assignment."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            accounts = [dict(row) for row in db.execute(
                """SELECT id,budget,rowid FROM accounts
                   WHERE enabled=1 AND state IN ('new','ready','busy','checking') ORDER BY rowid""")]
            eligible = {account["id"] for account in accounts}
            stale = db.execute(
                """SELECT j.id FROM jobs j LEFT JOIN accounts a ON a.id=j.assigned_account_id
                   WHERE j.status='waiting' AND j.assigned_account_id IS NOT NULL
                   AND (a.id IS NULL OR a.enabled!=1 OR a.state NOT IN ('new','ready','busy','checking'))"""
            ).fetchall()
            stale_ids = [row["id"] for row in stale]
            if stale_ids:
                db.executemany("UPDATE jobs SET assigned_account_id=NULL WHERE id=?",
                               [(job_id,) for job_id in stale_ids])
            for account in accounts:
                assigned = db.execute(
                    """SELECT id,cost FROM jobs WHERE status='waiting' AND assigned_account_id=?
                       ORDER BY created DESC,rowid DESC""", (account["id"],)).fetchall()
                planned_cost = sum(row["cost"] for row in assigned)
                while assigned and planned_cost > account["budget"] + 0.000001:
                    job = assigned.pop(0)
                    db.execute("UPDATE jobs SET assigned_account_id=NULL WHERE id=?", (job["id"],))
                    planned_cost -= job["cost"]
            counts = {account_id: 0 for account_id in eligible}
            budgets = {account["id"]: account["budget"] for account in accounts}
            for row in db.execute(
                    """SELECT account_id,COUNT(*) AS count FROM jobs
                       WHERE status='running' AND account_id IS NOT NULL GROUP BY account_id"""):
                if row["account_id"] in counts:
                    counts[row["account_id"]] += row["count"]
            for row in db.execute(
                    """SELECT assigned_account_id,cost FROM jobs
                       WHERE status='waiting' AND assigned_account_id IS NOT NULL"""):
                account_id = row["assigned_account_id"]
                if account_id in counts:
                    counts[account_id] += 1
                    budgets[account_id] -= row["cost"]
            unassigned = db.execute(
                "SELECT id,cost FROM jobs WHERE status='waiting' AND assigned_account_id IS NULL ORDER BY created,rowid"
            ).fetchall()
            for job in unassigned:
                choices = [account for account in accounts
                           if budgets[account["id"]] + 0.000001 >= job["cost"]]
                if not choices:
                    break
                account = min(choices, key=lambda item: (counts[item["id"]], item["rowid"]))
                db.execute("UPDATE jobs SET assigned_account_id=? WHERE id=?",
                           (account["id"], job["id"]))
                budgets[account["id"]] -= job["cost"]
                counts[account["id"]] += 1
            return len(unassigned)

    def phase(self, job_id, phase, source_url=None):
        progress = self.phase_progress(phase)
        with self.connect() as db:
            if source_url:
                db.execute("UPDATE jobs SET phase=?,source_url=?,progress=MAX(progress,?),updated=? WHERE id=?",
                           (phase, source_url, progress, time.time(), job_id))
            else:
                db.execute("UPDATE jobs SET phase=?,progress=MAX(progress,?),updated=? WHERE id=?",
                           (phase, progress, time.time(), job_id))

    @staticmethod
    def phase_progress(phase):
        if phase == "Đã tải MP4":
            return 100
        if "Đang tải MP4" in phase:
            return 94
        if "video sẵn sàng" in phase.lower() or "đã có video" in phase.lower():
            return 90
        if "Muse đang tạo video" in phase:
            return 20
        if "đã bắt đầu gửi" in phase.lower():
            return 18
        if "chuẩn bị gửi prompt" in phase.lower():
            return 15
        if "tải ảnh nhân vật" in phase.lower():
            return 12
        if "kiểm tra hộp thư" in phase.lower() or "chờ otp" in phase.lower():
            return 8
        if "đăng nhập" in phase.lower() or "kiểm tra phiên muse" in phase.lower():
            return 5
        return 0

    def submitting(self, job_id):
        # Commit BEFORE pressing Enter: even an ambiguous network failure must not resend.
        with self.connect() as db:
            db.execute("UPDATE jobs SET submitted=1,phase='Đã bắt đầu gửi; chờ video',updated=? WHERE id=? AND status='running'",
                       (time.time(), job_id))

    def finish(self, job_id, output=None, error="", account_state="ready"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM jobs WHERE id=? AND status='running'", (job_id,)).fetchone()
            if not job:
                return
            if error and not job["submitted"]:
                db.execute("UPDATE accounts SET budget=budget+? WHERE id=?", (job["cost"], job["account_id"]))
            db.execute("UPDATE jobs SET status=?,phase=?,progress=?,output=?,error=?,updated=? WHERE id=?",
                       ("error" if error else "done", "Cần kiểm tra" if error else "Đã tải MP4",
                        job["progress"] if error else 100, output, error, time.time(), job_id))
            db.execute("UPDATE accounts SET state=?,detail=? WHERE id=?", (account_state, error, job["account_id"]))
            if not error:  # vừa tải được video: phiên chắc chắn còn sống
                db.execute("UPDATE accounts SET health='LIVE',last_checked=?,last_error='',check_failures=0 WHERE id=?",
                           (time.time(), job["account_id"]))
        if output and not error:
            try:
                self.media.sync_generated()  # video vừa tải hiện ngay trong Thư viện Video & Ảnh
            except Exception:
                pass  # thư viện lỗi không được làm hỏng kết quả tác vụ; bấm Làm mới để đồng bộ lại

    DETAIL_FIELDS = ("setting", "camera", "behavior", "action")
    SCENE_PROMPT = re.compile(
        r"^\s*b[ốo]i\s*c[ảa]nh\s*\d*\s*:\s*(?P<setting>.*?),\s*g[óo]c\s*quay\s*:\s*(?P<camera>.*?),\s*"
        r"h[àa]nh\s*vi\s*nh[âa]n\s*v[ậa]t\s*:\s*(?P<behavior>.*?),\s*n[ộo]i\s*dung\s*h[àa]nh\s*[đd][ộo]ng"
        r"(?:\s*nh[âa]n\s*v[ậa]t)?\s*:\s*(?P<action>.*?)\s*;?\s*$", re.IGNORECASE | re.DOTALL)

    def job_detail(self, job_id):
        """Toàn bộ thông tin một tác vụ cho Prompt Studio."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("Không tìm thấy prompt này trong hàng đợi.")
        job = dict(row)
        job["images"] = [token for token in json.loads(job["images"] or "[]") if self.image_file(token)]
        job["name"] = f"Prompt {job['ordinal'] + 1}"
        try:
            details = json.loads(job.pop("details") or "{}")
        except (TypeError, ValueError):
            details = {}
        if not any(details.get(field) for field in self.DETAIL_FIELDS):
            details = self.prompt_details(job["prompt"])
        job["details"] = {field: str(details.get(field) or "") for field in self.DETAIL_FIELDS}
        job["editable"] = job["status"] != "running"
        return job

    def update_job(self, job_id, prompt, details=None, images=None, mode="text", account_id=None):
        """Lưu chỉnh sửa từ Prompt Studio. Không sửa được tác vụ đang chạy."""
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
            raise ValueError("Prompt trống hoặc dài quá 20.000 ký tự.")
        if mode not in ("text", "image"):
            raise ValueError("Loại tác vụ không hợp lệ.")
        details = details if isinstance(details, dict) else {}
        details = {field: str(details.get(field) or "").strip()[:5000] for field in self.DETAIL_FIELDS}
        images = images if images is not None else []
        if (not isinstance(images, list) or len(images) > 10 or
                any(not isinstance(t, str) or not re.fullmatch(r"[a-f0-9]{32}", t) or not self.image_file(t) for t in images)):
            raise ValueError("Ảnh tham chiếu không hợp lệ hoặc quá 10 ảnh.")
        if mode == "image" and not images:
            raise ValueError("Loại Ảnh → video cần ít nhất một ảnh tham chiếu.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,images,images_origin FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy prompt này trong hàng đợi.")
            if row["status"] == "running":
                raise ValueError("Prompt đang chạy, không sửa được. Đợi chạy xong rồi sửa và bấm Chạy lại.")
            # Người dùng đổi ảnh: từ giờ là ảnh tự gắn, Auto Character Mapper không ghi đè nếu chưa xác nhận.
            origin = "manual" if json.loads(row["images"] or "[]") != images else row["images_origin"]
            if account_id:
                account = db.execute("SELECT enabled FROM accounts WHERE id=?", (account_id,)).fetchone()
                if not account:
                    raise ValueError("Tài khoản đã chọn không còn trong danh sách.")
            db.execute("UPDATE jobs SET prompt=?,details=?,images=?,generation_mode=?,assigned_account_id=?,assigned_manual=?,images_origin=?,updated=? WHERE id=?",
                       (prompt.strip(), json.dumps(details, ensure_ascii=False), json.dumps(images), mode,
                        account_id or None, int(bool(account_id)), origin, time.time(), job_id))
        self.plan_waiting()
        self.cleanup_images()
        return self.job_detail(job_id)

    def rerun(self, job_id, confirmed=False):
        """Đưa một prompt đã Xong hoặc Lỗi về Chờ để chạy lại. File video cũ vẫn giữ trong thư mục lưu."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,submitted FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy prompt này trong hàng đợi.")
            if row["status"] == "running":
                raise ValueError("Prompt đang chạy.")
            if row["status"] == "waiting":
                raise ValueError("Prompt đang chờ chạy rồi.")
            if row["status"] == "error" and row["submitted"] and not confirmed:
                raise ValueError("Prompt này có thể đã được gửi lên Muse. Kiểm tra Muse trước; xác nhận để vẫn chạy lại.")
            db.execute("""UPDATE jobs SET status='waiting',phase='Đợi tài khoản',progress=0,error='',account_id=NULL,
                          submitted=0,output=NULL,source_url='',updated=? WHERE id=?""", (time.time(), job_id))
        self.plan_waiting()

    def retry(self, job_id):
        with self.connect() as db:
            cursor = db.execute("UPDATE jobs SET status='waiting',phase='Đợi tài khoản',progress=0,error='',account_id=NULL,updated=? WHERE id=? AND status='error' AND submitted=0",
                                (time.time(), job_id))
            if cursor.rowcount != 1:
                raise ValueError("Chỉ thử lại tác vụ chắc chắn chưa gửi. Tác vụ đã gửi cần kiểm tra trên Muse trước.")

    def snapshot(self):
        with self.connect() as db:
            accounts = [dict(r) for r in db.execute(
                """SELECT a.id,a.email,a.enabled,a.budget,a.state,a.detail,a.health,a.last_checked,a.last_error,
                   (SELECT count(*) FROM jobs j WHERE j.status='waiting' AND j.assigned_account_id=a.id) AS jobs_assigned,
                   (SELECT count(*) FROM jobs j WHERE j.status='done' AND j.account_id=a.id) AS jobs_completed,
                   (SELECT count(*) FROM jobs j WHERE j.status='error' AND j.account_id=a.id) AS jobs_failed
                   FROM accounts a ORDER BY a.rowid""")]
            jobs = []
            for row in db.execute("SELECT * FROM jobs ORDER BY created DESC,rowid DESC LIMIT 2000"):
                job = dict(row)
                job["image_count"] = len(json.loads(job.pop("images") or "[]"))
                job["name"] = f"Prompt {job['ordinal'] + 1}"
                jobs.append(job)
            cookies = self.cookies.summaries(db)
            for account in accounts:
                account.update(cookies.get(account["id"], {"cookie_count": 0}))
            totals = {s: 0 for s in ("waiting", "running", "done", "error")}
            totals.update({r[0]: r[1] for r in db.execute("SELECT status,count(*) FROM jobs GROUP BY status")})
            return {"accounts": accounts, "jobs": jobs, "totals": totals}

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def save_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
