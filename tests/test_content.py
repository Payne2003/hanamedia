import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app import AppServer
from runner import content
from runner.store import Store

SCENE = {"setting": "Bãi biển lúc bình minh", "camera": "Toàn cảnh, lia chậm",
         "behavior": "Cô gái mỉm cười", "action": "Cô gái chạy dọc bờ biển"}


class ContentTests(unittest.TestCase):
    def test_scenes_merged_on_one_line_are_split(self):
        from runner.content import parse_answer
        text = ("KỊCH BẢN VIDEO GỐC: Dê núi trên vách đá.\n\nDANH SÁCH CẢNH: Bối cảnh 1: Vách đá, ban ngày, góc quay: Toàn cảnh, "
                "hành vi nhân vật: Đứng yên, Nội dung hành động nhân vật: Giữ thăng bằng; Bối cảnh 2: Sườn núi, góc quay: Cận cảnh, "
                "hành vi nhân vật: Nằm im, Nội dung hành động nhân vật: Ngẩng đầu; Bối cảnh 3: Thung lũng, góc quay: Trung cảnh, "
                "hành vi nhân vật: Chạy, Nội dung hành động nhân vật: Hú vang. HẾT DANH SÁCH")
        result = parse_answer(text)
        self.assertEqual(len(result["scenes"]), 3)
        self.assertEqual(result["scenes"][1]["setting"], "Sườn núi")
        self.assertEqual(result["scenes"][2]["camera"], "Trung cảnh")
        self.assertEqual(result["original_script"], "Dê núi trên vách đá.")

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


class ContentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")
        engine = type("Idle", (), {"snapshot": lambda self: {}, "changed": threading.Event()})()
        self.server = AppServer(0, self.store, engine)
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

    def test_only_gemini_pro_endpoints_remain_and_old_key_is_cleared(self):
        for path in ("/api/content/settings", "/api/content/generate", "/api/content/review",
                     "/api/content/duration", "/api/content/web-prompt", "/api/content/parse"):
            self.assertEqual(self.post(path, {"brief": "x"})[0], 404, path)
        self.assertEqual(self.post("/api/content/web-settings", {"email": "jimmy@gmail.com", "model": "Pro"})[0], 200)
        web = self.state()["content"]
        self.assertEqual((web["version"], web["web"]["email"], web["web"]["model"]),
                         (content.VERSION, "jimmy@gmail.com", "Pro"))
        self.assertEqual(self.post("/api/content/web-settings", {"email": "sai email"})[0], 400)
        self.store.save_setting("gemini_api_key", "AIzaOLD000000000000000000")
        engine = type("Idle", (), {"snapshot": lambda self: {}, "changed": threading.Event()})()
        other = AppServer(0, self.store, engine)
        other.server_close()
        other.gemini_web.close()
        self.assertEqual(self.store.setting("gemini_api_key"), "")



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


