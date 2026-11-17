import json
import os
import stat
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from runner.workflows import Workflows, WorkflowError, slugify


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "HanaMedia"
        self.fake_user = self.base / "user"
        self.fake_user.mkdir()
        self.patch = mock.patch("pathlib.Path.home", return_value=self.fake_user)
        self.patch.start()
        self.flows = Workflows(lambda: self.home)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def skill(self, name, text="Hướng dẫn"):
        folder = self.base / "src" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(f'---\nname: "{name}"\ndescription: "Mô tả {name}"\n---\n# {name}\n{text}\n', encoding="utf-8")
        return folder

    def test_default_and_new_workflow_with_skills_and_requests(self):
        items = self.flows.list()
        self.assertEqual(items[0]["slug"], "wildlife")
        self.assertEqual(items[0]["agents"]["writer"]["skill"], "wildlife-motion-prompt-meta")
        wf = self.flows.save({"name": "Phim hoạt hình", "command": "Chạy cartoon-workflow"})
        self.assertEqual(wf["slug"], "phim-hoat-hinh")
        self.assertEqual(self.flows.save({"name": "Phim hoạt hình"})["slug"], "phim-hoat-hinh-2")  # không trùng
        self.skill("cartoon-writer")
        self.skill("cartoon-workflow")
        names = self.flows.import_skills("phim-hoat-hinh", self.base / "src")
        self.assertEqual(sorted(names), ["cartoon-workflow", "cartoon-writer"])
        # nạp từ zip
        z = self.base / "k.zip"
        with zipfile.ZipFile(z, "w") as handle:
            handle.writestr("reviewer/SKILL.md", '---\nname: "reviewer"\n---\nKiểm duyệt')
        self.assertEqual(self.flows.import_skills("phim-hoat-hinh", z), ["reviewer"])
        wf = self.flows.save({"slug": "phim-hoat-hinh", "name": "Phim hoạt hình", "command": "Chạy cartoon-workflow",
                              "agents": {"conductor": {"skill": "cartoon-workflow"}, "writer": {"name": "Biên kịch", "skill": "cartoon-writer"}}})
        self.assertEqual(len(wf["skills"]), 3)
        self.assertEqual(wf["agents"]["writer"]["name"], "Biên kịch")
        requests = self.flows.add_request("phim-hoat-hinh", "Phần kiểm duyệt bỏ sót cảnh trùng", "reviewer")
        self.assertEqual(len(requests), 1)
        self.assertIn("kỹ năng: reviewer", requests[0])
        prompt = self.flows.prompt("phim-hoat-hinh", "run")
        self.assertIn("workflows/phim-hoat-hinh/requests.md", prompt)
        self.assertIn("Chạy cartoon-workflow", prompt)
        self.assertIn("output/_status/phim-hoat-hinh.json", prompt)
        self.assertIn("KHÔNG chạy workflow", self.flows.prompt("phim-hoat-hinh", "apply"))
        done = self.flows.sync("phim-hoat-hinh")
        self.assertEqual(done, {"claude": 3, "codex": 3, "zip": 3})
        self.assertTrue((self.home / ".claude" / "skills" / "cartoon-writer" / "SKILL.md").is_file())
        self.assertTrue((self.fake_user / ".codex" / "skills" / "reviewer" / "SKILL.md").is_file())
        self.assertTrue((self.home / "workflows" / "phim-hoat-hinh" / "dist" / "reviewer.zip").is_file())
        with self.assertRaises(WorkflowError):
            self.flows.delete("wildlife")
        self.flows.delete("phim-hoat-hinh-2")
        self.assertNotIn("phim-hoat-hinh-2", [w["slug"] for w in self.flows.list()])
        self.assertTrue(any((self.home / "workflows-deleted").iterdir()))  # không xóa hẳn

    def test_bad_inputs(self):
        with self.assertRaises(WorkflowError):
            self.flows.folder("../x")
        with self.assertRaises(WorkflowError):
            self.flows.save({"name": " "})
        self.flows.save({"name": "A"})
        with self.assertRaises(WorkflowError):
            self.flows.import_skills("a", self.base / "khong-co")
        with self.assertRaises(WorkflowError):
            self.flows.add_request("a", "   ")
        self.assertEqual(slugify("Review Sản phẩm!"), "review-san-pham")

    @unittest.skipIf(sys.platform == "win32", "dùng script shell giả")
    def test_run_with_fake_codex_applies_request_and_reports_progress(self):
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "codex"
        # Codex giả: đọc lệnh từ stdin, sửa SKILL.md theo yêu cầu, ghi CHANGELOG + tiến độ
        fake.write_text("#!/bin/sh\ncat > prompt.txt\n"
                        "echo 'Đã thêm: cấm slow motion' >> workflows/a/skills/writer/SKILL.md\n"
                        "echo 'sửa writer' >> workflows/a/CHANGELOG.md\n"
                        "rm -f workflows/a/requests.md\n"
                        "mkdir -p output/_status\n"
                        "echo '{\"agents\": {\"writer\": \"done\", \"boss\": \"working\"}, \"item\": \"video-1\", \"percent\": 60}' > output/_status/a.json\n"
                        "echo xong\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.flows.save({"name": "A", "command": "Chạy boss", "agents": {"conductor": {"skill": "boss"}, "writer": {"skill": "writer"}}})
        self.skill("writer")
        self.flows.import_skills("a", self.base / "src" / "writer")
        self.flows.add_request("a", "Cấm slow motion")
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            self.assertTrue(self.flows.engines()["codex"])
            status = self.flows.run("a", "codex", "run")
            self.assertTrue(status["running"] or status["code"] == 0)
            with self.assertRaises(WorkflowError):
                if self.flows.running("a"):
                    self.flows.run("a", "codex")
                else:
                    raise WorkflowError("đã xong")
            for _ in range(100):
                if not self.flows.running("a"):
                    break
                time.sleep(.05)
            time.sleep(.2)
        status = self.flows.status("a")
        self.assertEqual(status["code"], 0)
        self.assertIn("xong", status["log"])
        self.assertIn("requests.md", (self.home / "prompt.txt").read_text(encoding="utf-8"))
        self.assertIn("cấm slow motion", (self.home / "workflows/a/skills/writer/SKILL.md").read_text(encoding="utf-8"))
        # sau khi chạy: kỹ năng đã sửa được chép lại cho Codex
        self.assertIn("cấm slow motion", (self.fake_user / ".codex/skills/writer/SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(self.flows.requests("a"), [])
        [item] = self.flows.board("a")
        self.assertEqual((item["name"], item["percent"], item["stages"]["writer"], item["stages"]["conductor"]),
                         ("video-1", 60, "done", "working"))
        with self.assertRaises(WorkflowError):
            self.flows.run("a", "gemini")


class StreamTests(unittest.TestCase):
    def test_stream_json_is_readable(self):
        from runner.workflows import stream_line
        self.assertIn("model opus", stream_line('{"type":"system","subtype":"init","model":"opus"}'))
        self.assertEqual(stream_line('{"type":"assistant","message":{"content":[{"type":"text","text":"Đang xem video"},'
                                     '{"type":"tool_use","name":"Skill","input":{"skill":"phan-tich"}}]}}'),
                         "Đang xem video\n  → Skill: phan-tich")
        self.assertIn("✕", stream_line('{"type":"user","message":{"content":[{"type":"tool_result","is_error":true,"content":"denied"}]}}'))
        self.assertIsNone(stream_line('{"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}}'))
        self.assertIn("✓ XONG: Đã ghi 4 file", stream_line('{"type":"result","subtype":"success","result":"Đã ghi 4 file"}'))
        self.assertEqual(stream_line("Invalid API key · Please run /login"), "Invalid API key · Please run /login")


if __name__ == "__main__":
    unittest.main()
