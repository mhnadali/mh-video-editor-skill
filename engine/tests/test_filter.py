"""Phase 1 of the selection depth: hard filters (short, shaky, covered ...), and "same moment" clusters (keep the best take)."""
import unittest

import helpers
import vid_editorial as ed
import vid_filter as vf
import vid_story as st
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")

W, H = ed.GW, ed.GH


def textured(shift=0):
    """A fixed texture, optionally shifted horizontally."""
    return bytes(((x + shift) * 37 + y * 11) % 200 for y in range(H) for x in range(W))


def rec(i, minute=0.0, dur=12.0, device="iPhone", dup=None, error=None, second=0):
    return {"index": i, "path": f"clip{i}.mp4", "name": f"clip{i}.mp4", "duration": dur, "dup_of": dup, "error": error,
            "device": device, "taken": "2026-10-08 10:00:00", "taken_epoch": 1791400000.0 + minute * 60.0 + second}


def sc(i, q=80.0, h=None, bright=120.0, csig=None, shake=0.5, tex=5.0, flags=None, **kw):
    base = (i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1) if h is None else h
    s = {"index": i, "path": f"clip{i}.mp4", "quality": q, "hash": base, "hashes": [base, base, base], "brightness": bright,
         "csig": csig or [100] * 12, "shake": shake, "texture": tex, "flags": flags or [], "blur": 3.0, "scene": "desert",
         "energy": 50.0, "best_start": 1.0}
    s.update(kw)
    return s


class Usable(unittest.TestCase):
    def test_a_clip_must_last_about_five_seconds(self):
        for dur, ok in ((5.0, True), (4.95, True), (4.8, False), (3.0, False), (12.0, True), (0.9, False)):
            why = vf.usable(rec(0, dur=dur), sc(0))
            self.assertEqual("short" in why, not ok, dur)
        self.assertEqual(vf.usable(rec(0, dur=3.0), sc(0), cfg={"min_duration": 2.0}), [])
        self.assertEqual(vf.usable(rec(0, dur=1.0), sc(0), cfg={"min_duration": 0.0}), [])

    def test_each_reason_is_reported(self):
        self.assertEqual(vf.usable(rec(0, error="bad"), sc(0)), ["unreadable"])
        self.assertEqual(vf.usable(rec(0, dup=3), sc(0)), ["copy"])
        self.assertIn("skipped", vf.usable(rec(0), sc(0), {"skip": True}))
        self.assertIn("not-scored", vf.usable(rec(0), None))
        for flag in ("dark", "blurry"):
            self.assertIn(flag, vf.usable(rec(0), sc(0, flags=[flag])))
        self.assertNotIn("static", vf.usable(rec(0), sc(0, flags=["static"])), "a still shot (a portrait) is allowed")
        self.assertIn("internal-cut", vf.usable(rec(0), sc(0, internal_cut=True)))
        self.assertIn("shaky", vf.usable(rec(0), sc(0, shake=4.5)))
        self.assertNotIn("shaky", vf.usable(rec(0), sc(0, shake=2.0)))
        self.assertIn("covered", vf.usable(rec(0), sc(0, tex=0.2, bright=30.0)))
        self.assertNotIn("covered", vf.usable(rec(0), sc(0, tex=0.2, bright=150.0)), "a bright flat sky is not a covered lens")
        self.assertIn("low-quality", vf.usable(rec(0), sc(0, q=20.0)))
        self.assertEqual(vf.usable(rec(0), sc(0)), [])

    def test_a_clip_the_user_insists_on_skips_the_soft_reasons_only(self):
        bad = sc(0, shake=5.0, flags=["blurry"])
        self.assertEqual(vf.usable(rec(0), bad, must=True), [])
        self.assertIn("short", vf.usable(rec(0, dur=2.0), bad, must=True))
        self.assertIn("skipped", vf.usable(rec(0), bad, {"skip": True}, must=True))

    def test_filters_can_be_switched_off(self):
        cfg = {"block_shaky": False, "block_flags": [], "min_quality": 0.0}
        self.assertEqual(vf.usable(rec(0), sc(0, shake=9.0, flags=["blurry"], q=10.0), cfg=cfg), [])


