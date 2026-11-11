"""Gemini qua API key: tải video bằng Files API, hội thoại nhiều lượt, xem link YouTube, xử lý lỗi; quy trình không mở Chrome."""
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from runner import gemini_api


class Response:
    def __init__(self, body, headers=None):
        self.body, self.headers = json.dumps(body).encode(), headers or {}

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeGoogle:
    """Giả máy chủ Gemini API; ghi lại các yêu cầu."""
    def __init__(self, answers=("Trả lời 1",), errors=()):
        self.calls, self.answers, self.errors = [], list(answers), list(errors)

    def __call__(self, request, timeout, handlers=()):
        url, headers = request.full_url, {k.lower(): v for k, v in request.header_items()}
        data = request.data
        body = data.read() if hasattr(data, "read") else data
        self.calls.append((request.get_method(), url, headers, body))
        if url.endswith("/upload/v1beta/files"):
            return Response({}, {"X-Goog-Upload-URL": "https://generativelanguage.googleapis.com/upload/session/abc"})
        if "/upload/session/" in url:
            return Response({"file": {"name": "files/f1", "uri": "https://generativelanguage.googleapis.com/v1beta/files/f1",
                                      "mimeType": "video/mp4", "state": "PROCESSING"}})
        if url.endswith("/v1beta/files/f1") and request.get_method() == "GET":
            return Response({"name": "files/f1", "state": "ACTIVE", "mimeType": "video/mp4"})
        if ":generateContent" in url:
            if self.errors:
                code, message = self.errors.pop(0)
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(json.dumps({"error": {"message": message}}).encode()))
            text = self.answers.pop(0)
            return Response({"candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
                {"text": "nghĩ", "thought": True}, {"text": text, "thoughtSignature": "sig"}]}}]})
        if "/v1beta/models?" in url:
            return Response({"models": [
                {"name": "models/gemini-4-carbon", "displayName": "Gemini 4 Carbon", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]}]})
        return Response({})

    def bodies(self):
        return [json.loads(body) for method, url, headers, body in self.calls if ":generateContent" in url]


