"""Phase 4 end to end: every new capability in ONE edit, in two formats, through the QA gate."""
import copy
import unittest
from pathlib import Path

import helpers
import mh_video
import vid_beats
import vid_color as vc
import vid_edl as ve
import vid_plan as vp
import vid_qa as qa
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def count_frames(path) -> int:
    r = vl.run([vl.find_ffmpeg()["ffprobe"], "-v", "error", "-count_frames", "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", vl.ffp(path)])
    return int(r.stdout.strip().rstrip(","))


@NEEDS_FF
@helpers.requires_filters(*helpers.ALL_FEATURE_FILTERS)
@helpers.slow
class Everything(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "p4all"
        cls.root.mkdir(parents=True, exist_ok=True)
        ff = vl.find_ffmpeg()["ffmpeg"]
        cls.clips = []
        for i, (src, br, hz) in enumerate((("testsrc2", -0.15, 300), ("smptebars", 0.1, 800), ("rgbtestsrc", 0.0, 1500), ("testsrc", 0.15, 2200))):
            p = cls.root / f"c{i}.mp4"
            vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=s=360x640:r=30:d=6", "-f", "lavfi", "-i", f"sine=f={hz}:d=6",
                    "-vf", f"eq=brightness={br}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)])
            cls.clips.append(p)
        cls.shaky = cls.root / "shaky.mp4"
        vl.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                "color=c=gray:s=960x540:r=30:d=6,geq=lum='128+70*sin(X/13)*sin(Y/17)':cb=128:cr=128,"
                "crop=w=640:h=360:x='160+22*sin(n*0.9)':y='90+16*cos(n*1.3)'", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.shaky)])
        cls.music = cls.root / "m.wav"
        helpers.click_track(cls.music, bpm=120, first=0.5, seconds=10.0, step_at=5.0)
        beats = vid_beats.analyse(cls.music)
        body = vp.body_start_time(beats, beats["duration"], beats["period"], 2)
        need = vp.layout(beats["duration"], body, beats["period"], 2)["shots"]
        pool = [vl.ffp(cls.clips[i % 4]) for i in range(need)]
        pool[3] = vl.ffp(cls.shaky)
        edl = vp.plan([{"clip": c} for c in pool], beats, name="all", music=vl.ffp(cls.music), duration=beats["duration"])
        edl["project"].update(width=270, height=480)
        edl["style"].update({"grade": {"look": "desert-gold", "strength": 0.8}, "end_flare": False,
                             "sound_design": {"whoosh": True}, "ambient": {"level_db": -14}})
        bounds = ve.timeline(edl)
        edl["shots"][3]["stabilize"] = "light"
        edl["shots"][5]["speed"] = {"from": 1.0, "to": 0.5}
        edl["shots"][7]["correct"] = {"ev": 0.2, "gain": [1.03, 1.0, 0.97]}
        edl["shots"][9]["reframe"] = "blur-fill"
        edl["shots"][11]["focus"] = [0.3, 0.5]
        edl["texts"] = [{"text": "کویر مرنجاب", "start": 0.3, "end": 2.2, "position": "title"},
                        {"text": "Maranjab | Oct 2026", "start": 0.6, "end": 2.2, "position": "bottom", "style": "subtitle"}]
        cls.edl = edl
        cls.path = cls.root / "all.edl.json"
        vl.save_json(cls.path, edl)

    def test_00_lint_is_clean_apart_from_clip_reuse(self):
        issues = ve.lint(self.edl)
        # shot 5 slows a 30 fps test clip to 0.5x on purpose: the frames are interpolated, which lint rightly reports
        self.assertIn("slowmo-invented-frames", [i["code"] for i in issues])
        issues = [i for i in issues if i["code"] not in ("clip-reused", "slowmo-invented-frames")]
        self.assertEqual(issues, [], ve.format_issues(issues))

    def test_10_vertical_final_passes_the_gate_with_everything_on(self):
        res = vr.render(self.path, out=self.root / "all.mp4", encoder="libx264", workdir=self.root / "work", qa=True, log=lambda *_: None)
        self.assertNotEqual(res["qa"], "fail")
        self.assertEqual(count_frames(res["out"]), vl.frames(self.edl["project"]["duration"], 30))
        info = vl.probe(res["out"])
        self.assertEqual((info["width"], info["height"]), (270, 480))
        self.assertTrue(info["has_audio"])
        rep = vl.load_json(self.root / "all.qa.json")
        content = next(c["msg"] for c in rep["checks"] if c["id"] == "content")
        for part in ("desert-gold", "speed-changed", "stabilised", "text", "sound design", "ambient"):
            self.assertIn(part, content)
        self.assertAlmostEqual(rep["audio"]["lufs"], -14.0, delta=1.8)

    def test_20_the_same_edit_in_16x9_and_1x1(self):
        for fmt, size in (("youtube-16x9", (960, 540)), ("square-1x1", (540, 540))):
            res = vr.render(self.path, out=self.root / f"all_{fmt}.mp4", encoder="libx264", workdir=self.root / "work", preview=True,
                            fmt=fmt, qa=True, log=lambda *_: None)
            info = vl.probe(res["out"])
            self.assertEqual((info["width"], info["height"]), size, fmt)
            self.assertNotEqual(res["qa"], "fail", fmt)
            self.assertEqual(count_frames(res["out"]), vl.frames(self.edl["project"]["duration"], 30))

    def test_30_second_render_is_nearly_all_cache(self):
        vr.render(self.path, out=self.root / "out_first.mp4", encoder="libx264", workdir=self.root / "work", log=lambda *_: None)
        again = vr.render(self.path, out=self.root / "out_second.mp4", encoder="libx264", workdir=self.root / "work", log=lambda *_: None)
        self.assertEqual(again["parts_cached"], again["shots"])

    def test_40_changing_one_shot_rerenders_only_that_shot(self):
        vr.render(self.path, out=self.root / "out_d1.mp4", encoder="libx264", workdir=self.root / "work", log=lambda *_: None)
        e = copy.deepcopy(self.edl)
        e["shots"][8]["grade"] = "teal-orange"
        p = self.root / "edit.edl.json"
        vl.save_json(p, e)
        res = vr.render(p, out=self.root / "out_d2.mp4", encoder="libx264", workdir=self.root / "work", log=lambda *_: None)
        self.assertEqual(res["shots"] - res["parts_cached"], 1)

    def test_50_phase4_edl_still_roundtrips_through_the_cli(self):
        self.assertEqual(mh_video.main(["lint", str(self.path), "--no-files"]), 0)
        self.assertEqual(mh_video.main(["sfx", "plan", str(self.path)]), 0)
        self.assertEqual(mh_video.main(["color", "plan", str(self.path), "--only", "0-2"]), 0)

    def test_60_the_gate_notices_when_a_phase4_feature_breaks_the_picture(self):
        """A grade that crushes everything to black must not slip through: the gate has to say so."""
        e = copy.deepcopy(self.edl)
        e["style"]["grade"] = {"look": {"exposure": -4.0, "contrast": 0.3, "saturation": 0.8}}
        e["texts"] = []
        p = self.root / "dark.edl.json"
        vl.save_json(p, e)
        res = vr.render(p, out=self.root / "dark.mp4", encoder="libx264", workdir=self.root / "work_dark", qa=True, log=lambda *_: None)
        rep = vl.load_json(self.root / "dark.qa.json")
        lum = next(c["msg"] for c in rep["checks"] if c["id"] == "luma-range")
        self.assertIn("luma average", lum)
        avg = float(lum.split("luma average ")[1].split(" ")[0])
        self.assertLess(avg, 45)                                                       # visible in the numbers the report carries


if __name__ == "__main__":
    unittest.main()
