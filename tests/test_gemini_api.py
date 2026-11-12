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
        with mock.patch.object(gemini_api, "opener", return_value=google), mock.patch.object(gemini_api, "RETRY_WAIT", (0, 0, 0)), \
                mock.patch.object(gemini_api, "OVERLOAD_WAIT", (0, 0)), \
                mock.patch.object(gemini_api, "BUSY_WAIT", (0, 0, 0)):
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

    def test_overloaded_model_switches_to_fallback_quickly(self):
        google = FakeGoogle(errors=[(503, "The model is overloaded")] * 3)
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-3-pro-preview")
        seen = []
        answer = self.run_with(google, lambda: session.ask("Hỏi", seen.append, threading.Event()))
        self.assertEqual(answer, "Trả lời 1")
        urls = [url for method, url, headers, body in google.calls if ":generateContent" in url]
        self.assertEqual(len(urls), 4)  # 1 lần đầu + 2 lần thử lại nhanh, rồi đổi model
        self.assertIn("gemini-flash-latest", urls[-1])
        self.assertTrue(any("chuyển sang gemini-flash-latest" in text for text in seen))

    def test_every_model_overloaded_rotates_then_reports_google_side(self):
        google = FakeGoogle(errors=[(503, "The model is overloaded")] * 100)
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-3-pro-preview")
        seen = []
        with self.assertRaises(gemini_api.GeminiAPIError) as caught:
            self.run_with(google, lambda: session.ask("Hỏi", seen.append, threading.Event()))
        self.assertIn("quá tải ở mọi model", str(caught.exception))
        models = {url.split("/models/")[1].split(":")[0] for m, url, h, b in google.calls if ":generateContent" in url}
        self.assertEqual(models, {"gemini-3-pro-preview", *gemini_api.GeminiAPISession.OVERLOAD_FALLBACKS})
        self.assertTrue(any("phía Google" in text for text in seen))

    def test_missing_fallback_model_is_skipped(self):
        google = FakeGoogle(errors=[(503, "overloaded"), (503, "overloaded"), (503, "overloaded"), (404, "model not found")])
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-3-pro-preview")
        self.assertEqual(self.run_with(google, lambda: session.ask("Hỏi", lambda t: None, threading.Event())), "Trả lời 1")
        self.assertEqual(session.model, "gemini-2.5-flash")

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


