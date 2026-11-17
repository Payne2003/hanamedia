"""Tự chạy workflow khi gửi video (máy chủ và máy khách): mỗi video vào hàng đợi, lần lượt
chép vào input/ (dọn dự án cũ) → gọi Claude Code / Codex chạy workflow → đợi xong → video kế tiếp.
Chỉ một lần chạy tại một thời điểm, vì mọi workflow dùng chung thư mục input/."""
import threading
import time
from pathlib import Path


LOGIN_WORDS = ("login", "log in", "logged", "api key", "authenticat", "unauthorized", "401", "credential", "sign in")


def explain(engine, code, log):
    """Lý do dừng đọc được từ cuối nhật ký (khách không xem được khung nhật ký của Super Admin)."""
    lines = [line.strip() for line in log.splitlines() if line.strip() and not line.startswith("# ")]
    tail = " · ".join(lines[-3:])[-260:]
    name = "Claude Code" if engine == "claude" else "Codex"
    if any(word in tail.lower() for word in LOGIN_WORDS):
        return (f"{name} chưa đăng nhập trên máy này (đăng nhập app Claude / ChatGPT trên web hay app desktop không tính) — "
                f"bấm Kiểm tra máy → Đăng nhập cạnh {name}. Chi tiết: {tail}")
    return f"{name} dừng với mã {code}: {tail or 'không có thông báo'}"


class AutoRunner:
    def __init__(self, workflows, prepare, engine_pref=lambda: "auto", pick=None, poll=2.0):
        self.workflows = workflows
        self.prepare = prepare          # prepare(video) -> kết quả bridge.send (chép video, điểm cắt, gói ảnh)
        self.engine_pref = engine_pref  # "auto" | "claude" | "codex"
        if pick is None:
            from .agent_cli import pick as pick_engine
            pick = pick_engine
        self.pick = pick
        self.poll = poll
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.queue = []
        self.current = None
        self.history = []
        self.thread = None

    def busy(self):
        return any(self.workflows.running(slug) for slug in list(self.workflows.runs))

    def engine(self):
        return self.pick(self.engine_pref() or "auto")

    def enqueue(self, video, slug="wildlife"):
        video = str(video)
        if not Path(video).is_file():
            raise ValueError("Không thấy file video đã chọn.")
        with self.lock:
            item = {"video": video, "name": Path(video).name, "slug": slug, "added": time.time()}
            self.queue.append(item)
            position = len(self.queue) + (1 if self.current or self.busy() else 0)
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self.loop, daemon=True)
                self.thread.start()
        self.wake.set()
        return position

    def remove(self, index):
        with self.lock:
            if 0 <= index < len(self.queue):
                self.queue.pop(index)

    def note(self, item, ok, message):
        self.history.insert(0, {"name": item["name"], "slug": item["slug"], "ok": ok, "message": message[:300],
                                "time": time.time()})
        del self.history[20:]

    def loop(self):
        while True:
            with self.lock:
                item = self.queue.pop(0) if self.queue and not self.busy() else None
                if item:
                    self.current = dict(item, phase="prepare", started=time.time())
            if not item:
                if not self.queue:
                    with self.lock:
                        if not self.queue:
                            self.thread = None
                            return
                self.wake.wait(self.poll)
                self.wake.clear()
                continue
            self.run_one(item)

    def run_one(self, item):
        try:
            engine = self.engine()
            if not engine:
                raise ValueError("Máy chưa có Claude Code hay Codex. Mở Cài đặt workflow → Kiểm tra máy để cài.")
            sent = self.prepare(item["video"])
            self.current.update(phase="running", engine=engine, project=(sent or {}).get("name", ""))
            self.workflows.run(item["slug"], engine, "run")
            while self.workflows.running(item["slug"]):
                time.sleep(self.poll)
            info = self.workflows.status(item["slug"])
            code = info.get("code")
            self.note(item, code == 0, "Xong." if code == 0 else explain(engine, code, info.get("log") or ""))
        except Exception as error:  # một video lỗi không chặn các video sau
            self.note(item, False, str(error) or type(error).__name__)
        finally:
            with self.lock:
                self.current = None

    def status(self):
        with self.lock:
            return {"queue": [{"name": q["name"], "slug": q["slug"]} for q in self.queue],
                    "current": dict(self.current) if self.current else None, "history": list(self.history[:8])}
