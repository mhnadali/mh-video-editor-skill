"""Phase 4 of the selection depth: the offline vision plug-in, people detection in the selection, sound events."""
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import helpers
import vid_story as st
import vid_vision as vv
import vidlib as vl

ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN = ROOT / "plugins" / "vision"
HAS_PLUGIN = vv.plugin_python() is not None


class Core(unittest.TestCase):
    def test_the_plugin_environment_is_found_or_reported(self):
        with helpers_env(MH_VISION_PYTHON=None):
            tmp = helpers.tmpdir() / "novision"
            tmp.mkdir(exist_ok=True)
            self.assertIsNone(vv.plugin_python(tmp))
            self.assertFalse(vv.available(tmp))
            with self.assertRaises(vl.EngineError) as cm:
                vv.command("work", plugin_dir=tmp)
            self.assertIn("setup.py", str(cm.exception))
            exe = tmp / ".venv" / ("Scripts" if os.name == "nt" else "bin")
            exe.mkdir(parents=True, exist_ok=True)
            fake = exe / ("python.exe" if os.name == "nt" else "python")
            fake.write_text("x")
            self.assertEqual(vv.plugin_python(tmp), fake)

    def test_an_environment_variable_overrides_the_location(self):
        f = helpers.tmpdir() / "mypython"
        f.write_text("x")
        with helpers_env(MH_VISION_PYTHON=str(f)):
            self.assertEqual(vv.plugin_python(), f)
        with helpers_env(MH_VISION_PYTHON=str(f) + ".missing"):
            self.assertIsNone(vv.plugin_python())

    def test_the_command_line(self):
        tmp = helpers.tmpdir() / "withvision"
        exe = tmp / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        exe.mkdir(parents=True, exist_ok=True)
        (exe / ("python.exe" if os.name == "nt" else "python")).write_text("x")
        with helpers_env(MH_VISION_PYTHON=None), mock.patch.object(vl, "find_ffmpeg", return_value={"ffmpeg": "FF"}):
            cmd = vv.command("W", only="1-5", onnx="m.onnx", labels="l.txt", frames=7, plugin_dir=tmp)
        self.assertEqual(cmd[1], str(tmp / "vision_run.py"))
        for token in ("--work", "W", "--ffmpeg", "FF", "--frames", "7", "--only", "1-5", "--onnx", "m.onnx", "--labels", "l.txt"):
            self.assertIn(token, cmd)

    def test_load_merge_and_summary(self):
        root = helpers.tmpdir() / "visionload"
        root.mkdir(exist_ok=True)
        self.assertEqual(vv.load(root), {})
        vl.save_json(root / "vision.json", {"clips": {"1": {"faces": 2, "face_area": 0.03, "bodies": 0, "people": True,
                                                              "people_sure": True, "scene_label": "beach"},
                                                       "2": {"faces": 0, "face_area": 0.0, "bodies": 0, "people": False}}})
        v = vv.load(root)
        self.assertEqual(sorted(v), [1, 2])
        scores = {1: {"index": 1}, 2: {"index": 2}, 3: {"index": 3}}
        self.assertEqual(vv.merge_into_scores(scores, v), 2)
        self.assertTrue(scores[1]["person"])
        self.assertTrue(scores[1]["person_sure"])
        self.assertEqual(scores[1]["scene_label"], "beach")
        self.assertFalse(scores[2]["person"])
        self.assertNotIn("person", scores[3], "a clip the plug-in did not see is left alone")
        self.assertIsNone(vv.is_person_clip(scores[3]))
        self.assertTrue(vv.is_person_clip(scores[1]))
        s = vv.summarize(v)
        self.assertEqual((s["clips"], s["with_people"], s["with_faces"], s["sure"]), (2, 1, 1, 1))

    def test_a_failing_plugin_is_reported(self):
        tmp = helpers.tmpdir() / "withvision2"
        exe = tmp / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        exe.mkdir(parents=True, exist_ok=True)
        (exe / ("python.exe" if os.name == "nt" else "python")).write_text("x")
        failed = subprocess.CompletedProcess([], 3, stdout="", stderr="no cascades")
        with helpers_env(MH_VISION_PYTHON=None), mock.patch.object(vl, "find_ffmpeg", return_value={"ffmpeg": "FF"}), \
                mock.patch.object(subprocess, "run", return_value=failed):
            with self.assertRaises(vl.EngineError) as cm:
                vv.run("W", plugin_dir=tmp, log=lambda *_: None)
        self.assertIn("no cascades", str(cm.exception))

    def test_the_cli_commands_exist(self):
        import mh_video
        parser = mh_video.build_parser()
        a = parser.parse_args(["vision", "run", "--work", "w", "--only", "1-3"])
        self.assertEqual((a.frames, a.only), (9, "1-3"))
        self.assertEqual(parser.parse_args(["vision", "status"]).action, "status")


