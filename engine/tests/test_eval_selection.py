"""The selection evaluation: a synthetic library with a known truth must stay above the floors."""
import sys
import unittest
from pathlib import Path

import helpers

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import eval_selection as ev  # noqa: E402
import vid_story as st  # noqa: E402


class Evaluation(unittest.TestCase):
    def test_floors_hold_for_several_seeds(self):
        for seed in (7, 11, 23):
            res = ev.evaluate(seed)
            self.assertEqual(ev.check(res), [], f"seed {seed}: {res}")

    def test_the_library_really_contains_the_traps(self):
        inv, sc, truth = ev.build_library(7)
        self.assertGreater(len(truth["short"]), 3)
        self.assertGreater(len(truth["groups"]), 10)
        self.assertTrue(all(inv[i]["duration"] < 5.0 for i in truth["short"]))

    def test_the_check_notices_a_violation(self):
        res = ev.evaluate(7)
        bad = dict(res, beam=dict(res["beam"], duplicates_used=2))
        self.assertTrue(any("duplicates_used" in p for p in ev.check(bad)))

    def test_a_long_chronological_chapter_still_gets_a_sequence(self):
        # a regression: one chapter of 90 clips for 36 slots once stranded the beam (no sequence at all)
        inv, sc, _ = ev.build_library(5, 120)
        cfg = {"chapters": [{"name": "all", "from": 0, "to": 119, "weight": 1, "energy": "building"}]}
        sb = st.select(inv, sc, ev.build_beats(), cfg, None, duration=45.0, pacing="energy")
        self.assertEqual(len(sb["rows"]), sb["slots"])


if __name__ == "__main__":
    unittest.main()
