"""Phase 4: colour correction and grading."""
import copy
import json
import math
import unittest
from pathlib import Path

import helpers
import mh_video
import vid_color as vc
import vid_edl as ve
import vid_render as vr
import vid_sheets
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class Maths(unittest.TestCase):
    def test_gamma_round_trip(self):
        for x in (0.0, 0.1, 0.5, 0.9, 1.0):
            self.assertAlmostEqual(vc.to_encoded(vc.to_linear(x)), x, places=9)

    def test_hsv_round_trip(self):
        for rgb in [(1, 0, 0), (0.2, 0.5, 0.8), (0.9, 0.9, 0.1), (0.3, 0.3, 0.3), (0, 0, 0)]:
            back = vc.hsv_to_rgb(*vc.rgb_to_hsv(*rgb))
            for a, b in zip(rgb, back):
                self.assertAlmostEqual(a, b, places=6)

    def test_yuv_means_to_rgb_gray_is_neutral(self):
        r, g, b = vc.yuv_means_to_rgb(126.0, 128.0, 128.0)
        self.assertAlmostEqual(r, g, places=6)
        self.assertAlmostEqual(g, b, places=6)
        self.assertAlmostEqual(r, 0.502, delta=0.01)

    def test_yuv_means_warm_has_red_over_blue(self):
        r, g, b = vc.yuv_means_to_rgb(126.0, 115.0, 142.0)               # orange: U low, V high
        self.assertGreater(r, b)

    def test_s_curve_properties(self):
        for c in (0.1, 0.3, 0.5):
            for pivot in (0.35, 0.5, 0.65):
                self.assertAlmostEqual(vc.s_curve(0.0, c, pivot), 0.0, places=9)
                self.assertAlmostEqual(vc.s_curve(1.0, c, pivot), 1.0, places=9)
                self.assertAlmostEqual(vc.s_curve(pivot, c, pivot), pivot, places=9)
                xs = [i / 100 for i in range(101)]
                ys = [vc.s_curve(x, c, pivot) for x in xs]
                self.assertTrue(all(b >= a - 1e-12 for a, b in zip(ys, ys[1:])), "monotonic")
                self.assertLess(vc.s_curve(pivot * 0.5, c, pivot), pivot * 0.5)          # shadows darker
                self.assertGreater(vc.s_curve(pivot + (1 - pivot) * 0.5, c, pivot), pivot + (1 - pivot) * 0.5)   # highlights brighter
        self.assertEqual(vc.s_curve(0.3, 0.0, 0.5), 0.3)

    def test_shoulder_rolloff(self):
        self.assertEqual(vc.shoulder_rolloff(0.5, 0.8), 0.5)
        self.assertAlmostEqual(vc.shoulder_rolloff(0.8, 0.8), 0.8)
        self.assertLess(vc.shoulder_rolloff(1.0, 0.8), 1.0)
        ys = [vc.shoulder_rolloff(0.8 + i / 50, 0.8) for i in range(30)]
        self.assertTrue(all(b > a for a, b in zip(ys, ys[1:])))
        self.assertLess(max(ys), 1.0)

    def test_hue_weight(self):
        self.assertAlmostEqual(vc._hue_weight(30, 30, 40), 1.0)
        self.assertEqual(vc._hue_weight(100, 30, 40), 0.0)
        self.assertAlmostEqual(vc._hue_weight(359, 1, 10), vc._hue_weight(1, 359, 10))           # wraps around 0/360


