"""Phase 4: stabilisation and speed changes (slow motion, speed-up, ramps)."""
import copy
import statistics
import unittest
from pathlib import Path

import helpers
import vid_edl as ve
import vid_motion as vm
import vid_qa as qa
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class Normalise(unittest.TestCase):
    def test_stabilize_forms(self):
        self.assertIsNone(vm.normalize_stabilize(None))
        self.assertIsNone(vm.normalize_stabilize(False))
        self.assertIsNone(vm.normalize_stabilize("off"))
        self.assertEqual(vm.normalize_stabilize(True), vm.STAB_PRESETS["light"])
        self.assertEqual(vm.normalize_stabilize("strong")["smoothing"], 20)
        custom = vm.normalize_stabilize({"smoothing": 12, "zoom": 2})
        self.assertEqual(custom["smoothing"], 12)
        self.assertEqual(custom["shakiness"], vm.STAB_PRESETS["light"]["shakiness"])
        for bad in ("heavy", {"shakiness": 11}, {"smoothing": 500}, {"zoom": 50}, 3.5, []):
            with self.assertRaises(vl.EngineError, msg=str(bad)):
                vm.normalize_stabilize(bad)

    def test_presets_do_not_leak(self):
        a = vm.normalize_stabilize("light")
        a["smoothing"] = 99
        self.assertNotEqual(vm.STAB_PRESETS["light"]["smoothing"], 99)

    def test_speed_forms(self):
        self.assertIsNone(vm.normalize_speed(None))
        self.assertIsNone(vm.normalize_speed(1))
        self.assertIsNone(vm.normalize_speed({"from": 1.0, "to": 1.0}))
        self.assertEqual(vm.normalize_speed(0.5), {"from": 0.5, "to": 0.5})
        self.assertEqual(vm.normalize_speed({"from": 1, "to": 0.4}), {"from": 1.0, "to": 0.4})
        for bad in (0.05, 9, "slow", {"from": 1}, True, {"from": 1, "to": 20}):
            with self.assertRaises(vl.EngineError, msg=str(bad)):
                vm.normalize_speed(bad)

    def test_source_seconds(self):
        self.assertEqual(vm.source_seconds(None, 2.0), 2.0)
        self.assertAlmostEqual(vm.source_seconds({"from": 0.5, "to": 0.5}, 2.0), 1.0)
        self.assertAlmostEqual(vm.source_seconds({"from": 1.0, "to": 0.4}, 1.0), 0.7)
        self.assertAlmostEqual(vm.average_speed({"from": 2, "to": 4}), 3.0)

    def test_choose_mode(self):
        slow = {"from": 0.5, "to": 0.5}
        self.assertEqual(vm.choose_mode(None, "auto", 1.0, 30, 30), "native")
        self.assertEqual(vm.choose_mode({"from": 2, "to": 2}, "auto", 1.0, 30, 30), "native")
        self.assertEqual(vm.choose_mode(slow, "auto", 1.0, 120, 30), "native")            # 120 fps at 50%: 60 real frames per second
        self.assertEqual(vm.choose_mode(slow, "auto", 1.0, 60, 30), "native")
        self.assertEqual(vm.choose_mode(slow, "auto", 1.0, 30, 30), "mci")                # needs invented frames, short shot
        self.assertEqual(vm.choose_mode(slow, "auto", 3.0, 30, 30), "blend")              # long shot: cheaper
        self.assertEqual(vm.choose_mode(slow, "mci", 3.0, 120, 30), "mci")                # explicit wins
        with self.assertRaises(vl.EngineError):
            vm.choose_mode(slow, "fancy", 1.0, 30, 30)

    def test_retime_chain_shapes(self):
        self.assertEqual(vm.retime_chain(None, 1.0, 30, 30), ["fps=30"])
        c = vm.retime_chain({"from": 0.5, "to": 0.5}, 1.0, 30, 30, "blend")
        self.assertEqual(c[0], "setpts=PTS-STARTPTS")
        self.assertEqual(c[1], "setpts=PTS/0.50000")
        self.assertIn("mi_mode=blend", c[2])
        ramp = vm.retime_chain({"from": 1.0, "to": 0.4}, 1.0, 30, 30, "mci")
        self.assertIn("sqrt(", ramp[1])
        self.assertIn("mi_mode=mci", ramp[2])
        self.assertEqual(vm.retime_chain({"from": 2.0, "to": 2.0}, 1.0, 30, 30)[-1], "fps=30")

    def test_nearly_flat_ramp_is_constant(self):
        c = vm.retime_chain({"from": 0.5, "to": 0.5005}, 1.0, 30, 120)
        self.assertNotIn("sqrt", c[1])

    def test_stab_filters(self):
        p = vm.normalize_stabilize("strong")
        det = vm.stab_detect_filter(p, "C:/w/a.trf")
        self.assertIn("vidstabdetect=shakiness=8", det)
        self.assertIn("C\\:/w/a.trf", det)
        tr = vm.stab_transform_filter(p, "C:/w/a.trf")
        self.assertIn("smoothing=20", tr)
        self.assertIn("optzoom=1", tr)

    def test_validate_shot(self):
        self.assertEqual(vm.validate_shot({}, 0), [])
        codes = lambda s: {i["code"] for i in vm.validate_shot(s, 0)}
        self.assertIn("stabilize", codes({"stabilize": "huge"}))
        self.assertIn("speed", codes({"speed": 9}))
        self.assertIn("speed-mode", codes({"speed": 0.5, "speed_mode": "zzz"}))
        self.assertIn("stabilize-slow", codes({"speed": 0.4, "stabilize": True}))

    def test_smoothness(self):
        self.assertEqual(vm.smoothness([1, 1]), 0.0)
        self.assertEqual(vm.smoothness([0, 0, 0, 0]), 0.0)
        self.assertLess(vm.smoothness([5, 5, 5, 5, 5, 5.1]), vm.smoothness([1, 9, 1, 9, 1, 9]))


