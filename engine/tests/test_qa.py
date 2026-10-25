"""Phase 3: the QA gate. Pure-logic tests on synthetic measurements, then real videos built to be defective."""
import copy
import json
import statistics
import unittest
from pathlib import Path

import helpers
import mh_video
import vid_beats
import vid_edl as ve
import vid_plan as vp
import vid_qa as qa
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
FPS = 30.0


def pic(n=300, yavg=110.0, u=121.0, v=132.0, scene=0.02, diff=3.0):
    return {"frames": n, "yavg": [yavg] * n, "ylow": [60.0] * n, "ymax": [220.0] * n, "u": [u] * n, "v": [v] * n, "sat": [30.0] * n,
            "scene": [scene] * n, "diff": [diff] * (n - 1 if False else n)}


def ev(black=((0.0, 0.5),), white=(), cuts=(), transitions=()):
    return {"black": list(black), "white": list(white), "cuts": list(cuts), "transitions": list(transitions), "planned": True}


def lvl(checks, check_id):
    return next(c["level"] for c in checks if c["id"] == check_id)


class PictureChecks(unittest.TestCase):
    def test_clean_picture_passes(self):
        c = qa.check_picture(pic(), ev(), FPS)
        self.assertEqual({x["level"] for x in c} - {"ok", "info"}, set())

    def test_black_frame_mid_video_is_an_error_with_a_timestamp(self):
        p = pic()
        p["yavg"][150] = 2.0
        p["ymax"][150] = 10.0
        c = qa.check_picture(p, ev(), FPS)
        self.assertEqual(lvl(c, "black-frames"), "error")
        self.assertIn("5.0s", next(x["msg"] for x in c if x["id"] == "black-frames"))

    def test_black_inside_the_planned_fade_is_fine(self):
        p = pic()
        for i in range(0, 4):
            p["yavg"][i], p["ymax"][i] = 2.0, 10.0
        self.assertEqual(lvl(qa.check_picture(p, ev(black=[(0.0, 0.5)]), FPS), "black-frames"), "ok")

    def test_dip_to_black_transition_window_is_allowed(self):
        p = pic()
        for i in range(60, 63):
            p["yavg"][i], p["ymax"][i] = 2.0, 10.0
        self.assertEqual(lvl(qa.check_picture(p, ev(black=[(0, 0.5), (1.9, 2.3)]), FPS), "black-frames"), "ok")

    def test_dark_but_not_black_scene_is_not_a_black_frame(self):
        p = pic(yavg=18.0)
        p["ymax"] = [140.0] * p["frames"]               # a night scene with lights in it
        self.assertEqual(lvl(qa.check_picture(p, ev(), FPS), "black-frames"), "ok")

    def test_white_frames_outside_the_flash_are_an_error(self):
        p = pic()
        for i in range(0, 22):
            p["yavg"][i], p["ylow"][i] = 235.0, 235.0    # the v1 bug: 22 white first frames
        c = qa.check_picture(p, ev(), FPS)
        self.assertEqual(lvl(c, "white-frames"), "error")

    def test_without_an_edl_white_frames_are_only_a_warning(self):
        p = pic()
        for i in range(0, 22):
            p["yavg"][i], p["ylow"][i] = 235.0, 235.0
        e = ev()
        e["planned"] = False
        c = qa.check_picture(p, e, FPS)
        self.assertEqual(lvl(c, "white-frames"), "warn")
        self.assertIn("no EDL", next(x["msg"] for x in c if x["id"] == "white-frames"))

    def test_single_white_frame_is_only_a_warning(self):
        p = pic()
        p["yavg"][100], p["ylow"][100] = 235.0, 235.0
        self.assertEqual(lvl(qa.check_picture(p, ev(), FPS), "white-frames"), "warn")

    def test_bright_sky_is_not_a_white_frame(self):
        p = pic(yavg=226.0)                               # bright on average but with dark ground: YLOW stays low
        self.assertEqual(lvl(qa.check_picture(p, ev(), FPS), "white-frames"), "ok")

    def test_planned_flash_is_not_reported(self):
        p = pic()
        for i in range(150, 154):
            p["yavg"][i], p["ylow"][i] = 235.0, 235.0
        c = qa.check_picture(p, ev(white=[(4.8, 5.4)]), FPS)
        self.assertEqual(lvl(c, "white-frames"), "ok")
        self.assertNotIn("flash-missing", [x["id"] for x in c])

    def test_missing_flash_is_noticed(self):
        c = qa.check_picture(pic(), ev(white=[(4.8, 5.4)]), FPS)
        self.assertEqual(lvl(c, "flash-missing"), "warn")

    def test_magenta_cast(self):
        p = pic(u=140.0, v=140.0)                        # the v3 bug
        c = qa.check_picture(p, ev(), FPS)
        self.assertEqual(lvl(c, "magenta-cast"), "error")
        p2 = pic()
        for i in range(60, 90):
            p2["u"][i], p2["v"][i] = 141.0, 141.0
        self.assertEqual(lvl(qa.check_picture(p2, ev(), FPS), "magenta-cast"), "warn")

    def test_green_cast(self):
        self.assertEqual(lvl(qa.check_picture(pic(u=105.0, v=100.0), ev(), FPS), "green-cast"), "error")

    def test_warm_desert_is_not_a_cast(self):
        c = qa.check_picture(pic(u=118.0, v=140.0), ev(), FPS)          # strongly orange: V high, U low
        self.assertEqual(lvl(c, "magenta-cast"), "ok")
        self.assertEqual(lvl(c, "green-cast"), "ok")

    def test_frozen_stretch(self):
        p = pic()
        for i in range(100, 130):
            p["diff"][i] = 0.0
        c = qa.check_picture(p, ev(), FPS)
        self.assertEqual(lvl(c, "frozen"), "warn")
        self.assertIn("3.33s", next(x["msg"] for x in c if x["id"] == "frozen"))

    def test_short_identical_run_is_not_a_freeze(self):
        p = pic()
        for i in range(100, 108):
            p["diff"][i] = 0.0
        self.assertEqual(lvl(qa.check_picture(p, ev(), FPS), "frozen"), "ok")

    def test_runs_helper(self):
        self.assertEqual(qa._runs([False, True, True, False, True]), [(1, 2), (4, 1)])
        self.assertEqual(qa._runs([]), [])


