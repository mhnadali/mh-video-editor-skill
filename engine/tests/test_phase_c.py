"""Phase C (the review experience): proxy videos, the timeline, start-here, auto-save, the `start` feedback."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers
import test_profile as tp
import test_story as ts
import vid_profile as vpf
import vid_review
import vid_story as st
import vidlib as vl

HERE = Path(__file__).resolve().parent
NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class Windows(unittest.TestCase):
    def test_the_proxy_starts_a_little_before_the_shot_and_stays_inside_the_clip(self):
        span, lead = vid_review.PROXY_SPAN, vid_review.PROXY_LEAD
        self.assertEqual(vid_review.proxy_window(60.0, 20.0), (20.0 - lead, span))
        self.assertEqual(vid_review.proxy_window(60.0, 2.0), (0.0, span))
        self.assertEqual(vid_review.proxy_window(60.0, 59.0), (60.0 - span, span), "never past the end")
        self.assertEqual(vid_review.proxy_window(10.0, 5.0), (0.0, 10.0), "a short clip is held whole")
        self.assertEqual(vid_review.proxy_window(0, 0), (0.0, 0.0))


class StartFeedback(unittest.TestCase):
    def setUp(self):
        self.inv, self.sc, self.views = tp.world_and_views(90)
        cfg = ts.cfg_of(ts.chap("start", 0, 29, energy="calm"), ts.chap("action", 30, 59, energy="peak"),
                        ts.chap("end", 60, 89, energy="calm"))
        self.sb = st.select(self.inv, self.sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")

    def test_a_start_is_applied_and_the_old_moment_dropped(self):
        row = self.sb["rows"][3]
        sb = dict(self.sb, rows=[dict(r, moment={"x": 1}) for r in self.sb["rows"]])
        _p, rows = vpf.learn(vpf.empty(), sb, {"decisions": [{"action": "start", "slot": row["slot"], "clip": row["clip"], "start": 4.25}]},
                             self.inv, self.views)
        self.assertEqual(rows[3]["start"], 4.25)
        self.assertEqual(rows[3]["start_kind"], "set by the user")
        self.assertNotIn("moment", rows[3])
        self.assertIn("moment", rows[4])

    def test_a_start_for_a_clip_that_was_replaced_is_ignored(self):
        row = self.sb["rows"][3]
        free = next(i for i in range(30, 60) if i not in {r["clip"] for r in self.sb["rows"]})
        decisions = [{"action": "start", "slot": row["slot"], "clip": row["clip"], "start": 4.0},
                     {"action": "replace", "slot": row["slot"], "with": free}]
        prof, rows = vpf.learn(vpf.empty(), self.sb, {"decisions": decisions}, self.inv, self.views)
        self.assertEqual(rows[3]["clip"], free)
        self.assertNotEqual(rows[3]["start"], 4.0)
        self.assertIn("start-ignored", [h["action"] for h in prof["history"]])

    def test_bad_starts_are_refused(self):
        row = self.sb["rows"][0]
        for bad in ({"action": "start", "slot": 0}, {"action": "start", "slot": 0, "start": -1}, {"action": "start", "slot": 0, "start": "3"},
                    {"action": "start", "slot": 0, "start": True}, {"action": "start", "slot": 999, "start": 1.0},
                    {"action": "start", "slot": 0, "start": 999.0}, {"action": "start", "slot": 0, "start": 1.0, "clip": "a"}):
            with self.assertRaises(vl.EngineError, msg=str(bad)):
                vpf.learn(vpf.empty(), self.sb, {"decisions": [bad]}, self.inv, self.views)
        self.assertEqual(self.sb["rows"][0]["clip"], row["clip"])


class Data(unittest.TestCase):
    def test_the_page_data_has_a_timeline_left_out_and_starts(self):
        inv, sc, _ = tp.world_and_views(60)
        cfg = ts.cfg_of(ts.chap("a", 0, 59))
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
        orig = vid_review.strip_for
        vid_review.strip_for = lambda rec, wd, size=140: ["QUJD"] * 3
        try:
            with tempfile.TemporaryDirectory() as d:
                data = vid_review.build_data(sb, inv, sc, Path(d), log=lambda *_: None)
        finally:
            vid_review.strip_for = orig
        self.assertEqual(len(data["rows"]), len(sb["rows"]))
        first = data["rows"][0]["clips"][0]
        self.assertEqual(first["start"], round(sb["rows"][0]["start"], 2))
        self.assertNotIn("video", first, "no media folder, no video")
        self.assertIn("by_reason", data["left_out"])

    def test_the_page_text_has_the_new_controls_and_no_network(self):
        text = vid_review.PAGE
        for needle in ("id=\"timeline\"", "Start here", "localStorage", "Load decisions", "Start over", "What was left out"):
            self.assertIn(needle, text)
        for bad in ("http://", "https://", "<script src", "@import"):
            self.assertNotIn(bad, text)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Page(unittest.TestCase):
    def run_page(self, video):
        inv, sc, _ = tp.world_and_views(60)
        cfg = ts.cfg_of(ts.chap("a", 0, 59))
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
        o_strip, o_proxy = vid_review.strip_for, vid_review.make_proxy
        vid_review.strip_for = lambda rec, wd, size=140: ["QUJD"] * 3
        vid_review.make_proxy = lambda rec, dest, offset, span: True            # no media is needed to test the page logic
        try:
            with tempfile.TemporaryDirectory() as d:
                page = Path(d) / "r.html"
                vid_review.write(page, sb, inv, sc, d, log=lambda *_: None, video=video)
                text = page.read_text(encoding="utf-8")
                js = Path(d) / "r.js"
                js.write_text(text.split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
                r = subprocess.run(["node", str(HERE / "review_dom_check.js"), str(js)], capture_output=True, text=True)
        finally:
            vid_review.strip_for, vid_review.make_proxy = o_strip, o_proxy
        self.assertEqual(r.returncode, 0, r.stderr)
        return sb, inv, json.loads(r.stdout.strip().splitlines()[-1])

    def test_with_videos_the_start_is_recorded_from_the_player_and_decisions_survive_a_reload(self):
        sb, inv, out = self.run_page(True)
        self.assertGreater(out["videos"], 0)
        actions = [d["action"] for d in out["decisions"]]
        self.assertEqual(actions[0], "replace")
        start = next(d for d in out["decisions"] if d["action"] == "start")
        row = next(r for r in sb["rows"] if r["slot"] == start["slot"])
        by = {r["index"]: r for r in inv}
        offset, _span = vid_review.proxy_window(by[start["clip"]]["duration"], row["start"] if start["clip"] == row["clip"] else 0.0)
        self.assertAlmostEqual(start["start"], offset + 4.0, places=2)
        self.assertEqual(out["restored"], len(out["decisions"]), "the page comes back with its decisions after a reload")

    def test_without_videos_there_is_no_start_button(self):
        _sb, _inv, out = self.run_page(False)
        self.assertEqual(out["videos"], 0)
        self.assertNotIn("start", [d["action"] for d in out["decisions"]])
        self.assertEqual(out["restored"], len(out["decisions"]))


@NEEDS_FF
@helpers.slow
class Proxy(unittest.TestCase):
    def test_a_proxy_is_small_silent_and_playable(self):
        root = helpers.tmpdir() / "phase_c"
        root.mkdir(exist_ok=True)
        src = helpers.make_clip(root / "src.mp4", "testsrc2", size="640x360", seconds=6.0, audio="sine=f=220")
        rec = {"index": 1, "path": vl.ffp(src), "duration": 6.0}
        dest = root / "media" / "1.mp4"
        self.assertTrue(vid_review.make_proxy(rec, dest, 1.0, 3.0))
        info = vl.probe(dest)
        self.assertLessEqual(info["height"], vid_review.PROXY_HEIGHT)
        self.assertFalse(info["has_audio"])
        self.assertAlmostEqual(info["duration"], 3.0, delta=0.4)
        self.assertFalse(vid_review.make_proxy({"index": 2, "path": str(root / "missing.mp4"), "duration": 3.0}, root / "media" / "2.mp4", 0, 3))


if __name__ == "__main__":
    unittest.main()
