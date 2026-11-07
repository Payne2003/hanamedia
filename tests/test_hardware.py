"""Tự đo cấu hình máy: số luồng tối đa theo RAM / CPU; dùng GPU để mã hóa video, GPU lỗi thì tự về CPU."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runner import hardware, video_tools


class HardwareTests(unittest.TestCase):
    def setUp(self):
        hardware._gpu.update(done=False, name=None)

    def tearDown(self):
        hardware._gpu.update(done=False, name=None)

    def test_threads_follow_ram_and_cpu(self):
        self.assertEqual(hardware.recommended_threads(8, 8)[0], 2)
        self.assertEqual(hardware.recommended_threads(16, 8)[0], 4)
        self.assertEqual(hardware.recommended_threads(16, 12)[0], 6)
        self.assertEqual(hardware.recommended_threads(32, 16)[0], 8)
        self.assertEqual(hardware.recommended_threads(128, 64)[0], hardware.HARD_CAP)
        self.assertEqual(hardware.recommended_threads(4, 2)[0], 1)  # máy yếu vẫn chạy được 1
        self.assertEqual(hardware.recommended_threads(None, None)[0], 1)
        info = hardware.capacity()
        self.assertTrue(1 <= info["max_threads"] <= hardware.HARD_CAP)

    def test_gpu_detected_only_when_test_encode_works(self):
        calls = []

        def runner(args, **kw):
            calls.append(args)
            if "-encoders" in args:
                return SimpleNamespace(stdout=" V..... h264_nvenc  NVIDIA\n V..... h264_qsv  Intel", returncode=0)
            return SimpleNamespace(stdout="", returncode=0 if "h264_qsv" in args else 1)  # NVENC hỏng, QSV chạy
        self.assertEqual(hardware.gpu_encoder("ffmpeg", runner), "h264_qsv")
        self.assertEqual(hardware.gpu_encoder("ffmpeg", runner), "h264_qsv")  # nhớ kết quả, không thử lại
        self.assertEqual(sum(1 for c in calls if "-encoders" in c), 1)

    def test_no_gpu(self):
        runner = lambda args, **kw: SimpleNamespace(stdout=" V..... libx264", returncode=0)
        self.assertIsNone(hardware.gpu_encoder("ffmpeg", runner))

    def test_encode_falls_back_to_cpu_when_gpu_fails(self):
        temp = Path(tempfile.mkdtemp()) / "out.part.mp4"
        seen = []

        def fake_run(args, timeout):
            seen.append(args)
            if "libx264" in args:
                temp.write_bytes(b"ok")
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=1)
        with patch.object(video_tools, "run", fake_run):
            result = video_tools.encode(lambda video: ["ffmpeg", *video, str(temp)], temp, 10,
                                        hardware.ENCODERS["h264_nvenc"][1])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(seen), 2)
        self.assertIn("h264_nvenc", seen[0])
        self.assertIn("libx264", seen[1])


if __name__ == "__main__":
    unittest.main()
