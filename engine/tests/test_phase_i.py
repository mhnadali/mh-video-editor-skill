"""Phase I (0.25): the opening and the ending (story hook, teaser_clip, loop fit) and playing the whole storyboard over the music
on the review page."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import helpers
import mh_video
import test_story as ts
import vid_hook as vh
import vid_review
import vid_story as st
import vidlib as vl

HERE = Path(__file__).resolve().parent


def notes_for(inv, mapping):
    """shot-notes.json keyed by path, from {index: note}."""
    return {inv[i]["path"]: n for i, n in mapping.items()}


class Reading(unittest.TestCase):
    def test_an_intense_action_shot_is_the_strongest_opening_and_a_calm_portrait_the_weakest(self):
        self.assertEqual(vh.reading({"role": "action", "mood": "intense"}), 1.0)
        self.assertGreater(vh.reading({"role": "action", "mood": "building"}), vh.reading({"role": "establishing", "mood": "calm"}))
        self.assertLess(vh.reading({"role": "portrait", "mood": "calm"}), 0.3)
        self.assertIsNone(vh.reading({}), "an unread clip is not guessed from its motion")
        self.assertIsNone(vh.reading({"topic": "road"}))

    def test_quality_and_freshness_only_break_ties(self):
        strong, _ = vh.hook_score({"role": "action", "mood": "intense"}, 0.6, 0.0, 0.0)
        weak, _ = vh.hook_score({"role": "portrait", "mood": "calm"}, 1.0, 1.0, 1.0)
        self.assertGreater(strong, weak)
        a, _ = vh.hook_score({"role": "action", "mood": "intense"}, 0.9, 0.5, 1.0)
        b, why = vh.hook_score({"role": "action", "mood": "intense"}, 0.9, 0.5, 0.0)
        self.assertGreater(a, b)
        self.assertIn("repeat", "; ".join(why))


class Rank(unittest.TestCase):
    def setUp(self):
        self.inv, self.sc = ts.world(40)
        self.cfg = ts.cfg_of(ts.chap("a", 0, 19), ts.chap("b", 20, 39))
        self.sb = st.select(self.inv, self.sc, ts.BEATS, self.cfg, None, duration=15.0, pacing="energy")
        used = {r["clip"] for r in self.sb["rows"]}
        self.free = [i for i in range(40) if i not in used and self.inv[i].get("dup_of") is None]

    def test_read_clips_are_ranked_and_unread_ones_counted(self):
        a, b, c = self.free[:3]
        notes = notes_for(self.inv, {a: {"role": "action", "mood": "intense"}, b: {"role": "portrait", "mood": "calm"},
                                     c: {"role": "establishing", "mood": "building"}})
        rank = vh.hook_rank(self.inv, self.sc, self.sb, self.cfg, notes, top=3)
        self.assertEqual(rank["candidates"][0]["clip"], a)
        self.assertEqual(rank["considered"], 3)
        self.assertGreater(rank["unread"], 0)
        self.assertIsNone(rank["current"]["score"], "the opening has no note: its strength is not judged")

    def test_one_candidate_per_scene(self):
        a, b = self.free[:2]
        for i in (a, b):
            self.inv[i]["device"], self.inv[i]["taken_epoch"] = "cam", 1000.0 + i
        notes = notes_for(self.inv, {a: {"role": "action", "mood": "intense"}, b: {"role": "action", "mood": "intense"}})
        rank = vh.hook_rank(self.inv, self.sc, self.sb, self.cfg, notes, top=5)
        self.assertEqual(len([c for c in rank["candidates"] if c["clip"] in (a, b)]), 1)


class Loop(unittest.TestCase):
    def test_a_loop_needs_the_same_place(self):
        base = {"hash": 0, "csig": [100] * 12, "brightness": 120.0}
        near = dict(base, hash=(1 << 15) - 1, csig=[108] * 12)                     # 15 bits, colour 8 apart
        far = dict(base, hash=(1 << 32) - 1, csig=[100] * 12)                      # 32 bits: random pairs sit here
        self.assertTrue(vh.loops(near, base))
        self.assertFalse(vh.loops(far, base))
        self.assertFalse(vh.loops(dict(near, csig=[140] * 12), base), "same shapes, other colours")
        self.assertLess(vh.look_distance(near, base), vh.look_distance(far, base))

    def test_loop_fit_names_an_alternate_of_the_last_slot_that_loops(self):
        sc = {0: {"hash": 0, "csig": [100] * 12, "brightness": 120.0}, 1: {"hash": (1 << 40) - 1, "csig": [30] * 12, "brightness": 40.0},
              2: {"hash": 3, "csig": [101] * 12, "brightness": 121.0}}
        sb = {"rows": [{"clip": 0}, {"clip": 1, "alternates": [2]}]}
        fit = vh.loop_fit(sb, sc)
        self.assertFalse(fit["fits"])
        self.assertEqual(fit["alternates"][0], {"clip": 2, "distance": fit["alternates"][0]["distance"], "fits": True})
        self.assertIn("alternate clip 2", vh.format_report({"current": None, "candidates": [], "considered": 0, "unread": 0}, fit))


class Teaser(unittest.TestCase):
    def test_teaser_clip_opens_the_storyboard(self):
        inv, sc = ts.world(40)
        cfg = ts.cfg_of(ts.chap("a", 0, 19), ts.chap("b", 20, 39))
        cfg.update(hook="teaser", teaser_clip=31)
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=15.0, pacing="energy")
        self.assertEqual(sb["rows"][0]["clip"], 31)
        self.assertTrue(sb["rows"][0]["chapter"].endswith("(teaser)"))

    def test_a_teaser_clip_that_cannot_be_used_says_why(self):
        inv, sc = ts.world(40)
        for bad, msg in ((999, "in no chapter"), (-1, "clip number"), ("31", "clip number")):
            cfg = ts.cfg_of(ts.chap("a", 0, 19), ts.chap("b", 20, 39))
            cfg.update(hook="teaser", teaser_clip=bad)
            with self.assertRaises(vl.EngineError) as cm:
                st.select(inv, sc, ts.BEATS, cfg, None, duration=15.0, pacing="energy")
            self.assertIn(msg, str(cm.exception))

    def test_the_cli_parses_story_hook(self):
        a = mh_video.build_parser().parse_args(["story", "hook", "--work", "W", "--storyboard", "s.json", "--top", "3"])
        self.assertEqual((a.action, a.top), ("hook", 3))


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Player(unittest.TestCase):
    def page(self):
        rows = []
        for k, (t, ln) in enumerate(((0.0, 1.5), (1.5, 1.0), (2.5, 2.0))):
            clips = [{"index": 10 * k + j, "name": f"c{10 * k + j}", "frames": [], "start": 4.0 + j, "duration": 20.0,
                      "video": f"m/{10 * k + j}.mp4", "voffset": 2.0} for j in range(2)]
            rows.append({"slot": k, "t": t, "len": ln, "chapter": "x", "clip": clips[0]["index"], "why": "", "clips": clips})
        data = {"duration": 4.5, "variant": "balanced", "rows": rows, "music": "m/music.m4a", "left_out": {}}
        path = helpers.tmpdir() / "player_page.js"
        html = vid_review.PAGE.replace("__DATA__", json.dumps(data))
        path.write_text(html.split("<script>")[1].split("</script>")[0], encoding="utf-8")
        r = subprocess.run(["node", str(HERE / "review_player_check.js"), str(path)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_the_storyboard_plays_slot_by_slot_at_the_music_time(self):
        out = self.page()
        self.assertEqual(out["slotAt"], [0, 0, 1, 2, 2])
        self.assertEqual(out["cued"][0], {"src": "m/0.mp4", "t": 2.0, "k": "0"}, "slot 0 cued at its start inside the proxy")
        self.assertEqual(out["cued"][1]["src"], "m/10.mp4", "the next slot waits in the other video")
        self.assertEqual([f["k"] for f in out["frames"]], [0, 1, 2])
        self.assertEqual([f["src"] for f in out["frames"]], ["m/0.mp4", "m/10.mp4", "m/20.mp4"])
        self.assertEqual([f["marked"] for f in out["frames"]], ["now"] * 3)
        self.assertEqual(out["stopped"], {"on": False, "button": "Play the storyboard"})

    def test_choices_made_on_the_page_play_at_once(self):
        out = self.page()
        self.assertEqual(out["choice"]["slot1"], out["choice"]["alt"])
        self.assertEqual(out["choice"]["slot2at"], 7.25)


@unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
class Music(unittest.TestCase):
    def test_the_music_for_the_page_is_cut_to_the_storyboard(self):
        root = helpers.tmpdir() / "phase_i_music"
        root.mkdir(parents=True, exist_ok=True)
        wav = root / "m.wav"
        helpers.click_track(wav, bpm=120, first=0.2, seconds=6.0, step_at=None)
        name = vid_review.make_music({"file": str(wav)}, 3.0, root / "media")
        self.assertTrue(name and name.startswith("music_"))
        self.assertAlmostEqual(vl.probe(root / "media" / name)["duration"], 3.5, delta=0.2)
        self.assertEqual(vid_review.make_music({"file": str(wav)}, 3.0, root / "media"), name, "made once, then reused")
        self.assertIsNone(vid_review.make_music({}, 3.0, root / "media"))
        self.assertIsNone(vid_review.make_music({"file": str(root / "none.mp3")}, 3.0, root / "media"))


if __name__ == "__main__":
    unittest.main()
