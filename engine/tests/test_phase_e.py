"""Phase E (speed and scale): skipping clips that are never used, time-left estimates, faster and reusable review proxies."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mh_video
import vid_review
import vid_score
import vidlib as vl


def records(n, short=()):
    return [{"index": i, "path": f"x{i}.mp4", "name": f"x{i}.mp4", "size": i, "mtime": 1, "duration": 3.0 if i in short else 8.0,
             "dup_of": None, "error": None} for i in range(n)]


def fake_score(r, hw=False):
    return {"index": r["index"], "path": r["path"], "quality": 50.0 + r["index"], "motion": float(r["index"]), "flags": [],
            "v": vid_score.SCHEMA}


class Score(unittest.TestCase):
    def test_clips_too_short_to_be_used_are_not_measured(self):
        recs = records(10, short={2, 5})
        with mock.patch.object(vid_score, "score_clip", side_effect=fake_score) as sc:
            out = vid_score.score_inventory(recs, hw=False, min_duration=5.0)
            self.assertEqual(sorted(s["index"] for s in out), [0, 1, 3, 4, 6, 7, 8, 9])
            self.assertEqual(sc.call_count, 8)
            out = vid_score.score_inventory(recs, hw=False)
            self.assertEqual(len(out), 10, "the default still measures every clip")

    def test_progress_says_how_long_is_left(self):
        lines = []
        with mock.patch.object(vid_score, "score_clip", side_effect=fake_score):
            vid_score.score_inventory(records(60), hw=False, workers=1, progress=lines.append)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("scored 25/60 (0 reused, about "), lines[0])
        self.assertTrue(lines[0].endswith(" left)"), lines[0])

    def test_progress_with_only_reused_entries_promises_nothing(self):
        recs = records(30)
        prev = {r["path"]: dict(fake_score(r), size=r["size"], mtime=r["mtime"]) for r in recs}
        lines = []
        vid_score.score_inventory(recs, hw=False, workers=1, progress=lines.append, previous=prev)
        self.assertEqual(lines, ["scored 25/30 (25 reused)"])

    def test_the_cli_option_exists(self):
        a = mh_video.build_parser().parse_args(["score", "--work", "W", "--min-duration", "5"])
        self.assertEqual(a.min_duration, 5.0)


class Proxies(unittest.TestCase):
    def test_a_proxy_decodes_cheaply_and_is_reused(self):
        calls = []

        def run(cmd, check=False):
            calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"x" * 500)
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(vl, "run", side_effect=run), \
                mock.patch.object(vl, "find_ffmpeg", return_value={"ffmpeg": "ff"}):
            dest = Path(d) / "m" / "3_500_1400.mp4"
            self.assertTrue(vid_review.make_proxy({"index": 3, "path": "a.mp4"}, dest, 5.0, 14.0))
            self.assertTrue(vid_review.make_proxy({"index": 3, "path": "a.mp4"}, dest, 5.0, 14.0))
        self.assertEqual(len(calls), 1, "the second call reuses the file")
        cmd = calls[0]
        self.assertIn("-skip_loop_filter", cmd)
        self.assertIn("bidir", cmd)
        self.assertLess(cmd.index("-skip_frame"), cmd.index("-i"), "decoder options come before the input")

    def test_the_window_is_in_the_file_name(self):
        seen = []
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(vid_review, "make_proxy", side_effect=lambda rec, dest, o, s: seen.append(dest.name) or True), \
                mock.patch.object(vid_review, "strip_for", return_value=["QUJD"]):
            sb = {"rows": [{"slot": 0, "clip": 1, "start": 9.0, "alternates": [2]}]}
            inv = [{"index": 1, "path": "a.mp4", "duration": 30.0}, {"index": 2, "path": "b.mp4", "duration": 8.0}]
            data = vid_review.build_data(sb, inv, {2: {"best_start": 1.0}}, Path(d), log=lambda *_: None, media_dir=Path(d), media_url="m/")
        o1, s1 = vid_review.proxy_window(30.0, 9.0)
        self.assertIn(f"1_{int(round(o1 * 100))}_{int(round(s1 * 100))}.mp4", seen)
        self.assertEqual(data["rows"][0]["clips"][0]["video"], f"m/1_{int(round(o1 * 100))}_{int(round(s1 * 100))}.mp4")


if __name__ == "__main__":
    unittest.main()
