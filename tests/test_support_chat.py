"""Chatbot hỗ trợ: FAQ trước (miễn phí), trợ lý AI có giới hạn lượt/ngày, hết lượt hoặc lỗi → Zalo."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from runner import gemini_api, support_chat
from runner.store import Store


class Reply:
    def __init__(self, body): self.body, self.headers = json.dumps(body).encode(), {}
    def read(self, limit=-1): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


class SupportChatTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name) / "t.db")
        self.calls = []

    def tearDown(self):
        self.folder.cleanup()

    def fake(self, errors=()):
        errors = list(errors)

        def opener(request, timeout, handlers=()):
            self.calls.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
            if errors:
                code = errors.pop(0)
                raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(b'{"error":{"message":"quota"}}'))
            return Reply({"candidates": [{"content": {"parts": [{"text": "Bạn vào Studio rồi bấm Chạy."}]}}]})
        return opener

    def chat(self, key="AIzaOWNERKEY0000000000", allowed=True):
        return support_chat.SupportChat(self.store, key_source=lambda: key, allowed=lambda: allowed)

    def test_faq_answers_free_without_calling_api(self):
        with mock.patch.object(gemini_api, "opener", return_value=self.fake()):
            r = self.chat().ask("Lỗi 429 là gì vậy?")
        self.assertEqual(r["source"], "faq")
        self.assertIn("Số yêu cầu tối đa mỗi phút", r["answer"])
        self.assertEqual((self.calls, r["remaining"]), ([], 20))

    def test_ai_answer_counts_one_and_sends_history_and_knowledge(self):
        with mock.patch.object(gemini_api, "opener", return_value=self.fake()):
            r = self.chat().ask("Làm sao đổi tỉ lệ khung hình video?", [{"role": "user", "text": "chào"},
                                                                         {"role": "model", "text": "Xin chào"}])
        self.assertEqual((r["source"], r["remaining"]), ("ai", 19))
        url, headers, body = self.calls[0]
        self.assertIn("gemini-flash-latest:generateContent", url)
        self.assertEqual(headers["X-goog-api-key"], "AIzaOWNERKEY0000000000")
        self.assertIn("HANA MEDIA", body["system_instruction"]["parts"][0]["text"])
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model", "user"])
        self.assertNotIn("AIzaOWNER", json.dumps(r))  # khóa không bao giờ trả về giao diện

    def test_daily_limit_then_zalo(self):
        self.store.save_setting("support_chat_usage", {"day": self.chat().today(), "count": support_chat.DAILY_LIMIT})
        with mock.patch.object(gemini_api, "opener", return_value=self.fake()):
            r = self.chat().ask("Làm sao đổi tỉ lệ khung hình video?")
        self.assertEqual(r["source"], "zalo")
        self.assertIn("0983105651", r["answer"])
        self.assertEqual(self.calls, [])

    def test_quota_on_both_models_goes_to_zalo_without_counting(self):
        with mock.patch.object(gemini_api, "opener", return_value=self.fake([429, 429])):
            r = self.chat().ask("Làm sao đổi tỉ lệ khung hình video?")
        self.assertEqual((r["source"], r["remaining"]), ("zalo", 20))
        self.assertIn("flash-lite", self.calls[1][0])  # đã thử model dự phòng

    def test_no_key_or_no_license_uses_zalo(self):
        self.assertEqual(self.chat(key="").ask("Tỉ lệ khung hình đổi ở đâu?")["source"], "zalo")
        self.assertEqual(self.chat(allowed=False).ask("Tỉ lệ khung hình đổi ở đâu?")["source"], "zalo")
        self.assertEqual(self.chat(key="").status()["ai"], False)

    def test_mask_roundtrip_hides_plain_key(self):
        blob = support_chat.mask("AIzaSECRET123")
        self.assertNotIn("SECRET", blob)
        self.assertEqual(support_chat.unmask(blob), "AIzaSECRET123")

    def test_empty_and_long_messages_refused(self):
        with self.assertRaises(ValueError):
            self.chat().ask("   ")
        with self.assertRaises(ValueError):
            self.chat().ask("x" * 1001)


if __name__ == "__main__":
    unittest.main()
