"""Phase H (0.24): picture quality. Lossless 10-bit parts and a 16-bit assembly dithered once for final renders, every
conversion with the Rec.709 matrix stated (colours come out exact), and an optional 10-bit HEVC master."""
import os
import re
import subprocess
import time
import unittest

import helpers
import test_robustness as tr
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def _tiny(n=2):
    edl = tr.tiny_edl([{"clip": f"{j}.mp4", "span": [j * 2.0 / n, (j + 1) * 2.0 / n]} for j in range(n)], 2.0)
    bounds = vr.ve.timeline(edl)
    trans = vr.ve.effective_transitions(edl, bounds)
    parts = [{"j": j, "path": f"{j}.mkv", "n": bounds[j + 1] - bounds[j]} for j in range(n)]
    return edl, parts, bounds, trans


class PartEncoding(unittest.TestCase):
    def test_finals_keep_10_bits_and_max_is_lossless(self):
        enc = vr.part_encoding("max")
        self.assertEqual((enc["pix_fmt"], enc["ext"]), ("yuv420p10le", ".mkv"))
        self.assertIn("ffv1", enc["args"])
        high = vr.part_encoding("high")
        self.assertEqual(high["pix_fmt"], "yuv420p10le")
        self.assertIn("libx264" if vr.x264_10bit() else "ffv1", high["args"])

    def test_without_a_10_bit_libx264_high_falls_back_to_lossless(self):
        from unittest import mock
        with mock.patch.object(vr, "x264_10bit", return_value=False):
            self.assertIn("ffv1", vr.part_encoding("high")["args"])

    def test_previews_and_standard_keep_light_8_bit_parts(self):
        self.assertFalse(vr.precise_parts("high", preview=True))
        enc = vr.part_encoding("standard")
        self.assertEqual((enc["pix_fmt"], enc["ext"]), ("yuv420p", ".mp4"))
        self.assertIn("libx264", enc["args"])

    def test_the_part_filter_ends_in_the_part_format(self):
        pl = {"n": 30, "W": 180, "H": 320, "fps": 30, "zoom": 0.0, "grade": "", "look": None, "correct": None, "split": (2, 7),
              "stab": None, "speed": None, "reframe": "cover", "focus": None, "pix_fmt": "yuv420p10le"}
        vf = vr.part_vf(pl, False)
        self.assertIn("format=yuv420p10le,tpad", vf)
        self.assertIn("format=gbrp16le,rgbashift", vf, "the drop flash does not drop a precise part to 8-bit RGB")
        self.assertIn("format=yuv420p,tpad", vr.part_vf(dict(pl, pix_fmt=None), False))


