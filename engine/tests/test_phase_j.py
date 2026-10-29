"""Phase J (0.26): the sound. What a clip's sound is (vid_audio), ambient sound without wind or clipping, the song's own ending
(vid_musicfit), the beat analysis time scale, and the listening page with its calibration."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers
import vid_audio as va
import vid_beats as vb
import vid_edl as ve
import vid_listen as vli
import vid_musicfit as mf
import vid_render as vr
import vid_sfx as sfx
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
TOOLS = Path(__file__).resolve().parents[1] / "tools"


def sound(path: Path, graph: str, seconds: float = 4.0, inputs: int = 1):
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    srcs = graph.split("|")
    cmd = [vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y"]
    for s in srcs[:-1]:
        cmd += ["-f", "lavfi", "-i", s]
    cmd += ["-filter_complex", srcs[-1], vl.ffp(path)] if len(srcs) > 1 else ["-f", "lavfi", "-i", srcs[0], vl.ffp(path)]
    vl.run(cmd)
    return path


class Pure(unittest.TestCase):
    def test_syllables_swing_and_steady_sound_does_not(self):
        fps = 50
        talk = [-30 + 12 * ((k // 6) % 2) for k in range(150)]                       # loudness up and down about 4 times a second
        steady = [-30 + 0.3 * ((k * 7) % 3) for k in range(150)]
        share, depth = va.syllables(talk, fps)
        self.assertGreater(share, 0.5)
        self.assertGreater(depth, 0.3)
        self.assertLess(va.syllables(steady, fps)[1], 0.05)
        self.assertEqual(va.syllables(talk[:20], fps), (0.0, 0.0), "under a second: no rhythm")

    def test_periodicity_finds_a_hum_and_not_noise(self):
        import math
        import random
        hum = [math.sin(2 * math.pi * 60 * k / 1000) + 0.5 * math.sin(2 * math.pi * 120 * k / 1000) for k in range(2000)]
        rng = random.Random(1)
        noise = [rng.gauss(0, 1) for _ in range(2000)]
        self.assertGreater(va.periodicity(hum), 0.9)
        self.assertLess(va.periodicity(noise), 0.3)

    def test_labels_and_ambience(self):
        self.assertEqual(va.label({"level_db": -70}), "quiet")
        wind = {"level_db": -25, "low_db": -1.5, "voice_db": -14, "syllables": 0.1, "depth": 0.04, "periodic": 0.06, "clipped": 0}
        self.assertEqual(va.label(wind), "wind")
        self.assertEqual(va.good_ambience(wind), (False, "wind"))
        self.assertEqual(va.label(dict(wind, periodic=0.95)), "rumble", "a hum is an engine, kept under the music")
        talk = dict(wind, low_db=-30, voice_db=-1, syllables=0.9, depth=0.5)
        self.assertEqual(va.good_ambience(talk), (True, "voices"))
        self.assertEqual(va.good_ambience(dict(talk, clipped=0.2)), (False, "clipped"))
        self.assertEqual(va.label(wind, {"wind_low_db": 0.0}), "rumble", "thresholds can be overridden")


@NEEDS_FF
class Measure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = helpers.tmpdir() / "phase_j_sounds"
        cls.wind = sound(d / "wind.wav", "anoisesrc=d=4:c=brown:r=44100:a=0.5,lowpass=f=200")
        cls.talk = sound(d / "talk.wav", "anoisesrc=d=4:c=white:r=44100:a=0.3,highpass=f=300,lowpass=f=3000,"
                                         "volume='0.15+0.85*pow(abs(sin(2*PI*2.2*t)),2)':eval=frame")
        cls.engine = sound(d / "engine.wav", "sine=f=55:d=4:r=44100|sine=f=110:d=4:r=44100|[0:a][1:a]amix=inputs=2,volume=2")
        cls.quiet = sound(d / "quiet.wav", "anoisesrc=d=4:c=white:r=44100:a=0.001")
        cls.clip = sound(d / "clip.wav", "sine=f=440:d=4:r=44100,volume=8")

    def test_synthetic_sounds_are_told_apart(self):
        got = {n: va.label(va.measure(str(getattr(self, n)), 0, 3.5)) for n in ("wind", "talk", "engine", "quiet")}
        self.assertEqual(got, {"wind": "wind", "talk": "voices", "engine": "rumble", "quiet": "quiet"})
        self.assertEqual(va.good_ambience(va.measure(str(self.clip), 0, 3.5)), (False, "clipped"))

    def test_a_file_without_sound_is_none(self):
        clip = helpers.make_clips(helpers.tmpdir() / "phase_j_silent", 1, seconds=2.0)[0]
        self.assertIsNone(va.measure(str(clip), 0, 1.5))


class Ambient(unittest.TestCase):
    def edl(self, **amb):
        return {"project": {"duration": 4.0, "fps": 30}, "style": {"ambient": dict({"level_db": -14}, **amb)},
                "shots": [{"clip": "a.mp4"}, {"clip": "b.mp4", "audio": {"use": True}}, {"clip": "c.mp4"}]}

    def windows(self):
        return [{"j": j, "clip": f"{c}.mp4", "start0": 1.0, "seconds": 1.0, "out_start": j * 1.0, "out_seconds": 1.0, "speed": None,
                 "has_audio": True} for j, c in enumerate("abc")]

    def run_with(self, edl, kinds):
        wind = {"level_db": -25, "low_db": -1, "voice_db": -15, "syllables": 0.1, "depth": 0.02, "periodic": 0.05, "clipped": 0.0}
        talk = dict(wind, low_db=-30, voice_db=-1, syllables=0.9, depth=0.5)
        by = {"a.mp4": wind, "b.mp4": wind, "c.mp4": talk if kinds == "talk" else dict(talk, clipped=0.5)}
        left: list = []
        with mock.patch.object(sfx, "mean_volume", return_value=-20.0), \
                mock.patch.object(sfx, "sound_of", side_effect=lambda clip, s, n: by[clip]):
            segs = sfx.ambient_segments(edl, self.windows(), -20.0, left)
        return [s["j"] for s in segs], left

    def test_wind_and_clipping_are_left_out_unless_the_shot_keeps_its_sound(self):
        used, left = self.run_with(self.edl(), "clipped")
        self.assertEqual(used, [1], "shot 1 says use: true; shot 0 is wind; shot 2 is clipped")
        self.assertEqual(sorted((x["j"], x["why"]) for x in left), [(0, "wind"), (2, "clipped")])
        used, _ = self.run_with(self.edl(skip=[]), "clipped")
        self.assertEqual(used, [0, 1, 2])
        used, _ = self.run_with(self.edl(thresholds={"wind_low_db": 0.0}), "talk")
        self.assertEqual(used, [0, 1, 2], "a stricter wind reading from calibrate_audio.py keeps shot 0")

    def test_a_window_that_cannot_be_measured_is_kept(self):
        with mock.patch.object(sfx, "mean_volume", return_value=-20.0), mock.patch.object(sfx, "sound_of", return_value=None):
            self.assertEqual(len(sfx.ambient_segments(self.edl(), self.windows(), -20.0)), 3)

    def test_skip_and_thresholds_are_checked(self):
        bad = [sfx.validate({"style": {"ambient": {"skip": ["rain"]}}}),
               sfx.validate({"style": {"ambient": {"thresholds": {"wind_low": 1}}}})]
        self.assertTrue(all(any(i["code"] == "ambient" for i in b) for b in bad))
        self.assertEqual(sfx.validate({"style": {"ambient": {"skip": ["wind"], "thresholds": {"wind_low_db": -3}}}}), [])


class MusicFitPure(unittest.TestCase):
    def beats(self, song=120.0, bpm=120.0):
        p = 60.0 / bpm
        beats = [round(0.5 + k * p, 3) for k in range(int((song - 0.5) / p))]
        return {"file": "song.wav", "duration": song, "period": p, "beats": beats, "downbeats": beats[::4]}

    def lev(self, song=120.0, hit=110.0):
        n = int(song / mf.STEP)
        loud = [-10.0 if k * mf.STEP < hit else -10.0 - 15 * (k * mf.STEP - hit) for k in range(n)]
        return {"all": loud, "low": [v - 6 for v in loud], "mid": [v - 3 for v in loud], "high": [v - 9 for v in loud]}

    def test_the_final_hit_lands_just_before_the_end_of_the_video(self):
        b = self.beats()
        onset = [0.0] * int(120 / vb.FRAME)
        for t in b["beats"]:
            onset[int(t / vb.FRAME)] = 1.0
        cands = mf.candidates(b, 30.0, self.lev(), onset=onset)
        self.assertTrue(cands)
        for c in cands:
            self.assertTrue(mf.HIT_BEFORE_END[0] <= 30.0 - c["hit_at"] <= mf.HIT_BEFORE_END[1], c)
            self.assertIn(c["cut"], b["downbeats"])
            self.assertEqual(c["tempo"], 1.0, "a steady song is not stretched")
        self.assertIsNone(mf.fit(dict(b, duration=31.0), 30.0, self.lev(31.0, 29.0)), "the song ends in time on its own")

    def test_edit_filter_and_validation(self):
        f = mf.edit_filter({"cut": 10.0, "resume": 50.0, "tempo": 1.01})
        self.assertIn("atrim=0:10.000", f)
        self.assertIn("atrim=start=50.000", f)
        self.assertIn("atempo=1.0100", f)
        self.assertNotIn("atempo", mf.edit_filter({"cut": 10.0, "resume": 50.0}))
        self.assertEqual(mf.validate(None), [])
        self.assertTrue(mf.validate({"cut": 20, "resume": 10}))
        self.assertTrue(mf.validate({"cut": 10, "resume": 50, "tempo": 1.5}))
        self.assertTrue(mf.validate({"cut": "10"}))
        edl = {"version": 1, "project": {"name": "x", "width": 180, "height": 320, "fps": 30, "duration": 2.0,
                                         "music_edit": {"cut": 5, "resume": 2}}, "shots": [{"clip": "a.mp4", "span": [0, 2]}]}
        self.assertTrue(any(i["code"] == "music-edit" for i in ve.lint(edl, check_files=False)))


@NEEDS_FF
class MusicFitRender(unittest.TestCase):
    def test_the_edited_music_is_the_song_cut_and_resumed(self):
        root = helpers.tmpdir() / "phase_j_music"
        root.mkdir(parents=True, exist_ok=True)
        song = root / "song.wav"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=20:r=44100", vl.ffp(song)])
        out = vr.edited_music(str(song), {"cut": 4.0, "resume": 12.0, "tempo": 1.0}, root)
        self.assertAlmostEqual(vl.probe(out)["duration"], 12.0, delta=0.05)
        self.assertEqual(vr.edited_music(str(song), {"cut": 4.0, "resume": 12.0, "tempo": 1.0}, root), out, "cached")
        edl = {"version": 1, "project": {"name": "x", "width": 180, "height": 320, "fps": 30, "duration": 10.0, "music": str(song),
                                         "music_edit": {"cut": 4.0, "resume": 12.0}}, "shots": [{"clip": "a.mp4", "span": [0, 10]}]}
        self.assertTrue(vr.resolve_music(edl, root).endswith(".wav"))


class Timebase(unittest.TestCase):
    def test_the_frame_is_the_real_hop(self):
        self.assertAlmostEqual(vb.FRAME, vb.HOP / vb.SR)
        self.assertIsNone(vb.stale({"timebase": vb.TIMEBASE}))
        self.assertIn("0.23 %", vb.stale({"bpm": 98}))

    @NEEDS_FF
    @helpers.slow
    def test_beat_times_do_not_drift_over_two_and_a_half_minutes(self):
        p = helpers.tmpdir() / "phase_j_click150.wav"
        helpers.click_track(p, bpm=120, first=0.5, seconds=150.0, step_at=None)
        r = vb.analyse(p)
        self.assertEqual(r["timebase"], vb.TIMEBASE)
        truth = [0.5 + 0.5 * k for k in range(300)]
        for t in (45.0, 90.0, 140.0):
            g = min(r["beats"], key=lambda x: abs(x - t))
            self.assertLess(abs(g - min(truth, key=lambda x: abs(x - g))), 0.03, f"at {t} s (0.25 was 0.1-0.2 s late)")


class Listen(unittest.TestCase):
    def sounds(self):
        out = {}
        for i in range(40):
            lab = ("voices", "wind", "rumble", "other")[i % 4]
            m = {"level_db": -25, "low_db": -1 if lab in ("wind", "rumble") else -30, "voice_db": -1 if lab == "voices" else -15,
                 "syllables": 0.9 if lab == "voices" else 0.1, "depth": 0.5 if lab == "voices" else 0.02,
                 "periodic": 0.9 if lab == "rumble" else 0.05, "clipped": 0.0}
            out[str(i)] = {"start": 1.0, "m": m, "label": va.label(m)}
        return out

    def test_the_sample_covers_every_label(self):
        picks = vli.sample(self.sounds(), 8)
        self.assertEqual(len(picks), 8)
        self.assertEqual({self.sounds()[str(i)]["label"] for i in picks}, {"voices", "wind", "rumble", "other"})

    def test_agreement_and_the_calibration_tool(self):
        s = self.sounds()
        labels = [{"clip": 1, "heard": "wind"}, {"clip": 5, "heard": "voices"}, {"clip": 0, "heard": "voices"},
                  {"clip": 2, "heard": "engine"}, {"clip": 9, "heard": "wind"}]
        r = vli.agreement(s, labels)
        self.assertEqual((r["n"], r["wind_precision"], r["wind_recall"]), (5, 0.67, 1.0))      # clips 1 and 9 right, 5 heard as voices
        work = helpers.tmpdir() / "phase_j_cal"
        work.mkdir(parents=True, exist_ok=True)
        vl.save_json(work / "audio.json", s)
        vl.save_json(work / "labels.json", {"labels": labels})
        out = subprocess.run([sys.executable, str(TOOLS / "calibrate_audio.py"), "--work", str(work), "--labels", str(work / "labels.json")],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("wind: precision 0.67", out.stdout)


if __name__ == "__main__":
    unittest.main()