class CutChecks(unittest.TestCase):
    def scene_with_cuts(self, frames, n=300, strength=0.6):
        s = [0.01] * n
        for f in frames:
            s[f] = strength
        return s

    def test_cuts_on_their_frames(self):
        cuts = [(1, 2.0), (2, 4.0), (3, 6.0)]
        p = dict(pic(), scene=self.scene_with_cuts([60, 120, 180]))
        c, stats = qa.check_cuts(p, ev(cuts=cuts), FPS)
        self.assertEqual(lvl(c, "cut-timing"), "ok")
        self.assertEqual(stats["detected"], 3)
        self.assertEqual(stats["median_offset_frames"], 0)

    def test_cuts_late_by_three_frames_are_an_error(self):
        cuts = [(1, 2.0), (2, 4.0), (3, 6.0)]
        p = dict(pic(), scene=self.scene_with_cuts([63, 123, 183]))
        c, stats = qa.check_cuts(p, ev(cuts=cuts), FPS)
        self.assertEqual(lvl(c, "cut-timing"), "error")
        self.assertEqual(stats["median_offset_frames"], 3)

    def test_off_by_one_is_a_warning_not_an_error(self):
        cuts = [(1, 2.0), (2, 4.0), (3, 6.0)]
        p = dict(pic(), scene=self.scene_with_cuts([61, 121, 181]))
        c, _ = qa.check_cuts(p, ev(cuts=cuts), FPS)
        self.assertEqual(lvl(c, "cut-timing"), "warn")

    def test_similar_neighbours_mean_cuts_cannot_be_verified(self):
        cuts = [(1, 2.0), (2, 4.0)]
        c, stats = qa.check_cuts(pic(), ev(cuts=cuts), FPS)           # scene score stays flat
        self.assertEqual(lvl(c, "cut-timing"), "info")
        self.assertEqual(stats["undetected"], 2)

    def test_no_cuts_no_checks(self):
        c, stats = qa.check_cuts(pic(), ev(), FPS)
        self.assertEqual(c, [])
        self.assertEqual(stats["hard_cuts"], 0)


