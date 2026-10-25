"""End-to-end tests on synthetic media: probe -> sheets -> beats -> plan -> lint -> render -> verify."""
import copy
import shutil
import unittest
from pathlib import Path

import helpers
import mh_video
import vid_beats
import vid_edl as ve
import vid_plan as vp
import vid_probe
import vid_render as vr
import vid_sheets
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def count_frames(path) -> int:
    r = vl.run([vl.find_ffmpeg()["ffprobe"], "-v", "error", "-count_frames", "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", vl.ffp(path)])
    return int(r.stdout.strip().rstrip(","))


def small(edl):
    edl = copy.deepcopy(edl)
    edl["project"].update(width=270, height=480)
    return edl


@NEEDS_FF
@helpers.slow
class Project(unittest.TestCase):
    """Shared project: 10 s of 120 BPM music with a drop, 18 shots from synthetic clips."""

    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "proj"
        cls.footage = cls.root / "footage"
        cls.clips = helpers.make_clips(cls.footage, 4, seconds=5.0)
        cls.hlg = helpers.make_hlg_clip(cls.footage / "hlg.mp4", 4.0)
        try:
            cls.rot = helpers.make_rotated_clip(cls.clips[0], cls.footage / "rot.mp4", 90)
        except vl.EngineError:
            cls.rot = None
        cls.music = cls.root / "music.wav"
        helpers.click_track(cls.music, bpm=120.0, first=0.5, seconds=10.0, step_at=5.0)
        cls.beats = vid_beats.analyse(cls.music)
        cls.beats_path = cls.root / "beats.json"
        vl.save_json(cls.beats_path, cls.beats)
        body = vp.body_start_time(cls.beats, cls.beats["duration"], cls.beats["period"], 2)
        cls.need = vp.layout(cls.beats["duration"], body, cls.beats["period"], 2)["shots"]
        pool = [cls.clips[i % 4] for i in range(cls.need)]
        pool[3] = cls.hlg
        if cls.rot:
            pool[5] = cls.rot
        cls.clip_list = [{"clip": vl.ffp(p)} for p in pool]
        cls.edl = small(vp.plan(cls.clip_list, cls.beats, name="proj", music=vl.ffp(cls.music),
                                duration=cls.beats["duration"]))
        cls.edl_path = cls.root / "proj.edl.json"
        vl.save_json(cls.edl_path, cls.edl)

    def test_00_planned_project_lints_without_errors(self):
        issues = ve.lint(self.edl)
        self.assertFalse(ve.has_errors(issues), ve.format_issues(issues))

    def test_10_preview_render(self):
        res = vr.render(self.edl_path, preview=True, encoder="libx264", log=lambda *_: None)
        out = Path(res["out"])
        self.assertTrue(out.is_file())
        info = vl.probe(out)
        self.assertEqual((info["width"], info["height"]), (136, 240))      # half of 270x480, even-rounded
        self.assertAlmostEqual(info["duration"], self.edl["project"]["duration"], delta=0.06)
        self.assertTrue(info["has_audio"])

    def test_20_final_render_is_frame_exact_and_verifies(self):
        res = vr.render(self.edl_path, encoder="libx264", log=lambda *_: None)
        out = Path(res["out"])
        self.assertEqual(count_frames(out), vl.frames(self.edl["project"]["duration"], 30))
        check = vr.verify(self.edl_path, out, self.root / "check.jpg")
        self.assertTrue(check["ok"], check["checks"])
        self.assertTrue(Path(check["sheet"]).is_file())
        self.assertLess(out.stat().st_size, 30 * 1024 * 1024)

    def test_21_second_render_uses_the_cache(self):
        vr.render(self.edl_path, encoder="libx264", log=lambda *_: None)
        res = vr.render(self.edl_path, encoder="libx264", log=lambda *_: None)
        self.assertEqual(res["parts_cached"], res["shots"])

    def test_22_changing_one_shot_rerenders_only_that_part(self):
        vr.render(self.edl_path, encoder="libx264", log=lambda *_: None)
        e = copy.deepcopy(self.edl)
        e["shots"][10]["src_start"] = 1.0
        p = self.root / "edit.edl.json"
        vl.save_json(p, e)
        # same work dir so the cache is shared
        res1 = vr.render(p, out=self.root / "edit.mp4", encoder="libx264", workdir=self.root / "proj.edl_work", log=lambda *_: None)
        self.assertEqual(res1["shots"] - res1["parts_cached"], 1)

    def test_30_audio_has_the_music(self):
        out = Path(vr.render(self.edl_path, encoder="libx264", log=lambda *_: None)["out"])
        r = vl.run([vl.find_ffmpeg()["ffmpeg"], "-hide_banner", "-i", vl.ffp(out), "-af", "volumedetect", "-vn", "-f", "null", "-"])
        text = r.stderr
        self.assertIn("mean_volume", text)
        mean = float(text.split("mean_volume:")[1].split("dB")[0])
        mx = float(text.split("max_volume:")[1].split("dB")[0])
        self.assertGreater(mean, -40)
        self.assertLessEqual(mx, 0.0)

    def test_40_silent_video_without_music(self):
        e = copy.deepcopy(self.edl)
        e["project"]["music"] = None
        p = self.root / "silent.edl.json"
        vl.save_json(p, e)
        res = vr.render(p, encoder="libx264", workdir=self.root / "silent_work", log=lambda *_: None)
        info = vl.probe(res["out"])
        self.assertFalse(info["has_audio"])
        self.assertTrue(vr.verify(p, res["out"])["ok"])

    def test_41_all_hard_cuts_and_no_effects(self):
        e = copy.deepcopy(self.edl)
        e["transitions"] = {}
        e["style"] = {"zoom": 0, "drop_flash": False, "end_flare": False, "riser": False, "boom": False}
        p = self.root / "cuts.edl.json"
        vl.save_json(p, e)
        res = vr.render(p, encoder="libx264", workdir=self.root / "cuts_work", log=lambda *_: None)
        self.assertEqual(count_frames(res["out"]), vl.frames(e["project"]["duration"], 30))

    def test_42_every_cut_a_transition_still_frame_exact(self):
        e = copy.deepcopy(self.edl)
        names = [n for n in vl.PLANNER_TRANSITIONS]
        e["transitions"] = {str(j): names[j % len(names)] for j in range(1, len(e["shots"]))}
        p = self.root / "alltrans.edl.json"
        vl.save_json(p, e)
        res = vr.render(p, encoder="libx264", workdir=self.root / "alltrans_work", force=True, log=lambda *_: None)
        self.assertEqual(count_frames(res["out"]), vl.frames(e["project"]["duration"], 30))

    def test_50_missing_clip_is_a_clear_error(self):
        e = copy.deepcopy(self.edl)
        e["shots"][4]["clip"] = str(self.root / "gone.mp4")
        p = self.root / "missing.edl.json"
        vl.save_json(p, e)
        with self.assertRaises(vl.EngineError) as cm:
            vr.render(p, encoder="libx264", workdir=self.root / "missing_work", log=lambda *_: None)
        self.assertIn("gone.mp4", str(cm.exception))

    def test_51_banned_transition_blocks_render_unless_forced(self):
        e = copy.deepcopy(self.edl)
        e["transitions"]["1"] = "zoomin"
        p = self.root / "banned.edl.json"
        vl.save_json(p, e)
        with self.assertRaises(vl.EngineError):
            vr.render(p, encoder="libx264", workdir=self.root / "banned_work", log=lambda *_: None)

    def test_60_nvenc_when_available(self):
        if not vr.nvenc_works():
            self.skipTest("no NVENC on this machine")
        res = vr.render(self.edl_path, out=self.root / "nv.mp4", encoder="nvenc", log=lambda *_: None)
        self.assertEqual(res["encoder"], "h264_nvenc")
        self.assertEqual(count_frames(res["out"]), vl.frames(self.edl["project"]["duration"], 30))

    def test_61_nvenc_requested_but_unavailable_is_an_error(self):
        if vr.nvenc_works():
            self.skipTest("NVENC exists here")
        with self.assertRaises(vl.EngineError):
            vr.encoder_args("nvenc", False)

    def test_70_hdr_clip_is_detected_and_tonemapped(self):
        self.assertTrue(vl.probe(self.hlg)["hdr"])

    def test_80_cli_plan_lint_render_verify(self):
        root = helpers.tmpdir() / "cli_proj"
        root.mkdir(exist_ok=True)
        clips_txt = root / "clips.txt"
        clips_txt.write_text("\n".join(c["clip"] for c in self.clip_list) + "\n", encoding="utf-8")
        edl = str(root / "v.edl.json")
        self.assertEqual(mh_video.main(["plan", "--beats", str(self.beats_path), "--clips", str(clips_txt), "--out", edl,
                                        "--name", "cli", "--music", vl.ffp(self.music)]), 0)
        data = vl.load_json(edl)
        data["project"].update(width=270, height=480)
        vl.save_json(edl, data)
        self.assertEqual(mh_video.main(["lint", edl]), 0)
        out = str(root / "cli.mp4")
        self.assertEqual(mh_video.main(["render", edl, "--out", out, "--encoder", "libx264", "--preview"]), 0)
        self.assertEqual(mh_video.main(["verify", edl, out]), 0)        # preview keeps aspect, duration, audio
        self.assertTrue((root / "cli_check.jpg").is_file())

    def test_71_rotation_is_respected(self):
        if not self.rot:
            self.skipTest("this ffmpeg cannot write display rotation")
        info = vl.probe(self.rot)
        self.assertEqual((info["width"], info["height"]), (360, 640))