class EnglishPromptTests(unittest.TestCase):
    def test_input_is_split_from_lines_or_bracketed_list(self):
        one_line = ("Input: [ Bối cảnh 1: Bãi biển, góc quay: Toàn cảnh, hành vi nhân vật: Vui, Nội dung hành động nhân vật: Chạy; "
                    "Bối cảnh 2: Rừng, góc quay: Cận, hành vi nhân vật: Sợ, Nội dung hành động nhân vật: Trốn;]")
        lines = content.split_scene_lines(one_line)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("Bối cảnh 2: Rừng"))
        self.assertEqual(len(content.split_scene_lines("Cảnh A\n\nCảnh B\n")), 2)
        with self.assertRaises(ValueError):
            content.split_scene_lines("  ")

    def test_answer_parsing_prefers_english_and_reports_missing(self):
        answer = """Sure! Here are the prompts:
**1.** Bối cảnh bãi biển lúc bình minh, toàn cảnh, cô gái vui vẻ chạy dọc bờ biển.
1. Wide shot of a sunny beach at dawn, a young woman runs happily along the shoreline.
Scene 3: Close-up of a frightened man hiding behind a large tree in a dark forest.
END OF LIST"""
        found = content.parse_english(answer, 3)
        self.assertTrue(found[1].startswith("Wide shot"))
        self.assertEqual(content.missing_english(found, 3), [2])
        request = content.english_request(["Cảnh A", "Cảnh B", "Cảnh C"], [2], "cinematic 9:16")
        self.assertIn("2. Cảnh B", request)
        self.assertNotIn("1. Cảnh A", request)
        self.assertIn("cinematic 9:16", request)
        self.assertIn("16:9 widescreen", request)
        self.assertIn('"16:9 aspect ratio"', request)
        self.assertIn("9:16 vertical", content.english_request(["A"], aspect="9:16"))
        self.assertNotIn("aspect ratio", content.english_request(["A"], aspect="none"))

    def test_run_english_continues_and_retries_missing_scenes(self):
        from runner import gemini_web
        en = lambda n: f"{n}. English prompt for scene {n}, wide shot, a person walks slowly."

        class Session:
            def __init__(self):
                self.sent = []
                self.answers = [
                    "\n".join([en(1), "2. Bối cảnh rừng, cận cảnh nhân vật sợ hãi trốn sau thân cây lớn.", en(3)]),
                    en(4),                       # viết tiếp, vẫn chưa có END
                    "Nothing more to add.",      # viết tiếp không ra cảnh mới -> dừng
                    "\n".join([en(2), en(5), content.END_EN]),  # hỏi lại riêng cảnh 2 và 5
                ]
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return name
            def ask(self, text, status, stop, email="", label=""):
                self.sent.append(text)
                return self.answers.pop(0)

        session = Session()
        lines = [f"Bối cảnh {n}: cảnh {n}" for n in range(1, 6)]
        result = gemini_web.run_english(session, lines, lambda _: None, threading.Event(), model="Pro", structured=False)
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["english"][1], "English prompt for scene 2, wide shot, a person walks slowly.")
        self.assertEqual(result["rounds"], 4)
        self.assertIn("Continue with scene 4", session.sent[1])
        self.assertIn("2. Bối cảnh 2", session.sent[3])
        self.assertIn("5. Bối cảnh 5", session.sent[3])
        self.assertNotIn("1. Bối cảnh 1", session.sent[3])

    def test_still_missing_after_retries_is_reported(self):
        from runner import gemini_web

        class Session:
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return ""
            def ask(self, text, status, stop, email="", label=""):
                return "1. A long English prompt for the first scene only.\n" + content.END_EN

        result = gemini_web.run_english(Session(), ["A", "B"], lambda _: None, threading.Event(), structured=False)
        # Hỏi lại 1 lần không sửa được cảnh nào -> dừng, không gửi lặp thêm.
        self.assertEqual((result["missing"], result["english"][1], result["rounds"]), ([2], "", 2))

    def test_unreadable_first_answer_stops_without_resending(self):
        from runner import gemini_web

        class Session:
            sent = 0
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return ""
            def ask(self, text, status, stop, email="", label=""):
                Session.sent += 1
                return "Đang suy nghĩ về video của bạn…"
            def dump(self, reason, answer=""):
                return "data/profiles/gemini-debug.txt"

        with self.assertRaisesRegex(gemini_web.GeminiWebError, "gemini-debug.txt"):
            gemini_web.run_english(Session(), ["A", "B"], lambda _: None, threading.Event())
        self.assertEqual(Session.sent, 1)
        with self.assertRaisesRegex(gemini_web.GeminiWebError, "không gửi lại"):
            gemini_web.run_conversation(Session(), "Yêu cầu", lambda _: None, threading.Event())
        self.assertEqual(Session.sent, 2)


class GeminiAskWaitTests(unittest.TestCase):
    """ask() chỉ trả về khi Gemini thật sự viết xong, không trả về phần đang suy nghĩ."""

    def test_waits_through_thinking_until_finished(self):
        from unittest.mock import patch
        from runner import gemini_web

        clock = {"now": 0.0}
        states = iter(
            [{"count": 0, "busy": False}] * 3                       # wait_ready, wait_idle, wait_ready trước khi gửi
            + [{"count": 0, "busy": False}]                          # before
            + [{"count": 1, "busy": True, "text": ""}] * 3          # đang suy nghĩ
            + [{"count": 1, "busy": False, "done": False, "text": "Đang phân tích"}] * 4  # đứng yên 8s nhưng chưa xong
            + [{"count": 1, "busy": True, "text": "1. Wide shot"}] * 2
            + [{"count": 1, "busy": False, "done": True, "text": "1. Wide shot of a beach.\nEND OF LIST"}] * 5)

        class Driver:
            current_url = "https://gemini.google.com/app"
            def execute_script(self, script, *args):
                if script is gemini_web.JS_STATE:
                    return next(states)
                if script is gemini_web.JS_FIND_INPUT:
                    return object()
                if script is gemini_web.JS_SEND:
                    return True

        session = gemini_web.GeminiWebSession.__new__(gemini_web.GeminiWebSession)
        session.driver = Driver()
        session.fill = lambda field, text, before: {"ok": True}
        fake_time = type("T", (), {"monotonic": staticmethod(lambda: clock["now"]),
                                   "sleep": staticmethod(lambda s: clock.__setitem__("now", clock["now"] + s))})
        with patch.object(gemini_web, "time", fake_time):
            answer = session.ask("Yêu cầu", lambda _: None, threading.Event())
        self.assertEqual(answer, "1. Wide shot of a beach.\nEND OF LIST")


