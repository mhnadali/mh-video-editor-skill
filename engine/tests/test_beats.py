import unittest

import helpers
import vid_beats
import vidlib as vl


@unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
class BeatDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wav = helpers.tmpdir() / "click92.wav"
        cls.period = helpers.click_track(cls.wav, bpm=92.0, first=1.0, seconds=20.0, step_at=10.0)
        cls.res = vid_beats.analyse(cls.wav)

    def test_tempo(self):
        self.assertAlmostEqual(self.res["bpm"], 92.0, delta=1.0)
        self.assertAlmostEqual(self.res["period"], self.period, delta=0.01)

    def test_grid_lands_on_clicks(self):
        for k in (0, 5, 15):
            click = 1.0 + k * self.period
            nearest = min(self.res["beats"], key=lambda t: abs(t - click))
            self.assertLess(abs(nearest - click), 0.04, f"beat {k}")

    def test_first_beat_is_where_the_music_starts(self):
        self.assertAlmostEqual(self.res["t0"], 1.0, delta=0.06)

    def test_drop_found_on_the_beat_grid(self):
        self.assertIsNotNone(self.res["drop"])
        self.assertAlmostEqual(self.res["drop"], 10.0, delta=1.5 * self.period)
        grid = self.res["t0"] + self.res["drop_beat"] * self.res["period"]
        self.assertAlmostEqual(grid, self.res["drop"], places=2)

    def test_confidence_high_for_clean_clicks(self):
        self.assertGreater(self.res["confidence"], 0.5)

    def test_reports_half_or_double_alternative(self):
        self.assertTrue(self.res["alternatives"])

    def test_other_tempo(self):
        wav = helpers.tmpdir() / "click120.wav"
        helpers.click_track(wav, bpm=120.0, first=0.5, seconds=16.0, step_at=None)
        res = vid_beats.analyse(wav)
        self.assertTrue(abs(res["bpm"] - 120.0) < 1.5 or abs(res["bpm"] - 60.0) < 1.0 or abs(res["bpm"] - 240.0) < 3,
                        res["bpm"])
        self.assertIsNone(res["drop"])          # no loudness step -> no drop invented

    def test_too_short_audio_is_an_error(self):
        wav = helpers.tmpdir() / "short.wav"
        helpers.click_track(wav, bpm=100, first=0.2, seconds=2.0, step_at=None)
        with self.assertRaises(vl.EngineError):
            vid_beats.analyse(wav)

    def test_missing_file_is_an_error(self):
        with self.assertRaises(vl.EngineError):
            vid_beats.analyse(helpers.tmpdir() / "nope.wav")


class SectionMath(unittest.TestCase):
    def test_find_sections_snaps_to_grid(self):
        # 20 s of energy: 100 frames/s; quiet until 10 s then 3x louder
        full = [100.0] * 1000 + [300.0] * 1000
        res = vid_beats.find_sections(full, period=0.5, t0=0.0, duration=20.0)
        self.assertIsNotNone(res["drop"])
        self.assertAlmostEqual(res["drop"], 10.0, delta=1.0)
        self.assertAlmostEqual(res["drop"] / 0.5, round(res["drop"] / 0.5), places=2)

    def test_flat_energy_has_no_drop(self):
        res = vid_beats.find_sections([100.0] * 2000, period=0.5, t0=0.0, duration=20.0)
        self.assertIsNone(res["drop"])


if __name__ == "__main__":
    unittest.main()