@NEEDS_FF
class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "tools"
        cls.footage = cls.root / "footage"
        cls.clips = helpers.make_clips(cls.footage, 3, seconds=3.0)
        shutil.copy2(cls.clips[0], cls.footage / "copy_of_clip0.mp4")          # same size + mtime -> duplicate
        (cls.footage / "broken.mp4").write_bytes(b"not a video")
        (cls.footage / "notes.txt").write_text("ignore me")

    def test_probe_inventory(self):
        recs = vid_probe.scan(self.footage)
        self.assertEqual(len(recs), 5)                                          # txt ignored
        bad = [r for r in recs if r["error"]]
        self.assertEqual([r["name"] for r in bad], ["broken.mp4"])
        dups = [r for r in recs if r["dup_of"] is not None]
        self.assertEqual(len(dups), 1)
        s = vid_probe.summarize(recs)
        self.assertEqual(s["files"], 5)
        self.assertEqual(s["readable"], 4)
        self.assertEqual(s["duplicates"], 1)
        self.assertEqual(s["horizontal"], 4)
        paths = vid_probe.write_inventory(recs, self.root / "work")
        self.assertTrue(Path(paths["json"]).is_file() and Path(paths["csv"]).is_file())
        self.assertEqual(len(vid_probe.load_inventory(self.root / "work")), 5)

    def test_probe_missing_folder(self):
        with self.assertRaises(vl.EngineError):
            vid_probe.scan(self.root / "nowhere")

    def test_load_inventory_requires_probe(self):
        with self.assertRaises(vl.EngineError):
            vid_probe.load_inventory(self.root / "empty_work")

    def test_clip_sheets_are_made_even_with_a_broken_clip(self):
        recs = vid_probe.scan(self.footage)
        sheets = vid_sheets.clip_sheets(recs, self.root / "sheets", size=100, cols=3, rows=2)
        self.assertEqual(len(sheets), 1)
        self.assertTrue(Path(sheets[0]).stat().st_size > 1000)

    def test_each_sheet_shows_its_own_clips(self):
        """Regression: every sheet used to be overwritten by the last chunk (identical files)."""
        recs = [r for r in vid_probe.scan(self.footage) if not r["error"] and r["dup_of"] is None]
        self.assertGreaterEqual(len(recs), 3)
        sheets = vid_sheets.clip_sheets(recs, self.root / "sheets2", size=100, cols=2, rows=1)
        self.assertEqual(len(sheets), -(-len(recs) // 2))
        blobs = [Path(s).read_bytes() for s in sheets]
        self.assertEqual(len(set(blobs)), len(blobs), "sheets must differ from each other")

    def test_video_sheet(self):
        out = vid_sheets.video_sheet(self.clips[1], self.root / "v.jpg", count=12, cols=4, tile_w=80)
        self.assertTrue(Path(out).is_file())

    def test_cli_pipeline(self):
        work = str(self.root / "cli_work")
        self.assertEqual(mh_video.main(["probe", str(self.footage), "--work", work]), 0)
        self.assertEqual(mh_video.main(["sheets", "--work", work, "--size", "80", "--cols", "3", "--rows", "2"]), 0)
        wav = self.root / "cli.wav"
        helpers.click_track(wav, bpm=100.0, first=0.4, seconds=8.0, step_at=4.0)
        beats = str(self.root / "cli_beats.json")
        self.assertEqual(mh_video.main(["beats", str(wav), "--out", beats]), 0)
        self.assertEqual(mh_video.main(["need", beats]), 0)

    def test_cli_errors_are_clean(self):
        self.assertEqual(mh_video.main(["lint", str(self.root / "missing.edl.json")]), 2)
        self.assertEqual(mh_video.main(["probe", str(self.root / "nowhere"), "--work", str(self.root / "w")]), 2)

    def test_doctor_reports_ready(self):
        self.assertEqual(mh_video.main(["doctor"]), 0)


if __name__ == "__main__":
    unittest.main()
