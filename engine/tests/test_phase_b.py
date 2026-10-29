"""Phase B (content understanding): the YuNet reading, the shot-size rule, the drop check, the model download check."""
import hashlib
import importlib.util
import os
import random
import unittest
from pathlib import Path
from unittest import mock

import helpers
import test_story as ts
import vid_editorial as ed
import vid_score
import vid_story as st
import vid_vision as vv
import vidlib as vl

ROOT = Path(__file__).resolve().parent.parent.parent
NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


class Yunet(unittest.TestCase):
    def merged(self, **v):
        scores = {1: {"path": "a"}}
        vv.merge_into_scores(scores, {1: dict({"path": "a", "bodies": 0}, **v)})
        return scores[1]

    def test_yunet_sightings_count_for_more_than_cascade_sightings(self):
        weak_y = self.merged(detector="yunet", face_frames=1, face_area=0.01)
        sure_y = self.merged(detector="yunet", face_frames=2, face_area=0.01)
        weak_c = self.merged(detector="cascades", face_frames=1, face_area=0.01)
        sure_c = self.merged(detector="cascades", face_frames=3, face_area=0.01)
        self.assertEqual((weak_y["person_certainty"], sure_y["person_certainty"]), (0.6, 0.9))
        self.assertEqual((weak_c["person_certainty"], sure_c["person_certainty"]), (0.25, 0.6))
        self.assertTrue(sure_y["person_sure"] and not weak_y["person_sure"])
        self.assertEqual(sure_y["person_detector"], "yunet")

    def test_a_tiny_face_in_several_frames_is_not_sure(self):
        far = self.merged(detector="yunet", face_frames=5, face_area=0.003)            # a mirror or a far figure
        self.assertTrue(far["person"] and not far["person_sure"])

    def test_no_face_means_no_person_and_old_results_read_as_cascades(self):
        none = self.merged(detector="yunet", face_frames=0, face_area=0.0)
        self.assertFalse(none["person"])
        self.assertEqual(none["person_certainty"], 0.0)
        old = self.merged(face_frames=1, face_area=0.01)                                  # no detector key: written by 0.16
        self.assertEqual(old["person_detector"], "cascades")

    def test_the_story_uses_the_measured_certainty(self):
        self.assertEqual(st._people_certainty({}, {"person": True, "person_certainty": 0.9}), 0.9)       # noqa: SLF001
        self.assertEqual(st._people_certainty({"people": True}, {"person_certainty": 0.9}), 1.0)         # noqa: SLF001
        self.assertEqual(st._people_certainty({"people": False}, {"person_certainty": 0.9}), 0.0)        # noqa: SLF001


class ShotSize(unittest.TestCase):
    def test_flat_is_wide_and_busy_is_medium(self):
        flat = bytes([128]) * (48 * 27)
        rng = random.Random(1)
        busy = bytes(rng.randrange(0, 256) for _ in range(48 * 27))
        self.assertEqual(ed.shot_size(flat), "wide")
        self.assertEqual(ed.shot_size(busy), "medium")

    def test_a_large_face_makes_a_close_up_unless_the_notes_say_otherwise(self):
        rec = {"index": 1, "path": "a.mp4", "duration": 10.0}
        sc = {"quality": 80, "energy": 50, "shot_size": "medium", "face_area": 0.08, "person_certainty": 0.9}
        self.assertEqual(st.clip_view(rec, sc, {})["size"], "close")
        self.assertEqual(st.clip_view(rec, sc, {"shot_size": "wide"})["size"], "wide")
        self.assertEqual(st.clip_view(rec, dict(sc, person_certainty=0.25), {})["size"], "medium", "a doubtful face decides nothing")
        self.assertEqual(st.clip_view(rec, dict(sc, face_area=0.01), {})["size"], "medium")


class Drop(unittest.TestCase):
    def test_a_calm_shot_on_the_drop_is_reported(self):
        inv, sc = ts.world(40)
        beats = dict(ts.BEATS, drop=3.0)
        for i in range(40):
            sc[i]["energy"] = 5.0
        rep = st.audit_edl(ts.edl_for([0, 1, 2, 3, 4, 5]), inv, sc, beats)
        self.assertTrue(any(f["code"] == "drop-weak" for f in rep["findings"]), rep["findings"])
        self.assertEqual(rep["stats"]["drop_shot"]["clip"], 3)
        for i in range(40):
            sc[i]["energy"] = 90.0
        rep = st.audit_edl(ts.edl_for([0, 1, 2, 3, 4, 5]), inv, sc, beats)
        self.assertFalse(any(f["code"] == "drop-weak" for f in rep["findings"]))

    def test_no_drop_no_check(self):
        inv, sc = ts.world(10)
        rep = st.audit_edl(ts.edl_for([0, 1, 2]), inv, sc, dict(ts.BEATS))
        self.assertNotIn("drop_shot", rep["stats"])


class Setup(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("vision_setup", ROOT / "plugins" / "vision" / "setup.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_the_downloaded_model_is_only_saved_if_its_hash_matches(self):
        mod = self.load()
        self.assertEqual(len(mod.MODEL_SHA256), 64)
        self.assertTrue(mod.MODEL_URL.startswith("https://github.com/opencv/opencv_zoo/"))
        saved = []
        with mock.patch("urllib.request.urlopen") as op, mock.patch.object(Path, "write_bytes", lambda self, data: saved.append(data)), \
                mock.patch.object(Path, "mkdir"):
            op.return_value.read.return_value = b"not the model"
            self.assertEqual(mod.fetch_model(), 1)
            self.assertEqual(saved, [])
            real = b"model bytes"
            with mock.patch.object(mod, "MODEL_SHA256", hashlib.sha256(real).hexdigest()):
                op.return_value.read.return_value = real
                self.assertEqual(mod.fetch_model(), 0)
            self.assertEqual(saved, [real])

    def test_the_model_folder_is_not_committed(self):
        self.assertIn("models/", (ROOT / "plugins" / "vision" / ".gitignore").read_text(encoding="utf-8"))


@NEEDS_FF
@helpers.slow
class TopUp(unittest.TestCase):
    def test_a_schema_4_entry_only_gets_its_shot_size_recomputed(self):
        root = helpers.tmpdir() / "phase_b"
        root.mkdir(exist_ok=True)
        path = root / "m.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "mandelbrot=s=320x180:r=30", "-t", "6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
        rec = {"index": 0, "path": vl.ffp(path), "name": "m.mp4", "size": 1, "mtime": 1, "duration": 6.0, "has_audio": False}
        fresh = vid_score.score_clip(rec)
        old = dict(fresh, v=4, shot_size="close")
        fixed = vid_score.top_up(old, rec)
        self.assertEqual(fixed["shot_size"], fresh["shot_size"])
        self.assertEqual(fixed["v"], vid_score.SCHEMA)
        self.assertEqual(fixed["hashes"], old["hashes"], "a schema 4 entry keeps its hashes")


if __name__ == "__main__":
    unittest.main()
