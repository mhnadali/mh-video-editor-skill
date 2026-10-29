"""Phase F (several outputs, taste across projects): cut-downs of an approved storyboard, promoting taste, the story arc in music."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers
import mh_video
import test_story as ts
import vid_profile as vpf
import vid_story as st
import vidlib as vl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pick_music as pm  # noqa: E402


def approved_world():
    inv, sc = ts.world(90, devices=("iPhone", "DJI"))
    cfg = ts.cfg_of(ts.chap("a", 0, 29, weight=1, energy="calm"), ts.chap("b", 30, 59, weight=3, energy="peak"),
                    ts.chap("c", 60, 89, weight=1, energy="calm"))
    full = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
    approved = {r["clip"]: round(r["start"] + 0.37, 2) for r in full["rows"]}           # the user moved every start a little
    return inv, sc, cfg, full, approved


class CutDown(unittest.TestCase):
    def test_a_shorter_cut_uses_only_approved_shots_at_their_starts(self):
        inv, sc, cfg, full, approved = approved_world()
        short = st.select(inv, sc, ts.BEATS, cfg, None, duration=15.0, pacing="energy", approved=approved)
        clips = [r["clip"] for r in short["rows"]]
        self.assertEqual(len(clips), short["slots"])
        self.assertTrue(set(clips) <= set(approved))
        for r in short["rows"]:
            self.assertEqual(r["start"], approved[r["clip"]])
            self.assertEqual(r["start_kind"], "as approved")
        self.assertEqual(short["cutdown_of"], len(approved))
        self.assertIsNone(full["cutdown_of"])

    def test_a_too_short_cut_drops_the_lightest_chapters_instead_of_failing(self):
        inv, sc = ts.world(90)
        chapters = [ts.chap(f"c{k}", 10 * k, 10 * k + 9, weight=(5 if k == 4 else 1 + k % 2)) for k in range(9)]
        cfg = ts.cfg_of(*chapters)
        with self.assertRaises(vl.EngineError):
            st.select(inv, sc, ts.BEATS, cfg, None, duration=4.0, pacing="beat")
        approved = {i: 1.0 for i in range(90)}
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=4.0, pacing="beat", approved=approved)
        self.assertEqual(len(sb["rows"]), sb["slots"])
        left_out = [w for w in sb["warnings"] if "is left out" in w]
        self.assertTrue(left_out)
        self.assertIn("c4", {r["chapter"] for r in sb["rows"]}, "the heaviest chapter stays")

    def test_allocate_drops_by_weight_then_energy(self):
        slots = [{"music": 0.5}] * 3
        chs = [ts.chap("a", 0, 1, weight=1, energy="calm"), ts.chap("b", 2, 3, weight=1, energy="peak"),
               ts.chap("c", 4, 5, weight=2), ts.chap("d", 6, 7, weight=3), ts.chap("e", 8, 9, weight=1, energy="building")]
        counts = st.allocate(chs, slots, drop=True)
        self.assertEqual(sum(counts), 3)
        self.assertEqual(counts[0], 0, "weight 1 and calm goes first")
        self.assertEqual(counts[4], 0)
        self.assertGreater(counts[3], 0)

    def test_the_cli_reads_an_approved_storyboard(self):
        inv, sc, cfg, full, approved = approved_world()
        root = helpers.tmpdir() / "phase_f_cli"
        root.mkdir(exist_ok=True)
        vl.save_json(root / "inventory.json", {"summary": {}, "clips": inv})
        vl.save_json(root / "scores.json", {"summary": {}, "scores": list(sc.values())})
        vl.save_json(root / "beats.json", ts.BEATS)
        vl.save_json(root / "ch.json", cfg)
        full["rows"][0]["banned"] = True
        vl.save_json(root / "approved.json", full)
        a = mh_video.build_parser().parse_args(["story", "select", "--work", str(root), "--beats", str(root / "beats.json"),
                                                "--chapters", str(root / "ch.json"), "--duration", "15", "--no-profile",
                                                "--cut-from", str(root / "approved.json"), "--out", str(root / "short.json")])
        self.assertEqual(mh_video.cmd_story(a), 0)
        short = vl.load_json(root / "short.json")
        self.assertNotIn(full["rows"][0]["clip"], [r["clip"] for r in short["rows"]], "a banned row is not approved")


class Promote(unittest.TestCase):
    def test_taste_is_averaged_and_clip_decisions_stay_with_the_project(self):
        project = dict(vpf.empty(), topics={"food": -0.2, "dunes": 0.1}, roles={"portrait": 0.08}, weights={"people": 0.3},
                       banned=["a.mp4"], favoured=["b.mp4"], pins={"c.mp4": 3})
        glob = dict(vpf.empty(), topics={"food": 0.1}, weights={"people": 0.2, "fit": 0.3})
        out = vpf.promote(project, glob, "p/profile.json")
        self.assertEqual(out["topics"], {"food": -0.05, "dunes": 0.1})
        self.assertEqual(out["roles"], {"portrait": 0.08})
        self.assertEqual(out["weights"], {"people": 0.3, "fit": 0.3})
        self.assertEqual((out["banned"], out["favoured"], out["pins"]), ([], [], {}))
        self.assertEqual(out["history"][-1]["action"], "promote")

    def test_the_cli_promotes_and_forgets(self):
        root = helpers.tmpdir() / "phase_f_promote"
        root.mkdir(exist_ok=True)
        vpf.save(dict(vpf.empty(), topics={"food": -0.12}, banned=["x.mp4"]), root / "profile.json")
        gpath = root / "global" / "profile.json"
        parser = mh_video.build_parser()
        with mock.patch.dict(os.environ, {"MH_PROFILE": str(gpath)}), mock.patch("builtins.print"):
            vl.save_json(root / "inventory.json", {"clips": []})
            vl.save_json(root / "scores.json", {"scores": []})
            self.assertEqual(mh_video.cmd_story(parser.parse_args(["story", "profile", "--work", str(root), "--promote"])), 0)
            self.assertEqual(vpf.load(gpath)["topics"], {"food": -0.12})
            self.assertEqual(vpf.load(gpath)["banned"], [])
            self.assertEqual(mh_video.cmd_story(parser.parse_args(["story", "profile", "--work", str(root), "--forget-global"])), 0)
        self.assertFalse(gpath.exists())


class MusicArc(unittest.TestCase):
    CFG = {"chapters": [{"name": "calm", "from": 0, "to": 9, "weight": 1, "energy": "calm"},
                        {"name": "peak", "from": 10, "to": 19, "weight": 1, "energy": "peak"}]}

    def test_the_arc_follows_weights_and_energy(self):
        arc = pm.story_arc(self.CFG, n=4)
        self.assertEqual(len(arc), 4)
        self.assertLess(arc[0], arc[-1])
        self.assertEqual(arc[0], arc[1])

    def test_music_that_follows_the_story_ranks_higher(self):
        rising = {"bpm": 120.0, "duration": 30.0, "confidence": 0.8, "drop": 15.0,
                  "sections": [{"start": 0.0, "end": 15.0, "energy": 0.2}, {"start": 15.0, "end": 30.0, "energy": 0.9}]}
        falling = dict(rising, sections=[{"start": 0.0, "end": 15.0, "energy": 0.9}, {"start": 15.0, "end": 30.0, "energy": 0.2}])
        flat = dict(rising, sections=[])
        tracks = {"rise.mp3": rising, "fall.mp3": falling, "flat.mp3": flat}
        with mock.patch.object(pm, "list_audio", return_value=[Path(k) for k in tracks]), \
                mock.patch.object(pm.vid_beats, "analyse", side_effect=lambda p, max_seconds=None: tracks[Path(p).name]):
            ranked = pm.rank("x", target_seconds=30.0, chapters=self.CFG)
        names = [Path(r["file"]).name for r in ranked]
        self.assertEqual(names[0], "rise.mp3")
        self.assertEqual(names[-1], "fall.mp3")
        by = {Path(r["file"]).name: r for r in ranked}
        self.assertGreater(by["rise.mp3"]["arc_fit"], 0.9)
        self.assertLess(by["fall.mp3"]["arc_fit"], -0.9)
        self.assertIsNone(by["flat.mp3"]["arc_fit"])

    def test_correlation_of_a_flat_series_is_unknown(self):
        self.assertIsNone(pm.arc_correlation([1, 1, 1], [1, 2, 3]))
        self.assertAlmostEqual(pm.arc_correlation([1, 2, 3], [2, 4, 6]), 1.0)


if __name__ == "__main__":
    unittest.main()
