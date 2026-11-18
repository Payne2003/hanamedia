"""Read-only access to the HanaMedia tool's media library (Thư viện Video & Ảnh).

The tool stores image metadata in <data>/studio.sqlite3, table `media` (runner/media.py in Payne2003/hanamedia):
  media_id (32 hex = image token), media_type image|video, category character|product|reference|other,
  display_name, storage_root ('' = inside the data folder), relative_path, hidden.
We only ever open the database read-only (mode=ro) and copy image files into our own job folders, so the
tool keeps full ownership of its library. Product briefs are not stored by the tool: they live as text files in
<HanaMedia>/thu-vien/brief/<tên sản phẩm>.md (or <media_id>.md).
"""
from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from pathlib import Path

from .config import resolve_path
from .jobs import JobError

CATEGORIES = ("character", "product", "reference", "other")


def data_dir(cfg: dict) -> Path:
    p = cfg.get("hanamedia", {}).get("data_dir") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "HanaMedia", "data")
    return resolve_path(p)


def brief_dir(cfg: dict) -> Path:
    return resolve_path(cfg.get("hanamedia", {}).get("brief_dir") or str(Path.home() / "HanaMedia" / "thu-vien" / "brief"))


def slug(text: str) -> str:
    text = str(text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "item"


def _connect(cfg: dict) -> sqlite3.Connection:
    db = data_dir(cfg) / "studio.sqlite3"
    if not db.exists():
        raise JobError(f"Không thấy thư viện HanaMedia: {db} (đặt hanamedia.data_dir trong config.json)")
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _path(cfg: dict, row) -> Path | None:
    root = Path(row["storage_root"]) if row["storage_root"] else data_dir(cfg)
    p = (root / row["relative_path"]).resolve()
    return p if p.is_file() else None


def list_images(cfg: dict, category: str | None = None, query: str = "") -> list[dict]:
    clauses, params = ["hidden=0", "media_type='image'"], []
    if category:
        if category not in CATEGORIES:
            raise JobError(f"category phải là một trong {CATEGORIES}")
        clauses.append("category=?")
        params.append(category)
    if query:
        clauses.append("(display_name LIKE ? OR original_filename LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]
    with _connect(cfg) as con:
        rows = con.execute(f"SELECT * FROM media WHERE {' AND '.join(clauses)} ORDER BY created_at DESC", params).fetchall()
    out = []
    for r in rows:
        p = _path(cfg, r)
        name = r["display_name"]
        out.append({"id": r["media_id"], "name": name, "stem": Path(name).stem, "slug": slug(Path(name).stem),
                    "category": r["category"], "path": str(p) if p else None, "width": r["width"],
                    "height": r["height"]})
    return out


def resolve(cfg: dict, category: str, ref: str) -> dict:
    """Find one library image by media_id, exact display name (with/without extension) or slug."""
    items = list_images(cfg, category)
    ref = str(ref or "").strip()
    for key in ("id", "name", "stem"):
        hit = [i for i in items if i[key] == ref]
        if hit:
            break
    else:
        hit = [i for i in items if i["slug"] == slug(ref)] or [i for i in items if slug(ref) in i["slug"]]
    label = {"character": "Nhân vật", "product": "Sản phẩm"}.get(category, category)
    if not hit:
        names = ", ".join(i["stem"] for i in items[:15]) or "(trống - hãy đổi nhóm ảnh trong tool)"
        raise JobError(f"Không thấy {label} '{ref}' trong thư viện HanaMedia. Có: {names}")
    if len(hit) > 1 and hit[0]["slug"] != slug(ref) and hit[0]["id"] != ref:
        raise JobError(f"'{ref}' khớp nhiều {label}: {', '.join(h['stem'] for h in hit[:5])} - ghi rõ hơn hoặc dùng media_id")
    if not hit[0]["path"]:
        raise JobError(f"Ảnh {label} '{hit[0]['name']}' không còn file trên máy")
    return hit[0]


def find_brief(cfg: dict, product: dict, explicit: str | None = None) -> Path | None:
    """Optional brief for a product: explicit path, else <brief_dir>/<slug(name)>.md|.txt or <media_id>.md.
    None = no brief: the script may then make NO product claims (image-only mode)."""
    if explicit:
        p = resolve_path(explicit)
        if p and p.exists():
            return p
        raise JobError(f"Không thấy file brief: {explicit}")
    d = brief_dir(cfg)
    for stem in (product["slug"], product["stem"], product["id"]):
        for ext in (".md", ".txt"):
            p = d / f"{stem}{ext}"
            if p.exists():
                return p
    return None
