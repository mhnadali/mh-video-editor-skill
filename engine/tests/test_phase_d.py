"""Phase D (image quality): camera colour matching, stabilising shaky picks, the slow-motion frame check."""
import math
import random
import subprocess
import unittest

import helpers
import vid_camera as cam
import vid_color as vc
import vid_edl as ve
import vid_plan as vp
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
BASE_T = 1.79e9


def stats_for(r_over_g, b_over_g, y=120.0):
    """Signalstats means (Y, U, V) whose mean RGB has these R/G and B/G ratios (approximately, via a small search)."""
    g = (y - 16.0) / 219.0 * 0.9
    r, b = g * r_over_g, g * b_over_g
    yn = 0.2126 * r + 0.7152 * g + 0.0722 * b
    cb = (b - yn) / 1.8556
    cr = (r - yn) / 1.5748
    return {"YAVG": yn * 219.0 + 16.0, "UAVG": cb * 224.0 + 128.0, "VAVG": cr * 224.0 + 128.0}


def library(n_pairs=30, offset=(0.05, -0.08), noise=0.02, third=None, seed=4):
    """Pairs of clips from camera A (main, more clips) and camera B filmed seconds apart; B is redder / less blue by `offset`."""
    rng = random.Random(seed)
    inv, scores, truth = [], {}, {}
    t = 0.0
    i = 0
    for _ in range(n_pairs):
        t += 300.0
        rg, bg = math.exp(rng.uniform(-0.2, 0.2)), math.exp(rng.uniform(-0.2, 0.2))           # the light of this moment
        for dev, off in (("A", (0.0, 0.0)), ("B", offset)):
            inv.append({"index": i, "path": f"c{i}.mp4", "device": dev, "taken_epoch": BASE_T + t + (5 if dev == "B" else 0),
                        "duration": 10.0, "dup_of": None})
            scores[i] = {"quality": 70, "best_start": 1.0}
            truth[i] = stats_for(rg * math.exp(off[0] + rng.gauss(0, noise)), bg * math.exp(off[1] + rng.gauss(0, noise)))
            i += 1
    for _ in range(n_pairs):                                                                     # more clips of A alone
        t += 300.0
        inv.append({"index": i, "path": f"c{i}.mp4", "device": "A", "taken_epoch": BASE_T + t, "duration": 10.0, "dup_of": None})
        scores[i] = {"quality": 70, "best_start": 1.0}
        truth[i] = stats_for(1.0, 1.0)
        i += 1
    if third:
        t += 5000.0
        inv.append({"index": i, "path": f"c{i}.mp4", "device": third, "taken_epoch": BASE_T + t, "duration": 10.0, "dup_of": None})
        scores[i] = {"quality": 70, "best_start": 1.0}
        truth[i] = stats_for(1.0, 1.0)
    by_path = {r["path"]: r["index"] for r in inv}
    return inv, scores, (lambda path, start, dur, hdr: truth[by_path[path]])


