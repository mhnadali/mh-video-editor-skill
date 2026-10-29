"""Interactions between features, found by the audit of 0.23.0 on the real Kavir pipeline. Each test failed before its fix."""
import os
import unittest
from unittest import mock

import helpers
import mh_video
import test_profile as tp
import test_story as ts
import vid_cutlogic as cl
import vid_editorial as ed
import vid_filter as vf
import vid_moment as vm
import vid_profile as vpf
import vid_score
import vid_story as st
import vidlib as vl


class ScoreAndFilters(unittest.TestCase):
    def test_min_duration_keeps_earlier_measurements_of_short_clips(self):
        recs = [{"index": i, "path": f"x{i}.mp4", "name": f"x{i}", "size": i, "mtime": 1, "duration": 3.0 if i < 2 else 8.0,
                 "dup_of": None, "error": None} for i in range(5)]
        prev = {r["path"]: {"index": r["index"], "path": r["path"], "size": r["size"], "mtime": 1, "quality": 50.0,
                            "motion": float(r["index"]), "flags": [], "v": vid_score.SCHEMA} for r in recs}
        with mock.patch.object(vid_score, "score_clip", side_effect=AssertionError("nothing needs measuring")):
            full = vid_score.score_inventory(recs, hw=False, previous=prev)
            short_skipped = vid_score.score_inventory(recs, hw=False, previous=prev, min_duration=5.0)
        self.assertEqual([s["index"] for s in short_skipped], [0, 1, 2, 3, 4], "measured short clips are kept, in order")
        self.assertEqual([s["energy"] for s in short_skipped], [s["energy"] for s in full], "the percentiles do not move")
        with mock.patch.object(vid_score, "score_clip", side_effect=lambda r, hw=False: dict(prev[r["path"]], index=r["index"])):
            fresh = vid_score.score_inventory(recs, hw=False, previous={}, min_duration=5.0)
        self.assertEqual([s["index"] for s in fresh], [2, 3, 4], "a short clip measured never before is not measured now")

    def test_a_short_clip_is_short_not_also_unmeasured(self):
        self.assertEqual(vf.usable({"duration": 3.0}, None), ["short"])
        self.assertEqual(vf.usable({"duration": 8.0}, None), ["not-scored"])


class Refine(unittest.TestCase):
    def test_a_start_the_user_chose_or_approved_is_not_moved(self):
        series = {"start": 0.0, "fps": 6, "n": 60, "motion": [3.0] * 60, "bright": [120.0] * 60, "blur": [3.0] * 60,
                  "dx": [0.0] * 60, "dy": [0.0] * 60, "dxl": [0.0] * 60, "dxr": [0.0] * 60}
        sb = {"rows": [{"slot": 0, "clip": 0, "len": 1.0, "start": 4.2, "start_kind": "set by the user"},
                       {"slot": 1, "clip": 1, "len": 1.0, "start": 2.5, "start_kind": "as approved"},
                       {"slot": 2, "clip": 2, "len": 1.0, "start": 9.9, "start_kind": "best frame window"}]}
        inv = [{"index": i, "path": f"c{i}.mp4", "duration": 10.0} for i in range(3)]
        with mock.patch.object(vm, "analyse_clip", return_value=series):
            out = vm.refine_storyboard(sb, inv, {}, helpers.tmpdir())
        self.assertEqual([r["start"] for r in out["rows"][:2]], [4.2, 2.5])
        self.assertIn("moment", out["rows"][0], "the clip is still analysed")
        self.assertNotEqual(out["rows"][2]["start"], 9.9)


class Audit(unittest.TestCase):
    def test_trip_order_is_not_required_inside_a_chapter_ordered_by_energy(self):
        inv, sc = ts.world(60)
        edl = ts.edl_for([2, 1, 0, 30, 31, 32])
        cfg = ts.cfg_of(ts.chap("a", 0, 29, order="energy"), ts.chap("b", 30, 59))
        rep = st.audit_edl(edl, inv, sc, None, None, cfg)
        self.assertEqual(rep["stats"]["order_inversions"], 0)
        cfg2 = ts.cfg_of(ts.chap("a", 0, 29), ts.chap("b", 30, 59))
        self.assertEqual(st.audit_edl(edl, inv, sc, None, None, cfg2)["stats"]["order_inversions"], 2)


class Sizes(unittest.TestCase):
    def feats(self, sizes, source):
        return [{"shot_size": s, "shot_size_from": source} for s in sizes]

    def test_measured_size_runs_are_a_note_and_noted_runs_a_warning(self):
        edl = {"shots": [{}] * 5}
        bounds = [0, 30, 60, 90, 120, 150]
        noted = [i for i in ed.editorial_issues(edl, self.feats(["wide"] * 5, "notes"), bounds, {}) if i["code"] == "shot-size-repeat"]
        measured = [i for i in ed.editorial_issues(edl, self.feats(["wide"] * 5, "measured"), bounds, {}) if i["code"] == "shot-size-repeat"]
        self.assertTrue(noted and all(i["level"] == "warn" for i in noted))
        self.assertEqual([i["level"] for i in measured], ["info"])
        three = [i for i in ed.editorial_issues({"shots": [{}] * 3}, self.feats(["wide"] * 3, "measured"), [0, 30, 60, 90], {})
                 if i["code"] == "shot-size-repeat"]
        self.assertEqual(three, [], "three measured shots in a row are not even mentioned")

    def test_notes_mark_their_size(self):
        self.assertEqual(cl.override_score({"shot_size": "wide"}, {"shot_size": "close"})["shot_size_from"], "notes")
        self.assertEqual(cl.override_score({"shot_size": "wide"}, {})["shot_size_from"], "measured")

    def test_a_measured_size_costs_half_in_the_sequence(self):
        w = dict(st.WEIGHTS)
        base = {"topic": None, "brightness": None, "motion_dx": None, "motion_mag": None, "csig": [], "device": "d"}
        known = [dict(base, size="wide", size_known=True), dict(base, size="wide", size_known=True)]
        guess = [dict(base, size="wide", size_known=False), dict(base, size="wide", size_known=False)]
        vk, _ = st.transition_value(known[:1], known[1], w)
        vg, _ = st.transition_value(guess[:1], guess[1], w)
        self.assertAlmostEqual(vg, vk / 2)


