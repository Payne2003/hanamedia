import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app import AppServer
from runner import content
from runner.store import Store

KEY = "AIzaTEST_key_0123456789abcdef"
SCENE = {"setting": "Bãi biển lúc bình minh", "camera": "Toàn cảnh, lia chậm",
         "behavior": "Cô gái mỉm cười", "action": "Cô gái chạy dọc bờ biển"}


def fake_transport(reply, calls, status=200):
    def transport(url, payload, headers, timeout):
        calls.append((url, payload, headers))
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps(reply, ensure_ascii=False)}]}}]}
        return status, json.dumps(body if status == 200 else {"error": {"message": "bad"}}).encode()
    return transport


class ContentTests(unittest.TestCase):
    def test_youtube_links_are_normalized_and_others_rejected(self):
        expected = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        for link in ("https://youtu.be/dQw4w9WgXcQ?t=3", "youtube.com/watch?v=dQw4w9WgXcQ&list=x",
                     "https://m.youtube.com/shorts/dQw4w9WgXcQ"):
            self.assertEqual(content.youtube_url(link), expected)
        self.assertEqual(content.youtube_url("  "), "")
        for link in ("https://www.tiktok.com/@a/video/1", "https://youtube.com/watch?v=short", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                content.youtube_url(link)

    def test_prompts_follow_scene_structure(self):
        self.assertEqual(content.format_prompts([SCENE])[0],
                         "Bối cảnh 1: Bãi biển lúc bình minh, góc quay: Toàn cảnh, lia chậm, "
                         "hành vi nhân vật: Cô gái mỉm cười, Nội dung hành động nhân vật: Cô gái chạy dọc bờ biển;")

    def test_trailing_punctuation_is_trimmed_in_prompts(self):
        scene = dict(SCENE, camera="Trung cảnh, máy quay cố định.")
        self.assertIn("góc quay: Trung cảnh, máy quay cố định, hành vi", content.format_prompts([scene])[0])

    def test_generate_sends_video_and_cleans_scenes(self):
        calls = []
        reply = {"original_script": "Kịch bản", "scenes": [dict(SCENE, action="Chạy\n nhanh"),
                                                            {"setting": "", "camera": "", "behavior": "", "action": ""},
                                                            dict(SCENE, camera="")]}
        result = content.Gemini(KEY, transport=fake_transport(reply, calls)).generate(
            "Chia cảnh", "https://youtu.be/dQw4w9WgXcQ")
        url, payload, headers = calls[0]
        self.assertTrue(url.endswith("/gemini-3.8-flash:generateContent"))
        self.assertEqual(headers["x-goog-api-key"], KEY)
        self.assertEqual(payload["contents"][0]["parts"][0]["file_data"]["file_uri"],
                         "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(len(result["scenes"]), 2)
        self.assertEqual(result["scenes"][0]["action"], "Chạy nhanh")
        self.assertEqual(result["issues"], ["Cảnh 2 thiếu: góc quay."])

    def test_segment_clip_is_sent_to_gemini(self):
        calls = []
        gemini = content.Gemini(KEY, transport=fake_transport({"original_script": "K", "scenes": [SCENE]}, calls))
        gemini.generate("Chia cảnh", "https://youtu.be/dQw4w9WgXcQ", start=60, end=120)
        part = calls[0][1]["contents"][0]["parts"][0]
        self.assertEqual(part["video_metadata"], {"start_offset": "60s", "end_offset": "120s"})
        self.assertIn("01:00 đến 02:00", calls[0][1]["contents"][0]["parts"][1]["text"])
        gemini.review("", "https://youtu.be/dQw4w9WgXcQ", [SCENE], start=0, end=60)
        self.assertEqual(calls[1][1]["contents"][0]["parts"][0]["video_metadata"]["end_offset"], "60s")
        with self.assertRaises(ValueError):
            gemini.generate("Chia cảnh", "https://youtu.be/dQw4w9WgXcQ", start=90, end=30)

    def test_video_duration_is_read_from_youtube_page(self):
        page = 'xx "videoDetails":{"videoId":"dQw4w9WgXcQ","lengthSeconds":"553","keywords":[]} yy'
        self.assertEqual(content.video_duration("https://youtu.be/dQw4w9WgXcQ", lambda url: page), 553)
        self.assertIsNone(content.video_duration("https://youtu.be/dQw4w9WgXcQ", lambda url: "no data"))
        def offline(url):
            raise OSError("blocked")
        self.assertIsNone(content.video_duration("https://youtu.be/dQw4w9WgXcQ", offline))
        self.assertEqual(content.timestamp(553), "09:13")

    def test_timeout_tries_next_model_then_explains(self):
        calls = []
        ok = fake_transport({"original_script": "K", "scenes": [SCENE]}, calls)
        def flaky(url, payload, headers, timeout):
            if "/gemini-3.8-flash:" in url:
                raise TimeoutError("timed out")
            return ok(url, payload, headers, timeout)
        gemini = content.Gemini(KEY, transport=flaky)
        self.assertEqual(len(gemini.generate("Chia cảnh")["scenes"]), 1)
        def dead(url, payload, headers, timeout):
            raise TimeoutError("timed out")
        gemini = content.Gemini(KEY, transport=dead)
        gemini.sleep = lambda _: None
        with self.assertRaisesRegex(ValueError, "quá lâu"):
            gemini.generate("Chia cảnh")

    def test_web_prompt_contains_link_rules_and_format(self):
        prompt = content.web_prompt("generate", "Chia 3 giây", "https://youtu.be/dQw4w9WgXcQ")
        for text in ("Chia 3 giây", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Bối cảnh 1: ..., góc quay:", "DANH SÁCH CẢNH"):
            self.assertIn(text, prompt)
        review = content.web_prompt("review", "", "", scenes=[SCENE])
        self.assertIn(content.format_prompts([SCENE])[0], review)
        with self.assertRaises(ValueError):
            content.web_prompt("generate", "")

    def test_pasted_gemini_answer_is_parsed(self):
        answer = """Chắc chắn rồi! Dưới đây là phân tích:
**VẤN ĐỀ:**
- Thiếu cảnh kết
**KỊCH BẢN VIDEO GỐC:**
Báo tuyết rình đàn dê.

**DANH SÁCH CẢNH:**
1. **Bối cảnh 1:** Vách đá xám, cỏ xanh, góc quay: Cận cảnh, tĩnh, hành vi nhân vật: Thư giãn, Nội dung hành động nhân vật: Dê gặm cỏ;
- Bối cảnh 2: Gờ đá; góc quay: Toàn cảnh; hành vi nhân vật: Cẩn trọng; nội dung hành động: Báo lẻn đi.
Hy vọng hữu ích!"""
        result = content.parse_answer(answer)
        self.assertEqual(result["original_script"], "Báo tuyết rình đàn dê.")
        self.assertEqual(result["issues"], ["Thiếu cảnh kết"])
        self.assertEqual(result["scenes"][0], {"setting": "Vách đá xám, cỏ xanh", "camera": "Cận cảnh, tĩnh",
                                               "behavior": "Thư giãn", "action": "Dê gặm cỏ"})
        self.assertEqual(result["scenes"][1]["action"], "Báo lẻn đi.")
        json_answer = '```json\n{"original_script": "K", "scenes": [%s]}\n```' % json.dumps(SCENE, ensure_ascii=False)
        self.assertEqual(content.parse_answer(json_answer)["scenes"], [SCENE])
        with self.assertRaisesRegex(ValueError, "Không tìm thấy"):
            content.parse_answer("Xin chào, tôi không xem được video.")

    def test_review_returns_fixed_scenes_and_issues(self):
        calls = []
        reply = {"issues": ["Thiếu cảnh kết"], "scenes": [SCENE, SCENE]}
        result = content.Gemini(KEY, transport=fake_transport(reply, calls)).review("", "", [SCENE])
        self.assertNotIn("file_data", calls[0][1]["contents"][0]["parts"][0])
        self.assertEqual((len(result["scenes"]), result["issues"]), (2, ["Thiếu cảnh kết"]))

    def test_overloaded_model_falls_back(self):
        calls = []
        ok = fake_transport({"original_script": "K", "scenes": [SCENE]}, calls)
        def transport(url, payload, headers, timeout):
            if "/gemini-3.8-flash:" in url:
                calls.append((url,))
                return 503, b'{"error":{"message":"high demand"}}'
            return ok(url, payload, headers, timeout)
        gemini = content.Gemini(KEY, transport=transport)
        gemini.sleep = lambda _: None
        self.assertEqual(len(gemini.generate("Chia cảnh")["scenes"]), 1)
        self.assertIn("/gemini-3.5-flash:", calls[1][0])
        self.assertEqual(gemini.used_model, "gemini-3.5-flash")

    def test_api_errors_are_explained(self):
        gemini = content.Gemini(KEY, transport=fake_transport({}, [], status=429))
        gemini.sleep = lambda _: None
        busy = content.Gemini(KEY, transport=fake_transport({}, [], status=503))
        busy.sleep = lambda _: None
        with self.assertRaisesRegex(ValueError, "quá tải"):
            busy.generate("Chia cảnh")
        with self.assertRaisesRegex(ValueError, "429"):
            gemini.generate("Chia cảnh")
        with self.assertRaises(ValueError):
            content.Gemini("short")
        for pasted in ('"AQ.Fake0ExampleKey0123456789"', " AQ.Fake0Example\nKey0123456789 ", "key=AQ.Fake0ExampleKey0123456789"):
            self.assertEqual(content.api_key_value(pasted), "AQ.Fake0ExampleKey0123456789")


class ContentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")
        engine = type("Idle", (), {"snapshot": lambda self: {}, "changed": threading.Event()})()
        self.server = AppServer(0, self.store, engine)
        self.calls = []
        self.server.gemini_transport = fake_transport({"original_script": "K", "scenes": [SCENE]}, self.calls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, path, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("POST", path, body=json.dumps(body), headers={
            "Content-Type": "application/json", "Origin": self.server.origin, "X-Runner-Key": self.server.secret})
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def state(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("GET", "/api/state")
        data = json.loads(connection.getresponse().read())
        connection.close()
        return data

    def test_web_mode_endpoints_need_no_api_key(self):
        status, data = self.post("/api/content/web-prompt", {"kind": "generate", "brief": "Chia cảnh",
                                                             "video_url": "https://youtu.be/dQw4w9WgXcQ"})
        self.assertEqual(status, 200)
        self.assertIn("dQw4w9WgXcQ", data["prompt"])
        status, data = self.post("/api/content/parse", {"text": content.format_prompts([SCENE])[0]})
        self.assertEqual((status, data["scenes"], data["prompts"]), (200, [SCENE], content.format_prompts([SCENE])))
        self.assertEqual(self.calls, [])

    def test_key_is_required_saved_and_never_returned(self):
        status, data = self.post("/api/content/generate", {"brief": "Chia cảnh"})
        self.assertEqual(status, 400)
        self.assertIn("khóa API", data["error"])
        self.assertEqual(self.post("/api/content/settings", {"api_key": KEY, "model": "gemini-2.5-pro"})[0], 200)
        state = self.state()
        self.assertEqual({k: state["content"][k] for k in ("version", "configured", "model")},
                         {"version": content.VERSION, "configured": True, "model": "gemini-2.5-pro"})
        self.assertNotIn(KEY, json.dumps(state))
        status, data = self.post("/api/content/generate", {"brief": "Chia cảnh", "video_url": ""})
        self.assertEqual(status, 200)
        self.assertEqual(data["prompts"], content.format_prompts([SCENE]))
        self.assertIn("/gemini-2.5-pro:", self.calls[0][0])
        self.post("/api/content/settings", {"clear_key": True, "model": ""})
        self.assertFalse(self.state()["content"]["configured"])


if __name__ == "__main__":
    unittest.main()


class GeminiWebTests(unittest.TestCase):
    def test_conversation_continues_until_end_mark_and_merges(self):
        from runner import gemini_web

        class FakeSession:
            def __init__(self, answers):
                self.answers, self.sent = list(answers), []
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return name
            def ask(self, text, status, stop, email="", label=""):
                self.sent.append(text)
                return self.answers.pop(0)

        line = lambda n: f"Bối cảnh {n}: Cảnh {n}, góc quay: Trung cảnh, hành vi nhân vật: Đi, Nội dung hành động nhân vật: Bước {n};"
        session = FakeSession(["KỊCH BẢN VIDEO GỐC:\nK\nDANH SÁCH CẢNH:\n" + line(1) + "\n" + line(2),
                               line(2) + "\n" + line(3),
                               line(4) + "\n" + gemini_web.END_MARK])
        result = gemini_web.run_conversation(session, "Yêu cầu", lambda _: None, threading.Event(), model="Pro")
        self.assertEqual([s["setting"] for s in result["scenes"]], ["Cảnh 1", "Cảnh 2", "Cảnh 3", "Cảnh 4"])
        self.assertEqual(result["rounds"], 3)
        self.assertIn(gemini_web.END_MARK, session.sent[0])
        self.assertIn("Bối cảnh 3", session.sent[1])

    def test_runner_reports_result_and_errors(self):
        from runner import gemini_web
        line = "Bối cảnh 1: A, góc quay: B, hành vi nhân vật: C, Nội dung hành động nhân vật: D;\n" + gemini_web.END_MARK

        class Session:
            def __init__(self, profile):
                pass
            def alive(self):
                return True
            def close(self):
                pass
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return ""
            def ask(self, text, status, stop, email="", label=""):
                if "lỗi" in text:
                    raise gemini_web.GeminiWebError("Gemini chưa nhận yêu cầu")
                return line

        with tempfile.TemporaryDirectory() as folder:
            runner = gemini_web.GeminiWebRunner(Path(folder), Session)
            runner.start("generate", "Yêu cầu")
            for _ in range(50):
                if not runner.snapshot()["busy"]:
                    break
                threading.Event().wait(0.05)
            self.assertEqual(runner.snapshot()["result"]["prompts"], content.format_prompts([dict(zip(content.FIELDS, "ABCD"))]))
            runner.start("generate", "lỗi")
            for _ in range(50):
                if not runner.snapshot()["busy"]:
                    break
                threading.Event().wait(0.05)
            self.assertIn("chưa nhận", runner.snapshot()["error"])
            runner.close()


class GeminiLoginTests(unittest.TestCase):
    def test_login_opens_plain_chrome_with_tool_profile_and_blocks_run_while_open(self):
        from unittest.mock import patch
        from runner import gemini_web

        class Process:
            running = True
            def poll(self):
                return None if self.running else 0

        launched = []
        process = Process()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(gemini_web, "chrome_executable", return_value="chrome.exe"):
            runner = gemini_web.GeminiWebRunner(Path(folder) / "gemini-web")
            runner.open_login("jimmy@gmail.com", lambda args: launched.append(args) or process)
            args = launched[0]
            self.assertEqual(args[0], "chrome.exe")
            self.assertIn(f"--user-data-dir={Path(folder) / 'gemini-web'}", args)
            self.assertNotIn("--remote-debugging-port", " ".join(args))
            self.assertIn("Email=jimmy%40gmail.com", args[-1])
            with self.assertRaisesRegex(ValueError, "đang mở"):
                runner.open_login("", lambda args: process)
            runner.start("generate", "Yêu cầu")
            for _ in range(50):
                if not runner.snapshot()["busy"]:
                    break
                threading.Event().wait(0.05)
            self.assertIn("vẫn đang mở", runner.snapshot()["error"])
            runner.close()

    def test_sign_in_page_fails_fast_with_instructions(self):
        from runner import gemini_web

        class Driver:
            def execute_script(self, script, *args):
                if script is gemini_web.JS_STATE:
                    return {"signIn": True, "rejected": True}
                return None

        session = gemini_web.GeminiWebSession.__new__(gemini_web.GeminiWebSession)
        session.driver = Driver()
        with self.assertRaisesRegex(gemini_web.GeminiWebError, "Đăng nhập Google"):
            session.wait_ready(lambda _: None, threading.Event(), timeout=5)


class GeminiFillTests(unittest.TestCase):
    """fill(): Ctrl+V thật trước, rồi gõ từng dòng bằng Shift+Enter; không bao giờ gửi từng dòng rời rạc."""

    def run_fill(self, paste_works, shift_enter_sends):
        import sys
        import types
        from unittest.mock import patch
        from runner import gemini_web

        class Keys:
            CONTROL, SHIFT, ENTER = "ctrl", "shift", "\n"

        class Driver:
            current_url = "https://gemini.google.com/app"

            def __init__(self):
                self.box, self.count, self.clip = "", 0, ""

            def execute_cdp_cmd(self, command, args):
                if command == "Input.insertText":
                    self.box += args["text"]

            def execute_async_script(self, script, text, *rest):
                self.clip = text
                return True

            def key(self, key):
                if key == "v" and paste_works:
                    self.box = self.clip
                if key == "\n":
                    if shift_enter_sends:
                        self.count, self.box = self.count + 1, ""
                    else:
                        self.box += "\n"

            def execute_script(self, script, *args):
                if script is gemini_web.JS_STATE:
                    return {"count": self.count}
                if script is gemini_web.JS_CLEAR:
                    self.box = ""
                    return 0
                if script is gemini_web.JS_CHECK:
                    return {"length": len(self.box), "ok": "".join(self.box.split()) == "".join(args[1].split())}
                if script is gemini_web.JS_INSERT:
                    return {"length": 0, "ok": False}

        driver = Driver()

        class Actions:
            def __init__(self, _):
                pass

            def key_down(self, key):
                return self

            def key_up(self, key):
                return self

            def send_keys(self, key):
                driver.key(key)
                return self

            def perform(self):
                pass

        chains = types.ModuleType("selenium.webdriver.common.action_chains")
        chains.ActionChains = Actions
        keys = types.ModuleType("selenium.webdriver.common.keys")
        keys.Keys = Keys
        modules = {"selenium": types.ModuleType("selenium"), "selenium.webdriver": types.ModuleType("selenium.webdriver"),
                   "selenium.webdriver.common": types.ModuleType("selenium.webdriver.common"),
                   "selenium.webdriver.common.action_chains": chains, "selenium.webdriver.common.keys": keys}
        session = gemini_web.GeminiWebSession.__new__(gemini_web.GeminiWebSession)
        session.driver = driver
        class Field:
            def click(self):
                pass
        with patch.dict(sys.modules, modules), patch.object(gemini_web.time, "sleep", lambda _: None):
            try:
                return session.fill(Field(), "Dòng một\nDòng hai\n\nDòng ba", 0), driver.count
            except gemini_web.GeminiWebError as error:
                return error, driver.count

    def test_real_paste_is_used_first(self):
        result, sent = self.run_fill(paste_works=True, shift_enter_sends=False)
        self.assertEqual((result["ok"], sent), (True, 0))

    def test_typing_with_shift_enter_when_paste_fails(self):
        result, sent = self.run_fill(paste_works=False, shift_enter_sends=False)
        self.assertEqual((result["ok"], sent), (True, 0))

    def test_stops_after_one_message_if_shift_enter_sends(self):
        result, sent = self.run_fill(paste_works=False, shift_enter_sends=True)
        self.assertIsInstance(result, Exception)
        self.assertEqual(sent, 1)
