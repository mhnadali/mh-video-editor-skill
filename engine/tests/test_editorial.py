"""Tracks A-G: editorial measurement and lint, musical sections, pacing, skin-tone WB, auto-reframe, music selection."""
import sys
import unittest
from pathlib import Path

import helpers
import vid_beats
import vid_color as vc
import vid_editorial as ed
import vid_plan as vp
import vidlib as vl

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "engine" / "tools"))
import pick_music  # noqa: E402

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class EditorialLint(unittest.TestCase):
    def test_issues_fire_for_the_classic_mistakes(self):
        edl = {"shots": [{"clip": f"{i}.mp4"} for i in range(5)]}
        bounds = [0, 30, 60, 90, 120, 150]                 # equal shots -> flat pacing
        feats = [
            {"shot_size": "close", "motion_dx": 2.0, "motion_mag": 2.0, "hash": 5},
            {"shot_size": "close", "motion_dx": -2.0, "motion_mag": 2.0, "hash": 5},   # jump-cut + screen-direction vs 0
            {"shot_size": "close", "motion_dx": 0.0, "motion_mag": 0.0, "hash": 999},  # 0,1,2 all close -> repeat
            {"shot_size": "medium", "motion_dx": 0.0, "motion_mag": 0.0, "hash": 111},
            {"shot_size": "wide", "motion_dx": 0.0, "motion_mag": 0.0, "hash": 222},
        ]
        codes = {i["code"] for i in ed.editorial_issues(edl, feats, bounds, {})}
        self.assertIn("shot-size-repeat", codes)
        self.assertIn("jump-cut", codes)
        self.assertIn("screen-direction", codes)
        self.assertIn("pacing-flat", codes)

    def test_a_transition_cut_is_not_a_jump_cut(self):
        edl = {"shots": [{"clip": "a.mp4"}, {"clip": "b.mp4"}]}
        feats = [{"shot_size": "close", "motion_dx": 0.0, "hash": 1}, {"shot_size": "close", "motion_dx": 0.0, "hash": 1}]
        codes = {i["code"] for i in ed.editorial_issues(edl, feats, [0, 30, 60], {1: "fade"})}
        self.assertNotIn("jump-cut", codes)                # a deliberate transition is not a jump cut

    def test_summary_counts_shot_sizes(self):
        edl = {"shots": [{"clip": f"{i}.mp4"} for i in range(3)]}
        feats = [{"shot_size": "wide", "motion_mag": 2.0}, {"shot_size": "close", "motion_mag": 0.0}, {"shot_size": "wide", "motion_mag": 2.0}]
        s = ed.summarize_editorial(edl, feats, [0, 20, 40, 60], {})
        self.assertEqual(s["distinct_shot_sizes"], 2)
        self.assertEqual(s["shot_size_mix"], {"wide": 2, "close": 1})


class Sections(unittest.TestCase):
    def test_detects_a_loudness_boundary(self):
        full = [0.1] * 800 + [0.9] * 800          # 10 ms frames: calm for 8 s, loud for 8 s
        beats = [round(0.5 * k, 3) for k in range(1, 32)]
        secs = vid_beats.detect_sections(full, beats, 0.5)
        self.assertGreaterEqual(len(secs), 2)
        self.assertTrue(any(abs(s["start"] - 8.0) < 1.2 for s in secs), [s["start"] for s in secs])

    def test_beat_strengths_are_normalised(self):
        onset = [0.0, 0.5, 0.0, 1.0, 0.0, 0.25]
        beats = [0.01, 0.03, 0.05]
        st = vid_beats.beat_strengths(onset, beats)
        self.assertEqual(len(st), 3)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in st))


class Pacing(unittest.TestCase):
    def test_paced_layout_holds_calm_beats(self):
        beats = {"beats": [round(0.5 * k, 3) for k in range(60)],
                 "beat_strength": [1.0 if k % 2 == 0 else 0.1 for k in range(60)]}
        beat_lay = vp.layout(20.0, 0.5, 0.5, 2)
        paced = vp.layout_paced(beats, 20.0, 0.5, 0.5, 2)
        self.assertLess(paced["shots"], beat_lay["shots"])          # calm beats are held -> fewer shots
        self.assertEqual(paced["cuts"], sorted(set(paced["cuts"])))  # strictly increasing, unique

    def test_strength_at_picks_the_nearest_beat(self):
        beats = {"beats": [0.0, 1.0, 2.0], "beat_strength": [0.1, 0.9, 0.2]}
        self.assertEqual(vp.strength_at(beats, 1.05), 0.9)
        self.assertEqual(vp.strength_at({}, 1.0), 1.0)              # no data -> treat as strong


