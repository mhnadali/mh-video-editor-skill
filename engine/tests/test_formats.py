"""Phase 4: the same EDL in 9:16, 16:9 and 1:1 (reframing: cover with a focus point, blur-fill, fit)."""
import copy
import json
import unittest
from pathlib import Path

import helpers
import mh_video
import vid_edl as ve
import vid_qa as qa
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def edl(shots, duration, **style):
    return {"version": 1, "project": {"name": "f", "width": 270, "height": 480, "fps": 30, "duration": duration, "music": None},
            "style": dict({"grade": "neutral", "zoom": 0, "drop_flash": False, "end_flare": False}, **style), "transitions": {},
            "shots": shots}


class Sizes(unittest.TestCase):
    def test_output_size(self):
        e = edl([{"clip": "a", "span": [0, 1]}], 1.0)
        self.assertEqual(vr.output_size(e), (270, 480))
        self.assertEqual(vr.output_size(e, "youtube-16x9"), (1920, 1080))
        self.assertEqual(vr.output_size(e, "square-1x1"), (1080, 1080))
        self.assertEqual(vr.output_size(e, "youtube-16x9", 0.5), (960, 540))
        self.assertEqual(vr.output_size(e, None, 0.5), (136, 240))
        with self.assertRaises(vl.EngineError):
            vr.output_size(e, "no-such-format")

    def test_format_tag(self):
        self.assertEqual(vr.format_tag("youtube-16x9"), "16x9")
        self.assertEqual(vr.format_tag("reels-9x16"), "9x16")
        self.assertEqual(vr.format_tag("square-1x1"), "1x1")
        self.assertEqual(vr.format_tag(None), "")
        self.assertEqual(vr.format_tag("my_custom"), "mycustom")

    def test_reframe_defaults(self):
        e = edl([{"clip": "a.mp4", "span": [0, 2.0]}], 2.0)
        bounds = ve.timeline(e)
        native = vr.part_plan(e, 0, bounds, {}, 1.0)
        self.assertEqual(native["reframe"], "cover")
        other = vr.part_plan(e, 0, bounds, {}, 1.0, "youtube-16x9")
        self.assertEqual(other["reframe"], "blur-fill")                                   # never silently crop most of the picture
        e["style"]["reframe"] = "cover"
        explicit = vr.part_plan(e, 0, bounds, {}, 1.0, "youtube-16x9")
        self.assertEqual(explicit["reframe"], "cover")                                    # the user's choice wins
        e["style"].pop("reframe")
        e["shots"][0]["reframe"] = "fit"
        self.assertEqual(vr.part_plan(e, 0, bounds, {}, 1.0, "youtube-16x9")["reframe"], "fit")

    def test_reframe_filters(self):
        pl = {"W": 960, "H": 540, "reframe": "cover", "focus": None}
        self.assertEqual(vr._reframe(pl), ["scale=960:540:force_original_aspect_ratio=increase", "crop=960:540"])
        pl["focus"] = [0.2, 0.7]
        cover = vr._reframe(pl)
        self.assertIn("x='(iw-ow)*0.2000'", cover[1])
        self.assertIn("y='(ih-oh)*0.7000'", cover[1])
        pl["reframe"] = "fit"
        self.assertIn("pad=960:540", vr._reframe(pl)[0])
        pl["reframe"] = "blur-fill"
        bf = vr._reframe(pl)[0]
        self.assertIn("gblur=sigma=", bf)
        self.assertIn("overlay=x=(W-w)/2:y=(H-h)/2", bf)
        self.assertIn("split=2[bgi][fgi]", bf)

    def test_front_chain_is_valid_syntax_shape(self):
        pl = {"n": 20, "fps": 30, "W": 540, "H": 960, "reframe": "blur-fill", "focus": None, "speed": None}
        front = vr.part_front(pl, False)
        self.assertTrue(front.startswith("fps=30,split=2"))
        self.assertTrue(front.endswith("setsar=1"))


def make_split_clip(path: Path, size: str, left: int = 200, right: int = 40, seconds: float = 2.0) -> Path:
    """Left half bright, right half dark (neutral colour): shows which part of the picture a crop keeps."""
    vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c=gray:s={size}:r=30:d={seconds},geq=lum='if(lt(X,W/2),{left},{right})':cb=128:cr=128",
            "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", str(path)])
    return path


