"""Phase 8 + 12: delivery presets, size/bitrate targeting, render manifests, project bundles, cache GC, multi-track music."""
import json
import unittest
import zipfile
from pathlib import Path

import helpers
import vid_beats
import vid_deliver as vd
import vid_edl as ve
import vid_plan as vp
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
ROOT = Path(__file__).resolve().parent.parent.parent


class SizeMath(unittest.TestCase):
    def test_bitrate_hits_the_requested_size(self):
        kbps = vd.video_kbps_for_size(10.0, 30.0)
        self.assertAlmostEqual(vd.estimate_mb(kbps, 30.0), 10.0, delta=0.3)

    def test_too_small_a_target_is_refused(self):
        with self.assertRaises(vl.EngineError):
            vd.video_kbps_for_size(0.05, 30.0)

    def test_every_delivery_preset_maps_to_a_real_format_and_sane_loudness(self):
        for name, _desc in vd.list_delivery():
            d = vd.resolve_delivery(name)
            pre = vl.load_preset(d["format"])
            self.assertEqual({"width", "height", "fps"} <= set(pre), True, name)
            self.assertTrue(-23.0 <= d["loudness"] <= -9.0, name)

    def test_unknown_delivery_is_an_error(self):
        with self.assertRaises(vl.EngineError):
            vd.resolve_delivery("myspace")

    def test_verify_delivery_flags_wrong_size(self):
        ok = vd.verify_delivery({"size": 10_000_000, "width": 1080, "height": 1920}, 10.0, "reels")
        self.assertEqual(ok["level"], "ok")
        bad = vd.verify_delivery({"size": 40_000_000, "width": 1080, "height": 1920}, 10.0, "reels")
        self.assertEqual(bad["level"], "warn")


