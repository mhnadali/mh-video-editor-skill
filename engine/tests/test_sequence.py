"""Phase 3 of the selection depth: the sequence as a whole (beam search), transitions between shots, three variants."""
import unittest

import helpers
import vid_story as st
import vidlib as vl

W = dict(st.WEIGHTS)


def view(i, size="medium", topic=None, bright=120.0, dx=0.0, mag=0.0, csig=None, device="iPhone", people=False, act=0.5,
         quality=0.8, role=None, epoch=None, h=None, must=False):
    return {"index": i, "path": f"c{i}.mp4", "name": f"c{i}.mp4", "taken": "2026-10-08 10:00:00",
            "epoch": float(i if epoch is None else epoch), "device": device, "duration": 10.0, "quality": quality, "act": act,
            "sound": None, "hash": (i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1) if h is None else h, "size": size, "role": role,
            "topic": topic, "people": people, "best_start": 1.0, "action_start": 2.0, "flags": [], "internal_cut": False,
            "brightness": bright, "csig": csig or [100] * 12, "scene": "desert", "motion_dx": dx, "motion_mag": mag, "faces": None,
            "must": must}


class Transitions(unittest.TestCase):
    def test_variety_of_size_and_topic(self):
        a, same = view(1, "wide"), view(2, "wide")
        v, why = st.transition_value([a], same, W)
        self.assertLess(v, 0)
        v2, _ = st.transition_value([a], view(3, "close"), W)
        self.assertEqual(v2, 0.0)
        v3, why3 = st.transition_value([view(0, "wide"), a], same, W)
        self.assertLess(v3, v)
        self.assertIn("a third shot of the same size", why3)
        v4, why4 = st.transition_value([view(1, topic="food")], view(2, "close", topic="food"), W)
        self.assertLess(v4, 0)
        self.assertTrue(any("food" in x for x in why4))

    def test_light_direction_colour_and_camera(self):
        prev = view(1, bright=30.0)
        v, why = st.transition_value([prev], view(2, "close", bright=200.0), W)
        self.assertIn("a big jump of light", why)
        left, right = view(1, dx=-4.0, mag=4.0), view(2, "close", dx=4.0, mag=4.0)
        v, why = st.transition_value([left], right, W)
        self.assertIn("the movement reverses direction", why)
        self.assertEqual(st.transition_value([left], view(3, "close", dx=-3.0, mag=3.0), W)[0], 0.0, "the same direction is fine")
        far = view(2, "close", csig=[240, 20, 20] * 4)
        self.assertLess(st.transition_value([view(1)], far, W)[0], 0)
        self.assertLess(st.transition_value([view(0, "wide"), view(1, "close")], view(2, "medium"), W)[0], 0.0 + 1e-9)
        self.assertEqual(st.transition_value([], view(1), W), (0.0, []))

    def test_the_weight_scales_the_transition_cost(self):
        a, same = view(1, "wide"), view(2, "wide")
        base = st.transition_value([a], same, W)[0]
        w0 = dict(W, transition=0.0)
        self.assertEqual(st.transition_value([a], same, w0)[0], 0.0)
        w2 = dict(W, transition=2.0)
        self.assertAlmostEqual(st.transition_value([a], same, w2)[0], 2 * base)


def plan_of(slots_cands, chapter=None, order="chronological", music=0.5):
    chapter = chapter or {"name": "ch", "from": 0, "to": 99, "weight": 1}
    plan = []
    n = len(slots_cands)
    for k, cands in enumerate(slots_cands):
        by_time = sorted({c["index"]: c for slot in slots_cands for c in slot}.values(), key=lambda c: c["epoch"])
        later = {c["index"]: len(by_time) - 1 - i for i, c in enumerate(by_time)}
        plan.append({"slot": {"i": k, "music": music}, "chapter": chapter, "target": 0.5, "cands": cands, "order": order,
                     "unary": {c["index"]: st.unary_value(c, 0.5, chapter, W, {}) for c in cands}, "later": later,
                     "remaining": n - 1 - k})
    return plan


CTX = {"min_people_share": 0.0, "device_library_share": {}, "w": W}