class AudioChecks(unittest.TestCase):
    A = {"lufs": -15.0, "lra": 5.0, "true_peak": -2.5, "max_volume": -3.0, "mean_volume": -18.0, "silences": []}

    def test_good_audio(self):
        c = qa.check_audio(self.A, True, -15.0, 28.0)
        self.assertEqual({x["level"] for x in c}, {"ok"})

    def test_too_loud_and_too_quiet(self):
        self.assertEqual(lvl(qa.check_audio(dict(self.A, lufs=-11.0), True, -15.0, 28.0), "loudness"), "warn")
        self.assertEqual(lvl(qa.check_audio(dict(self.A, lufs=-20.0), True, -15.0, 28.0), "loudness"), "warn")
        self.assertEqual(lvl(qa.check_audio(dict(self.A, lufs=-13.5), True, -15.0, 28.0), "loudness"), "ok")

    def test_true_peak_levels(self):
        self.assertEqual(lvl(qa.check_audio(dict(self.A, true_peak=-0.5), True, -15.0, 28.0), "true-peak"), "warn")
        self.assertEqual(lvl(qa.check_audio(dict(self.A, true_peak=0.2), True, -15.0, 28.0), "true-peak"), "error")

    def test_silence_in_the_middle_only(self):
        edges = dict(self.A, silences=[(0.0, 0.4), (27.5, 28.0)])
        self.assertEqual(lvl(qa.check_audio(edges, True, -15.0, 28.0), "silence"), "ok")
        mid = dict(self.A, silences=[(10.0, 12.0)])
        self.assertEqual(lvl(qa.check_audio(mid, True, -15.0, 28.0), "silence"), "warn")

    def test_missing_audio(self):
        self.assertEqual(qa.check_audio(None, True, -15.0, 28.0)[0]["level"], "error")
        self.assertEqual(qa.check_audio(None, False, -15.0, 28.0)[0]["level"], "info")


class StructureChecks(unittest.TestCase):
    INFO = {"duration": 28.235, "width": 1080, "height": 1920, "fps": 30.0}
    P = {"duration": 28.235, "width": 1080, "height": 1920, "fps": 30}

    def test_ok(self):
        c = qa.check_structure(self.INFO, self.P, 50_000_000)
        self.assertEqual({x["level"] for x in c}, {"ok"})

    def test_file_size_levels(self):
        self.assertEqual(lvl(qa.check_structure(self.INFO, self.P, 500_000_000), "file-size"), "error")
        self.assertEqual(lvl(qa.check_structure(self.INFO, self.P, 200_000_000), "file-size"), "warn")
        self.assertEqual(lvl(qa.check_structure(self.INFO, self.P, 40_000_000), "file-size"), "ok")

    def test_mismatches(self):
        self.assertEqual(lvl(qa.check_structure(dict(self.INFO, duration=25.0), self.P, 1e7), "duration"), "error")
        self.assertEqual(lvl(qa.check_structure(dict(self.INFO, width=1920, height=1080), self.P, 1e7), "aspect"), "error")
        self.assertEqual(lvl(qa.check_structure(dict(self.INFO, fps=25.0), self.P, 1e7), "fps"), "error")

    def test_without_an_edl_only_size_is_checked(self):
        c = qa.check_structure(self.INFO, None, 1e7)
        self.assertEqual([x["id"] for x in c], ["file-size"])


def small_edl(n=8, duration=8.0, transitions=None, beats=True):
    edges = [round(duration * i / n, 4) for i in range(n + 1)]
    e = {"version": 1, "project": {"name": "q", "width": 180, "height": 320, "fps": 30, "duration": duration, "music": None},
         "style": {"grade": "neutral"}, "transitions": transitions if transitions is not None else {"1": "fade", "4": "circleopen"},
         "shots": [{"clip": f"c{i}.mp4", "span": [edges[i], edges[i + 1]]} for i in range(n)]}
    if beats:
        e["beats"] = {"t0": 0.0, "period": 1.0, "drop_beat": 4}
    return e


class Expectations(unittest.TestCase):
    def test_fades_flash_and_cuts(self):
        e = small_edl()
        x = qa.expected_events(e, 30.0, 8.0)
        self.assertEqual(x["black"][0][0], 0.0)
        self.assertGreater(x["black"][1][1], 7.9)
        self.assertTrue(any(abs(a - 3.85) < 0.2 for a, _ in x["white"]))        # the drop flash at beat 4
        self.assertIn(("fadewhite"), [t for _, _, t in x["transitions"]])
        self.assertTrue(all(1 <= j <= 7 for j, _ in x["cuts"]))
        self.assertEqual(len(x["cuts"]) + len(x["transitions"]), 7)

    def test_fadeblack_makes_black_legal(self):
        e = small_edl(transitions={"2": "fadeblack"}, beats=False)
        x = qa.expected_events(e, 30.0, 8.0)
        self.assertTrue(any(a < 2.0 < b for a, b in x["black"][2:]))

    def test_no_edl(self):
        x = qa.expected_events(None, 30.0, 10.0)
        self.assertEqual((x["white"], x["cuts"], x["transitions"]), ([], [], []))