class SameMoment(unittest.TestCase):
    def pair(self, **kw):
        a, b = rec(0, 0), rec(1, 0, second=kw.pop("gap", 30))
        sa = sc(0, h=0b1011)
        sb = sc(1, h=kw.pop("h", 0b1011), **kw)
        return vf.same_moment(a, b, sa, sb)

    def test_the_same_picture_at_the_same_time_is_one_moment(self):
        self.assertTrue(self.pair())
        self.assertTrue(self.pair(h=0b1011 ^ 0b111))                    # 3 bits apart
        self.assertFalse(self.pair(h=(1 << 60) - 1), "a different picture is a different moment")

    def test_time_colour_and_light_must_agree(self):
        self.assertFalse(self.pair(gap=600), "ten minutes apart")
        self.assertFalse(self.pair(csig=[200, 40, 40] * 4), "same shapes but other colours")
        self.assertFalse(self.pair(bright=200.0), "the light differs")

    def test_two_of_the_three_frames_must_match(self):
        a, b = rec(0), rec(1, second=10)
        sa = sc(0)
        sb = sc(1)
        sa["hashes"] = [0b1, 0b10, 1 << 62]
        sb["hashes"] = [0b1, 0b10, (1 << 62) - 1]
        self.assertTrue(vf.same_moment(a, b, sa, sb), "two of three close")
        sb["hashes"] = [(1 << 63) - 1, 0b10, (1 << 62) - 1]
        self.assertFalse(vf.same_moment(a, b, sa, sb, {"frames_matching": 2}))

    def test_two_cameras_on_the_same_moment_count_once(self):
        a, b = rec(0, device="iPhone"), rec(1, device="DJI", second=40)
        sa, sb = sc(0, h=5), sc(1, h=5)
        self.assertTrue(vf.same_moment(a, b, sa, sb))
        far = rec(1, device="DJI", second=120)
        self.assertFalse(vf.same_moment(a, far, sa, sb), "cameras get a shorter window than one camera")
        self.assertTrue(vf.same_moment(rec(0), rec(1, second=120), sa, sb), "one camera gets the longer window")

    def test_clusters_are_transitive_and_time_ordered(self):
        inv = [rec(0, 0), rec(1, 0, second=100), rec(2, 0, second=200), rec(3, 30), rec(4, 31)]
        scores = {0: sc(0, h=7), 1: sc(1, h=7), 2: sc(2, h=7), 3: sc(3, h=9999), 4: sc(4, h=9999 ^ 1)}
        groups = vf.same_moment_clusters(inv, scores)
        self.assertEqual(groups, [[0, 1, 2], [3, 4]], "0~1 and 1~2 make 0,1,2 one moment even though 0 and 2 are 200 s apart")
        self.assertEqual(vf.same_moment_clusters(inv, scores, {"enabled": False}), [])
        self.assertEqual(vf.same_moment_clusters(inv, scores, only={3, 4}), [[3, 4]])

    def test_the_best_take_is_kept(self):
        inv = [rec(0, 0, dur=8.0), rec(1, 0, dur=8.0, second=20), rec(2, 0, dur=8.0, second=40)]
        scores = {0: sc(0, h=7, q=70.0, shake=3.0), 1: sc(1, h=7, q=90.0, shake=0.4), 2: sc(2, h=7, q=80.0, shake=1.0)}
        self.assertEqual(vf.best_of([0, 1, 2], inv, scores), 1)
        scores[1]["quality"] = 70.0
        scores[1]["shake"] = 3.0
        self.assertEqual(vf.best_of([0, 1, 2], inv, scores), 2)
        notes = {"clip2.mp4": {"skip": True}}
        self.assertNotEqual(vf.best_of([0, 1, 2], inv, scores, notes), 2, "a clip the agent skipped is never the best take")

    def test_quality_score_rewards_steady_sharp_long_clips(self):
        base = vf.quality_score(rec(0, dur=10.0), sc(0))
        self.assertLess(vf.quality_score(rec(0, dur=10.0), sc(0, shake=5.0)), base)
        self.assertLess(vf.quality_score(rec(0, dur=10.0), sc(0, q=50.0)), base)
        self.assertLess(vf.quality_score(rec(0, dur=6.0), sc(0)), base)
        self.assertLess(vf.quality_score(rec(0, dur=10.0), sc(0, bright=15.0)), base)
        self.assertLess(vf.quality_score(rec(0, dur=10.0), sc(0, blur=9.5)), base)