class Beam(unittest.TestCase):
    def test_the_whole_sequence_beats_a_greedy_step(self):
        # slot 0 prefers `a` (wide, best alone) but then slot 1 only has wide `b`/close `c`: the pair (close, wide) is better
        a, a2 = view(1, "wide", quality=0.90), view(2, "close", quality=0.85)
        b, c = view(3, "wide", quality=0.84), view(4, "close", quality=0.60)
        plan = plan_of([[a, a2], [b, c]], order="energy")
        picks, value, _ = st.beam_entries(plan, W, {}, CTX, [], {})
        self.assertEqual([p["index"] for p in picks], [2, 3], "close then wide beats wide then wide")
        greedy = [a, b]
        gv = sum(plan[k]["unary"][p["index"]] for k, p in enumerate(greedy)) + st.transition_value([a], b, W)[0]
        self.assertGreater(value, gv)

    def test_no_clip_twice_and_never_the_same_picture(self):
        same_a, same_b = view(1, h=5), view(2, "close", h=5)
        other = view(3, "wide", h=1 << 40)
        plan = plan_of([[same_a, other], [same_b, other, view(9, "close", quality=0.1)]], order="energy")
        picks, _, _ = st.beam_entries(plan, W, {}, CTX, [], {})
        self.assertEqual(len({p["index"] for p in picks}), 2)
        hs = {p["hash"] for p in picks}
        self.assertEqual(len(hs), 2, "two clips with the same picture hash are never both used")

    def test_a_chronological_chapter_keeps_the_order_and_stays_feasible(self):
        cs = [view(i, ("wide", "medium", "close")[i % 3], epoch=100 + i * 10, quality=0.5 + 0.04 * (i % 7)) for i in range(8)]
        plan = plan_of([cs, cs, cs, cs], order="chronological")
        picks, _, _ = st.beam_entries(plan, W, {}, CTX, [], {})
        self.assertEqual(len(picks), 4, "a dead end would return nothing")
        epochs = [p["epoch"] for p in picks]
        self.assertEqual(epochs, sorted(epochs))
        self.assertEqual(len(set(epochs)), 4)

    def test_energy_chapters_may_reorder_but_chronological_ones_may_not(self):
        cs = [view(1, epoch=5, act=0.9), view(2, "close", epoch=1, act=0.1)]
        high_first = plan_of([cs, cs], order="energy")
        for sl, m in zip(high_first, (0.9, 0.1)):
            sl["target"] = m
            sl["unary"] = {c["index"]: st.unary_value(c, m, sl["chapter"], W, {}) for c in cs}
        picks, _, _ = st.beam_entries(high_first, W, {}, CTX, [], {})
        self.assertEqual([p["index"] for p in picks], [1, 2], "the lively clip on the loud slot even though it is later in time")
        chrono = plan_of([cs, cs], order="chronological")
        picks, _, _ = st.beam_entries(chrono, W, {}, CTX, [], {})
        self.assertEqual([p["index"] for p in picks], [2, 1])

    def test_topic_repeats_inside_a_chapter_are_discouraged(self):
        food = [view(i, topic="food", quality=0.9, epoch=i) for i in range(1, 5)]
        other = [view(10 + i, "close" if i % 2 else "wide", topic=f"t{i}", quality=0.7, epoch=20 + i) for i in range(4)]
        plan = plan_of([food + other] * 4, order="energy")
        picks, _, _ = st.beam_entries(plan, W, {"max_per_topic": 1}, CTX, [], {})
        self.assertLessEqual(sum(1 for p in picks if p["topic"] == "food"), 2)

    def test_people_and_cameras_are_looked_after(self):
        ppl = [view(i, "close" if i % 2 else "wide", people=True, quality=0.7, epoch=i) for i in range(1, 7)]
        non = [view(10 + i, "close" if i % 2 else "wide", people=False, quality=0.8, epoch=20 + i) for i in range(6)]
        plan = plan_of([ppl + non] * 6, order="energy")
        ctx = dict(CTX, min_people_share=0.5)
        picks, _, _ = st.beam_entries(plan, W, {}, ctx, [], {})
        self.assertGreaterEqual(sum(1 for p in picks if p["people"]) / len(picks), 0.45)
        dj = [view(i, "close" if i % 2 else "wide", device="DJI", quality=0.7, epoch=i) for i in range(1, 7)]
        ip = [view(10 + i, "close" if i % 2 else "wide", device="iPhone", quality=0.72, epoch=20 + i) for i in range(6)]
        plan = plan_of([dj + ip] * 8, order="energy")
        ctx = dict(CTX, device_library_share={"DJI": 0.4, "iPhone": 0.6})
        picks, _, _ = st.beam_entries(plan, W, {}, ctx, [], {})
        self.assertGreaterEqual(sum(1 for p in picks if p["device"] == "DJI"), 2)

    def test_a_clip_the_user_insists_on_is_chosen(self):
        cs = [view(i, quality=0.9, epoch=i) for i in range(1, 6)]
        forced = view(50, "close", quality=0.2, epoch=60, must=True)
        plan = plan_of([cs + [forced]] * 3, order="energy")
        picks, _, _ = st.beam_entries(plan, W, {}, CTX, [], {})
        self.assertIn(50, [p["index"] for p in picks])

    def test_alternates_are_the_best_unchosen(self):
        cs = [view(i, quality=0.5 + 0.05 * i, epoch=i) for i in range(1, 8)]
        plan = plan_of([cs, cs], order="energy")
        picks, _, alts = st.beam_entries(plan, W, {}, CTX, [], {})
        chosen = {p["index"] for p in picks}
        for a in alts:
            self.assertFalse(set(a) & chosen)
            self.assertLessEqual(len(a), 6)

    def test_the_beam_width_matters_only_for_quality_not_for_validity(self):
        cs = [view(i, ("wide", "medium", "close")[i % 3], epoch=i, quality=0.5 + 0.03 * (i % 9)) for i in range(1, 15)]
        plan = plan_of([cs] * 6, order="energy")
        narrow = st.beam_entries(plan, W, {}, CTX, [], {}, width=1)
        wide = st.beam_entries(plan, W, {}, CTX, [], {}, width=60)
        self.assertEqual(len(narrow[0]), 6)
        self.assertEqual(len(wide[0]), 6)
        self.assertGreaterEqual(wide[1], narrow[1] - 1e-9)