class GeminiListFormatTests(unittest.TestCase):
    """Gemini thật hiển thị danh sách bằng <ol><li>: chữ đọc được không có số thứ tự (lỗi người dùng gửi về)."""

    REAL = """A vertical off-white limestone cliff face with natural fissures above a loose scree slope with sparse green grass tufts under crystal-clear natural daylight, framed horizontally in a medium close-up as the camera tilts gently upward along the rock wall, 16:9 aspect ratio.

A rugged vertical limestone cliff strewn with scree under angled natural sunlight accentuating the coarse stone texture, captured in a horizontal close-up at character eye level, 16:9 aspect ratio.

A narrow horizontal ledge suspended across a colossal vertical stone wall under bright daylight accentuating each vertical fissure, framed in an extreme wide horizontal shot, 16:9 aspect ratio.
END OF LIST"""

    def test_unnumbered_paragraphs_map_to_scenes_in_order(self):
        found = content.parse_english(self.REAL, 3)
        self.assertEqual(content.missing_english(found, 3), [])
        self.assertTrue(found[3].startswith("A narrow horizontal ledge"))

    def test_unnumbered_retry_maps_to_requested_scenes(self):
        found = content.parse_english(self.REAL.split("\n\n")[1], 5, [4])
        self.assertEqual(list(found), [4])

    def test_lead_in_and_end_lines_are_not_prompts(self):
        found = content.parse_english("Here are the prompts:\n\n" + self.REAL, 3)
        self.assertTrue(found[1].startswith("A vertical off-white"))

    def test_page_script_numbers_list_items(self):
        from runner import gemini_web
        self.assertIn("querySelectorAll('li, p", gemini_web.JS_STATE)
        self.assertIn("getAttribute('start'", gemini_web.JS_STATE)


class ChatGPTEngineTests(unittest.TestCase):
    def test_chatgpt_session_uses_own_page_scripts_and_name(self):
        from runner import chatgpt_web, gemini_web
        self.assertEqual(chatgpt_web.ChatGPTWebSession.NAME, "ChatGPT")
        self.assertEqual(chatgpt_web.ChatGPTWebSession.URL, "https://chatgpt.com/")
        self.assertIsNot(chatgpt_web.ChatGPTWebSession.JS_STATE, gemini_web.GeminiWebSession.JS_STATE)
        self.assertIn('data-message-author-role="assistant"', chatgpt_web.JS_STATE)

    def test_runner_opens_chatgpt_login_in_its_own_profile(self):
        from unittest.mock import patch
        from runner import chatgpt_web, gemini_web
        launched = []
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(gemini_web, "chrome_executable", return_value="chrome.exe"):
            runner = chatgpt_web.chatgpt_runner(Path(folder) / "chatgpt-web")
            runner.open_login("", lambda args: launched.append(args) or type("P", (), {"poll": lambda self: 0})())
            self.assertIn(f"--user-data-dir={Path(folder) / 'chatgpt-web'}", launched[0])
            self.assertEqual(launched[0][-1], "https://chatgpt.com/auth/login")
            self.assertIn("ChatGPT", runner.snapshot()["status"])
            runner.close()

    def test_logged_out_page_asks_to_sign_in(self):
        from runner import chatgpt_web, gemini_web

        class Driver:
            def execute_script(self, script, *args):
                if script is chatgpt_web.JS_STATE:
                    return {"loggedOut": True}
                return object()

        session = chatgpt_web.ChatGPTWebSession.__new__(chatgpt_web.ChatGPTWebSession)
        session.driver = Driver()
        with self.assertRaisesRegex(gemini_web.GeminiWebError, "Đăng nhập ChatGPT"):
            session.wait_ready(lambda _: None, threading.Event(), timeout=5)

    def test_english_run_endpoint_routes_to_chatgpt(self):
        from runner import chatgpt_web, gemini_web
        store = Store(Path(tempfile.mkdtemp()) / "t.db")
        engine = type("Idle", (), {"snapshot": lambda self: {}, "changed": threading.Event()})()
        server = AppServer(0, store, engine)
        line = "1. A wide shot of a snowy cliff at dawn, 16:9 aspect ratio.\n" + content.END_EN

        class Session:
            NAME = "ChatGPT"
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
                return name
            def ask(self, text, status, stop, email="", label=""):
                return line

        server.chatgpt_web.close()
        server.chatgpt_web = chatgpt_web.chatgpt_runner(Path(tempfile.mkdtemp()) / "chatgpt-web", Session)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("POST", "/api/content/english-run", body=json.dumps(
                {"engine": "chatgpt", "text": "Bối cảnh 1: Vách đá"}),
                headers={"Content-Type": "application/json", "Origin": server.origin, "X-Runner-Key": server.secret})
            reply = json.loads(connection.getresponse().read())
            self.assertEqual(reply["engine"], "chatgpt")
            for _ in range(50):
                if not server.chatgpt_web.snapshot()["busy"]:
                    break
                threading.Event().wait(0.05)
            result = server.chatgpt_web.snapshot()["result"]
            self.assertEqual(result["english"], ["A wide shot of a snowy cliff at dawn, 16:9 aspect ratio."])
            self.assertEqual(server.gemini_web.snapshot()["id"], "")  # Gemini không bị dùng
        finally:
            server.shutdown()
            server.server_close()
            server.gemini_web.close()
            server.chatgpt_web.close()


