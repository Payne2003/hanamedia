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

    prepared = 0

    def prepare_ref(self, ref_audio, ref_text=""):
        FakeStudio.prepared += 1
        return "lời mẫu"

    def synth(self, ref_audio, ref_text, text, out, speed=1.0, timeout=900, nfe=None):
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
                "        import shutil\n"
                "        if 'engine' not in (shutil.which('ffmpeg') or ''): raise ValueError('ffmpeg was not found')  # phải là bản của tool\n"
                "        if 'HỎNG' in gen_text: raise RuntimeError('lỗi giả')\n"
                "        subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=0.3', file_wave], check=True)\n"
                "        return None, 24000, None\n", encoding="utf-8")
            (pkg / "infer").mkdir()
            (pkg / "infer" / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "infer" / "utils_infer.py").write_text(
                "CALLS = []\n"
                "def preprocess_ref_audio_text(ref_audio, ref_text, show_info=print):\n"
                "    return ref_audio, (ref_text or 'xin chào các bạn') + '. '\n", encoding="utf-8")
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
            self.assertEqual(studio.prepare_ref(ref), "xin chào các bạn.")
            # hồ sơ chưa có lời mẫu: nhận dạng một lần rồi lưu vào profile.json
            sample = temp / "s.wav"
            tone(sample, 3)
            profile = studio.add_profile(sample.read_bytes(), "s.wav", "P", "", consent=True)
            self.assertEqual(studio.ensure_ref_text(profile), "xin chào các bạn.")
            self.assertEqual(studio.profile(profile["id"])["ref_text"], "xin chào các bạn.")
            self.assertFalse(studio.warm())  # mô hình đã nạp sẵn: không nạp lại
            studio.stop_worker()
            self.assertIn("đang nạp mô hình", (studio.engine_dir / "worker.log").read_text(encoding="utf-8"))


class VietnameseTextTests(unittest.TestCase):
    def test_numbers_and_normalize(self):
        from runner.voice import normalize_vi, number_vi
        cases = {21: "hai mươi mốt", 105: "một trăm linh năm", 1015: "một nghìn không trăm mười lăm", 24: "hai mươi tư",
                 15: "mười lăm", 1250000: "một triệu hai trăm năm mươi nghìn"}
        for n, words in cases.items():
            self.assertEqual(number_vi(n), words)
        text = normalize_vi("Ngày 15/11/2026 lúc 9:30, giá 1.250.000đ giảm 12,5% tại TP.HCM, 32°C, 45km.")
        self.assertIn("Ngày mười lăm tháng mười một năm hai nghìn không trăm hai mươi sáu", text)
        self.assertIn("chín giờ ba mươi phút", text)
        self.assertIn("một triệu hai trăm năm mươi nghìn đồng", text)
        self.assertIn("mười hai phẩy năm phần trăm", text)
        self.assertIn("thành phố Hồ Chí Minh", text)
        self.assertIn("bốn mươi lăm ki lô mét", text)
        self.assertNotRegex(text, r"\d")

    def test_pause_tags_and_line_mode(self):
        from runner.voice import build_chunks
        chunks = build_chunks("Câu một. Câu hai. [ngắt 1.5s] Câu ba.\nDòng hai", "line")
        self.assertEqual([c["text"] for c in chunks], ["Câu một. Câu hai.", "Câu ba.", "Dòng hai"])
        self.assertEqual([c["pause"] for c in chunks], [1.5, None, None])
        self.assertEqual(len(build_chunks("A. B.\nC.", "sentence")), 1)  # theo câu: gộp tới 220 ký tự


class PauseMergeTests(unittest.TestCase):
    def test_pause_and_gap_lengths(self):
        try:
            video.ffmpeg()
        except Exception:
            self.skipTest("không có ffmpeg")
        with tempfile.TemporaryDirectory() as temp:
            studio = FakeStudio(Path(temp))
            sample = Path(temp) / "s.wav"
            tone(sample, 5)
            profile = studio.add_profile(sample.read_bytes(), "s.wav", "P", "", consent=True)
            job = studio.create_job(profile["id"], "Một.\n[ngắt 2s]\nHai.\nBa.", "", "wav", 1.0, split="line", gap=0.5)
            studio.process(job["id"])
            self.assertEqual(studio.profile(profile["id"])["ref_text"], "lời mẫu")  # lưu lại: lần sau không nhận dạng nữa
            before = FakeStudio.prepared
            studio.process(studio.create_job(profile["id"], "Bốn.", "", "wav", 1.0)["id"])
            self.assertEqual(FakeStudio.prepared, before)
            info = studio.public(studio.load_job(job["id"]))
            # 3 đoạn × 0,4 s + nghỉ 2 s + nghỉ 0,5 s = 3,7 s
            self.assertAlmostEqual(info["seconds"], 3.7, delta=0.15)


