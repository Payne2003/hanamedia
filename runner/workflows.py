"""Nhiều workflow trong Không gian Agent (chỉ máy Super Admin dùng).

Mỗi workflow là một thư mục  <HanaMedia>/workflows/<slug>/ :
  workflow.json      tên, câu lệnh chạy, vai của từng agent (Master + 3 bàn) gắn với kỹ năng nào
  skills/<tên>/      BẢN GỐC các kỹ năng (SKILL.md + tài liệu) — AI sửa ở đây khi có yêu cầu thay đổi
  requests.md        yêu cầu sửa kỹ năng chưa xử lý (nhập từ ô "Yêu cầu thay đổi" trong tool)
  requests-done.md   yêu cầu đã xử lý (AI chuyển sang sau khi sửa)
  CHANGELOG.md       AI ghi tóm tắt mỗi lần sửa kỹ năng
  runs/<thời gian>.log  nhật ký mỗi lần tool tự gọi Claude Code / Codex
Trước khi chạy, tool chép kỹ năng sang  <HanaMedia>/.claude/skills/  (Claude Code) và  ~/.codex/skills/  (Codex),
và đóng gói zip cho Claude desktop (dist/). Tiến độ: AI ghi  output/_status/<slug>.json .
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import zipfile
from pathlib import Path

SLOTS = ("conductor", "analyst", "writer", "studio")  # Master + 3 bàn trong không gian 3D
SLOT_NAMES = {"conductor": "Master Agent", "analyst": "Agent 1", "writer": "Agent 2", "studio": "Agent 3"}
MAX_REQUEST = 6000
MAX_LOG = 400_000
DEFAULT = {
    "slug": "wildlife", "name": "Động vật hoang dã", "builtin": True,
    "command": "Chạy hanamedia-wildlife-workflow",
    "description": "Phân tích video đối thủ → prompt 8–10 giây cho Meta Muse → Studio.",
    "agents": {
        "conductor": {"name": "Master Agent", "role": "Điều phối", "skill": "hanamedia-wildlife-workflow"},
        "analyst": {"name": "Agent 1", "role": "Phân tích video", "skill": "phan-tich-video-doi-thu"},
        "writer": {"name": "Agent 2", "role": "Viết prompt", "skill": "wildlife-motion-prompt-meta"},
        "studio": {"name": "Agent 3", "role": "Studio · Muse", "skill": ""},
    },
}


BUILTIN = ("wildlife", "thoai-phim", "universal")


CLAUDE_TOOLS = ",".join(["Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Skill", "TodoWrite"] + [
    f"Bash({cmd}:*)" for cmd in ("python", "python3", "py", "ffmpeg", "ffprobe", "ls", "dir", "mkdir", "cp", "copy", "mv",
                                 "move", "cat", "type", "head", "tail", "wc", "find", "pwd", "echo", "cd")])


def run_env():
    """Biến môi trường cho Claude / Codex: thêm ffmpeg của tool vào PATH (máy khách thường không cài ffmpeg riêng)."""
    env = dict(os.environ)
    try:
        from . import video_tools
        folder = str(Path(video_tools.ffmpeg()).parent)
        env["PATH"] = folder + os.pathsep + env.get("PATH", "")
    except Exception:
        pass
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _short(value, limit=160):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def stream_line(line):
    """Một dòng stream-json của Claude Code → dòng nhật ký dễ đọc (None = bỏ qua)."""
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return line  # thông báo thường (vd lỗi đăng nhập) giữ nguyên
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return f"▶ Claude Code bắt đầu · model {event.get('model', '?')}"
    if kind == "assistant":
        out = []
        for part in (event.get("message") or {}).get("content") or []:
            if part.get("type") == "text" and part.get("text", "").strip():
                out.append(_short(part["text"], 600))
            elif part.get("type") == "tool_use":
                data = part.get("input") or {}
                detail = data.get("command") or data.get("file_path") or data.get("skill") or data.get("pattern") \
                    or data.get("path") or data.get("description") or ""
                out.append(f"  → {part.get('name')}: {_short(detail)}")
        return "\n".join(out) or None
    if kind == "user":
        for part in (event.get("message") or {}).get("content") or []:
            if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("is_error"):
                content = part.get("content")
                if isinstance(content, list):
                    content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
                return f"  ✕ {_short(content, 300)}"
        return None
    if kind == "result":
        head = "✕ LỖI" if event.get("is_error") else "✓ XONG"
        return f"\n{head}: {_short(event.get('result') or event.get('subtype'), 1500)}"
    return None


def pump_stream(stdout, log):
    for line in stdout:
        text = stream_line(line)
        if text is None:
            continue
        try:
            log.write(text + "\n")
            log.flush()
        except (OSError, ValueError):
            return


class WorkflowError(ValueError):
    pass


def slugify(text):
    text = str(text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "workflow"


def clean(text, limit):
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", str(text or "")).strip()[:limit]


class Workflows:
    def __init__(self, folder_getter):
        self.folder_getter = folder_getter  # trả về thư mục HanaMedia hiện tại (đổi được trong cài đặt)
        self.lock = threading.Lock()
        self.runs = {}  # slug -> {"process", "log", "engine", "mode", "started", "code"}
        self.chats = {}  # slug -> cửa sổ trò chuyện đang mở (người dùng tự đóng)

    # ---- thư mục ----
    @property
    def home(self):
        return Path(self.folder_getter())

    @property
    def root(self):
        return self.home / "workflows"

    def folder(self, slug):
        if not re.fullmatch(r"[a-z0-9-]{1,40}", str(slug or "")):
            raise WorkflowError("Workflow không hợp lệ.")
        path = self.root / slug
        if not (path / "workflow.json").is_file():
            if slug not in BUILTIN:
                raise WorkflowError("Không tìm thấy workflow.")
            self.ensure_default()
        return path

    def ensure_default(self):
        path = self.root / "wildlife"
        if not (path / "workflow.json").is_file():
            (path / "skills").mkdir(parents=True, exist_ok=True)
            self.write_json(path / "workflow.json", {k: v for k, v in DEFAULT.items() if k != "slug"})
        self.ensure_narration()
        self.ensure_universal()

    def ensure_universal(self):
        """Workflow "Universal" (8 module): tạo lần đầu kèm bộ kỹ năng gốc + file bài học; file đã có thì KHÔNG ghi đè."""
        from . import universal_skills as pack
        path = self.root / pack.SLUG
        if not (path / "workflow.json").is_file():
            self.write_json(path / "workflow.json", pack.WORKFLOW)
        lessons = path / "lessons.md"
        if not lessons.exists():
            lessons.write_text(pack.LESSONS_TEMPLATE, encoding="utf-8", newline="\n")
        marker = path / "skills" / ".builtin"
        if marker.is_file():
            return
        for rel, text in pack.files().items():
            target = path / "skills" / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="\n")
        marker.write_text("1", encoding="utf-8")

    def lessons(self, slug="universal"):
        """Các bài học lỗi đã rút ra (chung cho mọi dự án của workflow Universal)."""
        text = self.read_tail(self.root / slug / "lessons.md", 20000)
        return [line[2:].strip() for line in text.splitlines() if line.startswith("- [")]

    def ensure_narration(self):
        """Workflow "Thoại phim": tạo lần đầu kèm bộ kỹ năng gốc; file đã có trên máy thì KHÔNG ghi đè (bản máy là bản chính)."""
        from . import narration_skills as pack
        path = self.root / pack.SLUG
        if not (path / "workflow.json").is_file():
            self.write_json(path / "workflow.json", pack.WORKFLOW)
        # Agent 1 dùng lại kỹ năng phân tích của workflow động vật (nếu đã nạp ở đó)
        source = self.root / "wildlife" / "skills" / "phan-tich-video-doi-thu"
        target = path / "skills" / "phan-tich-video-doi-thu"
        if source.is_dir() and not target.exists():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        marker = path / "skills" / ".builtin"
        if marker.is_file():
            return
        for rel, text in pack.files().items():
            target = path / "skills" / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="\n")
        marker.write_text("1", encoding="utf-8")

    @staticmethod
    def write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, path)

    def load(self, slug):
        path = self.folder(slug)
        try:
            data = json.loads((path / "workflow.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {k: v for k, v in DEFAULT.items() if k != "slug"} if slug == "wildlife" else {}
        agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
        data["agents"] = {s: {"name": clean((agents.get(s) or {}).get("name"), 40) or SLOT_NAMES[s],
                              "role": clean((agents.get(s) or {}).get("role"), 60),
                              "skill": clean((agents.get(s) or {}).get("skill"), 80)} for s in SLOTS}
        data["slug"] = slug
        data["name"] = clean(data.get("name"), 60) or slug
        data["command"] = clean(data.get("command"), 300)
        data["description"] = clean(data.get("description"), 400)
        data["skills"] = self.skills(slug)
        data["requests"] = self.requests(slug)
        data["changelog"] = self.read_tail(path / "CHANGELOG.md", 4000)
        return data

    def list(self):
        self.ensure_default()
        items = []
        for path in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if (path / "workflow.json").is_file():
                try:
                    items.append(self.load(path.name))
                except WorkflowError:
                    continue
        items.sort(key=lambda w: (w["slug"] != "wildlife", w["name"].lower()))
        return items

    # ---- tạo / sửa ----
    def save(self, data):
        name = clean(data.get("name"), 60)
        if not name:
            raise WorkflowError("Đặt tên workflow.")
        slug = data.get("slug") or slugify(name)
        if not data.get("slug"):
            base, n = slug, 2
            while (self.root / slug).exists():
                slug, n = f"{base}-{n}", n + 1
        path = self.root / slug
        current = {}
        if (path / "workflow.json").is_file():
            current = json.loads((path / "workflow.json").read_text(encoding="utf-8"))
        agents = data.get("agents") if isinstance(data.get("agents"), dict) else current.get("agents", {})
        record = {"name": name, "builtin": bool(current.get("builtin")),
                  "command": clean(data.get("command", current.get("command")), 300),
                  "description": clean(data.get("description", current.get("description")), 400),
                  "agents": {s: {k: clean((agents.get(s) or {}).get(k), 80) for k in ("name", "role", "skill")} for s in SLOTS},
                  "updated": int(time.time())}
        (path / "skills").mkdir(parents=True, exist_ok=True)
        self.write_json(path / "workflow.json", record)
        return self.load(slug)

    def delete(self, slug):
        path = self.folder(slug)
        if slug in BUILTIN:
            raise WorkflowError("Không xóa workflow mặc định.")
        if self.running(slug):
            raise WorkflowError("Workflow đang chạy.")
        trash = self.home / "workflows-deleted" / f"{slug}-{int(time.time())}"
        trash.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(trash))  # không xóa hẳn: chuyển sang workflows-deleted/

    # ---- kỹ năng ----
    def skills(self, slug):
        base = self.root / slug / "skills"
        out = []
        for skill in sorted(base.glob("*/SKILL.md")) if base.is_dir() else []:
            text = skill.read_text(encoding="utf-8", errors="replace")
            match = re.search(r'^description:\s*"?(.*?)"?\s*$', text, re.M)
            out.append({"name": skill.parent.name, "description": (match.group(1) if match else "")[:200],
                        "chars": len(text), "updated": int(skill.stat().st_mtime)})
        return out

    def import_skills(self, slug, source):
        """Chép kỹ năng từ một thư mục: chính nó có SKILL.md, hoặc các thư mục con có SKILL.md, hoặc file .zip."""
        path = self.folder(slug)
        source = Path(str(source or "").strip().strip('"')).expanduser()
        if not source.exists():
            raise WorkflowError("Không thấy thư mục / file kỹ năng.")
        temp = None
        if source.is_file() and source.suffix.lower() == ".zip":
            temp = path / ".import"
            shutil.rmtree(temp, ignore_errors=True)
            with zipfile.ZipFile(source) as z:
                for member in z.namelist():
                    target = (temp / member).resolve()
                    if not str(target).startswith(str(temp.resolve())):
                        raise WorkflowError("File zip không hợp lệ.")
                z.extractall(temp)
            source = temp
        found = [source] if (source / "SKILL.md").is_file() else sorted(p.parent for p in source.rglob("SKILL.md"))
        found = [p for p in found if ".git" not in p.parts][:20]
        if not found:
            raise WorkflowError("Không có SKILL.md nào trong thư mục này.")
        names = []
        for skill in found:
            name = slugify(skill.name) if skill != temp else slugify(source.name)
            target = path / "skills" / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(skill, target, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "node_modules"))
            names.append(name)
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
        return names

    def remove_skill(self, slug, name):
        target = self.folder(slug) / "skills" / slugify(name)
        if target.is_dir():
            shutil.rmtree(target)

    def sync(self, slug):
        """Chép bản gốc kỹ năng sang chỗ Claude Code / Codex đọc, đóng gói zip cho Claude desktop."""
        path = self.folder(slug)
        done = {"claude": 0, "codex": 0, "zip": 0}
        targets = [("claude", self.home / ".claude" / "skills"), ("codex", Path.home() / ".codex" / "skills")]
        dist = path / "dist"
        dist.mkdir(exist_ok=True)
        for skill in (path / "skills").glob("*/SKILL.md"):
            src = skill.parent
            for key, base in targets:
                try:
                    dst = base / src.name
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    done[key] += 1
                except OSError:
                    pass
            with zipfile.ZipFile(dist / f"{src.name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
                for file in src.rglob("*"):
                    if file.is_file():
                        z.write(file, f"{src.name}/{file.relative_to(src).as_posix()}")
            done["zip"] += 1
        return done

    # ---- yêu cầu thay đổi ----
    def requests(self, slug):
        text = self.read_tail(self.root / slug / "requests.md", 20000)
        return [block.strip() for block in re.split(r"\n(?=## )", text) if block.strip().startswith("## ")]

    def add_request(self, slug, text, target=""):
        path = self.folder(slug)
        text = clean(text, MAX_REQUEST)
        if not text:
            raise WorkflowError("Nhập nội dung cần thay đổi.")
        target = clean(target, 80)
        entry = f"## {time.strftime('%Y-%m-%d %H:%M')}" + (f" · kỹ năng: {target}" if target else "") + f"\n{text}\n\n"
        with open(path / "requests.md", "a", encoding="utf-8") as handle:
            handle.write(entry)
        return self.requests(slug)

    def report_error(self, slug, text, context=""):
        """Người dùng báo lỗi video: thành một yêu cầu thay đổi mang nhãn «LỖI VIDEO». Lần chạy "chỉ áp dụng yêu cầu"
        (hoặc lần chạy kế) AI chẩn đoán module gây lỗi, sửa kỹ năng và ghi bài học chung."""
        text = clean(text, MAX_REQUEST - 2000)
        if not text:
            raise WorkflowError("Mô tả lỗi bạn thấy trong video.")
        body = "LỖI VIDEO: " + text + (f"\nBối cảnh: {clean(context, 1800)}" if context else "")
        return self.add_request(slug, body, "adaptive-quality-control" if slug == "universal" else "")

    def clear_requests(self, slug):
        path = self.folder(slug)
        (path / "requests.md").unlink(missing_ok=True)

    @staticmethod
    def read_tail(path, limit):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-limit:]

    # ---- chạy bằng Claude Code / Codex ----
    @staticmethod
    def engines():
        from .agent_cli import find
        return {"claude": bool(find("claude")), "codex": bool(find("codex"))}

    def prompt(self, slug, mode):
        data = self.load(slug)
        rel = f"workflows/{slug}"
        roles = "; ".join(f"{SLOT_NAMES[s]} = {a['skill'] or '—'} ({a['role'] or a['name']})" for s, a in data["agents"].items())
        lines = [
            f"Bạn đang làm việc trong thư mục HanaMedia. Workflow: «{data['name']}».",
            f"Bản gốc các kỹ năng của workflow nằm ở {rel}/skills/<tên kỹ năng>/SKILL.md. Vai các agent: {roles}.",
            "",
            f"BƯỚC 1 — YÊU CẦU THAY ĐỔI: đọc {rel}/requests.md (nếu có).",
            f"- Với từng mục: sửa ĐÚNG phần được yêu cầu trong file SKILL.md liên quan ở {rel}/skills/ (không viết lại phần khác,"
            " giữ nguyên định dạng file, giữ khối frontmatter name/description).",
            f"- Ghi tóm tắt đã sửa gì, ở file nào vào cuối {rel}/CHANGELOG.md (kèm ngày giờ).",
            f"- Chuyển các mục đã xử lý từ requests.md sang cuối {rel}/requests-done.md rồi xóa chúng khỏi requests.md.",
            "- Không có requests.md hoặc file trống thì bỏ qua bước này.",
            "",
        ]
        if slug == "universal":
            from . import universal_skills as pack
            lines[7:7] = [
                f"- Mục nào bắt đầu bằng nhãn «LỖI VIDEO» là LỖI NGƯỜI DÙNG BÁO về một video Muse đã tạo. Với từng mục: "
                f"(1) chẩn đoán lỗi do module nào (xem kỹ năng {pack.QC}); (2) sửa ĐÚNG SKILL.md của module đó để lỗi "
                f"không lặp lại; (3) thêm một dòng vào mục «Luật hiện hành» của {pack.LESSONS} theo mẫu trong file "
                "(gộp nếu trùng ý với luật đã có, giữ file ngắn gọn); (4) ghi vào CHANGELOG.md module nào đã sửa và vì sao.",
            ]
        if mode == "apply":
            lines.append("BƯỚC 2 — CHỈ sửa kỹ năng theo yêu cầu, KHÔNG chạy workflow. Trả lời ngắn gọn đã sửa những gì.")
        else:
            lines += [
                f"BƯỚC 2 — CHẠY WORKFLOW: {data['command'] or 'chạy kỹ năng điều phối của workflow này'}.",
                f"- Dùng các kỹ năng trong {rel}/skills/ (đọc SKILL.md của kỹ năng tương ứng khi cần gọi nó).",
                "- Video / dữ liệu vào: thư mục input/ ; kết quả: output/ (theo quy ước trong kỹ năng). Chỉ làm dự án đang "
                "có trong input/, KHÔNG đọc input-done/ hay kết quả của dự án cũ trong output/.",
                "- Trong file JSON kết quả cho tool, thêm trường \"character\": tên nhân vật chính (tiếng Việt) để tool đặt "
                "tên thư mục kết quả theo nhân vật.",
                f"- Trong lúc chạy, ghi tiến độ vào output/_status/{slug}.json dạng "
                '{"agents": {"<tên kỹ năng>": "working|done|error"}, "item": "<video/việc đang làm>", "percent": 0-100, '
                '"note": "<một câu>"} — cập nhật mỗi khi chuyển bước.',
                "- Không hỏi lại người dùng giữa chừng.",
            ]
            brief = self.brief(slug)
            if brief:
                lines.append("- THIẾT LẬP LẦN CHẠY NÀY (cũng lưu ở " + f"{rel}/brief.json): "
                             + "; ".join(f"{k} = {v}" for k, v in brief.items() if v) + ".")
        return "\n".join(lines)

    def brief(self, slug):
        try:
            data = json.loads((self.root / slug / "brief.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k)[:30]: clean(v, 600) for k, v in data.items()} if isinstance(data, dict) else {}

    def save_brief(self, slug, data):
        path = self.folder(slug)
        record = {str(k)[:30]: clean(v, 600) for k, v in (data or {}).items()}
        self.write_json(path / "brief.json", record)
        return record

    def open_chat(self, slug, engine):
        """Mở cửa sổ trò chuyện Claude Code / Codex (chế độ tương tác) và gửi sẵn lệnh chạy workflow: người dùng xem
        từng bước, trả lời / trò chuyện tiếp ngay trong cửa sổ đó. Trả về process (Windows) hoặc None (Mac/Linux)."""
        from .agent_cli import WINDOWS, find, open_terminal
        if engine not in ("claude", "codex"):
            raise WorkflowError("Chọn Claude Code hoặc Codex.")
        exe = find(engine)
        if not exe:
            raise WorkflowError("Máy chưa có " + ("Claude Code." if engine == "claude" else "Codex."))
        self.folder(slug)
        self.sync(slug)
        (self.home / "output" / "_status").mkdir(parents=True, exist_ok=True)
        runs = self.root / slug / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        prompt_file = runs / f"{time.strftime('%Y%m%d-%H%M%S')}-lenh.md"
        prompt_file.write_text(self.prompt(slug, "run"), encoding="utf-8")
        rel = prompt_file.relative_to(self.home).as_posix()
        short = (f"Đây là lệnh tự động từ tool HanaMedia. Đọc file {rel} và làm đúng theo hướng dẫn trong đó, "
                 "chạy liền một mạch tới khi xong rồi báo ngắn gọn.")
        model = os.environ.get("HANAMEDIA_MODEL", "").strip()
        if engine == "claude":
            args = [exe, "--permission-mode", "acceptEdits", "--allowedTools", CLAUDE_TOOLS] + (["--model", model] if model else [])
        else:
            args = [exe, "-s", "workspace-write", "-a", "on-request", "-C", str(self.home)] + (["-m", model] if model else [])
        args.append(short)
        if WINDOWS:
            process = subprocess.Popen(args, cwd=str(self.home), env=run_env(),
                                       creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        else:
            quote = lambda value: "'" + str(value).replace("'", "'\\''") + "'"
            open_terminal(f"cd {quote(self.home)} && " + " ".join(quote(a) for a in args), "HanaMedia - Claude")
            process = None
        self.chats[slug] = {"process": process, "engine": engine, "started": time.time(), "prompt": str(prompt_file)}
        return process

    def running(self, slug):
        run = self.runs.get(slug)
        return bool(run and run["process"].poll() is None)

    def run(self, slug, engine, mode="run"):
        if engine not in ("claude", "codex"):
            raise WorkflowError("Chọn Claude Code hoặc Codex.")
        if mode not in ("run", "apply"):
            raise WorkflowError("Thao tác không hợp lệ.")
        from .agent_cli import find
        exe = find(engine)
        if not exe:
            raise WorkflowError(("Máy chưa cài Claude Code (lệnh claude)." if engine == "claude" else
                                 "Máy chưa cài Codex CLI (lệnh codex).") + " Dùng nút Chép lệnh để chạy tay.")
        with self.lock:
            if self.running(slug):
                raise WorkflowError("Workflow này đang chạy.")
            path = self.folder(slug)
            self.sync(slug)
            (self.home / "input").mkdir(parents=True, exist_ok=True)
            (self.home / "output" / "_status").mkdir(parents=True, exist_ok=True)
            prompt = self.prompt(slug, mode)
            model = os.environ.get("HANAMEDIA_MODEL", "").strip()
            if engine == "claude":
                # chỉ đọc / ghi file, gọi kỹ năng, chạy python / ffmpeg và vài lệnh xem thư mục — không toàn quyền shell
                # stream-json: thấy từng bước Claude làm trong nhật ký / cửa sổ xem (text thường chỉ in khi xong hẳn)
                args = [exe, "-p", "--permission-mode", "acceptEdits", "--allowedTools", CLAUDE_TOOLS,
                        "--output-format", "stream-json", "--verbose"]
                if model:
                    args += ["--model", model]
            else:
                # bản Codex mới bỏ --full-auto: chạy không hỏi, chỉ ghi được trong thư mục HanaMedia
                args = [exe, "exec", "-s", "workspace-write", "--skip-git-repo-check", "-C", str(self.home)]
                if model:
                    args += ["-m", model]
                args.append("-")
            runs = path / "runs"
            runs.mkdir(exist_ok=True)
            log_path = runs / f"{time.strftime('%Y%m%d-%H%M%S')}-{engine}-{mode}.log"
            log = open(log_path, "w", encoding="utf-8", errors="replace")
            log.write(f"# {engine} · {'chỉ sửa kỹ năng' if mode == 'apply' else 'chạy workflow'} · {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            log.flush()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            stream = engine == "claude"
            process = subprocess.Popen(args, cwd=str(self.home), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE if stream else log, stderr=subprocess.STDOUT,
                                       creationflags=flags, text=True, encoding="utf-8", errors="replace", env=run_env())
            try:
                process.stdin.write(prompt)
                process.stdin.close()
            except OSError:
                pass
            self.runs[slug] = {"process": process, "log": log_path, "engine": engine, "mode": mode,
                               "started": time.time(), "handle": log}
            if stream:
                threading.Thread(target=pump_stream, args=(process.stdout, log), daemon=True).start()
            threading.Thread(target=self._finish, args=(slug, process, log, mode), daemon=True).start()
        return self.status(slug)

    def _finish(self, slug, process, log, mode):
        process.wait()
        if process.stdout is not None:
            time.sleep(.3)  # để luồng đọc stream-json ghi nốt dòng cuối
        try:
            log.write(f"\n\n# Kết thúc (mã {process.returncode}) · {time.strftime('%H:%M:%S')}\n")
            log.close()
        except OSError:
            pass
        try:
            self.sync(slug)  # AI vừa sửa kỹ năng gốc → chép lại cho Claude Code / Codex / zip
        except (OSError, WorkflowError):
            pass

    def stop(self, slug):
        run = self.runs.get(slug)
        if run and run["process"].poll() is None:
            run["process"].terminate()
        return self.status(slug)

    def status(self, slug):
        run = self.runs.get(slug)
        info = {"running": False, "engine": "", "mode": "", "log": "", "code": None, "started": None}
        if run:
            info.update(running=run["process"].poll() is None, engine=run["engine"], mode=run["mode"],
                        code=run["process"].poll(), started=run["started"], log=self.read_tail(run["log"], 6000))
        status_file = self.home / "output" / "_status" / f"{slug}.json"
        try:
            info["progress"] = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info["progress"] = None
        return info

    def board(self, slug):
        """Tiến độ cho không gian 3D (cùng dạng bảng của workflow động vật): đọc output/_status/<slug>.json."""
        data = self.load(slug)
        progress = self.status(slug).get("progress") or {}
        states = progress.get("agents") if isinstance(progress.get("agents"), dict) else {}
        running = self.running(slug)
        stages = {"tool": "done"}
        for slot in SLOTS:
            skill = data["agents"][slot]["skill"]
            value = str(states.get(skill) or states.get(slot) or "")
            stages[slot] = value if value in ("working", "done", "error") else ("working" if running and slot == "conductor" else "waiting")
        if not progress and not running:
            return []
        percent = progress.get("percent")
        item = {"name": clean(progress.get("item"), 40) or data["name"], "video": clean(progress.get("note"), 120),
                "stages": stages, "studio": {"total": 0, "done": 0, "running": 0, "error": 0, "waiting": 0},
                "percent": int(percent) if isinstance(percent, (int, float)) else
                round(100 * sum(1 for s in SLOTS if stages[s] == "done") / len(SLOTS)), "updated": time.time()}
        return [item]
