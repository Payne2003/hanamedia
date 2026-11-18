# HanaMedia Short

Ảnh nhân vật + brief sản phẩm → YouTube Short 9:16 (3 cảnh × 8 giây, Hook → Demo → CTA).

```
/hana-short (Claude Skill)          .claude/skills/hana-short/SKILL.md
   │  viết script.json (lời thoại, prompt Veo 3, character bible, tiêu chí duyệt)
   ▼
hana.bat / hana/  (Python + FFmpeg)  job theo job_id/scene_id, trạng thái trong jobs/<id>/job.json
   ▲  Native Messaging (native_host/)
   ▼
extension/ (Chrome MV3)             thao tác tab Flow đã đăng nhập: cài đặt, ảnh tham chiếu, prompt, Tạo, tải clip
   │
   ▼
review (storyboard FFmpeg → Claude) PASS / RETRY / REVIEW_REQUIRED, giới hạn số lần, chỉ tạo lại cảnh lỗi
   ▼
timeline.json + edit_package/       thứ tự clip, điểm cắt, âm lượng, mốc SFX từ thư viện của bạn
   ▼
CapCut Desktop (hana/capcut.py)     tạo draft JSON → bạn mở draft → Export (phím tắt) → verify MP4 9:16
```

- Cài đặt & xử lý sự cố: [docs/CAI-DAT.md](docs/CAI-DAT.md)
- Ghi chú giao diện Flow / CapCut đã kiểm tra: [docs/GHI-CHU-TICH-HOP.md](docs/GHI-CHU-TICH-HOP.md)
- Kiểm thử: `python -m unittest discover -s tests -v`

Các lệnh chính: `doctor`, `init`, `validate-script`, `set-script`, `status`, `review-prep`, `review-set`,
`resolve`, `flow-unblock`, `import-clip`, `timeline`, `package`, `preview`, `capcut probe|template|build|opened|export|register-export`,
`verify`, `simulate-flow` (chỉ test).