class QuotaTests(unittest.TestCase):
    def quota_error(self, metric):
        body = {"error": {"message": "You exceeded your current quota", "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": metric}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}}
        return lambda url: urllib.error.HTTPError(url, 429, "x", {}, io.BytesIO(json.dumps(body).encode()))

    def run_session(self, errors, model="gemini-3.1-pro-preview"):
        calls = []

        def fake(request, timeout, handlers=()):
            calls.append(request.full_url)
            if errors:
                raise errors.pop(0)(request.full_url)
            return Response({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "OK"}]}}]})
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", model)
        waits, status = [], []

        class Stop(threading.Event):
            def wait(self, timeout=None):
                waits.append(timeout)
                return False
        with mock.patch.object(gemini_api, "opener", return_value=fake):
            answer = session.ask("Hỏi", status.append, Stop())
        return answer, calls, waits, status, session

    def test_daily_quota_switches_to_flash_instead_of_retrying(self):
        answer, calls, waits, status, session = self.run_session([self.quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")])
        self.assertEqual(answer, "OK")
        self.assertIn("gemini-3.1-pro-preview:", calls[0])
        self.assertIn("gemini-flash-latest:", calls[1])
        self.assertEqual(session.model, "gemini-flash-latest")
        self.assertNotIn(7, waits)  # không đợi gửi lại vô ích
        self.assertIn("hết hạn mức trong ngày", " ".join(status))

    def test_per_minute_quota_waits_as_google_suggests(self):
        answer, calls, waits, status, _ = self.run_session([self.quota_error("GenerateContentInputTokensPerModelPerMinute-FreeTier")])
        self.assertEqual(answer, "OK")
        self.assertEqual(len(calls), 2)
        self.assertIn(9, waits)  # retryDelay 7s + 2

    def test_all_daily_quotas_used_up_gives_clear_message(self):
        errors = [self.quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")] * 3
        with self.assertRaises(gemini_api.GeminiAPIError) as caught:
            self.run_session(errors)
        self.assertIn("Đợi hạn mức đặt lại", str(caught.exception))


class ChunkedTests(unittest.TestCase):
    def test_video_is_sent_part_by_part_and_results_joined_in_order(self):
        from runner import gemini_web, reverse_engineer
        windows = [{"start": i * 10.0, "end": i * 10.0 + 10, "angles": [(0.0, 5.0), (5.0, 10.0)]} for i in range(14)]
        sent = []

        class Session(gemini_api.GeminiAPISession):
            def attach_images(self, files, status, stop, email=""):
                sent.append([Path(f).name for f in files])

            def ask(self, text, status, stop, email="", timeout=1200, label=""):
                found = re.search(r"gồm đúng (\d+) cảnh", text)
                if not found:
                    return ""  # câu hỏi kiểm tra thêm của tool
                count = int(found.group(1))
                part = len(sent)
                if part == 3:
                    raise gemini_api.GeminiAPIError("API key đã hết hạn mức trong ngày.")
                return "\n".join(f"Bối cảnh {n}: Đoạn {part} cảnh {n}, góc quay: 0–3s toàn cảnh; 3–5s trung cảnh; 5–10s cận cảnh, "
                                 f"hành vi nhân vật: Chạy, Nội dung hành động nhân vật: Chạy {n};" for n in range(1, count + 1)) \
                    + "\n" + gemini_web.END_MARK

        import re
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(reverse_engineer, "cut_clip", side_effect=lambda src, start, length, target, stop=None: Path(target)), \
                mock.patch.object(reverse_engineer, "contact_sheets", return_value=[]):
            result = gemini_web.run_chunked(Session("AIzaTESTKEY", "gemini-flash-latest"), "Yêu cầu", Path(folder) / "v.mp4",
                                            windows, lambda t: None, threading.Event(), folder=Path(folder))
        self.assertEqual(sent, [["doan-1.mp4"], ["doan-2.mp4"], ["doan-3.mp4"]])  # mỗi đoạn một yêu cầu riêng
        self.assertEqual(len(result["scenes"]), 12)  # đoạn 3 hết hạn mức: giữ 12 cảnh của 2 đoạn đầu
        self.assertIn("Đoạn 1 cảnh 1", result["scenes"][0]["setting"])
        self.assertIn("Đoạn 2 cảnh 1", result["scenes"][6]["setting"])
        self.assertIn("Dừng ở đoạn 3/3 (từ cảnh 13)", " ".join(result["issues"]))


class TokenSavingTests(unittest.TestCase):
    def test_video_sent_once_low_res_and_usage_counted(self):
        google = FakeGoogle(answers=("Cảnh 1", "Cảnh 2"))
        real_call = google.__call__

        def with_usage(request, timeout, handlers=()):
            response = real_call(request, timeout, handlers)
            if ":generateContent" in request.full_url:
                body = json.loads(response.body)
                body["usageMetadata"] = {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 50}
                response.body = json.dumps(body).encode()
            return response
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-4-carbon")

        class Quick(threading.Event):
            def wait(self, timeout=None):
                return self.is_set()
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(gemini_api, "opener", return_value=with_usage):
            video = Path(folder) / "doan.mp4"
            video.write_bytes(b"video")
            session.motion_plan = [(0, 10, 1), (10, 20, 5)]
            session.attach_images([video], lambda t: None, Quick())
            session.ask("Phân tích", lambda t: None, Quick())
            session.ask("Viết tiếp", lambda t: None, Quick())
        first, second = google.bodies()
        videos = [p for p in first["contents"][0]["parts"] if "fileData" in p]
        self.assertEqual(len(videos), 2)  # hai khúc của cùng một video
        self.assertEqual(videos[0]["mediaResolution"], {"level": "MEDIA_RESOLUTION_LOW"})
        self.assertEqual(videos[1]["videoMetadata"]["fps"], 5.0)
        # Lượt sau: không gửi lại video, chỉ chữ.
        self.assertFalse(any("fileData" in p for turn in second["contents"] for p in turn["parts"]))
        self.assertEqual(session.usage, {"input": 2000, "output": 500, "requests": 2})

    def test_model_rejecting_low_res_retries_without_it(self):
        google = FakeGoogle(errors=[(400, 'Invalid JSON payload received. Unknown name "mediaResolution"')])
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-4-carbon")
        session.pending = [{"fileData": {"fileUri": "u", "mimeType": "video/mp4"}, "mediaResolution": {"level": "MEDIA_RESOLUTION_LOW"}}]
        with mock.patch.object(gemini_api, "opener", return_value=google):
            self.assertEqual(session.ask("Hỏi", lambda t: None, threading.Event()), "Trả lời 1")
        self.assertNotIn("mediaResolution", google.bodies()[-1]["contents"][0]["parts"][0])
        self.assertFalse(session.low_res)


class OverloadAlgorithmTests(unittest.TestCase):
    def patched(self):
        return (mock.patch.object(gemini_api, "RETRY_WAIT", (0, 0, 0)), mock.patch.object(gemini_api, "OVERLOAD_WAIT", (0, 0)),
                mock.patch.object(gemini_api, "BUSY_WAIT", (0, 0, 0)))

    def test_timeout_is_retried_instead_of_stopping(self):
        calls = []

        def slow_then_ok(request, timeout, handlers=()):
            calls.append(timeout)
            if len(calls) == 1:
                raise TimeoutError("timed out")
            return Response({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "OK"}]}}]})
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-4-carbon")
        a, b, c = self.patched()
        with a, b, c, mock.patch.object(gemini_api, "opener", return_value=slow_then_ok):
            self.assertEqual(session.ask("Hỏi", lambda t: None, threading.Event()), "OK")
        self.assertLessEqual(calls[0], gemini_api.REQUEST_TIMEOUT)  # không còn chờ 20 phút mỗi lần

    def test_repeated_server_error_with_video_options_retries_simple_video_first(self):
        google = FakeGoogle(errors=[(500, "Internal error")] * 3)
        session = gemini_api.GeminiAPISession("AIzaTESTKEY", "gemini-4-carbon")
        video = {"fileData": {"fileUri": "u", "mimeType": "video/mp4"}}
        session.pending = [dict(video, videoMetadata={"startOffset": "0s", "endOffset": "10s"}, mediaResolution={"level": "LOW"}),
                           dict(video, videoMetadata={"startOffset": "10s", "endOffset": "20s", "fps": 5.0})]
        a, b, c = self.patched()
        with a, b, c, mock.patch.object(gemini_api, "opener", return_value=google):
            self.assertEqual(session.ask("Hỏi", lambda t: None, threading.Event()), "Trả lời 1")
        last = google.bodies()[-1]["contents"][0]["parts"]
        self.assertEqual(last[0], video)  # một video, không tham số
        self.assertEqual(len([p for p in last if "fileData" in p]), 1)
        self.assertEqual(session.model, "gemini-4-carbon")  # chưa phải đổi model
        self.assertTrue(session.simple_media)

    def test_chunk_failing_everywhere_is_split_in_half(self):
        from runner import gemini_web
        windows = [{"start": i * 3.0, "end": i * 3.0 + 3, "angles": [(0.0, 3.0)], "shot": True} for i in range(8)]
        seen, calls = [], []

        def fake_conversation(session, prompt, status, stop, email, model, review, files=(), windows=None):
            calls.append(len(windows))
            if len(windows) > 4:
                raise gemini_api.Overloaded("quá tải")
            return {"scenes": [{"setting": "s", "camera": "c", "behavior": "b", "action": f"a{n}"} for n in range(len(windows))],
                    "issues": [], "rounds": 1}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(gemini_web, "run_conversation", side_effect=fake_conversation), \
                mock.patch.object(gemini_web, "SPLIT_PAUSE", 0), \
                mock.patch("runner.reverse_engineer.cut_clip", side_effect=lambda src, o, l, target, stop=None: target), \
                mock.patch("runner.reverse_engineer.contact_sheets", return_value=[]):
            session = types_session()
            result = gemini_web.run_chunked(session, "Yêu cầu", Path(folder) / "v.mp4", windows, seen.append,
                                            threading.Event(), folder=folder)
        self.assertEqual(calls, [8, 4, 4])
        self.assertEqual(len(result["scenes"]), 8)
        self.assertTrue(any("chia đôi" in text for text in seen))


def types_session():
    class Session:
        busy_rounds = None
    return Session()
