"""Plugin viết prompt dạng skill (plugin.json + skills/<tên>/SKILL.md + references/lookup/agents)."""
import json
import tempfile
import unittest
from pathlib import Path

from runner import content, prompt_plugin


def make_plugin(root):
    root = Path(root)
    (root / ".codex-plugin").mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({"name": "Wildlife Prompt Pro", "description": "Viết prompt động vật"}),
                                      encoding="utf-8")
    skill = root / "skills" / "instructions"
    for sub in ("agents", "lookup", "references"):
        (skill / sub).mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: instructions\ndescription: x\n---\n# Cách viết\nLuôn đọc references/camera.md.\n",
                                    encoding="utf-8")
    (skill / "references" / "camera.md").write_text("Góc máy: dùng ống kính 600mm.", encoding="utf-8")
    (skill / "lookup" / "animals.csv").write_text("loài,tốc độ\nbáo tuyết,64 km/h", encoding="utf-8")
    (skill / "agents" / "critic.md").write_text("Kiểm tra lại prompt.", encoding="utf-8")
    (skill / "lookup" / "run.py").write_text("print('không được chạy')", encoding="utf-8")
    return root


class PromptPluginTests(unittest.TestCase):
    def tearDown(self):
        content.set_plugin(None)

    def test_reads_skill_and_text_documents_only(self):
        with tempfile.TemporaryDirectory() as folder:
            plugin = prompt_plugin.load(make_plugin(folder))
        self.assertEqual(plugin["name"], "Wildlife Prompt Pro")
        self.assertEqual(plugin["files"][0], "skills/instructions/SKILL.md")
        self.assertEqual(plugin["files"][1], "skills/instructions/references/camera.md")  # tệp SKILL.md nhắc tới: lên trước
        self.assertIn("skills/instructions/lookup/animals.csv", plugin["files"])
        self.assertNotIn("name: instructions", plugin["text"])  # bỏ khối YAML đầu tệp
        self.assertNotIn("run.py", " ".join(plugin["files"]))  # không đọc / chạy mã
        self.assertIn("600mm", plugin["text"])

    def test_plugin_goes_in_front_of_every_prompt_request(self):
        with tempfile.TemporaryDirectory() as folder:
            content.set_plugin(prompt_plugin.load(make_plugin(folder)))
        for request in (content.detailed_request("Bối cảnh 1: x", 1, 1, 10),
                        content.detailed_batch_request([(1, "x", 10, None)], 1),
                        content.english_request(["Bối cảnh 1: x"]),
                        content.review_request(["Generate a 10-second video."], "rules")):
            self.assertTrue(request.startswith("PROMPT-WRITING PLUGIN «Wildlife Prompt Pro»"))
            self.assertIn("600mm", request)
        from runner.prompt_example import DETAILED_EXAMPLE
        self.assertNotIn(DETAILED_EXAMPLE[:200], content.detailed_batch_request([(1, "x", 10, None)], 1))  # có plugin: bỏ bài mẫu
        content.set_plugin(None)
        self.assertFalse(content.detailed_request("x", 1, 1, 10).startswith("PROMPT-WRITING"))
        self.assertIn(DETAILED_EXAMPLE[:200], content.detailed_batch_request([(1, "x", 10, None)], 1))

    def test_folder_without_skill_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(prompt_plugin.PluginError):
                prompt_plugin.load(folder)


class PickerRoutingTests(unittest.TestCase):
    def test_installed_app_routes_every_picker_flag(self):
        import re
        root = Path(__file__).resolve().parent.parent
        app_flags = set(re.findall(r'"(--pick-[a-z]+)"', (root / "app.py").read_text(encoding="utf-8")))
        import desktop
        self.assertEqual(set(desktop.PICK_FLAGS), app_flags)  # thiếu cờ nào thì bản cài đặt không mở được hộp chọn
        self.assertIn("--pick-plugin", app_flags)


class PluginFormatTests(unittest.TestCase):
    def tearDown(self):
        content.set_plugin(None)

    def test_prompts_keep_plugin_format_and_exact_decimal_seconds(self):
        import threading
        from runner import gemini_web
        sent = []
        body = "| ENVIRONMENT: a towering pale-grey limestone wall with dry grass | ACTION: the bear roars | CAMERA: Full body"

        class Session:
            def new_chat(self): pass
            def wait_ready(self, *a, **k): pass
            def choose_model(self, name): return name
            def ask(self, text, *a, **k):
                sent.append(text)
                return (f"SCENE 1 (duration 3 seconds) {body} | RENDERING RULES: Duration 3 seconds\n"
                        f"SCENE 2 (duration 2 seconds) {body}\n{content.END_EN}")
        content.set_plugin({"name": "Demo", "text": "Write SCENE N (duration X seconds) | ENVIRONMENT | … on one line."})
        windows = content.shot_windows([dict(start=0, end=2.8), dict(start=2.8, end=5.0)], 5.0)
        result = gemini_web.run_english(Session(), ["Cảnh 1", "Cảnh 2"], lambda t: None, threading.Event(),
                                        structured="detailed", windows=windows)
        self.assertEqual(len(sent), 1)  # không bắt viết lại theo mẫu "Generate…" của tool
        self.assertIn("exact duration 2.8 seconds", sent[0])
        self.assertTrue(result["english"][0].startswith("SCENE 1 (duration 2.8 seconds) | ENVIRONMENT"))
        self.assertIn("Duration 2.8 seconds", result["english"][0])
        self.assertTrue(result["english"][1].startswith("SCENE 2 (duration 2.2 seconds)"))
        self.assertNotIn("STRICT NEGATIVE CONSTRAINTS", result["english"][0])  # không gắn mục chuẩn của tool
        self.assertEqual(result["missing"], [])
