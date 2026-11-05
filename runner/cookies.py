"""Cookie Manager (tùy chọn) cho tài khoản Muse.

- Nhận Cookie JSON (xuất từ tiện ích kiểu Cookie-Editor / EditThisCookie: mảng object, hoặc {"cookies": [...]})
  hoặc Header String ("Cookie: a=b; c=d" hay "a=b; c=d").
- Chỉ giữ cookie của muse.ai (muse.ai và tên miền con). Cookie của trang khác bị bỏ, không lưu, không gửi đi đâu.
- Lưu MÃ HÓA: Windows dùng DPAPI (gắn với tài khoản Windows đang đăng nhập; chép thư mục data sang máy/người dùng khác
  không giải mã được). Máy khác dùng Fernet (thư viện cryptography) với khóa riêng trong thư mục dữ liệu.
- Không bao giờ trả giá trị cookie ra API, giao diện, log hay file xuất; thông báo lỗi không chứa giá trị cookie.
- Nhập cookie KHÔNG có nghĩa tài khoản LIVE: chỉ sau khi áp cookie vào Chrome của tài khoản và Health Monitor thấy
  ô soạn tin Muse thì mới là LIVE.
"""
import json
import os
import re
import time
from pathlib import Path

MAX_INPUT = 100_000
MAX_COOKIES = 200
NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,256}$")  # token theo RFC 6265
BAD_VALUE = re.compile(r"[\x00-\x1f\x7f;\"\\]")
SAME_SITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}
ENTROPY = b"HanaMedia muse cookies v1"


class CookieError(ValueError):
    pass


def muse_domain(domain):
    domain = str(domain or "").strip().lower().lstrip(".")
    return domain == "muse.ai" or domain.endswith(".muse.ai")


