"""ChatGPT API key + Quality Control: Viết prompt → Kiểm tra → Tự sửa → Kiểm tra lại → Khóa prompt."""
import io
import json
import threading
import unittest
import urllib.error
from unittest import mock

from runner import content, gemini_web, openai_api

GOOD = ("SETTING: Snowy tundra at dawn; CHARACTERS: two muskoxen; CAMERA: 0–3s close-up of horns, 3–7s wide shot, "
        "7–10s low angle tracking; CHARACTER BEHAVIOR: tense; ACTION: they charge and collide hard; "
        "ENDING FRAME: both stand still; DURATION: 10 seconds")


class CheckerTests(unittest.TestCase):
    def test_each_error_type_is_detected(self):
        self.assertEqual(content.qc_problems(GOOD, 10), [])
        self.assertEqual(content.qc_problems("", 10), ["Prompt trống"])
        self.assertIn("Còn tiếng Việt", " ".join(content.qc_problems(GOOD.replace("dawn", "bình minh trên đồi tuyết trắng"), 10)))
        self.assertIn("thiếu trường ENDING FRAME", " ".join(content.qc_problems(GOOD.replace("ENDING FRAME: both stand still; ", ""), 10)))
        self.assertIn("Gộp nhiều cảnh", " ".join(content.qc_problems("Scene 1: " + GOOD + " Scene 2: a wolf runs", 10)))
        self.assertIn("Sai thời lượng — phải là 10 giây", content.qc_problems(GOOD.replace("10 seconds", "8 seconds"), 10))

    def test_format_and_duration_are_fixed_without_ai(self):
        messy = "**Prompt 3:** " + GOOD.replace("10 seconds", "8 seconds").replace("; ACTION", ";\nACTION")
        fixed = content.tidy_prompt(messy, 10)
        self.assertEqual(content.qc_problems(fixed, 10), [])
        self.assertNotIn("\n", fixed)

    def test_character_master_lists_first_description(self):
        self.assertIn("two muskoxen (first in prompt 1)", content.character_master([GOOD, GOOD]))


class QualityLoopTests(unittest.TestCase):
    def run_loop(self, answers):
        class Session:
            sent = []
            def new_chat(self): pass
            def wait_ready(self, *a, **k): pass
            def choose_model(self, name): return name
            def ask(self, text, status, stop, email="", label=""):
                self.sent.append(text)
                return answers.pop(0) if answers else ""
        session = Session()
        result = gemini_web.run_review(session, [GOOD, GOOD], "", "no music", lambda _: None, threading.Event(), seconds=10)
        return result, session.sent

    def test_bad_prompt_is_fixed_on_second_check(self):
        bad = GOOD.replace("tundra at dawn", "đồi tuyết lúc bình minh có gió lạnh thổi mạnh")
        result, sent = self.run_loop([f"1. {GOOD}\n2. {bad}\n{content.END_EN}", f"2. {GOOD}\n{content.END_EN}"])
        self.assertIn("2. ", sent[1])  # prompt còn tiếng Việt được hỏi lại riêng
        self.assertIn("CHARACTER MASTER", sent[0])
        self.assertEqual(result["violations"], {})
        self.assertTrue(all(p.endswith("LOCK: no music.") for p in result["english"]))

    def test_still_wrong_after_three_fixes_goes_to_needs_check(self):
        bad = "2. " + GOOD.replace("collide hard", "collide hard in slow motion with epic music")
        answers = [f"1. {GOOD}\n{bad}\n{content.END_EN}"] + [bad] * 5
        result, sent = self.run_loop(answers)
        self.assertEqual(len(sent), 1 + gemini_web.REGENERATE_ROUNDS)
        self.assertEqual(result["needs_check"], [2])
        self.assertIn("sửa đúng các lỗi sau", sent[1])  # mỗi lần sửa gửi kèm đúng lỗi của prompt
        self.assertTrue(result["violations"][2])


class Response:
    def __init__(self, body): self.body = json.dumps(body).encode()
    def read(self, limit=-1): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


class OpenAIAPITests(unittest.TestCase):
    def test_conversation_retry_and_key_only_to_openai(self):
        calls, errors = [], [(503, "busy")]

        def fake(request, timeout, handlers=()):
            calls.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
            if errors:
                code, message = errors.pop(0)
                raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(json.dumps({"error": {"message": message}}).encode()))
            return Response({"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": f"answer {len(calls)}"}]}]})
        session = openai_api.OpenAIAPISession("sk-TESTKEY000000000000000", "gpt-5-mini", "high")
        with mock.patch.object(openai_api, "opener", return_value=fake), mock.patch.object(openai_api, "RETRY_WAIT", (0, 0, 0)):
            first = session.ask("Write prompts", lambda t: None, threading.Event())
            second = session.ask("Continue", lambda t: None, threading.Event())
        self.assertEqual((first, second), ("answer 2", "answer 3"))
        url, headers, body = calls[-1]
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer sk-TESTKEY000000000000000")
        self.assertEqual([m["role"] for m in body["input"]], ["user", "assistant", "user"])
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertFalse(body["store"])
        with self.assertRaises(openai_api.OpenAIAPIError):
            openai_api.http("GET", "https://evil.example.com/v1/models", "sk-x")

    def test_quota_error_is_not_retried(self):
        def fake(request, timeout, handlers=()):
            raise urllib.error.HTTPError(request.full_url, 429, "x", {}, io.BytesIO(b'{"error":{"message":"You exceeded your current quota"}}'))
        session = openai_api.OpenAIAPISession("sk-TESTKEY000000000000000", "gpt-5-mini")
        with mock.patch.object(openai_api, "opener", return_value=fake), self.assertRaises(openai_api.HTTPFailure) as caught:
            session.ask("x", lambda t: None, threading.Event())
        self.assertEqual(caught.exception.code, 429)


