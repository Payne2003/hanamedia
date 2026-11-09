"""Thư viện âm thanh tự nhiên cho Sound Agent: gió, nước, côn trùng, bước chân… (file của người dùng, lưu trên máy)."""
import hashlib
import re
import time
import uuid
from pathlib import Path

from .characters import check_id, text

AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac")
MAX_BYTES = 80 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS production_sounds (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL,
 duration REAL NOT NULL, sha256 TEXT NOT NULL UNIQUE, created REAL NOT NULL);
"""


class SoundLibrary:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        with store.connect() as db:
            db.executescript(SCHEMA)

    def list(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,name,tags,duration,created FROM production_sounds ORDER BY name")]

    def path(self, sound_id):
        check_id(sound_id, "Âm thanh")
        with self.store.connect() as db:
            row = db.execute("SELECT filename FROM production_sounds WHERE id=?", (sound_id,)).fetchone()
        if not row:
            raise ValueError("Không tìm thấy âm thanh trong thư viện.")
        path = (self.root / row["filename"]).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise ValueError("File âm thanh đã bị xóa khỏi thư mục thư viện.")
        return path

    def add(self, data, name, tags=""):
        from . import video_tools as video
        name = text(name, 120)
        suffix = Path(name).suffix.lower()
        if suffix not in AUDIO_EXTENSIONS:
            raise ValueError("Chỉ nhận file âm thanh wav, mp3, m4a, aac, ogg, flac.")
        if not data or len(data) > MAX_BYTES:
            raise ValueError("File âm thanh trống hoặc lớn hơn 80 MB.")
        digest = hashlib.sha256(data).hexdigest()
        with self.store.connect() as db:
            old = db.execute("SELECT id,name,tags,duration,created FROM production_sounds WHERE sha256=?", (digest,)).fetchone()
        if old:
            return dict(old), True
        self.root.mkdir(parents=True, exist_ok=True)
        sound_id = uuid.uuid4().hex
        target = self.root / (sound_id + suffix)
        target.write_bytes(data)
        try:
            result = video.run([video.ffmpeg(), "-hide_banner", "-i", str(target)], 60)
            found = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
            if not re.search(r"Stream #\S+.*?Audio:", result.stderr) or not found:
                raise ValueError("File không có luồng âm thanh đọc được.")
            duration = int(found[1]) * 3600 + int(found[2]) * 60 + float(found[3])
            if not 0.2 <= duration <= 3600:
                raise ValueError("Âm thanh cần dài từ 0,2 giây đến 60 phút.")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        stem = Path(name).stem or "Âm thanh"
        with self.store.connect() as db:
            db.execute("INSERT INTO production_sounds VALUES(?,?,?,?,?,?,?)",
                       (sound_id, stem, text(tags, 300), target.name, round(duration, 3), digest, time.time()))
        return dict(id=sound_id, name=stem, tags=text(tags, 300), duration=round(duration, 3)), False

    def update(self, sound_id, name=None, tags=None):
        check_id(sound_id, "Âm thanh")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM production_sounds WHERE id=?", (sound_id,)).fetchone()
            if not row:
                raise ValueError("Không tìm thấy âm thanh.")
            db.execute("UPDATE production_sounds SET name=?,tags=? WHERE id=?",
                       (text(name, 120) or row["name"] if name is not None else row["name"],
                        text(tags, 300) if tags is not None else row["tags"], sound_id))

    def delete(self, sound_id):
        path = None
        try:
            path = self.path(sound_id)
        except ValueError:
            pass
        with self.store.connect() as db:
            if db.execute("DELETE FROM production_sounds WHERE id=?", (sound_id,)).rowcount != 1:
                raise ValueError("Không tìm thấy âm thanh.")
        if path:
            path.unlink(missing_ok=True)