class EditAudit(unittest.TestCase):
    def test_restrained_plan_is_ok(self):
        c, data = qa.audit_edit(small_edl())
        self.assertEqual({x["level"] for x in c} - {"ok", "info"}, set())          # info lines (content summary) are fine
        self.assertEqual(data["shots"], 8)
        self.assertEqual(data["visible_effects"], ["zoom", "drop_flash", "end_flare"])
        self.assertEqual(data["cuts_on_beat_share"], 1.0)

    def test_too_many_transitions_is_reported_through_lint(self):
        e = small_edl(12, 12.0, transitions={str(j): vl.PLANNER_TRANSITIONS[j] for j in range(1, 12)})
        c, _ = qa.audit_edit(e)
        self.assertIn("lint-transition-share", [x["id"] for x in c])

    def test_cuts_off_the_beat(self):
        e = small_edl(beats=True)
        e["beats"] = {"t0": 0.3, "period": 1.0}
        c, data = qa.audit_edit(e)
        self.assertLess(data["cuts_on_beat_share"], 0.9)
        self.assertEqual(lvl(c, "beat-alignment"), "warn")

    def test_scores_flag_weak_and_repeated_clips(self):
        e = small_edl(4, 4.0, transitions={}, beats=False)
        scores = {}
        for i in range(4):
            scores[i] = {"index": i, "path": f"c{i}.mp4", "name": f"c{i}.mp4", "quality": 80.0, "flags": [], "similar_to": None}
        scores[1]["flags"] = ["dark"]
        scores[2]["similar_to"] = 1
        c, data = qa.audit_edit(e, scores)
        ids = [x["id"] for x in c]
        self.assertIn("weak-clips", ids)
        self.assertIn("similar-neighbours", ids)
        self.assertEqual(data["mean_quality_used"], 80.0)


