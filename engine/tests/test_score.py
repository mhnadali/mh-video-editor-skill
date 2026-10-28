"""Phase 2: clip scoring, same-scene hashes, ranking and story-ordered picking."""
import copy
import json
import os
import unittest
from pathlib import Path

import helpers
import mh_video
import vid_edl as ve
import vid_plan as vp
import vid_probe
import vid_score as vs
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def fake(index, quality=80.0, energy=50.0, flags=None, similar_to=None, dup_of=None, path=None):
    return {"index": index, "path": path or f"clip{index}.mp4", "name": f"clip{index}.mp4", "duration": 5.0,
            "dup_of": dup_of, "error": None, "quality": quality, "energy": energy, "flags": flags or [],
            "similar_to": similar_to, "best_start": 1.0 + index / 10, "blur": 4.0, "brightness": 120.0, "motion": 5.0,
            "flux": 0.0, "wins": [[0.0, 80.0, 1.0]], "hashes": [0, 0, 0]}


class Pure(unittest.TestCase):
    def test_windows_for(self):
        self.assertEqual(vs.windows_for(1.5), [0.0])
        w = vs.windows_for(20.0)
        self.assertEqual(len(w), 3)
        self.assertTrue(all(0 <= s <= 19.0 for s in w))
        self.assertEqual(w, sorted(w))

    def test_quality_is_monotonic(self):
        base = {"blur": 4.0, "brightness": 120.0, "contrast": 100.0, "motion": 5.0, "saturation": 20.0}
        sharper = dict(base, blur=3.0)
        blurrier = dict(base, blur=7.5)
        dark = dict(base, brightness=30.0)
        washed = dict(base, brightness=235.0)
        flat = dict(base, contrast=10.0)
        q = vs.quality(base)
        self.assertGreater(vs.quality(sharper), q)
        for worse in (blurrier, dark, washed, flat):
            self.assertLess(vs.quality(worse), q)
        for m in (base, sharper, blurrier, dark, washed, flat):
            self.assertTrue(0 <= vs.quality(m) <= 100)

    def test_motion_does_not_change_quality(self):
        a = {"blur": 4.0, "brightness": 120.0, "contrast": 100.0, "motion": 1.0}
        self.assertEqual(vs.quality(a), vs.quality(dict(a, motion=20.0)))

    def test_flags(self):
        ok = {"blur": 4.0, "brightness": 120.0, "contrast": 100.0, "motion": 5.0}
        self.assertEqual(vs.flags(ok), [])
        self.assertIn("dark", vs.flags(dict(ok, brightness=20)))
        self.assertIn("overexposed", vs.flags(dict(ok, brightness=230)))
        self.assertIn("flat", vs.flags(dict(ok, contrast=10)))
        self.assertIn("blurry", vs.flags(dict(ok, blur=12)))
        self.assertIn("static", vs.flags(dict(ok, motion=0.2)))

    def test_percentile_rank(self):
        self.assertEqual(vs.percentile_rank([5.0]), [50.0])
        r = vs.percentile_rank([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(r, [0.0, 25.0, 50.0, 75.0, 100.0])
        self.assertEqual(vs.percentile_rank([3.0, 3.0, 9.0])[0], vs.percentile_rank([3.0, 3.0, 9.0])[1])

    def test_hamming_and_similar_marking(self):
        self.assertEqual(vs.hamming(0b1010, 0b0110), 2)
        scores = [{"index": 0, "hash": 0}, {"index": 1, "hash": 0b111}, {"index": 2, "hash": (1 << 40) - 1}, {"index": 3, "hash": None}]
        n = vs.mark_similar(scores, max_distance=4)
        self.assertEqual(n, 1)
        self.assertEqual(scores[1]["similar_to"], 0)
        self.assertIsNone(scores[2]["similar_to"])
        self.assertIsNone(scores[3]["similar_to"])

    def test_candidates_filters(self):
        recs = [fake(i) for i in range(8)]
        recs[1]["dup_of"] = 0
        recs[2]["error"] = "broken"
        scores = {r["index"]: fake(r["index"]) for r in recs}
        scores[3]["flags"] = ["dark"]
        scores[4]["flags"] = ["static"]            # static is allowed (it is a hint, not a defect)
        scores[5]["similar_to"] = 0
        scores[6]["quality"] = 20.0
        base = {c["index"] for c in vs.candidates(recs, scores)}
        self.assertEqual(base, {0, 4, 6, 7})
        self.assertEqual({c["index"] for c in vs.candidates(recs, scores, skip_flagged=False)} - base, {3})
        self.assertEqual({c["index"] for c in vs.candidates(recs, scores, skip_similar=False)} - base, {5})
        self.assertNotIn(6, {c["index"] for c in vs.candidates(recs, scores, min_quality=50)})

    def test_parse_index_spec(self):
        self.assertIsNone(vs.parse_index_spec(None))
        self.assertIsNone(vs.parse_index_spec("  "))
        self.assertEqual(vs.parse_index_spec("0-3, 7,9-10"), {0, 1, 2, 3, 7, 9, 10})
        self.assertEqual(vs.parse_index_spec("5"), {5})
        for bad in ("a", "3-1", "1-b", "1;2"):
            with self.assertRaises(vl.EngineError):
                vs.parse_index_spec(bad)

    def test_only_and_exclude_shape_the_pool(self):
        recs = [fake(i) for i in range(10)]
        scores = {r["index"]: fake(r["index"]) for r in recs}
        ids = lambda **kw: {c["index"] for c in vs.candidates(recs, scores, **kw)}
        self.assertEqual(ids(only={2, 3, 4}), {2, 3, 4})
        self.assertEqual(ids(exclude={0, 1}), set(range(2, 10)))
        self.assertEqual(ids(only={0, 1, 2, 3}, exclude={1}), {0, 2, 3})
        self.assertEqual(ids(only=set()), set())              # an empty pool is empty, not "no filter"
        self.assertEqual(ids(only={99}), set())

    def test_pick_stays_inside_the_chosen_pool(self):
        recs = [fake(i, quality=60 + i % 30, energy=(i * 7) % 100) for i in range(40)]
        scores = {r["index"]: r for r in recs}
        pool = set(range(10, 30))
        chosen = vs.pick(vs.candidates(recs, scores, only=pool), intro=2, build=4, payoff=4, outro=1)
        self.assertTrue(all(c["index"] in pool for c in chosen))

    def test_pick_orders_the_story_and_never_repeats(self):
        cands = [fake(i, quality=60 + i % 30, energy=(i * 7) % 100) for i in range(60)]
        chosen = vs.pick(cands, intro=2, build=10, payoff=12, outro=1)
        self.assertEqual(len(chosen), 25)
        self.assertEqual(len({c["index"] for c in chosen}), 25)
        intro, build, payoff, outro = chosen[:2], chosen[2:12], chosen[12:24], chosen[24:]
        self.assertEqual([c["energy"] for c in build], sorted(c["energy"] for c in build))
        self.assertEqual([c["energy"] for c in payoff], sorted(c["energy"] for c in payoff))
        self.assertGreater(min(c["energy"] for c in payoff), sum(c["energy"] for c in build) / len(build) - 30)
        self.assertLessEqual(max(c["energy"] for c in intro + outro), 100)
        # calm opening: the intro clips are lower energy than the average payoff clip
        self.assertLess(sum(c["energy"] for c in intro) / 2, sum(c["energy"] for c in payoff) / len(payoff))

    def test_pick_with_too_few_candidates_explains_itself(self):
        with self.assertRaises(vl.EngineError) as cm:
            vs.pick([fake(i) for i in range(5)], 2, 3, 3, 1)
        self.assertIn("usable candidates", str(cm.exception))

    def test_apply_scores_fills_only_missing_src_start(self):
        scores = {0: fake(0, path="D:/a/one.mp4"), 1: fake(1, path="D:/a/two.mp4")}
        clips = [{"clip": "D:/a/one.mp4"}, {"clip": "D:/a/two.mp4", "src_start": 9.0}, {"clip": "D:/a/unknown.mp4"}]
        n = vp.apply_scores(clips, scores)
        self.assertEqual(n, 1)
        self.assertEqual(clips[0]["src_start"], scores[0]["best_start"])
        self.assertEqual(clips[1]["src_start"], 9.0)
        self.assertNotIn("src_start", clips[2])

    def test_incremental_rescoring_reuses_unchanged_files(self):
        recs = [{"index": i, "path": f"x{i}.mp4", "name": f"x{i}.mp4", "size": 100 + i, "mtime": 5, "duration": 3.0,
                 "dup_of": None, "error": None} for i in range(4)]
        previous = {}
        for r in recs:
            e = fake(r["index"], path=r["path"])
            e.update(size=r["size"], mtime=r["mtime"], motion=float(r["index"]))
            previous[r["path"]] = e
        stale = dict(previous["x0.mp4"])
        stale.pop("flux")                                                    # measured before the colour/flux fields existed
        self.assertNotIn("flux", stale)
        out = vs.score_inventory(recs, hw=False, previous=previous)           # no FFmpeg needed: all reused
        self.assertEqual([s["index"] for s in out], [0, 1, 2, 3])
        self.assertTrue(all("energy" in s for s in out))
        self.assertEqual([s["energy"] for s in out], sorted(s["energy"] for s in out))

    def test_exact_duplicates_are_never_scored(self):
        recs = [{"index": 0, "path": "a.mp4", "name": "a", "size": 1, "mtime": 1, "duration": 3.0, "dup_of": None, "error": None},
                {"index": 1, "path": "b.mp4", "name": "b", "size": 1, "mtime": 1, "duration": 3.0, "dup_of": 0, "error": None}]
        previous = {"a.mp4": dict(fake(0, path="a.mp4"), size=1, mtime=1)}
        out = vs.score_inventory(recs, hw=False, previous=previous)
        self.assertEqual([s["index"] for s in out], [0])

    def test_summary(self):
        s = vs.summarize([dict(fake(0), flags=["dark"]), dict(fake(1), flags=["dark", "static"]), {"index": 2, "error": "x"}])
        self.assertEqual(s["clips_scored"], 2)
        self.assertEqual(s["unmeasurable"], 1)
        self.assertEqual(s["flags"], {"dark": 2, "static": 1})

    def test_load_scores_requires_a_scoring_run(self):
        with self.assertRaises(vl.EngineError):
            vs.load_scores(helpers.tmpdir() / "no_scores_here")


@NEEDS_FF
class Measured(unittest.TestCase):
    """Real FFmpeg measurements on synthetic clips."""

    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "scoring"
        cls.root.mkdir(parents=True, exist_ok=True)
        mk = helpers.make_clip
        cls.sharp = mk(cls.root / "sharp.mp4", "testsrc2", size="640x360", seconds=4.0)
        cls.blurred = mk(cls.root / "blurred.mp4", "testsrc2", size="640x360", seconds=4.0, extra=["-vf", "gblur=sigma=14"])
        cls.dark = mk(cls.root / "dark.mp4", "testsrc2", size="640x360", seconds=4.0, extra=["-vf", "eq=brightness=-0.75"])
        cls.bright = mk(cls.root / "bright.mp4", "testsrc2", size="640x360", seconds=4.0, extra=["-vf", "eq=brightness=0.75"])
        # a truly static, textured picture: loop one frame
        still_png = cls.root / "still.png"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=1:d=1",
                "-frames:v", "1", str(still_png)])
        cls.static = cls.root / "static.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-t", "4", "-i",
                str(still_png), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.static)])
        cls.other = mk(cls.root / "other.mp4", "smptebars", size="640x360", seconds=4.0)
        cls.copy = cls.root / "copy.mp4"
        cls.copy.write_bytes(cls.sharp.read_bytes())
        cls.hlg = helpers.make_hlg_clip(cls.root / "hlg.mp4", 4.0)

    def rec(self, path, index=0):
        info = vl.probe(path)
        return {"index": index, "path": vl.ffp(path), "name": Path(path).name, "duration": info["duration"],
                "hdr": info["hdr"], "size": os.path.getsize(path), "mtime": int(os.path.getmtime(path)), "error": None,
                "dup_of": None}

    def test_sharp_beats_blurred(self):
        a = vs.score_clip(self.rec(self.sharp))
        b = vs.score_clip(self.rec(self.blurred))
        self.assertLess(a["blur"], b["blur"])
        self.assertGreater(a["quality"], b["quality"])

    def test_dark_and_bright_are_flagged(self):
        d = vs.score_clip(self.rec(self.dark))
        b = vs.score_clip(self.rec(self.bright))
        self.assertIn("dark", d["flags"])
        self.assertIn("overexposed", b["flags"])
        self.assertLess(d["brightness"], 60)
        self.assertGreater(b["brightness"], 190)

    def test_static_clip_is_flagged_and_moving_clip_is_not(self):
        s = vs.score_clip(self.rec(self.static))
        m = vs.score_clip(self.rec(self.sharp))
        self.assertIn("static", s["flags"])
        self.assertLess(s["motion"], m["motion"])

    def test_best_start_is_inside_the_clip(self):
        r = vs.score_clip(self.rec(self.sharp))
        self.assertTrue(0.0 <= r["best_start"] <= 4.0 - vs.WINDOW + 0.1)
        self.assertEqual(r["windows"], 3)

    def test_short_clip_uses_one_window(self):
        short = helpers.make_clip(self.root / "short.mp4", "testsrc2", seconds=1.2)
        r = vs.score_clip(self.rec(short))
        self.assertEqual(r["windows"], 1)
        self.assertEqual(r["best_start"], 0.0)

    def test_copy_has_same_hash_and_other_scene_differs(self):
        h1 = vs.score_clip(self.rec(self.sharp))["hash"]
        h2 = vs.score_clip(self.rec(self.copy))["hash"]
        h3 = vs.score_clip(self.rec(self.other))["hash"]
        self.assertLessEqual(vs.hamming(h1, h2), 2)
        self.assertGreater(vs.hamming(h1, h3), 8)

    def test_hdr_clip_is_measured_after_tone_mapping(self):
        r = vs.score_clip(self.rec(self.hlg))
        self.assertNotIn("error", r)
        self.assertGreater(r["brightness"], 20)

    def test_unreadable_clip_does_not_crash(self):
        bad = self.root / "bad.mp4"
        bad.write_bytes(b"nope")
        out = vs.score_clip({"index": 9, "path": vl.ffp(bad), "name": "bad.mp4", "duration": 3.0, "error": None, "hdr": False})
        self.assertIn("error", out)

    @classmethod
    def scored_work(cls) -> Path:
        """A probed + scored work folder over 8 synthetic clips (built once, shared by the CLI tests)."""
        work = cls.root / "work"
        if not (work / "scores.json").exists():
            footage = cls.root / "footage"
            footage.mkdir(exist_ok=True)
            srcs = ["testsrc2", "smptebars", "rgbtestsrc", "testsrc"]
            for i in range(8):
                helpers.make_clip(footage / f"c{i}.mp4", srcs[i % 4], size=f"{320 + 2 * i}x180", seconds=3.0 + i * 0.1)
            assert mh_video.main(["probe", str(footage), "--work", str(work)]) == 0
            assert mh_video.main(["score", "--work", str(work)]) == 0
        return work

    def test_cli_score_rank_pick_plan(self):
        work = self.scored_work()
        self.assertEqual(mh_video.main(["score", "--work", str(work)]), 0)
        first = vl.load_json(work / "scores.json")
        # second run: all reused (no change in files) -> identical numbers
        self.assertEqual(mh_video.main(["score", "--work", str(work)]), 0)
        self.assertEqual(vl.load_json(work / "scores.json")["scores"], first["scores"])
        self.assertEqual(mh_video.main(["rank", "--work", str(work), "--top", "5", "--allow-similar"]), 0)
        self.assertEqual(mh_video.main(["rank", "--work", str(work), "--by", "energy", "--allow-similar"]), 0)
        self.assertEqual(mh_video.main(["rank", "--work", str(work), "--only", "0-3", "--exclude", "1", "--allow-similar"]), 0)
        self.assertEqual(mh_video.main(["rank", "--work", str(work), "--only", "zero"]), 2)
        out = self.root / "picked.txt"
        self.assertEqual(mh_video.main(["pick", "--work", str(work), "--count", "6", "--out", str(out), "--allow-similar",
                                        "--allow-flagged", "--min-duration", "0", "--keep-repeats"]), 0)
        lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 6)
        self.assertTrue(all(" @ " in l for l in lines))
        self.assertEqual(len({l.split(" @ ")[0] for l in lines}), 6)
        # plan fills src_start from the best windows
        items = vp.parse_clip_list(out)
        self.assertTrue(all(i.get("src_start") is not None for i in items))
        plain = self.root / "plain.txt"
        plain.write_text("\n".join(i["clip"] for i in items) + "\n", encoding="utf-8")
        clips = vp.parse_clip_list(plain)
        n = vp.apply_scores(clips, vs.load_scores(work))
        self.assertEqual(n, 6)

    def test_pick_too_many_is_a_clean_error(self):
        work = self.scored_work()
        rc = mh_video.main(["pick", "--work", str(work), "--count", "50", "--out", str(self.root / "x.txt")])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
