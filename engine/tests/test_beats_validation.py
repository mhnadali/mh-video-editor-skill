"""Phase 2: second-opinion tempo, drift, onset error and downbeats."""
import unittest

import helpers
import vid_beats as vb
import vid_plan as vp
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class Pure(unittest.TestCase):
    def test_relation(self):
        self.assertEqual(vb.relation(92.0, 92.5), "same")
        self.assertEqual(vb.relation(92.0, 184.0), "double")
        self.assertEqual(vb.relation(92.0, 46.0), "half")
        self.assertEqual(vb.relation(92.0, 120.0), "different")

    def test_ioi_tempo_from_regular_peaks(self):
        peaks = [1.0 + 0.5 * k for k in range(30)]
        self.assertAlmostEqual(vb.ioi_tempo(peaks), 120.0, delta=3.0)

    def test_ioi_tempo_with_extra_offbeat_peaks(self):
        peaks = sorted([1.0 + 0.6 * k for k in range(30)] + [1.0 + 0.6 * k + 0.3 for k in range(0, 30, 2)])
        self.assertAlmostEqual(vb.ioi_tempo(peaks), 100.0, delta=4.0)

    def test_ioi_needs_enough_peaks(self):
        self.assertIsNone(vb.ioi_tempo([1.0, 2.0, 3.0]))

    def test_peak_times_ignores_weak_and_close_peaks(self):
        on = [0.0] * 500
        on[100] = 1.0
        on[104] = 0.9                      # too close to the first: merged
        on[200] = 0.1                      # below 30% of max
        on[300] = 0.8
        t = vb.peak_times(on)
        self.assertEqual(t, [100 * vb.FRAME, 300 * vb.FRAME])         # frames of 110 samples at 11025 Hz, not 10 ms

    def test_bar_phase_picks_the_heavy_beat(self):
        # low-band envelope with a big kick every 4th beat starting at beat index 2
        period, n = 0.5, 24
        low = [0.0] * int((n * period + 1) / vb.FRAME)
        beats = [1.0 + k * period for k in range(n)]
        for k, t in enumerate(beats):
            i = int(round(t / vb.FRAME))
            low[i + 1] = 10.0 if k % 4 == 2 else 3.0
        phase, conf = vb.bar_phase(low, beats)
        self.assertEqual(phase, 2)
        self.assertGreater(conf, 0.5)

    def test_bar_phase_no_energy(self):
        self.assertEqual(vb.bar_phase([0.0] * 500, [1.0, 1.5, 2.0, 2.5]), (0, 0.0))


@NEEDS_FF
class Audio(unittest.TestCase):
    def analyse(self, name, **kw):
        wav = helpers.tmpdir() / f"{name}.wav"
        helpers.click_track(wav, **kw)
        return vb.analyse(wav)

    def test_clean_track_has_no_warnings(self):
        r = self.analyse("v_clean", bpm=100, first=0.5, seconds=16, step_at=None, accent_every=4)
        v = r["validation"]
        self.assertTrue(v["ok"], v["notes"])
        self.assertEqual(v["second_relation"], "same")
        self.assertLess(v["drift_pct"], 0.8)
        self.assertLess(v["median_onset_error_ms"], 35)

    def test_downbeats_follow_the_accent(self):
        for accent_phase in (0, 1, 3):
            r = self.analyse(f"v_accent{accent_phase}", bpm=100, first=0.5, seconds=20, step_at=None,
                             accent_every=4, accent_phase=accent_phase, accent_gain=2.5)
            first_beat_index = round((0.5 - r["t0"]) / r["period"])      # beat index of the very first click
            truth = (first_beat_index + accent_phase) % 4
            self.assertEqual(r["bar_phase"], truth, f"phase {accent_phase}")
            self.assertEqual(r["downbeats"], [t for k, t in enumerate(r["beats"]) if k % 4 == truth])

    def test_tempo_ramp_is_reported_as_drift(self):
        r = self.analyse("v_ramp", bpm=90, bpm_end=104, first=0.5, seconds=24, step_at=None)
        v = r["validation"]
        self.assertGreater(v["drift_pct"], 0.8)
        self.assertTrue(any("drifts" in n for n in v["notes"]), v["notes"])

    def test_fields_are_always_present(self):
        r = self.analyse("v_fields", bpm=120, first=0.5, seconds=12, step_at=None)
        for key in ("second_tempo_bpm", "second_relation", "drift_pct", "median_onset_error_ms", "bar_phase",
                    "bar_confidence", "notes", "ok"):
            self.assertIn(key, r["validation"])
        self.assertIn("downbeats", r)


class BarAlign(unittest.TestCase):
    BEATS = {"duration": 28.235, "period": 0.65, "t0": 0.6, "bar_phase": 1}

    def test_default_is_unchanged(self):
        t_plain = vp.body_start_time(self.BEATS, 28.235, 0.65, 2)
        self.assertAlmostEqual(t_plain, 0.6 + 4 * 0.65, places=3)

    def test_aligned_start_is_on_a_downbeat_and_not_earlier(self):
        plain = vp.body_start_time(self.BEATS, 28.235, 0.65, 2)
        aligned = vp.body_start_time(self.BEATS, 28.235, 0.65, 2, align_bars=True)
        k = round((aligned - 0.6) / 0.65)
        self.assertEqual((k - 1) % 4, 0)
        self.assertGreaterEqual(aligned, plain)
        self.assertLess(aligned - plain, 4 * 0.65)

    def test_no_bar_phase_means_no_alignment(self):
        b = {k: v for k, v in self.BEATS.items() if k != "bar_phase"}
        self.assertEqual(vp.body_start_time(b, 28.235, 0.65, 2, align_bars=True), vp.body_start_time(b, 28.235, 0.65, 2))

    def test_plan_records_the_bar_phase_relative_to_the_body(self):
        beats = dict(self.BEATS, drop_beat=22)
        n = vp.layout(28.235, vp.body_start_time(beats, 28.235, 0.65, 2, align_bars=True), 0.65, 2)["shots"]
        edl = vp.plan([{"clip": f"c{i}.mp4"} for i in range(n)], beats, duration=28.235, align_bars=True)
        self.assertEqual(edl["beats"]["bar_phase"], 0)         # aligned start = a downbeat = body beat 0


if __name__ == "__main__":
    unittest.main()
