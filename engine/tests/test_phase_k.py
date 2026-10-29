"""Phase K (0.29): the optional content plug-in (SigLIP). The engine side is checked with small hand-made embeddings (no model
needed); the plug-in itself only where it is set up (`setup.py --content`)."""
import base64
import json
import os
import struct
import unittest
from pathlib import Path
from unittest import mock

import helpers
import mh_video
import vid_content as vc
import vid_filter as vf
import vid_hook as vh
import vid_story as st
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def vec(*xs, dim=vc.DIM):
    v = list(xs) + [0.0] * (dim - len(xs))
    return vc.unit(v)


def content_of(clips: dict, texts: dict) -> dict:
    return {"clips": {i: {"mean": v, "frames": [v]} for i, v in clips.items()}, "texts": texts}


class Vectors(unittest.TestCase):
    def test_float16_round_trip_and_cosine(self):
        v = vec(0.6, 0.8)
        raw = base64.b64encode(struct.pack(f"<{len(v)}e", *v)).decode()
        back = vc._vec(raw)                                                         # noqa: SLF001
        self.assertAlmostEqual(vc.dot(back, v), 1.0, places=3)

    def test_zero_shot_and_search(self):
        texts = {"red": vec(1, 0), "blue": vec(0, 1), "a red one": vec(0.9, 0.1)}
        c = content_of({1: vec(1, 0.1), 2: vec(0.1, 1)}, texts)
        p = vc.zero_shot(c, 1, {"red": ["red", "a red one"], "blue": ["blue"]})
        self.assertAlmostEqual(sum(p.values()), 1.0, places=3)
        self.assertGreater(p["red"], 0.9)
        self.assertEqual([i for i, _ in vc.search(c, texts["blue"])], [2, 1])
        self.assertIsNone(vc.zero_shot(c, 9, {"red": ["red"]}))


class Merge(unittest.TestCase):
    def setUp(self):
        # people prompts point along axis 0 ("yes") and 1 ("no"); sizes along 2 / 3 / 4
        texts = {}
        for k, axis in (("yes", 0), ("no", 1)):
            for t in vc.PROMPTS["people"][k]:
                texts[t] = vec(*([0.0] * axis + [1.0]))
        for k, axis in (("wide", 2), ("medium", 3), ("close", 4)):
            for t in vc.PROMPTS["size"][k]:
                texts[t] = vec(*([0.0] * axis + [1.0]))
        clips = {i: vec(1.0, 0, 0, 0, 0.5, 0.0, 0.01 * i) for i in range(5)}          # people, close-up
        clips.update({i: vec(0, 1.0, 0.5, 0, 0, 0.3, 0.01 * i) for i in range(5, 10)})  # no people, wide
        self.c = content_of(clips, texts)
        self.scores = {i: {"index": i, "shot_size": "medium", "person": i == 7, "person_certainty": 0.6 if i == 7 else 0.0}
                       for i in range(10)}
        self.notes = {i: {"role": "portrait"} for i in range(4)}
        self.notes.update({i: {"role": "establishing"} for i in range(5, 9)})

    def test_people_yes_counts_like_a_sure_face_and_no_leaves_the_detector_alone(self):
        vc.merge_into_scores(self.scores, self.c, self.notes)
        self.assertEqual((self.scores[0]["person"], self.scores[0]["person_certainty"], self.scores[0]["person_detector"]),
                         (True, vc.PEOPLE_CERTAINTY, "content"))
        self.assertEqual(self.scores[7]["person_certainty"], 0.6, "a face the detector found is not taken away")
        self.assertFalse(self.scores[6]["person"])

    def test_shot_size_and_guesses_for_clips_without_a_note(self):
        vc.merge_into_scores(self.scores, self.c, self.notes)
        self.assertEqual((self.scores[0]["shot_size"], self.scores[0]["shot_size_measured"], self.scores[0]["shot_size_from"]),
                         ("close", "medium", "content"))
        self.assertEqual(self.scores[9]["shot_size"], "wide")
        self.assertEqual(self.scores[4].get("role_guess"), "portrait", "unnoted clip 4 sits with the noted portraits")
        self.assertEqual(self.scores[9].get("role_guess"), "establishing")
        self.assertNotIn("role_guess", self.scores[0], "a noted role is never guessed over")

    def test_a_label_with_too_few_noted_clips_is_not_guessed(self):
        vc.merge_into_scores(self.scores, self.c, {0: {"role": "detail"}, 1: {"role": "detail"}})
        self.assertNotIn("role_guess", self.scores[5])

    def test_nothing_happens_without_content(self):
        self.assertEqual(vc.merge_into_scores(self.scores, {"clips": {}, "texts": {}}), 0)