class Correction(unittest.TestCase):
    def test_identity_is_none_and_empty_filter(self):
        self.assertIsNone(vc.normalize_correction(None))
        self.assertIsNone(vc.normalize_correction({}))
        self.assertIsNone(vc.normalize_correction({"ev": 0.0, "gain": [1, 1, 1]}))
        self.assertEqual(vc.curves_filter(None), "")

    def test_validation(self):
        bad = [{"ev": 5}, {"black": 0.5}, {"white": 0.4}, {"black": 0.3, "white": 0.65}, {"gamma": 3}, {"gain": [1, 1]},
               {"gain": [3, 1, 1]}, {"nonsense": 1}, "text"]
        for b in bad:
            with self.assertRaises(vl.EngineError, msg=str(b)):
                vc.normalize_correction(b)

    def test_channel_function(self):
        c = vc.normalize_correction({"ev": 1.0})
        self.assertGreater(vc.correct_channel(0.3, c, 0), 0.3)
        c = vc.normalize_correction({"black": 0.1})
        self.assertEqual(vc.correct_channel(0.05, c, 0), 0.0)
        self.assertAlmostEqual(vc.correct_channel(1.0, c, 0), 1.0)
        c = vc.normalize_correction({"gain": [1.2, 1.0, 0.8]})
        self.assertGreater(vc.correct_channel(0.5, c, 0), vc.correct_channel(0.5, c, 1))
        self.assertLess(vc.correct_channel(0.5, c, 2), vc.correct_channel(0.5, c, 1))
        c = vc.normalize_correction({"gamma": 1.4})
        self.assertGreater(vc.correct_channel(0.4, c, 1), 0.4)
        self.assertAlmostEqual(vc.correct_channel(1.0, c, 1), 1.0)

    def test_curves_filter_is_monotonic_and_in_range(self):
        f = vc.curves_filter({"ev": 0.4, "black": 0.05, "white": 0.95, "gamma": 1.1, "gain": [1.05, 1.0, 0.95]})
        self.assertTrue(f.startswith("curves=r='"))
        self.assertIn("interp=pchip", f)
        for ch in "rgb":
            pts = f.split(f"{ch}='")[1].split("'")[0].split()
            ys = [float(p.split("/")[1]) for p in pts]
            xs = [float(p.split("/")[0]) for p in pts]
            self.assertEqual(xs[0], 0.0)
            self.assertEqual(xs[-1], 1.0)
            self.assertTrue(all(0.0 <= y <= 1.0 for y in ys))
            self.assertTrue(all(b >= a for a, b in zip(ys, ys[1:])))