class CameraMatch(unittest.TestCase):
    def test_a_real_offset_is_found_and_removed_from_the_minor_camera_only(self):
        inv, sc, fn = library(offset=(0.03, -0.04))                     # small enough to stay inside the gain cap
        rep = cam.estimate(inv, sc, None, measure_fn=fn)
        self.assertEqual(rep["reference"], "A", "the camera that filmed most is the reference")
        self.assertEqual(rep["gains"]["A"], [1.0, 1.0, 1.0])
        ev = rep["evidence"][0]
        self.assertAlmostEqual(ev["red"], 0.03, delta=0.015)
        self.assertAlmostEqual(ev["blue"], -0.04, delta=0.015)
        k = cam._linear_exponent()                                                              # noqa: SLF001
        self.assertAlmostEqual(math.log(rep["gains"]["B"][0]), -k * ev["red"], delta=0.01)
        self.assertAlmostEqual(math.log(rep["gains"]["B"][2]), -k * ev["blue"], delta=0.01)
        self.assertLess(rep["gains"]["B"][0], 1.0)
        self.assertGreater(rep["gains"]["B"][2], 1.0)

    def test_an_offset_within_the_noise_or_with_few_pairs_is_not_used(self):
        inv, sc, fn = library(offset=(0.005, -0.004))
        rep = cam.estimate(inv, sc, None, measure_fn=fn)
        self.assertEqual(rep["gains"]["B"], [1.0, 1.0, 1.0])
        inv, sc, fn = library(n_pairs=cam.MIN_PAIRS - 2)
        rep = cam.estimate(inv, sc, None, measure_fn=fn)
        self.assertEqual(rep["gains"]["B"], [1.0, 1.0, 1.0])
        self.assertIn("B", rep["not_matched"])

    def test_a_camera_without_simultaneous_clips_is_left_alone(self):
        inv, sc, fn = library(third="C")
        rep = cam.estimate(inv, sc, None, measure_fn=fn)
        self.assertEqual(rep["gains"]["C"], [1.0, 1.0, 1.0])
        self.assertIn("C", rep["not_matched"])

    def test_gains_are_capped_and_the_reference_can_be_chosen(self):
        inv, sc, fn = library(offset=(0.3, -0.3))
        rep = cam.estimate(inv, sc, None, measure_fn=fn)
        self.assertEqual(rep["gains"]["B"][0], round(1 - cam.MAX_GAIN, 4))
        self.assertEqual(rep["gains"]["B"][2], round(1 + cam.MAX_GAIN, 4))
        inv, sc, fn = library()
        rep = cam.estimate(inv, sc, None, measure_fn=fn, reference="B")
        self.assertEqual(rep["gains"]["B"], [1.0, 1.0, 1.0])
        self.assertGreater(rep["gains"]["A"][0], 1.0)
        with self.assertRaises(vl.EngineError):
            cam.estimate(inv, sc, None, measure_fn=fn, reference="Z")

    def test_three_cameras_are_solved_together(self):
        devices = ["A", "B", "C"]
        edges = [("A", "B", 0.05, 50), ("B", "C", 0.03, 20), ("A", "C", 0.08, 10)]
        off = cam._solve(devices, edges, {"A": 1.0, "B": 0.0, "C": 0.0})                          # noqa: SLF001
        self.assertAlmostEqual(off["A"], 0.0, places=6)
        self.assertAlmostEqual(off["B"], 0.05, places=3)
        self.assertAlmostEqual(off["C"], 0.08, places=3)
        self.assertEqual(cam._reachable("A", [("A", "B", 1, 1)]), {"A", "B"})                    # noqa: SLF001

    def test_pairs_are_only_two_cameras_close_in_time(self):
        inv = [{"index": 0, "path": "a", "device": "A", "taken_epoch": 100.0, "duration": 9, "dup_of": None},
               {"index": 1, "path": "b", "device": "B", "taken_epoch": 130.0, "duration": 9, "dup_of": None},
               {"index": 2, "path": "c", "device": "A", "taken_epoch": 140.0, "duration": 9, "dup_of": None},
               {"index": 3, "path": "d", "device": "B", "taken_epoch": 400.0, "duration": 9, "dup_of": None},
               {"index": 4, "path": "e", "device": "B", "taken_epoch": 401.0, "duration": 3, "dup_of": None}]
        sc = {i: {"quality": 1} for i in range(5)}
        self.assertEqual(cam.cross_camera_pairs(inv, sc, 60.0), [(0, 1, 30.0), (1, 2, 10.0)])