class Exclusions(unittest.TestCase):
    def library(self):
        inv = [rec(0, 0, dur=12.0), rec(1, 0, dur=12.0, second=30), rec(2, 5, dur=3.0), rec(3, 9, dur=12.0, dup=0),
               rec(4, 20, dur=12.0), rec(5, 40, dur=12.0)]
        scores = {0: sc(0, h=7, q=70.0), 1: sc(1, h=7, q=92.0), 2: sc(2), 4: sc(4, shake=5.0), 5: sc(5)}
        return inv, scores

    def test_reasons_and_clusters(self):
        inv, scores = self.library()
        res = vf.exclusions(inv, scores)
        r = res["reasons"]
        self.assertEqual(r[0], ["same-moment:1"])
        self.assertNotIn(1, r)
        self.assertEqual(r[2], ["short"])
        self.assertEqual(r[4], ["shaky"])
        self.assertNotIn(3, r, "an exact copy is not a candidate at all")
        self.assertNotIn(5, r)
        self.assertEqual(res["clusters"], [{"keep": 1, "drop": [0], "members": [0, 1]}])
        s = vf.summarize(res, total=5)
        self.assertEqual((s["usable"], s["excluded"]), (2, 3))
        self.assertEqual(s["same_moment_clusters"], 1)
        text = vf.format_report(res, inv)
        self.assertIn("2 usable of 5 clips", text)
        self.assertIn("keep 1, drop 0", text)

    def test_a_forced_clip_is_never_dropped_as_a_weaker_take(self):
        inv, scores = self.library()
        res = vf.exclusions(inv, scores, must={0})
        self.assertNotIn(0, res["reasons"])

    def test_exclusions_follow_the_config(self):
        inv, scores = self.library()
        res = vf.exclusions(inv, scores, filters={"min_duration": 0.0, "block_shaky": False}, dedupe={"enabled": False})
        self.assertEqual(res["reasons"], {})


class InTheSelection(unittest.TestCase):
    def world(self):
        inv, scores = [], {}
        for i in range(60):
            inv.append(rec(i, minute=i * 3, dur=3.0 if i % 5 == 0 else 14.0))
            scores[i] = sc(i, q=60 + (i * 7) % 35)
        return inv, scores

    BEATS = {"period": 0.6, "t0": 0.2, "duration": 45.0, "beats": [round(0.2 + 0.6 * k, 3) for k in range(74)],
             "beat_strength": [0.9 if k % 2 == 0 else 0.3 for k in range(74)],
             "sections": [{"label": "a", "start": 0.2, "end": 20.0, "energy": 0.2}, {"label": "b", "start": 20.6, "end": 45.0, "energy": 0.8}]}

    def test_short_clips_and_weaker_takes_are_never_chosen(self):
        inv, scores = self.world()
        for i in (31, 32):                                           # the same moment, 30 s apart
            scores[i]["hashes"] = [5, 5, 5]
            scores[i]["hash"] = 5
            inv[i]["taken_epoch"] = inv[30]["taken_epoch"] + (i - 30) * 20
        scores[31]["quality"] = 99.0
        cfg = {"chapters": [{"name": "a", "from": 0, "to": 29, "weight": 1}, {"name": "b", "from": 30, "to": 59, "weight": 1}]}
        sb = st.select(inv, scores, self.BEATS, cfg, duration=45.0, pacing="energy")
        picked = {r["clip"] for r in sb["rows"]}
        self.assertFalse(picked & {i for i in range(60) if i % 5 == 0}, "no clip under 5 seconds")
        self.assertNotIn(32, picked, "the weaker take of the moment")
        self.assertEqual(sb["exclusions"]["by_reason"].get("short"), 12)
        self.assertIn("Left out before choosing", st.storyboard_markdown(sb))
        self.assertIn("left out:", st.format_storyboard(sb))

    def test_a_thin_chapter_may_use_soft_problems_but_never_short_clips(self):
        inv, scores = self.world()
        for i in range(30, 60):
            scores[i]["shake"] = 6.0                                 # everything in chapter b is shaky
        cfg = {"chapters": [{"name": "a", "from": 0, "to": 29, "weight": 1}, {"name": "b", "from": 30, "to": 59, "weight": 1}]}
        sb = st.select(inv, scores, self.BEATS, cfg, duration=45.0, pacing="energy")
        rows_b = [r for r in sb["rows"] if r["chapter"] == "b"]
        self.assertTrue(rows_b, "relaxed pool still fills the chapter")
        self.assertTrue(any("soft problems" in w for w in sb["warnings"]))
        self.assertTrue(all(inv[r["clip"]]["duration"] >= 5 for r in rows_b))

    def test_the_config_is_validated(self):
        base = {"chapters": [{"name": "a", "from": 0, "to": 5}]}
        for bad in ({"filters": {"min_duraton": 5}}, {"filters": 3}, {"dedupe": {"window": 1}}, {"filters": {"min_duration": -1}},
                    {"filters": {"min_duration": "five"}}):
            with self.assertRaises(vl.EngineError, msg=str(bad)):
                st.validate_chapters(dict(base, **bad))
        st.validate_chapters(dict(base, filters={"min_duration": 4}, dedupe={"window_s": 60}))