def small_edl(shots, duration, transitions=None, style=None):
    return {"version": 1, "project": {"name": "m", "width": 180, "height": 320, "fps": 30, "duration": duration, "music": None},
            "style": dict({"grade": "neutral", "zoom": 0, "drop_flash": False, "end_flare": False}, **(style or {})),
            "transitions": transitions or {}, "shots": shots}


class Lint(unittest.TestCase):
    def test_speed_counts_as_a_visible_effect(self):
        shots = [{"clip": "a.mp4", "span": [0, 2.0], "speed": 0.5}, {"clip": "b.mp4", "span": [2.0, 4.0]}]
        e = small_edl(shots, 4.0, style={"zoom": 0.05, "drop_flash": True, "end_flare": True})
        e["beats"] = {"t0": 0.0, "period": 1.0, "drop_beat": 2}
        codes = {i["code"] for i in ve.lint(e, check_files=False)}
        self.assertIn("effect-budget", codes)                                # zoom + flash + flare + slow motion = 4
        e["style"]["end_flare"] = False
        self.assertNotIn("effect-budget", {i["code"] for i in ve.lint(e, check_files=False)})

    def test_motion_errors_reach_the_linter(self):
        shots = [{"clip": "a.mp4", "span": [0, 2.0], "speed": 99}, {"clip": "b.mp4", "span": [2.0, 4.0], "stabilize": "x"}]
        codes = {i["code"] for i in ve.lint(small_edl(shots, 4.0), check_files=False)}
        self.assertTrue({"speed", "stabilize"} <= codes)