if __name__ == "__main__":
    unittest.main()


class PacerTests(unittest.TestCase):
    def test_requests_are_spaced_by_rpm_and_cool_down_after_429(self):
        import time
        from runner.ai_api import Pacer
        stop, notes = threading.Event(), []
        start = time.monotonic()
        for _ in range(3):
            Pacer.wait("test-pace", 600, notes.append, stop)  # 600/phút = 0,1 giây một yêu cầu
        self.assertGreaterEqual(time.monotonic() - start, 0.19)
        Pacer.cool_down("test-pace", 0.3)
        before = time.monotonic()
        Pacer.wait("test-pace", 600, notes.append, stop)
        self.assertGreaterEqual(time.monotonic() - before, 0.25)
        Pacer.wait("other-key", 0, notes.append, stop)  # 0 = không giới hạn, khóa khác không bị ảnh hưởng


class BatchAndFallbackTests(unittest.TestCase):
    def test_api_writes_prompts_in_batches_of_ten_with_continuity(self):
        sent = []

        class Session:
            api = True
            def new_chat(self): pass
            def wait_ready(self, *a, **k): pass
            def choose_model(self, name): return name
            def ask(self, text, *a, **k):
                sent.append(text)
                count = text.count("\n") and len([l for l in text.split("\n") if l[:1].isdigit() and ". " in l[:5]])
                return "\n".join(f"{n}. {GOOD}" for n in range(1, 11)) + f"\n{content.END_EN}"

        lines = [f"Cảnh {n}: hổ chạy" for n in range(1, 24)]
        result = gemini_web.run_english(Session(), lines, lambda t: None, threading.Event(), seconds=10)
        self.assertEqual(len(result["english"]), 23)
        self.assertEqual(len([t for t in sent if "MASTER PROMPT 08" in t]), 3)  # 3 nhóm: 10 + 10 + 3
        self.assertIn("CONTINUITY: these scenes", sent[1])
        self.assertNotIn("CONTINUITY: these scenes", sent[0])

    def test_review_batches_keep_numbering(self):
        bad = GOOD.replace("collide hard", "collide hard in slow motion")

        class Session:
            api = True
            def new_chat(self): pass
            def wait_ready(self, *a, **k): pass
            def choose_model(self, name): return name
            def ask(self, text, *a, **k):
                rows = [l for l in text.split("\n") if l[:2].strip(". ").isdigit() and l[1:3].startswith((". ", ".")) or l[:3].rstrip(".").isdigit() and ". " in l[:4]]
                count = len(rows) or 10
                return "\n".join(f"{n}. {bad if n == 2 else GOOD}" for n in range(1, count + 1)) + f"\n{content.END_EN}"

        prompts = [GOOD] * 12
        result = gemini_web.run_review(Session(), prompts, "", "no music", lambda t: None, threading.Event(), seconds=10)
        self.assertEqual(len(result["english"]), 12)
        self.assertIn(2, result["violations"])
        self.assertTrue(all(n <= 12 for n in result["violations"]))

    def test_openai_out_of_credit_switches_to_gemini(self):
        def fake(request, timeout, handlers=()):
            raise urllib.error.HTTPError(request.full_url, 429, "x", {}, io.BytesIO(
                b'{"error":{"message":"You have no credits remaining. Add credits to continue using the API"}}'))
        asked = []

        class Gemini:
            api = True
            history = []
            def ask(self, text, status, stop, email="", timeout=1200, label=""):
                asked.append((text, list(self.history)))
                return "gemini answer"
            def new_chat(self): self.history = []
            def close(self): pass
        session = openai_api.OpenAIAPISession("sk-TESTKEY000000000000000", "gpt-5-mini")
        session.fallback = Gemini
        notes = []
        with mock.patch.object(openai_api, "opener", return_value=fake):
            self.assertEqual(session.ask("Write prompts", notes.append, threading.Event()), "gemini answer")
            self.assertEqual(session.ask("Continue", notes.append, threading.Event()), "gemini answer")
        self.assertEqual([a[0] for a in asked], ["Write prompts", "Continue"])
        self.assertIn("chuyển sang Gemini", " ".join(notes))
