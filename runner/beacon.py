"""Bảng theo dõi máy khách online (không cần máy chủ riêng).

Máy Super Admin tạo một cặp khóa X25519 + một "kênh" ngẫu nhiên trên trạm chuyển tin (mặc định ntfy.sh, đổi được).
Khóa công khai + kênh được ghi vào key bản quyền cấp cho khách (có chữ ký, không sửa được). Máy khách mỗi 10 phút gửi
một "nhịp tim" ĐÃ MÃ HÓA bằng khóa công khai đó: chỉ máy Super Admin (giữ khóa riêng) đọc được, trạm chuyển tin và người
ngoài chỉ thấy chuỗi mã hóa. Nhịp tim chỉ gồm: mã bản quyền, tên tài khoản, mã máy, phiên bản app, hệ điều hành, trạng
thái bản quyền, số video đang chạy / chờ — KHÔNG có prompt, video, cookie, mật khẩu.
"""
import base64
import json
import os
import platform
import secrets
import time
from pathlib import Path

DEFAULT_RELAY = "https://ntfy.sh"
INTERVAL = 600          # máy khách gửi nhịp tim mỗi 10 phút (và khi mở app)
ONLINE_SECONDS = 1200   # thấy nhịp tim trong 20 phút gần nhất = đang online
INFO = b"hana-beacon-v1"


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text):
    text = str(text)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(shared):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO).derive(shared)


def seal(public_b64, data):
    """Mã hóa cho người giữ khóa riêng: X25519 tạm thời + HKDF-SHA256 + AES-GCM. Trả về chuỗi base64."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import serialization
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(X25519PublicKey.from_public_bytes(unb64(public_b64)))
    nonce = os.urandom(12)
    sealed = AESGCM(_derive(shared)).encrypt(nonce, json.dumps(data, ensure_ascii=False).encode(), INFO)
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "HB1." + b64(eph_pub + nonce + sealed)


def unseal(private_raw, text):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not str(text).startswith("HB1."):
        raise ValueError("not a beacon")
    raw = unb64(str(text)[4:])
    eph_pub, nonce, sealed = raw[:32], raw[32:44], raw[44:]
    shared = X25519PrivateKey.from_private_bytes(private_raw).exchange(X25519PublicKey.from_public_bytes(eph_pub))
    return json.loads(AESGCM(_derive(shared)).decrypt(nonce, sealed, INFO))


class BeaconKeys:
    """Khóa riêng của bảng theo dõi trên máy Super Admin: data/license/beacon.json (không bao giờ rời máy)."""

    def __init__(self, folder):
        self.file = Path(folder) / "beacon.json"

    def load(self):
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            if len(unb64(data["private"])) == 32 and data.get("topic"):
                return data
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def create(self, relay=DEFAULT_RELAY):
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        existing = self.load()
        if existing:
            return existing
        key = X25519PrivateKey.generate()
        data = {"private": b64(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                 serialization.NoEncryption())),
                "public": b64(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
                "topic": "hana-" + secrets.token_hex(16), "relay": relay.rstrip("/"), "created": int(time.time())}
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(temp, self.file)
        return data

    def public_info(self):
        """Phần ghi vào key bản quyền cho khách (không có khóa riêng)."""
        data = self.load()
        return {"url": data["relay"], "topic": data["topic"], "pub": data["public"]} if data else None


def os_name():
    system = platform.system()
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}".strip()[:40]
    if system == "Windows":
        return f"Windows {platform.release()}"[:40]
    return f"{system} {platform.release()}"[:40]


def heartbeat(payload, machine, version, jobs=None, state=""):
    """Nội dung nhịp tim (trước khi mã hóa)."""
    return {"v": 1, "at": int(time.time()), "id": payload.get("id"), "name": payload.get("name"), "machine": machine,
            "ver": version, "os": os_name(), "state": state,
            "expires": payload.get("expires"), "role": payload.get("role"),
            "running": int((jobs or {}).get("running", 0)), "waiting": int((jobs or {}).get("waiting", 0))}


def send(http, beacon, data, timeout=10):
    """Gửi nhịp tim lên trạm chuyển tin. Trả về True nếu trạm nhận."""
    url = f"{str(beacon['url']).rstrip('/')}/{beacon['topic']}"
    code, _ = http("POST", url, {"Content-Type": "text/plain", "Title": "hb"}, seal(beacon["pub"], data).encode(), timeout)
    return 200 <= code < 300


def collect(http, keys_data, since="12h", timeout=20):
    """Máy Super Admin: tải các nhịp tim gần đây từ trạm, giải mã, gộp theo (mã bản quyền, mã máy) — bản mới nhất."""
    url = f"{keys_data['relay']}/{keys_data['topic']}/json?poll=1&since={since}"
    code, body = http("GET", url, {"Accept": "application/x-ndjson"}, None, timeout)
    if code != 200:
        raise OSError(f"Trạm chuyển tin trả mã {code}.")
    private = unb64(keys_data["private"])
    latest = {}
    for line in body.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
            if event.get("event") != "message":
                continue
            data = unseal(private, event.get("message", ""))
        except (ValueError, KeyError, TypeError):
            continue  # tin rác / không phải của mình / sai khóa: bỏ qua
        data["seen"] = int(event.get("time") or data.get("at") or 0)
        key = (str(data.get("id")), str(data.get("machine")))
        if key not in latest or data["seen"] >= latest[key]["seen"]:
            latest[key] = data
    now = time.time()
    rows = []
    for data in latest.values():
        data["online"] = now - data["seen"] <= ONLINE_SECONDS
        rows.append(data)
    rows.sort(key=lambda r: (not r["online"], -r["seen"]))
    return rows
