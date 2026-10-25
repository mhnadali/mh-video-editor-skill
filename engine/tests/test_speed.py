"""Phase 2 speed work: probe cache, GPU decode choice, cheaper effects, timing report."""
import copy
import os
import time
import unittest
from pathlib import Path

import helpers
import vid_edl as ve
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def edl_with_drop(n=8, duration=8.0):
    edges = [round(duration * i / n, 4) for i in range(n + 1)]
    return {"version": 1, "project": {"name": "s", "width": 180, "height": 320, "fps": 30, "duration": duration, "music": None},
            "beats": {"t0": 0.0, "period": 1.0, "drop_beat": 4}, "style": {"grade": "neutral"}, "transitions": {},
            "shots": [{"clip": f"c{i}.mp4", "span": [edges[i], edges[i + 1]]} for i in range(n)]}


class Graph(unittest.TestCase):
    def build(self, edl):
        bounds = ve.timeline(edl)
        trans = ve.effective_transitions(edl, bounds)
        parts = []
        for j in range(len(edl["shots"])):
            pl = vr.part_plan(edl, j, bounds, trans, 1.0)
            parts.append({"n": pl["n"], "path": f"p{j}.mp4"})
        return vr.build_graph(edl, parts, bounds, trans, 1.0), bounds, trans

    def test_flare_uses_fast_builtin_screen_blend_not_an_expression(self):
        (lines, extra), _, _ = self.build(edl_with_drop())
        text = "\n".join(lines)
        self.assertIn("blend=all_mode=screen", text)
        self.assertNotIn("all_expr", text)                      # per-pixel expressions were 10x slower
        self.assertIn("fade=t=in", text.split("[leak]")[0].split("\n")[-1])

    def test_rgb_split_lives_in_the_drop_part_only(self):
        edl = edl_with_drop()
        bounds = ve.timeline(edl)
        trans = ve.effective_transitions(edl, bounds)
        dj = ve.drop_boundary(edl, bounds)
        self.assertIsNotNone(dj)
        for j in range(len(edl["shots"])):
            pl = vr.part_plan(edl, j, bounds, trans, 1.0)
            vf = vr.part_vf(pl, False)
            if j == dj:
                self.assertIn("rgbashift", vf)
                a, b = pl["split"]
                self.assertEqual(b - a, 5)
                self.assertEqual(a, pl["head"])                  # starts on the first timeline frame of the cut
            else:
                self.assertNotIn("rgbashift", vf)
                self.assertIsNone(pl["split"])
        (lines, _), _, _ = self.build(edl)
        self.assertNotIn("rgbashift", "\n".join(lines))          # nothing global that slows every frame

    def test_no_drop_flash_means_no_split_anywhere(self):
        edl = edl_with_drop()
        edl["style"]["drop_flash"] = False
        bounds = ve.timeline(edl)
        trans = ve.effective_transitions(edl, bounds)
        self.assertTrue(all(vr.part_plan(edl, j, bounds, trans, 1.0)["split"] is None for j in range(8)))

    def test_flare_window_is_clamped_at_the_start(self):
        edl = edl_with_drop(4, 2.0)
        edl["style"]["flare_time"] = 0.2
        (lines, _), _, _ = self.build(edl)
        self.assertIn("fade=t=in:st=0.000", "\n".join(lines))


class Hardware(unittest.TestCase):
    def test_hwdecode_none_is_empty(self):
        self.assertEqual(vr.hwdecode_args("none"), [] if not os.environ.get("MH_HWACCEL") else vr.hwdecode_args("none"))

    @NEEDS_FF
    def test_hwdecode_auto_follows_gpu_availability(self):
        args = vr.hwdecode_args("auto")
        if os.environ.get("MH_HWACCEL"):
            self.assertEqual(args, ["-hwaccel", os.environ["MH_HWACCEL"]])
        elif vr.nvenc_works():
            self.assertEqual(args, ["-hwaccel", "cuda"])
        else:
            self.assertEqual(args, [])

    def test_env_override(self):
        old = os.environ.get("MH_HWACCEL")
        os.environ["MH_HWACCEL"] = "d3d11va"
        try:
            self.assertEqual(vr.hwdecode_args("none"), ["-hwaccel", "d3d11va"])
        finally:
            if old is None:
                os.environ.pop("MH_HWACCEL")
            else:
                os.environ["MH_HWACCEL"] = old


