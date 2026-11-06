"""Bản quyền HANA MEDIA bằng key ký số (Ed25519), không cần máy chủ.

Máy Super Admin giữ khóa ký (data/license/signing.key, không bao giờ rời máy). Mỗi khách hàng là một bản ghi trong
sổ bản quyền của Super Admin; mỗi lần tạo/gia hạn, tool sinh một key:  HANA1.<nội dung base64>.<chữ ký base64>.
Nội dung gồm: mã bản quyền, tên, email, role, quyền, gói, ngày bắt đầu/hết hạn, mã máy được phép, quota video.

Máy khách dán key; tool kiểm tra chữ ký bằng khóa công khai GẮN CỨNG trong app (runner/license_trust.py), mã máy,
hạn dùng và đồng hồ máy. Hết hạn / sai máy / key giả: khóa chạy video và tải media, vẫn xem được dữ liệu.
Chưa gắn khóa công khai nào (TRUSTED_KEYS rỗng) thì chưa bật khóa bản quyền: tool chạy như trước.

Giới hạn (nói rõ với người dùng): không có máy chủ nên không thu hồi key đã cấp ngay lập tức (khóa = không gia hạn);
người rành kỹ thuật vẫn có thể sửa mã app trên máy họ để bỏ kiểm tra.
"""
import base64
import calendar
import hashlib
import json
import os
import re
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from . import license_trust

ROLES = ("super_admin", "admin", "manager", "user", "viewer")
ROLE_NAMES = {"super_admin": "Super Admin", "admin": "Admin / Reseller", "manager": "Manager / Team Lead",
              "user": "User / Operator", "viewer": "Viewer / Support"}
PERMISSIONS = ("create_users", "renew", "manage_roles", "view_revenue", "manage_quota", "view_audit", "create_jobs",
               "upload_media")
PERMISSION_NAMES = {"create_users": "Tạo tài khoản con", "renew": "Gia hạn tài khoản", "manage_roles": "Quản lý role",
                    "view_revenue": "Xem doanh thu", "manage_quota": "Quản lý quota", "view_audit": "Xem audit logs",
                    "create_jobs": "Tạo job", "upload_media": "Tải media"}
ROLE_DEFAULTS = {
    "super_admin": list(PERMISSIONS),
    "admin": ["create_users", "renew", "manage_roles", "view_revenue", "manage_quota", "create_jobs", "upload_media"],
    "manager": ["create_users", "renew", "view_revenue", "create_jobs", "upload_media"],
    "user": ["create_jobs", "upload_media"],
    "viewer": [],
}
PLANS = {"1m": 1, "1y": 12, "3y": 36}
PLAN_NAMES = {"1m": "1 tháng", "1y": "1 năm", "3y": "3 năm"}
PREFIX = "HANA1"
MACHINE = re.compile(r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}$")


class LicenseError(ValueError):
    pass


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def key_id(public_raw):
    return hashlib.sha256(public_raw).hexdigest()[:12]


def add_months(start, months):
    """Ngày hết hạn = ngày bắt đầu + số tháng - 1 ngày (01/03 + 1 tháng → 31/03; + 1 năm → 28/02 năm sau)."""
    month = start.month - 1 + months
    year, month = start.year + month // 12, month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) - timedelta(days=1)


_MACHINE = None


def machine_code():
    """Mã máy ổn định (không lộ thông tin máy): băm của MachineGuid Windows hoặc /etc/machine-id."""
    global _MACHINE
    if _MACHINE:
        return _MACHINE
    raw = ""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as handle:
                raw = winreg.QueryValueEx(handle, "MachineGuid")[0]
        except OSError:
            raw = ""
    else:
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                raw = Path(path).read_text().strip()
                break
            except OSError:
                continue
    raw = raw or os.environ.get("COMPUTERNAME") or os.uname().nodename
    digest = hashlib.sha256(("hanamedia-machine:" + raw).encode()).hexdigest()[:16].upper()
    _MACHINE = "-".join(digest[i:i + 4] for i in range(0, 16, 4))
    return _MACHINE


def public_from_private(private_raw):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    key = Ed25519PrivateKey.from_private_bytes(private_raw)
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def sign(private_raw, payload):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    body = b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode())
    signature = Ed25519PrivateKey.from_private_bytes(private_raw).sign(f"{PREFIX}.{body}".encode())
    return f"{PREFIX}.{body}.{b64(signature)}"


