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


def urlopen(request, timeout=20):
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ssl.SSLError) as error:
        if not _ssl_failure(error):
            raise
        try:
            context = certifi_context()
        except ImportError:
            raise error from None
        return urllib.request.urlopen(request, timeout=timeout, context=context)
