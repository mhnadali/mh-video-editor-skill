"""Real-world awkwardness: Persian/space file names, odd fps, short clips, portrait->landscape, mp3 music, no drop,
silent audio, cache invalidation, corrupt files."""
import copy
import os
import time
import unittest
from pathlib import Path

import helpers
import vid_beats
import vid_edl as ve
import vid_plan as vp
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def count_frames(path) -> int:
    r = vl.run([vl.find_ffmpeg()["ffprobe"], "-v", "error", "-count_frames", "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", vl.ffp(path)])
    return int(r.stdout.strip().rstrip(","))


def tiny_edl(shots, duration, music=None, size=(180, 320), transitions=None, beats=None, style=None):
    edl = {"version": 1, "project": {"name": "r", "width": size[0], "height": size[1], "fps": 30, "duration": duration,
                                     "music": music},
           "style": style or {"grade": "neutral"}, "shots": shots, "transitions": transitions or {}}
    if beats:
        edl["beats"] = beats
    return edl


def spans(clips, duration):
    n = len(clips)
    edges = [round(duration * i / n, 4) for i in range(n + 1)]
    return [{"clip": vl.ffp(c), "span": [edges[i], edges[i + 1]]} for i, c in enumerate(clips)]


def render(edl, root, name, **kw):
    p = root / f"{name}.edl.json"
    vl.save_json(p, edl)
    kw.setdefault("encoder", "libx264")
    return vr.render(p, out=root / f"{name}.mp4", workdir=root / f"{name}_work", log=lambda *_: None, **kw), p