class Looks(unittest.TestCase):
    def test_every_look_is_valid_and_documented(self):
        self.assertGreaterEqual(len(vc.LOOKS), 10)
        for name, look in vc.LOOKS.items():
            vc.validate_look(look, name)
            self.assertTrue(look.get("description"), name)
        self.assertEqual(len(vc.list_looks()), len(vc.LOOKS))

    def test_neutral_is_exact_identity(self):
        f = vc.look_function(vc.LOOKS["neutral"])
        for rgb in [(0, 0, 0), (1, 1, 1), (0.2, 0.5, 0.9), (0.33, 0.66, 0.1)]:
            for a, b in zip(rgb, f(*rgb)):
                self.assertAlmostEqual(a, b, places=9)

    def test_every_look_stays_in_range_and_keeps_order_of_grays(self):
        for name, look in vc.LOOKS.items():
            f = vc.look_function(look)
            prev = -1.0
            for i in range(0, 101):
                x = i / 100
                out = f(x, x, x)
                self.assertTrue(all(0.0 <= v <= 1.0 for v in out), f"{name} @ {x}: {out}")
                lum = vc.luma(*out)
                self.assertGreaterEqual(lum, prev - 1e-6, f"{name}: gray ramp must not invert at {x}")
                prev = lum

    def test_black_and_white_are_protected(self):
        for name in ("clean-bright", "desert-gold", "teal-orange", "vivid-travel", "bleach-bypass"):
            f = vc.look_function(vc.LOOKS[name])
            lo, hi = f(0.0, 0.0, 0.0), f(1.0, 1.0, 1.0)
            self.assertLess(vc.luma(*lo), 0.05, name)
            self.assertGreater(vc.luma(*hi), 0.85, name)

    def test_lifted_blacks_looks(self):
        for name in ("film-faded", "cinematic-soft"):
            self.assertGreater(vc.luma(*vc.look_function(vc.LOOKS[name])(0, 0, 0)), 0.02, name)

    def test_warm_looks_push_orange_and_cool_shadows(self):
        f = vc.look_function(vc.LOOKS["desert-gold"])
        r, g, b = f(0.6, 0.6, 0.6)
        self.assertGreater(r, b, "warm mids/highlights")
        r, g, b = f(0.12, 0.12, 0.12)
        self.assertGreaterEqual(b, r - 0.002, "cool shadows")
        r, g, b = vc.look_function(vc.LOOKS["teal-orange"])(0.15, 0.15, 0.15)
        self.assertGreater(b, r, "teal shadows")

    def test_saturation_and_mono(self):
        vivid = vc.look_function(vc.LOOKS["vivid-travel"])(0.7, 0.4, 0.3)
        plain = (0.7, 0.4, 0.3)
        self.assertGreater(max(vivid) - min(vivid), max(plain) - min(plain))
        bleach = vc.look_function(vc.LOOKS["bleach-bypass"])(0.7, 0.4, 0.3)
        self.assertLess(max(bleach) - min(bleach), max(plain) - min(plain))
        bw = vc.look_function(vc.LOOKS["bw-contrast"])(0.7, 0.4, 0.3)
        self.assertAlmostEqual(bw[0], bw[1], places=9)
        self.assertAlmostEqual(bw[1], bw[2], places=9)

    def test_strength_blends_towards_identity(self):
        f = vc.look_function(vc.LOOKS["teal-orange"])
        half = vc._blend_towards_identity(f, 0.5)
        zero = vc._blend_towards_identity(f, 0.0)
        src = (0.4, 0.5, 0.6)
        full = f(*src)
        h = half(*src)
        for a, b, c in zip(src, h, full):
            self.assertAlmostEqual(b, (a + c) / 2, places=9)
        for a, b in zip(src, zero(*src)):
            self.assertAlmostEqual(a, b, places=9)

    def test_validation_errors(self):
        for bad in ({"shadows": [1, 2]}, {"saturation": 5}, {"contrast": 1.0}, {"pivot": 0.05}, {"vignette": 2}, {"bogus": 1},
                    {"cdl": {"slope": [1, 1]}}, {"cdl": {"gamma": [1, 1, 1]}}):
            with self.assertRaises(vl.EngineError, msg=str(bad)):
                vc.validate_look(bad)

    def test_resolve_look_forms(self):
        look, st, lut, ls = vc.resolve_look("desert-gold")
        self.assertEqual(st, 1.0)
        look, st, lut, ls = vc.resolve_look({"look": "teal-orange", "strength": 0.6, "contrast": 0.3, "lut_strength": 0.5})
        self.assertEqual(st, 0.6)
        self.assertEqual(look["contrast"], 0.3)
        self.assertEqual(ls, 0.5)
        look, *_ = vc.resolve_look({"look": {"saturation": 1.2}})
        self.assertEqual(look["saturation"], 1.2)
        for bad in ("nope", {"look": "nope"}, {"strength": 2}, 5, {"look": 5}):
            with self.assertRaises(vl.EngineError, msg=str(bad)):
                vc.resolve_look(bad)

    def test_is_look_spec(self):
        self.assertTrue(vc.is_look_spec("desert-gold"))
        self.assertTrue(vc.is_look_spec({"look": "x"}))
        self.assertFalse(vc.is_look_spec("warm-desert"))                  # legacy grade preset
        self.assertFalse(vc.is_look_spec("eq=contrast=1.1"))
        self.assertFalse(vc.is_look_spec(None))

    def test_vignette_filter(self):
        self.assertEqual(vc.vignette_filter(0), "")
        self.assertIn("vignette=angle=", vc.vignette_filter(0.2))
        a1 = float(vc.vignette_filter(0.1).split("=")[2])
        a2 = float(vc.vignette_filter(0.5).split("=")[2])
        self.assertGreater(a2, a1)                                        # FFmpeg: a larger lens angle darkens MORE
        self.assertAlmostEqual(float(vc.vignette_filter(0.2).split("=")[2]), math.pi / 7, places=3)
        self.assertLessEqual(float(vc.vignette_filter(1.0).split("=")[2]), math.pi / 2 * 0.8 + 1e-6)

    @NEEDS_FF
    def test_vignette_strength_on_pixels(self):
        """Measured, not assumed: corners darken with the amount and the centre never changes."""
        import subprocess
        def corner_ratio(amount):
            r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=270x480:r=1:d=1", "-vf",
                                vc.vignette_filter(amount) + ",format=gray", "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True)
            b = r.stdout
            return b[2 * 270 + 2] / b[240 * 270 + 135], b[240 * 270 + 135]
        mild, c1 = corner_ratio(0.1)
        normal, c2 = corner_ratio(0.2)
        strong, c3 = corner_ratio(0.4)
        self.assertTrue(c1 == c2 == c3)
        self.assertGreater(mild, normal)
        self.assertGreater(normal, strong)
        self.assertGreater(normal, 0.45)                                  # the approved v4 level keeps the corners visible
        self.assertLess(normal, 0.85)


