"""Phase 9: the editing-quality evaluation harness measures what it should, so changes can be judged by numbers."""
import sys
import unittest
from pathlib import Path

import helpers

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "engine" / "tools"))
import eval_quality  # noqa: E402

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


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


if __name__ == "__main__":
    unittest.main()
