"""The randomized rule check of the selection (engine/tools/fuzz_selection.py) runs a small batch in every test run."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fuzz_selection as fz  # noqa: E402
import vid_story as st  # noqa: E402


class Rules(unittest.TestCase):
    def test_a_small_batch_keeps_every_rule(self):
        found = fz.run(30, seed=1000, feedback_runs=8, log=lambda *_: None)
        self.assertEqual(found, {})

    def test_the_checker_notices_broken_rules(self):
        case = fz.random_case(3)
        sb = st.select(case["inv"], case["sc"], case["beats"], case["cfg"], None, duration=case["duration"], pacing=case["pacing"],
                       variant=case["variant"], method=case["method"], profile=case["profile"])
        self.assertEqual(fz.check_storyboard(case, sb), [])
        broken = dict(sb, rows=[dict(r) for r in sb["rows"]])
        broken["rows"][1]["clip"] = broken["rows"][0]["clip"]
        broken["rows"][2]["start"] = -1.0
        codes = {c for c, _ in fz.check_storyboard(case, broken)}
        self.assertIn("clip-twice", codes)
        self.assertIn("start-outside", codes)
        silent = dict(sb, rows=sb["rows"][:-1], warnings=[])
        self.assertIn("unfilled-silent", {c for c, _ in fz.check_storyboard(case, silent)})


if __name__ == "__main__":
    unittest.main()