class GeminiAPISessionTests(unittest.TestCase):
    def run_with(self, google, steps):
        with mock.patch.object(gemini_api, "opener", return_value=google), mock.patch.object(gemini_api, "RETRY_WAIT", (0, 0, 0)):
            return steps()

    def test_video_uploaded_once_then_multi_turn_conversation_keeps_history(self):
        google = FakeGoogle(answers=("Cảnh 1", "Cảnh 2"))
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-4-carbon", "high")
        class Quick(threading.Event):
            def wait(self, timeout=None):
                return self.is_set()  # không đợi thật khi chờ Google xử lý video
        stop, seen = Quick(), []
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "video-goc.mp4"
            video.write_bytes(b"0" * 1000)

            def steps():
                session.new_chat()
                session.attach_images([video], seen.append, stop)
                return session.ask("Viết cảnh", seen.append, stop), session.ask("Viết tiếp", seen.append, stop)
            first, second = self.run_with(google, steps)
        self.assertEqual((first, second), ("Cảnh 1", "Cảnh 2"))  # bỏ phần suy nghĩ của model
        upload = [c for c in google.calls if "/upload/session/" in c[1]][0]
        self.assertNotIn("x-goog-api-key", upload[2])  # địa chỉ tải lên đã có mã phiên: không gửi khóa
        self.assertEqual(upload[2]["x-goog-upload-command"], "upload, finalize")
        one, two = google.bodies()
        self.assertEqual(one["contents"][0]["parts"][0]["fileData"]["fileUri"], "https://generativelanguage.googleapis.com/v1beta/files/f1")
        self.assertEqual(one["generationConfig"]["thinkingConfig"], {"thinkingLevel": "high"})
        self.assertEqual([c["role"] for c in two["contents"]], ["user", "model", "user"])
        self.assertEqual(two["contents"][1]["parts"][1]["thoughtSignature"], "sig")  # giữ chữ ký suy nghĩ cho lượt sau
        self.assertEqual(len([c for c in google.calls if c[1].endswith("/upload/v1beta/files")]), 1)
        generate = [c for c in google.calls if ":generateContent" in c[1]][0]
        self.assertEqual(generate[2]["x-goog-api-key"], "AIzaTESTKEY")
        self.assertTrue(generate[1].startswith("https://generativelanguage.googleapis.com/v1beta/models/gemini-4-carbon:"))

    def test_youtube_link_is_sent_as_video_when_no_file(self):
        google = FakeGoogle()
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-4-carbon")
        self.run_with(google, lambda: session.ask("Phân tích https://www.youtube.com/watch?v=abcDEF12345 giúp tôi",
                                                  lambda t: None, threading.Event()))
        parts = google.bodies()[0]["contents"][0]["parts"]
        self.assertEqual(parts[0]["fileData"]["fileUri"], "https://www.youtube.com/watch?v=abcDEF12345")
        self.assertNotIn("thinkingConfig", google.bodies()[0]["generationConfig"])  # "Mặc định của model"

    def test_unsupported_thinking_level_retries_without_it_and_busy_server_is_retried(self):
        google = FakeGoogle(errors=[(400, "thinking_level is not supported for this model"), (503, "overloaded")])
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-2.5-flash", "low")
        answer = self.run_with(google, lambda: session.ask("Hỏi", lambda t: None, threading.Event()))
        self.assertEqual(answer, "Trả lời 1")
        bodies = google.bodies()
        self.assertIn("thinkingConfig", bodies[0]["generationConfig"])
        self.assertNotIn("thinkingConfig", bodies[-1]["generationConfig"])

    def test_bad_key_is_reported_clearly(self):
        google = FakeGoogle(errors=[(401, "API key not valid")])
        session = gemini_api.GeminiAPISession("AIzaBADKEY000000000000", "gemini-4-carbon")
        with self.assertRaises(gemini_api.GeminiAPIError) as caught:
            self.run_with(google, lambda: session.ask("Hỏi", lambda t: None, threading.Event()))
        self.assertIn("không chấp nhận khóa", str(caught.exception))
        self.assertNotIn("AIzaBADKEY", str(caught.exception))

    def test_upload_url_on_another_host_is_refused(self):
        with self.assertRaises(gemini_api.GeminiAPIError):
            gemini_api.http("POST", "https://evil.example.com/upload", "AIzaTESTKEY", payload={})

    def test_model_list_keeps_text_models_only(self):
        models = self.run_with(FakeGoogle(), lambda: gemini_api.list_models("AIzaTESTKEY"))
        self.assertEqual(models, [{"id": "gemini-4-carbon", "name": "Gemini 4 Carbon", "thinking": False}])


class RunnerUsesAPITests(unittest.TestCase):
    def test_content_pipeline_runs_on_api_without_opening_chrome(self):
        from runner import content, gemini_web
        line = lambda n: f"Bối cảnh {n}: Rừng, góc quay: Toàn cảnh, hành vi nhân vật: Rình, Nội dung hành động nhân vật: Hổ chạy {n};"
        opened = []

        class Session(gemini_api.GeminiAPISession):
            def ask(self, text, status, stop, email="", timeout=1200, label=""):
                return line(1) + "\n" + gemini_web.END_MARK

        with tempfile.TemporaryDirectory() as folder:
            runner = gemini_web.GeminiWebRunner(Path(folder) / "profile", lambda profile: opened.append(profile))
            runner.api_session = lambda: Session("AIzaTESTKEY", "gemini-4-carbon")
            runner.login_window_open = lambda: True  # cửa sổ Chrome đang mở cũng không cản chế độ API
            runner.start("generate", content.web_prompt("generate", "Hổ săn mồi", "", "vi", None))
            for _ in range(200):
                if not runner.snapshot()["busy"]:
                    break
                threading.Event().wait(0.05)
            runner.close()
        snap = runner.snapshot()
        self.assertEqual(snap["error"], "")
        self.assertEqual(len(snap["result"]["scenes"]), 1)
        self.assertEqual(opened, [])  # không mở Chrome


if __name__ == "__main__":
    unittest.main()