def column_means(video, t=0.7, width=None):
    """Mean luma of the left and right 10% columns and of the centre, via raw gray frames."""
    import subprocess
    info = vl.probe(video)
    w, h = info["width"], info["height"]
    r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-ss", str(t), "-i", vl.ffp(video), "-frames:v", "1", "-pix_fmt", "gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    buf = r.stdout
    cols = lambda x0, x1: [buf[y * w + x] for y in range(h) for x in range(x0, x1)]
    k = max(2, w // 10)
    avg = lambda v: sum(v) / len(v)
    return avg(cols(0, k)), avg(cols(w // 2 - k // 2, w // 2 + k // 2)), avg(cols(w - k, w))


def count_frames(path) -> int:
    r = vl.run([vl.find_ffmpeg()["ffprobe"], "-v", "error", "-count_frames", "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", vl.ffp(path)])
    return int(r.stdout.strip().rstrip(","))


@NEEDS_FF
@helpers.slow
class Renders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "formats"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.land = make_split_clip(cls.root / "land.mp4", "640x360")                  # 16:9 source
        cls.port = make_split_clip(cls.root / "port.mp4", "360x640")                  # 9:16 source
        cls.sq = make_split_clip(cls.root / "sq.mp4", "400x400")

    def render(self, e, name, **kw):
        p = self.root / f"{name}.edl.json"
        vl.save_json(p, e)
        kw.setdefault("encoder", "libx264")
        kw.setdefault("workdir", self.root / f"{name}_work")
        kw.setdefault("preview", True)
        return vr.render(p, out=self.root / f"{name}.mp4", log=lambda *_: None, **kw), p

    def two_shots(self, a, b, **extra):
        return edl([{"clip": vl.ffp(a), "span": [0, 1.5], "src_start": 0.1, **extra}, {"clip": vl.ffp(b), "span": [1.5, 3.0], "src_start": 0.1}], 3.0)

    def test_every_standard_format_has_the_right_size_and_frame_count(self):
        e = self.two_shots(self.land, self.port)
        for fmt, size in (("reels-9x16", (540, 960)), ("youtube-16x9", (960, 540)), ("square-1x1", (540, 540))):
            res, _ = self.render(e, "std_" + fmt, fmt=fmt)
            info = vl.probe(res["out"])
            self.assertEqual((info["width"], info["height"]), size, fmt)
            self.assertEqual(count_frames(res["out"]), 90, fmt)
            self.assertEqual(res["size"], list(size))

    def test_blur_fill_shows_blurred_picture_where_fit_shows_black(self):
        e = self.two_shots(self.port, self.port)
        res_fill, _ = self.render(e, "fill", fmt="youtube-16x9")                       # default for a different aspect: blur-fill
        e2 = copy.deepcopy(e)
        e2["style"]["reframe"] = "fit"
        res_fit, _ = self.render(e2, "fit", fmt="youtube-16x9")
        l_fill, _, r_fill = column_means(res_fill["out"])
        l_fit, _, r_fit = column_means(res_fit["out"])
        self.assertLess(l_fit, 18)                                                       # black bars
        self.assertLess(r_fit, 18)
        self.assertGreater(l_fill + r_fill, 60)                                          # blurred copy of the picture fills the bars (darkened)

    def test_cover_crops_and_focus_chooses_what_is_kept(self):
        base = lambda focus: edl([{"clip": vl.ffp(self.land), "span": [0, 2.0], "src_start": 0.1, **({"focus": focus} if focus else {})}],
                                 2.0, reframe="cover")
        left, _ = self.render(base([0.0, 0.5]), "focus_l")
        right, _ = self.render(base([1.0, 0.5]), "focus_r")
        mid, _ = self.render(base(None), "focus_c")
        _, c_left, _ = column_means(left["out"])
        _, c_right, _ = column_means(right["out"])
        _, c_mid, _ = column_means(mid["out"])
        self.assertGreater(c_left, 150)                                                  # kept the bright left half
        self.assertLess(c_right, 90)                                                     # kept the dark right half
        self.assertTrue(c_right < c_mid + 100 and c_left > c_right)

    def test_per_shot_reframe_override(self):
        e = edl([{"clip": vl.ffp(self.port), "span": [0, 1.5], "src_start": 0.1, "reframe": "fit"},
                 {"clip": vl.ffp(self.port), "span": [1.5, 3.0], "src_start": 0.1, "reframe": "blur-fill"}], 3.0)
        res, _ = self.render(e, "mixed", fmt="youtube-16x9")
        l1, _, _ = column_means(res["out"], 0.5)
        l2, _, _ = column_means(res["out"], 2.0)
        self.assertLess(l1, 18)
        self.assertGreater(l2, 25)

    def test_square_source_in_every_format(self):
        e = self.two_shots(self.sq, self.sq)
        for fmt in ("reels-9x16", "youtube-16x9"):
            res, _ = self.render(e, "sqsrc_" + fmt, fmt=fmt)
            self.assertEqual(count_frames(res["out"]), 90)

    def test_render_formats_makes_three_files_and_shares_the_audio_master(self):
        music = self.root / "m.wav"
        helpers.click_track(music, bpm=120, first=0.5, seconds=4.0, step_at=None)
        e = self.two_shots(self.land, self.port)
        e["project"]["duration"] = 3.0
        e["project"]["music"] = vl.ffp(music)
        p = self.root / "multi.edl.json"
        vl.save_json(p, e)
        results = vr.render_formats(p, None, preview=True, encoder="libx264", workdir=self.root / "multi_work", log=lambda *_: None)
        self.assertEqual([r["format_name"] for r in results], list(vr.STANDARD_FORMATS))
        self.assertEqual({tuple(r["size"]) for r in results}, {(540, 960), (960, 540), (540, 540)})
        names = sorted(Path(r["out"]).name for r in results)
        self.assertTrue(any("16x9" in n for n in names) and any("1x1" in n for n in names))
        masters = list((self.root / "multi_work").glob("master_*.wav"))
        self.assertEqual(len(masters), 1)                                                  # one master for all formats
        for r in results:
            self.assertEqual(count_frames(r["out"]), 90)
            self.assertTrue(vl.probe(r["out"])["has_audio"])

    def test_parts_are_cached_per_format(self):
        e = self.two_shots(self.land, self.port)
        first, p = self.render(e, "cachefmt", fmt="youtube-16x9")
        again, _ = self.render(e, "cachefmt", fmt="youtube-16x9")
        other, _ = self.render(e, "cachefmt", fmt="square-1x1")
        self.assertEqual(again["parts_cached"], 2)
        self.assertEqual(other["parts_cached"], 0)                                         # a different size is a different part

    def test_qa_checks_the_format_it_was_rendered_in(self):
        e = self.two_shots(self.land, self.port)
        res, p = self.render(e, "qafmt", fmt="youtube-16x9")
        good = qa.run_qa(res["out"], p, fmt="youtube-16x9")
        self.assertNotEqual(good["verdict"], "fail", qa.format_report(good))
        bad = qa.run_qa(res["out"], p)                                                       # expects 9:16
        self.assertEqual({c["id"]: c["level"] for c in bad["checks"]}["aspect"], "error")

    def test_render_with_qa_flag_uses_the_format(self):
        e = self.two_shots(self.land, self.port)
        res, _ = self.render(e, "qaflag", fmt="square-1x1", qa=True)
        self.assertNotEqual(res["qa"], "fail")

    def test_cli_format_options(self):
        e = self.two_shots(self.land, self.port)
        p = self.root / "cli.edl.json"
        vl.save_json(p, e)
        out = self.root / "cli_169.mp4"
        self.assertEqual(mh_video.main(["render", str(p), "--out", str(out), "--preview", "--encoder", "libx264", "--format", "youtube-16x9",
                                        "--work", str(self.root / "cli_work")]), 0)
        self.assertEqual(vl.probe(out)["width"], 960)
        self.assertEqual(mh_video.main(["render", str(p), "--preview", "--encoder", "libx264", "--all-formats",
                                        "--work", str(self.root / "cli_work2")]), 0)
        for tag in ("9x16", "16x9", "1x1"):
            self.assertTrue((self.root / f"f_{tag}_preview.mp4").is_file(), tag)                 # named after the project plus the format tag

    @helpers.requires_filters("ass")
    def test_text_uses_the_safe_zone_of_the_format(self):
        e = self.two_shots(self.land, self.land)
        e["texts"] = [{"text": "کویر مرنجاب", "start": 0.2, "end": 2.8, "position": "bottom", "animation": "none"}]
        res, _ = self.render(e, "textfmt", fmt="youtube-16x9")
        self.assertTrue(Path(res["out"]).is_file())
        import vid_text
        self.assertEqual(vid_text.safe_area(960, 540), vid_text.SAFE_LANDSCAPE)


if __name__ == "__main__":
    unittest.main()