@NEEDS_FF
class ProbeCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "speed"
        cls.clips = helpers.make_clips(cls.root, 3, seconds=2.0)

    def test_second_probe_is_served_from_the_cache(self):
        vl._PROBE_CACHE.clear()
        t0 = time.time()
        a = vl.probe(self.clips[0])
        first = time.time() - t0
        t1 = time.time()
        b = vl.probe(self.clips[0])
        second = time.time() - t1
        self.assertEqual(a, b)
        self.assertLess(second, first / 5 + 0.002)

    def test_cache_returns_copies(self):
        a = vl.probe(self.clips[1])
        a["width"] = -1
        self.assertNotEqual(vl.probe(self.clips[1])["width"], -1)

    def test_cache_invalidates_when_the_file_changes(self):
        p = self.root / "changing.mp4"
        helpers.make_clip(p, "testsrc2", size="320x180", seconds=1.0)
        self.assertEqual(vl.probe(p)["width"], 320)
        helpers.make_clip(p, "testsrc2", size="640x360", seconds=1.0)
        later = time.time() + 3
        os.utime(p, (later, later))
        self.assertEqual(vl.probe(p)["width"], 640)

    def test_probe_many_collects_errors_without_raising(self):
        bad = self.root / "bad.mp4"
        bad.write_bytes(b"x")
        res = vl.probe_many([self.clips[0], self.clips[1], bad, self.root / "missing.mp4", self.clips[0]])
        self.assertEqual(len(res), 4)                              # duplicates collapsed
        self.assertIsInstance(res[str(bad)], vl.EngineError)
        self.assertIsInstance(res[str(self.root / "missing.mp4")], vl.EngineError)
        self.assertIn("duration", res[str(self.clips[0])])

    def test_missing_file_still_raises(self):
        with self.assertRaises(vl.EngineError):
            vl.probe(self.root / "nope.mp4")

    def test_render_reports_phase_timings(self):
        edges = [0.0, 1.0, 2.0]
        edl = {"version": 1, "project": {"name": "t", "width": 180, "height": 320, "fps": 30, "duration": 2.0},
               "style": {"grade": "neutral"}, "transitions": {"1": "fade"},
               "shots": [{"clip": vl.ffp(self.clips[0]), "span": [0, 1.0]}, {"clip": vl.ffp(self.clips[1]), "span": [1.0, 2.0]}]}
        p = self.root / "t.edl.json"
        vl.save_json(p, edl)
        res = vr.render(p, out=self.root / "t.mp4", encoder="libx264", workdir=self.root / "t_work", log=lambda *_: None)
        for key in ("parts_seconds", "assemble_seconds", "seconds", "parts_cached", "encoder"):
            self.assertIn(key, res)
        self.assertGreaterEqual(res["seconds"], res["assemble_seconds"])
        again = vr.render(p, out=self.root / "t.mp4", encoder="libx264", workdir=self.root / "t_work", log=lambda *_: None)
        self.assertEqual(again["parts_cached"], 2)
        self.assertLess(again["parts_seconds"], 1.0)

    def test_hardware_decode_gives_same_frame_count(self):
        edl = {"version": 1, "project": {"name": "t", "width": 180, "height": 320, "fps": 30, "duration": 2.0},
               "style": {"grade": "neutral"}, "transitions": {},
               "shots": [{"clip": vl.ffp(self.clips[0]), "span": [0, 1.0]}, {"clip": vl.ffp(self.clips[1]), "span": [1.0, 2.0]}]}
        p = self.root / "hw.edl.json"
        vl.save_json(p, edl)
        counts = []
        for mode in ("none", "auto"):
            res = vr.render(p, out=self.root / f"hw_{mode}.mp4", encoder="libx264", workdir=self.root / f"hw_{mode}_work",
                            hwdecode=mode, log=lambda *_: None)
            r = vl.run([vl.find_ffmpeg()["ffprobe"], "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", vl.ffp(res["out"])])
            counts.append(int(r.stdout.strip().rstrip(",")))
        self.assertEqual(counts, [60, 60])


if __name__ == "__main__":
    unittest.main()
