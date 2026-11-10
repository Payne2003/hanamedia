"""Thư viện góc quay của HANA MEDIA: hàng nghìn góc máy (cỡ cảnh × góc máy × chuyển động × ống kính) và bộ chọn
đúng 3 góc cho mỗi cảnh 10 giây theo hành động của cảnh (đuổi, đánh nhau, rình, nghỉ…).

- Ba góc của một cảnh đi theo nhịp dựng phim tài liệu: mở bằng góc rộng/thiết lập, giữa là trung cảnh theo hành động,
  kết bằng cận/đặc tả — với cảnh đánh nhau thì dồn dập hơn (trung toàn cầm tay → cận → đặc tả).
- Chọn có thứ tự cố định theo số cảnh + nội dung (chạy lại cho cùng kết quả) và không lặp góc của cảnh liền trước.
- Người dùng thêm góc riêng (mỗi dòng một góc, tiếng Anh); góc riêng được xếp vào nhóm theo chữ wide/medium/close.
"""
import hashlib
import re

# (tiếng Anh cho prompt, tên tiếng Việt, nhóm cỡ cảnh)
SIZES = (("extreme wide shot", "toàn cảnh cực rộng", "wide"), ("wide establishing shot", "toàn cảnh thiết lập", "wide"),
         ("full shot", "toàn thân", "wide"), ("medium wide shot", "trung toàn", "medium"),
         ("medium shot", "trung cảnh", "medium"), ("medium close-up", "trung cận", "close"),
         ("close-up", "cận cảnh", "close"), ("extreme close-up", "đặc tả", "close"))
HEIGHTS = (("eye-level", "ngang tầm mắt"), ("low angle", "góc thấp"), ("ground-level", "sát mặt đất"),
           ("high angle", "góc cao"), ("overhead top-down", "trên cao nhìn xuống"), ("shoulder-height", "ngang vai"),
           ("dutch tilt", "nghiêng khung"))
MOVES = (("static locked-off camera", "máy tĩnh", {"rest", "stalk", "general"}),
         ("slow push-in", "đẩy chậm vào", {"stalk", "rest", "general"}),
         ("fast push-in", "đẩy nhanh vào", {"fight", "chase", "stalk"}),
         ("pull-back reveal", "lùi ra mở cảnh", {"rest", "general", "stalk"}),
         ("lateral tracking alongside", "bám ngang song song", {"chase", "general"}),
         ("following from behind", "bám theo phía sau", {"chase", "stalk"}),
         ("leading in front", "lùi trước mặt", {"chase", "fight"}),
         ("handheld", "cầm tay rung nhẹ", {"fight", "chase"}),
         ("orbit around the subject", "xoay quanh chủ thể", {"fight", "general"}),
         ("crane up", "cẩu lên", {"rest", "general"}), ("crane down", "cẩu xuống", {"general", "stalk"}),
         ("whip pan", "lia giật", {"fight", "chase"}), ("smooth pan following", "lia theo", {"chase", "general", "stalk"}))
LENSES = (("telephoto compression", "tele nén hậu cảnh"), ("wide lens", "ống rộng"), ("macro detail", "macro chi tiết"),
          ("shallow depth of field", "xoá phông"), ("deep focus", "rõ sâu"))

# Nhịp 3 góc cho mỗi loại cảnh: nhóm cỡ cảnh của góc 1, 2, 3.
PATTERNS = {"fight": ("medium", "close", "close"), "chase": ("wide", "medium", "close"),
            "stalk": ("wide", "medium", "close"), "rest": ("wide", "medium", "close"),
            "general": ("wide", "medium", "close")}
KINDS = (("fight", r"fight|attack|bite|biting|clash|charg|strike|wrestl|kill|đánh|cắn|húc|lao vào|tấn công|vật lộn|giao chiến"),
         ("chase", r"chase|chasing|pursu|flee|fleeing|sprint|run|running|gallop|đuổi|chạy|bỏ chạy|truy"),
         ("stalk", r"stalk|creep|approach|ambush|crouch|watch|rình|tiến lại|tiếp cận|phục kích|nấp"),
         ("rest", r"rest|graz|eat|drink|sleep|groom|nghỉ|gặm|ăn|uống|ngủ|liếm"))