class TakeNowTests(unittest.TestCase):
    def test_take_now_returns_visible_answer_while_tool_still_waits(self):
        from unittest.mock import patch
        from runner import gemini_web
        take = threading.Event()
        states = iter([{"count": 0, "busy": False}] * 4 + [{"count": 1, "busy": True, "text": "1. A long English prompt.\nEND OF LIST"}] * 50)

        class Driver:
            current_url = "https://chatgpt.com/"
            def execute_script(self, script, *args):
                if script is gemini_web.JS_STATE:
                    state = next(states)
                    if state.get("count"):
                        take.set()  # người dùng bấm "Lấy kết quả ngay" khi tool vẫn tưởng đang viết
                    return state
                if script is gemini_web.JS_FIND_INPUT:
                    return object()
                if script is gemini_web.JS_SEND:
                    return True

        session = gemini_web.GeminiWebSession.__new__(gemini_web.GeminiWebSession)
        session.driver, session.take_now = Driver(), take
        session.fill = lambda field, text, before: {"ok": True}
        with patch.object(gemini_web.time, "sleep", lambda _: None):
            self.assertEqual(session.ask("Yêu cầu", lambda _: None, threading.Event()), "1. A long English prompt.\nEND OF LIST")

    def test_take_now_requires_running_job(self):
        from runner import gemini_web
        with tempfile.TemporaryDirectory() as folder:
            runner = gemini_web.GeminiWebRunner(Path(folder))
            with self.assertRaisesRegex(ValueError, "không có yêu cầu"):
                runner.take_now()
            runner.close()

    def test_chatgpt_busy_ignores_unrelated_stop_buttons(self):
        from runner import chatgpt_web
        self.assertNotIn("|dừng/i", chatgpt_web.JS_STATE)
        self.assertIn('data-turn="assistant"', chatgpt_web.JS_STATE)


class ChatGPT2026DomTests(unittest.TestCase):
    """Cấu trúc trang ChatGPT thật (lấy từ file chẩn đoán người dùng gửi, 11/2026)."""

    def test_page_script_targets_new_chatgpt_markup(self):
        from runner import chatgpt_web
        for marker in ('[data-content-search-unit-key$=":assistant"]', 'data-markdown-text-style="assistant-message"',
                       "data-talvt-turn-state", 'form[data-chatgpt-composer] .ProseMirror',
                       'data-composer-navigation-target="reasoning"'):
            self.assertIn(marker, chatgpt_web.JS_STATE + chatgpt_web.JS_FIND_INPUT + chatgpt_web.JS_OPEN_MODEL_MENU)

    def test_answer_text_from_real_chatgpt_reply_is_parsed(self):
        text = ("1. Ultra-photorealistic BBC-style wildlife documentary footage on a steep limestone mountainside "
                "in the Himalayas, 16:9 aspect ratio.\n2. Ultra-photorealistic BBC-style wildlife documentary footage "
                "on the same rugged Himalayan limestone mountainside, 16:9 aspect ratio.\nEND OF LIST")
        found = content.parse_english(text, 2)
        self.assertEqual(content.missing_english(found, 2), [])



