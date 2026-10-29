"""Phase A (real-data validation): fixed picture hashes, look-alike flags, people certainty, calibration tool."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
import test_story as ts
import vid_filter as vf
import vid_score
import vid_story as st
import vid_vision as vv
import vidlib as vl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import calibrate_selection as cal  # noqa: E402

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def rec(i, device="iPhone", t=0.0):
    return {"index": i, "path": f"c{i}.mp4", "device": device, "taken_epoch": 1.0e9 + t, "duration": 10.0}


def score(h, hashes, col=100, br=120.0, **kw):
    return dict({"hash": h, "hashes": hashes, "csig": [col] * 12, "brightness": br}, **kw)


class Similarity(unittest.TestCase):
    def test_strict_lookalike_and_different(self):
        a = score(0, [0, 0, 0])
        same = score(0, [0b1111, 0b1111, 0b1111])                         # 4 bits apart on every frame
        alike = score(0, [(1 << 15) - 1] * 3)                                # 15 bits apart: only a look-alike
        far = score(0, [(1 << 30) - 1] * 3)
        self.assertEqual(vf.similarity_level(rec(1), rec(2, t=5), a, same), "same")
        self.assertEqual(vf.similarity_level(rec(1), rec(2, t=5), a, alike), "lookalike")
        self.assertIsNone(vf.similarity_level(rec(1), rec(2, t=5), a, far))
        self.assertIsNone(vf.similarity_level(rec(1), rec(2, t=5000), a, same), "too far apart in time")
        self.assertTrue(vf.same_moment(rec(1), rec(2, t=5), a, same))
        self.assertFalse(vf.same_moment(rec(1), rec(2, t=5), a, alike), "a look-alike is never dropped")

    def test_the_light_must_agree_for_a_strict_match(self):
        a = score(0, [0, 0, 0], br=100.0)
        dim = score(0, [0b111] * 3, br=100.0 + 25.0)                       # was 30 in 0.16: it joined two cameras of one drive
        self.assertEqual(vf.DEFAULT_DEDUPE["brightness"], 10.0)
        self.assertNotEqual(vf.similarity_level(rec(1), rec(2, t=5), a, dim), "same")

    def test_lookalikes_among_the_chosen_clips(self):
        inv = [rec(0), rec(1, t=5), rec(2, t=10)]
        sc = {0: score(0, [0] * 3), 1: score(0, [(1 << 15) - 1] * 3), 2: score(0, [(1 << 40) - 1] * 3)}
        self.assertEqual(vf.lookalikes([0, 1, 2], inv, sc), [[0, 1]])

    def test_the_storyboard_warns_when_two_chosen_shots_look_alike(self):
        inv, sc = ts.world(60)
        h = sc[10]["hash"]
        sc[11] = dict(sc[11], hash=h, hashes=[h ^ ((1 << 14) - 1)] * 3, csig=sc[10].get("csig") or [100] * 12, brightness=120.0)
        sc[10] = dict(sc[10], hash=h, hashes=[h] * 3, csig=sc[11]["csig"], brightness=120.0)
        cfg = ts.cfg_of(ts.chap("a", 0, 59), must=[])
        cfg["chapters"][0]["must"] = [10, 11]
        sb = st.select(inv, sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")
        clips = [r["clip"] for r in sb["rows"]]
        self.assertTrue(10 in clips and 11 in clips)
        self.assertTrue(any("look alike" in w for w in sb["warnings"]), sb["warnings"])
        self.assertTrue(all("lookalike" in r for r in sb["rows"]))
        flagged = [r for r in sb["rows"] if r["lookalike"]]
        self.assertEqual(len(flagged), 2)


class People(unittest.TestCase):
    def test_certainty_follows_the_source(self):
        self.assertEqual(st._people_certainty({"people": True}, {}), 1.0)               # noqa: SLF001
        self.assertEqual(st._people_certainty({"role": "portrait"}, {}), 1.0)            # noqa: SLF001
        self.assertEqual(st._people_certainty({"people": False}, {"person_sure": True}), 0.0)    # noqa: SLF001
        self.assertEqual(st._people_certainty({}, {"person_sure": True, "person": True}), 0.6)   # noqa: SLF001
        self.assertEqual(st._people_certainty({}, {"person": True}), 0.25)               # noqa: SLF001
        self.assertEqual(st._people_certainty({}, {}), 0.0)                             # noqa: SLF001

    def test_the_vision_rule_is_read_from_the_raw_counts(self):
        scores = {1: {"path": "a"}, 2: {"path": "b"}, 3: {"path": "c"}}
        v = {1: {"face_frames": 3, "bodies": 0, "people": False, "people_sure": False, "path": "a"},
             2: {"face_frames": 1, "bodies": 0, "path": "b"},
             3: {"face_frames": 0, "bodies": 1, "path": "c"}}
        vv.merge_into_scores(scores, v)
        self.assertTrue(scores[1]["person"] and scores[1]["person_sure"], "raw counts win over stored flags")
        self.assertTrue(scores[2]["person"] and not scores[2]["person_sure"])
        self.assertTrue(scores[3]["person"] and not scores[3]["person_sure"])

    def test_coverage_separates_confirmed_people(self):
        rows = [{"device": "d", "role": None, "people": True, "people_certainty": 1.0},
                {"device": "d", "role": None, "people": True, "people_certainty": 0.25},
                {"device": "d", "role": None, "people": False, "people_certainty": 0.0}]
        cv = st.coverage(rows, [])
        self.assertEqual(cv["people_share"], 0.67)
        self.assertEqual(cv["people_confirmed_share"], 0.33)

    def test_a_weak_sighting_pulls_less_than_a_noted_person(self):
        w = dict(st.WEIGHTS)
        ctx = {"people_share": 0.0, "min_people_share": 0.5, "w": w, "device_library_share": {}, "device_share": {},
               "picked_so_far": 0, "roles": set(), "topic_count": {}, "max_per_topic": 3, "chapter_span": 1.0}
        base = dict(index=1, quality=0.8, act=0.5, sound=None, hash=1, size="wide", role=None, topic=None, people=True, epoch=0.0,
                    device="d")
        sure = st._components(dict(base, people_certainty=1.0), 0.5, [], ctx)             # noqa: SLF001
        weak = st._components(dict(base, people_certainty=0.25), 0.5, [], ctx)            # noqa: SLF001
        self.assertGreater(sure["bonus"], weak["bonus"])


class Calibration(unittest.TestCase):
    def world(self):
        inv = {1: rec(1), 2: rec(2, t=5), 3: rec(3, t=8), 4: rec(4, t=9)}
        sc = {1: score(0, [0, 0, 0]), 2: score(0, [0b11] * 3), 3: score(0, [(1 << 30) - 1] * 3), 4: score(0, [(1 << 31) - 1] * 3)}
        for i in sc:
            sc[i].update(quality=70, person=i in (1, 3), person_sure=i == 1)
        return inv, sc

    def test_confusion(self):
        c = cal.confusion([True, True, False, False], [True, False, True, False])
        self.assertEqual((c["tp"], c["fp"], c["fn"], c["tn"]), (1, 1, 1, 1))
        self.assertEqual(c["precision"], 0.5)
        self.assertEqual(cal.confusion([False], [False])["precision"], None)

    def test_pairs_are_judged_and_swept(self):
        inv, sc = self.world()
        pairs = [{"a": 1, "b": 2, "same": True}, {"a": 1, "b": 3, "same": False}]
        res = cal.judge_pairs(pairs, inv, sc)
        self.assertEqual(res["strict"]["tp"], 1)
        self.assertEqual(res["strict"]["fp"], 0)
        sweep = cal.sweep(pairs, inv, sc)
        self.assertTrue(sweep and all(r["fp"] == 0 for r in sweep))
        self.assertTrue(all(r["tp"] == 1 for r in sweep[:3]))

    def test_people_are_judged(self):
        _inv, sc = self.world()
        res = cal.judge_people([{"clip": 1, "people": True}, {"clip": 2, "people": False}, {"clip": 3, "people": False}], sc)
        self.assertEqual((res["person"]["tp"], res["person"]["fp"]), (1, 1))
        self.assertEqual((res["person_sure"]["tp"], res["person_sure"]["fp"]), (1, 0))

    def test_the_command_line(self):
        inv, sc = self.world()
        with tempfile.TemporaryDirectory() as d:
            vl.save_json(Path(d) / "inventory.json", {"clips": list(inv.values())})
            vl.save_json(Path(d) / "scores.json", {"scores": [dict(s, index=i) for i, s in sc.items()]})
            vl.save_json(Path(d) / "labels.json", {"pairs": [{"a": 1, "b": 2, "same": True}, {"a": 1, "b": 3, "same": False}],
                                                    "people": [{"clip": 1, "people": True}]})
            self.assertEqual(cal.main(["--work", d, "--labels", str(Path(d) / "labels.json"), "--json", str(Path(d) / "out.json")]), 0)
            out = json.loads((Path(d) / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(out["pairs"], 2)
        self.assertIn("people", out)


@NEEDS_FF
@helpers.slow
class RealMedia(unittest.TestCase):
    """The bug that real footage showed: the three picture hashes of a window were ONE frame repeated."""

    def clip(self, name, source):
        root = helpers.tmpdir() / "phase_a"
        root.mkdir(exist_ok=True)
        path = root / name
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", source, "-t", "6", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(path)])
        return path

    def record(self, path):
        return {"index": 0, "path": vl.ffp(path), "name": path.name, "size": 1, "mtime": 1, "duration": 6.0, "has_audio": False}

    def test_a_moving_clip_has_three_different_hashes_and_a_still_one_has_one(self):
        moving = vid_score.score_clip(self.record(self.clip("zoom.mp4", "mandelbrot=s=320x180:r=30")))
        self.assertEqual(moving["v"], vid_score.SCHEMA)
        self.assertEqual(len(set(moving["hashes"])), 3, moving["hashes"])
        still = vid_score.score_clip(self.record(self.clip("bars.mp4", "smptebars=s=320x180:r=30")))
        self.assertEqual(len(set(still["hashes"])), 1)

    def test_an_older_entry_is_repaired_without_measuring_the_clip_again(self):
        r = self.record(self.clip("zoom2.mp4", "mandelbrot=s=320x180:r=30"))
        fresh = vid_score.score_clip(r)
        old = dict(fresh, v=3, hashes=[fresh["hashes"][1]] * 3)
        old.pop("audio_peak_db", None)
        fixed = vid_score.top_up(old, r)
        self.assertEqual(fixed["hashes"], fresh["hashes"])
        self.assertEqual(fixed["hash"], fresh["hash"])
        self.assertEqual(fixed["v"], vid_score.SCHEMA)
        calls = []
        orig = vid_score.score_clip
        vid_score.score_clip = lambda rr, hw=False: calls.append(rr) or orig(rr, hw)
        try:
            out = vid_score.score_inventory([dict(r, size=old["size"], mtime=old["mtime"])], hw=False, previous={r["path"]: old})
        finally:
            vid_score.score_clip = orig
        self.assertEqual(calls, [], "the cached entry was topped up, not measured again")
        self.assertEqual(out[0]["hashes"], fresh["hashes"])


if __name__ == "__main__":
    unittest.main()