class Assembly(unittest.TestCase):
    def lines(self, n=2, **kw):
        edl, parts, bounds, trans = _tiny(n)
        return vr.build_graph(edl, parts, bounds, trans, 1.0, **kw)[0]

    def test_light_assembly_is_unchanged(self):
        g = ";".join(self.lines())
        self.assertIn("format=gbrp[v0]", g)
        self.assertNotIn("zscale", g)

    def test_precise_assembly_stays_16_bit_yuv_and_dithers_once(self):
        g = ";".join(self.lines(precise=True))
        self.assertIn(vr.TO_YUV16 + "[v0]", g)
        self.assertNotIn("gbrp", g, "no RGB stage without the flare")
        self.assertEqual(g.count("d=error_diffusion"), 1)
        self.assertTrue(g.split("[vout]")[0].endswith(vr.FINAL_DITHER + "format=yuv420p"))

    def test_every_precise_conversion_states_the_matrix(self):
        """A `format=` that changes between YUV and RGB would let FFmpeg guess the matrix (BT.601 for RGB or untagged frames)."""
        g = ";".join(self.lines(n=3, precise=True))
        self.assertIn(vr.YUV_TO_RGB + "[v0]", g, "the flare is blended in RGB")
        self.assertIn("screen:all_opacity=0.6," + vr.RGB_TO_YUV16, g)
        self.assertNotIn("gbrp16le", g, "FFmpeg's blend is wrong at 16-bit RGB")
        for z in re.findall(r"zscale=[^,\]]*", g):
            self.assertIn("709", z)

    def test_the_master_graph_is_10_bit(self):
        g = ";".join(self.lines(precise=True, master=True))
        self.assertIn(f"format={vr.MASTER_PIX_FMT}[vout]", g)
        self.assertNotIn("format=yuv420p[vout]", g)

    def test_a_fade_of_zero_seconds_is_left_out(self):
        """FFmpeg reads fade d=0 as 25 frames and afade d=0 as one second: `fade_in: 0` used to fade in from black."""
        edl, parts, bounds, trans = _tiny()
        edl["project"].update(fade_in=0.0, fade_out=0.0, music="m.wav", music_fade_in=0.0, music_fade_out=0.0)
        g = ";".join(vr.build_graph(edl, parts, bounds, trans, 1.0)[0])
        self.assertNotIn("fade=t=in", g)
        self.assertNotIn("fade=t=out", g)
        self.assertNotIn("afade", ";".join(vr.audio_lines(edl, 2)))
        edl["project"].update(fade_in=0.4, music_fade_in=0.3)
        self.assertIn("fade=t=in:st=0:d=0.4", ";".join(vr.build_graph(edl, parts, bounds, trans, 1.0)[0]))
        self.assertIn("afade=t=in:st=0:d=0.3", ";".join(vr.audio_lines(edl, 2)))

    def test_text_is_drawn_on_a_dithered_8_bit_picture_and_not_dithered_again(self):
        g = ";".join(self.lines(precise=True, text_filter="null"))
        self.assertEqual(g.count("d=error_diffusion"), 1)
        self.assertIn(vr.FINAL_DITHER + "format=yuv420p,null[vtxt]", g)


@NEEDS_FF
@helpers.slow
class Colour(unittest.TestCase):
    """Flat colours of a Rec.709 phone clip through a whole edit (cross-fade, concat, flare, fades) and back to RGB."""
    COLOURS = {"0xC03018": "red-orange", "0x2E8B57": "green", "0x1E50C8": "blue", "0xE8C090": "skin"}

    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "phase_h_colour"
        cls.root.mkdir(parents=True, exist_ok=True)

    def src(self, c):
        out = self.root / f"src_{c}.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={c}:s=180x320:d=3:r=30",
                "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-c:v", "libx264", "-crf", "1",
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", vl.ffp(out)])
        return out

    def rgb(self, path, at=1.0):
        raw = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-ss", str(at), "-i", vl.ffp(path), "-vf",
                              "crop=64:64,scale=in_color_matrix=bt709:in_range=tv,format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        n = len(raw) // 3
        return [sum(raw[i::3]) / n for i in range(3)]

    def test_a_precise_final_keeps_the_colours_exactly(self):
        for c, name in self.COLOURS.items():
            clip = vl.ffp(self.src(c))
            edl = tr.tiny_edl([{"clip": clip, "span": [0.0, 0.7]}, {"clip": clip, "span": [0.7, 1.4]}, {"clip": clip, "span": [1.4, 2.0]}],
                              2.0, transitions={"1": "fade"}, style={"grade": "neutral", "zoom": 0.0})
            edl["project"].update(fade_in=0.3, fade_out=0.3)
            res, _ = tr.render(edl, self.root, f"c_{c}", quality="high")
            want, got = self.rgb(self.root / f"src_{c}.mp4"), self.rgb(res["out"])
            self.assertLessEqual(max(abs(a - b) for a, b in zip(want, got)), 1.0, f"{name}: {want} -> {got}")