class Edl(unittest.TestCase):
    def edl(self):
        return {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": 3.0},
                "shots": [{"clip": "c0.mp4", "span": [0, 1]}, {"clip": "c1.mp4", "span": [1, 2], "correct": {"ev": 0.2, "gain": [1.0, 1.0, 1.1]}},
                          {"clip": "c9.mp4", "span": [2, 3]}]}

    def test_camera_gains_are_multiplied_in_once(self):
        inv = [{"index": 0, "path": "c0.mp4", "device": "A"}, {"index": 1, "path": "c1.mp4", "device": "B"},
               {"index": 9, "path": "c9.mp4", "device": "A"}]
        rep = {"gains": {"A": [1.0, 1.0, 1.0], "B": [0.9, 1.0, 1.1]}, "reference": "A"}
        edl = self.edl()
        self.assertEqual(cam.apply_to_edl(edl, rep, inv), 1)
        self.assertEqual(edl["shots"][1]["correct"]["gain"], [0.9, 1.0, 1.21])
        self.assertEqual(edl["shots"][1]["correct"]["ev"], 0.2, "the rest of the correction stays")
        self.assertNotIn("correct", edl["shots"][0])
        self.assertEqual(cam.apply_to_edl(edl, rep, inv), 0, "twice changes nothing")
        self.assertEqual(edl["shots"][1]["correct"]["gain"], [0.9, 1.0, 1.21])
        self.assertEqual(cam.camera_gain_of(edl, 1), [0.9, 1.0, 1.1])
        self.assertIsNone(cam.camera_gain_of(edl, 0))
        self.assertEqual(ve.structure_errors(edl) if hasattr(ve, "structure_errors") else [], [])

    def test_a_new_colour_plan_keeps_the_camera_gain(self):
        inv = [{"index": 1, "path": "c1.mp4", "device": "B"}]
        edl = self.edl()
        cam.apply_to_edl(edl, {"gains": {"B": [0.9, 1.0, 1.1]}, "reference": "A"}, inv)
        plan = {1: {"correct": {"ev": 0.1, "black": 0.0, "white": 1.0, "gamma": 1.0, "gain": [1.0, 1.0, 1.0]}, "reasons": []},
                2: {"correct": None, "reasons": []}}
        vc.apply_plan(edl, plan, overwrite=True)
        self.assertEqual(edl["shots"][1]["correct"]["gain"], [0.9, 1.0, 1.1])
        self.assertEqual(edl["shots"][1]["correct"]["ev"], 0.1)
        plan = {1: {"correct": None, "reasons": []}}
        vc.apply_plan(edl, plan, overwrite=True)
        self.assertEqual(edl["shots"][1]["correct"]["gain"], [0.9, 1.0, 1.1], "even with no other correction")


class Stabilise(unittest.TestCase):
    def test_only_shaky_shots_without_a_setting_are_stabilised(self):
        edl = {"shots": [{"clip": "a.mp4"}, {"clip": "b.mp4"}, {"clip": "c.mp4", "stabilize": False}, {"clip": "d.mp4"}]}
        scores = {0: {"path": "a.mp4", "shake": 2.4}, 1: {"path": "b.mp4", "shake": 0.4}, 2: {"path": "c.mp4", "shake": 3.0}}
        self.assertEqual(vp.stabilize_shaky(edl, scores), [0])
        self.assertEqual(edl["shots"][0]["stabilize"], "light")
        self.assertNotIn("stabilize", edl["shots"][1])
        self.assertIs(edl["shots"][2]["stabilize"], False, "the user's own setting wins")
        self.assertEqual(vp.stabilize_shaky({"shots": [{"clip": "b.mp4"}]}, scores, 0.3), [0])


@NEEDS_FF
@helpers.slow
class RealMedia(unittest.TestCase):
    def clip(self, name, fps):
        root = helpers.tmpdir() / "phase_d"
        root.mkdir(exist_ok=True)
        return helpers.make_clip(root / name, "testsrc2", size="320x180", seconds=3.0, fps=fps)

    def lint_codes(self, clip, speed):
        edl = {"version": 1, "project": {"width": 180, "height": 320, "fps": 30, "duration": 1.0, "music": None},
               "style": {"drop_flash": False, "end_flare": False},
               "shots": [{"clip": vl.ffp(clip), "span": [0, 1], "speed": speed}]}
        return [i["code"] for i in ve.lint(edl, check_files=True)]

    def test_slow_motion_of_a_30_fps_clip_is_flagged_and_of_a_60_fps_clip_is_not(self):
        self.assertIn("slowmo-invented-frames", self.lint_codes(self.clip("f30.mp4", 30), 0.5))
        self.assertNotIn("slowmo-invented-frames", self.lint_codes(self.clip("f60.mp4", 60), 0.5))
        self.assertNotIn("slowmo-invented-frames", self.lint_codes(self.clip("f30b.mp4", 30), 1.5), "speeding up drops frames")


if __name__ == "__main__":
    unittest.main()