BEATS = {"period": 0.6, "t0": 0.2, "duration": 45.0, "beats": [round(0.2 + 0.6 * k, 3) for k in range(74)],
         "beat_strength": [0.9 if k % 2 == 0 else 0.3 for k in range(74)],
         "sections": [{"label": "a", "start": 0.2, "end": 20.0, "energy": 0.2}, {"label": "b", "start": 20.6, "end": 45.0, "energy": 0.8}]}


def library(n=90):
    inv, sc = [], {}
    for i in range(n):
        inv.append({"index": i, "path": f"clip{i}.mp4", "name": f"clip{i}.mp4", "duration": 12.0, "dup_of": None, "error": None,
                    "device": "DJI" if i % 3 == 0 else "iPhone", "taken": "2026-10-08 06:00:00",
                    "taken_epoch": 1791400000.0 + i * 600.0})
        sc[i] = {"index": i, "path": f"clip{i}.mp4", "quality": 60 + (i * 7) % 35, "energy": (i * 13) % 100, "best_start": 1.0,
                 "flags": [], "hash": (i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1), "shot_size": ("wide", "medium", "close")[i % 3],
                 "wins": [[1.0, 80.0, 1.0], [3.0, 80.0, 6.0], [5.0, 80.0, 2.0]], "csig": [100 + i % 50] * 12, "brightness": 120.0,
                 "motion_dx": 0.0, "motion_mag": 0.0}
    return inv, sc


CFG = {"min_people_share": 0.3, "chapters": [{"name": "a", "from": 0, "to": 29, "weight": 1, "energy": "calm"},
                                             {"name": "b", "from": 30, "to": 59, "weight": 2, "energy": "peak", "order": "energy"},
                                             {"name": "c", "from": 60, "to": 89, "weight": 1, "energy": "calm"}]}


