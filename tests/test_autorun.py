import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from runner import agent_cli
from runner.autorun import AutoRunner


class FakeProc:
    def __init__(self, seconds):
        self.end = time.time() + seconds

    def poll(self):
        return 0 if time.time() >= self.end else None


class FakeFlows:
    def __init__(self):
        self.runs, self.calls, self.input = {}, [], []

    def running(self, slug):
        run = self.runs.get(slug)
        return bool(run and run.poll() is None)

    def run(self, slug, engine, mode="run"):
        assert not any(self.running(s) for s in self.runs), "hai lần chạy chồng nhau"
        self.calls.append((slug, engine, list(self.input)))
        self.runs[slug] = FakeProc(.15)

    def status(self, slug):
        return {"code": 0}


class AutoRunTests(unittest.TestCase):
    def test_queue_runs_one_video_at_a_time_in_order(self):
        with tempfile.TemporaryDirectory() as temp:
            videos = []
            for name in ("a.mp4", "b.mp4", "c.mp4"):
                path = Path(temp) / name
                path.write_bytes(b"v")
                videos.append(path)
            flows = FakeFlows()

            def prepare(video):
                flows.input = [Path(video).name]  # dự án cũ bị dọn khỏi input/
                return {"name": Path(video).stem}
            runner = AutoRunner(flows, prepare, pick=lambda pref: "codex", poll=.02)
            self.assertEqual(runner.enqueue(videos[0]), 1)
            runner.enqueue(videos[1])
            runner.enqueue(videos[2], "thoai-phim")
            for _ in range(200):
                if len(runner.history) == 3:
                    break
                time.sleep(.02)
            self.assertEqual([c[2] for c in flows.calls], [["a.mp4"], ["b.mp4"], ["c.mp4"]])
            self.assertEqual([c[0] for c in flows.calls], ["wildlife", "wildlife", "thoai-phim"])
            self.assertTrue(all(h["ok"] for h in runner.history))
            with self.assertRaises(ValueError):
                runner.enqueue(Path(temp) / "khong-co.mp4")

    def test_chat_mode_moves_on_when_result_is_written(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "a.mp4"
            video.write_bytes(b"v")
            flows = FakeFlows()
            opened, ready = [], {"ok": False}
            flows.open_chat = lambda slug, engine, project="": opened.append((slug, engine, project))
            runner = AutoRunner(flows, lambda v: {"name": "a"}, pick=lambda pref: "claude", poll=.02, chat=lambda: True,
                                done=lambda slug, name: ready["ok"] and name == "a")
            runner.enqueue(video)
            time.sleep(.2)
            self.assertEqual(opened, [("wildlife", "claude", "a")])  # đoạn chat biết tên dự án
            self.assertTrue(runner.status()["current"]["chat"])
            ready["ok"] = True
            for _ in range(100):
                if runner.history:
                    break
                time.sleep(.02)
            self.assertTrue(runner.history[0]["ok"])
            self.assertEqual(flows.calls, [])  # không chạy nền khi đã mở cửa sổ trò chuyện

    def test_project_done_detects_result(self):
        import json, os
        from runner import claude_bridge as bridge
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "output" / "Sư tử - clip"
            out.mkdir(parents=True)
            (out / ".hanamedia-source").write_text("clip", encoding="utf-8")
            self.assertFalse(bridge.project_done(temp, "clip"))
            (out / "hanamedia_clip.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
            self.assertFalse(bridge.project_done(temp, "clip"))  # vừa ghi
            old = time.time() - 60
            for f in out.iterdir():
                os.utime(f, (old, old))
            self.assertTrue(bridge.project_done(temp, "clip"))
            self.assertFalse(bridge.project_done(temp, "clip", "thoai-phim"))

    def test_no_engine_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "a.mp4"
            video.write_bytes(b"v")
            runner = AutoRunner(FakeFlows(), lambda v: {}, pick=lambda pref: None, poll=.02)
            runner.enqueue(video)
            for _ in range(100):
                if runner.history:
                    break
                time.sleep(.02)
            self.assertFalse(runner.history[0]["ok"])
            self.assertIn("Claude Code hay Codex", runner.history[0]["message"])


@unittest.skipIf(sys.platform == "win32", "dùng script shell giả")
class AgentCliTests(unittest.TestCase):
    def test_find_outside_path_and_login_status(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            exe = home / ".local" / "bin" / "claude"
            exe.parent.mkdir(parents=True)
            exe.write_text("#!/bin/sh\n[ \"$1 $2\" = \"auth status\" ] && echo 'Logged in as x' && exit 0\nexit 1\n")
            exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
            with mock.patch.dict(os.environ, {"HOME": str(home), "HANAMEDIA_ENGINE": ""}):
                none = lambda name: None
                self.assertEqual(agent_cli.find("claude", none), str(exe))
                self.assertIsNone(agent_cli.find("codex", none))
                self.assertEqual(agent_cli.pick("auto", none), "claude")
                self.assertIsNone(agent_cli.pick("codex", none))
                self.assertTrue(agent_cli.logged_in("claude", str(exe)))
                exe.write_text("#!/bin/sh\necho 'Not logged in'\nexit 1\n")
                self.assertFalse(agent_cli.logged_in("claude", str(exe)))
                info = agent_cli.status(none, check_login=False)
                self.assertTrue(info["engines"]["claude"]["found"])
                self.assertFalse(info["engines"]["codex"]["found"])


if __name__ == "__main__":
    unittest.main()
