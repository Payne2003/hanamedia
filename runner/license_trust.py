"""Khóa công khai Super Admin được app tin cậy (gắn cứng khi đóng gói).

Để TRỐNG: chưa bật khóa bản quyền, tool chạy đầy đủ như trước.
Khi Super Admin tạo khóa ký (trang Bản quyền → Khởi tạo khóa Super Admin), tool hiện "Khóa công khai"; dán chuỗi đó
vào danh sách dưới đây rồi đóng gói lại thì các bản app phát cho khách mới bắt buộc kích hoạt bằng key.
KHÔNG BAO GIỜ đưa khóa ký (signing.key) vào đây hay vào kho mã.
"""
TRUSTED_KEYS = []