class ExposureJumps(unittest.TestCase):
    def pic_with_levels(self, levels, per=30):
        """A picture whose luma is constant inside each shot (levels[i]); cuts every `per` frames."""
        n = per * len(levels)
        ya = [levels[i // per] for i in range(n)]
        return {"frames": n, "yavg": ya, "ylow": [60.0] * n, "ymax": [220.0] * n, "u": [128.0] * n, "v": [128.0] * n,
                "sat": [10.0] * n, "scene": [0.0] * n, "diff": [3.0] * n}

    def cuts(self, shots, per=30):
        return {"cuts": [(j, j * per / 30.0) for j in range(1, shots)], "black": [], "white": [], "transitions": [], "planned": True}

    def test_even_shots_are_just_info(self):
        pic = self.pic_with_levels([100, 104, 98, 102, 101, 99])
        c = qa.check_exposure_jumps(pic, self.cuts(6), 30.0)
        self.assertEqual(c[0]["id"], "exposure-jumps")
        self.assertEqual(c[0]["level"], "info")

    def test_flickering_shots_warn_and_name_the_worst_cuts(self):
        pic = self.pic_with_levels([40, 180, 45, 190, 50, 185, 42, 175])
        c = qa.check_exposure_jumps(pic, self.cuts(8), 30.0)
        self.assertEqual(c[0]["level"], "warn")
        self.assertEqual(len(c[0]["where"]), 3)
        self.assertIn("color plan", c[0]["msg"])

    def test_a_few_big_jumps_are_tolerated(self):
        pic = self.pic_with_levels([100, 102, 104, 40, 100, 102, 98, 101, 100, 99])
        self.assertEqual(qa.check_exposure_jumps(pic, self.cuts(10), 30.0)[0]["level"], "info")

    def test_too_few_cuts_gives_nothing(self):
        self.assertEqual(qa.check_exposure_jumps(self.pic_with_levels([100, 200]), self.cuts(2), 30.0), [])


class ContentAudit(unittest.TestCase):
    def edl(self, **kw):
        e = small_edl(6, 6.0, transitions={})
        for k, v in kw.items():
            if k == "style":
                e["style"].update(v)
            else:
                e[k] = v
        return e

    def test_content_line_lists_what_is_used(self):
        e = self.edl(style={"grade": {"look": "desert-gold"}, "sound_design": True, "ambient": True},
                     texts=[{"text": "x", "start": 0, "end": 2}])
        e["shots"][1]["speed"] = 0.5
        e["shots"][2]["stabilize"] = True
        e["shots"][3]["correct"] = {"ev": 0.1}
        c, data = qa.audit_edit(e)
        msg = next(x["msg"] for x in c if x["id"] == "content")
        for part in ("desert-gold", "1 corrected shot", "1 speed-changed", "1 stabilised", "1 text", "sound design", "ambient"):
            self.assertIn(part, msg)
        self.assertEqual((data["speed_shots"], data["stabilised_shots"], data["texts"]), (1, 1, 1))

    def test_too_many_looks_warn(self):
        e = self.edl()
        e["shots"][0]["grade"] = "desert-gold"
        e["shots"][1]["grade"] = "teal-orange"
        e["shots"][2]["grade"] = "bleach-bypass"
        c, data = qa.audit_edit(e)
        self.assertIn("look-consistency", [x["id"] for x in c])
        self.assertGreaterEqual(data["distinct_looks"], 3)

    def test_one_look_everywhere_is_fine(self):
        c, _ = qa.audit_edit(self.edl(style={"grade": "desert-gold"}))
        self.assertNotIn("look-consistency", [x["id"] for x in c])


# --------------------------------------------------------------------------- real videos

def count_frames(path) -> int:
    r = vl.run([vl.find_ffmpeg()["ffprobe"], "-v", "error", "-count_frames", "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", vl.ffp(path)])
    return int(r.stdout.strip().rstrip(","))


@NEEDS_FF
@helpers.slow
class RealVideos(unittest.TestCase):
    """A real engine render must PASS; the same video with a defect injected must fail on exactly that defect."""

    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "qa"
        cls.root.mkdir(parents=True, exist_ok=True)
        footage = cls.root / "footage"
        clips = helpers.make_clips(footage, 4, seconds=5.0)
        cls.music = cls.root / "music.wav"
        helpers.click_track(cls.music, bpm=120.0, first=0.5, seconds=10.0, step_at=5.0)
        beats = vid_beats.analyse(cls.music)
        body = vp.body_start_time(beats, beats["duration"], beats["period"], 2)
        need = vp.layout(beats["duration"], body, beats["period"], 2)["shots"]
        cls.edl = vp.plan([{"clip": vl.ffp(clips[i % 4])} for i in range(need)], beats, name="q", music=vl.ffp(cls.music),
                          duration=beats["duration"])
        cls.edl["project"].update(width=270, height=480)
        cls.edl_path = cls.root / "q.edl.json"
        vl.save_json(cls.edl_path, cls.edl)
        cls.res = vr.render(cls.edl_path, out=cls.root / "good.mp4", encoder="libx264", log=lambda *_: None)
        cls.good = Path(cls.res["out"])
        cls.report = qa.run_qa(cls.good, cls.edl_path)

    def defect(self, name, vf=None, af=None, extra=None):
        out = self.root / f"{name}.mp4"
        cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", vl.ffp(self.good)]
        if vf:
            cmd += ["-vf", vf, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]
        else:
            cmd += ["-c:v", "copy"]
        if af:
            cmd += ["-af", af, "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-c:a", "copy"]
        vl.run(cmd + (extra or []) + [vl.ffp(out)])
        return qa.run_qa(out, self.edl_path)

    def levels(self, rep):
        return {c["id"]: c["level"] for c in rep["checks"]}

    def test_a_good_engine_render_passes(self):
        bad = [c for c in self.report["checks"] if c["level"] in ("warn", "error")]
        self.assertEqual(bad, [], qa.format_report(self.report))
        self.assertEqual(self.report["verdict"], "pass")
        self.assertEqual(self.report["frames"], vl.frames(self.edl["project"]["duration"], 30))

    def test_hard_cuts_are_confirmed_on_their_frames(self):
        self.assertGreater(self.report["cuts"]["detected"], 0)
        self.assertEqual(self.report["cuts"]["median_offset_frames"], 0)

    def test_audio_is_mastered_to_target(self):
        a = self.report["audio"]
        self.assertAlmostEqual(a["lufs"], -14.0, delta=1.0)
        self.assertLess(a["true_peak"], -1.0)

    def test_white_first_frames_the_v1_bug(self):
        rep = self.defect("white_start", vf="drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(n,0,21)'")
        self.assertEqual(self.levels(rep)["white-frames"], "error")
        self.assertEqual(rep["verdict"], "fail")

    def test_black_frames_in_the_middle(self):
        rep = self.defect("black_mid", vf="drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(n,150,152)'")
        self.assertEqual(self.levels(rep)["black-frames"], "error")

    def test_purple_cast_the_v3_bug(self):
        rep = self.defect("purple", vf="colorbalance=rs=0.4:bs=0.4:rm=0.4:bm=0.4:rh=0.4:bh=0.4")
        lv = self.levels(rep)
        self.assertIn(lv["magenta-cast"], ("warn", "error"))

    def test_frozen_picture(self):
        rep = self.defect("frozen", vf="loop=loop=40:size=1:start=100")
        self.assertEqual(self.levels(rep)["frozen"], "warn")

    def test_cuts_shifted_against_the_edl(self):
        shifted = copy.deepcopy(self.edl)
        d = 0.1                                    # 3 frames
        for i, s in enumerate(shifted["shots"]):
            a, b = s["span"]
            s["span"] = [0.0 if i == 0 else a + d, shifted["project"]["duration"] if i == len(shifted["shots"]) - 1 else b + d]
        p = self.root / "shifted.edl.json"
        vl.save_json(p, shifted)
        rep = qa.run_qa(self.good, p)
        self.assertEqual(self.levels(rep)["cut-timing"], "error")

    def test_audio_too_hot_clips(self):
        rep = self.defect("hot", af="volume=14dB,alimiter=limit=1:level=disabled")
        lv = self.levels(rep)
        self.assertIn(lv["true-peak"], ("warn", "error"))
        self.assertEqual(lv["loudness"], "warn")

    def test_silent_gap(self):
        rep = self.defect("gap", af="volume=0:enable='between(t,3,5)'")
        self.assertEqual(self.levels(rep)["silence"], "warn")

    def test_missing_audio_when_the_edl_has_music(self):
        out = self.root / "noaudio.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", vl.ffp(self.good), "-an", "-c:v", "copy", vl.ffp(out)])
        rep = qa.run_qa(out, self.edl_path)
        self.assertEqual(self.levels(rep)["audio"], "error")

    def test_wrong_duration(self):
        out = self.root / "trimmed.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", vl.ffp(self.good), "-t", "8", "-c", "copy", vl.ffp(out)])
        rep = qa.run_qa(out, self.edl_path)
        lv = self.levels(rep)
        self.assertEqual(lv["duration"], "error")
        self.assertEqual(lv["frame-count"], "error")

    def test_generic_qa_without_an_edl(self):
        rep = qa.run_qa(self.good)
        self.assertNotEqual(rep["verdict"], "fail")          # the planned flash shows up as a warning: no EDL to excuse it
        self.assertNotIn("restraint", self.levels(rep))
        self.assertNotIn("cut-timing", self.levels(rep))

    def test_report_round_trips_as_json(self):
        p = self.root / "report.json"
        qa.save_report(self.report, p)
        again = vl.load_json(p)
        self.assertEqual(again["verdict"], self.report["verdict"])
        text = qa.format_report(self.report)
        self.assertIn("QA PASS", text)
        self.assertIn("OPEN IT", qa.format_report(dict(self.report, sheet="x.jpg")))

    def test_cli_exit_codes(self):
        self.assertEqual(mh_video.main(["qa", str(self.good), "--edl", str(self.edl_path)]), 0)
        out = self.root / "cli_white.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", vl.ffp(self.good), "-vf",
                "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(n,0,21)'", "-c:v", "libx264", "-crf", "18",
                "-c:a", "copy", vl.ffp(out)])
        self.assertEqual(mh_video.main(["qa", str(out), "--edl", str(self.edl_path)]), 1)
        self.assertTrue(out.with_suffix(".qa.json").is_file())

    def test_strict_mode_fails_on_warnings(self):
        out = self.root / "strict_gap.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", vl.ffp(self.good), "-af", "volume=0:enable='between(t,3,5)'",
                "-c:v", "copy", "-c:a", "aac", vl.ffp(out)])
        self.assertEqual(mh_video.main(["qa", str(out), "--edl", str(self.edl_path)]), 0)
        self.assertEqual(mh_video.main(["qa", str(out), "--edl", str(self.edl_path), "--strict"]), 1)

    def test_render_with_qa_flag(self):
        res = vr.render(self.edl_path, out=self.root / "withqa.mp4", encoder="libx264", qa=True, log=lambda *_: None)
        self.assertEqual(res["qa"], "pass")
        self.assertTrue((self.root / "withqa.qa.json").is_file())
        self.assertTrue((self.root / "withqa_check.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