class Luts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "luts"
        cls.root.mkdir(parents=True, exist_ok=True)

    def test_cube_round_trip_and_format(self):
        data = vc.build_lut(vc.look_function(vc.LOOKS["desert-gold"]), 9)
        p = vc.write_cube(self.root / "a.cube", data, 9, "t")
        text = Path(p).read_text()
        self.assertTrue(text.startswith('TITLE "t"\nLUT_3D_SIZE 9\n'))
        cube = vc.read_cube(p)
        self.assertEqual(cube["size"], 9)
        self.assertEqual(len(cube["data"]), 729)
        for a, b in zip(data[:50], cube["data"][:50]):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=5)

    def test_red_varies_fastest(self):
        data = vc.build_lut(lambda r, g, b: (r, g, b), 5)
        self.assertEqual(data[1], (0.25, 0.0, 0.0))
        self.assertEqual(data[5], (0.0, 0.25, 0.0))
        self.assertEqual(data[25], (0.0, 0.0, 0.25))

    def test_identity_lut_lookup(self):
        cube = {"size": 5, "data": vc.build_lut(lambda r, g, b: (r, g, b), 5), "domain_min": [0] * 3, "domain_max": [1] * 3}
        for rgb in [(0.1, 0.5, 0.9), (0.33, 0.77, 0.12), (0, 0, 0), (1, 1, 1)]:
            for a, b in zip(rgb, vc.apply_cube(cube, *rgb)):
                self.assertAlmostEqual(a, b, places=9)

    def test_lut_matches_the_look_function(self):
        look = vc.LOOKS["teal-orange"]
        fn = vc.look_function(look)
        cube = {"size": vc.LUT_SIZE, "data": vc.build_lut(fn), "domain_min": [0] * 3, "domain_max": [1] * 3}
        for rgb in [(0.2, 0.4, 0.6), (0.8, 0.6, 0.3), (0.5, 0.5, 0.5), (0.1, 0.9, 0.2)]:
            for a, b in zip(fn(*rgb), vc.apply_cube(cube, *rgb)):
                self.assertAlmostEqual(a, b, delta=0.01)                  # trilinear error of a 33-point LUT

    def test_read_cube_rejects_bad_files(self):
        for name, body in (("nosize.cube", "0 0 0\n"), ("short.cube", "LUT_3D_SIZE 2\n0 0 0\n"), ("oned.cube", "LUT_1D_SIZE 4\n"),
                           ("badline.cube", "LUT_3D_SIZE 2\n0 0\n")):
            p = self.root / name
            p.write_text(body)
            with self.assertRaises(vl.EngineError, msg=name):
                vc.read_cube(p)
        with self.assertRaises(vl.EngineError):
            vc.read_cube(self.root / "missing.cube")

    def test_read_cube_accepts_comments_and_domain(self):
        p = self.root / "c.cube"
        p.write_text('# made by hand\nTITLE "x"\nLUT_3D_SIZE 2\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n'
                     + "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n")
        self.assertEqual(vc.read_cube(p)["size"], 2)

    def test_ensure_look_lut_is_cached_and_neutral_is_skipped(self):
        d = self.root / "cache"
        p1 = vc.ensure_look_lut(vc.LOOKS["desert-gold"], 1.0, d)
        m1 = Path(p1).stat().st_mtime_ns
        p2 = vc.ensure_look_lut(vc.LOOKS["desert-gold"], 1.0, d)
        self.assertEqual(p1, p2)
        self.assertEqual(Path(p2).stat().st_mtime_ns, m1)
        self.assertNotEqual(p1, vc.ensure_look_lut(vc.LOOKS["desert-gold"], 0.5, d))
        self.assertIsNone(vc.ensure_look_lut(vc.LOOKS["neutral"], 1.0, d))
        self.assertIsNone(vc.ensure_look_lut(vc.LOOKS["desert-gold"], 0.0, d))

    def test_grade_chain_forms(self):
        d = self.root / "chain"
        self.assertEqual(vc.grade_chain(None, None, d), [])
        self.assertEqual(vc.grade_chain("neutral", None, d), [])
        ch = vc.grade_chain("desert-gold", {"ev": 0.2}, d)
        self.assertEqual(ch[0], "format=gbrp16le")
        self.assertTrue(ch[1].startswith("curves="))
        self.assertTrue(ch[2].startswith("lut3d=file="))
        self.assertTrue(any(c.startswith("vignette=") for c in ch))
        only_corr = vc.grade_chain(None, {"ev": 0.2}, d)
        self.assertEqual(len(only_corr), 2)
        mine = self.root / "mine.cube"
        vc.write_cube(mine, vc.build_lut(lambda r, g, b: (r, g, b), 5), 5)
        full = vc.grade_chain({"lut": str(mine)}, None, d)
        self.assertEqual(sum(1 for c in full if c.startswith("lut3d")), 1)
        half = vc.grade_chain({"lut": str(mine), "lut_strength": 0.5}, None, d)
        self.assertTrue(any(f.name.startswith("user_") for f in d.glob("*.cube")))
        with self.assertRaises(vl.EngineError):
            vc.grade_chain({"lut": str(self.root / "gone.cube")}, None, d)


