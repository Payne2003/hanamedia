"""Mở kết nối HTTPS với dự phòng chứng chỉ.

Một số máy Windows thiếu chứng chỉ gốc trong kho của Windows (lâu không cập nhật Windows, hoặc phần mềm diệt virus
can thiệp HTTPS) nên Python báo CERTIFICATE_VERIFY_FAILED. Khi đó thử lại bằng bộ chứng chỉ certifi đóng gói sẵn
trong app. Luôn xác minh chứng chỉ — không bao giờ tắt kiểm tra.
"""
import ssl
import urllib.error
import urllib.request


def _ssl_failure(error):
    reason = getattr(error, "reason", error)
    return isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def certifi_context():
    import certifi
    return ssl.create_default_context(cafile=certifi.where())


def urlopen(request, timeout=20, handlers=()):
    """handlers: handler urllib thêm vào (vd. chặn chuyển hướng). Lần thử lại bằng certifi vẫn giữ các handler đó."""
    def open_once(context=None):
        if not handlers:
            if context is None:
                return urllib.request.urlopen(request, timeout=timeout)
            return urllib.request.urlopen(request, timeout=timeout, context=context)
        extra = [urllib.request.HTTPSHandler(context=context)] if context is not None else []
        return urllib.request.build_opener(*handlers, *extra).open(request, timeout=timeout)
    try:
        return open_once()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ssl.SSLError) as error:
        if not _ssl_failure(error):
            raise
        try:
            context = certifi_context()
        except ImportError:
            raise error from None
        return open_once(context)
