"""HanaMedia AI Voice — nhân bản giọng + đọc văn bản bằng F5-TTS chạy trên máy (chỉ máy Super Admin).

- AI Engine KHÔNG đóng gói trong app: tool tạo môi trường Python riêng (data/voice/engine/venv), cài PyTorch (+CUDA nếu
  có card NVIDIA) và gói f5-tts. Một tiến trình worker nạp mô hình một lần rồi nhận từng đoạn văn bản qua stdin/stdout.
- Hồ sơ giọng: data/voice/profiles/<id>/ (ref.wav 24 kHz mono ≤ 12 giây + profile.json). Phải xác nhận có quyền dùng giọng.
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
MAX_REF_SECONDS = 12  # F5-TTS khuyên ≤ 12 s: mẫu càng dài, đọc càng chậm
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
p.add_argument("--device", default=""); p.add_argument("--ffmpeg", default="")
a = p.parse_args()
if a.ffmpeg:
    os.environ["PATH"] = os.path.dirname(a.ffmpeg) + os.pathsep + os.environ.get("PATH", "")
    try:
        from pydub import AudioSegment
        AudioSegment.converter = a.ffmpeg
    except Exception:
        pass
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
        if req.get("op") == "prepare":  # nhận dạng lời mẫu một lần để lưu vào hồ sơ giọng
            from f5_tts.infer.utils_infer import preprocess_ref_audio_text
            _, text = preprocess_ref_audio_text(req["ref_audio"], req.get("ref_text") or "", show_info=quiet)
            send({"id": req["id"], "ok": True, "ref_text": str(text or "")})
            continue
        if req.get("op") == "ping":
            send({"id": req["id"], "ok": True})
            continue
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


def peak_db(path):
    """Mức to nhất (dB) của file âm thanh; -91 nếu câm hoàn toàn / không đọc được."""
    try:
        text = video.run([video.ffmpeg(), "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], 120).stderr or ""
    except Exception:
        return 0.0
    match = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", text)
    if not match:
        return 0.0
    return -91.0 if match.group(1) == "-inf" else float(match.group(1))


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


PAUSE = re.compile(r"\[\s*(?:ngắt|ngat|nghỉ|nghi|dừng|dung|pause)\s*(\d+(?:[.,]\d+)?)\s*(?:s|giây|giay)?\s*\]", re.IGNORECASE)


def build_chunks(text, split="sentence", limit=CHUNK_CHARS):
    """Đoạn để đọc: [{"text", "pause"}]. split="line": mỗi dòng một đoạn (dòng dài vẫn cắt nhỏ); "sentence": theo câu.
    Thẻ [ngắt 1.5s] trong văn bản = khoảng lặng đúng 1,5 giây sau đoạn đứng trước nó."""
    chunks = []
    position = 0
    text = str(text or "").replace("\r", "")
    for match in list(PAUSE.finditer(text)) + [None]:
        segment = text[position:match.start() if match else len(text)]
        position = match.end() if match else len(text)
        if split == "line":
            pieces = [c for line in segment.split("\n") for c in split_text(line, limit)]
        else:
            pieces = split_text(segment, limit)
        chunks += [{"text": piece, "done": False, "pause": None} for piece in pieces]
        if match and chunks:
            chunks[-1]["pause"] = max(0.0, min(10.0, float(match.group(1).replace(",", "."))))
    return chunks


DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
SCALES = ("", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ")


def _three(n, full):
    hundreds, tens, units = n // 100, n % 100 // 10, n % 10
    words = []
    if full or hundreds:
        words += [DIGITS[hundreds], "trăm"]
    if tens == 0:
        if units:
            if full or hundreds:
                words.append("linh")
            words.append(DIGITS[units])
    elif tens == 1:
        words.append("mười")
        if units:
            words.append("lăm" if units == 5 else DIGITS[units])
    else:
        words += [DIGITS[tens], "mươi"]
        if units:
            words.append({1: "mốt", 4: "tư", 5: "lăm"}.get(units, DIGITS[units]))
    return words


def number_vi(n):
    """Đọc số nguyên tiếng Việt: 1005 → "một nghìn không trăm linh năm"; 21 → "hai mươi mốt"."""
    n = int(n)
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + number_vi(-n)
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    words = []
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if group == 0:
            continue
        words += _three(group, full=bool(words))
        if SCALES[index] if index < len(SCALES) else "":
            words.append(SCALES[index])
    return " ".join(words)


def _num(text):
    """Chuỗi số (1.000.000 / 3,5 / 12) → chữ."""
    text = text.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        return number_vi(text.replace(".", ""))
    match = re.fullmatch(r"(\d+)[.,](\d+)", text)
    if match:
        decimals = match.group(2)
        tail = " ".join(DIGITS[int(d)] for d in decimals) if decimals.startswith("0") or len(decimals) > 2 else number_vi(decimals)
        return f"{number_vi(match.group(1))} phẩy {tail}"
    return number_vi(text) if text.isdigit() else text


UNITS = {"km": "ki lô mét", "kg": "ki lô gam", "cm": "xen ti mét", "mm": "mi li mét", "m": "mét", "g": "gam", "ml": "mi li lít",
         "l": "lít", "h": "giờ", "ha": "héc ta", "kw": "ki lô oát", "gb": "gi ga bai", "mb": "mê ga bai"}
ABBREV = [(r"\bTP\.?\s*HCM\b|\bTPHCM\b", "thành phố Hồ Chí Minh"), (r"\bTP\.", "thành phố"), (r"\bHN\b", "Hà Nội"),
          (r"\bVN\b", "Việt Nam"), (r"\bv\.v\.?", "vân vân"), (r"&", " và "), (r"\bkm/h\b", "ki lô mét trên giờ"),
          (r"\bUBND\b", "ủy ban nhân dân"), (r"\bTW\b", "trung ương"), (r"\bĐH\b", "đại học")]


def normalize_vi(text):
    """Chuẩn hóa văn bản tiếng Việt cho TTS: số, ngày tháng, giờ, %, tiền, đơn vị, viết tắt thường gặp → chữ."""
    text = str(text or "")
    for pattern, repl in ABBREV:
        text = re.sub(pattern, repl, text)
    text = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b",
                  lambda m: f"ngày {number_vi(m.group(1))} tháng {number_vi(m.group(2))} năm {number_vi(m.group(3))}", text)
    text = re.sub(r"\b(\d{1,2})/(\d{1,2})\b(?!/)", lambda m: f"ngày {number_vi(m.group(1))} tháng {number_vi(m.group(2))}"
                  if 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12 else m.group(0), text)
    text = re.sub(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)\b", lambda m: f"{number_vi(m.group(1))} giờ" +
                  (f" {number_vi(m.group(2))} phút" if int(m.group(2)) else ""), text)
    number = r"(\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?)"
    text = re.sub(number + r"\s*%", lambda m: f"{_num(m.group(1))} phần trăm", text)
    text = re.sub(number + r"\s*(?:đ|đồng|vnđ|vnd)\b", lambda m: f"{_num(m.group(1))} đồng", text, flags=re.IGNORECASE)
    text = re.sub(r"\$\s*" + number, lambda m: f"{_num(m.group(1))} đô la", text)
    text = re.sub(number + r"\s*°\s*C\b", lambda m: f"{_num(m.group(1))} độ C", text)
    text = re.sub(number + r"\s*(km|kg|cm|mm|ml|ha|kw|gb|mb|m|g|l|h)\b",
                  lambda m: f"{_num(m.group(1))} {UNITS[m.group(2).lower()]}", text, flags=re.IGNORECASE)
    text = re.sub(number, lambda m: _num(m.group(1)), text)
    text = re.sub(r"\b([Nn]gày)\s+ngày\b", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text)


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
        self.worker_lock = threading.Lock()
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
        pip = [str(self.venv_python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir"]  # không giữ bản sao ~2,5 GB trong pip cache
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
        with self.worker_lock:  # trang AI Voice nạp sẵn + hàng đợi có thể gọi cùng lúc
            self._start_worker()

    def warm(self):
        """Nạp mô hình sẵn ở nền (khi mở trang AI Voice) để lúc bấm Tạo đọc được ngay."""
        if not self.installed() or self.install_state.get("running") or (self.worker_alive() and self.worker_info.get("ready")):
            return False
        if self.worker_lock.locked():
            return False

        def run():
            try:
                self.start_worker()
            except VoiceError:
                pass
        threading.Thread(target=run, daemon=True).start()
        return True

    def _start_worker(self):
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
        # F5-TTS (pydub) và bộ nhận dạng lời mẫu (transformers) gọi chương trình tên "ffmpeg": đưa bản ffmpeg có sẵn của tool
        # vào thư mục bin riêng với đúng tên đó rồi đặt lên đầu PATH của engine.
        ffmpeg_bin = self.ensure_ffmpeg()
        if ffmpeg_bin:
            env["PATH"] = str(ffmpeg_bin.parent) + os.pathsep + env.get("PATH", "")
            env["FFMPEG_BINARY"] = env["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg_bin)
            args += ["--ffmpeg", str(ffmpeg_bin)]
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

    def ensure_ffmpeg(self):
        """Bản sao ffmpeg của tool mang đúng tên ffmpeg(.exe) trong engine/bin. None nếu tool không có ffmpeg."""
        try:
            source = Path(video.ffmpeg())
        except Exception:
            return None
        target = self.engine_dir / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        try:
            if not target.is_file() or target.stat().st_size != source.stat().st_size:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if os.name != "nt":
                    target.chmod(0o755)
        except OSError:
            return None
        return target

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

    def request(self, payload, timeout=900, slow="Đọc một đoạn quá lâu, đã dừng."):
        self.start_worker()
        request_id = uuid.uuid4().hex
        payload = dict(payload, id=request_id)
        self.worker.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.worker.stdin.flush()
        deadline = time.time() + timeout
        with self.response_event:
            while request_id not in self.responses:
                if not self.worker_alive():
                    raise VoiceError("AI Engine dừng đột ngột (xem nhật ký engine).")
                if time.time() > deadline:
                    raise VoiceError(slow)
                self.response_event.wait(timeout=2)
            return self.responses.pop(request_id)

    def prepare_ref(self, ref_audio, ref_text=""):
        """Lời mẫu (do Whisper nghe ra nếu trống) — gọi một lần rồi lưu vào hồ sơ giọng."""
        msg = self.request({"op": "prepare", "ref_audio": str(ref_audio), "ref_text": ref_text or ""}, timeout=600,
                           slow="Nhận dạng lời mẫu quá lâu.")
        if not msg.get("ok"):
            raise VoiceError(msg.get("error") or "Không nhận dạng được lời mẫu.")
        return re.sub(r"\s+", " ", str(msg.get("ref_text") or "")).strip()[:1000]

    def ensure_ref_text(self, profile):
        """Hồ sơ chưa có lời mẫu: nhận dạng một lần và lưu, để mỗi đoạn sau không phải chạy Whisper lại."""
        if (profile.get("ref_text") or "").strip():
            return profile["ref_text"]
        folder = self.profiles_dir / profile["id"]
        try:
            text = self.prepare_ref(folder / "ref.wav")
        except (VoiceError, OSError):
            return ""  # để F5-TTS tự nhận dạng như cũ
        if text:
            profile = dict(profile, ref_text=text, ref_text_auto=True)
            (folder / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
        return text

    def synth(self, ref_audio, ref_text, text, out, speed=1.0, timeout=900, nfe=None):
        msg = self.request({"ref_audio": str(ref_audio), "ref_text": ref_text, "text": text, "out": str(out),
                            "speed": speed, "nfe": int(nfe or self.config()["nfe"])}, timeout)
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
                                         "created", "updated", "output", "seconds", "split", "gap", "nfe", "group", "group_title",
                                         "index")} | {"text": " ".join(c["text"] for c in job["chunks"])[:400]} | {"total": len(job["chunks"]), "done": done,
                                                                                         "chars": sum(len(c["text"]) for c in job["chunks"])}

    def jobs(self):
        out = []
        for file in self.jobs_dir.glob("*/job.json"):
            try:
                out.append(json.loads(file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda j: -j.get("created", 0))

    def create_job(self, profile_id, text, title="", fmt="mp3", speed=1.0, split="sentence", gap=SILENCE, nfe=None, extra=None):
        profile = self.profile(profile_id)
        text = str(text or "")
        if len(text) > MAX_TEXT:
            raise VoiceError(f"Văn bản dài quá {MAX_TEXT:,} ký tự.".replace(",", "."))
        chunks = build_chunks(text, "line" if split == "line" else "sentence")
        if not chunks:
            raise VoiceError("Nhập văn bản cần đọc.")
        speed = max(.6, min(1.6, float(speed or 1.0)))
        job_id = uuid.uuid4().hex[:12]
        (self.jobs_dir / job_id).mkdir(parents=True)
        job = {"id": job_id, "title": re.sub(r"\s+", " ", str(title or "")).strip()[:80] or chunks[0]["text"][:40],
               "profile": profile_id, "profile_name": profile["name"], "format": "wav" if fmt == "wav" else "mp3",
               "speed": speed, "split": "line" if split == "line" else "sentence",
               "gap": max(0.0, min(3.0, float(gap if gap is not None else SILENCE))),
               "nfe": max(8, min(64, int(nfe or self.config()["nfe"]))),
               "status": "waiting", "note": "", "error": "", "output": "", "seconds": 0,
               "created": time.time(), "updated": time.time(), "chunks": chunks, **(extra or {})}
        self.save_job(job)
        self.wake.set()
        return self.public(job)

    def create_batch(self, profile_id, text, title="", fmt="mp3", speed=1.0, gap=SILENCE, nfe=None, group=None, start=False):
        """Mỗi dòng một file âm thanh riêng, gom thành một dự án (group). group có sẵn = thêm dòng vào dự án đó."""
        lines = [line.strip() for line in str(text or "").replace("\r", "").split("\n") if line.strip()]
        if not lines:
            raise VoiceError("Nhập văn bản: mỗi dòng sẽ thành một file âm thanh.")
        if len(lines) > 2000:
            raise VoiceError("Tối đa 2000 dòng mỗi lần.")
        existing = [j for j in self.jobs() if group and j.get("group") == group]
        if group and not existing:
            raise VoiceError("Không tìm thấy dự án.")
        group = group or uuid.uuid4().hex[:12]
        project = existing[0].get("group_title") if existing else (re.sub(r"\s+", " ", str(title or "")).strip()[:80] or "Dự án mới")
        first = max((j.get("index", 0) for j in existing), default=0)
        created = []
        for offset, line in enumerate(lines, 1):
            name = re.sub(r"\s+", " ", PAUSE.sub(" ", line)).strip()[:40] or f"Dòng {first + offset}"
            created.append(self.create_job(profile_id, line, name, fmt, speed, "sentence", gap, nfe,
                                           extra={"group": group, "group_title": project, "index": first + offset,
                                                  "created": time.time() + offset * 1e-3,
                                                  **({} if start else {"status": "draft"})}))
        self.wake.set()
        return {"group": group, "title": project, "jobs": created}

    def start_group(self, group, settings=None):
        """Bấm "Tạo giọng nói" trong dự án: mọi dòng Sẵn sàng / Lỗi chạy (dùng cài đặt mới nhất nếu có)."""
        settings = settings or {}
        count = 0
        for job in self.jobs():
            if job.get("group") == group and job["status"] in ("draft", "error"):
                fields = {"status": "waiting", "error": "", "note": ""}
                if settings.get("profile"):
                    profile = self.profile(settings["profile"])
                    fields.update(profile=profile["id"], profile_name=profile["name"])
                if settings.get("speed"):
                    fields["speed"] = max(.6, min(1.6, float(settings["speed"])))
                if settings.get("format") in ("mp3", "wav"):
                    fields["format"] = settings["format"]
                if settings.get("gap") is not None:
                    fields["gap"] = max(0.0, min(3.0, float(settings["gap"])))
                if settings.get("nfe"):
                    fields["nfe"] = max(8, min(64, int(settings["nfe"])))
                self.update_job(job["id"], **fields)
                count += 1
        if not count:
            raise VoiceError("Không có dòng nào cần tạo (đã xong hết hoặc đang chạy).")
        self.wake.set()
        return count

    def edit(self, job_id, text, name=None):
        """Sửa văn bản của một dòng/tác vụ: đọc lại từ đầu."""
        job = self.load_job(job_id)
        if job["status"] == "running":
            raise VoiceError("Tác vụ đang chạy.")
        chunks = build_chunks(text, job.get("split", "sentence"))
        if not chunks:
            raise VoiceError("Văn bản trống.")
        folder = self.jobs_dir / job_id
        for file in folder.glob("*.*"):
            if file.name != "job.json":
                file.unlink(missing_ok=True)
        job.update(chunks=chunks, status="draft" if job.get("group") else "waiting", error="", note="", output="", seconds=0,
                   updated=time.time())
        if name is not None and str(name).strip():
            job["title"] = re.sub(r"\s+", " ", str(name)).strip()[:60]
        self.save_job(job)
        self.wake.set()
        return self.public(job)

    def export_group(self, group):
        """Zip mọi file đã xong của một dự án: 001_ten.mp3, 002_…"""
        import zipfile
        if not re.fullmatch(r"[a-f0-9]{12}", str(group or "")):
            raise VoiceError("Dự án không hợp lệ.")
        jobs = sorted((j for j in self.jobs() if j.get("group") == group and j["status"] == "done" and j.get("output")),
                      key=lambda j: j.get("index", 0))
        if not jobs:
            raise VoiceError("Dự án chưa có file nào xong.")
        target = self.root / "exports"
        target.mkdir(exist_ok=True)
        title = re.sub(r"[^\w\- ]+", "", jobs[0].get("group_title") or "du-an", flags=re.UNICODE).strip()[:50] or "du-an"
        path = target / f"{title}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
            for job in jobs:
                file = self.jobs_dir / job["id"] / job["output"]
                if file.is_file():
                    name = re.sub(r"[^\w\- ]+", "", job["title"], flags=re.UNICODE).strip()[:40] or "dong"
                    z.write(file, f"{job.get('index', 0):03d}_{name}{file.suffix}")
        return path

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
            if not (profile.get("ref_text") or "").strip():
                self.update_job(job_id, note="Đang nhận dạng lời mẫu (chỉ lần đầu)…")
                profile = dict(profile, ref_text=self.ensure_ref_text(profile))
            for index, chunk in enumerate(job["chunks"]):
                if chunk.get("done") and (folder / f"chunk_{index:04d}.wav").is_file():
                    continue
                if self.load_job(job_id)["status"] != "running":
                    return  # người dùng bấm dừng
                self.update_job(job_id, note=f"Đang đọc đoạn {index + 1}/{len(job['chunks'])}")
                out = folder / f"chunk_{index:04d}.wav"
                try:
                    self.synth(ref, profile.get("ref_text", ""), chunk["text"], out, job["speed"], nfe=job.get("nfe"))
                except VoiceError as error:
                    self.update_job(job_id, status="error", error=f"Đoạn {index + 1}: {error}", note="")
                    return
                with self.lock:
                    fresh = self.load_job(job_id)
                    fresh["chunks"][index]["done"] = True
                    fresh["updated"] = time.time()
                    self.save_job(fresh)
            self.update_job(job_id, note="Đang ghép và xuất file…")
            self.last_quiet = []
            output, seconds = self.merge(job_id)
            quiet = self.last_quiet
            self.update_job(job_id, status="done", output=output, seconds=seconds,
                            note=(f"Cảnh báo: đoạn {', '.join(map(str, quiet[:10]))} gần như không có tiếng — thử giọng mẫu "
                                  "rõ hơn / có lời mẫu, rồi bấm Tạo lại." if quiet else ""))
        finally:
            self.current = None

    def merge(self, job_id):
        job = self.load_job(job_id)
        folder = self.jobs_dir / job_id
        cache = {}

        def silence(seconds):
            key = f"{round(seconds, 2):.2f}"
            if key not in cache:
                path = folder / f"silence_{key.replace('.', '_')}.wav"
                video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
                           "-t", key, "-c:a", "pcm_s16le", str(path)], 60)
                cache[key] = path
            return cache[key]
        lines = []
        gap = float(job.get("gap", SILENCE))
        quiet = []
        for index, chunk in enumerate(job["chunks"]):
            # Đưa mọi đoạn về cùng định dạng (PCM 16-bit, 24 kHz, mono) trước khi ghép: F5-TTS / thư viện âm thanh có thể
            # ghi WAV kiểu khác (float, header mở rộng) làm bộ ghép đọc sai sau đoạn đầu.
            source = folder / f"chunk_{index:04d}.wav"
            norm = folder / f"norm_{index:04d}.wav"
            result = video.run([video.ffmpeg(), "-v", "error", "-y", "-i", str(source), "-af", f"aresample={SAMPLE_RATE}",
                                "-ac", "1", "-c:a", "pcm_s16le", str(norm)], 300)
            if result.returncode != 0 or not norm.is_file():
                raise VoiceError(f"Đoạn {index + 1}: file âm thanh hỏng ({(result.stderr or '').strip()[-200:]}).")
            if peak_db(norm) < -45:
                quiet.append(index + 1)
            lines.append(f"file '{norm.as_posix()}'")
            pause = chunk.get("pause")
            seconds = pause if pause is not None else (gap if index < len(job["chunks"]) - 1 else 0)
            if seconds > 0.01:
                lines.append(f"file '{silence(seconds).as_posix()}'")
        listing = folder / "concat.txt"
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        safe = re.sub(r"[^\w\- ]+", "", job["title"], flags=re.UNICODE).strip()[:50] or "voice"
        name = f"{safe}.{job['format']}"
        codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if job["format"] == "mp3" else ["-c:a", "pcm_s16le"]
        result = video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-ar",
                            str(SAMPLE_RATE), "-ac", "1", *codec, str(folder / name)], 1800)
        if result.returncode != 0 or not (folder / name).is_file():
            raise VoiceError("Ghép âm thanh lỗi: " + (result.stderr or "")[-300:])
        for file in folder.glob("norm_*.wav"):
            file.unlink(missing_ok=True)
        self.last_quiet = quiet
        return name, audio_seconds(folder / name)
