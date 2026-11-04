# -*- coding: utf-8 -*-
"""斜角遮罩几何、速度曲线和异常回收的回归检查。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smooth_transition import DiagonalWipe, ease_cover, ease_reveal, mask_polygon


def inside(points, x, y):
    result = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1:
            result = not result
        previous = current
    return result


class GeometryChecks(unittest.TestCase):
    def test_coverage_handoff_has_no_exposed_corners(self):
        for direction in (1, -1):
            for polygon in (mask_polygon(1000, 600, 1, direction=direction),
                            mask_polygon(1000, 600, 0, reveal=True, direction=direction)):
                for point in ((1, 1), (999, 1), (1, 599), (999, 599), (500, 300)):
                    self.assertTrue(inside(polygon, *point))

    def test_start_and_finish_reveal_the_whole_page(self):
        for direction in (1, -1):
            for polygon in (mask_polygon(1000, 600, 0, direction=direction),
                            mask_polygon(1000, 600, 1, reveal=True, direction=direction)):
                for x in (1, 250, 500, 750, 999):
                    for y in (1, 300, 599):
                        self.assertFalse(inside(polygon, x, y))

    def test_reverse_direction_is_mirrored(self):
        for reveal in (False, True):
            for progress in (0.1, 0.35, 0.6, 0.9):
                forward = mask_polygon(1000, 600, progress, reveal, 1)
                reverse = mask_polygon(1000, 600, progress, reveal, -1)
                for x in (10, 200, 470, 650, 990):
                    for y in (10, 250, 590):
                        self.assertEqual(inside(forward, x, y), inside(reverse, 1000-x, y))

    def test_easing_is_monotonic_and_stops_cleanly(self):
        for easing in (ease_cover, ease_reveal):
            values = [easing(i/100) for i in range(101)]
            self.assertEqual(values, sorted(values))
            self.assertEqual(values[0], 0)
            self.assertEqual(values[-1], 1)
            self.assertLess(1-easing(.99), .001)


class LifecycleChecks(unittest.TestCase):
    def setUp(self):
        self.clock = [0.0]
        self.owner = SimpleNamespace(_cancel=Mock(), _later=Mock(return_value='timer'),
            winfo_viewable=lambda: True, update_idletasks=Mock(), winfo_id=lambda: 1,
            app=SimpleNamespace(log=Mock()))
        self.viewport = SimpleNamespace(winfo_rootx=lambda: 0, winfo_rooty=lambda: 0,
            winfo_width=lambda: 1000, winfo_height=lambda: 600)
        self.commit, self.finished = Mock(return_value=7), Mock()
        self.wipe = DiagonalWipe(self.owner, self.viewport, '#FF6B1A', self.commit, self.finished)
        self.wipe.window = SimpleNamespace(withdraw=Mock())
        self.wipe.user = SimpleNamespace(GetAncestor=Mock(return_value=1), RedrawWindow=Mock())
        def prepare():
            self.wipe.bounds = (0, 0, 1000, 600)
            self.wipe.width, self.wipe.height = 1000, 600
        self.wipe._prepare_window = prepare
        self.wipe._shape = Mock()
        self.time_patch = patch('smooth_transition.time.perf_counter', side_effect=lambda: self.clock[0])
        self.flush_patch = patch('smooth_transition.ctypes.windll', SimpleNamespace(
            dwmapi=SimpleNamespace(DwmFlush=Mock())))
        self.time_patch.start()
        self.flush_patch.start()

    def tearDown(self):
        self.flush_patch.stop()
        self.time_patch.stop()

    def test_commit_only_when_fully_covered(self):
        self.wipe.start()
        self.clock[0] = self.wipe.COVER_SECONDS/2
        self.wipe._frame()
        self.commit.assert_not_called()
        self.clock[0] = self.wipe.COVER_SECONDS+.001
        self.wipe._frame()
        self.commit.assert_called_once()
        self.wipe.user.RedrawWindow.assert_called_once_with(7, None, None, 0x0185)
        self.assertEqual(self.wipe.phase, 'reveal')
        self.clock[0] += self.wipe.REVEAL_SECONDS+.001
        self.wipe._frame()
        self.assertFalse(self.wipe.active)
        self.finished.assert_called_once()

    def test_cancel_removes_mask_without_late_commit(self):
        self.wipe.start()
        self.wipe.cancel()
        self.clock[0] = 10
        self.wipe._frame()
        self.commit.assert_not_called()
        self.assertFalse(self.wipe.active)

    def test_render_failure_does_not_leave_an_overlay(self):
        self.wipe._shape.side_effect = OSError('simulation')
        self.wipe.start()
        self.assertFalse(self.wipe.active)
        self.commit.assert_called_once()
        self.finished.assert_called_once()
        self.wipe.window.withdraw.assert_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
