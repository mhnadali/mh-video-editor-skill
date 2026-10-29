"""Phase L (0.27): workflow and speed. `prepare` from a footage folder to the review page with every step cached, `clean` that
reports the work folder and deletes only what can be made again, and the review page's frame strips reused."""
import io
import os
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import helpers
import mh_video
import vid_deliver as vd
import vid_review
import vidlib as vl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def cli(*argv) -> str:
    a = mh_video.build_parser().parse_args([str(x) for x in argv])
    buf = io.StringIO()
    with redirect_stdout(buf):
        a.fn(a)
    return buf.getvalue()


class Shifts(unittest.TestCase):
    def test_a_reprobe_keeps_the_clock_corrections_of_the_inventory(self):
        inv = [{"device": "DJI OsmoPocket3", "clock_shift_min": -30}, {"device": "iPhone", "clock_shift_min": 0}, {"device": None}]
        self.assertEqual(mh_video.inventory_shifts(inv), ["DJI OsmoPocket3=-30"])
        self.assertEqual(mh_video.inventory_shifts([]), [])

    def test_prepare_parses(self):
        a = mh_video.build_parser().parse_args(["prepare", "--footage", "F", "--music", "m.mp3", "--work", "W", "--duration", "30",
                                                "--until", "beats", "--clock-shift", "DJI=-30"])
        self.assertEqual((a.until, a.clock_shift, a.variant), ("beats", ["DJI=-30"], "balanced"))


@NEEDS_FF
@helpers.slow
class Prepare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import make_sample
        cls.root = helpers.tmpdir() / "phase_l"
        res = make_sample.make_sample(cls.root, clips=8, seconds=6.0)
        cls.footage = cls.root / "footage"
        cls.music = res.get("music") or str(cls.footage / "music.wav")
        cls.work = cls.root / "work"

    def test_the_first_run_stops_for_chapters_and_the_second_reaches_the_review_page(self):
        out = cli("prepare", "--footage", self.footage, "--music", self.music, "--work", self.work, "--duration", 8)
        self.assertIn("NEEDS_INPUT", out)
        self.assertTrue((self.work / "chapters.json").is_file(), "a draft to name and weigh")
        out = cli("prepare", "--footage", self.footage, "--music", self.music, "--work", self.work, "--duration", 8, "--no-drop")
        self.assertIn("NEEDS_APPROVAL", out)
        self.assertTrue((self.work / "review.html").is_file())
        steps = {s["step"]: s for s in vl.load_json(self.work / "pipeline.json")["steps"]}
        self.assertIn("the footage has not changed", steps["probe"].get("note", ""))
        self.assertIn("the music has not changed", steps["beats"].get("note", ""))
        out = cli("prepare", "--footage", self.footage, "--music", self.music, "--work", self.work, "--duration", 8, "--until", "score")
        self.assertIn("stopped after score", out)

    def test_a_new_clip_is_probed_and_the_old_ones_stay_measured(self):
        cli("prepare", "--footage", self.footage, "--music", self.music, "--work", self.work, "--duration", 8, "--until", "score")
        before = vl.load_json(self.work / "scores.json")
        extra = self.footage / "clip99.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=6", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", vl.ffp(extra)])
        try:
            out = cli("prepare", "--footage", self.footage, "--music", self.music, "--work", self.work, "--duration", 8, "--until", "score")
            self.assertIn("== probe\n", out, "the folder changed: probe again")
            inv = vl.load_json(self.work / "inventory.json")["clips"]
            self.assertIn("clip99.mp4", [Path(r["path"]).name for r in inv])
            after = vl.load_json(self.work / "scores.json")
            self.assertEqual(len(after["scores"]), len(before["scores"]) + 1)
        finally:
            extra.unlink(missing_ok=True)


class Clean(unittest.TestCase):
    def test_only_old_files_that_can_be_made_again_are_deleted(self):
        work = helpers.tmpdir() / "phase_l_clean"
        files = {"v_work/parts/main/000_a.mp4": True, "review_media/1_0_1400.mp4": True, "review_media/strips/1_0_0_140_0.jpg": True,
                 "moments/x.json": True, "inventory.json": False, "scores.json": False, "storyboard.json": False,
                 "out/final.mp4": False, "decisions.json": False}
        old = time.time() - 30 * 86400
        for rel in files:
            p = work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * 1000)
            os.utime(p, (old, old))
        (work / "review_media" / "2_0_1400.mp4").write_bytes(b"y" * 1000)          # recent: kept
        rep = vd.workspace(work, 14)
        kinds = {r["kind"]: r for r in rep["rows"]}
        self.assertTrue(kinds["render parts"]["deletable"] and not kinds["videos"]["deletable"])
        self.assertIn("dry run", cli("clean", "--work", work))
        self.assertTrue((work / "v_work/parts/main/000_a.mp4").is_file(), "a dry run deletes nothing")
        cli("clean", "--work", work, "--yes")
        for rel, gone in files.items():
            self.assertEqual(not (work / rel).is_file(), gone, rel)
        self.assertTrue((work / "review_media" / "2_0_1400.mp4").is_file())


@NEEDS_FF
class Strips(unittest.TestCase):
    def test_frame_strips_are_reused(self):
        root = helpers.tmpdir() / "phase_l_strips"
        clip = helpers.make_clips(root / "clips", 1, seconds=3.0)[0]
        st = os.stat(clip)
        rec = {"index": 0, "path": str(clip), "duration": 3.0, "size": st.st_size, "mtime": int(st.st_mtime)}
        (root / "strips").mkdir(exist_ok=True)
        first = vid_review.strip_for(rec, root / "strips")
        self.assertEqual(len(first), 3)
        from unittest import mock
        with mock.patch.object(vid_review.vid_sheets, "_thumb", side_effect=AssertionError("made again")):
            self.assertEqual(vid_review.strip_for(rec, root / "strips"), first)
        self.assertEqual(len(vid_review.strip_for(dict(rec, mtime=rec["mtime"] + 5), root / "strips")), 3, "a changed file: new strip")


if __name__ == "__main__":
    unittest.main()