def stats_of(path, t=0.5):
    return vc.measure_window(vl.ffp(path), t, 1.0, False, False)


@NEEDS_FF
class OnPixels(unittest.TestCase):
    """The filters really do what the maths says: measured with signalstats on test clips."""

    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "colorpix"
        cls.root.mkdir(parents=True, exist_ok=True)
        ff = vl.find_ffmpeg()["ffmpeg"]
        cls.mid = cls.root / "mid.mp4"           # a horizontal luma ramp 40..210, neutral chroma, mid exposure
        vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=2,geq=lum='40+170*X/W':cb=128:cr=128",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.mid)])
        cls.dark = cls.root / "dark.mp4"
        vl.run([ff, "-v", "error", "-y", "-i", str(cls.mid), "-vf", "eq=brightness=-0.22", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.dark)])
        cls.blue = cls.root / "blue.mp4"
        vl.run([ff, "-v", "error", "-y", "-i", str(cls.mid), "-vf", "colorchannelmixer=rr=0.8:bb=1.25", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(cls.blue)])
        cls.hazy = cls.root / "hazy.mp4"          # compressed range 110..170: hazy
        vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=2,geq=lum='110+60*X/W':cb=128:cr=128",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.hazy)])

    def render_chain(self, src, chain, name):
        out = self.root / f"{name}.mp4"
        vf = ",".join(chain + ["format=yuv420p"])
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])
        return out

    def test_identity_chain_changes_nothing(self):
        cube = self.root / "id.cube"
        vc.write_cube(cube, vc.build_lut(lambda r, g, b: (r, g, b), 17), 17)
        out = self.render_chain(self.mid, ["format=gbrp16le", f"lut3d=file='{vl.filter_escape_path(cube)}':interp=tetrahedral"], "idlut")
        a, b = stats_of(self.mid), stats_of(out)
        for k in ("YAVG", "UAVG", "VAVG"):
            self.assertAlmostEqual(a[k], b[k], delta=1.5, msg=k)

    def test_warm_look_moves_chroma_towards_orange(self):
        chain = vc.grade_chain("golden-hour", None, self.root / "lutw")
        out = self.render_chain(self.mid, chain, "warm")
        a, b = stats_of(self.mid), stats_of(out)
        self.assertGreater(b["VAVG"], a["VAVG"] + 2)             # more red
        self.assertLess(b["UAVG"], a["UAVG"] - 2)                # less blue

    def test_bw_look_removes_colour(self):
        out = self.render_chain(self.blue, vc.grade_chain("bw-contrast", None, self.root / "lutbw"), "bw")
        b = stats_of(out)
        self.assertAlmostEqual(b["UAVG"], 128.0, delta=1.5)
        self.assertAlmostEqual(b["VAVG"], 128.0, delta=1.5)

    def test_contrast_look_spreads_the_ramp(self):
        before = stats_of(self.mid)
        out = self.render_chain(self.mid, vc.grade_chain("bleach-bypass", None, self.root / "lutc"), "contrast")
        after = stats_of(out)
        self.assertGreater(after["YHIGH"] - after["YLOW"], before["YHIGH"] - before["YLOW"])

    def test_exposure_correction_moves_luma_up(self):
        corr = {"ev": 0.45}
        out = self.render_chain(self.dark, ["format=gbrp16le", vc.curves_filter(corr)], "ev")
        a, b = stats_of(self.dark), stats_of(out)
        self.assertGreater(b["YAVG"], a["YAVG"] + 8)

    def test_white_balance_gains_remove_a_blue_cast(self):
        s = stats_of(self.blue)
        corr, why = vc.propose_correction(s, 1.0, wb="gray-world")
        self.assertIsNotNone(corr)
        self.assertGreater(corr["gain"][0], 1.0)
        self.assertLess(corr["gain"][2], 1.0)
        out = self.render_chain(self.blue, ["format=gbrp16le", vc.curves_filter(corr)], "wb")
        a, b = stats_of(self.blue), stats_of(out)
        self.assertLess(abs(b["UAVG"] - 128), abs(a["UAVG"] - 128))

    def test_black_point_removes_haze(self):
        s = stats_of(self.hazy)
        corr, why = vc.propose_correction(s, 1.0, target_mean=(s["YAVG"] - 16) / 219)
        self.assertIsNotNone(corr, why)
        self.assertGreater(corr["black"], 0.04)
        out = self.render_chain(self.hazy, ["format=gbrp16le", vc.curves_filter({"black": corr["black"]})], "haze")
        self.assertLess(stats_of(out)["YMIN"], stats_of(self.hazy)["YMIN"] - 5)

    def test_scope_sheet_is_made(self):
        p = vc.scope_sheet(vl.ffp(self.mid), self.root / "scope.jpg", 0.5, width=240)
        self.assertTrue(Path(p).stat().st_size > 2000)
        info = vl.probe(p)
        self.assertEqual((info["width"], info["height"]), (480, 360))   # 2 x 2 tiles of 240 x 180
        odd = vc.scope_sheet(vl.ffp(self.mid), self.root / "scope_odd.jpg", 0.5, width=421)      # odd widths are rounded to even
        self.assertEqual(vl.probe(odd)["width"] % 2, 0)

    def test_measure_window_unreadable(self):
        self.assertIsNone(vc.measure_window(vl.ffp(self.root / "nope.mp4"), 0.0))


