"""Phase 5: remembered preferences (profile + feedback), the review page, and the selection evaluation."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers
import test_story as ts
import vid_profile as vpf
import vid_review
import vid_story as st
import vidlib as vl


def world_and_views(n=90, topics=None):
    inv, sc = ts.world(n, devices=("iPhone", "iPhone", "DJI"))
    nt = st.cl.notes_by_path({r["path"]: {"topic": topics[r["index"]]} for r in inv if r["index"] in (topics or {})})
    views = {r["index"]: st.clip_view(r, sc[r["index"]], st._note(nt, r)) for r in inv}     # noqa: SLF001
    return inv, sc, views


class Profile(unittest.TestCase):
    def test_empty_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "profile.json"
            self.assertEqual(vpf.load(p), vpf.empty())
            prof = vpf.empty()
            prof["topics"]["food"] = -0.08
            prof["banned"].append("x.mp4")
            vpf.save(prof, p)
            self.assertEqual(vpf.load(p)["topics"], {"food": -0.08})
            self.assertEqual(vpf.load(p)["banned"], ["x.mp4"])

    def test_project_overrides_global(self):
        g = dict(vpf.empty(), topics={"food": -0.1, "dunes": 0.1}, banned=["a"])
        p = dict(vpf.empty(), topics={"food": 0.05}, banned=["b"])
        m = vpf.merge(g, p)
        self.assertEqual(m["topics"], {"food": 0.05, "dunes": 0.1})
        self.assertEqual(m["banned"], ["a", "b"])

    def test_validation(self):
        with self.assertRaises(vl.EngineError):
            vpf.validate(dict(vpf.empty(), weights={"nonsense": 1.0}))
        with self.assertRaises(vl.EngineError):
            vpf.validate(dict(vpf.empty(), topics={"food": 0.9}))
        vpf.validate(dict(vpf.empty(), topics={"food": 0.2}, weights={"people": 0.3}))

    def test_bias(self):
        prof = dict(vpf.empty(), topics={"food": -0.1}, roles={"action": 0.05}, favoured=["c1.mp4"])
        self.assertAlmostEqual(vpf.clip_bias(prof, {"topic": "food", "role": "action", "path": "c1.mp4"}),
                               -0.1 + 0.05 + vpf.FAVOUR)
        self.assertEqual(vpf.clip_bias(prof, {"topic": None, "role": None, "path": "z"}), 0.0)

    def test_feedback_validation(self):
        for bad in ({}, {"decisions": [{"action": "dance"}]}, {"decisions": [{"action": "replace", "slot": 1}]},
                    {"decisions": [{"action": "ban"}]}, {"decisions": [{"action": "weight", "name": "x"}]}):
            with self.assertRaises(vl.EngineError):
                vpf.validate_feedback(bad)
        vpf.validate_feedback({"decisions": [{"action": "keep", "slot": 0}]})


class Learning(unittest.TestCase):
    def setUp(self):
        self.inv, self.sc, self.views = world_and_views(topics={i: "food" for i in range(0, 30)} | {i: "dunes" for i in range(30, 60)})
        cfg = ts.cfg_of(ts.chap("start", 0, 29, energy="calm"), ts.chap("action", 30, 59, energy="peak"),
                        ts.chap("end", 60, 89, energy="calm"))
        self.cfg = cfg
        self.sb = st.select(self.inv, self.sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy")

    def test_replace_changes_the_row_and_learns_topic(self):
        row = self.sb["rows"][3]
        new = next(i for i in range(30, 60) if i != row["clip"] and i not in {r["clip"] for r in self.sb["rows"]})
        prof, rows = vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "replace", "slot": row["slot"], "with": new}]},
                               self.inv, self.views)
        self.assertEqual(rows[3]["clip"], new)
        self.assertEqual(rows[3]["start_kind"], "chosen by the user")
        self.assertIn(self.inv[new]["path"], prof["favoured"])
        self.assertLess(prof["topics"].get(self.views[row["clip"]]["topic"], 0.0) if self.views[row["clip"]]["topic"] != "dunes" else -1, 0.0)
        self.assertEqual(prof["history"][0]["action"], "replace")
        self.assertEqual(self.sb["rows"][3]["clip"], row["clip"], "the original storyboard is not changed in place")

    def test_unknown_slot_and_clip(self):
        with self.assertRaises(vl.EngineError):
            vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "replace", "slot": 999, "with": 1}]}, self.inv, self.views)
        with self.assertRaises(vl.EngineError):
            vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "replace", "slot": 0, "with": 9999}]}, self.inv, self.views)

    def test_bias_is_capped(self):
        prof = vpf.empty()
        for _ in range(50):
            prof, _ = vpf.learn(prof, self.sb, {"decisions": [{"action": "avoid-topic", "topic": "food"}]}, self.inv, self.views)
        self.assertEqual(prof["topics"]["food"], -vpf.BIAS_LIMIT)

    def test_ban_and_unban(self):
        prof, _ = vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "ban", "clip": 5}]}, self.inv, self.views)
        self.assertEqual(vpf.banned_indexes(prof, self.inv), {5})
        prof, _ = vpf.learn(prof, self.sb, {"decisions": [{"action": "unban", "clip": 5}]}, self.inv, self.views)
        self.assertEqual(vpf.banned_indexes(prof, self.inv), set())

    def test_keep_pins_the_clip(self):
        r = self.sb["rows"][2]
        prof, _ = vpf.learn(vpf.empty(), self.sb, {"decisions": [{"action": "keep", "slot": r["slot"]}]}, self.inv, self.views)
        self.assertEqual(vpf.pinned(prof, self.inv), {r["clip"]: r["slot"]})


class AppliedToSelection(unittest.TestCase):
    def setUp(self):
        self.inv, self.sc, _ = world_and_views()
        self.cfg = ts.cfg_of(ts.chap("start", 0, 29, energy="calm"), ts.chap("action", 30, 59, energy="peak"),
                             ts.chap("end", 60, 89, energy="calm"))

    def run_select(self, profile=None):
        return st.select(self.inv, self.sc, ts.BEATS, self.cfg, None, duration=45.0, pacing="energy", profile=profile)

    def test_a_banned_clip_is_never_used_and_not_even_as_must(self):
        base = self.run_select()
        victim = base["rows"][4]["clip"]
        prof = dict(vpf.empty(), banned=[self.inv[victim]["path"]])
        cfg = dict(self.cfg, chapters=[dict(c, must=[victim]) if c["from"] <= victim <= c["to"] else c for c in self.cfg["chapters"]])
        sb = st.select(self.inv, self.sc, ts.BEATS, cfg, None, duration=45.0, pacing="energy", profile=prof)
        self.assertNotIn(victim, [r["clip"] for r in sb["rows"]])
        self.assertEqual(len(sb["rows"]), sb["slots"])

    def test_a_pinned_clip_is_used(self):
        base = self.run_select()
        used = {r["clip"] for r in base["rows"]}
        pin = next(i for i in range(30, 60) if i not in used)
        prof = dict(vpf.empty(), pins={self.inv[pin]["path"]: 0})
        sb = self.run_select(prof)
        self.assertIn(pin, [r["clip"] for r in sb["rows"]])

    def test_an_avoided_topic_loses_share(self):
        inv, sc, views = world_and_views(topics={i: ("food" if i % 2 == 0 else "dunes") for i in range(90)})
        notes = {r["path"]: {"topic": "food" if r["index"] % 2 == 0 else "dunes"} for r in inv}
        cfg = ts.cfg_of(ts.chap("all", 0, 89, energy="building"), max_per_topic=99)

        def food_share(profile):
            sb = st.select(inv, sc, ts.BEATS, cfg, notes, duration=45.0, pacing="energy", profile=profile)
            rows = sb["rows"]
            return sum(1 for r in rows if r["topic"] == "food") / len(rows)

        before = food_share(None)
        after = food_share(dict(vpf.empty(), topics={"food": -vpf.BIAS_LIMIT}))
        self.assertLess(after, before + 1e-9)
        self.assertLess(after, 0.5)

    def test_a_profile_changes_nothing_when_empty(self):
        a = self.run_select()
        b = self.run_select(vpf.empty())
        self.assertEqual([r["clip"] for r in a["rows"]], [r["clip"] for r in b["rows"]])


class ReviewPage(unittest.TestCase):
    def test_page_is_offline_and_embeds_the_decisions_ui(self):
        inv, sc, _ = world_and_views(12)
        sb = {"duration": 10.0, "variant": "balanced", "rows": [
            {"slot": 0, "t": 0.0, "len": 1.0, "music": 0.5, "chapter": "c", "clip": 0, "name": "clip0.mp4", "device": "iPhone",
             "role": None, "people": False, "topic": None, "energy": 0.5, "target": 0.5, "start": 1.0, "why": "because",
             "score": 1.0, "alternates": [1]}]}
        with tempfile.TemporaryDirectory() as d:
            # no media on disk: the page is still written, with clips that have no frames
            out = Path(d) / "review.html"
            orig = vid_review.strip_for
            vid_review.strip_for = lambda rec, wd, size=140: ["QUJD"]
            try:
                vid_review.write(out, sb, inv, sc, d, log=lambda *_: None, video=False)
            finally:
                vid_review.strip_for = orig
            text = out.read_text(encoding="utf-8")
        self.assertIn("Export decisions", text)
        self.assertIn("const DATA", text)
        self.assertNotIn("http://", text.replace("http://www.w3.org", ""))
        self.assertNotIn("https://", text)
        self.assertNotIn("<script src", text)
        self.assertIn('"index": 1', text)
        data = json.loads(text.split("const DATA = ", 1)[1].split(";\nconst decisions", 1)[0].replace("<\\/", "</"))
        self.assertEqual(len(data["rows"][0]["clips"]), 2)


if __name__ == "__main__":
    unittest.main()
