"""Phase 4: synthesised sound design (whooshes, ducking) and the natural sound of the clips (ambient bed)."""
import copy
import re
import subprocess
import unittest
from pathlib import Path

import helpers
import mh_video
import vid_edl as ve
import vid_qa as qa
import vid_render as vr
import vid_sfx as sfx
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class Mapping(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(sfx.whoosh_kind("slideleft"), "rl")
        self.assertEqual(sfx.whoosh_kind("coverright"), "lr")
        self.assertEqual(sfx.whoosh_kind("slideup"), "up")
        self.assertEqual(sfx.whoosh_kind("vertclose"), "down")
        self.assertEqual(sfx.whoosh_kind("circleopen"), "swell")
        self.assertEqual(sfx.whoosh_kind("pixelize"), "swish")
        for soft in ("fade", "dissolve", "fadegrays", "fadewhite", "fadefast", "nonsense"):
            self.assertIsNone(sfx.whoosh_kind(soft), soft)

    def test_every_planner_transition_is_classified_or_soft(self):
        import vidlib
        for name in vidlib.PLANNER_TRANSITIONS:
            self.assertTrue(sfx.whoosh_kind(name) is not None or name in sfx.SILENT or name in {"fade", "fadegrays", "dissolve"}, name)

    def test_events_follow_the_transitions(self):
        trans = {1: "fade", 3: "slideleft", 5: "circleopen", 8: "fadewhite"}
        bounds = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270]
        ev = sfx.whoosh_events(trans, bounds, 30.0, sfx.DEFAULT_SOUND_DESIGN)
        self.assertEqual([(e["j"], e["kind"]) for e in ev], [(3, "rl"), (5, "swell")])
        self.assertAlmostEqual(ev[0]["t"], 3.0)

    def test_event_cap_keeps_an_even_spread(self):
        trans = {j: "slideleft" for j in range(1, 41)}
        bounds = [j * 30 for j in range(42)]
        cfg = dict(sfx.DEFAULT_SOUND_DESIGN, max_events=8)
        ev = sfx.whoosh_events(trans, bounds, 30.0, cfg)
        self.assertEqual(len(ev), 8)
        gaps = [b["j"] - a["j"] for a, b in zip(ev, ev[1:])]
        self.assertLessEqual(max(gaps) - min(gaps), 1)
        self.assertLess(ev[0]["j"], 6)
        self.assertGreater(ev[-1]["j"], 33)

    def test_config_forms(self):
        self.assertIsNone(sfx.sound_design({"style": {}}))
        self.assertIsNone(sfx.sound_design({"style": {"sound_design": False}}))
        cfg = sfx.sound_design({"style": {"sound_design": True}})
        self.assertEqual(cfg, sfx.DEFAULT_SOUND_DESIGN)
        self.assertEqual(sfx.sound_design({"style": {"sound_design": {"level_db": -30}}})["level_db"], -30)
        self.assertIsNone(sfx.ambient_config({"style": {}}))
        self.assertEqual(sfx.ambient_config({"style": {"ambient": True}})["fade_ms"], 40)

    def test_validation(self):
        codes = lambda style, **kw: {i["code"] for i in sfx.validate({"style": style, "shots": [kw] if kw else []})}
        self.assertEqual(codes({"sound_design": True, "ambient": True}), set())
        self.assertIn("sound-design", codes({"sound_design": {"level_db": 5}}))
        self.assertIn("sound-design", codes({"sound_design": {"max_events": 0}}))
        self.assertIn("sound-design", codes({"sound_design": "loud"}))
        self.assertIn("ambient", codes({"ambient": {"level_db": 10}}))
        self.assertIn("ambient", codes({"ambient": {"shots": "some"}}))
        self.assertIn("ambient", codes({"ambient": {"fade_ms": 9000}}))
        self.assertIn("shot-audio", codes({}, audio={"gain_db": 99}))
        self.assertIn("shot-audio", codes({}, audio="loud"))
        self.assertEqual(codes({}, audio={"use": False, "gain_db": 3}), set())

    def test_linter_reaches_audio_rules(self):
        edl = {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": 4.0}, "style": {"ambient": {"level_db": 10}},
               "transitions": {}, "shots": [{"clip": "a.mp4", "span": [0, 4.0]}]}
        self.assertIn("ambient", {i["code"] for i in ve.lint(edl, check_files=False)})