class Measures(unittest.TestCase):
    def test_a_steady_pan_is_not_shake_and_a_wobble_is(self):
        pan = [textured(shift=k * 2) for k in range(8)]
        wobble = [textured(shift=(3 if k % 2 else -3)) for k in range(8)]
        self.assertLess(ed.jitter(pan), 0.5)
        self.assertGreater(ed.jitter(wobble), 1.5)
        self.assertEqual(ed.jitter([textured()] * 2), 0.0)
        self.assertEqual(ed.jitter([bytes(W * H)] * 6), 0.0, "nothing to track")

    def test_texture_colour_and_scene(self):
        self.assertGreater(ed.texture_mean([textured()]), 5.0)
        self.assertLess(ed.texture_mean([bytes([90]) * (W * H)]), 0.1)
        sand = bytes([200, 160, 110]) * (16 * 9)
        sky = bytes([90, 140, 230]) * (16 * 9)
        self.assertEqual(ed.scene_label(ed.scene_shares([sand])), "desert")
        self.assertEqual(ed.scene_label(ed.scene_shares([sky])), "sky")
        self.assertEqual(ed.scene_label(ed.scene_shares([bytes([5, 5, 5]) * (16 * 9)])), "dark")
        sig = ed.colour_signature([sand])
        self.assertEqual(len(sig), 12)
        self.assertEqual(ed.colour_distance(sig, sig), 0.0)
        self.assertGreater(ed.colour_distance(sig, ed.colour_signature([sky])), 40.0)
        self.assertEqual(ed.colour_distance([], sig), 255.0)


@NEEDS_FF
@helpers.slow
class RealMedia(unittest.TestCase):
    def test_score_measures_shake_cover_scene_and_three_hashes(self):
        import vid_score as vs
        root = helpers.tmpdir() / "p1media"
        root.mkdir(exist_ok=True)
        steady = helpers.make_clip(root / "steady.mp4", "testsrc2", size="320x568", seconds=6.0)
        shaky = helpers.make_clip(root / "shaky.mp4", "testsrc2", size="360x608", seconds=6.0,
                                  extra=["-vf", "crop=320:568:x='20+16*sin(n*2.3)':y='20+16*cos(n*1.7)'"])
        black = helpers.make_clip(root / "black.mp4", "color=c=black", size="320x568", seconds=6.0) if False else None
        covered = root / "covered.mp4"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x080808:s=320x568:r=30:d=6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(covered)])
        recs = []
        for i, p in enumerate((steady, shaky, covered)):
            info = vl.probe(p)
            recs.append({"index": i, "path": vl.ffp(p), "name": p.name, "size": 10 + i, "mtime": 1, "duration": info["duration"],
                         "has_audio": False, "dup_of": None, "error": None})
        by = {s["index"]: s for s in vs.score_inventory(recs, hw=False) if "quality" in s}
        for i in by:
            self.assertEqual(len(by[i]["hashes"]), 3, i)
            self.assertEqual(len(by[i]["csig"]), 12, i)
            self.assertIn("shake", by[i])
            self.assertIn("scene", by[i])
        self.assertGreater(by[1]["shake"], by[0]["shake"] + 0.5, (by[0]["shake"], by[1]["shake"]))
        self.assertLess(by[2]["texture"], 0.8)
        self.assertEqual(by[2]["scene"], "dark")
        self.assertIn("covered", vf.usable(recs[2], by[2]))
        self.assertNotIn("covered", vf.usable(recs[0], by[0]))
        self.assertIn("shaky", vf.usable(recs[1], by[1], cfg={"max_shake": by[0]["shake"] + 0.4}))


if __name__ == "__main__":
    unittest.main()