@NEEDS_FF
@helpers.slow
class Awkward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "robust"
        cls.foot = cls.root / "کل سفر مرنجاب"                      # Persian folder name with spaces
        cls.foot.mkdir(parents=True)
        cls.persian = helpers.make_clip(cls.foot / "کلیپ شماره ۱.mp4", "testsrc2", seconds=3.0)
        cls.fps24 = helpers.make_clip(cls.foot / "fps24.mp4", "smptebars", seconds=3.0, fps=24)
        cls.fps25 = helpers.make_clip(cls.foot / "fps25.mp4", "rgbtestsrc", seconds=3.0, fps=25)
        cls.portrait = helpers.make_clip(cls.foot / "portrait.mp4", "testsrc", size="360x640", seconds=3.0)
        cls.short = helpers.make_clip(cls.foot / "short.mp4", "testsrc2", seconds=0.5)
        cls.wav = cls.root / "my music.wav"
        helpers.click_track(cls.wav, bpm=100.0, first=0.4, seconds=8.0, step_at=4.0)
        cls.mp3 = cls.root / "music.mp3"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", str(cls.wav), "-b:a", "128k", str(cls.mp3)])

    def test_persian_and_space_paths_end_to_end(self):
        clips = [self.persian, self.fps24, self.fps25, self.portrait]
        edl = tiny_edl(spans(clips, 4.0), 4.0, music=vl.ffp(self.wav), transitions={"1": "fade", "3": "circleopen"})
        res, p = render(edl, self.root, "persian")
        self.assertEqual(count_frames(res["out"]), 120)
        self.assertTrue(vr.verify(p, res["out"])["ok"])

    def test_mixed_fps_sources_give_exact_frames(self):
        clips = [self.fps24, self.fps25, self.persian]
        edl = tiny_edl(spans(clips, 3.0), 3.0, transitions={"1": "slideleft", "2": "hlslice"})
        res, _ = render(edl, self.root, "fps")
        self.assertEqual(count_frames(res["out"]), 90)

    def test_clip_shorter_than_shot_freezes_instead_of_failing(self):
        edl = tiny_edl([{"clip": vl.ffp(self.short), "span": [0, 2.0]}, {"clip": vl.ffp(self.persian), "span": [2.0, 3.0]}], 3.0)
        issues = ve.lint(edl)
        self.assertIn("clip-short", {i["code"] for i in issues})
        self.assertFalse(ve.has_errors(issues))
        res, _ = render(edl, self.root, "short")
        self.assertEqual(count_frames(res["out"]), 90)

    def test_portrait_and_landscape_clips_in_every_output_preset(self):
        clips = [self.persian, self.portrait]
        for preset, size in (("youtube-16x9", (320, 180)), ("square-1x1", (240, 240)), ("reels-9x16", (180, 320))):
            edl = tiny_edl(spans(clips, 2.0), 2.0, size=size)
            res, _ = render(edl, self.root, f"preset_{preset}")
            info = vl.probe(res["out"])
            self.assertEqual((info["width"], info["height"]), size, preset)

    def test_mp3_music_beats_and_render(self):
        beats = vid_beats.analyse(self.mp3)
        self.assertAlmostEqual(beats["bpm"], 100.0, delta=1.5)
        need = vp.layout(beats["duration"], vp.body_start_time(beats, beats["duration"], beats["period"], 2),
                         beats["period"], 2)["shots"]
        clips = [{"clip": vl.ffp([self.persian, self.fps24, self.fps25, self.portrait][i % 4])} for i in range(need)]
        edl = vp.plan(clips, beats, name="mp3", music=vl.ffp(self.mp3), duration=beats["duration"])
        edl["project"].update(width=180, height=320)
        res, p = render(edl, self.root, "mp3")
        self.assertTrue(vr.verify(p, res["out"])["ok"])

    def test_no_drop_means_no_flash_and_no_riser(self):
        beats = {"duration": 6.0, "period": 0.5, "t0": 1.0, "file": None}
        need = vp.layout(6.0, vp.body_start_time(beats, 6.0, 0.5, 2), 0.5, 2)["shots"]
        clips = [{"clip": vl.ffp(self.persian)} for _ in range(need)]
        edl = vp.plan(clips, beats, name="nodrop", music=vl.ffp(self.wav), duration=6.0)
        self.assertNotIn("drop_beat", edl["beats"])
        edl["project"].update(width=180, height=320)
        res, p = render(edl, self.root, "nodrop")
        self.assertEqual(count_frames(res["out"]), 180)

    def test_beats_spans_instead_of_seconds(self):
        edl = tiny_edl([{"clip": vl.ffp(self.persian), "beats": [-4, 0]},
                        {"clip": vl.ffp(self.fps24), "beats": [0, 2]},
                        {"clip": vl.ffp(self.fps25), "beats": [2, 8]}], 3.0,
                       beats={"t0": 1.0, "period": 0.25})
        res, _ = render(edl, self.root, "beatspans")
        self.assertEqual(count_frames(res["out"]), 90)

    def test_intro_zero_plan_renders(self):
        beats = {"duration": 5.0, "period": 0.5, "t0": 0.0, "file": None}
        lay = vp.layout(5.0, 0.0, 0.5, 0)
        clips = [{"clip": vl.ffp(self.persian)} for _ in range(lay["shots"])]
        edl = vp.plan(clips, beats, name="nointro", music=None, duration=5.0, intro_shots=0, transitions=False)
        edl["project"].update(width=180, height=320)
        res, _ = render(edl, self.root, "nointro")
        self.assertEqual(count_frames(res["out"]), 150)

    def test_cache_invalidates_when_a_source_file_changes(self):
        clips = [self.persian, self.fps24]
        edl = tiny_edl(spans(clips, 2.0), 2.0)
        res1, p = render(edl, self.root, "cache")
        self.assertEqual(res1["parts_cached"], 0)
        res2, _ = render(edl, self.root, "cache")
        self.assertEqual(res2["parts_cached"], 2)
        later = time.time() + 5
        os.utime(self.fps24, (later, later))
        res3, _ = render(edl, self.root, "cache")
        self.assertEqual(res3["parts_cached"], 1)

    def test_no_cache_flag(self):
        edl = tiny_edl(spans([self.persian, self.fps24], 2.0), 2.0)
        render(edl, self.root, "nocache")
        res, _ = render(edl, self.root, "nocache", use_cache=False)
        self.assertEqual(res["parts_cached"], 0)

    def test_corrupt_clip_is_reported_before_rendering(self):
        bad = self.foot / "corrupt.mp4"
        bad.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 50)
        edl = tiny_edl(spans([self.persian, bad], 2.0), 2.0)
        issues = ve.lint(edl)
        self.assertIn("clip-unreadable", {i["code"] for i in issues})
        with self.assertRaises(vl.EngineError):
            render(edl, self.root, "corrupt")

    def test_silent_music_file_does_not_crash_analysis(self):
        sil = self.root / "silence.wav"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                "-t", "6", str(sil)])
        res = vid_beats.analyse(sil)
        self.assertLess(res["confidence"], 0.35)
        self.assertIsNone(res["drop"])

    def test_output_folder_is_created(self):
        edl = tiny_edl(spans([self.persian, self.fps24], 2.0), 2.0)
        p = self.root / "outdir.edl.json"
        vl.save_json(p, edl)
        out = self.root / "deep" / "er" / "x.mp4"
        vr.render(p, out=out, encoder="libx264", workdir=self.root / "outdir_work", log=lambda *_: None)
        self.assertTrue(out.is_file())

    def test_grade_can_be_raw_filter_or_missing(self):
        for name, style in (("rawgrade", {"grade": "eq=contrast=1.2:saturation=1.1"}), ("nograde", {"grade": None})):
            edl = tiny_edl(spans([self.persian, self.fps24], 2.0), 2.0, style=style)
            res, _ = render(edl, self.root, name)
            self.assertEqual(count_frames(res["out"]), 60)

    def test_unknown_grade_preset_is_a_clear_error(self):
        edl = tiny_edl(spans([self.persian, self.fps24], 2.0), 2.0, style={"grade": "no-such-grade"})
        with self.assertRaises(vl.EngineError):
            render(edl, self.root, "badgrade")


if __name__ == "__main__":
    unittest.main()