class helpers_env:
    """Set or remove environment variables for a block."""

    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class InTheSelection(unittest.TestCase):
    REC = {"index": 1, "path": "a.mp4", "name": "a.mp4", "taken": "2026-10-08 10:00:00", "taken_epoch": 1.0, "device": "iPhone",
           "duration": 10.0}

    def view(self, score=None, note=None):
        s = {"index": 1, "path": "a.mp4", "quality": 80.0, "energy": 50.0}
        s.update(score or {})
        return st.clip_view(self.REC, s, note or {})

    def test_the_note_decides_then_the_plugin_then_no(self):
        self.assertFalse(self.view()["people"])
        self.assertTrue(self.view({"person": True})["people"])
        self.assertFalse(self.view({"person": False})["people"])
        self.assertTrue(self.view({"person": False}, {"people": True})["people"], "the agent's note beats the plug-in")
        self.assertFalse(self.view({"person": True}, {"people": False})["people"], "and so does a note that says nobody")
        self.assertTrue(self.view({}, {"role": "portrait"})["people"])

    def test_a_big_face_makes_an_unnoted_clip_a_portrait(self):
        self.assertEqual(self.view({"faces": 1, "face_area": 0.06})["role"], "portrait")
        self.assertIsNone(self.view({"faces": 1, "face_area": 0.01})["role"])
        self.assertEqual(self.view({"faces": 1, "face_area": 0.06}, {"role": "action"})["role"], "action")

    def test_the_story_counts_people_the_plugin_found(self):
        inv, sc = [], {}
        for i in range(60):
            inv.append({"index": i, "path": f"c{i}.mp4", "name": f"c{i}.mp4", "duration": 12.0, "dup_of": None, "error": None,
                        "device": "iPhone", "taken": "2026-10-08 06:00:00", "taken_epoch": 1791400000.0 + i * 600.0})
            sc[i] = {"index": i, "path": f"c{i}.mp4", "quality": 70.0 + (i % 9), "energy": 40.0, "best_start": 1.0, "flags": [],
                     "hash": (i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1), "shot_size": ("wide", "medium", "close")[i % 3],
                     "person": i % 3 == 0}
        beats = {"period": 0.6, "t0": 0.2, "duration": 45.0, "beats": [round(0.2 + 0.6 * k, 3) for k in range(74)],
                 "beat_strength": [0.9 if k % 2 == 0 else 0.3 for k in range(74)],
                 "sections": [{"label": "a", "start": 0.2, "end": 45.0, "energy": 0.5}]}
        cfg = {"min_people_share": 0.5, "chapters": [{"name": "a", "from": 0, "to": 59, "weight": 1}]}
        sb = st.select(inv, sc, beats, cfg, None, duration=45.0, pacing="energy")
        self.assertGreaterEqual(sb["coverage"]["people_share"], 0.4)
        sc2 = {i: dict(s, person=False) for i, s in sc.items()}
        none = st.select(inv, sc2, beats, cfg, None, duration=45.0, pacing="energy")
        self.assertEqual(none["coverage"]["people_share"], 0.0)


class SoundEvents(unittest.TestCase):
    def test_a_burst_is_ranked_by_how_far_it_rises_above_the_clip(self):
        import vid_score as vs
        recs = [{"index": i, "path": f"c{i}.mp4", "name": f"c{i}.mp4", "size": 10 + i, "mtime": 1, "duration": 9.0,
                 "has_audio": True, "dup_of": None, "error": None} for i in range(3)]
        fake = {0: {"audio_db": -30.0, "audio_peak_db": -28.0}, 1: {"audio_db": -30.0, "audio_peak_db": -5.0},
                2: {"audio_db": -20.0, "audio_peak_db": -10.0}}

        def one(rec, hw=False):
            i = rec["index"]
            return dict({"index": i, "path": rec["path"], "name": rec["name"], "quality": 80.0, "motion": float(i), "flags": [],
                         "similar_to": None, "hash": i, "best_start": 0.0}, **fake[i])

        with mock.patch.object(vs, "score_clip", one):
            out = {s["index"]: s for s in vs.score_inventory(recs, hw=False)}
        self.assertGreater(out[1]["transient"], out[2]["transient"])
        self.assertGreater(out[2]["transient"], out[0]["transient"])
        self.assertGreater(out[2]["sound"], out[1]["sound"])

    def test_a_sound_spike_helps_a_strong_slot_only(self):
        cs = {"index": 1, "quality": 0.8, "act": 0.5, "sound": None, "role": None, "must": False, "transient": 1.0}
        quiet = dict(cs, transient=0.0)
        w = dict(st.WEIGHTS)
        chapter = {"name": "c", "roles": []}
        self.assertGreater(st.unary_value(cs, 0.8, chapter, w, {}), st.unary_value(quiet, 0.8, chapter, w, {}))
        self.assertEqual(st.unary_value(cs, 0.2, chapter, w, {}) - st.unary_value(quiet, 0.2, chapter, w, {}), 0.0)

    @unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
    @helpers.slow
    def test_volume_stats_of_a_real_clip(self):
        import vid_sfx
        root = helpers.tmpdir() / "soundev"
        root.mkdir(exist_ok=True)
        loud = helpers.make_clip(root / "loud.mp4", "testsrc2", size="160x90", seconds=3.0, audio="anoisesrc=c=brown:a=0.5")
        silent = helpers.make_clip(root / "mute.mp4", "testsrc2", size="160x90", seconds=3.0)
        mean, peak = vid_sfx.volume_stats(vl.ffp(loud), 0.0, 2.0)
        self.assertIsNotNone(mean)
        self.assertGreaterEqual(peak, mean)
        self.assertEqual(vid_sfx.volume_stats(vl.ffp(silent), 0.0, 2.0), (None, None))