class Proposals(unittest.TestCase):
    BASE = {"YAVG": 114.0, "YLOW": 60.0, "YHIGH": 170.0, "YMIN": 20.0, "YMAX": 230.0, "UAVG": 128.0, "VAVG": 128.0, "SATAVG": 20.0}

    def test_balanced_shot_needs_nothing(self):
        corr, why = vc.propose_correction(dict(self.BASE), 0.8, target_mean=0.45)
        self.assertIsNone(corr)
        self.assertIn("no correction", why[0])

    def test_dead_band(self):
        s = dict(self.BASE, YAVG=114 + 8)
        self.assertIsNone(vc.propose_correction(s, 0.8, target_mean=0.45)[0])

    def test_exposure_is_capped(self):
        dark = dict(self.BASE, YAVG=70.0)
        corr, _ = vc.propose_correction(dark, 1.0, target_mean=0.8)
        self.assertLessEqual(corr["ev"], 0.45 + 1e-9)
        bright = dict(self.BASE, YAVG=200.0)
        corr, _ = vc.propose_correction(bright, 1.0, target_mean=0.1)
        self.assertGreaterEqual(corr["ev"], -0.45 - 1e-9)

    def test_low_key_shots_are_protected(self):
        low = dict(self.BASE, YAVG=60.0)                                     # mean luma 0.20
        corr, _ = vc.propose_correction(low, 1.0, target_mean=0.6)
        self.assertLessEqual(corr["ev"], 0.25 + 1e-9)

    def test_bright_desert_is_not_hazy(self):
        desert = dict(self.BASE, YLOW=150.0, YMIN=20.0)                      # high 10th percentile, but a real black exists
        corr, _ = vc.propose_correction(desert, 0.8, target_mean=(desert["YAVG"] - 16) / 219)
        self.assertTrue(corr is None or corr["black"] == 0.0)

    def test_hazy_shot_gets_a_black_point(self):
        hazy = dict(self.BASE, YMIN=70.0, YLOW=90.0)
        corr, why = vc.propose_correction(hazy, 0.8, target_mean=(hazy["YAVG"] - 16) / 219)
        self.assertGreater(corr["black"], 0.0)
        self.assertTrue(any("black point" in w for w in why))

    def test_flat_highlights_get_a_white_point(self):
        flat = dict(self.BASE, YMAX=150.0)
        corr, why = vc.propose_correction(flat, 0.8, target_mean=(flat["YAVG"] - 16) / 219)
        self.assertLess(corr["white"], 1.0)

    def test_wb_modes(self):
        warm = dict(self.BASE, UAVG=118.0, VAVG=140.0)
        self.assertIsNone(vc.propose_correction(warm, 0.8, target_mean=0.45, wb="off")[0])
        c_med, _ = vc.propose_correction(warm, 0.8, target_mean=0.45, wb="median", median_chroma=(128.0, 128.0))
        self.assertLess(c_med["gain"][0], 1.0)                              # pull the orange back towards the median
        self.assertGreater(c_med["gain"][2], 1.0)
        for g in c_med["gain"]:
            self.assertTrue(0.9 - 1e-9 <= g <= 1.1 + 1e-9)                  # median mode is capped at +-10%
        c_gw, _ = vc.propose_correction(dict(self.BASE, UAVG=160.0, VAVG=100.0), 1.0, target_mean=0.45, wb="gray-world")
        for g in c_gw["gain"]:
            self.assertTrue(0.85 - 1e-9 <= g <= 1.15 + 1e-9)                # gray-world capped at +-15%

    def test_reference_matching(self):
        ref = dict(self.BASE, YAVG=140.0, UAVG=120.0, VAVG=136.0)
        shot = dict(self.BASE, YAVG=100.0, UAVG=128.0, VAVG=128.0)
        corr, why = vc.propose_correction(shot, 1.0, reference=ref)
        self.assertGreater(corr["ev"], 0.0)
        self.assertTrue(any("reference" in w for w in why))

    def test_stronger_strength_gives_larger_move(self):
        dark = dict(self.BASE, YAVG=100.0)
        a, _ = vc.propose_correction(dark, 0.3, target_mean=0.46)
        b, _ = vc.propose_correction(dark, 1.0, target_mean=0.46)
        self.assertGreater(b["ev"], a["ev"])

    def test_apply_plan_respects_existing_corrections(self):
        edl = {"shots": [{"clip": "a"}, {"clip": "b", "correct": {"ev": 0.1}}, {"clip": "c"}]}
        plan = {0: {"correct": {"ev": 0.3}}, 1: {"correct": {"ev": 0.4}}, 2: {"correct": None}}
        self.assertEqual(vc.apply_plan(edl, plan), 1)
        self.assertEqual(edl["shots"][0]["correct"], {"ev": 0.3})
        self.assertEqual(edl["shots"][1]["correct"], {"ev": 0.1})
        self.assertEqual(vc.apply_plan(edl, plan, overwrite=True), 2)
        self.assertEqual(edl["shots"][1]["correct"], {"ev": 0.4})