class LookAlike(unittest.TestCase):
    def rec(self, i, t, dev="cam"):
        return {"index": i, "taken_epoch": t, "device": dev}

    def test_close_embeddings_flag_but_never_drop(self):
        far = [(1 << 40) - 1] * 3                                                      # hashes far apart: the hash rules say nothing
        sa = {"hashes": [0, 0, 0], "csig": [0] * 12, "brightness": 100.0, "content_vec": vec(1, 0.1)}
        sb = {"hashes": far, "csig": [200] * 12, "brightness": 30.0, "content_vec": vec(1, 0.12)}
        with mock.patch.object(vf, "pair_similarity", return_value={"hash": [40, 40, 40], "colour": 90.0, "brightness": 70.0}):
            self.assertEqual(vf.similarity_level(self.rec(1, 0), self.rec(2, 30), sa, sb), "lookalike")
            self.assertIsNone(vf.similarity_level(self.rec(1, 0), self.rec(2, 30), sa, dict(sb, content_vec=vec(0, 1))))
            self.assertIsNone(vf.similarity_level(self.rec(1, 0), self.rec(2, 99999), sa, sb), "outside the time window")


class Hook(unittest.TestCase):
    def test_guessed_readings_rank_unread_clips_a_little_lower(self):
        inv, sc = __import__("test_story").world(30)
        cfg = __import__("test_story").cfg_of(__import__("test_story").chap("a", 0, 29))
        sb = st.select(inv, sc, __import__("test_story").BEATS, cfg, None, duration=15.0, pacing="energy")
        used = {r["clip"] for r in sb["rows"]}
        a, b = [i for i in range(30) if i not in used][:2]
        notes = {inv[a]["path"]: {"role": "action", "mood": "intense"}}
        sc[b]["role_guess"], sc[b]["mood_guess"] = "action", "intense"
        rank = vh.hook_rank(inv, sc, sb, cfg, notes, top=10)
        got = {c["clip"]: c for c in rank["candidates"]}
        self.assertIn(b, got)
        self.assertTrue(got[b]["why"][0].startswith("guessed"))
        self.assertLess(got[b]["score"], got[a]["score"] + 0.25)


class Files(unittest.TestCase):
    def test_a_changed_file_or_another_model_revision_is_not_read(self):
        root = helpers.tmpdir() / "phase_k_load"
        root.mkdir(parents=True, exist_ok=True)
        v = vec(1, 0)
        b = base64.b64encode(struct.pack(f"<{vc.DIM}e", *v)).decode()
        data = {"revision": vc.REVISION, "clips": {"0": {"key": ["a.mp4", 10, 1, []], "frames": b, "mean": b, "n": 1}}, "texts": {}}
        (root / vc.CONTENT_FILE).write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(list(vc.load(root, [{"index": 0, "path": "a.mp4", "size": 10}])["clips"]), [0])
        self.assertEqual(vc.load(root, [{"index": 0, "path": "b.mp4", "size": 10}])["clips"], {}, "number 0 is another file now")
        (root / vc.CONTENT_FILE).write_text(json.dumps(dict(data, revision="old")), encoding="utf-8")
        self.assertEqual(vc.load(root)["clips"], {})

    def test_the_job_tone_maps_hlg_and_looks_at_three_moments(self):
        job = vc.build_job([{"index": 0, "path": "x.mov", "duration": 10.0, "hdr": True, "size": 1, "mtime": 1},
                            {"index": 1, "path": "y.mov", "duration": 10.0, "dup_of": 0}], ["a text"])
        self.assertEqual(len(job["clips"]), 1)
        self.assertEqual(job["clips"][0]["times"], [2.0, 5.0, 8.0])
        self.assertIn("tonemap", job["clips"][0]["vf"])
        self.assertEqual((job["revision"], job["texts"]), (vc.REVISION, ["a text"]))

    def test_cli(self):
        p = mh_video.build_parser()
        self.assertEqual(p.parse_args(["content", "search", "a car", "--work", "W"]).query, "a car")
        self.assertIn("content", mh_video.PREPARE_STEPS)
        with mock.patch.dict(os.environ, {"MH_CONTENT_PYTHON": str(helpers.tmpdir() / "none.exe")}):
            self.assertIsNone(vc.content_python())


@NEEDS_FF
@unittest.skipUnless(vc.content_python(), "the content plug-in is not set up")
class Plugin(unittest.TestCase):
    def test_a_red_clip_is_described_as_red(self):
        root = helpers.tmpdir() / "phase_k_plugin"
        root.mkdir(parents=True, exist_ok=True)
        clip = root / "red.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x320:d=2:r=10", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", vl.ffp(clip)])
        st_ = os.stat(clip)
        inv = [{"index": 0, "path": str(clip), "duration": 2.0, "size": st_.st_size, "mtime": int(st_.st_mtime)}]
        c = vc.run(root, inv, extra_texts=["a plain red picture", "a plain blue picture"], log=lambda *_: None)
        p = vc.zero_shot(c, 0, {"red": ["a plain red picture"], "blue": ["a plain blue picture"]})
        self.assertGreater(p["red"], p["blue"])


if __name__ == "__main__":
    unittest.main()
