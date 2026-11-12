"""Plugin viết prompt: thư mục plugin dạng "skill" (Codex / ChatGPT) — plugin.json + skills/<tên>/SKILL.md kèm các thư
mục tài liệu (references, lookup, agents…). Tool đọc phần CHỮ (SKILL.md và tài liệu .md/.txt/.json/.csv/.yaml), lưu một
bản vào thư mục dữ liệu, rồi đưa vào đầu mỗi yêu cầu viết / kiểm duyệt prompt. Không chạy bất kỳ mã nào trong plugin.
"""
import json
import re
from pathlib import Path

TEXT_TYPES = {".md", ".markdown", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml"}
MAX_CHARS = 24000  # đủ cho SKILL.md + tài liệu chính; phần này đi kèm MỖI yêu cầu viết prompt nên không để quá lớn
MAX_FILE = 200_000  # bỏ qua tệp quá lớn (thường là dữ liệu thô)
FOLDER_ORDER = ("references", "reference", "lookup", "docs", "templates", "examples", "agents")
SKIP_PARTS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


class PluginError(ValueError):
    pass


def strip_frontmatter(text):
    """Bỏ khối YAML đầu tệp (--- … ---), trả về (thông tin, nội dung)."""
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip() and value.strip():
            meta[key.strip().lower()] = value.strip().strip("'\"")
    return meta, text[match.end():]


def read_text(path):
    try:
        data = path.read_bytes()[:MAX_FILE + 1]
    except OSError:
        return None
    if len(data) > MAX_FILE or b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace").replace("\r\n", "\n").strip()


def load(folder):
    """Đọc thư mục plugin. Trả về {name, description, skills, files, text, chars, truncated}."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise PluginError("Không thấy thư mục plugin.")
    info = {}
    manifest = root / "plugin.json"
    if manifest.is_file():
        try:
            info = json.loads(read_text(manifest) or "{}")
        except ValueError:
            info = {}
    skills = sorted(p for p in root.rglob("SKILL.md") if not SKIP_PARTS & set(p.relative_to(root).parts))
    if not skills:
        raise PluginError("Thư mục này không có tệp SKILL.md (plugin viết prompt cần SKILL.md trong thư mục skills).")
    blocks, files, used = [], [], 0
    names = []
    for skill in skills[:5]:
        meta, body = strip_frontmatter(read_text(skill) or "")
        names.append(meta.get("name") or skill.parent.name)
        blocks.append(("SKILL", skill, body.strip()))
        mentioned = set(re.findall(r"[\w./-]+\.(?:md|txt|json|csv|ya?ml)", body))
        extras = [p for p in skill.parent.rglob("*") if p.is_file() and p != skill and p.suffix.lower() in TEXT_TYPES
                  and not SKIP_PARTS & set(p.relative_to(root).parts)]

        def rank(path):
            rel = path.relative_to(skill.parent).as_posix()
            named = any(rel.endswith(m) or path.name == Path(m).name for m in mentioned)
            top = rel.split("/")[0]
            return (0 if named else 1, FOLDER_ORDER.index(top) if top in FOLDER_ORDER else len(FOLDER_ORDER), rel)
        for path in sorted(extras, key=rank):
            text = read_text(path)
            if text:
                blocks.append(("FILE", path, text))
    parts, truncated = [], False
    for kind, path, text in blocks:
        rel = path.relative_to(root).as_posix()
        piece = (f"### {rel}\n{text}" if kind == "FILE" else f"## SKILL: {rel}\n{text}")
        if used + len(piece) > MAX_CHARS:
            room = MAX_CHARS - used
            if kind == "SKILL" or room > 2000:
                parts.append(piece[:max(0, room)] + "\n…(đã cắt bớt)")
                files.append(rel)
            truncated = True
            break
        parts.append(piece)
        files.append(rel)
        used += len(piece) + 2
    text = "\n\n".join(parts).strip()
    if not text:
        raise PluginError("SKILL.md trống.")
    name = str(info.get("name") or info.get("displayName") or ", ".join(names) or root.name)[:120]
    description = str(info.get("description") or "")[:500]
    return {"name": name, "description": description, "skills": names, "files": files, "text": text,
            "chars": len(text), "truncated": truncated, "folder": str(root)}


def block(plugin):
    """Khối hướng dẫn đặt ở ĐẦU mỗi yêu cầu (phần đầu giống nhau giữa các lần gửi giúp Google tự giảm giá token)."""
    if not plugin or not plugin.get("text"):
        return ""
    return (f"PROMPT-WRITING PLUGIN «{plugin.get('name', 'plugin')}» — follow these instructions for style, wording, "
            "structure and quality of every prompt. They do NOT override the scene facts or the exact output format "
            "required after this block (numbering, one prompt per line, seconds, end marker).\n"
            "=== PLUGIN START ===\n" + plugin["text"] + "\n=== PLUGIN END ===\n\n")
