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
from .paths import frozen

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
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}  # số nhỏ = quyền cao
ISSUE_PERMISSIONS = ("create_users", "renew")  # có một trong hai thì máy được cấp/gia hạn key cho người dưới
MAX_CHAIN = 3
PLANS = {"1m": 1, "1y": 12, "3y": 36}
PLAN_NAMES = {"1m": "1 tháng", "1y": "1 năm", "3y": "3 năm"}
PREFIX = "HANA1"
MACHINE = re.compile(r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}$")
PUBLIC = re.compile(r"^[A-Za-z0-9_-]{43}$")


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


def verify(token, trusted, depth=0):
    """Trả về nội dung key nếu hợp lệ. Key ký bằng khóa gốc (trusted: kid -> raw) hoặc ký bằng khóa của một máy được
    ủy quyền: khi đó key mang theo key của máy đó ("chain") và phải nằm trong giới hạn của nó (role không cao hơn,
    quyền là tập con, hạn không vượt quá). Nêu LicenseError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    token = re.sub(r"\s+", "", str(token or ""))
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX or len(token) > 40000:
        raise LicenseError("Key không đúng định dạng HANA1.")
    try:
        payload = json.loads(unb64(parts[1]))
        signature = unb64(parts[2])
    except (ValueError, TypeError):
        raise LicenseError("Key bị hỏng hoặc chép thiếu ký tự.") from None
    if not isinstance(payload, dict):
        raise LicenseError("Key bị hỏng.")
    public, parent = trusted.get(payload.get("kid")), None
    if not public and payload.get("chain") and depth < MAX_CHAIN:
        parent = verify(payload["chain"], trusted, depth + 1)
        for candidate in parent.get("delegates") or []:
            try:
                raw = unb64(candidate)
            except (ValueError, TypeError):
                continue
            if len(raw) == 32 and key_id(raw) == payload.get("kid"):
                public = raw
                break
    if not public:
        raise LicenseError("Key không do HANA MEDIA cấp (khóa ký không được tin cậy).")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, f"{PREFIX}.{parts[1]}".encode())
    except InvalidSignature:
        raise LicenseError("Chữ ký key không hợp lệ (key đã bị sửa hoặc giả).") from None
    if parent is not None:
        if not set(parent.get("permissions") or []) & set(ISSUE_PERMISSIONS):
            raise LicenseError("Key do một tài khoản không có quyền cấp key tạo ra.")
        if ROLE_RANK.get(payload.get("role"), -1) < ROLE_RANK.get(parent.get("role"), 99):
            raise LicenseError("Key có role cao hơn người cấp.")
        if not set(payload.get("permissions") or []) <= set(parent.get("permissions") or []):
            raise LicenseError("Key có quyền vượt quá quyền của người cấp.")
        if str(payload.get("expires")) > str(parent.get("expires")):
            raise LicenseError("Key có hạn dùng vượt quá hạn của người cấp.")
        payload = {**payload, "issued_by": parent.get("name"), "issued_by_role": parent.get("role")}
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
    entries = data.get("devices") or []
    if isinstance(entries, str):
        entries = [d for d in re.split(r"[\s,;]+", entries) if d]
    devices, delegates, full = [], [], []
    for entry in entries:
        code, _, public = str(entry).strip().partition(".")
        code = code.upper()
        if not MACHINE.match(code) or (public and not PUBLIC.match(public)):
            raise LicenseError("Mã máy phải là chuỗi khách gửi, dạng XXXX-XXXX-XXXX-XXXX.xxxxxxxx (tối đa 20 máy).")
        devices.append(code)
        full.append(f"{code}.{public}" if public else code)
        if public:
            delegates.append(public)
    for public in data.get("delegates") or []:
        if not PUBLIC.match(str(public)):
            raise LicenseError("Mã máy không hợp lệ.")
        delegates.append(str(public))
    if len(devices) > 20:
        raise LicenseError("Tối đa 20 máy cho một tài khoản.")
    quota = data.get("quota") or 0
    if type(quota) is not int or not 0 <= quota <= 10_000_000:
        raise LicenseError("Quota phải là số video từ 0 (không giới hạn) trở lên.")
    return {"name": name, "email": email, "role": role, "permissions": sorted(set(permissions), key=PERMISSIONS.index),
            "devices": list(dict.fromkeys(full)), "delegates": list(dict.fromkeys(delegates)), "quota": quota}


def split_devices(entries):
    """Mã máy lưu như khách gửi ("CODE" hoặc "CODE.khóa"): tách thành (mã phần cứng, khóa công khai của máy)."""
    codes, delegates = [], []
    for entry in entries:
        code, _, public = str(entry).partition(".")
        codes.append(code)
        if public:
            delegates.append(public)
    return codes, delegates


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
        """trusted: khóa công khai gốc được tin cậy. Mặc định: danh sách gắn cứng, chỉ áp dụng cho bản app đã đóng gói
        (.exe phát cho khách); chạy từ mã nguồn thì chưa khóa (ai có mã nguồn cũng sửa được phần kiểm tra)."""
        self.store = store
        self.dir = Path(data) / "license"
        self.today = today or date.today
        self.clock = clock
        if trusted is None:
            trusted = license_trust.TRUSTED_KEYS if frozen() else []
        self.trusted = {key_id(unb64(k)): unb64(k) for k in trusted}
        with store.connect() as db:
            db.executescript(self.SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(licenses)")}
            if "delegates" not in columns:
                db.execute("ALTER TABLE licenses ADD COLUMN delegates TEXT NOT NULL DEFAULT '[]'")

    # ---- khóa của máy ------------------------------------------------------------------------------------------
    @property
    def key_file(self):
        return self.dir / "signing.key"

    @staticmethod
    def read_key(path):
        try:
            raw = unb64(path.read_text().strip())
            return raw if len(raw) == 32 else None
        except (OSError, ValueError):
            return None

    def write_key(self, path):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        raw = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                          serialization.NoEncryption())
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(b64(raw))
        return raw

    def signing_key(self):
        """Khóa ký GỐC của Super Admin (chỉ có trên máy Super Admin)."""
        return self.read_key(self.key_file)

    def device_key(self):
        """Khóa riêng của máy này, tự tạo; dùng khi máy được cấp quyền quản lý (cấp key cho người dưới)."""
        path = self.dir / "device.key"
        raw = self.read_key(path)
        if raw:
            return raw
        try:
            return self.write_key(path)
        except FileExistsError:
            return self.read_key(path)

    def device_public(self):
        return b64(public_from_private(self.device_key()))

    def request_code(self):
        """Mã máy gửi cho người bán: mã phần cứng + khóa công khai riêng của máy."""
        return f"{machine_code()}.{self.device_public()}"

    def init_signing_key(self):
        if self.enforced():
            raise LicenseError("App đã gắn khóa Super Admin. Chỉ máy Super Admin cấp quyền; máy này nhận key.")
        if self.signing_key():
            raise LicenseError("Máy này đã có khóa Super Admin. Không tạo lại để khỏi làm hỏng các key đã cấp.")
        self.write_key(self.key_file)
        self.audit("init_signing", "Tạo khóa ký Super Admin trên máy này")
        return self.public_key()

    def public_key(self):
        raw = self.signing_key()
        return b64(public_from_private(raw)) if raw else None

    def issuer_trusted(self):
        """Khóa ký gốc trên máy này có nằm trong danh sách gắn cứng của app không (tức là Super Admin thật)."""
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

    def license_status(self):
        """Trạng thái của key đã kích hoạt trên máy này (kèm nội dung key nếu hợp lệ)."""
        machine = machine_code()
        token = self.store.setting("license_token")
        if not token:
            return {"state": "missing", "allowed": [], "message": "Chưa kích hoạt bản quyền. Dán key để dùng tool."}, None
        try:
            payload = verify(token, self.trusted)
        except LicenseError as error:
            return {"state": "invalid", "allowed": [], "message": str(error)}, None
        info = {"id": payload.get("id"), "name": payload.get("name"), "email": payload.get("email"),
                "role": payload.get("role"), "plan": payload.get("plan"), "start": payload.get("start"),
                "expires": payload.get("expires"), "quota": payload.get("quota", 0), "used": self.used(payload.get("id")),
                "permissions": payload.get("permissions", []), "issued_by": payload.get("issued_by")}
        if payload.get("devices") and machine not in payload["devices"]:
            return {**info, "state": "wrong_device", "allowed": [],
                    "message": "Key không cấp cho máy này. Gửi mã máy của máy này cho người bán."}, payload
        if not self.clock_ok():
            return {**info, "state": "clock", "allowed": [],
                    "message": "Đồng hồ máy bị chỉnh lùi. Chỉnh lại ngày giờ đúng."}, payload
        today = self.today()
        start, expires = parse_day(payload.get("start"), today), parse_day(payload.get("expires"), today)
        if today < start:
            return {**info, "state": "not_started", "allowed": [],
                    "message": f"Bản quyền bắt đầu từ {start:%d/%m/%Y}."}, payload
        if today > expires:
            return {**info, "state": "expired", "allowed": [],
                    "message": f"Bản quyền đã hết hạn ngày {expires:%d/%m/%Y}. Liên hệ để gia hạn."}, payload
        days = (expires - today).days
        return {**info, "state": "expiring" if days < 7 else "active", "days_left": days,
                "allowed": [p for p in payload.get("permissions", []) if p in PERMISSIONS],
                "message": f"Còn {days + 1} ngày (hết hạn {expires:%d/%m/%Y})."}, payload

    def issuer_context(self):
        """Máy này có cấp key được không: 'root' (khóa gốc), 'delegate' (được cấp quyền quản lý) hoặc None."""
        if not self.enforced():
            raw = self.signing_key()
            return {"mode": "root", "raw": raw, "chain": None, "limits": None} if raw else None
        if self.issuer_trusted():
            return {"mode": "root", "raw": self.signing_key(), "chain": None, "limits": None}
        status, payload = self.license_status()
        if not payload or status["state"] not in ("active", "expiring"):
            return None
        permissions = set(payload.get("permissions") or [])
        if not permissions & set(ISSUE_PERMISSIONS) or self.device_public() not in (payload.get("delegates") or []):
            return None
        return {"mode": "delegate", "raw": self.device_key(), "chain": self.store.setting("license_token"),
                "limits": {"role": payload["role"], "permissions": permissions, "expires": payload["expires"],
                           "can_create": "create_users" in permissions, "can_renew": "renew" in permissions,
                           "name": payload.get("name")}}

    def status(self):
        base = {"enforced": self.enforced(), "machine": machine_code(), "request_code": self.request_code(),
                "issuer_trusted": self.issuer_trusted(), "public_key": self.public_key()}
        if not self.enforced():
            status = {"state": "not_enforced", "allowed": list(PERMISSIONS),
                      "message": "Chưa bật khóa bản quyền: tool chạy đầy đủ chức năng."}
        elif self.issuer_trusted():
            status = {"state": "owner", "role": "super_admin", "allowed": list(PERMISSIONS), "name": "Super Admin",
                      "message": "Máy Super Admin: toàn quyền."}
        else:
            status, _ = self.license_status()
        context = self.issuer_context()
        return {**base, **status, "issuer": bool(context), "issuer_mode": context["mode"] if context else None}

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
        if not self.enforced():
            raise LicenseError("App này chưa gắn khóa công khai HANA MEDIA nên chưa cần kích hoạt.")
        payload = verify(token, self.trusted)
        if payload.get("devices") and machine_code() not in payload["devices"]:
            raise LicenseError("Key không cấp cho máy này. Gửi mã máy của máy này cho người bán.")
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
            return None
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

    # ---- sổ bản quyền (máy cấp key) ------------------------------------------------------------------------------
    def require_issuer(self):
        context = self.issuer_context()
        if not context:
            raise LicenseError("Máy này không có quyền cấp key.")
        return context

    def check_limits(self, limits, role, permissions, expires, action):
        """Máy được ủy quyền chỉ cấp trong phạm vi của chính nó."""
        if not limits:
            return
        if action == "create" and not limits["can_create"]:
            raise LicenseError("Tài khoản của bạn không có quyền tạo tài khoản con.")
        if action == "renew" and not limits["can_renew"]:
            raise LicenseError("Tài khoản của bạn không có quyền gia hạn.")
        if role is not None and ROLE_RANK[role] < ROLE_RANK[limits["role"]]:
            raise LicenseError(f"Bạn chỉ cấp được role từ {ROLE_NAMES[limits['role']]} trở xuống.")
        if permissions is not None and not set(permissions) <= limits["permissions"]:
            raise LicenseError("Bạn chỉ cấp được những quyền mà tài khoản của bạn có.")
        if expires is not None and expires.isoformat() > limits["expires"]:
            raise LicenseError(f"Hạn tối đa bạn cấp được là {date.fromisoformat(limits['expires']):%d/%m/%Y} "
                               "(ngày hết hạn bản quyền của bạn).")

    def actor(self, context):
        return "Super Admin" if context["mode"] == "root" else (context["limits"].get("name") or "Đại lý")

    def audit(self, action, detail, license_id=""):
        with self.store.connect() as db:
            db.execute("INSERT INTO license_events(license_id,at,action,detail) VALUES(?,?,?,?)",
                       (license_id, self.clock(), action, detail[:500]))

    def issue(self, db, row, context):
        payload = {"v": 1, "kid": key_id(public_from_private(context["raw"])), "id": row["id"], "name": row["name"],
                   "email": row["email"], "role": row["role"], "permissions": json.loads(row["permissions"]),
                   "plan": row["plan"], "start": row["start"], "expires": row["expires"],
                   "devices": split_devices(json.loads(row["devices"]))[0],
                   "delegates": split_devices(json.loads(row["devices"]))[1],
                   "quota": row["quota"], "issued": int(self.clock())}
        if context["chain"]:
            payload["chain"] = context["chain"]
        token = sign(context["raw"], payload)
        db.execute("UPDATE licenses SET token=?,updated=? WHERE id=?", (token, self.clock(), row["id"]))
        return token

    def save_account(self, data):
        """Tạo mới (không có id) hoặc sửa tài khoản. Trả về (bản ghi, key mới)."""
        context = self.require_issuer()
        record = clean_record(data)
        now = self.clock()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if data.get("id"):
                row = db.execute("SELECT * FROM licenses WHERE id=?", (str(data["id"]),)).fetchone()
                if not row:
                    raise LicenseError("Không tìm thấy tài khoản.")
                self.check_limits(context["limits"], record["role"], record["permissions"],
                                  date.fromisoformat(row["expires"]), "create")
                db.execute("""UPDATE licenses SET name=?,email=?,role=?,permissions=?,devices=?,delegates=?,quota=?,
                              updated=? WHERE id=?""", (record["name"], record["email"], record["role"],
                                                        json.dumps(record["permissions"]), json.dumps(record["devices"]),
                                                        json.dumps(record["delegates"]), record["quota"], now, row["id"]))
                db.execute("INSERT INTO license_events(license_id,at,action,actor,detail) VALUES(?,?,?,?,?)",
                           (row["id"], now, "update", self.actor(context),
                            f"Sửa {record['name']}: role {ROLE_NAMES[record['role']]}"))
                license_id = row["id"]
            else:
                plan = data.get("plan")
                if plan not in PLANS:
                    raise LicenseError("Chọn gói 1 tháng, 1 năm hoặc 3 năm.")
                start = parse_day(data.get("start"), self.today())
                expires = add_months(start, PLANS[plan])
                self.check_limits(context["limits"], record["role"], record["permissions"], expires, "create")
                license_id = uuid.uuid4().hex[:16]
                db.execute("""INSERT INTO licenses(id,name,email,role,permissions,plan,start,expires,devices,delegates,
                              quota,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (license_id, record["name"], record["email"], record["role"],
                            json.dumps(record["permissions"]), plan, start.isoformat(), expires.isoformat(),
                            json.dumps(record["devices"]), json.dumps(record["delegates"]), record["quota"], now, now))
                db.execute("""INSERT INTO license_events(license_id,at,action,plan,expires,amount,actor,detail)
                              VALUES(?,?,?,?,?,?,?,?)""", (license_id, now, "create", plan, expires.isoformat(),
                                                           self.amount(data.get("amount")), self.actor(context),
                                                           f"Tạo {record['name']} ({PLAN_NAMES[plan]})"))
            row = db.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
            token = self.issue(db, row, context)
        return self.account(license_id), token

    @staticmethod
    def amount(value):
        value = value or 0
        if type(value) is not int or not 0 <= value <= 10_000_000_000:
            raise LicenseError("Số tiền phải là số nguyên VNĐ.")
        return value

    def renew(self, license_id, plan, amount=0):
        """Gia hạn: cộng thêm gói vào ngày hết hạn hiện tại (hoặc từ hôm nay nếu đã hết hạn), cấp key mới."""
        context = self.require_issuer()
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
            self.check_limits(context["limits"], None, None, expires, "renew")
            db.execute("UPDATE licenses SET plan=?,expires=?,locked=0,updated=? WHERE id=?",
                       (plan, expires.isoformat(), self.clock(), license_id))
            db.execute("""INSERT INTO license_events(license_id,at,action,plan,expires,amount,actor,detail)
                          VALUES(?,?,?,?,?,?,?,?)""", (license_id, self.clock(), "renew", plan, expires.isoformat(),
                                                       amount, self.actor(context),
                                                       f"Gia hạn {row['name']} thêm {PLAN_NAMES[plan]}"))
            token = self.issue(db, db.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone(), context)
        return self.account(license_id), token

    def set_locked(self, license_id, locked):
        context = self.require_issuer()
        with self.store.connect() as db:
            row = db.execute("SELECT name FROM licenses WHERE id=?", (license_id,)).fetchone()
            if not row:
                raise LicenseError("Không tìm thấy tài khoản.")
            db.execute("UPDATE licenses SET locked=?,updated=? WHERE id=?", (int(bool(locked)), self.clock(), license_id))
            db.execute("INSERT INTO license_events(license_id,at,action,actor,detail) VALUES(?,?,?,?,?)",
                       (license_id, self.clock(), "lock" if locked else "unlock", self.actor(context),
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
        codes, delegates = split_devices(json.loads(row["devices"]))
        return {"id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"],
                "permissions": json.loads(row["permissions"]), "plan": row["plan"], "start": row["start"],
                "expires": row["expires"], "devices": codes, "quota": row["quota"],
                "can_issue": bool(delegates) and bool(set(json.loads(row["permissions"])) & set(ISSUE_PERMISSIONS)),
                "device_codes": json.loads(row["devices"]),
                "state": state, "days_left": days, "token": row["token"]}

    def dashboard(self):
        """Dữ liệu trang quản lý (máy Super Admin hoặc máy được cấp quyền quản lý)."""
        context = self.require_issuer()
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
        limits = context["limits"]
        scope = {"mode": context["mode"], "roles": [r for r in ROLES if not limits or ROLE_RANK[r] >= ROLE_RANK[limits["role"]]],
                 "permissions": list(PERMISSIONS) if not limits else [p for p in PERMISSIONS if p in limits["permissions"]],
                 "max_expires": limits["expires"] if limits else None,
                 "role": limits["role"] if limits else "super_admin",
                 "can_create": True if not limits else limits["can_create"],
                 "can_renew": True if not limits else limits["can_renew"]}
        return {"accounts": accounts, "events": events, "revenue": revenue, "stats": stats, "scope": scope}