class BatchProjectTests(unittest.TestCase):
    def test_each_line_is_a_file_and_export_zip(self):
        import zipfile
        try:
            video.ffmpeg()
        except Exception:
            self.skipTest("không có ffmpeg")
        with tempfile.TemporaryDirectory() as temp:
            studio = FakeStudio(Path(temp))
            sample = Path(temp) / "s.wav"
            tone(sample, 5)
            profile = studio.add_profile(sample.read_bytes(), "s.wav", "P", "", consent=True)
            project = studio.create_batch(profile["id"], "Dòng thứ nhất.\n\nDòng thứ hai [ngắt 1s] tiếp.\n", "Dự án A")
            self.assertEqual([j["index"] for j in project["jobs"]], [1, 2])
            more = studio.create_batch(profile["id"], "Dòng ba", group=project["group"])
            self.assertEqual((more["title"], more["jobs"][0]["index"]), ("Dự án A", 3))
            self.assertEqual({j["status"] for j in studio.jobs()}, {"draft"})  # Sẵn sàng: chưa chạy tới khi bấm Tạo
            edited = studio.edit(more["jobs"][0]["id"], "Dòng ba đã sửa", "Dòng ba đã sửa")
            self.assertEqual((edited["status"], edited["title"]), ("draft", "Dòng ba đã sửa"))
            self.assertEqual(studio.start_group(project["group"], {"format": "wav", "speed": 1.1}), 3)
            for job in studio.jobs():
                self.assertEqual((job["status"], job["format"]), ("waiting", "wav"))
                studio.process(job["id"])
            path = studio.export_group(project["group"])
            with zipfile.ZipFile(path) as z:
                names = sorted(z.namelist())
            self.assertEqual(len(names), 3)
            self.assertTrue(names[0].startswith("001_Dòng thứ nhất") and names[2].startswith("003_Dòng ba đã sửa"))


class AudioPolishTests(unittest.TestCase):
    """Đợt 2: chuẩn hoá −16 LUFS, nhạc nền, lọc ồn + chọn đoạn giọng mẫu rõ nhất, mẫu cài đặt."""

    def setUp(self):
        try:
            video.ffmpeg()
        except Exception:
            self.skipTest("không có ffmpeg")
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_pick_reference_cuts_at_pauses(self):
        from runner.voice import pick_reference
        spans = [(0.5, 3.0), (3.4, 7.9), (8.3, 12.0), (12.6, 20.0), (20.4, 22.0)]
        start, end = pick_reference(spans, 25.0)
        self.assertTrue(8 <= end - start <= 12, (start, end))
        self.assertIn(start, [a for a, _ in spans])  # bắt đầu ngay sau chỗ lặng
        self.assertIn(end, [b for _, b in spans])  # kết thúc ở chỗ lặng
        self.assertEqual(pick_reference([(0.4, 6.0)], 7.0), (0.4, 6.0))  # file ngắn: lấy cả đoạn có tiếng

    def test_long_noisy_sample_is_cleaned_and_trimmed(self):
        studio = FakeStudio(self.data)
        sample = self.data / "dai.wav"
        # 4 "câu" 5 giây, mỗi câu cách nhau 1 giây lặng → 24 giây
        segs = []
        for i in range(4):
            seg = self.data / f"p{i}.wav"
            video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={300 + 60 * i}:duration=5",
                       "-af", "apad=pad_dur=1", "-ar", "24000", "-ac", "1", str(seg)], 60)
            segs.append(seg)
        listing = self.data / "l.txt"
        listing.write_text("".join(f"file '{x.as_posix()}'\n" for x in segs), encoding="utf-8")
        video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), str(sample)], 60)
        profile = studio.add_profile(sample.read_bytes(), "dai.wav", "Dài", "lời của cả file", consent=True)
        self.assertTrue(profile["cleaned"])
        self.assertGreaterEqual(profile["source_seconds"], 22)
        self.assertTrue(7.5 <= profile["seconds"] <= 12.5, profile)
        self.assertEqual(profile["ref_text"], "")  # lời mẫu của cả file không khớp đoạn đã cắt → tự nhận dạng
        raw = studio.add_profile(sample.read_bytes(), "dai.wav", "Thô", "", consent=True, clean=False)
        self.assertFalse(raw["cleaned"])
        self.assertLessEqual(raw["seconds"], 12.5)

    def test_output_is_normalised_to_minus_16_lufs_with_music(self):
        from runner.voice import measured_lufs
        studio = FakeStudio(self.data)
        sample = self.data / "s.wav"
        tone(sample, 5)
        profile = studio.add_profile(sample.read_bytes(), "s.wav", "P", "lời", consent=True)
        music = self.data / "nhac.wav"
        video.run([video.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                   "-ar", "44100", "-ac", "2", str(music)], 60)
        job = studio.create_job(profile["id"], "Một. Hai. Ba.", "", "wav", 1.0, "line", 0.3,
                                extra={"normalize": True, "music": str(music), "music_volume": 0.1})
        studio.process(job["id"])
        out = studio.output_file(job["id"])
        self.assertAlmostEqual(measured_lufs(out), -16, delta=1.5)
        quiet = studio.create_job(profile["id"], "Một.", "", "wav", 1.0, extra={"normalize": False})
        studio.process(quiet["id"])
        self.assertTrue(studio.output_file(quiet["id"]).is_file())

    def test_presets(self):
        studio = FakeStudio(self.data)
        names = [p["name"] for p in studio.presets()]
        self.assertIn("Kể chuyện động vật", names)
        items = studio.save_preset({"name": "Kênh mèo", "speed": 1.2, "gap": 0.5, "nfe": 24, "music_id": "abc",
                                    "music_volume": 0.2, "split": "line"})
        mine = next(p for p in items if p["name"] == "Kênh mèo")
        self.assertEqual((mine["speed"], mine["split"], mine["music_volume"]), (1.2, "line", 0.2))
        self.assertNotIn(mine["id"], [p["id"] for p in studio.delete_preset(mine["id"])])
        with self.assertRaises(VoiceError):
            studio.save_preset({"name": " "})
