"""Phase 9: the editing-quality evaluation harness measures what it should, so changes can be judged by numbers."""
import sys
import unittest
from pathlib import Path

import helpers

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "engine" / "tools"))
import eval_quality  # noqa: E402

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class CutMetrics(unittest.TestCase):
    """Pure: the cut-quality numbers on synthetic scores (no FFmpeg)."""

    def scores(self, n):
        return {i: {"index": i, "path": f"c{i}.mp4", "shot_size": ("wide", "medium", "close")[i % 3],
                    "motion_dx": -3.0 if i % 4 == 1 else 0.0, "motion_dy": 0.0, "motion_mag": 3.0 if i % 4 == 1 else 0.0,
                    "focus_x": 0.5, "focus_y": 0.5, "brightness": 100.0, "saturation": 50.0, "energy": 50.0,
                    "hash": (i * 0x9E3779B1) & ((1 << 48) - 1)} for i in range(n)}

    def test_metrics_describe_the_decisions(self):
        n = 16
        edl = {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": float(n), "music": None},
               "style": {"drop_flash": False, "end_flare": False},
               "shots": [{"clip": f"c{i}.mp4", "span": [float(i), float(i + 1)]} for i in range(n)]}
        m = eval_quality.cut_metrics(edl, self.scores(n))
        self.assertEqual(m["cuts"], n - 1)
        self.assertEqual(m["reason_coverage"], 1.0)
        self.assertEqual(m["transitions"], m["transitions_with_reason"])
        self.assertGreaterEqual(m["distinct_cut_types"], 2)
        self.assertLessEqual(m["transitions"] / m["cuts"], 0.5)
        self.assertEqual(m["direction_agreement"], 1.0 if m["directional_transitions"] else None)
        self.assertEqual(edl.get("transitions"), None, "the EDL passed in is not modified")


@NEEDS_FF
@helpers.slow
class Harness(unittest.TestCase):
    def test_reports_beat_pick_and_plan_metrics_on_a_known_corpus(self):
        res = eval_quality.evaluate(bpm=120.0, first=0.5, clips=8, seconds=10.0,
                                    workdir=helpers.tmpdir() / "eval_run")
        self.assertLess(res["beats"]["bpm_error"], 2.0, res["beats"])
        self.assertLess(res["beats"]["first_beat_error_ms"], 150.0, res["beats"])
        self.assertIn("distinct_clips", res["pick"])
        self.assertIn("transition_share", res["plan"])
        self.assertGreaterEqual(res["pick"]["diversity"], 0.0)
        self.assertEqual(res["cuts"]["reason_coverage"], 1.0, res["cuts"])


if __name__ == "__main__":
    unittest.main()
