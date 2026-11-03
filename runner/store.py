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


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.image_dir = Path(self.path).parent / "input-images"
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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            migrations = {
                "images": "TEXT NOT NULL DEFAULT '[]'",
                "generation_mode": "TEXT NOT NULL DEFAULT 'text'",
                "assigned_account_id": "TEXT",
                "progress": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        self.cleanup_images()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
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
            db.execute("UPDATE accounts SET state='new',detail='Cần kiểm tra lại phiên' WHERE state IN ('busy','authenticating','reviewing')")

    def add_account(self, email, mailbox, budget):
        email, mailbox = mailbox_for(email, mailbox)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM accounts").fetchone()[0] >= 10:
                raise ValueError("Phiên bản này hỗ trợ tối đa 10 tài khoản.")
            try:
                db.execute("INSERT INTO accounts(id,email,mailbox,budget) VALUES(?,?,?,?)",
                           (uuid.uuid4().hex, email, mailbox, number(budget)))
            except sqlite3.IntegrityError:
                raise ValueError("Email này đã có trong danh sách.") from None
        self.plan_waiting()

    def account(self, account_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy tài khoản.")
            return dict(row)

    def set_account(self, account_id, state, detail=""):
        with self.connect() as db:
            db.execute("UPDATE accounts SET state=?,detail=? WHERE id=?", (state, detail, account_id))

    def configure_account(self, account_id, enabled, budget):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row or row["state"] in ("busy", "authenticating", "reviewing"):
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
            if row["state"] in ("busy", "authenticating", "reviewing") or db.execute(
                    "SELECT 1 FROM jobs WHERE status='running' AND account_id=?", (account_id,)).fetchone():
                raise ValueError("Tài khoản đang chạy, đăng nhập hoặc đang mở phiên. Đợi xong hoặc đóng phiên rồi xóa.")
            db.execute("UPDATE jobs SET assigned_account_id=NULL WHERE status='waiting' AND assigned_account_id=?",
                       (account_id,))
            db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            email = row["email"]
        self.plan_waiting()
        return email

    def reserve_login(self, account_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE accounts SET state='authenticating',detail='Mở Chrome' WHERE id=? AND state NOT IN ('busy','authenticating','reviewing')", (account_id,))
            if cursor.rowcount != 1:
                raise ValueError("Tài khoản đang bận hoặc không tồn tại.")

    def enqueue(self, prompts, cost, batch, images=None, mode="text"):
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
        cost = number(cost, 0.01)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
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
            db.executemany("INSERT INTO jobs(id,prompt,cost,created,updated,batch,ordinal,images,generation_mode) VALUES(?,?,?,?,?,?,?,?,?)",
                           [(uuid.uuid4().hex, p.strip(), cost, now, now, batch, n, json.dumps(images[n]), mode)
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
        image = self.image_file(token)
        if not image:
            raise ValueError("Ảnh không còn tồn tại hoặc không hợp lệ.")
        name = Path(str(name)).name.strip()[:160] or "Ảnh tham chiếu"
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO image_library(token,name,created) VALUES(?,?,?)",
                       (token, name, time.time()))
        return token

    def library_images(self):
        with self.connect() as db:
            rows = db.execute("SELECT token,name,created FROM image_library ORDER BY created DESC").fetchall()
        result = []
        for row in rows:
            image = self.image_file(row["token"])
            if image:
                result.append({"token": row["token"], "name": row["name"], "created": row["created"],
                               "type": image[1]})
        return result

    def delete_library_images(self, tokens):
        if (not isinstance(tokens, list) or not 1 <= len(tokens) <= 200 or
                any(not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token)
                    for token in tokens)):
            raise ValueError("Chọn từ 1 đến 200 ảnh hợp lệ để xóa.")
        tokens = list(dict.fromkeys(tokens))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"SELECT token FROM image_library WHERE token IN ({','.join('?' for _ in tokens)})",
                tokens).fetchall()
            db.executemany("DELETE FROM image_library WHERE token=?", [(row["token"],) for row in rows])
            referenced = {row["token"] for row in db.execute("SELECT images FROM jobs")
                          for row in json.loads(row["images"] or "[]")}
        for row in rows:
            token = row["token"]
            if token not in referenced:
                image = self.image_file(token)
                if image:
                    image[0].unlink(missing_ok=True)
        return len(rows)

    def import_flow_downloads(self, folder):
        folder = Path(folder)
        if not folder.is_dir():
            return 0
        imported = 0
        known = {hashlib.sha256(self.image_file(item["token"])[0].read_bytes()).digest()
                 for item in self.library_images()}
        for path in sorted(folder.iterdir(), key=lambda item: item.stat().st_mtime):
            if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                continue
            data = path.read_bytes()
            extension, _ = self.image_type(data)
            if not extension:
                continue
            digest = hashlib.sha256(data).digest()
            if digest in known:
                continue
            token = self.add_image(data)
            self.add_library_image(token["token"], path.name)
            known.add(digest)
            imported += 1
        return imported

    def image_file(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            return None
        for extension, content_type in ((".png", "image/png"), (".jpg", "image/jpeg"),
                                        (".gif", "image/gif"), (".webp", "image/webp")):
            path = self.image_dir / (token + extension)
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
            pair = db.execute("""SELECT j.id AS job_id, a.id AS account_id FROM jobs j JOIN accounts a
                ON a.id=j.assigned_account_id AND a.enabled=1 AND a.state IN ('new','ready')
                AND a.budget+0.000001>=j.cost
                WHERE j.status='waiting' ORDER BY j.created,j.rowid LIMIT 1""").fetchone()
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

    def plan_waiting(self):
        """Balance unassigned prompts across eligible accounts and retain each assignment."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            accounts = [dict(row) for row in db.execute(
                """SELECT id,budget,rowid FROM accounts
                   WHERE enabled=1 AND state IN ('new','ready','busy') ORDER BY rowid""")]
            eligible = {account["id"] for account in accounts}
            stale = db.execute(
                """SELECT j.id FROM jobs j LEFT JOIN accounts a ON a.id=j.assigned_account_id
                   WHERE j.status='waiting' AND j.assigned_account_id IS NOT NULL
                   AND (a.id IS NULL OR a.enabled!=1 OR a.state NOT IN ('new','ready','busy'))"""
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

    def retry(self, job_id):
        with self.connect() as db:
            cursor = db.execute("UPDATE jobs SET status='waiting',phase='Đợi tài khoản',progress=0,error='',account_id=NULL,updated=? WHERE id=? AND status='error' AND submitted=0",
                                (time.time(), job_id))
            if cursor.rowcount != 1:
                raise ValueError("Chỉ thử lại tác vụ chắc chắn chưa gửi. Tác vụ đã gửi cần kiểm tra trên Muse trước.")

    def snapshot(self):
        with self.connect() as db:
            accounts = [dict(r) for r in db.execute("SELECT id,email,enabled,budget,state,detail FROM accounts ORDER BY rowid")]
            jobs = []
            for row in db.execute("SELECT * FROM jobs ORDER BY created DESC,rowid DESC LIMIT 2000"):
                job = dict(row)
                job["image_count"] = len(json.loads(job.pop("images") or "[]"))
                job["name"] = f"Prompt {job['ordinal'] + 1}"
                jobs.append(job)
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
