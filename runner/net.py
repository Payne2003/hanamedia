"""Mở kết nối HTTPS bền với VPN / đổi mạng, có dự phòng chứng chỉ.

- Mỗi lần gọi đọc lại cấu hình proxy hiện tại của Windows. (urllib.request.urlopen giữ proxy từ lần gọi đầu tiên tới
  khi tắt app, nên bật/tắt VPN như 1.1.1.1 giữa chừng làm app đi theo proxy cũ đã chết.)
- Kết nối hỏng (không phải lỗi HTTP) thì thử lại: bộ chứng chỉ certifi đóng gói sẵn (máy thiếu chứng chỉ gốc), rồi đi
  thẳng không qua proxy (VPN để lại proxy hệ thống đã chết).
- Luôn xác minh chứng chỉ — không bao giờ tắt kiểm tra.
"""
import socket
import ssl
import urllib.error
import urllib.request


class NetworkError(urllib.error.URLError):
    """Mọi cách kết nối đều hỏng. `attempts`: lỗi của từng cách (để ghi nhật ký)."""

    def __init__(self, attempts):
        self.attempts = attempts
        super().__init__("; ".join(attempts))


def _ssl_failure(error):
    reason = getattr(error, "reason", error)
    return isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def certifi_context():
    import certifi
    return ssl.create_default_context(cafile=certifi.where())


def system_proxies():
    return urllib.request.getproxies()


def describe(error):
    reason = getattr(error, "reason", None)
    if isinstance(error, urllib.error.URLError) and reason is not None:
        error = reason
    return f"{type(error).__name__}: {str(error)[:120]}"


def urlopen(request, timeout=20, handlers=(), proxies=None):
    """handlers: handler urllib thêm vào (vd. chặn chuyển hướng); các lần thử lại vẫn giữ chúng.
    Lỗi HTTP (404, 403…) trả về ngay — máy chủ đã trả lời, đổi đường mạng không giúp gì."""
    proxies = system_proxies() if proxies is None else proxies
    routes = [("proxy hệ thống" if proxies else "trực tiếp", proxies)]
    if proxies:
        routes.append(("trực tiếp, bỏ proxy", {}))
    attempts = []
    for label, route in routes:
        contexts = [None]
        for context in contexts:
            extra = [urllib.request.HTTPSHandler(context=context)] if context is not None else []
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(route), *handlers, *extra)
            try:
                return opener.open(request, timeout=timeout)
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, ssl.SSLError, socket.timeout, TimeoutError, ConnectionError) as error:
                attempts.append(f"{label}{' + certifi' if context is not None else ''}: {describe(error)}")
                if context is None and _ssl_failure(error):
                    try:
                        contexts.append(certifi_context())
                    except ImportError:
                        pass
    raise NetworkError(attempts)