class Windows(unittest.TestCase):
    def pl(self, **kw):
        base = {"n": 30, "fps": 30, "head": 0, "tail": 0, "src_start": None, "speed": None}
        base.update(kw)
        return base

    def test_slow_motion_reads_less_source(self):
        w = vr.shot_window(self.pl(speed={"from": 0.5, "to": 0.5}), {"duration": 20.0})
        self.assertAlmostEqual(w["src_seconds"], 0.5)
        self.assertAlmostEqual(w["out_seconds"], 1.0)

    def test_speed_up_reads_more_source_and_stays_inside_the_clip(self):
        w = vr.shot_window(self.pl(speed={"from": 3.0, "to": 3.0}, src_start=1.0), {"duration": 3.0})
        self.assertAlmostEqual(w["src_seconds"], 3.0)
        self.assertEqual(w["start"], 0.0)                                   # pulled back: 3 s of source from a 3 s clip

    def test_head_pad_is_in_source_time(self):
        w = vr.shot_window(self.pl(head=2, speed={"from": 0.5, "to": 0.5}, src_start=5.0), {"duration": 30.0})
        self.assertAlmostEqual(w["start0"] - w["start"], 2 / 30 * 0.5)


def ramp_clip(path: Path, seconds: float = 6.0, fps: int = 30, size: str = "320x180") -> Path:
    """Every frame is a flat grey whose level equals the clip time: level = 16 + 30 * t. Retiming becomes measurable."""
    vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c=gray:s={size}:r={fps}:d={seconds},geq=lum='16+30*T':cb=128:cr=128", "-c:v", "libx264", "-crf", "10",
            "-pix_fmt", "yuv420p", str(path)])
    return path


def levels(video) -> list[float]:
    return qa.measure_picture(vl.ffp(video), 30.0)["yavg"]


def count_frames(path) -> int:
    r = vl.run([vl.find_ffmpeg()["ffprobe"], "-v", "error", "-count_frames", "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", vl.ffp(path)])
    return int(r.stdout.strip().rstrip(","))