class InTheSelection(unittest.TestCase):
    def run_select(self, **kw):
        inv, sc = library()
        notes = {f"clip{i}.mp4": {"people": True, "role": "portrait"} for i in range(0, 90, 4)}
        return st.select(inv, sc, BEATS, CFG, notes, duration=45.0, pacing="energy", **kw), inv

    def test_beam_is_the_default_and_fills_every_slot_in_the_trip_order(self):
        sb, inv = self.run_select()
        self.assertEqual(sb["method"], "beam")
        self.assertEqual(sb["variant"], "balanced")
        self.assertIsNotNone(sb["sequence_score"])
        self.assertEqual(len(sb["rows"]), sb["slots"])
        self.assertEqual(sb["warnings"], [])
        chapters = [r["chapter"] for r in sb["rows"]]
        self.assertEqual(chapters, sorted(chapters, key=["a", "b", "c"].index))
        clips = [r["clip"] for r in sb["rows"]]
        self.assertEqual(len(clips), len(set(clips)))
        a_rows = [r["clip"] for r in sb["rows"] if r["chapter"] == "a"]
        self.assertEqual(a_rows, sorted(a_rows), "a chronological chapter stays in order")

    def test_greedy_still_works_and_beam_is_at_least_as_smooth(self):
        beam, inv = self.run_select(method="beam")
        greedy, _ = self.run_select(method="greedy")
        self.assertEqual(greedy["method"], "greedy")
        self.assertIsNone(greedy["sequence_score"])
        self.assertEqual(len(greedy["rows"]), len(beam["rows"]))

        def same_size_neighbours(sb):
            sizes = [r["size"] for r in sb["rows"]]
            return sum(1 for a, b in zip(sizes, sizes[1:]) if a and a == b)

        self.assertLessEqual(same_size_neighbours(beam), same_size_neighbours(greedy) + 2)

    def test_variants_tell_the_story_differently(self):
        inv, sc = library()
        notes = {f"clip{i}.mp4": {"people": True, "role": "portrait"} for i in range(0, 90, 3)}
        notes.update({f"clip{i}.mp4": {"role": "action", "mood": "intense"} for i in range(1, 90, 3)})
        sbs = st.select_variants(inv, sc, BEATS, CFG, notes, duration=45.0, pacing="energy")
        self.assertEqual(list(sbs), ["balanced", "action", "people"])
        table = {r["variant"]: r for r in st.compare_variants(sbs)}
        self.assertGreater(table["people"]["people_share"], table["action"]["people_share"])
        self.assertGreaterEqual(table["action"]["action_shots"], table["people"]["action_shots"])
        text = st.format_comparison(list(table.values()))
        for name in ("balanced", "action", "people"):
            self.assertIn(name, text)

    def test_unknown_variant_and_method_and_weights_are_refused(self):
        inv, sc = library()
        with self.assertRaises(vl.EngineError):
            st.select(inv, sc, BEATS, CFG, None, duration=45.0, variant="loud")
        with self.assertRaises(vl.EngineError):
            st.select(inv, sc, BEATS, CFG, None, duration=45.0, method="random")
        for bad in ({"weights": {"qualiti": 1}}, {"weights": 3}, {"weights": {"quality": "high"}}, {"method": "dice"}):
            with self.assertRaises(vl.EngineError, msg=str(bad)):
                st.validate_chapters(dict(CFG, **bad))
        st.validate_chapters(dict(CFG, weights={"quality": 0.4}, method="greedy"))

    def test_weights_from_the_config_change_the_choice(self):
        inv, sc = library()
        base = st.select(inv, sc, BEATS, CFG, None, duration=45.0, pacing="energy")
        heavy = st.select(inv, sc, BEATS, dict(CFG, weights={"quality": 2.0, "fit": 0.0}), None, duration=45.0, pacing="energy")
        mean_q = lambda sb: sum(sc[r["clip"]]["quality"] for r in sb["rows"]) / len(sb["rows"])
        self.assertGreaterEqual(mean_q(heavy), mean_q(base) - 0.5)

    def test_a_teaser_still_opens_the_edit_with_the_beam(self):
        inv, sc = library()
        cfg = dict(CFG, hook="teaser", teaser_chapter="b")
        sb = st.select(inv, sc, BEATS, cfg, None, duration=45.0, pacing="energy")
        self.assertTrue(sb["rows"][0]["chapter"].endswith("(teaser)"))
        self.assertEqual(len(sb["rows"]), sb["slots"])
        self.assertNotIn(sb["rows"][0]["clip"], [r["clip"] for r in sb["rows"][1:]])

    def test_the_cli_writes_the_variants(self):
        import json
        import mh_video
        root = helpers.tmpdir() / "seqcli"
        root.mkdir(exist_ok=True)
        inv, sc = library()
        vl.save_json(root / "inventory.json", {"summary": {}, "clips": inv})
        vl.save_json(root / "scores.json", {"summary": {}, "scores": list(sc.values())})
        vl.save_json(root / "beats.json", BEATS)
        vl.save_json(root / "ch.json", CFG)
        a = mh_video.build_parser().parse_args(["story", "select", "--work", str(root), "--beats", str(root / "beats.json"),
                                                "--chapters", str(root / "ch.json"), "--duration", "45", "--variants",
                                                "--out", str(root / "sb.json")])
        self.assertEqual(mh_video.cmd_story(a), 0)
        for name in ("balanced", "action", "people"):
            self.assertTrue((root / f"sb.{name}.json").is_file(), name)
            self.assertTrue((root / f"sb.{name}.md").is_file())
            self.assertTrue((root / f"sb.{name}-clips.txt").is_file())
        self.assertTrue((root / "sb.json").is_file())
        self.assertEqual(json.loads((root / "sb.json").read_text(encoding="utf-8"))["variant"], "balanced")


if __name__ == "__main__":
    unittest.main()
