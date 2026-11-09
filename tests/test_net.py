"""HTTPS bền với VPN/đổi mạng: đọc lại proxy mỗi lần, thử certifi khi thiếu chứng chỉ gốc, bỏ proxy chết."""
import ssl
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from runner import net


class FakeOpener:
    def __init__(self, calls, behaviour, handlers):
        self.calls, self.behaviour = calls, behaviour
        self.proxies = next(h.proxies for h in handlers if isinstance(h, urllib.request.ProxyHandler))
        self.context = next((h._context for h in handlers if isinstance(h, urllib.request.HTTPSHandler)), None)

    def open(self, request, timeout=20):
        self.calls.append((dict(self.proxies), self.context))
        return self.behaviour(self.proxies, self.context)


class NetTests(unittest.TestCase):
    def run_with(self, behaviour, proxies):
        calls = []
        with patch.object(net.urllib.request, "build_opener", lambda *h: FakeOpener(calls, behaviour, h)), \
                patch.object(net, "system_proxies", lambda: proxies):
            try:
                return net.urlopen("https://github.com"), calls
            except Exception as error:
                error.calls = calls
                raise

    def test_retries_with_certifi_on_certificate_error(self):
        def behaviour(proxies, context):
            if context is None:
                raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED"))
            return "ok"
        result, calls = self.run_with(behaviour, {})
        self.assertEqual(result, "ok")
        self.assertIsNone(calls[0][1])
        self.assertEqual(calls[1][1].verify_mode, ssl.CERT_REQUIRED)  # vẫn xác minh đầy đủ

    def test_other_errors_are_not_retried_with_certifi(self):
        def behaviour(proxies, context):
            raise urllib.error.URLError(ConnectionRefusedError())
        with self.assertRaises(urllib.error.URLError) as caught:
            self.run_with(behaviour, {})
        self.assertEqual(len(caught.exception.calls), 1)

    def test_dead_vpn_proxy_falls_back_to_direct(self):
        def behaviour(proxies, context):
            if proxies:
                raise urllib.error.URLError(ConnectionRefusedError("proxy 127.0.0.1:40000 đã tắt"))
            return "direct"
        result, calls = self.run_with(behaviour, {"https": "http://127.0.0.1:40000"})
        self.assertEqual(result, "direct")
        self.assertEqual([c[0] for c in calls], [{"https": "http://127.0.0.1:40000"}, {}])

    def test_http_error_is_final(self):
        def behaviour(proxies, context):
            raise urllib.error.HTTPError("https://github.com", 404, "Not Found", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            self.run_with(behaviour, {"https": "http://proxy:8080"})

    def test_proxy_settings_are_read_on_every_call(self):
        seen = []
        settings = iter([{"https": "http://old:1"}, {}])
        with patch.object(net.urllib.request, "build_opener", lambda *h: FakeOpener(seen, lambda p, c: "ok", h)), \
                patch.object(net, "system_proxies", lambda: next(settings)):
            net.urlopen("https://github.com")
            net.urlopen("https://github.com")
        self.assertEqual([c[0] for c in seen], [{"https": "http://old:1"}, {}])

    def test_all_routes_failing_reports_each_attempt(self):
        def behaviour(proxies, context):
            raise TimeoutError("timed out")
        with self.assertRaises(net.NetworkError) as caught:
            self.run_with(behaviour, {"https": "http://proxy:8080"})
        self.assertEqual(len(caught.exception.attempts), 2)
        self.assertIn("bỏ proxy", caught.exception.attempts[1])


if __name__ == "__main__":
    unittest.main()
