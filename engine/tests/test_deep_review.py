"""Bugs found by the deep review of 0.16.0. Each one has a test that failed before the fix."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers
import test_profile as tp
import test_story as ts
import vid_moment as vm
import vid_profile as vpf
import vid_score
import vid_story as st
import vidlib as vl

HERE = Path(__file__).resolve().parent


class Selection(unittest.TestCase):
    def setUp(self):
        self.inv, self.sc, self.views = tp.world_and_views(90)
        self.cfg = ts.cfg_of(ts.chap("start", 0, 29, energy="calm"), ts.chap("action", 30, 59, energy="peak"),
                             ts.chap("end", 60, 89, energy="calm"))
        self.sb = st.select(self.inv, self.sc, ts.BEATS, self.cfg, None, duration=45.0, pacing="energy")

    def test_a_must_clip_that_is_too_short_is_still_refused(self):
        inv, sc = ts.world(90)
        inv[10]["duration"] = 3.0
        cfg = ts.cfg_of(ts.chap("a", 0, 44, must=[10]), ts.chap("b", 45, 89))
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
        self.assertNotIn(10, [r["clip"] for r in sb["rows"]])

    def test_a_chapter_cannot_take_more_shots_than_it_has_clips(self):
        inv, sc = ts.world(90)
        cfg = ts.cfg_of(ts.chap("a", 0, 5, weight=8), ts.chap("b", 6, 89, weight=1))
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
        self.assertEqual(len(sb["rows"]), sb["slots"], sb["warnings"])
        self.assertLessEqual({c["name"]: c["filled"] for c in sb["chapters"]}["a"], 6)
        self.assertTrue(any("chapter 'a' has only" in w for w in sb["warnings"]), "the user is told the chapter got fewer shots")

    def test_with_too_few_clips_in_total_each_chapter_gives_all_it_has(self):
        inv, sc = ts.world(20)
        cfg = ts.cfg_of(ts.chap("a", 0, 9), ts.chap("b", 10, 19))
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
        self.assertEqual(len(sb["rows"]), 20)
        self.assertTrue(sb["warnings"])

    def test_a_clip_the_user_chose_beats_the_better_measured_take_of_its_moment(self):
        inv, sc = ts.world(90)
        h = sc[40]["hash"]
        sc[40] = dict(sc[40], hash=h, hashes=[h] * 3, csig=[100] * 12, brightness=120.0, quality=60.0)
        sc[41] = dict(sc[41], hash=h, hashes=[h] * 3, csig=[100] * 12, brightness=120.0, quality=95.0)
        cfg = ts.cfg_of(ts.chap("a", 0, 89), flex=0.0)
        both = {40, 41}
        plain = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
        self.assertLessEqual(len(both & {r["clip"] for r in plain["rows"]}), 1)
        prof = dict(vpf.empty(), pins={inv[40]["path"]: 0})
        chosen = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy", profile=prof)
        clips = [r["clip"] for r in chosen["rows"]]
        self.assertIn(40, clips)
        self.assertNotIn(41, clips, "the other take of the moment is the one that is dropped")

    def test_clips_without_a_capture_time_do_not_break_the_reports(self):
        inv = [dict(r, taken=None, device=None) for r in self.inv]
        sb = st.select(inv, self.sc, ts.BEATS, self.cfg, None, duration=45.0, pacing="energy")
        st.storyboard_markdown(sb)
        st.clip_list_text(sb)
        st.format_storyboard(sb)
        self.assertIn("?", st.propose_chapters(inv, self.sc)["chapters"][0]["name"])


class Feedback(unittest.TestCase):
    def setUp(self):
        self.inv, self.sc, self.views = tp.world_and_views(90)
        cfg = ts.cfg_of(ts.chap("start", 0, 29, energy="calm"), ts.chap("action", 30, 59, energy="peak"),
                        ts.chap("end", 60, 89, energy="calm"))
        self.sb = st.select(self.inv, self.sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")

    def free_clip(self, lo=30, hi=60):
        return next(i for i in range(lo, hi) if i not in {r["clip"] for r in self.sb["rows"]})

    def test_a_replacement_cannot_duplicate_or_be_short_or_banned(self):
        other = self.sb["rows"][5]["clip"]
        with self.assertRaises(vl.EngineError):
            vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "replace", "slot": 0, "with": other}]}, self.inv, self.views)
        free = self.free_clip()
        views = dict(self.views)
        views[free] = dict(self.views[free], duration=2.0)
        with self.assertRaises(vl.EngineError):
            vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "replace", "slot": 0, "with": free}]}, self.inv, views)
        prof = dict(vpf.empty(), banned=[self.inv[free]["path"]])
        with self.assertRaises(vl.EngineError):
            vpf.learn(prof, self.sb, {"decisions": [{"action": "replace", "slot": 0, "with": free}]}, self.inv, self.views)

    def test_unknown_slots_and_clips_are_errors_not_silence(self):
        for bad in ({"action": "keep", "slot": 999}, {"action": "ban", "clip": 9999}, {"action": "unpin", "slot": 999}):
            with self.assertRaises(vl.EngineError):
                vpf.learn(vpf.empty(), self.sb, {"decisions": [bad]}, self.inv, self.views)

    def test_replace_pins_the_choice_and_drops_the_stale_moment(self):
        sb = dict(self.sb, rows=[dict(r, moment={"motion_type": "pan"}) for r in self.sb["rows"]])
        free = self.free_clip()
        prof, rows = vpf.learn(vpf.empty(), sb, {"decisions": [{"action": "replace", "slot": 2, "with": free}]}, self.inv, self.views)
        self.assertNotIn("moment", rows[2])
        self.assertIn("moment", rows[3], "other rows keep their analysis")
        self.assertEqual(vpf.pinned(prof, self.inv).get(free), 2)

    def test_a_ban_swaps_in_an_alternate_or_marks_the_slot(self):
        row = next(r for r in self.sb["rows"] if r["alternates"])
        prof, rows = vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "ban", "clip": row["clip"]}]}, self.inv, self.views)
        new = rows[row["slot"]]
        self.assertNotEqual(new["clip"], row["clip"])
        self.assertEqual(new["start_kind"], "alternate after a ban")
        sb2 = dict(self.sb, rows=[dict(r, alternates=[]) for r in self.sb["rows"]])
        prof, rows = vpf.learn(vpf.empty(), sb2, {"decisions": [{"action": "ban", "clip": row["clip"]}]}, self.inv, self.views)
        self.assertTrue(rows[row["slot"]].get("banned"))
        text = st.clip_list_text(dict(sb2, rows=rows))
        self.assertNotIn(row["path"], text)
        self.assertIn("was banned", text)
        # a ban from an EARLIER round still counts when the old storyboard is used again
        prof2, rows2 = vpf.learn(prof, sb2, {"decisions": [{"action": "prefer-topic", "topic": "x"}]}, self.inv, self.views)
        self.assertTrue(rows2[row["slot"]].get("banned"))
        _p3, rows3 = vpf.learn(prof2, sb2, {"decisions": [{"action": "unban", "clip": row["clip"]}]}, self.inv, self.views)
        self.assertFalse(rows3[row["slot"]].get("banned"))

    def test_bans_are_applied_after_replacements(self):
        row = self.sb["rows"][4]
        free = self.free_clip()
        decisions = [{"action": "ban", "clip": row["clip"]}, {"action": "replace", "slot": row["slot"], "with": free}]
        prof, rows = vpf.learn(vpf.empty(), self.sb, {"decisions": decisions}, self.inv, self.views)
        self.assertEqual(rows[row["slot"]]["clip"], free)
        self.assertEqual(prof["history"][0]["action"], "replace")

    def test_paths_match_whatever_the_slashes(self):
        path = self.inv[7]["path"]
        other = path.replace("/", "\\")
        prof = dict(vpf.empty(), banned=[other], favoured=[other])
        self.assertEqual(vpf.banned_indexes(prof, self.inv), {7})
        self.assertGreater(vpf.clip_bias(prof, {"path": path}), 0)

    def test_a_hand_edited_profile_is_checked_when_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "profile.json"
            for bad in ({"weights": {"people": "lots"}}, {"pins": {"a.mp4": "first"}}, {"banned": [3]}, {"topics": {"x": 5}}):
                p.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(vl.EngineError):
                    vpf.load(p)


class Moments(unittest.TestCase):
    def series(self, motion, n=None):
        n = n or len(motion)
        return {"start": 0.0, "fps": 6, "n": n, "motion": motion, "bright": [120.0] * n, "blur": [3.0] * n,
                "dx": [0.0] * n, "dy": [0.0] * n, "dxl": [0.0] * n, "dxr": [0.0] * n}

    def test_a_clip_where_nothing_moves_has_no_action_peak(self):
        seg = vm.best_segment(self.series([0.0] * 60), 2.0, "action")
        self.assertIsNone(seg["peak_at"])
        self.assertIsNone(seg["end_of_action"])
        self.assertGreater(seg["start"], 0.3, "it does not cling to the start of the clip")

    def test_a_cut_is_not_the_action_peak(self):
        motion = [3.0] * 60
        motion[10] = 90.0                       # a cut or a flash
        for i in range(40, 46):
            motion[i] = 12.0                    # the real action
        seg = vm.best_segment(self.series(motion), 1.5, "action")
        self.assertAlmostEqual(seg["peak_at"], 41 / 6.0, delta=1.0)
        self.assertFalse(seg["had_cut"])


class Caching(unittest.TestCase):
    def test_a_cached_score_is_reused_by_schema_and_remeasured_when_the_schema_is_older(self):
        rec = {"index": 0, "path": "x.mp4", "name": "x.mp4", "size": 5, "mtime": 7, "duration": 6.0}
        old = {"index": 3, "path": "x.mp4", "size": 5, "mtime": 7, "quality": 60.0, "motion": 1.0, "flags": [], "v": vid_score.SCHEMA}
        calls = []
        orig = vid_score.score_clip
        vid_score.score_clip = lambda r, hw=False: calls.append(r) or {"index": r["index"], "quality": 1.0, "motion": 0.0, "flags": []}
        try:
            out = vid_score.score_inventory([rec], hw=False, previous={"x.mp4": old})
            self.assertEqual(calls, [])
            self.assertEqual(out[0]["index"], 0)
            vid_score.score_inventory([rec], hw=False, previous={"x.mp4": {k: v for k, v in old.items() if k != "v"}})
            self.assertEqual(len(calls), 1, "an entry without the fields of this version is measured again")
        finally:
            vid_score.score_clip = orig


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ReviewPage(unittest.TestCase):
    def test_the_page_records_decisions_and_exports_them(self):
        import vid_review
        inv, sc, _ = tp.world_and_views(60)
        cfg = ts.cfg_of(ts.chap("a", 0, 59))
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
        orig = vid_review.strip_for
        vid_review.strip_for = lambda rec, wd, size=140: ["QUJD"] * 3
        try:
            with tempfile.TemporaryDirectory() as d:
                page = Path(d) / "r.html"
                vid_review.write(page, sb, inv, sc, d, log=lambda *_: None)
                text = page.read_text(encoding="utf-8")
                js = Path(d) / "r.js"
                js.write_text(text.split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
                r = subprocess.run(["node", str(HERE / "review_dom_check.js"), str(js)], capture_output=True, text=True)
        finally:
            vid_review.strip_for = orig
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["slots"], len(sb["rows"]))
        self.assertEqual([d["action"] for d in out["decisions"]], ["replace", "ban", "ban"])


if __name__ == "__main__":
    unittest.main()