class StructuredPromptTests(unittest.TestCase):
    LINE = ("Setting: A steep limestone mountainside, bright daylight, a light brown mother goat; Camera: Static eye-level "
            "close-up; Character behavior: Calm, focused on grazing; Action: She chews fresh grass between rocks; "
            "Style: Photorealistic documentary; 16:9 aspect ratio.")

    def test_fields_are_split_from_labelled_prompt(self):
        fields = content.parse_structured(self.LINE)
        self.assertEqual(fields["camera"], "Static eye-level close-up")
        self.assertEqual(fields["behavior"], "Calm, focused on grazing")
        self.assertEqual(fields["action"], "She chews fresh grass between rocks")
        self.assertEqual(fields["style"], "Photorealistic documentary")
        self.assertTrue(content.structured_complete(self.LINE))
        self.assertFalse(content.structured_complete("Setting: A cliff; Camera: Wide; Action: A leopard runs."))
        bold = "Setting: A cliff; Camera angle: Low angle; Behaviour: Alert; Action: Runs; 9:16 aspect ratio"
        self.assertEqual(content.parse_structured(bold)["behavior"], "Alert")

    def test_request_asks_for_labels_only_when_structured(self):
        self.assertIn("Character behavior:", content.english_request(["A"]))
        self.assertNotIn("Character behavior:", content.english_request(["A"], structured=False))
        self.assertNotIn("aspect ratio", content.english_request(["A"], aspect="none"))

    def test_scene_missing_a_field_is_requested_again(self):
        from runner import gemini_web
        good = lambda n: f"{n}. {self.LINE}"

        class Session:
            def __init__(self):
                self.sent = []
                self.answers = [good(1) + "\n2. Setting: A ledge; Camera: Wide shot; Action: The leopard walks slowly along it.\n"
                                + content.END_EN, good(2) + "\n" + content.END_EN]
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return ""
            def ask(self, text, status, stop, email="", label=""):
                self.sent.append(text)
                return self.answers.pop(0)

        session = Session()
        result = gemini_web.run_english(session, ["Bối cảnh 1: A", "Bối cảnh 2: B"], lambda _: None, threading.Event())
        self.assertEqual(result["missing"], [])
        self.assertTrue(content.structured_complete(result["english"][1]))
        self.assertIn("2. Bối cảnh 2: B", session.sent[1])
        self.assertNotIn("1. Bối cảnh 1: A", session.sent[1])

    def test_queue_takes_fields_from_prompt_itself(self):
        import uuid
        store = Store(Path(tempfile.mkdtemp()) / "t.db")
        store.enqueue([self.LINE], 1, uuid.uuid4().hex, sources=["Bối cảnh 1: Khác, góc quay: Khác, hành vi nhân vật: Khác, "
                                                                 "Nội dung hành động nhân vật: Khác;"])
        job = store.snapshot()["jobs"][0]
        self.assertEqual(store.job_detail(job["id"])["details"]["camera"], "Static eye-level close-up")



class SecondsTests(unittest.TestCase):
    def test_default_duration_is_15_seconds(self):
        self.assertEqual(content.DEFAULT_SECONDS, 15)
        self.assertIn("15 giây", content.SCENE_RULES)
        request = content.english_request(["Bối cảnh 1: A"])
        self.assertIn("about 15 seconds", request)
        self.assertIn("; 15 seconds; 16:9 aspect ratio.", request)
        self.assertIn("about 8 seconds", content.english_request(["A"], seconds=8))

    def test_duration_tail_is_not_part_of_a_field(self):
        line = ("Setting: A cliff; Camera: Wide shot; Character behavior: Alert; Action: The leopard runs; "
                "Style: Photorealistic; 15 seconds; 16:9 aspect ratio.")
        self.assertEqual(content.parse_structured(line)["style"], "Photorealistic")
        self.assertEqual(content.parse_structured("Setting: A; Camera: B; Character behavior: C; Action: D, 15 seconds.")["action"], "D")