@NEEDS_FF
class PlanAndRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "colorplan"
        cls.root.mkdir(parents=True, exist_ok=True)
        ff = vl.find_ffmpeg()["ffmpeg"]
        cls.clips = []
        for i, br in enumerate((-0.25, 0.0, 0.2, -0.1)):
            p = cls.root / f"c{i}.mp4"
            vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",                      # deterministic ramp (the gradients source is random)
                    f"color=c=gray:s=320x180:r=30:d=3,geq=lum='50+130*(X+Y)/(W+H)':cb='122':cr='134',eq=brightness={br}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)])
            cls.clips.append(p)
        edges = [0, 1.0, 2.0, 3.0, 4.0]
        cls.edl = {"version": 1, "project": {"name": "cp", "width": 180, "height": 320, "fps": 30, "duration": 4.0},
                   "style": {"grade": "neutral", "zoom": 0, "drop_flash": False, "end_flare": False}, "transitions": {},
                   "shots": [{"clip": vl.ffp(c), "span": [edges[i], edges[i + 1]]} for i, c in enumerate(cls.clips)]}
        cls.path = cls.root / "cp.edl.json"
        vl.save_json(cls.path, cls.edl)

    def test_plan_reduces_the_brightness_spread(self):
        plan = vc.plan_corrections(self.edl, self.root, 1.0, "exposure")
        self.assertEqual(sorted(plan), [0, 1, 2, 3])
        before = [plan[j]["stats"]["YAVG"] for j in plan]
        out = []
        for j in sorted(plan):
            corr = plan[j]["correct"]
            out.append(before[j] + (24 * corr["ev"] if corr else 0))          # a crude prediction: about 24 levels per EV near mid
        self.assertLess(max(out) - min(out), max(before) - min(before))
        self.assertTrue(plan[0]["correct"]["ev"] > 0)                          # the darkest shot is lifted
        self.assertTrue(plan[2]["correct"] is None or plan[2]["correct"]["ev"] < 0)

    def test_corrected_render_has_less_spread(self):
        base = copy.deepcopy(self.edl)
        res0 = vr.render(self._save(base, "base"), out=self.root / "base.mp4", encoder="libx264", log=lambda *_: None)
        plan = vc.plan_corrections(base, self.root, 1.0, "exposure")
        corrected = copy.deepcopy(base)
        vc.apply_plan(corrected, plan)
        res1 = vr.render(self._save(corrected, "corr"), out=self.root / "corr.mp4", encoder="libx264", log=lambda *_: None)

        def spread(path):
            ys = [vc.measure_window(vl.ffp(path), t, 0.5, False, False)["YAVG"] for t in (0.2, 1.2, 2.2, 3.2)]
            return max(ys) - min(ys)
        self.assertLess(spread(res1["out"]), spread(res0["out"]))

    def test_cli_plan_dry_run_and_write(self):
        p = self._save(copy.deepcopy(self.edl), "cli")
        self.assertEqual(mh_video.main(["color", "plan", str(p)]), 0)
        self.assertNotIn("correct", json.loads(p.read_text())["shots"][0])            # dry run writes nothing
        self.assertEqual(mh_video.main(["color", "plan", str(p), "--write", "--strength", "1.0"]), 0)
        self.assertIn("correct", json.loads(p.read_text())["shots"][0])
        self.assertEqual(mh_video.main(["color", "plan", str(p), "--match", "reference", "--reference", "1"]), 0)
        self.assertEqual(mh_video.main(["color", "plan", str(p), "--only", "0-1"]), 0)

    def test_cli_looks_lut_analyze_scope(self):
        self.assertEqual(mh_video.main(["color", "looks"]), 0)
        cube = self.root / "cli.cube"
        self.assertEqual(mh_video.main(["color", "lut", "--look", "desert-gold", "--out", str(cube), "--size", "17"]), 0)
        self.assertEqual(vc.read_cube(cube)["size"], 17)
        self.assertEqual(mh_video.main(["color", "analyze", vl.ffp(self.clips[0]), "--scope", str(self.root / "an.jpg")]), 0)
        self.assertTrue((self.root / "an.jpg").is_file())
        self.assertEqual(mh_video.main(["color", "scope", vl.ffp(self.clips[1]), "--out", str(self.root / "sc.jpg")]), 0)

    def _save(self, edl, name):
        p = self.root / f"{name}.edl.json"
        vl.save_json(p, edl)
        return p