@NEEDS_FF
@helpers.slow
class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "phase_h"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.clips = helpers.make_clips(cls.root / "clips", 2, seconds=3.0)

    def edl(self):
        return tr.tiny_edl(tr.spans(self.clips, 2.0), 2.0, style={"grade": "neutral", "correct": {"ev": 0.3}})

    def test_a_final_render_uses_lossless_parts_and_delivers_8_bit(self):
        res, path = tr.render(self.edl(), self.root, "precise", quality="max")
        parts = sorted((self.root / "precise_work" / "parts" / "main").glob("*.mkv"))
        self.assertEqual(len(parts), 2)
        self.assertEqual(vl.probe(parts[0])["pix_fmt"], "yuv420p10le")
        self.assertEqual(res["parts"], "10-bit lossless")
        self.assertEqual(vl.probe(res["out"])["pix_fmt"], "yuv420p")
        self.assertEqual(tr.count_frames(res["out"]), 60)
        old = time.time() - 30 * 86400
        os.utime(parts[0], (old, old))
        again = vr.render(path, out=self.root / "precise2.mp4", workdir=self.root / "precise_work", encoder="libx264",
                          quality="max", log=lambda *_: None)
        self.assertEqual(again["parts_cached"], 2, "lossless parts are cached like the light ones")
        self.assertGreater(parts[0].stat().st_mtime, time.time() - 3600, "a reused part counts as used for gc_cache")

    def test_the_precise_chain_is_closer_to_the_lossless_picture(self):
        """Both deliveries against the same filters without any lossy step or 8-bit stage: the precise one loses less."""
        light, _ = tr.render(self.edl(), self.root, "lightq", quality="standard")
        precise, _ = tr.render(self.edl(), self.root, "preciseq", quality="max")
        ref = self.root / "ref.mkv"
        pl = vr.part_plan(self.edl(), 0, vr.ve.timeline(self.edl()), {}, 1.0, None, self.root / "luts")
        (self.root / "luts").mkdir(exist_ok=True)
        pl["pix_fmt"] = "gbrp16le"
        win = vr.shot_window(pl, vl.probe(pl["clip"]))
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-ss", f"{win['start']:.3f}", "-i", pl["clip"], "-an",
                "-frames:v", str(pl["n"]), "-vf", vr.part_vf(pl, False), "-c:v", "ffv1", "-pix_fmt", "gbrp16le", vl.ffp(ref)])

        def psnr(video):
            r = vl.run([vl.find_ffmpeg()["ffmpeg"], "-hide_banner", "-i", vl.ffp(video), "-i", vl.ffp(ref), "-lavfi",
                        f"[0:v]zscale=min=709:rin=tv:{vr.Z709}:m=709:r=tv,format=yuv444p16le,trim=end_frame={pl['n'] - 2}[a];"
                        f"[1:v]zscale=rin=full:{vr.Z709}:m=709:r=tv,format=yuv444p16le,trim=end_frame={pl['n'] - 2}[b];[a][b]psnr",
                        "-f", "null", "-"], check=False)
            return float(re.search(r"average:([\d.]+)", r.stderr).group(1))
        self.assertGreater(psnr(precise["out"]), psnr(light["out"]))

    def test_the_master_is_10_bit_hevc_of_the_same_length(self):
        if not vr.master_encoder():
            self.skipTest("no 10-bit HEVC encoder in this FFmpeg")
        res, _ = tr.render(self.edl(), self.root, "master", quality="high", master=True)
        info = vl.probe(res["master"])
        self.assertEqual((info["codec"], info["pix_fmt"]), ("hevc", "yuv420p10le"))
        self.assertEqual(tr.count_frames(res["master"]), 60)
        self.assertEqual(vl.probe(res["out"])["pix_fmt"], "yuv420p", "the delivery file stays 8-bit H.264")

    def test_qa_reads_a_video_given_by_a_relative_path(self):
        """The picture analysis runs FFmpeg in a temporary folder; `render --out sub/x.mp4 --qa` used to fail there."""
        import vid_qa
        res, _ = tr.render(self.edl(), self.root, "relqa", quality="standard")
        here = os.getcwd()
        try:
            os.chdir(self.root)
            self.assertTrue(vid_qa.measure_picture("relqa.mp4"))          # raised "picture analysis failed" before
        finally:
            os.chdir(here)

    def test_a_master_is_not_made_from_a_preview(self):
        with self.assertRaises(vl.EngineError):
            tr.render(self.edl(), self.root, "mprev", preview=True, master=True)


if __name__ == "__main__":
    unittest.main()