@NEEDS_FF
@helpers.slow
class SpeedRenders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "speed4"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.ramp30 = ramp_clip(cls.root / "ramp30.mp4", 6.0, 30)
        cls.ramp60 = ramp_clip(cls.root / "ramp60.mp4", 6.0, 60)

    def render(self, shots, duration, name, transitions=None):
        e = small_edl(shots, duration, transitions)
        p = self.root / f"{name}.edl.json"
        vl.save_json(p, e)
        res = vr.render(p, out=self.root / f"{name}.mp4", encoder="libx264", workdir=self.root / f"{name}_work", log=lambda *_: None)
        return res

    # The picture level of the test clip equals 16 + 30 * source_time, so the slope of the output level is the playback speed.
    # The project fades in (0.4 s) and out (0.8 s) from black: only the frames in between are measured.
    @staticmethod
    def speed_between(lv, a, b):
        return (lv[b] - lv[a]) / ((b - a) / 30.0) / 30.0

    def test_normal_speed_is_the_control(self):
        res = self.render([{"clip": vl.ffp(self.ramp30), "span": [0, 3.0], "src_start": 1.0}], 3.0, "normal")
        lv = levels(res["out"])
        self.assertEqual(len(lv), 90)
        self.assertAlmostEqual(self.speed_between(lv, 15, 64), 1.0, delta=0.06)

    def test_half_speed_plays_at_half_speed(self):
        for mode in ("blend", "mci"):
            res = self.render([{"clip": vl.ffp(self.ramp30), "span": [0, 3.0], "src_start": 1.0, "speed": 0.5, "speed_mode": mode}],
                              3.0, f"half_{mode}")
            lv = levels(res["out"])
            self.assertEqual(len(lv), 90, mode)
            self.assertAlmostEqual(self.speed_between(lv, 15, 64), 0.5, delta=0.06, msg=mode)

    def test_double_speed_plays_at_double_speed(self):
        res = self.render([{"clip": vl.ffp(self.ramp30), "span": [0, 3.0], "src_start": 0.3, "speed": 1.8}], 3.0, "double")
        lv = levels(res["out"])
        self.assertEqual(len(lv), 90)
        self.assertAlmostEqual(self.speed_between(lv, 15, 64), 1.8, delta=0.1)

    def test_native_slow_motion_uses_real_frames_of_a_60fps_clip(self):
        res = self.render([{"clip": vl.ffp(self.ramp60), "span": [0, 3.0], "src_start": 1.0, "speed": 0.5, "speed_mode": "native"}],
                          3.0, "native")
        lv = levels(res["out"])
        steps = [b - a for a, b in zip(lv[15:65], lv[16:66])]
        self.assertEqual(len(lv), 90)
        self.assertLess(max(steps) - min(steps), 2.5)                                          # evenly spaced: real frames, no stutter
        self.assertAlmostEqual(self.speed_between(lv, 15, 64), 0.5, delta=0.06)

    def test_native_mode_on_a_30fps_clip_repeats_frames(self):
        res = self.render([{"clip": vl.ffp(self.ramp30), "span": [0, 3.0], "src_start": 1.0, "speed": 0.5, "speed_mode": "native"}],
                          3.0, "native30")
        lv = levels(res["out"])
        steps = [b - a for a, b in zip(lv[15:65], lv[16:66])]
        self.assertGreater(max(steps) - min(steps), 0.5)                                       # stutter: frames were duplicated

    def test_auto_mode_picks_native_for_high_fps_sources(self):
        info = vl.probe(self.ramp60)
        self.assertEqual(vm.choose_mode({"from": 0.5, "to": 0.5}, "auto", 2.0, info["fps"], 30), "native")

    def test_speed_ramp_slows_down_progressively(self):
        res = self.render([{"clip": vl.ffp(self.ramp30), "span": [0, 3.0], "src_start": 0.3, "speed": {"from": 1.8, "to": 0.4},
                            "speed_mode": "blend"}], 3.0, "ramp")
        lv = levels(res["out"])
        self.assertEqual(len(lv), 90)
        early = self.speed_between(lv, 15, 30)
        late = self.speed_between(lv, 50, 65)
        # speed(t) = 1.8 - (1.4 / 3) * t: about 1.45 at 0.75 s and 0.9 at 1.9 s
        self.assertAlmostEqual(early, 1.45, delta=0.12)
        self.assertAlmostEqual(late, 0.9, delta=0.12)

    def test_retimed_shot_keeps_the_timeline_exact_with_transitions(self):
        shots = [{"clip": vl.ffp(self.ramp30), "span": [0, 1.0], "src_start": 0.5},
                 {"clip": vl.ffp(self.ramp30), "span": [1.0, 2.5], "src_start": 1.0, "speed": 0.5},
                 {"clip": vl.ffp(self.ramp30), "span": [2.5, 3.5], "src_start": 2.0, "speed": {"from": 1, "to": 0.5}},
                 {"clip": vl.ffp(self.ramp30), "span": [3.5, 4.5], "src_start": 3.0}]
        res = self.render(shots, 4.5, "mixed", transitions={"1": "fade", "2": "slideleft", "3": "circleopen"})
        self.assertEqual(count_frames(res["out"]), 135)

    def test_speed_change_near_the_end_of_a_short_clip_does_not_fail(self):
        res = self.render([{"clip": vl.ffp(self.ramp30), "span": [0, 2.0], "src_start": 5.5, "speed": 2.0}], 2.0, "tail")
        self.assertEqual(count_frames(res["out"]), 60)

    def test_cache_key_changes_with_speed(self):
        a = self.render([{"clip": vl.ffp(self.ramp30), "span": [0, 1.0], "src_start": 1.0}], 1.0, "ck")
        e = small_edl([{"clip": vl.ffp(self.ramp30), "span": [0, 1.0], "src_start": 1.0, "speed": 0.5}], 1.0)
        p = self.root / "ck.edl.json"
        vl.save_json(p, e)
        b = vr.render(p, out=self.root / "ck2.mp4", encoder="libx264", workdir=self.root / "ck_work", log=lambda *_: None)
        self.assertEqual(b["parts_cached"], 0)