@NEEDS_FF
class Tonemapping(unittest.TestCase):
    def test_names_and_chains(self):
        for name in vid_sheets.TONEMAPS:
            self.assertTrue(vid_sheets.tonemap_chain(name).endswith(","))
        self.assertEqual(vid_sheets.tonemap_chain(None), vid_sheets.TONEMAPS[vid_sheets.DEFAULT_TONEMAP])
        with self.assertRaises(vl.EngineError):
            vid_sheets.tonemap_chain("nope")
        self.assertIn("npl=203", vid_sheets.TONEMAPS["hable-203"])
        self.assertIn("npl=1000", vid_sheets.TONEMAPS["legacy"])

    def test_default_is_brighter_than_legacy_on_hlg(self):
        root = helpers.tmpdir() / "tm"
        root.mkdir(exist_ok=True)
        clip = helpers.make_hlg_clip(root / "hlg.mp4", 2.0)
        vals = {}
        for name in ("legacy", "hable-203"):
            out = root / f"{name}.mp4"
            vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", str(clip), "-vf", vid_sheets.tonemap_chain(name) + "format=yuv420p",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])
            vals[name] = vc.measure_window(vl.ffp(out), 0.2, 1.0, False, False)["YAVG"]
        self.assertGreater(vals["hable-203"], vals["legacy"] + 5)

    def test_edl_rejects_unknown_tonemap(self):
        edl = {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": 2.0}, "style": {"hdr_tonemap": "xyz"},
               "shots": [{"clip": "a.mp4", "span": [0, 2.0]}], "transitions": {}}
        self.assertIn("hdr-tonemap", {i["code"] for i in ve.lint(edl, check_files=False)})


class EdlColorLint(unittest.TestCase):
    def edl(self, **kw):
        e = {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": 4.0}, "style": {}, "transitions": {},
             "shots": [{"clip": "a.mp4", "span": [0, 2.0]}, {"clip": "b.mp4", "span": [2.0, 4.0]}]}
        e["style"].update(kw.pop("style", {}))
        e["shots"][0].update(kw)
        return e

    def codes(self, e):
        return {i["code"] for i in ve.lint(e, check_files=False)}

    def test_valid_forms_are_clean(self):
        for style in ({"grade": "desert-gold"}, {"grade": {"look": "teal-orange", "strength": 0.7}}, {"grade": "warm-desert"},
                      {"grade": "eq=contrast=1.1"}, {"correct": {"ev": 0.2}}):
            self.assertEqual(ve.lint(self.edl(style=style), check_files=False), [], str(style))

    def test_errors(self):
        self.assertIn("grade", self.codes(self.edl(style={"grade": "no-such-look"})))
        self.assertIn("grade", self.codes(self.edl(style={"grade": {"look": "nope"}})))
        self.assertIn("correct", self.codes(self.edl(style={"correct": {"ev": 9}})))
        self.assertIn("grade", self.codes(self.edl(grade="bogus")))
        self.assertIn("correct", self.codes(self.edl(correct={"white": 0.2})))
        self.assertIn("reframe", self.codes(self.edl(style={"reframe": "stretch"})))
        self.assertIn("reframe", self.codes(self.edl(reframe="zoomy")))
        self.assertIn("focus", self.codes(self.edl(focus=[2, 0.5])))
        self.assertIn("focus", self.codes(self.edl(focus="left")))


if __name__ == "__main__":
    unittest.main()
