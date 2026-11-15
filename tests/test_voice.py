import json
import tempfile
import time
import unittest
from pathlib import Path

from runner import video_tools as video
from runner.voice import VoiceError, VoiceStudio, split_text


def tone(path, seconds=1.0, freq=440):
    video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
               "-ar", "24000", "-ac", "1", str(path)], 60)


class FakeStudio(VoiceStudio):
    """Engine giả: mỗi đoạn tạo một âm 0,4 giây; đoạn có chữ LỖI thì báo lỗi (một lần)."""
    failed_once = False

    def __init__(self, data_dir):
        super().__init__(data_dir, autostart=False)

    def installed(self):
        return True

    def synth(self, ref_audio, ref_text, text, out, speed=1.0, timeout=900):
        assert Path(ref_audio).is_file()
        if "LỖI" in text and not FakeStudio.failed_once:
            FakeStudio.failed_once = True
            raise VoiceError("CUDA out of memory")
        tone(out, .4)


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        try:
            video.ffmpeg()
        except Exception:
            self.skipTest("không có ffmpeg")

    def tearDown(self):
        self.temp.cleanup()

    def test_split_text_keeps_sentences_under_limit(self):
        text = "Câu một ngắn. " * 30 + "Một câu rất dài, " * 40 + "kết thúc.\nDòng mới!"
        chunks = split_text(text, 220)
        self.assertTrue(all(len(c) <= 221 for c in chunks))
        self.assertEqual(" ".join(chunks).replace(" ", ""), text.replace("\n", "").replace(" ", ""))
        self.assertEqual(split_text("   "), [])

    def test_profile_job_retry_and_merge(self):
        studio = FakeStudio(self.data)
        sample = self.data / "mau.mp3"
        tone(sample, 20)
        with self.assertRaises(VoiceError):
            studio.add_profile(sample.read_bytes(), "mau.mp3", "Giọng A", "", consent=False)  # phải xác nhận quyền
        profile = studio.add_profile(sample.read_bytes(), "mau.mp3", "Giọng A", "Xin chào", consent=True)
        self.assertLessEqual(profile["seconds"], 15.1)  # cắt còn tối đa 15 giây
        self.assertEqual(studio.profiles()[0]["name"], "Giọng A")
        job = studio.create_job(profile["id"], "Câu một. Câu hai có LỖI. Câu ba.", "Tập 1", "mp3", 1.0)
        studio.process(job["id"])
        failed = studio.load_job(job["id"])
        self.assertEqual(failed["status"], "error")
        self.assertIn("CUDA", failed["error"])
        studio.retry(job["id"])
        studio.process(job["id"])  # chạy tiếp từ đoạn hỏng
        done = studio.public(studio.load_job(job["id"]))
        self.assertEqual((done["status"], done["done"], done["total"]), ("done", 1, 1))  # 3 câu ngắn gộp thành 1 đoạn
        out = studio.output_file(job["id"])
        self.assertEqual(out.suffix, ".mp3")
        self.assertGreater(out.stat().st_size, 1000)

    def test_long_text_many_chunks_merge_wav(self):
        studio = FakeStudio(self.data)
        sample = self.data / "mau.wav"
        tone(sample, 6)
        profile = studio.add_profile(sample.read_bytes(), "mau.wav", "B", "", consent=True)
        text = " ".join(f"Đây là câu số {i} trong kịch bản dài để thử ghép nhiều đoạn." for i in range(30))
        job = studio.create_job(profile["id"], text, "Dài", "wav", 1.1)
        self.assertGreater(job["total"], 5)
        studio.process(job["id"])
        info = studio.public(studio.load_job(job["id"]))
        self.assertEqual(info["status"], "done")
        self.assertGreater(info["seconds"], job["total"] * .4)  # đủ các đoạn + khoảng lặng

    def test_restart_resumes_running_jobs_and_config_rules(self):
        studio = FakeStudio(self.data)
        sample = self.data / "mau.wav"
        tone(sample, 5)
        profile = studio.add_profile(sample.read_bytes(), "mau.wav", "C", "", consent=True)
        job = studio.create_job(profile["id"], "Một câu.", "", "mp3")
        studio.update_job(job["id"], status="running")
        again = FakeStudio(self.data)
        self.assertEqual(again.load_job(job["id"])["status"], "waiting")  # mở lại app: tác vụ đang chạy dở được chạy tiếp
        with self.assertRaises(VoiceError):
            studio.save_config({"mode": "commercial"})  # thương mại phải có checkpoint riêng
        with self.assertRaises(VoiceError):
            studio.save_config({"ckpt": str(self.data / "khong-co.pt")})
        self.assertEqual(studio.save_config({"nfe": 16})["nfe"], 16)
        with self.assertRaises(VoiceError):
            studio.create_job(profile["id"], "   ")


if __name__ == "__main__":
    unittest.main()


class WorkerProtocolTests(unittest.TestCase):
    """Chạy đúng worker.py thật với một gói f5_tts giả (F5TTS.infer ghi file âm thanh) — kiểm tra giao thức stdin/stdout."""

    def test_worker_roundtrip(self):
        import sys
        try:
            video.ffmpeg()
        except Exception:
            self.skipTest("không có ffmpeg")
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            pkg = temp / "fake" / "f5_tts"
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "api.py").write_text(
                "import subprocess, sys\n"
                f"FFMPEG = {video.ffmpeg()!r}\n"
                "class F5TTS:\n"
                "    def __init__(self, model='F5TTS_v1_Base', **kw):\n"
                "        print('đang nạp mô hình', model)  # thư viện in ra stdout: không được làm hỏng giao thức\n"
                "        self.device = 'cpu'\n"
                "    def infer(self, ref_file, ref_text, gen_text, file_wave=None, speed=1.0, nfe_step=32, **kw):\n"
                "        if 'HỎNG' in gen_text: raise RuntimeError('lỗi giả')\n"
                "        subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=0.3', file_wave], check=True)\n"
                "        return None, 24000, None\n", encoding="utf-8")
            studio = VoiceStudio(temp / "data", autostart=False)
            studio.venv_python.parent.mkdir(parents=True)
            (studio.engine_dir / "installed.json").write_text("{}", encoding="utf-8")
            import os
            launcher = studio.venv_python
            if os.name == "nt":
                self.skipTest("bỏ qua trên Windows")
            launcher.write_text(f"#!/bin/sh\nPYTHONPATH={temp / 'fake'} exec {sys.executable} \"$@\"\n", encoding="utf-8")
            launcher.chmod(0o755)
            ref = temp / "ref.wav"
            tone(ref, 2)
            out = temp / "out.wav"
            studio.synth(ref, "", "Xin chào", out)
            self.assertTrue(out.is_file())
            self.assertTrue(studio.worker_info["ready"])
            with self.assertRaises(VoiceError) as caught:
                studio.synth(ref, "", "đoạn HỎNG", temp / "x.wav")
            self.assertIn("lỗi giả", str(caught.exception))
            studio.synth(ref, "", "Câu tiếp theo", temp / "y.wav")  # worker vẫn sống sau lỗi
            self.assertTrue((temp / "y.wav").is_file())
            studio.stop_worker()
            self.assertIn("đang nạp mô hình", (studio.engine_dir / "worker.log").read_text(encoding="utf-8"))