@NEEDS_FF
@helpers.requires_filters("vidstabdetect", "vidstabtransform")
@helpers.slow
class Stabilise(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "stab4"
        cls.root.mkdir(parents=True, exist_ok=True)
        # a static, textured picture filmed by a shaking camera (crop window jitters by up to +-25 px)
        cls.shaky = cls.root / "shaky.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i",
                "color=c=gray:s=960x540:r=30:d=3,geq=lum='128+70*sin(X/13)*sin(Y/17)+30*sin((X+Y)/7)':cb=128:cr=128,"
                "crop=w=640:h=360:x='160+25*sin(n*0.9)':y='90+20*cos(n*1.3)'", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p",
                str(cls.shaky)])
        cls.steady = cls.root / "steady.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i",
                "color=c=gray:s=960x540:r=30:d=3,geq=lum='128+70*sin(X/13)*sin(Y/17)+30*sin((X+Y)/7)':cb=128:cr=128,"
                "crop=w=640:h=360:x=160:y=90", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(cls.steady)])

    def motion(self, video):
        d = qa.measure_picture(vl.ffp(video), 30.0)["diff"]
        return statistics.mean(d[3:])

    def render(self, clip, stab, name, extra=None):
        shot = {"clip": vl.ffp(clip), "span": [0, 2.0], "src_start": 0.3}
        if stab is not None:
            shot["stabilize"] = stab
        shot.update(extra or {})
        e = small_edl([shot], 2.0)
        p = self.root / f"{name}.edl.json"
        vl.save_json(p, e)
        return vr.render(p, out=self.root / f"{name}.mp4", encoder="libx264", workdir=self.root / f"{name}_work", log=lambda *_: None), p

    def test_stabilisation_removes_most_of_the_shake(self):
        raw, _ = self.render(self.shaky, None, "raw")
        for preset in ("light", "strong"):
            res, _ = self.render(self.shaky, preset, f"st_{preset}")
            limit = {"light": 0.85, "strong": 0.65}[preset]
            self.assertLess(self.motion(res["out"]), limit * self.motion(raw["out"]), preset)

    def test_a_steady_shot_is_left_alone(self):
        raw, _ = self.render(self.steady, None, "steady_raw")
        st, _ = self.render(self.steady, "light", "steady_st")
        self.assertLess(self.motion(st["out"]), self.motion(raw["out"]) + 1.0)

    def test_trf_files_are_cached_and_reused(self):
        res, p = self.render(self.shaky, "light", "trfcache")
        trfs = list((self.root / "trfcache_work" / "parts" / "main").glob("*.trf"))
        self.assertEqual(len(trfs), 1)
        again = vr.render(p, out=self.root / "trfcache2.mp4", encoder="libx264", workdir=self.root / "trfcache_work", log=lambda *_: None)
        self.assertEqual(again["parts_cached"], 1)

    def test_changing_the_preset_recomputes(self):
        self.render(self.shaky, "light", "recompute")
        res, _ = self.render(self.shaky, "strong", "recompute")
        self.assertEqual(res["parts_cached"], 0)

    def test_custom_parameters(self):
        res, _ = self.render(self.shaky, {"smoothing": 15, "zoom": 3, "shakiness": 6}, "custom")
        self.assertTrue(Path(res["out"]).is_file())

    def test_stabilise_plus_slow_motion_and_reframe_render(self):
        res, _ = self.render(self.shaky, "light", "combo", {"speed": 0.5, "speed_mode": "blend", "reframe": "blur-fill"})
        self.assertEqual(count_frames(res["out"]), 60)

    def test_a_failing_analysis_is_a_clear_error(self):
        bad = self.root / "notvideo.mp4"
        bad.write_bytes(b"x" * 100)
        e = small_edl([{"clip": vl.ffp(bad), "span": [0, 2.0], "stabilize": True}], 2.0)
        p = self.root / "bad.edl.json"
        vl.save_json(p, e)
        with self.assertRaises(vl.EngineError):
            vr.render(p, out=self.root / "bad.mp4", encoder="libx264", workdir=self.root / "bad_work", log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
