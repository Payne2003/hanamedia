"""Tự chạy workflow khi gửi video (máy chủ và máy khách): mỗi video vào hàng đợi, lần lượt
chép vào input/ (dọn dự án cũ) → gọi Claude Code / Codex chạy workflow → đợi xong → video kế tiếp.
Chỉ một lần chạy tại một thời điểm, vì mọi workflow dùng chung thư mục input/."""
import threading
import time
from pathlib import Path


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
            code = self.workflows.status(item["slug"]).get("code")
            self.note(item, code == 0, "Xong." if code == 0 else f"Claude / Codex dừng với mã {code}. Xem nhật ký lần chạy.")
        except Exception as error:  # một video lỗi không chặn các video sau
            self.note(item, False, str(error) or type(error).__name__)
        finally:
            with self.lock:
                self.current = None

    def status(self):
        with self.lock:
            return {"queue": [{"name": q["name"], "slug": q["slug"]} for q in self.queue],
                    "current": dict(self.current) if self.current else None, "history": list(self.history[:8])}