def verify(token, trusted):
    """Trả về nội dung key nếu chữ ký hợp lệ với một khóa công khai tin cậy (dict kid -> raw). Nêu LicenseError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    token = re.sub(r"\s+", "", str(token or ""))
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX or len(token) > 8000:
        raise LicenseError("Key không đúng định dạng HANA1.")
    try:
        payload = json.loads(unb64(parts[1]))
        signature = unb64(parts[2])
    except (ValueError, TypeError):
        raise LicenseError("Key bị hỏng hoặc chép thiếu ký tự.") from None
    public = trusted.get(payload.get("kid")) if isinstance(payload, dict) else None
    if not public:
        raise LicenseError("Key không do HANA MEDIA cấp (khóa ký không được tin cậy).")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, f"{PREFIX}.{parts[1]}".encode())
    except InvalidSignature:
        raise LicenseError("Chữ ký key không hợp lệ (key đã bị sửa hoặc giả).") from None
    return payload


def clean_record(data):
    """Kiểm tra dữ liệu tài khoản bản quyền từ giao diện."""
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:80]
    email = str(data.get("email") or "").strip().lower()[:120]
    if not name:
        raise LicenseError("Nhập tên tài khoản.")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise LicenseError("Email không hợp lệ.")
    role = data.get("role")
    if role not in ROLES:
        raise LicenseError("Role không hợp lệ.")
    permissions = data.get("permissions")
    if permissions is None:
        permissions = ROLE_DEFAULTS[role]
    if not isinstance(permissions, list) or any(p not in PERMISSIONS for p in permissions):
        raise LicenseError("Quyền không hợp lệ.")
    devices = data.get("devices") or []
    if isinstance(devices, str):
        devices = [d for d in re.split(r"[\s,;]+", devices) if d]
    devices = [str(d).strip().upper() for d in devices]
    if len(devices) > 20 or any(not MACHINE.match(d) for d in devices):
        raise LicenseError("Mã máy phải có dạng XXXX-XXXX-XXXX-XXXX (tối đa 20 máy).")
    quota = data.get("quota") or 0
    if type(quota) is not int or not 0 <= quota <= 10_000_000:
        raise LicenseError("Quota phải là số video từ 0 (không giới hạn) trở lên.")
    return {"name": name, "email": email, "role": role, "permissions": sorted(set(permissions), key=PERMISSIONS.index),
            "devices": list(dict.fromkeys(devices)), "quota": quota}


def parse_day(value, fallback=None):
    try:
        return date.fromisoformat(str(value)) if value else fallback
    except ValueError:
        raise LicenseError("Ngày không hợp lệ (dạng YYYY-MM-DD).") from None


class LicenseManager:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS licenses (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', role TEXT NOT NULL,
        permissions TEXT NOT NULL, plan TEXT NOT NULL, start TEXT NOT NULL, expires TEXT NOT NULL,
        devices TEXT NOT NULL DEFAULT '[]', quota INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0,
        token TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS license_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, license_id TEXT NOT NULL, at REAL NOT NULL, action TEXT NOT NULL,
        plan TEXT NOT NULL DEFAULT '', expires TEXT NOT NULL DEFAULT '', amount INTEGER NOT NULL DEFAULT 0,
        actor TEXT NOT NULL DEFAULT 'Super Admin', detail TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS license_events_at ON license_events(at);
    """

    def __init__(self, store, data, trusted=None, today=None, clock=time.time):
        self.store = store
        self.dir = Path(data) / "license"
        self.today = today or date.today
        self.clock = clock
        keys = license_trust.TRUSTED_KEYS if trusted is None else trusted
        self.trusted = {key_id(unb64(k)): unb64(k) for k in keys}
        with store.connect() as db:
            db.executescript(self.SCHEMA)

    # ---- khóa ký của Super Admin ---------------------------------------------------------------------------------
    @property
    def key_file(self):
        return self.dir / "signing.key"

    def signing_key(self):
        try:
            raw = unb64(self.key_file.read_text().strip())
            return raw if len(raw) == 32 else None
        except (OSError, ValueError):
            return None

    def init_signing_key(self):
        if self.signing_key():
            raise LicenseError("Máy này đã có khóa Super Admin. Không tạo lại để khỏi làm hỏng các key đã cấp.")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        raw = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                          serialization.NoEncryption())
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(b64(raw))
        self.audit("init_signing", "Tạo khóa ký Super Admin trên máy này")
        return self.public_key()

    def public_key(self):
        raw = self.signing_key()
        return b64(public_from_private(raw)) if raw else None

    def issuer_trusted(self):
        """Khóa ký trên máy này có nằm trong danh sách gắn cứng của app không (tức là Super Admin thật)."""
        raw = self.signing_key()
        return bool(raw) and key_id(public_from_private(raw)) in self.trusted

    # ---- trạng thái bản quyền của máy này ------------------------------------------------------------------------
    def enforced(self):
        return bool(self.trusted)

    def clock_ok(self):
        """Phát hiện chỉnh lùi đồng hồ máy để kéo dài hạn dùng."""
        now = self.clock()
        seen = float(self.store.setting("license_clock", 0) or 0)
        if now + 86400 < seen:
            return False
        if now > seen + 3600:
            self.store.save_setting("license_clock", now)
        return True

    def status(self):
        machine = machine_code()
        base = {"enforced": self.enforced(), "machine": machine, "issuer": bool(self.signing_key()),
                "issuer_trusted": self.issuer_trusted(), "public_key": self.public_key()}
        if not self.enforced():
            return {**base, "state": "not_enforced", "allowed": list(PERMISSIONS),
                    "message": "Chưa bật khóa bản quyền: tool chạy đầy đủ chức năng."}
        if self.issuer_trusted():
            return {**base, "state": "owner", "role": "super_admin", "allowed": list(PERMISSIONS),
                    "name": "Super Admin", "message": "Máy Super Admin: toàn quyền."}
        token = self.store.setting("license_token")
        if not token:
            return {**base, "state": "missing", "allowed": [], "message": "Chưa kích hoạt bản quyền. Dán key để dùng tool."}
        try:
            payload = verify(token, self.trusted)
        except LicenseError as error:
            return {**base, "state": "invalid", "allowed": [], "message": str(error)}
        info = {**base, "id": payload.get("id"), "name": payload.get("name"), "email": payload.get("email"),
                "role": payload.get("role"), "plan": payload.get("plan"), "start": payload.get("start"),
                "expires": payload.get("expires"), "quota": payload.get("quota", 0), "used": self.used(payload.get("id")),
                "permissions": payload.get("permissions", [])}
        if payload.get("devices") and machine not in payload["devices"]:
            return {**info, "state": "wrong_device", "allowed": [],
                    "message": f"Key không cấp cho máy này (mã máy {machine}). Gửi mã máy cho Super Admin."}
        if not self.clock_ok():
            return {**info, "state": "clock", "allowed": [], "message": "Đồng hồ máy bị chỉnh lùi. Chỉnh lại ngày giờ đúng."}
        today = self.today()
        start, expires = parse_day(payload.get("start"), today), parse_day(payload.get("expires"), today)
        if today < start:
            return {**info, "state": "not_started", "allowed": [], "message": f"Bản quyền bắt đầu từ {start:%d/%m/%Y}."}
        if today > expires:
            return {**info, "state": "expired", "allowed": [],
                    "message": f"Bản quyền đã hết hạn ngày {expires:%d/%m/%Y}. Liên hệ để gia hạn."}
        days = (expires - today).days
        return {**info, "state": "expiring" if days < 7 else "active", "days_left": days,
                "allowed": [p for p in payload.get("permissions", []) if p in PERMISSIONS],
                "message": f"Còn {days + 1} ngày (hết hạn {expires:%d/%m/%Y})."}

    def allows(self, permission):
        return permission in self.status()["allowed"]

    def check(self, permission):
        status = self.status()
        if permission not in status["allowed"]:
            reason = status["message"] if status["state"] not in ("active", "expiring", "owner", "not_enforced") \
                else f"Tài khoản không có quyền \"{PERMISSION_NAMES[permission]}\"."
            raise PermissionError(reason)
        return status

    def activate(self, token):
        payload = verify(token, self.trusted) if self.enforced() else None
        if payload is None:
            raise LicenseError("App này chưa gắn khóa công khai HANA MEDIA nên chưa cần kích hoạt.")
        machine = machine_code()
        if payload.get("devices") and machine not in payload["devices"]:
            raise LicenseError(f"Key không cấp cho máy này. Mã máy của bạn: {machine}.")
        self.store.save_setting("license_token", re.sub(r"\s+", "", token))
        return self.status()

    def deactivate(self):
        self.store.save_setting("license_token", None)

    # ---- quota video ---------------------------------------------------------------------------------------------
    def used(self, license_id):
        return int((self.store.setting("license_usage", {}) or {}).get(license_id or "", 0))

    def reserve_jobs(self, count):
        """Trừ quota video khi thêm prompt. Không giới hạn nếu quota = 0 hoặc chưa bật bản quyền."""
        status = self.status()
        if status["state"] in ("not_enforced", "owner") or not status.get("quota"):
            return
        used = self.used(status["id"])
        if used + count > status["quota"]:
            raise PermissionError(f"Hết quota video: đã dùng {used}/{status['quota']}. Liên hệ để nâng quota.")
        usage = self.store.setting("license_usage", {}) or {}
        usage[status["id"]] = used + count
        self.store.save_setting("license_usage", usage)
        return status["id"]

    def refund_jobs(self, license_id, count):
        if not license_id:
            return
        usage = self.store.setting("license_usage", {}) or {}
        usage[license_id] = max(0, int(usage.get(license_id, 0)) - count)
        self.store.save_setting("license_usage", usage)

    # ---- sổ bản quyền (máy Super Admin) --------------------------------------------------------------------------
    def require_issuer(self):
        raw = self.signing_key()
        if not raw:
            raise LicenseError("Máy này chưa có khóa Super Admin.")
        return raw

    def audit(self, action, detail, license_id=""):
        with self.store.connect() as db:
            db.execute("INSERT INTO license_events(license_id,at,action,detail) VALUES(?,?,?,?)",
                       (license_id, self.clock(), action, detail[:500]))

    def issue(self, db, row):
        raw = self.require_issuer()
        payload = {"v": 1, "kid": key_id(public_from_private(raw)), "id": row["id"], "name": row["name"],
                   "email": row["email"], "role": row["role"], "permissions": json.loads(row["permissions"]),
                   "plan": row["plan"], "start": row["start"], "expires": row["expires"],
                   "devices": json.loads(row["devices"]), "quota": row["quota"], "issued": int(self.clock())}
        token = sign(raw, payload)
        db.execute("UPDATE licenses SET token=?,updated=? WHERE id=?", (token, self.clock(), row["id"]))
        return token

    def save_account(self, data):
        """Tạo mới (không có id) hoặc sửa tài khoản. Trả về (bản ghi, key mới)."""
        self.require_issuer()
        record = clean_record(data)
        now = self.clock()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if data.get("id"):
                row = db.execute("SELECT * FROM licenses WHERE id=?", (str(data["id"]),)).fetchone()
                if not row:
                    raise LicenseError("Không tìm thấy tài khoản.")
                db.execute("""UPDATE licenses SET name=?,email=?,role=?,permissions=?,devices=?,quota=?,updated=?
                              WHERE id=?""", (record["name"], record["email"], record["role"],
                                              json.dumps(record["permissions"]), json.dumps(record["devices"]),
                                              record["quota"], now, row["id"]))
                action, detail, license_id = "update", f"Sửa {record['name']}: role {ROLE_NAMES[record['role']]}", row["id"]
            else:
                plan = data.get("plan")
                if plan not in PLANS:
                    raise LicenseError("Chọn gói 1 tháng, 1 năm hoặc 3 năm.")
                start = parse_day(data.get("start"), self.today())
                expires = add_months(start, PLANS[plan])
                license_id = uuid.uuid4().hex[:16]
                db.execute("""INSERT INTO licenses(id,name,email,role,permissions,plan,start,expires,devices,quota,created,
                              updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (license_id, record["name"], record["email"], record["role"],
                            json.dumps(record["permissions"]), plan, start.isoformat(), expires.isoformat(),
                            json.dumps(record["devices"]), record["quota"], now, now))
                amount = self.amount(data.get("amount"))
                db.execute("""INSERT INTO license_events(license_id,at,action,plan,expires,amount,detail)
                              VALUES(?,?,?,?,?,?,?)""", (license_id, now, "create", plan, expires.isoformat(), amount,
                                                         f"Tạo {record['name']} ({PLAN_NAMES[plan]})"))
                action, detail = None, ""
            if action:
                db.execute("INSERT INTO license_events(license_id,at,action,detail) VALUES(?,?,?,?)",
                           (license_id, now, action, detail))
            row = db.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
            token = self.issue(db, row)
        return self.account(license_id), token

    @staticmethod
    def amount(value):
        value = value or 0
        if type(value) is not int or not 0 <= value <= 10_000_000_000:
            raise LicenseError("Số tiền phải là số nguyên VNĐ.")
        return value

    def renew(self, license_id, plan, amount=0):
        """Gia hạn: cộng thêm gói vào ngày hết hạn hiện tại (hoặc từ hôm nay nếu đã hết hạn), cấp key mới."""
        self.require_issuer()
        if plan not in PLANS:
            raise LicenseError("Chọn gói 1 tháng, 1 năm hoặc 3 năm.")
        amount = self.amount(amount)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
            if not row:
                raise LicenseError("Không tìm thấy tài khoản.")
            base = max(date.fromisoformat(row["expires"]) + timedelta(days=1), self.today())
            expires = add_months(base, PLANS[plan])
            db.execute("UPDATE licenses SET plan=?,expires=?,locked=0,updated=? WHERE id=?",
                       (plan, expires.isoformat(), self.clock(), license_id))
            db.execute("""INSERT INTO license_events(license_id,at,action,plan,expires,amount,detail)
                          VALUES(?,?,?,?,?,?,?)""", (license_id, self.clock(), "renew", plan, expires.isoformat(), amount,
                                                     f"Gia hạn {row['name']} thêm {PLAN_NAMES[plan]}"))
            token = self.issue(db, db.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone())
        return self.account(license_id), token

    def set_locked(self, license_id, locked):
        self.require_issuer()
        with self.store.connect() as db:
            row = db.execute("SELECT name FROM licenses WHERE id=?", (license_id,)).fetchone()
            if not row:
                raise LicenseError("Không tìm thấy tài khoản.")
            db.execute("UPDATE licenses SET locked=?,updated=? WHERE id=?", (int(bool(locked)), self.clock(), license_id))
            db.execute("INSERT INTO license_events(license_id,at,action,detail) VALUES(?,?,?,?)",
                       (license_id, self.clock(), "lock" if locked else "unlock",
                        ("Khóa " if locked else "Mở khóa ") + row["name"]))
        return self.account(license_id)

    def account(self, license_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
        return self.public_account(row)

    def public_account(self, row):
        today = self.today()
        expires = date.fromisoformat(row["expires"])
        days = (expires - today).days
        state = "locked" if row["locked"] else "expired" if days < 0 else "expiring" if days < 7 else "active"
        return {"id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"],
                "permissions": json.loads(row["permissions"]), "plan": row["plan"], "start": row["start"],
                "expires": row["expires"], "devices": json.loads(row["devices"]), "quota": row["quota"],
                "state": state, "days_left": days, "token": row["token"]}

    def dashboard(self):
        """Dữ liệu trang quản lý (chỉ máy Super Admin)."""
        self.require_issuer()
        with self.store.connect() as db:
            accounts = [self.public_account(r) for r in db.execute("SELECT * FROM licenses ORDER BY created")]
            events = [dict(r) for r in db.execute("""SELECT e.*,l.name FROM license_events e LEFT JOIN licenses l
                                                     ON l.id=e.license_id ORDER BY e.at DESC LIMIT 200""")]
            revenue = [dict(r) for r in db.execute("""SELECT strftime('%Y-%m',at,'unixepoch','localtime') AS month,
                       SUM(amount) AS total, COUNT(*) AS count FROM license_events WHERE amount>0
                       GROUP BY month ORDER BY month DESC LIMIT 24""")]
        stats = {"total": len(accounts), "admins": sum(a["role"] in ("super_admin", "admin") for a in accounts),
                 "expiring": sum(a["state"] == "expiring" for a in accounts),
                 "active": sum(a["state"] in ("active", "expiring") for a in accounts),
                 "revenue": sum(r["total"] for r in revenue)}
        return {"accounts": accounts, "events": events, "revenue": revenue, "stats": stats}