def build():
    """Toàn bộ góc của thư viện (bỏ các tổ hợp vô lý: macro chỉ cho cận, nhìn từ trên cao không đi với đặc tả…)."""
    library = []
    for size, size_vi, group in SIZES:
        for height, height_vi in HEIGHTS:
            if height.startswith("overhead") and group == "close":
                continue
            if height == "dutch tilt" and group == "wide":
                continue
            for move, move_vi, kinds in MOVES:
                if group == "close" and move in ("crane up", "crane down", "pull-back reveal"):
                    continue
                for lens, lens_vi in LENSES:
                    if lens == "macro detail" and group != "close":
                        continue
                    if lens == "wide lens" and size == "extreme close-up":
                        continue
                    library.append({"text": f"{size}, {height}, {move}, {lens}", "vi": f"{size_vi}, {height_vi}, {move_vi}, {lens_vi}",
                                    "group": group, "kinds": kinds})
    return library


LIBRARY = build()


def custom_angles(lines):
    """Góc người dùng thêm (mỗi dòng một góc): nhóm theo chữ trong mô tả; dùng được cho mọi loại cảnh."""
    result = []
    for line in str(lines or "").splitlines():
        text = re.sub(r"\s+", " ", line).strip(" -•;.")[:200]
        if not text:
            continue
        low = text.lower()
        group = ("close" if re.search(r"close|macro|detail|cận|đặc tả", low) else
                 "wide" if re.search(r"wide|establish|aerial|drone|toàn", low) else "medium")
        result.append({"text": text, "vi": text, "group": group, "kinds": {"fight", "chase", "stalk", "rest", "general"}})
    return result


def scene_kind(text):
    text = str(text or "").lower()
    for kind, pattern in KINDS:
        if re.search(pattern, text):
            return kind
    return "general"


def marks(seconds):
    """Ba khoảng thời gian cho 3 góc: 10s → 0–3s, 3–7s, 7–10s."""
    seconds = float(seconds or 10)
    a, b = round(seconds * 0.3 * 2) / 2, round(seconds * 0.7 * 2) / 2
    fmt = lambda value: f"{value:g}"
    return [(0, a), (a, b), (b, seconds)], fmt


def pick(scene_number, text, seconds=10, avoid=(), extra=()):
    """3 góc cho một cảnh. avoid: góc của cảnh trước (không lặp lại). Trả về danh sách 3 góc (dict)."""
    kind = scene_kind(text)
    pool = LIBRARY + list(extra)
    chosen = []
    for slot, group in enumerate(PATTERNS[kind]):
        fresh = lambda a: ((kind in ("fight", "chase") or "dutch tilt" not in a["text"]) and a["text"] not in avoid and all(a["text"] != c["text"] for c in chosen)
                           and not (chosen and a["text"].split(",")[2:3] == chosen[-1]["text"].split(",")[2:3]))
        candidates = [a for a in pool if a["group"] == group and kind in a["kinds"] and fresh(a)]
        if not candidates:
            candidates = [a for a in pool if a["group"] == group and "general" in a["kinds"] and fresh(a)]
        if not candidates:
            candidates = [a for a in pool if a["group"] == group] or pool
        seed = hashlib.sha1(f"{scene_number}|{slot}|{text}".encode("utf-8")).digest()
        chosen.append(candidates[int.from_bytes(seed[:4], "big") % len(candidates)])
    return chosen


def plan_text(angles, seconds=10):
    spans, fmt = marks(seconds)
    return "; ".join(f"{fmt(a)}–{fmt(b)}s: {angle['text']}" for (a, b), angle in zip(spans, angles))


def plans(scene_texts, seconds=10, extra_lines=""):
    """Kế hoạch 3 góc cho từng cảnh (không lặp góc của cảnh liền trước)."""
    extra = custom_angles(extra_lines)
    result, previous = [], ()
    for number, text in enumerate(scene_texts, 1):
        angles = pick(number, text, seconds, previous, extra)
        previous = {a["text"] for a in angles}
        result.append(angles)
    return result


CAMERA_FIELD = re.compile(r"(CAMERA\s*:\s*)(.*?)(?=;\s*(?:CHARACTER BEHAVIOR|ACTION|ENDING FRAME|STYLE|DURATION|ASPECT RATIO|LOCK)\s*:|$)",
                          re.IGNORECASE | re.DOTALL)


def apply(prompt, angles, seconds=10):
    """Đặt trường CAMERA của prompt thành đúng 3 góc đã chọn (thêm trường CAMERA nếu prompt chưa có)."""
    plan = plan_text(angles, seconds)
    prompt = str(prompt or "").strip()
    if CAMERA_FIELD.search(prompt):
        return CAMERA_FIELD.sub(lambda m: m.group(1) + plan, prompt, count=1)
    match = re.search(r";\s*(?=CHARACTER BEHAVIOR|ACTION)\s*", prompt, re.IGNORECASE)
    if match:
        return prompt[:match.start()] + f"; CAMERA: {plan}; " + prompt[match.end():]
    return f"{prompt.rstrip(' .;')}; CAMERA: {plan}."