class SkinWhiteBalance(unittest.TestCase):
    def _stats(self, u, v):
        return {"YAVG": 120.0, "YLOW": 40.0, "YHIGH": 200.0, "YMIN": 20.0, "YMAX": 235.0, "UAVG": u, "VAVG": v, "SATAVG": 40.0}

    def test_skin_mode_never_cools(self):
        for u, v in ((150.0, 120.0), (110.0, 150.0)):     # a blue cast and a warm cast
            corr, _why = vc.propose_correction(self._stats(u, v), strength=0.8, wb="skin")
            gain = corr["gain"] if corr else [1.0, 1.0, 1.0]
            self.assertGreaterEqual(gain[0], 1.0 - 1e-6, (u, v))    # red never pulled down
            self.assertLessEqual(gain[2], 1.0 + 1e-6, (u, v))       # blue never pushed up


@NEEDS_FF
@helpers.slow
class Measurement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "editorial_meas"
        cls.root.mkdir(parents=True, exist_ok=True)
        ff = vl.find_ffmpeg()["ffmpeg"]
        cls.flat = cls.root / "flat.mp4"
        vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.flat)])
        cls.right = cls.root / "right.mp4"
        vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=2",
                "-f", "lavfi", "-i", "testsrc2=s=320x360:r=30:d=2", "-filter_complex", "[0:v][1:v]overlay=x=320:y=0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.right)])

    def test_flat_clip_reads_wide_still_centred(self):
        m = ed.measure_shot(vl.ffp(self.flat), 0.3, 1.2)
        self.assertEqual(m["shot_size"], "wide")
        self.assertEqual(m["motion_mag"], 0.0)
        self.assertAlmostEqual(m["focus_x"], 0.5, delta=0.05)

    def test_activity_pulls_the_focus_toward_it(self):
        m = ed.measure_shot(vl.ffp(self.right), 0.3, 1.2)
        self.assertGreater(m["focus_x"], 0.55)              # saliency leans to the busy right half
        self.assertIn(m["shot_size"], ed.SHOT_SIZES)


@NEEDS_FF
@helpers.slow
class MusicSelection(unittest.TestCase):
    def test_ranks_the_closer_tempo_first(self):
        d = helpers.tmpdir() / "music_pick"
        d.mkdir(parents=True, exist_ok=True)
        helpers.click_track(d / "slow.wav", bpm=90, first=0.5, seconds=10.0, step_at=None)
        helpers.click_track(d / "fast.wav", bpm=140, first=0.5, seconds=10.0, step_at=None)
        ranked = pick_music.rank(d, target_bpm=140, max_seconds=10.0)
        self.assertTrue(ranked[0]["file"].endswith("fast.wav"), ranked)


@NEEDS_FF
@helpers.slow
class AutoReframe(unittest.TestCase):
    def test_auto_focus_renders_in_another_format(self):
        import vid_beats as vb
        import vid_render as vr
        root = helpers.tmpdir() / "auto_reframe"
        root.mkdir(parents=True, exist_ok=True)
        clips = helpers.make_clips(root, count=6, seconds=4.0)
        music = root / "m.wav"
        helpers.click_track(music, bpm=120, first=0.5, seconds=6.0, step_at=3.0)
        beats = vb.analyse(music)
        need = vp.layout(beats["duration"], vp.body_start_time(beats, beats["duration"], beats["period"], 2), beats["period"], 2)["shots"]
        edl = vp.plan([{"clip": vl.ffp(clips[i % len(clips)])} for i in range(need)], beats, name="af",
                      music=vl.ffp(music), duration=beats["duration"])
        edl["project"].update(width=180, height=320)
        edl["shots"][2]["reframe"] = "cover"
        edl["shots"][2]["focus"] = "auto"
        import vid_edl as ve
        self.assertEqual([i for i in ve.lint(edl) if i["code"] not in ("clip-reused",)], [])
        p = root / "af.edl.json"
        vl.save_json(p, edl)
        res = vr.render(p, out=root / "af.mp4", encoder="libx264", workdir=root / "w", preview=True,
                        fmt="youtube-16x9", log=lambda *_: None)
        info = vl.probe(res["out"])
        self.assertEqual((info["width"], info["height"]), (960, 540))


if __name__ == "__main__":
    unittest.main()
