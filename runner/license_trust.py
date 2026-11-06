"""Khóa công khai Super Admin được app tin cậy (gắn cứng khi đóng gói).

Chỉ áp dụng cho bản app đã đóng gói (HanaMedia.exe). Danh sách trống = chưa bật khóa bản quyền.
Khi Super Admin tạo khóa ký (trang Bản quyền → Khởi tạo khóa Super Admin), tool hiện "Khóa công khai"; dán chuỗi đó
vào danh sách dưới đây rồi đóng gói lại thì các bản app phát cho khách mới bắt buộc kích hoạt bằng key.
KHÔNG BAO GIỜ đưa khóa ký (signing.key) vào đây hay vào kho mã.
"""
TRUSTED_KEYS = [
    "y0UGCTQNhS5ZBtpDaHAq0O_VOGwZF9eON4k7ba8m40s",  # Super Admin HANA MEDIA (khóa ký nằm trên máy của chủ app)
]