class Taste(unittest.TestCase):
    def setUp(self):
        self.inv, self.sc, self.views = tp.world_and_views(90)
        cfg = ts.cfg_of(ts.chap("a", 0, 44), ts.chap("b", 45, 89))
        self.cfg = cfg
        self.sb = st.select(self.inv, self.sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")

    def test_a_chosen_start_is_remembered_pins_the_clip_and_survives_a_new_selection(self):
        row = self.sb["rows"][6]
        prof, rows = vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "start", "slot": row["slot"], "clip": row["clip"],
                                                                     "start": 3.3}]}, self.inv, self.views)
        self.assertEqual(vpf.start_of(prof, row["path"]), 3.3)
        self.assertIn(row["clip"], vpf.pinned(prof, self.inv))
        again = st.select(self.inv, self.sc, ts.BEATS, self.cfg, None, duration=45.0, pacing="energy", profile=prof)
        hit = next(r for r in again["rows"] if r["clip"] == row["clip"])
        self.assertEqual((hit["start"], hit["start_kind"]), (3.3, "set by the user"))

    def test_starts_are_checked_and_stay_with_the_project(self):
        with self.assertRaises(vl.EngineError):
            vpf.validate(dict(vpf.empty(), starts={"a.mp4": -1}))
        glob = vpf.promote(dict(vpf.empty(), starts={"a.mp4": 2.0}, topics={"x": 0.1}), vpf.empty())
        self.assertEqual(glob["starts"], {})

    def test_look_alike_notes_of_a_changed_slot_are_cleared(self):
        sb = dict(self.sb, rows=[dict(r) for r in self.sb["rows"]])
        sb["rows"][2]["lookalike"], sb["rows"][9]["lookalike"], sb["rows"][4]["lookalike"] = [9], [2], [12]
        free = next(i for i in range(90) if i not in {r["clip"] for r in sb["rows"]} and self.views[i]["duration"] >= 5)
        _p, rows = vpf.learn(vpf.empty(), sb, {"decisions": [{"action": "replace", "slot": 2, "with": free}]}, self.inv, self.views)
        self.assertEqual(rows[2]["lookalike"], [])
        self.assertEqual(rows[9]["lookalike"], [], "the other side no longer points at the replaced clip")
        self.assertEqual(rows[4]["lookalike"], [12], "unrelated notes stay")


class Relink(unittest.TestCase):
    def test_rows_follow_their_files_after_the_inventory_is_numbered_again(self):
        inv, sc = ts.world(30)
        sb = st.select(inv, sc, ts.BEATS, ts.cfg_of(ts.chap("a", 0, 29)), None, duration=15.0, pacing="energy")
        shifted = [dict(r, index=r["index"] + 1) for r in inv] + [dict(inv[0], index=0, path="new.mp4")]
        out, moved = st.relink(sb, shifted)
        self.assertTrue(moved)
        self.assertEqual([r["clip"] for r in out["rows"]], [r["clip"] + 1 for r in sb["rows"]])
        self.assertTrue(all(r["alternates"] == [] for r in out["rows"]))
        same, moved2 = st.relink(sb, inv)
        self.assertFalse(moved2)
        self.assertEqual(same["rows"], sb["rows"])
        with self.assertRaises(vl.EngineError):
            st.relink(sb, [r for r in inv if r["index"] != sb["rows"][0]["clip"]])

    def test_feedback_refuses_decisions_made_on_old_numbers(self):
        inv, sc = ts.world(30)
        root = helpers.tmpdir() / "relink_cli"
        root.mkdir(exist_ok=True)
        sb = st.select(inv, sc, ts.BEATS, ts.cfg_of(ts.chap("a", 0, 29)), None, duration=15.0, pacing="energy")
        shifted = [dict(r, index=r["index"] + 1) for r in inv] + [dict(inv[0], index=0, path="new.mp4")]
        vl.save_json(root / "inventory.json", {"summary": {}, "clips": shifted})
        vl.save_json(root / "scores.json", {"summary": {}, "scores": [dict(s, index=s["index"] + 1) for s in sc.values()]})
        vl.save_json(root / "sb.json", sb)
        vl.save_json(root / "fb.json", {"decisions": [{"action": "keep", "slot": 0}]})
        a = mh_video.build_parser().parse_args(["story", "feedback", "--work", str(root), "--storyboard", str(root / "sb.json"),
                                                "--feedback", str(root / "fb.json")])
        with self.assertRaises(vl.EngineError) as cm:
            mh_video.cmd_story(a)
        self.assertIn("inventory changed", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
