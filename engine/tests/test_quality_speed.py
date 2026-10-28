"""0.14: quality levels, natural-colour defaults, 4K presets, and the single-pass scoring."""
import unittest

import helpers
import vid_edl as ve
import vid_editorial as ed
import vid_plan as vp
import vid_render as vr
import vid_score as vs
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class Quality(unittest.TestCase):
    def crf(self, args):
        return int(args[args.index("-crf") + 1])

    def test_levels_get_better_not_worse(self):
        std = vr.encoder_args("libx264", False, quality="standard")[0]
        high = vr.encoder_args("libx264", False, quality="high")[0]
        mx = vr.encoder_args("libx264", False, quality="max")[0]
        self.assertEqual(self.crf(std), 18)                         # the pre-0.14 behaviour is kept as `standard`
        self.assertLess(self.crf(high), self.crf(std))
        self.assertLess(self.crf(mx), self.crf(high))
        self.assertEqual(self.crf(vr.encoder_args("libx264", False)[0]), 18)    # no argument = standard (callers unchanged)

    def test_parts_are_cleaner_than_the_final_so_the_second_encode_does_not_compound_the_first(self):
        for name, q in vr.QUALITY.items():
            self.assertLessEqual(q["part_crf"], q["final_crf"], name)
        self.assertLess(vr.QUALITY["high"]["part_crf"], vr.QUALITY["standard"]["part_crf"])

    def test_previews_stay_light_and_unknown_levels_are_refused(self):
        pv = vr.encoder_args("libx264", True, quality="max")[0]
        self.assertEqual(self.crf(pv), 24)
        with self.assertRaises(vl.EngineError):
            vr.quality_settings("ultra")
        self.assertIn("quality", vr.render.__code__.co_varnames)

    def test_hardware_encoders_follow_the_level(self):
        import unittest.mock as mock
        with mock.patch.object(vr, "_resolve_encoder", return_value="nvenc"):
            hi = vr.encoder_args("nvenc", False, quality="high")[0]
            lo = vr.encoder_args("nvenc", False, quality="standard")[0]
        self.assertLess(int(hi[hi.index("-cq") + 1]), int(lo[lo.index("-cq") + 1]))

    def test_cli_exposes_quality(self):
        import mh_video
        a = mh_video.build_parser().parse_args(["render", "x.edl.json", "--quality", "max"])
        self.assertEqual(a.quality, "max")
        self.assertIsNone(mh_video.build_parser().parse_args(["render", "x.edl.json"]).quality)


class QaSizeRule(unittest.TestCase):
    def test_a_4k_picture_is_allowed_more_bytes_than_a_1080p_one(self):
        import vid_qa
        info_hd = {"duration": 45.0, "width": 1080, "height": 1920, "has_video": True}
        info_4k = dict(info_hd, width=2160, height=3840)
        for info, size, level in ((info_hd, 479_000_000, "error"), (info_4k, 479_000_000, "ok"), (info_4k, 2_500_000_000, "error")):
            checks = vid_qa.check_structure(info, None, size)
            self.assertEqual([c for c in checks if c["id"] == "file-size"][0]["level"], level, (info["width"], size))


class Defaults(unittest.TestCase):
    def test_colour_is_natural_by_default(self):
        self.assertEqual(ve.DEFAULT_STYLE["grade"], "neutral")
        import inspect
        self.assertEqual(inspect.signature(vp.plan).parameters["grade"].default, "neutral")
        import mh_video
        a = mh_video.build_parser().parse_args(["plan", "--beats", "b", "--clips", "c", "--out", "o"])
        self.assertEqual(a.grade, "neutral")

    def test_4k_presets(self):
        self.assertEqual((vl.load_preset("reels-9x16-4k")["width"], vl.load_preset("reels-9x16-4k")["height"]), (2160, 3840))
        self.assertEqual((vl.load_preset("youtube-16x9-4k")["width"], vl.load_preset("youtube-16x9-4k")["height"]), (3840, 2160))

    def test_docs_state_the_no_text_rule(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        discovery = (root / "references" / "discovery.md").read_text(encoding="utf-8")
        self.assertIn("do NOT ask", discovery)
        self.assertNotIn("title+end card", discovery)
        self.assertIn("No on-screen text", (root / "agents" / "mh-video-editor.md").read_text(encoding="utf-8"))


class Hash(unittest.TestCase):
    def test_hash_from_pixels_matches_the_definition(self):
        px = bytes([(x * 29 + y * 7) % 256 for y in range(8) for x in range(9)])
        h = 0
        for y in range(8):
            for x in range(8):
                h = (h << 1) | (1 if px[y * 9 + x] > px[y * 9 + x + 1] else 0)
        self.assertEqual(vs.hash_from_pixels(px), h)
        self.assertIsNone(vs.hash_from_pixels(b"short"))

    def test_editorial_from_frames_is_the_same_as_from_a_decode(self):
        frames = [bytes([(i * 37 + k * 3) % 200 for i in range(ed.GW * ed.GH)]) for k in range(6)]
        rgb = [bytes([200, 120, 40]) * (16 * 9)] * 3
        out = ed.measure_from_frames(frames, rgb)
        for k in ("shot_size", "motion_dx", "motion_dy", "focus_x", "flux", "internal_cut", "local_motion", "hue"):
            self.assertIn(k, out)
        self.assertIsNone(ed.measure_from_frames([]).get("shot_size"))
        self.assertNotIn("hue", ed.measure_from_frames(frames, None))


@NEEDS_FF
@helpers.slow
class SinglePassScore(unittest.TestCase):
    def test_one_decode_gives_every_measurement(self):
        root = helpers.tmpdir() / "fastscore"
        root.mkdir(exist_ok=True)
        clip = helpers.make_clip(root / "a.mp4", "testsrc2", size="320x568", seconds=4.0)
        m = vs.measure_window(vl.ffp(clip), 1.0, 1.0)
        self.assertEqual({k: len(v) for k, v in m["tiny"].items()}, {"gray": 6, "rgb": 3, "hash": 1})
        self.assertEqual(len(m["tiny"]["hash"][0]), 72)
        rec = {"index": 0, "path": vl.ffp(clip), "name": "a.mp4", "duration": 4.0, "size": 1, "mtime": 1, "error": None}
        out = vs.score_clip(rec)
        for k in ("quality", "best_start", "hash", "shot_size", "motion_dx", "focus_x", "hue", "flux", "local_motion"):
            self.assertIn(k, out, k)
        self.assertIsNotNone(out["hash"])
        self.assertNotIn("tiny", out)

    def test_it_agrees_with_the_separate_decode(self):
        root = helpers.tmpdir() / "fastscore"
        root.mkdir(exist_ok=True)
        clip = helpers.make_clip(root / "b.mp4", "smptebars", size="320x568", seconds=4.0)
        fast = vs.measure_window(vl.ffp(clip), 1.0, 1.0)["tiny"]
        slow_hash = vs.dhash(vl.ffp(clip), 1.5)
        self.assertLessEqual(bin(vs.hash_from_pixels(fast["hash"][0]) ^ slow_hash).count("1"), 8)       # the 'same scene' threshold
        slow = ed.measure_shot(vl.ffp(clip), 1.0, 1.0)
        quick = ed.measure_from_frames(fast["gray"], fast["rgb"])
        self.assertEqual(slow["shot_size"], quick["shot_size"])
        self.assertAlmostEqual(slow["hue"], quick["hue"], delta=0.05)


if __name__ == "__main__":
    unittest.main()