def rms_db(path, start, dur, channel=None, band=None) -> float:
    """Mean level (dB) of a window, optionally one channel and a frequency band."""
    chain = []
    if channel is not None:
        chain.append(f"pan=mono|c0=c{channel}")
    if band:
        lo, hi = band
        chain.append(f"highpass=f={lo},lowpass=f={hi}")
    chain.append("volumedetect")
    r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-hide_banner", "-nostats", "-v", "info", "-ss", f"{start}", "-t", f"{dur}", "-i", vl.ffp(path),
                        "-af", ",".join(chain), "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else -91.0


@NEEDS_FF
class Synthesis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "sfx"
        cls.root.mkdir(parents=True, exist_ok=True)

    def bus(self, kind, name):
        lines, label = sfx.whoosh_graph([{"t": 0.6, "kind": kind, "name": "x", "j": 1}], -6.0)
        out = self.root / f"{name}.wav"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-filter_complex", ";".join(lines), "-map", f"[{label}]", "-t", "1.6",
                "-c:a", "pcm_s16le", str(out)])
        return out

    def test_every_kind_produces_stereo_sound_at_the_right_time(self):
        for kind in ("rl", "lr", "up", "down", "swell", "swish"):
            wav = self.bus(kind, "k_" + kind)
            info = vl.probe(wav)
            self.assertEqual(info["channels"], 2, kind)
            before = rms_db(wav, 0.0, 0.3)
            during = rms_db(wav, 0.5, 0.2)
            self.assertLess(before, -60, kind)                                    # silent until its slot
            self.assertGreater(during, before + 25, kind)

    def test_pan_follows_the_picture_direction(self):
        rl = self.bus("rl", "pan_rl")                                             # slide to the left: sweeps right -> left
        e1, e2 = (rms_db(rl, 0.41, 0.09, c) for c in (0, 1)), (rms_db(rl, 0.70, 0.09, c) for c in (0, 1))
        (l_early, r_early), (l_late, r_late) = tuple(e1), tuple(e2)
        self.assertGreater(r_early, l_early)
        self.assertGreater(l_late, r_late)
        lr = self.bus("lr", "pan_lr")
        (l1, r1), (l2, r2) = [tuple(rms_db(lr, s, 0.09, c) for c in (0, 1)) for s in (0.41, 0.70)]
        self.assertGreater(l1, r1)
        self.assertGreater(r2, l2)

    def test_centred_kinds_are_balanced(self):
        wav = self.bus("swell", "bal")
        l, r = rms_db(wav, 0.5, 0.2, 0), rms_db(wav, 0.5, 0.2, 1)
        self.assertAlmostEqual(l, r, delta=0.5)

    def test_up_is_brighter_than_down(self):
        up, down = self.bus("up", "up"), self.bus("down", "down")
        hi_up, hi_down = rms_db(up, 0.45, 0.3, None, (4000, 9000)), rms_db(down, 0.45, 0.3, None, (4000, 9000))
        self.assertGreater(hi_up, hi_down + 6)

    def test_level_is_applied(self):
        loud = sfx.whoosh_graph([{"t": 0.3, "kind": "swell", "name": "x", "j": 1}], -6.0)
        quiet = sfx.whoosh_graph([{"t": 0.3, "kind": "swell", "name": "x", "j": 1}], -26.0)
        out = []
        for lines_label, name in ((loud, "lv_loud"), (quiet, "lv_quiet")):
            lines, label = lines_label
            p = self.root / f"{name}.wav"
            vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-filter_complex", ";".join(lines), "-map", f"[{label}]", "-t", "1.0",
                    "-c:a", "pcm_s16le", str(p)])
            out.append(rms_db(p, 0.15, 0.3))
        self.assertAlmostEqual(out[0] - out[1], 20.0, delta=2.0)

    def test_several_events_are_mixed_on_one_bus(self):
        ev = [{"t": 0.5, "kind": "rl", "name": "a", "j": 1}, {"t": 1.5, "kind": "swish", "name": "b", "j": 2}, {"t": 2.5, "kind": "up", "name": "c", "j": 3}]
        lines, label = sfx.whoosh_graph(ev, -12.0)
        self.assertEqual(label, "sfxbus")
        wav = self.root / "multi.wav"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-filter_complex", ";".join(lines), "-map", f"[{label}]", "-t", "3.2",
                "-c:a", "pcm_s16le", str(wav)])
        for t in (0.5, 1.5, 2.5):
            self.assertGreater(rms_db(wav, t - 0.08, 0.16), -50, t)
        self.assertLess(rms_db(wav, 1.0, 0.2), -60)

    def test_no_events_no_graph(self):
        self.assertEqual(sfx.whoosh_graph([], -12.0), ([], None))


