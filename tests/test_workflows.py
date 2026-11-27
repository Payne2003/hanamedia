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

    def test_wildlife_skills_get_one_managed_action_analysis_block(self):
        from runner import agent_rules
        flows = self.flows
        flows.list()
        folder = self.home / "workflows" / "wildlife" / "skills"
        original = {}
        for name in ("phan-tich-video-doi-thu", "wildlife-motion-prompt-meta", "hanamedia-wildlife-workflow"):
            (folder / name).mkdir(parents=True)
            original[name] = f'---\nname: "{name}"\ndescription: "mô tả"\n---\n# {name}\nNội dung gốc của người dùng.\n'
            (folder / name / "SKILL.md").write_text(original[name], encoding="utf-8")
        flows.sync("wildlife")
        flows.sync("wildlife")  # chạy lại không nhân đôi khối
        analyst = (folder / "phan-tich-video-doi-thu" / "SKILL.md").read_text(encoding="utf-8")
        writer = (folder / "wildlife-motion-prompt-meta" / "SKILL.md").read_text(encoding="utf-8")
        conductor = (folder / "hanamedia-wildlife-workflow" / "SKILL.md").read_text(encoding="utf-8")
        for text, role in ((analyst, "analyst"), (writer, "writer"), (conductor, "conductor")):
            self.assertEqual(text.count(agent_rules.begin(role)), 1)
            self.assertEqual(text.count(agent_rules.end(role)), 1)
        self.assertTrue(analyst.startswith(original["phan-tich-video-doi-thu"]))     # phần của người dùng giữ nguyên
        for phrase in ("lao về phía", "chạm vào", "bắt giữ", "chưa xác định tiếp xúc", "CẮT CẢNH", "REVIEW.md", "review_conclusions.json"):
            self.assertIn(phrase, analyst)
        self.assertIn("ngoạm được dê", writer)                                     # ví dụ: không tự thêm kết quả không nhìn thấy
        self.assertIn("review_handoff.md", writer + conductor)
        # bản sao Claude Code / Codex nhận cùng nội dung (workflow thật nạp từ đây)
        copied = (self.home / ".claude" / "skills" / "phan-tich-video-doi-thu" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(copied, analyst)
        # khối đổi phiên bản: được thay, không thêm khối thứ hai
        skill = folder / "phan-tich-video-doi-thu" / "SKILL.md"
        skill.write_text(analyst.replace(agent_rules.begin("analyst"), "<!-- HANAMEDIA:action-analysis:analyst v0 BEGIN -->")
                         .replace("lao về phía", "SỬA TAY"), encoding="utf-8")
        flows.sync("wildlife")
        again = skill.read_text(encoding="utf-8")
        self.assertEqual(again.count("HANAMEDIA:action-analysis:analyst"), 2)  # BEGIN + END, không phải bốn dòng
        self.assertNotIn("SỬA TAY", again)
        # kỹ năng chưa nạp thì bỏ qua, workflow khác không bị đụng
        shutil_target = self.home / "workflows" / "thoai-phim" / "skills"
        self.assertFalse(any("HANAMEDIA:action-analysis" in p.read_text(encoding="utf-8") for p in shutil_target.rglob("SKILL.md")))
        self.assertIn("REVIEW.md", flows.prompt("wildlife", "run"))

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
