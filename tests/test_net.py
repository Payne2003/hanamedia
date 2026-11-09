"""HTTPS: kho chứng chỉ Windows thiếu gốc (CERTIFICATE_VERIFY_FAILED) thì thử lại bằng certifi; lỗi khác giữ nguyên."""
import ssl
import unittest
import urllib.error
from unittest.mock import patch

from runner import net


class NetTests(unittest.TestCase):
    def test_retries_with_certifi_on_certificate_error(self):
        calls = []

        def fake(request, timeout=20, context=None):
            calls.append(context)
            if context is None:
                raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED"))
            return "ok"
        with patch.object(net.urllib.request, "urlopen", fake):
            self.assertEqual(net.urlopen("https://github.com"), "ok")
        self.assertIsNone(calls[0])
        self.assertIsInstance(calls[1], ssl.SSLContext)
        self.assertEqual(calls[1].verify_mode, ssl.CERT_REQUIRED)  # vẫn xác minh đầy đủ

    def test_other_errors_are_not_retried(self):
        def fake(request, timeout=20, context=None):
            raise urllib.error.URLError(ConnectionRefusedError())
        with patch.object(net.urllib.request, "urlopen", fake):
            with self.assertRaises(urllib.error.URLError):
                net.urlopen("https://github.com")


if __name__ == "__main__":
    unittest.main()
