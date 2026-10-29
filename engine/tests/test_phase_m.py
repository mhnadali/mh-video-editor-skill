"""Phase M (0.28): reliability. QA measures the beat sync on the rendered sound itself (not against the EDL's own beat times), and
eval_real.py compares a real project's measurements with a baseline."""
import sys
import unittest
from pathlib import Path

import helpers
import test_robustness as tr
import vid_edl as ve
import vid_qa
import vidlib as vl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import eval_real  # noqa: E402

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


@NEEDS_FF
@helpers.slow
class BeatSync(unittest.TestCase):
    """A click every 0.5 s from 0.5 s; one shot per beat. Cuts on the clicks pass; the same cuts 0.1 s late (what 0.25's beat times
    were at 45 s) are reported, although the EDL's own beat grid agrees with them."""

    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "phase_m"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.music = cls.root / "click.wav"
        helpers.click_track(cls.music, bpm=120, first=0.5, seconds=7.0, step_at=None)
        cls.clips = helpers.make_clips(cls.root / "clips", 4, seconds=3.0)

    def sync(self, shift, name):
        edges = [0.0] + [round(0.5 + shift + 0.5 * k, 3) for k in range(1, 12)] + [6.5]
        shots = [{"clip": vl.ffp(self.clips[k % 4]), "span": [edges[k], edges[k + 1]]} for k in range(len(edges) - 1)]
        edl = tr.tiny_edl(shots, 6.5, music=vl.ffp(self.music), style={"grade": "neutral", "end_flare": False, "drop_flash": False})
        edl["beats"] = {"t0": round(0.5 + shift, 3), "period": 0.5}
        res, path = tr.render(edl, self.root, name, preview=True)
        e = ve.load(path)
        ev = vid_qa.expected_events(e, 30, vl.probe(res["out"])["duration"])
        return vid_qa.beat_sync(res["out"], e, ev)

    def test_cuts_on_the_clicks_pass(self):
        s = self.sync(0.0, "on_beat")
        self.assertGreaterEqual(s["cuts"], 6)
        self.assertLess(abs(s["median"]), vid_qa.BEAT_SYNC_WARN)
        self.assertEqual(vid_qa.check_beat_sync(s)[0]["level"], "ok")

    def test_cuts_a_tenth_of_a_second_late_are_reported(self):
        s = self.sync(0.1, "late")
        self.assertLess(s["median"], -vid_qa.BEAT_SYNC_WARN, "the onsets come before the cuts")
        self.assertEqual(vid_qa.check_beat_sync(s)[0]["level"], "warn")


class NoMusic(unittest.TestCase):
    def test_nothing_to_measure_without_music_or_a_beat_grid(self):
        edl = {"project": {"name": "x", "width": 180, "height": 320, "fps": 30, "duration": 4.0}, "shots": []}
        self.assertIsNone(vid_qa.beat_sync("none.mp4", edl, {"cuts": [(1, 1.0)]}))
        edl["project"]["music"] = "m.wav"
        self.assertIsNone(vid_qa.beat_sync("none.mp4", edl, {"cuts": [(1, 1.0)]}), "no beats in the EDL")
        self.assertEqual(vid_qa.check_beat_sync(None), [])


class Baseline(unittest.TestCase):
    def test_a_change_in_the_wrong_direction_is_a_regression(self):
        base = {"beats": {"median_abs": 0.005, "iqr": 0.14}, "same": {"precision": 1.0, "recall": 0.43},
                "select": {"deterministic": 1}, "hook": {"action_share": 1.0}}
        same = eval_real.compare(base, base)
        self.assertTrue(same and all(v == "ok" for *_r, v in same))
        worse = dict(base, beats={"median_abs": 0.1, "iqr": 0.14}, same={"precision": 0.9, "recall": 0.43},
                     select={"deterministic": 0})
        verdicts = {name: v for name, _a, _b, v in eval_real.compare(worse, base)}
        self.assertEqual((verdicts["beats.median_abs"], verdicts["same.precision"], verdicts["select.deterministic"]),
                         ("REGRESSION", "REGRESSION", "REGRESSION"))
        better = dict(base, same={"precision": 1.0, "recall": 0.6})
        self.assertEqual({v for *_r, v in eval_real.compare(better, base)}, {"ok"})
        self.assertEqual(eval_real.compare({"qa": {"median_abs": 0.01}}, base), [], "only numbers present in both are compared")


if __name__ == "__main__":
    unittest.main()
