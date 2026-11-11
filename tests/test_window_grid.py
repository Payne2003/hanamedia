"""Xếp cửa sổ Chrome các tài khoản Muse thành lưới 3 cột × 2 hàng."""
import threading
import unittest
import unittest.mock

from runner import window_grid


class GridTests(unittest.TestCase):
    AREA = (0, 0, 1920, 1040)

    def test_six_slots_fill_rows_left_to_right(self):
        rects = [window_grid.rect(i, self.AREA) for i in range(6)]
        self.assertEqual(rects[0], (0, 0, 640, 520))
        self.assertEqual(rects[2], (1280, 0, 640, 520))
        self.assertEqual(rects[3], (0, 520, 640, 520))
        self.assertEqual(rects[5], (1280, 520, 640, 520))
        self.assertEqual(len({(x, y) for x, y, _, _ in rects}), 6)

    def test_more_than_six_wrap_with_offset_and_free_slot_reuse(self):
        self.assertEqual(window_grid.rect(6, self.AREA)[:2], (36, 36))
        self.assertEqual(window_grid.free_slot([0, 1, 3]), 2)
        self.assertEqual(window_grid.free_slot([]), 0)

    def test_engine_gives_each_new_window_the_first_free_slot(self):
        from runner.engine import Engine
        placed = []

        class Driver:
            def __init__(self, name): self.name = name
            def set_window_rect(self, **r): placed.append((self.name, r["x"], r["y"]))

        class Browser:
            def __init__(self, name): self.driver, self.closed = Driver(name), False

        engine = Engine.__new__(Engine)
        engine.lock, engine.tiles = threading.RLock(), {}
        a, b, c = Browser("a"), Browser("b"), Browser("c")
        with unittest.mock.patch.object(window_grid, "work_area", return_value=self.AREA):
            engine.tile("A", a)
            engine.tile("B", b)
            a.closed = True  # tài khoản A đóng Chrome: ô đầu trống lại
            engine.tile("C", c)
        self.assertEqual(placed, [("a", 0, 0), ("b", 640, 0), ("c", 0, 0)])


if __name__ == "__main__":
    unittest.main()
