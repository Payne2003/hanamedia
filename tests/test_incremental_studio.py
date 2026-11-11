"""Viết prompt xong nhóm nào đưa vào Studio nhóm đó (cùng một lượt), và Gemini xem cả video một lần mà vẫn đủ cảnh."""
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from runner import gemini_web
from runner.store import Store


class AppendBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "t.db")

    def tearDown(self):
        self.temp.cleanup()

    def jobs(self, batch):
        return sorted(((j["ordinal"], j["prompt"]) for j in self.store.snapshot()["jobs"] if j["batch"] == batch))

    def stitch_status(self, batch):
        with self.store.connect() as db:
            return db.execute("SELECT stitch_status FROM batch_options WHERE batch=?", (batch,)).fetchone()[0]

    def test_groups_join_one_batch_in_order_and_hold_stitch_until_last(self):
        batch = uuid.uuid4().hex
        self.store.enqueue(["P1", "P2"], 1, batch, options={"stitch": True}, append=0, more=True)
        self.assertEqual(self.stitch_status(batch), "hold")
        self.store.enqueue(["P1", "P2"], 1, batch, options={"stitch": True}, append=0, more=True)  # gửi lại: không trùng
        self.store.enqueue(["P3"], 1, batch, options={"stitch": True}, append=2, more=False)
        self.assertEqual(self.jobs(batch), [(0, "P1"), (1, "P2"), (2, "P3")])
        self.assertEqual(self.stitch_status(batch), "")
        self.assertEqual(self.store.stitch_candidates(), [])  # chưa cảnh nào xong

    def test_held_batch_is_not_stitched(self):
        batch = uuid.uuid4().hex
        self.store.enqueue(["P1"], 1, batch, options={"stitch": True}, append=0, more=True)
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='done' WHERE batch=?", (batch,))
        self.assertEqual(self.store.stitch_candidates(), [])
        self.store.enqueue(["P1"], 1, batch, options={"stitch": True}, append=0, more=False)  # nhóm cuối không có prompt mới
        self.assertEqual(self.store.stitch_candidates(), [batch])

    def test_reprompt_only_changes_waiting_jobs(self):
        batch = uuid.uuid4().hex
        self.store.enqueue(["Old 1", "Old 2"], 1, batch, append=0)
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='done' WHERE batch=? AND ordinal=1", (batch,))
        self.assertTrue(self.store.reprompt(batch, 0, "New 1"))
        self.assertFalse(self.store.reprompt(batch, 1, "New 2"))
        self.assertEqual(self.jobs(batch), [(0, "New 1"), (1, "Old 2")])
        with self.assertRaises(ValueError):
            self.store.reprompt(batch, 0, "  ")


class WholeVideoTests(unittest.TestCase):
    def test_keeps_asking_until_every_shot_has_a_scene(self):
        class Session:
            def __init__(self, answers):
                self.answers, self.sent = list(answers), []
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return name
            def ask(self, text, status, stop, email="", label=""):
                self.sent.append(text)
                return self.answers.pop(0)

        line = lambda n: f"Bối cảnh {n}: Cảnh {n}, góc quay: Trung cảnh, hành vi nhân vật: Đi, Nội dung hành động nhân vật: Bước {n};"
        shots = [{"start": i * 2.0, "end": i * 2.0 + 2} for i in range(4)]
        session = Session(["KỊCH BẢN VIDEO GỐC:\nK\nDANH SÁCH CẢNH:\n" + line(1) + "\n" + line(2),
                           "Xin lỗi, tôi đã viết xong.",  # lượt không ra cảnh mới: vẫn nhắc lại vì còn thiếu cảnh
                           line(3) + "\n" + line(4) + "\n" + gemini_web.END_MARK])
        result = gemini_web.run_conversation(session, "Yêu cầu", lambda _: None, threading.Event(), shots=shots)
        self.assertEqual(len(result["scenes"]), 4)
        self.assertIn("Bối cảnh 3", session.sent[2])


if __name__ == "__main__":
    unittest.main()


class AngleDefaultTests(unittest.TestCase):
    def test_detailed_prompts_switch_angle_every_2_3_seconds(self):
        from runner import content
        self.assertEqual(content.angle_spans(10), [(0, 3), (3, 5), (5, 8), (8, 10)])
        sent = []

        class Session:
            NAME = "ChatGPT"
            def new_chat(self):
                pass
            def wait_ready(self, status, stop, email=""):
                pass
            def choose_model(self, name):
                return name
            def ask(self, text, status, stop, email="", label=""):
                sent.append(text)
                return "SCENE 1:\nGenerate a 10-second ultra-realistic wildlife documentary video at dawn.\n" + content.END_EN

        gemini_web.english_detailed(Session(), ["Bối cảnh 1: hổ đi"], lambda _: None, threading.Event(),
                                    seconds=10, angles=3)
        self.assertIn("0–3 SECONDS, 3–5 SECONDS, 5–8 SECONDS, 8–10 SECONDS", sent[0])
        sent.clear()
        gemini_web.english_detailed(Session(), ["Bối cảnh 1: hổ đi"], lambda _: None, threading.Event(), seconds=10)
        self.assertIn("1–3 consecutive camera segments", sent[0])


class ActionSceneTests(unittest.TestCase):
    def test_short_videos_are_watched_at_more_frames_per_second(self):
        from unittest import mock
        from runner import gemini_api
        for seconds, fps in ((60, 5), (240, 2), (600, 1), (1800, 1), (0, 1)):
            with mock.patch("runner.video_tools.probe", return_value={"duration": seconds}):
                self.assertEqual(gemini_api.video_fps(Path("v.mp4")), fps)

    def test_prompts_keep_full_speed_chases_and_fights(self):
        from runner import content
        self.assertIn("TỐC ĐỘ và CƯỜNG ĐỘ", content.SCENE_RULES)
        self.assertIn("FULL-SPEED", content.detailed_request("Bối cảnh 1: sói đuổi nai", 1, 1, 10))


class ShortSceneDurationTests(unittest.TestCase):
    def test_prompt_seconds_set_job_duration(self):
        from runner.store import prompt_seconds
        self.assertEqual(prompt_seconds("Generate a 2-second ultra-realistic video"), 2)
        self.assertEqual(prompt_seconds("Generate a 3-second video"), 3)
        self.assertEqual(prompt_seconds("Generate a 7-second video"), 0)  # Muse không có 7 giây: để Muse tự chọn
        self.assertEqual(prompt_seconds("A tiger walks"), 0)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.db")
            batch = uuid.uuid4().hex
            store.enqueue(["Generate a 2-second video of a fox.", "Generate a 10-second video of a fox."], 1, batch)
            self.assertEqual(sorted(j["duration"] for j in store.snapshot()["jobs"]), [2, 10])
            store.enqueue(["Generate a 2-second video of a deer."], 1, uuid.uuid4().hex, options={"duration": 5})
            self.assertIn(5, [j["duration"] for j in store.snapshot()["jobs"]])
