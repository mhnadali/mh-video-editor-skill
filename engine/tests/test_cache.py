"""Phase 5: the render cache survives crashes, and the same EDL always gives the same picture."""
import unittest
from pathlib import Path

import helpers
import test_robustness as tr
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def min_psnr(a, b) -> float:
    """Lowest per-frame PSNR (dB) between two videos of the same size and length."""
    r = vl.run([vl.find_ffmpeg()["ffmpeg"], "-hide_banner", "-nostats", "-i", vl.ffp(a), "-i", vl.ffp(b),
                "-lavfi", "[0:v][1:v]psnr", "-f", "null", "-"], check=False)
    import re
    m = re.search(r"min:([\d.]+|inf)", r.stderr or "")
    if not m:
        raise AssertionError("psnr gave no summary: " + (r.stderr or "")[-300:])
    return float("inf") if m.group(1) == "inf" else float(m.group(1))


@NEEDS_FF
@helpers.slow
class Cache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "cache"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.clips = helpers.make_clips(cls.root / "clips", 3, seconds=3.0)
        cls.music = cls.root / "m.wav"
        helpers.click_track(cls.music, bpm=120, first=0.2, seconds=3.0, step_at=None)

    def edl(self, with_music=False):
        return tr.tiny_edl(tr.spans(self.clips, 3.0), 3.0, music=vl.ffp(self.music) if with_music else None)

    def parts(self, name):
        return sorted((self.root / f"{name}_work" / "parts" / "main").glob("*.mp4"))

    def test_a_part_cut_short_by_a_crash_is_rendered_again(self):
        res, path = tr.render(self.edl(), self.root, "trunc")
        self.assertEqual(res["parts_cached"], 0)
        part = self.parts("trunc")[1]
        data = part.read_bytes()
        part.write_bytes(data[: max(vr.MIN_PART_BYTES + 100, len(data) // 3)])        # bigger than MIN_PART_BYTES, but broken
        again = vr.render(path, out=self.root / "trunc2.mp4", workdir=self.root / "trunc_work", encoder="libx264", log=lambda *_: None)
        self.assertEqual(again["shots"] - again["parts_cached"], 1)
        self.assertEqual(tr.count_frames(again["out"]), 90)

    def test_an_empty_or_garbage_part_is_rendered_again(self):
        res, path = tr.render(self.edl(), self.root, "garbage")
        victim = self.parts("garbage")[0]
        victim.write_bytes(b"not a video" * 100)
        again = vr.render(path, out=self.root / "garbage2.mp4", workdir=self.root / "garbage_work", encoder="libx264", log=lambda *_: None)
        self.assertEqual(again["shots"] - again["parts_cached"], 1)
        self.assertEqual(tr.count_frames(again["out"]), 90)

    def test_a_truncated_audio_master_is_rebuilt(self):
        res, path = tr.render(self.edl(with_music=True), self.root, "audio")
        masters = sorted((self.root / "audio_work").glob("master_*.wav"))
        self.assertEqual(len(masters), 1)
        size = masters[0].stat().st_size
        masters[0].write_bytes(masters[0].read_bytes()[: size // 3])
        vr.render(path, out=self.root / "audio2.mp4", workdir=self.root / "audio_work", encoder="libx264", log=lambda *_: None)
        self.assertEqual(masters[0].stat().st_size, size)                             # rebuilt to the original length
        self.assertAlmostEqual(vl.probe(self.root / "audio2.mp4")["duration"], 3.0, delta=0.1)

    def test_leftover_temp_files_from_a_killed_render_are_harmless(self):
        res, path = tr.render(self.edl(), self.root, "leftover")
        work = self.root / "leftover_work" / "parts" / "main"
        (work / "000_deadbeefdeadbeef.tmp.mp4").write_bytes(b"\x00" * 5000)
        (work / "001_00000000.mp4.123.456.tmp").write_bytes(b"junk")
        again = vr.render(path, out=self.root / "leftover2.mp4", workdir=self.root / "leftover_work", encoder="libx264", log=lambda *_: None)
        self.assertEqual(again["parts_cached"], again["shots"])
        self.assertEqual(tr.count_frames(again["out"]), 90)

    def test_no_cache_flag_rebuilds_everything(self):
        res, path = tr.render(self.edl(), self.root, "nocache")
        again = vr.render(path, out=self.root / "nocache2.mp4", workdir=self.root / "nocache_work", encoder="libx264",
                          use_cache=False, log=lambda *_: None)
        self.assertEqual(again["parts_cached"], 0)


@NEEDS_FF
@helpers.slow
class Deterministic(unittest.TestCase):
    """Two cold renders of one EDL in different work folders must show the same picture. Not byte-identical files:
    x264's output depends on thread timing and count (measured: about 1 render in 12 differed in bytes under load, with
    invisible differences), so equality is judged by PSNR; hidden randomness (grain, random sources) would fail it."""

    def test_same_edl_same_picture(self):
        root = helpers.tmpdir() / "determinism"
        root.mkdir(parents=True, exist_ok=True)
        clips = helpers.make_clips(root / "clips", 3, seconds=3.0)
        edl = tr.tiny_edl(tr.spans(clips, 3.0), 3.0, transitions={"1": "fade", "2": "slideleft"},
                          style={"grade": {"look": "teal-orange", "strength": 0.7}, "zoom": 0.05, "drop_flash": False, "end_flare": False})
        a, _ = tr.render(edl, root, "det_a")
        b, _ = tr.render(edl, root, "det_b")
        self.assertEqual(tr.count_frames(a["out"]), 90)
        self.assertGreater(min_psnr(a["out"], b["out"]), 40.0)

    def test_different_edls_are_told_apart_by_the_same_measure(self):
        """The yardstick has to be able to fail: a different look must fall far below the threshold."""
        root = helpers.tmpdir() / "determinism_neg"
        root.mkdir(parents=True, exist_ok=True)
        clips = helpers.make_clips(root / "clips", 3, seconds=3.0)
        plain = tr.tiny_edl(tr.spans(clips, 3.0), 3.0, style={"grade": "neutral"})
        graded = tr.tiny_edl(tr.spans(clips, 3.0), 3.0, style={"grade": {"look": "bleach-bypass", "strength": 1.0}})
        a, _ = tr.render(plain, root, "neg_a")
        b, _ = tr.render(graded, root, "neg_b")
        self.assertLess(min_psnr(a["out"], b["out"]), 30.0)

    def test_preview_and_final_have_the_same_length(self):
        root = helpers.tmpdir() / "determinism2"
        root.mkdir(parents=True, exist_ok=True)
        clips = helpers.make_clips(root / "clips", 3, seconds=3.0)
        edl = tr.tiny_edl(tr.spans(clips, 3.0), 3.0)
        final, _ = tr.render(edl, root, "pf_final")
        prev, _ = tr.render(edl, root, "pf_prev", preview=True)
        self.assertEqual(tr.count_frames(final["out"]), tr.count_frames(prev["out"]))


if __name__ == "__main__":
    unittest.main()