class ManifestAndBundle(unittest.TestCase):
    def setUp(self):
        self.d = helpers.tmpdir() / "deliver_mb"
        self.d.mkdir(parents=True, exist_ok=True)
        self.edl = {"version": 1, "project": {"name": "t", "width": 90, "height": 160, "fps": 30, "duration": 2.0,
                    "music": None}, "style": {}, "shots": [{"clip": "a.mp4", "span": [0, 2.0]}]}
        self.path = self.d / "m.edl.json"
        vl.save_json(self.path, self.edl)

    def test_manifest_records_versions_and_hashes(self):
        man = vd.build_manifest(self.path, self.edl, self.d / "no_output_yet.mp4")   # output does not exist yet
        self.assertEqual(man["manifest_version"], 1)
        self.assertEqual(man["engine_version"], vl.engine_version())
        self.assertEqual(len(man["edl"]["sha256"]), 64)
        self.assertEqual(man["project"]["duration"], 2.0)
        self.assertEqual(len(man["inputs"]["clips"]), 1)
        self.assertNotIn("output", man)

    def test_bundle_zips_the_sidecars_but_no_media(self):
        (self.d / "video-brief.md").write_text("# brief\nCONFIRMED\n", encoding="utf-8")
        (self.d / "beats.json").write_text("{}", encoding="utf-8")
        z = vd.build_bundle(self.path)
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
        self.assertIn("m.edl.json", names)
        self.assertIn("video-brief.md", names)
        self.assertIn("beats.json", names)
        self.assertIn("BUNDLE.txt", names)
        self.assertNotIn("a.mp4", names)

    def test_gc_cache_removes_only_old_files(self):
        work = self.d / "work"
        (work / "parts" / "main").mkdir(parents=True, exist_ok=True)
        old = work / "parts" / "main" / "000_old.mp4"
        old.write_bytes(b"x" * 1000)
        import os
        os.utime(old, (0, 0))                                    # 1970: definitely stale
        fresh = work / "parts" / "main" / "001_fresh.mp4"
        fresh.write_bytes(b"y" * 1000)
        res = vd.gc_cache(work, keep_days=1.0)
        self.assertEqual(res["removed"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


class Encoders(unittest.TestCase):
    def test_detection_returns_booleans_without_crashing(self):
        hw = vr.hw_encoders()
        self.assertEqual(set(hw), set(vr.HW_H264))
        self.assertTrue(all(isinstance(v, bool) for v in hw.values()))

    def test_auto_falls_back_to_libx264_without_hardware(self):
        self.assertEqual(vr._resolve_encoder("libx264"), "libx264")
        a, name = vr.encoder_args("libx264", preview=False, bitrate_kbps=1500)
        self.assertEqual(name, "libx264")
        self.assertIn("-b:v", a)

    def test_requesting_missing_hardware_is_a_clear_error(self):
        if not vr.encoder_available("qsv"):
            with self.assertRaises(vl.EngineError):
                vr.encoder_args("qsv", preview=False)


@NEEDS_FF
@helpers.slow
class RenderDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "deliver_render"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.clips = helpers.make_clips(cls.root, count=8, seconds=4.0)
        cls.m1 = cls.root / "m1.wav"
        cls.m2 = cls.root / "m2.wav"
        helpers.click_track(cls.m1, bpm=120, first=0.5, seconds=6.0, step_at=3.0)
        helpers.click_track(cls.m2, bpm=120, first=0.0, seconds=6.0, step_at=None)
        beats = vid_beats.analyse(cls.m1)
        body = vp.body_start_time(beats, beats["duration"], beats["period"], 2)
        need = vp.layout(beats["duration"], body, beats["period"], 2)["shots"]
        pool = [vl.ffp(cls.clips[i % len(cls.clips)]) for i in range(need)]
        cls.edl = vp.plan([{"clip": c} for c in pool], beats, name="deliv", music=vl.ffp(cls.m1), duration=beats["duration"])
        cls.edl["project"].update(width=180, height=320)
        cls.path = cls.root / "deliv.edl.json"
        vl.save_json(cls.path, cls.edl)

    def test_target_size_stays_under_budget_and_writes_manifest_and_bundle(self):
        # A size target is an upper bound (platform limit). Two-pass engages on a full libx264 render; flat synthetic
        # footage may undershoot, which is fine — exceeding the target is the failure we care about.
        res = vr.render(self.path, out=self.root / "size.mp4", encoder="libx264", workdir=self.root / "w",
                        preview=False, target_size_mb=1.5, manifest=True, bundle=True,
                        json_out=str(self.root / "res.json"), log=lambda *_: None)
        self.assertLessEqual(res["size_mb"], 1.5 * (1 + vd.SIZE_TOLERANCE) + 0.3)
        self.assertEqual(res["encoder"], "libx264")
        self.assertEqual(res["delivery_check"]["id"], "delivery")
        self.assertTrue(Path(res["manifest"]).is_file())
        self.assertTrue(Path(res["bundle"]).is_file())
        self.assertTrue(Path(res["json"]).is_file())
        man = vl.load_json(res["manifest"])
        self.assertIn("output", man)
        self.assertEqual(len(man["output"]["sha256"]), 64)

    def test_delivery_preset_sets_format_and_loudness(self):
        res = vr.render(self.path, out=self.root / "reels.mp4", encoder="libx264", workdir=self.root / "w",
                        preview=True, delivery="reels", qa=True, log=lambda *_: None)
        info = vl.probe(res["out"])
        pre = vl.load_preset("reels-9x16")
        self.assertAlmostEqual(info["width"] / info["height"], pre["width"] / pre["height"], places=2)
        self.assertNotEqual(res["qa"], "fail")

    def test_multi_track_music_concatenates_and_renders_with_audio(self):
        edl = vp.plan([{"clip": vl.ffp(self.clips[i % len(self.clips)])} for i in range(len(self.edl["shots"]))],
                      vid_beats.analyse(self.m1), name="multi", music=[vl.ffp(self.m1), vl.ffp(self.m2)],
                      duration=vid_beats.analyse(self.m1)["duration"])
        edl["project"].update(width=180, height=320)
        p = self.root / "multi.edl.json"
        vl.save_json(p, edl)
        self.assertEqual([i for i in ve.lint(edl) if i["code"] not in ("clip-reused",)], [])
        res = vr.render(p, out=self.root / "multi.mp4", encoder="libx264", workdir=self.root / "wm",
                        preview=True, log=lambda *_: None)
        self.assertTrue(vl.probe(res["out"])["has_audio"])


if __name__ == "__main__":
    unittest.main()
