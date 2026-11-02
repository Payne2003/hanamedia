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

    def test_key_is_required_saved_and_never_returned(self):
        status, data = self.post("/api/content/generate", {"brief": "Chia cảnh"})
        self.assertEqual(status, 400)
        self.assertIn("khóa API", data["error"])
        self.assertEqual(self.post("/api/content/settings", {"api_key": KEY, "model": "gemini-2.5-pro"})[0], 200)
        state = self.state()
        self.assertEqual(state["content"], {"version": content.VERSION, "configured": True, "model": "gemini-2.5-pro"})
        self.assertNotIn(KEY, json.dumps(state))
        status, data = self.post("/api/content/generate", {"brief": "Chia cảnh", "video_url": ""})
        self.assertEqual(status, 200)
        self.assertEqual(data["prompts"], content.format_prompts([SCENE]))
        self.assertIn("/gemini-2.5-pro:", self.calls[0][0])
        self.post("/api/content/settings", {"clear_key": True, "model": ""})
        self.assertFalse(self.state()["content"]["configured"])


if __name__ == "__main__":
    unittest.main()