PLUGIN_CODE = '''
import sys, json
sys.path.insert(0, r"{plugin}")
import vision_run as vr
assert vr.aggregate([]) == {{"faces": 0, "face_area": 0.0, "face_frames": 0, "bodies": 0, "frames": 0, "people": False,
                            "people_sure": False}}
one = vr.aggregate([{{"faces": 1, "face_area": 0.01, "bodies": 0}}, {{"faces": 0, "face_area": 0.0, "bodies": 0}}])
assert one["people"] and not one["people_sure"], one
two = vr.aggregate([{{"faces": 1, "face_area": 0.01, "bodies": 0}}, {{"faces": 2, "face_area": 0.02, "bodies": 0}}])
assert two["people"] and two["people_sure"], two
big = vr.aggregate([{{"faces": 1, "face_area": 0.05, "bodies": 0}}])
assert big["people_sure"], big
tiny = vr.aggregate([{{"faces": 1, "face_area": 0.001, "bodies": 0}}])
assert not tiny["people"], tiny
assert vr.aggregate([{{"faces": 0, "face_area": 0.0, "bodies": 1}}])["people"] is False, "one body frame is not enough"
assert vr.aggregate([{{"faces": 0, "face_area": 0.0, "bodies": 1}}] * 2)["people"]
assert vr.majority_label(["sea", "sea", "sky"]) == "sea" and vr.majority_label(["a", "b", "c"]) is None and vr.majority_label([]) is None
boxes = [(0, 0, 100, 100), (5, 5, 90, 90), (200, 200, 50, 50)]
assert len(vr._merge(boxes)) == 2
t = vr.sample_times({{"duration": 20.0}}, {{"wins": [[3.0, 80, 1], [10.0, 80, 2]]}}, 5)
assert 3 <= len(t) <= 5 and t == sorted(t) and all(0 <= x <= 20 for x in t), t
assert vr.sample_times({{"duration": 0}}, None, 5) == []
assert vr.parse_only("1-3,7") == {{1, 2, 3, 7}} and vr.parse_only(None) is None
import numpy as np
casc = vr.load_cascades()
assert "face" in casc, casc
blank = np.full((360, 640, 3), 128, dtype=np.uint8)
r = vr.detect(blank, casc)
assert r == {{"faces": 0, "face_area": 0.0, "bodies": 0}}, r
print("plugin ok")
'''


@unittest.skipUnless(HAS_PLUGIN, "the vision plug-in environment is not set up (python plugins/vision/setup.py)")
class Plugin(unittest.TestCase):
    def test_the_plugin_functions(self):
        r = subprocess.run([str(vv.plugin_python()), "-c", PLUGIN_CODE.format(plugin=str(PLUGIN))], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("plugin ok", r.stdout)

    @unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
    @helpers.slow
    def test_the_plugin_writes_vision_json_for_a_work_folder(self):
        root = helpers.tmpdir() / "visionrun"
        root.mkdir(exist_ok=True)
        clips = helpers.make_clips(root / "c", 2, 6.0)
        recs = [{"index": i, "path": vl.ffp(p), "name": p.name, "duration": 6.0, "dup_of": None, "error": None}
                for i, p in enumerate(clips)]
        vl.save_json(root / "inventory.json", {"summary": {}, "clips": recs})
        v = vv.run(root, frames=3, log=lambda *_: None)
        self.assertEqual(sorted(v), [0, 1])
        for res in v.values():
            self.assertFalse(res["people"], "a test pattern has no people")
            self.assertEqual(res["faces"], 0)
            self.assertGreaterEqual(res["frames"], 1)
        again = vv.run(root, frames=3, log=lambda *_: None)
        self.assertEqual(again, v, "the same input gives the same result")


if __name__ == "__main__":
    unittest.main()