def tone_clip(path: Path, hz: int, level: float, seconds: float = 4.0, with_audio: bool = True) -> Path:
    cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=180x320:r=30:d={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f={hz}:d={seconds},volume={level}dB", "-c:a", "aac", "-b:a", "128k", "-shortest"]
    vl.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    return path


@NEEDS_FF
class Ambient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "ambient"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.loud = tone_clip(cls.root / "loud.mp4", 1000, -6)
        cls.quiet = tone_clip(cls.root / "quiet.mp4", 2500, -16)
        cls.mute = tone_clip(cls.root / "mute.mp4", 1000, 0, with_audio=False)
        cls.music = cls.root / "m.wav"
        helpers.click_track(cls.music, bpm=120, first=0.5, seconds=8.0, step_at=None)
        cls.music_lo = cls.root / "lo.wav"                                   # a bare 80 Hz tone: nothing in the bands the ambient tones use
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=80:d=8", "-c:a", "pcm_s16le", str(cls.music_lo)])

    def windows(self, specs):
        out = []
        for j, (clip, speed) in enumerate(specs):
            info = vl.probe(clip)
            out.append({"j": j, "clip": vl.ffp(clip), "start0": 0.5, "seconds": 1.0, "out_start": j * 1.0, "out_seconds": 1.0,
                        "speed": speed, "has_audio": bool(info.get("has_audio"))})
        return out

    def edl(self, ambient=None, shot_extras=None, clips=None, music=None, **style):
        clips = clips or [self.loud, self.quiet, self.loud, self.quiet]
        shots = [{"clip": vl.ffp(c), "span": [i * 1.0, (i + 1) * 1.0], "src_start": 0.5} for i, c in enumerate(clips)]
        for i, extra in (shot_extras or {}).items():
            shots[i].update(extra)
        st = dict({"grade": "neutral", "zoom": 0, "drop_flash": False, "end_flare": False, "riser": False, "boom": False}, **style)
        if ambient is not None:
            st["ambient"] = ambient
        return {"version": 1, "project": {"name": "amb", "width": 180, "height": 320, "fps": 30, "duration": float(len(clips)),
                                         "music": vl.ffp(music or self.music)}, "style": st, "transitions": {}, "shots": shots}

    def test_mean_volume(self):
        loud = sfx.mean_volume(vl.ffp(self.loud), 0.5, 1.0)
        quiet = sfx.mean_volume(vl.ffp(self.quiet), 0.5, 1.0)
        self.assertGreater(loud, quiet + 8)
        self.assertIsNone(sfx.mean_volume(vl.ffp(self.mute), 0.5, 1.0))

    def test_gains_level_every_shot_to_the_same_bed(self):
        e = self.edl({"level_db": -14})
        segs = sfx.ambient_segments(e, self.windows([(self.loud, None), (self.quiet, None)]), -20.0)
        self.assertEqual(len(segs), 2)
        levels = [sfx.mean_volume(vl.ffp(self.loud), 0.5, 1.0) + segs[0]["gain_db"], sfx.mean_volume(vl.ffp(self.quiet), 0.5, 1.0) + segs[1]["gain_db"]]
        for lv in levels:
            self.assertAlmostEqual(lv, -34.0, delta=3.0)                              # music mean -20 + level -14
        self.assertLess(segs[0]["gain_db"], segs[1]["gain_db"])                       # the loud clip is turned down, the quiet one up

    def test_gain_is_limited(self):
        e = self.edl({"level_db": -14})
        segs = sfx.ambient_segments(e, self.windows([(self.quiet, None)]), 10.0)      # absurdly loud music reference
        self.assertLessEqual(segs[0]["gain_db"], 18.0 + 1e-9)

    def test_opt_outs_and_silent_sources(self):
        e = self.edl({"level_db": -14}, shot_extras={1: {"audio": {"use": False}}, 2: {"audio": {"gain_db": 4}}})
        segs = sfx.ambient_segments(e, self.windows([(self.loud, None), (self.quiet, None), (self.loud, None), (self.mute, None)]), -20.0)
        self.assertEqual([s["j"] for s in segs], [0, 2])                              # shot 1 opted out, shot 3 has no audio track
        base = sfx.ambient_segments(self.edl({"level_db": -14}), self.windows([(self.loud, None)] * 3), -20.0)
        self.assertAlmostEqual(segs[1]["gain_db"] - base[0]["gain_db"], 4.0, delta=0.2)

    def test_retimed_shots_are_muted_unless_they_follow(self):
        e = self.edl({"level_db": -14}, shot_extras={1: {"audio": {"speed_follow": True}}})
        w = self.windows([(self.loud, None), (self.loud, {"from": 0.5, "to": 0.5}), (self.loud, {"from": 0.5, "to": 0.5})])
        segs = sfx.ambient_segments(e, w, -20.0)
        self.assertEqual([s["j"] for s in segs], [0, 1])
        self.assertIsNone(segs[0]["tempo"])
        self.assertAlmostEqual(segs[1]["tempo"], 0.5)

    def test_subset_of_shots(self):
        e = self.edl({"level_db": -14, "shots": [0, 3]})
        segs = sfx.ambient_segments(e, self.windows([(self.loud, None)] * 4), -20.0)
        self.assertEqual([s["j"] for s in segs], [0, 3])

    def test_off_means_no_segments(self):
        self.assertEqual(sfx.ambient_segments(self.edl(), self.windows([(self.loud, None)]), -20.0), [])

    def test_lines_trim_fade_delay(self):
        segs = [{"j": 0, "clip": "a", "start": 0.5, "seconds": 1.0, "delay": 0.0, "out_seconds": 1.0, "gain_db": -3.0, "tempo": None},
                {"j": 1, "clip": "b", "start": 0.5, "seconds": 1.0, "delay": 1.0, "out_seconds": 1.0, "gain_db": 2.0, "tempo": 0.5}]
        lines, label = sfx.ambient_lines(segs, 1, sfx.DEFAULT_AMBIENT)
        self.assertEqual(label, "ambbus")
        self.assertIn("[1:a]", lines[0])
        self.assertIn("[2:a]", lines[1])
        self.assertIn("adelay=1000|1000", lines[1])
        self.assertIn("atempo=0.5000", lines[1])
        self.assertIn("afade=t=out:st=0.960", lines[0])
        self.assertIn("highpass=f=90", lines[0])
        self.assertEqual(sfx.ambient_lines([], 1, sfx.DEFAULT_AMBIENT), ([], None))

    def render_master(self, e, name):
        work = self.root / name
        work.mkdir(exist_ok=True)
        return vr.master_audio(e, work, log=lambda *_: None)

    def test_ambient_is_audible_in_the_master_at_the_right_times(self):
        e = self.edl({"level_db": -10}, clips=[self.loud, self.quiet, self.loud, self.quiet], music=self.music_lo)
        plain = self.render_master(self.edl(None, music=self.music_lo), "plain")
        amb = self.render_master(e, "amb")
        # shot 0 plays the 1000 Hz clip, shot 1 the 2500 Hz clip; the 80 Hz music has no energy in those bands
        for t, band in ((0.2, (950, 1050)), (1.2, (2450, 2550))):
            self.assertGreater(rms_db(amb, t, 0.6, None, band), rms_db(plain, t, 0.6, None, band) + 25, band)
        self.assertLess(rms_db(amb, 0.2, 0.6, None, (2450, 2550)), rms_db(amb, 1.2, 0.6, None, (2450, 2550)) - 8)    # not before its shot (the loud 1 kHz clip leaks a little)
        self.assertLess(rms_db(amb, 1.2, 0.6, None, (950, 1050)), rms_db(amb, 0.2, 0.6, None, (950, 1050)) - 8)     # and not after it

    def test_master_keeps_the_loudness_target_with_everything_on(self):
        e = self.edl({"level_db": -10}, sound_design={"whoosh": True})
        e["transitions"] = {"1": "slideleft", "2": "circleopen"}
        wav = self.render_master(e, "everything")
        a = qa.measure_audio(wav)
        self.assertAlmostEqual(a["lufs"], -14.0, delta=1.6)                           # the linear pass may stop short at the -2 dBTP ceiling
        self.assertLess(a["true_peak"], -1.0)

    def mix_only(self, e, name):
        """The pre-master mix (music + sfx), without loudness normalisation: a sparse click track has such a high crest factor
        that the master's limiter would flatten the very kicks this test compares."""
        bounds = ve.timeline(e)
        trans = ve.effective_transitions(e, bounds)
        plan = vr.audio_plan(e, bounds, trans, self.root, log=lambda *_: None)
        out = self.root / f"{name}.wav"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", vl.ffp(self.music)] + plan["inputs"] +
               ["-filter_complex", ";".join(vr.audio_lines(e, 0, plan)), "-map", "[mix]", "-t", "4", "-c:a", "pcm_f32le", str(out)])
        return out

    def test_ducking_lowers_the_music_under_a_whoosh(self):
        def kick_dip(duck, level):
            e = self.edl(None, sound_design={"whoosh": True, "duck": duck, "level_db": level})
            e["transitions"] = {"2": "slideleft"}                                      # the only whoosh: centred at 2.0 s, on a kick
            wav = self.mix_only(e, f"duck_{duck}_{level}")
            kick = lambda t: rms_db(wav, t, 0.1, None, (30, 120))
            return kick(2.0) - (kick(1.5) + kick(2.5)) / 2                            # the kick under the whoosh vs the kicks around it
        for level in (-24, -8):                                                        # the threshold follows the whoosh level
            self.assertLess(kick_dip(True, level), -2.0, level)                       # about 4 dB of ducking
            self.assertGreater(kick_dip(False, level), -0.8, level)                   # none without it

    def test_audio_plan_description_is_stable(self):
        e = self.edl({"level_db": -12}, sound_design={"whoosh": True})
        e["transitions"] = {"1": "slideleft"}
        bounds = ve.timeline(e)
        trans = ve.effective_transitions(e, bounds)
        a = vr.audio_plan(e, bounds, trans, self.root, log=lambda *_: None)
        b = vr.audio_plan(e, bounds, trans, self.root, log=lambda *_: None)
        self.assertEqual(a["key"], b["key"])
        self.assertTrue(a["inputs"])
        self.assertIn("whoosh", a["key"])
        self.assertIn("ambient", a["key"])

    def test_master_is_cached_and_changes_with_settings(self):
        e = self.edl({"level_db": -12})
        work = self.root / "cachem"
        work.mkdir(exist_ok=True)
        m1 = vr.master_audio(e, work, log=lambda *_: None)
        t = m1.stat().st_mtime_ns
        m2 = vr.master_audio(e, work, log=lambda *_: None)
        self.assertEqual((m1, m2.stat().st_mtime_ns), (m2, t))
        e2 = copy.deepcopy(e)
        e2["style"]["ambient"]["level_db"] = -8
        self.assertNotEqual(vr.master_audio(e2, work, log=lambda *_: None), m1)

    def test_full_render_with_ambient_and_whooshes_passes_qa(self):
        e = self.edl({"level_db": -12}, sound_design={"whoosh": True})
        e["transitions"] = {"1": "slideleft", "3": "circleopen"}
        p = self.root / "full.edl.json"
        vl.save_json(p, e)
        res = vr.render(p, out=self.root / "full.mp4", encoder="libx264", workdir=self.root / "full_work", qa=True, log=lambda *_: None)
        self.assertNotEqual(res["qa"], "fail")
        rep = vl.load_json(self.root / "full.qa.json")
        content = next(c for c in rep["checks"] if c["id"] == "content")
        self.assertIn("sound design", content["msg"])
        self.assertIn("ambient sound", content["msg"])

    def test_cli_sfx_plan(self):
        e = self.edl({"level_db": -12}, sound_design={"whoosh": True})
        e["transitions"] = {"1": "slideleft"}
        p = self.root / "cli.edl.json"
        vl.save_json(p, e)
        self.assertEqual(mh_video.main(["sfx", "plan", str(p)]), 0)

    def test_clip_without_any_audio_stream_never_breaks_the_render(self):
        e = self.edl({"level_db": -12}, clips=[self.mute, self.mute])
        wav = self.render_master(e, "allmute")
        self.assertTrue(wav.is_file())


if __name__ == "__main__":
    unittest.main()