def parse(text, now=None):
    """Trả về (danh sách cookie đã chuẩn hóa, thống kê). Nêu CookieError với thông báo không chứa giá trị cookie."""
    now = now or time.time()
    if not isinstance(text, str) or not text.strip():
        raise CookieError("Dán Cookie JSON hoặc chuỗi Cookie trước.")
    if len(text) > MAX_INPUT:
        raise CookieError("Dữ liệu cookie quá lớn (tối đa 100 KB).")
    text = text.strip()
    if text[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            raise CookieError("Cookie JSON không hợp lệ (sai cú pháp JSON).") from None
        if isinstance(data, dict):
            data = data.get("cookies")
        if not isinstance(data, list):
            raise CookieError("Cookie JSON phải là một mảng cookie hoặc có khóa \"cookies\".")
        raw = data
        source = "json"
    else:
        header = re.sub(r"^\s*cookie\s*:\s*", "", text, flags=re.IGNORECASE)
        raw = []
        for part in header.split(";"):
            if not part.strip():
                continue
            if "=" not in part:
                raise CookieError("Chuỗi Cookie phải có dạng ten=giatri; ten2=giatri2.")
            name, value = part.split("=", 1)
            raw.append({"name": name.strip(), "value": value.strip(), "domain": ".muse.ai"})
        source = "header"
    if len(raw) > MAX_COOKIES:
        raise CookieError(f"Tối đa {MAX_COOKIES} cookie mỗi lần nhập.")
    cookies, foreign, expired = {}, 0, 0
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise CookieError(f"Cookie thứ {index} không phải object.")
        name, value = item.get("name"), item.get("value")
        if not isinstance(name, str) or not NAME.match(name):
            raise CookieError(f"Cookie thứ {index}: tên không hợp lệ.")
        if not isinstance(value, str) or len(value) > 4096 or BAD_VALUE.search(value):
            raise CookieError(f"Cookie {name}: giá trị không hợp lệ (rỗng kiểu, quá 4 KB hoặc có ký tự cấm).")
        domain = item.get("domain") or ".muse.ai"
        if not isinstance(domain, str) or not muse_domain(domain):
            foreign += 1
            continue
        expires = item.get("expirationDate", item.get("expires"))
        if expires in (None, -1, "", 0) or item.get("session") is True:
            expires = None
        else:
            try:
                expires = float(expires)
            except (TypeError, ValueError):
                raise CookieError(f"Cookie {name}: thời hạn không hợp lệ.") from None
            if expires > 1e11:  # mili giây
                expires /= 1000
            if expires <= now:
                expired += 1
                continue
        path = item.get("path") or "/"
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
            raise CookieError(f"Cookie {name}: path không hợp lệ.")
        same_site = SAME_SITE.get(str(item.get("sameSite") or "").lower())
        cookie = {"name": name, "value": value, "domain": domain.strip().lower(), "path": path,
                  "secure": bool(item.get("secure", True)), "httpOnly": bool(item.get("httpOnly", False))}
        if same_site:
            cookie["sameSite"] = same_site
            if same_site == "None":
                cookie["secure"] = True
        if expires:
            cookie["expires"] = expires
        cookies[(cookie["name"], cookie["domain"], cookie["path"])] = cookie  # trùng: giữ bản sau
    if not cookies:
        reason = (f"Tất cả {expired} cookie muse.ai đã hết hạn." if expired else
                  "Không có cookie nào của muse.ai." if foreign else "Không có cookie hợp lệ.")
        raise CookieError(reason)
    stats = {"count": len(cookies), "foreign": foreign, "expired": expired, "source": source,
             "expires_at": min((c["expires"] for c in cookies.values() if "expires" in c), default=None)}
    return list(cookies.values()), stats


class Vault:
    """Mã hóa/giải mã dữ liệu phiên. Windows: DPAPI. Nơi khác: Fernet (cryptography) với khóa trong thư mục dữ liệu."""

    def __init__(self, data):
        self.key_file = Path(data) / "cookie.key"

    @staticmethod
    def kind():
        if os.name == "nt":
            return "dpapi"
        try:
            import cryptography.fernet  # noqa: F401
            return "fernet"
        except ImportError:
            return None

    def available(self):
        return self.kind() is not None

    def fernet(self):
        from cryptography.fernet import Fernet
        if not self.key_file.exists():
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
        return Fernet(self.key_file.read_bytes().strip())

    def encrypt(self, data):
        kind = self.kind()
        if kind == "dpapi":
            return b"dpapi:" + dpapi(data, protect=True)
        if kind == "fernet":
            return b"fernet:" + self.fernet().encrypt(data)
        raise CookieError("Máy này không có cách mã hóa an toàn để lưu cookie.")

    def decrypt(self, blob):
        try:
            if blob.startswith(b"dpapi:") and os.name == "nt":
                return dpapi(blob[6:], protect=False)
            if blob.startswith(b"fernet:"):
                return self.fernet().decrypt(blob[7:])
        except Exception:
            pass
        raise CookieError("Không giải mã được cookie đã lưu (khác máy, khác tài khoản Windows hoặc dữ liệu hỏng). "
                          "Nhập lại cookie.")


def dpapi(data, protect):
    """CryptProtectData / CryptUnprotectData của Windows (không cần thư viện ngoài)."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(value):
        buffer = ctypes.create_string_buffer(value, len(value))
        return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    source, keep1 = blob(data)
    entropy, keep2 = blob(ENTROPY)
    output = Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x01, ctypes.byref(output))
    if not ok:
        raise OSError("DPAPI thất bại")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


class CookieStore:
    """Bảng account_cookies: chỉ lưu bản mã hóa và thông tin không nhạy cảm (số lượng, thời điểm)."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS account_cookies (
        account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
        payload BLOB NOT NULL,
        cookie_count INTEGER NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('json', 'header')),
        expires_at REAL,
        imported_at REAL NOT NULL,
        applied_at REAL,
        apply_result TEXT NOT NULL DEFAULT '');
    """

    def __init__(self, store):
        self.store = store
        self.vault = Vault(Path(store.path).resolve().parent)
        with store.connect() as db:
            db.executescript(self.SCHEMA)

    def save(self, account_id, text):
        if not self.vault.available():
            raise CookieError("Máy này không có cách mã hóa an toàn để lưu cookie.")
        cookies, stats = parse(text)
        payload = self.vault.encrypt(json.dumps(cookies, separators=(",", ":")).encode())
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise CookieError("Không tìm thấy tài khoản.")
            db.execute("""INSERT INTO account_cookies(account_id,payload,cookie_count,source,expires_at,imported_at)
                          VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload,
                          cookie_count=excluded.cookie_count,source=excluded.source,expires_at=excluded.expires_at,
                          imported_at=excluded.imported_at,applied_at=NULL,apply_result=''""",
                       (account_id, payload, stats["count"], stats["source"], stats["expires_at"], time.time()))
        return stats

    def load(self, account_id):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM account_cookies WHERE account_id=?", (account_id,)).fetchone()
        if not row:
            return None
        return json.loads(self.vault.decrypt(bytes(row["payload"])))

    def mark_applied(self, account_id, result):
        with self.store.connect() as db:
            db.execute("UPDATE account_cookies SET applied_at=?,apply_result=? WHERE account_id=?",
                       (time.time(), result, account_id))

    def delete(self, account_id):
        with self.store.connect() as db:
            return db.execute("DELETE FROM account_cookies WHERE account_id=?", (account_id,)).rowcount

    def summaries(self, db):
        """Thông tin hiển thị (không có giá trị cookie)."""
        return {row["account_id"]: {"cookie_count": row["cookie_count"], "cookie_source": row["source"],
                                    "cookie_expires_at": row["expires_at"], "cookie_imported_at": row["imported_at"],
                                    "cookie_applied_at": row["applied_at"], "cookie_result": row["apply_result"]}
                for row in db.execute("""SELECT account_id,cookie_count,source,expires_at,imported_at,applied_at,
                                         apply_result FROM account_cookies""")}
