"""Phase 2 of the selection depth: the best moment inside a clip (sharp, steady, the action peak, no cut)."""
import unittest
from unittest import mock

import helpers
import vid_moment as vm
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")

FPS = 6


def series(seconds=20.0, motion=None, bright=120.0, blur=3.0, dx=0.0, dy=0.0, start=0.0, wobble=None, spike=None):
    """A synthetic analysed clip. `motion(t)` gives the motion value, `wobble` = (t0, t1) makes dx alternate there."""
    n = int(seconds * FPS)
    ts = [i / FPS for i in range(n)]
    m = [motion(t) if motion else 1.0 for t in ts]
    if spike is not None:
        m[int(spike * FPS)] = 200.0
    dxs = [dx] * n
    if wobble:
        for i, t in enumerate(ts):
            if wobble[0] <= t < wobble[1]:
                dxs[i] = 4.0 if i % 2 else -4.0
    return {"start": start, "fps": FPS, "n": n, "bright": [bright] * n, "motion": m, "blur": [blur] * n,
            "dx": dxs, "dy": [dy] * n, "dxl": dxs[:], "dxr": dxs[:]}


class Segment(unittest.TestCase):
    def test_an_action_slot_gets_the_action_peak(self):
        s = series(motion=lambda t: 20.0 if 12.0 <= t < 14.5 else 1.0)
        seg = vm.best_segment(s, 1.8, "action")
        self.assertGreaterEqual(seg["start"], 11.0)
        self.assertLessEqual(seg["start"] + seg["length"], 15.6)
        self.assertGreaterEqual(seg["peak_at"], 12.0)
        self.assertLessEqual(seg["peak_at"], 14.6)

    def test_a_calm_slot_gets_a_quiet_stretch(self):
        s = series(motion=lambda t: 20.0 if 12.0 <= t < 14.5 else 1.0)
        seg = vm.best_segment(s, 1.8, "calm")
        self.assertFalse(seg["start"] < 14.6 and seg["start"] + seg["length"] > 12.0, "not across the burst")

    def test_the_first_and_last_half_second_are_never_chosen(self):
        s = series(motion=lambda t: 20.0 if t < 1.0 or t > 19.0 else 1.0)
        seg = vm.best_segment(s, 1.5, "action")
        self.assertGreaterEqual(seg["start"], vm.MARGIN - 1e-9)
        self.assertLessEqual(seg["start"] + seg["length"], 20.0 - vm.MARGIN + 0.01)

    def test_a_wobbling_stretch_loses_to_a_steady_one(self):
        s = series(wobble=(2.0, 9.0))
        seg = vm.best_segment(s, 2.0, "any")
        self.assertGreaterEqual(seg["start"], 8.5)
        self.assertEqual(seg["motion_type"], "static")

    def test_a_cut_or_flash_is_avoided(self):
        s = series(motion=lambda t: 2.0 if t > 4 else 4.0, spike=6.0)
        for want in ("any", "action", "calm"):
            seg = vm.best_segment(s, 2.0, want)
            self.assertFalse(seg["start"] <= 6.0 < seg["start"] + seg["length"], want)
            self.assertFalse(seg["had_cut"])

    def test_sharp_and_well_exposed_beats_soft_and_dark(self):
        s = series()
        n = s["n"]
        for i in range(0, n // 2):
            s["blur"][i] = 9.0
            s["bright"][i] = 25.0
        seg = vm.best_segment(s, 2.0, "any")
        self.assertGreaterEqual(seg["start"], 9.5)

    def test_a_clip_no_longer_than_the_segment_is_taken_whole(self):
        s = series(seconds=1.5)
        seg = vm.best_segment(s, 2.0, "any")
        self.assertEqual(seg["start"], 0.0)
        self.assertAlmostEqual(seg["length"], 1.5, delta=0.2)

    def test_start_is_in_the_clip_own_time_when_only_a_part_was_analysed(self):
        s = series(seconds=10.0, motion=lambda t: 20.0 if 4.0 <= t < 6.0 else 1.0, start=30.0)
        seg = vm.best_segment(s, 1.5, "action")
        self.assertGreaterEqual(seg["start"], 33.0)
        self.assertLessEqual(seg["start"], 36.5)


class Motion(unittest.TestCase):
    def test_kinds_of_camera_movement(self):
        n = 12
        still = [0.0] * n
        pan = [2.0] * n
        self.assertEqual(vm.classify_motion(still, still, still, still), "static")
        self.assertEqual(vm.classify_motion(pan, still, pan, pan), "pan")
        self.assertEqual(vm.classify_motion(still, pan, still, still), "tilt")
        alt = [4.0 if i % 2 else -4.0 for i in range(n)]
        self.assertEqual(vm.classify_motion(alt, alt, alt, alt), "hand-held")
        left = [-2.0] * n
        right = [2.0] * n
        self.assertEqual(vm.classify_motion(still, still, left, right), "push-pull")
        self.assertEqual(vm.classify_motion([1.0], [1.0], [1.0], [1.0]), "static")

    def test_the_end_of_the_action(self):
        m = [1.0] * 6 + [20.0] * 6 + [1.0] * 12
        self.assertEqual(vm.end_of_action(m, FPS, peak=8), 2.0)
        self.assertIsNone(vm.end_of_action([1.0] * 6, FPS, peak=5))
        self.assertIsNone(vm.end_of_action([20.0] * 10, FPS, peak=3), "the action never ends inside the clip")

    def test_smoothing_keeps_the_length(self):
        self.assertEqual(len(vm.smooth([1.0, 5.0, 1.0, 5.0], 3)), 4)
        self.assertAlmostEqual(vm.smooth([0.0, 3.0, 0.0], 3)[1], 1.0)

    def test_shifts_follow_a_panning_picture(self):
        def frame(shift):
            return bytes(((x + shift) * 37 + y * 11) % 200 for y in range(vm.GH) for x in range(vm.GW))
        frames = [frame(2 * k) for k in range(8)]                       # the texture moves 2 px to the left per frame
        dx, dy, dxl, dxr = vm.shifts(frames)
        self.assertEqual(len(dx), 8)
        self.assertEqual(dx[0], 0.0)
        self.assertLess(sum(dx[1:]) / 7, -1.0)                          # a texture that reads (x + shift) moves LEFT as shift grows
        self.assertLess(abs(sum(dy[1:]) / 7), 0.5)
        flat = [bytes([90]) * (vm.GW * vm.GH)] * 5
        self.assertEqual(vm.shifts(flat)[0], [0.0] * 5, "nothing to track")


class Storyboard(unittest.TestCase):
    def sb(self):
        rows = [{"slot": 0, "clip": 1, "len": 1.2, "start": 2.0, "start_kind": "best frame window", "role": "action", "energy": 0.8},
                {"slot": 1, "clip": 2, "len": 1.2, "start": 3.0, "start_kind": "best frame window", "role": "portrait", "energy": 0.2},
                {"slot": 2, "clip": 3, "len": 1.2, "start": 1.0, "start_kind": "best frame window", "role": None, "energy": 0.5}]
        return {"rows": rows, "slots": 3, "chapters": [], "coverage": {}, "warnings": [], "duration": 10}

    def test_want_for(self):
        self.assertEqual(vm.want_for({"role": "action", "energy": 0.1}), "action")
        self.assertEqual(vm.want_for({"role": None, "energy": 0.7}), "action")
        self.assertEqual(vm.want_for({"role": "portrait", "energy": 0.2}), "calm")
        self.assertEqual(vm.want_for({"role": "portrait", "energy": 0.5}), "any")

    def test_refine_sets_the_start_times_and_records_the_moment(self):
        inv = [{"index": i, "path": f"c{i}.mp4", "duration": 20.0, "hdr": False} for i in (1, 2, 3)]
        burst = series(motion=lambda t: 20.0 if 12.0 <= t < 14.5 else 1.0)

        def fake(path, duration, work, hdr=False, around=None, fps=vm.FPS):
            if path == "c3.mp4":
                raise vl.EngineError("cannot read")
            return burst

        with mock.patch.object(vm, "analyse_clip", fake):
            out = vm.refine_storyboard(self.sb(), inv, {}, "work", workers=2)
        rows = {r["clip"]: r for r in out["rows"]}
        self.assertGreaterEqual(rows[1]["start"], 11.0)
        self.assertTrue(rows[1]["start_kind"].startswith("refined moment"))
        self.assertIn("motion_type", rows[1]["moment"])
        self.assertEqual(rows[3]["start"], 1.0, "an unreadable clip keeps its start")
        self.assertIn("error", rows[3]["moment"])
        self.assertEqual(out["refined"]["clips"], 3)
        self.assertEqual(self.sb()["rows"][0]["start"], 2.0, "the input storyboard is not modified")


@NEEDS_FF
@helpers.slow
class RealMedia(unittest.TestCase):
    def test_scan_finds_where_the_action_is(self):
        import tempfile
        from pathlib import Path
        root = helpers.tmpdir() / "momentmedia"
        root.mkdir(exist_ok=True)
        # still for 5 s, then a steady pan for the rest: the picture only moves after second 5
        clip = helpers.make_clip(root / "burst.mp4", "testsrc2", size="640x360", seconds=14.0,
                                 extra=["-vf", "crop=320:360:x='if(lt(t,5),0,min(320,(t-5)*35))':y=0"])
        s = vm.scan(vl.ffp(clip), 0.0, 14.0)
        self.assertGreater(s["n"], 40)
        self.assertEqual(len(s["motion"]), s["n"])
        seg = vm.best_segment(s, 1.8, "action")
        self.assertGreaterEqual(seg["start"], 4.0)
        calm = vm.best_segment(s, 1.8, "calm")
        self.assertLess(calm["start"], 4.0)
        with tempfile.TemporaryDirectory() as d:
            a = vm.analyse_clip(vl.ffp(clip), 14.0, Path(d))
            self.assertEqual(len(list((Path(d) / "moments").glob("*.json"))), 1)
            with mock.patch.object(vm, "scan", side_effect=AssertionError("the cache must be used")):
                b = vm.analyse_clip(vl.ffp(clip), 14.0, Path(d))
            self.assertEqual(a["n"], b["n"])

    def test_a_long_clip_is_analysed_around_its_best_window_only(self):
        from pathlib import Path
        import tempfile
        root = helpers.tmpdir() / "momentmedia"
        root.mkdir(exist_ok=True)
        clip = helpers.make_clip(root / "long.mp4", "testsrc2", size="320x180", seconds=3.0)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(vm, "MAX_SPAN", 1.5):
            s = vm.analyse_clip(vl.ffp(clip), 3.0, Path(d), around=2.5)
        self.assertAlmostEqual(s["start"], 1.5, places=1)
        self.assertLessEqual(s["n"], int(1.5 * vm.FPS) + 2)


class Cli(unittest.TestCase):
    def test_the_command_exists(self):
        import mh_video
        a = mh_video.build_parser().parse_args(["story", "refine", "--work", "w", "--storyboard", "sb.json"])
        self.assertEqual(a.workers, 4)
        self.assertEqual(a.action, "refine")


if __name__ == "__main__":
    unittest.main()
