"""HanaMedia AI Voice — nhân bản giọng + đọc văn bản bằng F5-TTS chạy trên máy (chỉ máy Super Admin).

- AI Engine KHÔNG đóng gói trong app: tool tạo môi trường Python riêng (data/voice/engine/venv), cài PyTorch (+CUDA nếu
  có card NVIDIA) và gói f5-tts. Một tiến trình worker nạp mô hình một lần rồi nhận từng đoạn văn bản qua stdin/stdout.
- Hồ sơ giọng: data/voice/profiles/<id>/ (ref.wav 24 kHz mono ≤ 15 giây + profile.json). Phải xác nhận có quyền dùng giọng.
- Tác vụ: data/voice/jobs/<id>/ (job.json + chunk_0001.wav…). Chờ / Đang chạy / Xong / Lỗi; thử lại chạy tiếp từ đoạn
  hỏng; app tắt giữa chừng thì lần mở sau chạy tiếp.
- Giấy phép: mã nguồn F5-TTS là MIT; checkpoint chính thức F5TTS_v1_Base là CC BY-NC 4.0 (chỉ thử nghiệm, không thương mại).
  Chế độ "commercial" chỉ chạy khi đã chỉ định checkpoint riêng (đã có quyền thương mại).
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import video_tools as video

AUDIO_TYPES = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".webm")
MAX_REF_SECONDS = 15
MAX_TEXT = 200_000           # ~ 60 phút đọc
CHUNK_CHARS = 220
SAMPLE_RATE = 24000
SILENCE = .28                # khoảng lặng giữa các đoạn (giây)
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

WORKER_SCRIPT = r'''# HanaMedia AI Voice worker (chạy trong venv của engine). Giao thức: mỗi dòng JSON vào stdin → một dòng JSON ra.
import argparse, json, os, sys, traceback
proto = os.fdopen(os.dup(1), "w", encoding="utf-8")   # kênh giao thức riêng
sys.stdout = sys.stderr                                # mọi print / tiến trình của thư viện → stderr (ghi log)
def send(obj):
    proto.write(json.dumps(obj, ensure_ascii=False) + "\n"); proto.flush()
p = argparse.ArgumentParser()
p.add_argument("--model", default="F5TTS_v1_Base"); p.add_argument("--ckpt", default=""); p.add_argument("--vocab", default="")
p.add_argument("--device", default="")
a = p.parse_args()
try:
    from f5_tts.api import F5TTS
    kw = {"model": a.model}
    if a.ckpt: kw["ckpt_file"] = a.ckpt
    if a.vocab: kw["vocab_file"] = a.vocab
    if a.device: kw["device"] = a.device
    tts = F5TTS(**kw)
    send({"ready": True, "device": str(getattr(tts, "device", ""))})
except Exception as e:
    traceback.print_exc()
    send({"ready": False, "error": f"{type(e).__name__}: {e}"[:800]}); sys.exit(1)
quiet = lambda *x, **k: None
for line in sys.stdin:
    try:
        req = json.loads(line)
    except ValueError:
        continue
    try:
        tts.infer(ref_file=req["ref_audio"], ref_text=req.get("ref_text") or "", gen_text=req["text"], file_wave=req["out"],
                  speed=float(req.get("speed") or 1.0), nfe_step=int(req.get("nfe") or 32), remove_silence=False,
                  seed=req.get("seed"), show_info=quiet)
        send({"id": req["id"], "ok": True})
    except Exception as e:
        traceback.print_exc()
        send({"id": req.get("id"), "ok": False, "error": f"{type(e).__name__}: {e}"[:800]})
'''


class VoiceError(ValueError):
    pass


def audio_seconds(path):
    """Độ dài file âm thanh (giây) đọc từ thông tin ffmpeg; 0 nếu không đọc được."""
    try:
        text = video.run([video.ffmpeg(), "-hide_banner", "-i", str(path)], 60).stderr or ""
    except Exception:
        return 0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    return round(int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3)), 1) if match else 0


def split_text(text, limit=CHUNK_CHARS):
    """Cắt văn bản thành đoạn ≤ limit ký tự, ưu tiên ranh giới câu → dấu phẩy → khoảng trắng."""
    text = re.sub(r"[ \t]+", " ", str(text or "").replace("\r", "")).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…。])\s+|\n+", text) if s.strip()]
    pieces = []
    for sentence in sentences:
        while len(sentence) > limit:
            cut = max(sentence.rfind(", ", 0, limit), sentence.rfind("; ", 0, limit), sentence.rfind(": ", 0, limit))
            if cut < limit // 3:
                cut = sentence.rfind(" ", 0, limit)
            if cut < limit // 3:
                cut = limit
            pieces.append(sentence[:cut + 1].strip())
            sentence = sentence[cut + 1:].strip()
        if sentence:
            pieces.append(sentence)
    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


class VoiceStudio:
    def __init__(self, data_dir, runner=subprocess.run, popen=subprocess.Popen, autostart=True):
        self.root = Path(data_dir) / "voice"
        self.engine_dir = self.root / "engine"
        self.profiles_dir = self.root / "profiles"
        self.jobs_dir = self.root / "jobs"
        for folder in (self.engine_dir, self.profiles_dir, self.jobs_dir):
            folder.mkdir(parents=True, exist_ok=True)
        self.run_cmd, self.popen = runner, popen
        self.lock = threading.RLock()
        self.install_state = {"running": False, "phase": "", "error": ""}
        self.worker = None
        self.worker_info = {"ready": False, "device": "", "error": ""}
        self.responses = {}
        self.response_event = threading.Condition()
        self.wake = threading.Event()
        self.current = None
        for job in self.jobs():  # app tắt giữa chừng: chạy tiếp
            if job["status"] == "running":
                self.update_job(job["id"], status="waiting", note="Tiếp tục sau khi mở lại app")
        if autostart:
            threading.Thread(target=self.loop, daemon=True).start()

    # ---------- cấu hình engine ----------
    @property
    def config_file(self):
        return self.engine_dir / "config.json"

    def config(self):
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"model": str(data.get("model") or "F5TTS_v1_Base")[:60], "ckpt": str(data.get("ckpt") or "")[:500],
                "vocab": str(data.get("vocab") or "")[:500], "device": str(data.get("device") or "")[:20],
                "mode": "commercial" if data.get("mode") == "commercial" else "development",
                "nfe": int(data.get("nfe") or 32)}

    def save_config(self, data):
        config = self.config()
        for key in ("model", "ckpt", "vocab", "device"):
            if key in data:
                config[key] = str(data.get(key) or "").strip().strip('"')[:500]
        if data.get("mode") in ("development", "commercial"):
            config["mode"] = data["mode"]
        if "nfe" in data:
            config["nfe"] = max(8, min(64, int(data.get("nfe") or 32)))
        for key in ("ckpt", "vocab"):
            if config[key] and not Path(config[key]).is_file():
                raise VoiceError(f"Không thấy file {'checkpoint' if key == 'ckpt' else 'vocab'}: {config[key]}")
        if config["mode"] == "commercial" and not config["ckpt"]:
            raise VoiceError("Chế độ thương mại cần chỉ định checkpoint riêng đã có quyền thương mại "
                             "(checkpoint chính thức F5TTS_v1_Base là CC BY-NC, không dùng thương mại).")
        self.config_file.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
        self.stop_worker()  # lần tạo tới nạp lại mô hình theo cấu hình mới
        return config

    # ---------- cài engine ----------
    @property
    def venv_python(self):
        sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
        return self.engine_dir / "venv" / sub[0] / sub[1]

    def installed(self):
        return self.venv_python.is_file() and (self.engine_dir / "installed.json").is_file()

    def find_python(self):
        """Python 3.10–3.12 của hệ thống để tạo venv (PyTorch chưa hỗ trợ mọi bản mới nhất)."""
        if os.name == "nt":
            candidates = [["py", "-3.11"], ["py", "-3.10"], ["py", "-3.12"], ["python"]]
        else:
            candidates = [["python3.11"], ["python3.10"], ["python3.12"], ["python3"]]
        for cmd in candidates:
            if not shutil.which(cmd[0]):
                continue
            try:
                out = self.run_cmd(cmd + ["-c", "import sys;print('%d.%d' % sys.version_info[:2])"], capture_output=True,
                                   text=True, timeout=20, creationflags=FLAGS)
            except (OSError, subprocess.SubprocessError):
                continue
            version = (out.stdout or "").strip()
            if out.returncode == 0 and re.fullmatch(r"3\.(1[0-2])", version):
                return cmd, version
        return None, ""

    def gpu(self):
        if not shutil.which("nvidia-smi"):
            return ""
        try:
            out = self.run_cmd(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True,
                               text=True, timeout=15, creationflags=FLAGS)
            return (out.stdout or "").strip().splitlines()[0][:120] if out.returncode == 0 and out.stdout.strip() else ""
        except (OSError, subprocess.SubprocessError, IndexError):
            return ""

    def install(self):
        with self.lock:
            if self.install_state["running"]:
                raise VoiceError("Đang cài AI Engine.")
            python, version = self.find_python()
            if not python:
                raise VoiceError("Chưa có Python 3.10–3.12 trên máy. Cài Python 3.11 từ python.org (tick “Add python.exe to "
                                 "PATH”) rồi bấm cài lại.")
            self.install_state = {"running": True, "phase": "Chuẩn bị", "error": ""}
        threading.Thread(target=self._install, args=(python, version), daemon=True).start()
        return self.status()

    def _install(self, python, version):
        log_path = self.engine_dir / "install.log"
        gpu = self.gpu()
        venv = self.engine_dir / "venv"
        steps = []
        if not self.venv_python.is_file():
            steps.append(("Tạo môi trường Python riêng", python + ["-m", "venv", str(venv)]))
        pip = [str(self.venv_python), "-m", "pip", "install", "--disable-pip-version-check"]
        steps.append(("Cập nhật pip", pip + ["--upgrade", "pip", "setuptools", "wheel"]))
        if gpu:
            steps.append(("Cài PyTorch (CUDA, ~2,5 GB)", pip + ["torch", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu124"]))
        else:
            steps.append(("Cài PyTorch (CPU — máy không có card NVIDIA, sẽ chậm)", pip + ["torch", "torchaudio"]))
        steps.append(("Cài F5-TTS", pip + ["f5-tts"]))
        steps.append(("Kiểm tra", [str(self.venv_python), "-c", "import torch, f5_tts; print('torch', torch.__version__, "
                                                                 "'cuda', torch.cuda.is_available())"]))
        try:
            with open(log_path, "w", encoding="utf-8", errors="replace") as log:
                log.write(f"Python hệ thống {version} · GPU: {gpu or 'không có'}\n")
                for label, cmd in steps:
                    self.install_state["phase"] = label
                    log.write(f"\n=== {label}\n$ {' '.join(cmd)}\n")
                    log.flush()
                    proc = self.popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(self.engine_dir), creationflags=FLAGS)
                    if proc.wait() != 0:
                        raise VoiceError(f"Bước “{label}” lỗi (mã {proc.returncode}). Xem nhật ký cài đặt.")
            (self.engine_dir / "installed.json").write_text(json.dumps({"at": time.time(), "gpu": gpu, "python": version}),
                                                           encoding="utf-8")
            self.install_state = {"running": False, "phase": "Đã cài xong", "error": ""}
        except (OSError, VoiceError) as error:
            self.install_state = {"running": False, "phase": "Lỗi", "error": str(error)[:400]}

    def status(self):
        def tail(path, size=5000):
            try:
                return Path(path).read_text(encoding="utf-8", errors="replace")[-size:]
            except OSError:
                return ""
        python, version = (None, "") if self.install_state["running"] else self.find_python()
        return {"installed": self.installed(), "install": dict(self.install_state), "python": version, "gpu": self.gpu(),
                "config": self.config(), "worker": dict(self.worker_info, running=self.worker_alive()),
                "install_log": tail(self.engine_dir / "install.log"), "worker_log": tail(self.engine_dir / "worker.log", 3000)}

    # ---------- worker ----------
    def worker_alive(self):
        return bool(self.worker and self.worker.poll() is None)

    def start_worker(self):
        if self.worker_alive() and self.worker_info.get("ready"):
            return
        if not self.installed():
            raise VoiceError("Chưa cài AI Engine. Bấm “Cài AI Engine” trước.")
        config = self.config()
        if config["mode"] == "commercial" and not config["ckpt"]:
            raise VoiceError("Chế độ thương mại chưa có checkpoint riêng.")
        script = self.engine_dir / "worker.py"
        script.write_text(WORKER_SCRIPT, encoding="utf-8")
        args = [str(self.venv_python), str(script), "--model", config["model"]]
        for key in ("ckpt", "vocab", "device"):
            if config[key]:
                args += [f"--{key}", config[key]]
        log = open(self.engine_dir / "worker.log", "a", encoding="utf-8", errors="replace")
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} nạp mô hình {config['model']} {config['ckpt']}\n")
        log.flush()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.worker = self.popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8",
                                 errors="replace", cwd=str(self.engine_dir), env=env, creationflags=FLAGS)
        self.worker_info = {"ready": False, "device": "", "error": "", "loading": True}
        threading.Thread(target=self._read_worker, args=(self.worker,), daemon=True).start()
        deadline = time.time() + 1800  # lần đầu tải checkpoint (~1,3 GB) có thể lâu
        with self.response_event:
            while not self.worker_info.get("ready") and not self.worker_info.get("error") and self.worker.poll() is None:
                self.response_event.wait(timeout=2)
                if time.time() > deadline:
                    break
        if not self.worker_info.get("ready"):
            error = self.worker_info.get("error") or "AI Engine không khởi động được (xem nhật ký engine)."
            self.stop_worker()
            raise VoiceError(error)

    def _read_worker(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self.response_event:
                if "ready" in msg:
                    self.worker_info.update(ready=bool(msg["ready"]), device=msg.get("device", ""), error=msg.get("error", ""),
                                            loading=False)
                elif msg.get("id"):
                    self.responses[msg["id"]] = msg
                self.response_event.notify_all()
        with self.response_event:
            self.worker_info.update(ready=False, loading=False)
            self.response_event.notify_all()

    def stop_worker(self):
        proc, self.worker = self.worker, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(5)
            except (OSError, subprocess.SubprocessError):
                proc.kill()
        self.worker_info = {"ready": False, "device": "", "error": ""}

    def synth(self, ref_audio, ref_text, text, out, speed=1.0, timeout=900):
        self.start_worker()
        request_id = uuid.uuid4().hex
        payload = {"id": request_id, "ref_audio": str(ref_audio), "ref_text": ref_text, "text": text, "out": str(out),
                   "speed": speed, "nfe": self.config()["nfe"]}
        self.worker.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.worker.stdin.flush()
        deadline = time.time() + timeout
        with self.response_event:
            while request_id not in self.responses:
                if not self.worker_alive():
                    raise VoiceError("AI Engine dừng đột ngột (xem nhật ký engine).")
                if time.time() > deadline:
                    raise VoiceError("Đọc một đoạn quá lâu, đã dừng.")
                self.response_event.wait(timeout=2)
            msg = self.responses.pop(request_id)
        if not msg.get("ok") or not Path(out).is_file():
            raise VoiceError(msg.get("error") or "Không tạo được âm thanh cho đoạn này.")

    # ---------- hồ sơ giọng ----------
    def profiles(self):
        out = []
        for file in sorted(self.profiles_dir.glob("*/profile.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                out.append(data)
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda p: -p.get("created", 0))

    def profile(self, profile_id):
        if not re.fullmatch(r"[a-f0-9]{12}", str(profile_id or "")):
            raise VoiceError("Giọng không hợp lệ.")
        file = self.profiles_dir / profile_id / "profile.json"
        if not file.is_file():
            raise VoiceError("Không tìm thấy giọng.")
        return json.loads(file.read_text(encoding="utf-8"))

    def add_profile(self, raw, filename, name, ref_text, consent):
        if not consent:
            raise VoiceError("Cần xác nhận bạn có quyền dùng giọng mẫu này (giọng của chính bạn hoặc đã được đồng ý).")
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:60]
        if not name:
            raise VoiceError("Đặt tên cho giọng.")
        suffix = Path(str(filename or "")).suffix.lower()
        if suffix not in AUDIO_TYPES:
            raise VoiceError("File giọng mẫu phải là " + ", ".join(AUDIO_TYPES) + ".")
        if not raw or len(raw) > 60 * 1024 * 1024:
            raise VoiceError("File giọng mẫu trống hoặc lớn hơn 60 MB.")
        profile_id = uuid.uuid4().hex[:12]
        folder = self.profiles_dir / profile_id
        folder.mkdir(parents=True)
        source = folder / f"source{suffix}"
        source.write_bytes(raw)
        ref = folder / "ref.wav"
        result = video.run([video.ffmpeg(), "-v", "error", "-y", "-i", str(source), "-t", str(MAX_REF_SECONDS), "-ac", "1",
                            "-ar", str(SAMPLE_RATE), "-af", "loudnorm=I=-18:TP=-2,silenceremove=start_periods=1:start_threshold=-45dB",
                            str(ref)], 120)
        if result.returncode != 0 or not ref.is_file():
            shutil.rmtree(folder, ignore_errors=True)
            raise VoiceError("Không đọc được file âm thanh này. Thử file WAV/MP3 khác.")
        seconds = audio_seconds(ref)
        data = {"id": profile_id, "name": name, "ref_text": re.sub(r"\s+", " ", str(ref_text or "")).strip()[:1000],
                "seconds": seconds, "consent": True, "created": time.time()}
        (folder / "profile.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return data

    def delete_profile(self, profile_id):
        self.profile(profile_id)
        shutil.rmtree(self.profiles_dir / profile_id, ignore_errors=True)

    # ---------- tác vụ ----------
    def job_file(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{12}", str(job_id or "")):
            raise VoiceError("Tác vụ không hợp lệ.")
        return self.jobs_dir / job_id / "job.json"

    def load_job(self, job_id):
        try:
            return json.loads(self.job_file(job_id).read_text(encoding="utf-8"))
        except OSError:
            raise VoiceError("Không tìm thấy tác vụ.") from None

    def save_job(self, job):
        file = self.job_file(job["id"])
        temp = file.with_suffix(".tmp")
        temp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, file)

    def update_job(self, job_id, **fields):
        with self.lock:
            job = self.load_job(job_id)
            job.update(fields, updated=time.time())
            self.save_job(job)
            return job

    def public(self, job):
        done = sum(1 for c in job["chunks"] if c.get("done"))
        return {k: job.get(k) for k in ("id", "title", "profile", "profile_name", "status", "note", "error", "format", "speed",
                                         "created", "updated", "output", "seconds")} | {"total": len(job["chunks"]), "done": done,
                                                                                         "chars": sum(len(c["text"]) for c in job["chunks"])}

    def jobs(self):
        out = []
        for file in self.jobs_dir.glob("*/job.json"):
            try:
                out.append(json.loads(file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda j: -j.get("created", 0))

    def create_job(self, profile_id, text, title="", fmt="mp3", speed=1.0):
        profile = self.profile(profile_id)
        text = str(text or "")
        if len(text) > MAX_TEXT:
            raise VoiceError(f"Văn bản dài quá {MAX_TEXT:,} ký tự.".replace(",", "."))
        chunks = split_text(text)
        if not chunks:
            raise VoiceError("Nhập văn bản cần đọc.")
        speed = max(.6, min(1.6, float(speed or 1.0)))
        job_id = uuid.uuid4().hex[:12]
        (self.jobs_dir / job_id).mkdir(parents=True)
        job = {"id": job_id, "title": re.sub(r"\s+", " ", str(title or "")).strip()[:80] or chunks[0][:40],
               "profile": profile_id, "profile_name": profile["name"], "format": "wav" if fmt == "wav" else "mp3",
               "speed": speed, "status": "waiting", "note": "", "error": "", "output": "", "seconds": 0,
               "created": time.time(), "updated": time.time(), "chunks": [{"text": c, "done": False} for c in chunks]}
        self.save_job(job)
        self.wake.set()
        return self.public(job)

    def retry(self, job_id):
        job = self.load_job(job_id)
        if job["status"] == "running":
            raise VoiceError("Tác vụ đang chạy.")
        job = self.update_job(job_id, status="waiting", error="", note="Chạy tiếp từ đoạn chưa xong")
        self.wake.set()
        return self.public(job)

    def cancel(self, job_id):
        job = self.load_job(job_id)
        if job["status"] in ("waiting", "running"):
            job = self.update_job(job_id, status="error", error="Đã dừng theo yêu cầu.", note="")
        return self.public(job)

    def delete_job(self, job_id):
        job = self.load_job(job_id)
        if job["status"] == "running":
            raise VoiceError("Tác vụ đang chạy; dừng trước khi xóa.")
        shutil.rmtree(self.jobs_dir / job_id, ignore_errors=True)

    def output_file(self, job_id):
        job = self.load_job(job_id)
        path = self.jobs_dir / job_id / str(job.get("output") or "")
        if not job.get("output") or not path.is_file():
            raise VoiceError("Chưa có file âm thanh.")
        return path

    # ---------- vòng chạy tác vụ ----------
    def loop(self):
        while True:
            self.wake.wait(5)
            self.wake.clear()
            waiting = [j for j in self.jobs() if j["status"] == "waiting"]
            if not waiting or not self.installed():
                continue
            job = sorted(waiting, key=lambda j: j["created"])[0]
            try:
                self.process(job["id"])
            except Exception as error:  # không để vòng chạy chết
                try:
                    self.update_job(job["id"], status="error", error=str(error)[:400], note="")
                except VoiceError:
                    pass
            self.wake.set()

    def process(self, job_id):
        job = self.update_job(job_id, status="running", error="", note="Đang nạp AI Engine…")
        profile = self.profile(job["profile"])
        ref = self.profiles_dir / profile["id"] / "ref.wav"
        folder = self.jobs_dir / job_id
        self.current = job_id
        try:
            for index, chunk in enumerate(job["chunks"]):
                if chunk.get("done") and (folder / f"chunk_{index:04d}.wav").is_file():
                    continue
                if self.load_job(job_id)["status"] != "running":
                    return  # người dùng bấm dừng
                self.update_job(job_id, note=f"Đang đọc đoạn {index + 1}/{len(job['chunks'])}")
                out = folder / f"chunk_{index:04d}.wav"
                try:
                    self.synth(ref, profile.get("ref_text", ""), chunk["text"], out, job["speed"])
                except VoiceError as error:
                    self.update_job(job_id, status="error", error=f"Đoạn {index + 1}: {error}", note="")
                    return
                with self.lock:
                    fresh = self.load_job(job_id)
                    fresh["chunks"][index]["done"] = True
                    fresh["updated"] = time.time()
                    self.save_job(fresh)
            self.update_job(job_id, note="Đang ghép và xuất file…")
            output, seconds = self.merge(job_id)
            self.update_job(job_id, status="done", output=output, seconds=seconds, note="")
        finally:
            self.current = None

    def merge(self, job_id):
        job = self.load_job(job_id)
        folder = self.jobs_dir / job_id
        silence = folder / "silence.wav"
        video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono", "-t", str(SILENCE),
                   str(silence)], 60)
        lines = []
        for index in range(len(job["chunks"])):
            lines.append(f"file '{(folder / f'chunk_{index:04d}.wav').as_posix()}'")
            if index < len(job["chunks"]) - 1:
                lines.append(f"file '{silence.as_posix()}'")
        listing = folder / "concat.txt"
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        safe = re.sub(r"[^\w\- ]+", "", job["title"], flags=re.UNICODE).strip()[:50] or "voice"
        name = f"{safe}.{job['format']}"
        codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if job["format"] == "mp3" else ["-c:a", "pcm_s16le"]
        result = video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-ar",
                            str(SAMPLE_RATE), "-ac", "1", *codec, str(folder / name)], 1800)
        if result.returncode != 0 or not (folder / name).is_file():
            raise VoiceError("Ghép âm thanh lỗi: " + (result.stderr or "")[-300:])
        return name, audio_seconds(folder / name)
