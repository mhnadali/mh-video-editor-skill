"""Cut decisions with reasons (vid_cutlogic): classification, the transition budget, notes overriding measurements,
the rationale field, and the lint/QA checks that use it. Pure Python: no FFmpeg, no footage."""
import unittest

import helpers  # noqa: F401
import vid_cutlogic as cl
import vid_edl as ve
import vid_editorial as ed
import vidlib as vl

W, H = 24, 16


def frame_with_block(x: int) -> bytes:
    buf = bytearray(W * H)
    for y in range(4, 12):
        for xx in range(x, x + 4):
            buf[y * W + xx] = 220
    # texture so the tracker has something to hold on to
    for i in range(0, W * H, 7):
        buf[i] = (buf[i] + 40) % 256
    return bytes(buf)


def feat(**kw):
    base = {"size": "medium", "hash": 0, "brightness": 120.0, "saturation": 60.0, "role": None, "mood": None,
            "dx": 0.0, "dy": 0.0, "mag": 0.0, "focus": None, "energy": 50.0}
    base.update(kw)
    return base


def far_hash():
    return (1 << 40) - 1                       # 40 bits apart from hash 0


class SignConvention(unittest.TestCase):
    def test_positive_dx_means_content_moves_right(self):
        a, b = frame_with_block(6), frame_with_block(8)          # the block moved 2 px to the right
        dx, dy = ed.motion_between(a, b, W, H, rng=3)
        self.assertGreater(dx, 0)
        a, b = frame_with_block(8), frame_with_block(6)
        self.assertLess(ed.motion_between(a, b, W, H, rng=3)[0], 0)

    def test_slide_names_agree_with_the_directions(self):
        for name in cl.DIRECTIONAL["left"]:
            self.assertEqual(cl.TRANSITION_DIRECTION[name], "left")
            self.assertIn(name, vl.ALL_TRANSITIONS)
            self.assertNotIn(name, vl.BANNED_TRANSITIONS)
        for names in cl.DIRECTIONAL.values():
            for n in names:
                self.assertIn(n, vl.ALL_TRANSITIONS, n)
        for n in cl.SECTION_LIFT + cl.SOFT:
            self.assertIn(n, vl.ALL_TRANSITIONS, n)
            self.assertNotIn(n, vl.BANNED_TRANSITIONS, n)


class Classify(unittest.TestCase):
    def go(self, a, b, **ctx):
        c = {"is_drop": False, "is_last": False, "section": None}
        c.update(ctx)
        return cl.classify(a, b, c)

    def test_drop_and_ending(self):
        self.assertEqual(self.go(feat(), feat(), is_drop=True)["transition"], "fadewhite")
        self.assertEqual(self.go(feat(), feat(), is_last=True)["type"], "closing")

    def test_cut_on_action_when_both_move_the_same_way(self):
        d = self.go(feat(dx=-3.0, mag=3.0, hash=0), feat(dx=-2.5, mag=2.5, hash=far_hash(), size="close"))
        self.assertEqual(d["type"], "cut-on-action")
        self.assertIsNone(d["transition"])

    def test_direction_clash_when_movement_reverses(self):
        d = self.go(feat(dx=-3.0, mag=3.0), feat(dx=3.0, mag=3.0, hash=far_hash()))
        self.assertEqual(d["type"], "direction-clash")

    def test_jump_risk_and_soften(self):
        d = self.go(feat(energy=70), feat(energy=70))
        self.assertEqual(d["type"], "jump-risk")
        self.assertIn("jump cut", d["why"])
        d = self.go(feat(energy=20), feat(energy=20))
        self.assertEqual((d["type"], d["transition"]), ("soften", "dissolve"))

    def test_graphic_match_needs_a_different_scene_and_close_focus(self):
        a = feat(focus=(0.5, 0.5), size="wide")
        b = feat(focus=(0.52, 0.48), size="medium", hash=far_hash())
        self.assertEqual(self.go(a, b)["type"], "graphic-match")
        b2 = feat(focus=(0.9, 0.1), size="medium", hash=far_hash())
        self.assertNotEqual(self.go(a, b2)["type"], "graphic-match")

    def test_contrast_cut_on_big_jump(self):
        d = self.go(feat(brightness=30.0, hash=0, size="wide"), feat(brightness=190.0, hash=far_hash(), size="close"))
        self.assertEqual(d["type"], "contrast-cut")

    def test_motion_bridge_follows_the_direction(self):
        d = self.go(feat(dx=-4.0, mag=4.0, size="wide"), feat(size="medium", hash=far_hash()))
        self.assertEqual((d["type"], d["transition"]), ("motion-bridge", "dir:left"))
        d = self.go(feat(dx=4.0, mag=4.0, size="wide"), feat(size="medium", hash=far_hash()))
        self.assertEqual(d["transition"], "dir:right")

    def test_section_boundaries(self):
        up = self.go(feat(size="wide"), feat(size="medium", hash=far_hash()), section="up")
        self.assertEqual(up["type"], "section-lift")
        down = self.go(feat(size="wide"), feat(size="medium", hash=far_hash()), section="down")
        self.assertEqual((down["type"], down["transition"]), ("calm-dissolve", "fade"))

    def test_every_decision_has_a_known_type_and_a_reason(self):
        import itertools
        sizes = ["wide", "medium", "close", None]
        for sa, sb, ma, mb, h in itertools.product(sizes, sizes, (0.0, 3.0), (0.0, -3.0), (0, far_hash())):
            d = self.go(feat(size=sa, dx=ma, mag=abs(ma)), feat(size=sb, dx=mb, mag=abs(mb), hash=h))
            self.assertIn(d["type"], cl.ALL_TYPES)
            self.assertTrue(d["why"].strip())


class Choose(unittest.TestCase):
    def decisions(self, types):
        return [{"j": i + 1, "type": t, "transition": w, "confidence": 0.7, "why": "x"} for i, (t, w) in enumerate(types)]

    def test_budget_variety_and_no_neighbours(self):
        wants = [("motion-bridge", "dir:left")] * 12
        out = cl.choose(self.decisions(wants), 12, dict(ve.DEFAULT_BUDGET))
        names = [d["transition"] for d in out if d["transition"]]
        self.assertLessEqual(len(names), 6)
        self.assertEqual(len(names), len(set(names)), "each transition once")
        idx = [i for i, d in enumerate(out) if d["transition"]]
        self.assertTrue(all(b - a >= 2 for a, b in zip(idx, idx[1:])), "no two neighbouring transitions")
        self.assertTrue(all(vl.family_of(n) == "horizontal" for n in names))
        self.assertLessEqual(len(names), ve.DEFAULT_BUDGET["max_family"], "family cap")

    def test_drop_and_closing_always_get_theirs(self):
        wants = [("motion-bridge", "dir:left")] * 3 + [("impact", "fadewhite")] + [("motion-bridge", "dir:right")] * 3 + [("closing", "fade")]
        out = cl.choose(self.decisions(wants), 8, dict(ve.DEFAULT_BUDGET))
        self.assertEqual(out[3]["transition"], "fadewhite")
        self.assertEqual(out[7]["transition"], "fade")

    def test_hard_cut_types_never_get_a_transition(self):
        out = cl.choose(self.decisions([("cut-on-action", None)] * 5), 5, dict(ve.DEFAULT_BUDGET))
        self.assertTrue(all(d["transition"] is None for d in out))


def make_edl(n=6):
    return {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": float(n), "music": None},
            "style": {"drop_flash": False, "end_flare": False},
            "shots": [{"clip": f"clip{i}.mp4", "span": [float(i), float(i + 1)]} for i in range(n)]}


def make_scores(per=None, n=6):
    per = per or {}
    out = {}
    for i in range(n):
        rec = {"index": i, "path": f"clip{i}.mp4", "shot_size": "wide" if i % 2 == 0 else "close",
               "motion_dx": 0.0, "motion_dy": 0.0, "motion_mag": 0.0, "focus_x": 0.5, "focus_y": 0.5,
               "brightness": 100.0, "saturation": 50.0, "energy": 50.0, "hash": 0 if i % 2 == 0 else far_hash()}
        rec.update(per.get(i, {}))
        out[i] = rec
    return out


class WholeEdl(unittest.TestCase):
    def test_analyse_reads_scores_and_notes(self):
        edl = make_edl()
        scores = make_scores({1: {"motion_dx": -4.0, "motion_mag": 4.0}})
        dec = cl.analyse(edl, scores)
        self.assertEqual([d["j"] for d in dec], [1, 2, 3, 4, 5])
        self.assertEqual(dec[-1]["type"], "closing")            # the last cut is the ending
        self.assertEqual(dec[1]["type"], "motion-bridge")       # shot 1 ends moving left, shot 2 is still
        self.assertEqual(dec[1]["transition"] in cl.DIRECTIONAL["left"], True)
        # the agent's reading wins over the measurement
        notes = {"clip1.mp4": {"motion_dir": "right"}}
        dec2 = cl.analyse(edl, scores, notes=notes)
        self.assertIn(dec2[1]["transition"], cl.DIRECTIONAL["right"])

    def test_apply_keeps_existing_and_overwrite_replaces(self):
        edl = make_edl()
        edl["transitions"] = {"1": "pixelize"}
        dec = cl.analyse(edl, make_scores())
        cl.apply(edl, dec)
        self.assertEqual(edl["transitions"]["1"], "pixelize")
        self.assertEqual(set(edl["rationale"]), {"1", "2", "3", "4", "5"})
        cl.apply(edl, dec, overwrite=True)
        self.assertNotEqual(edl["transitions"].get("1"), "pixelize")
        for v in edl["rationale"].values():
            self.assertTrue(v["why"])

    def test_result_passes_lint_without_unmotivated_warnings(self):
        edl = make_edl()
        cl.apply(edl, cl.analyse(edl, make_scores({1: {"motion_dx": -4.0, "motion_mag": 4.0}})))
        issues = ve.lint(edl, check_files=False)
        self.assertFalse(ve.has_errors(issues), issues)
        self.assertFalse([i for i in issues if i["code"] in ("transition-unmotivated", "transition-share", "transition-repeat", "transition-family")], issues)
        rep = cl.cut_report(edl)
        self.assertEqual(rep["transitions"], rep["transitions_with_reason"])
        self.assertEqual(sum(rep["cut_types"].values()), 5)

    def test_format_table_lists_every_cut(self):
        edl = make_edl()
        text = cl.format_table(cl.analyse(edl, make_scores()), edl)
        self.assertEqual(len(text.splitlines()), 1 + 5 + 1)


class LintAndQa(unittest.TestCase):
    def codes(self, edl):
        return [i["code"] for i in ve.lint(edl, check_files=False)]

    def test_unmotivated_transition_warns_only_once_rationale_is_in_use(self):
        edl = make_edl()
        edl["transitions"] = {"2": "slideleft", "4": "circleopen"}
        self.assertNotIn("transition-unmotivated", self.codes(edl))          # no rationale: old behaviour
        edl["rationale"] = {"2": {"type": "motion-bridge", "why": "carries the movement"}}
        codes = self.codes(edl)
        self.assertEqual(codes.count("transition-unmotivated"), 1)

    def test_rationale_shape_is_validated(self):
        edl = make_edl()
        for bad in ({"9": {"type": "x", "why": "y"}}, {"a": {"type": "x", "why": "y"}}, {"1": {"type": "x", "why": " "}},
                    {"1": "text"}):
            edl["rationale"] = bad
            self.assertTrue(ve.has_errors(ve.lint(edl, check_files=False)), bad)
        edl["rationale"] = []
        self.assertTrue(ve.has_errors(ve.lint(edl, check_files=False)))

    def test_flash_overuse(self):
        edl = make_edl()
        edl["transitions"] = {"1": "fadewhite", "3": "fadewhite"}
        self.assertIn("flash-overuse", self.codes(edl))

    def test_direction_mismatch(self):
        edl = make_edl()
        feats = [{"motion_dx": -3.0}, {"motion_dx": 0.0}] + [{}] * 4
        bounds = [0, 30, 60, 90, 120, 150, 180]
        ok = ed.editorial_issues(edl, feats, bounds, {1: "slideleft"})
        bad = ed.editorial_issues(edl, feats, bounds, {1: "slideright"})
        self.assertFalse([i for i in ok if i["code"] == "direction-mismatch"])
        self.assertEqual([i["code"] for i in bad if i["code"] == "direction-mismatch"], ["direction-mismatch"])


class Cli(unittest.TestCase):
    def test_commands_exist_and_smart_transitions_needs_scores(self):
        import mh_video
        parser = mh_video.build_parser()
        names = set(parser._subparsers._group_actions[0].choices)       # noqa: SLF001
        self.assertIn("cuts", names)
        args = parser.parse_args(["plan", "--beats", "b.json", "--clips", "c.txt", "--out", "o.json", "--smart-transitions"])
        self.assertTrue(args.smart_transitions)


class SmartPlan(unittest.TestCase):
    """The real planner layout (39 shots, a drop): smart transitions stay inside the budget and keep the drop flash."""

    def test_plan_with_smart_transitions(self):
        import vid_plan as vp
        beats = {"duration": 28.235, "period": 0.6505, "t0": 0.6, "drop_beat": 22, "file": None}
        n = 39
        clips = [{"clip": f"clip{i}.mp4"} for i in range(n)]
        edl = vp.plan(clips, beats, name="t", music=None, duration=28.235, period=0.6505)
        scores = make_scores(
            {i: {"motion_dx": -3.0 if i % 5 == 1 else 0.0, "motion_mag": 3.0 if i % 5 == 1 else 0.0} for i in range(n)}, n=n)
        edl["transitions"] = {}
        cl.apply(edl, cl.analyse(edl, scores, beats), overwrite=True)
        bud = ve.DEFAULT_BUDGET
        eff = ve.effective_transitions(edl)
        self.assertLessEqual(len(eff) / (len(edl["shots"]) - 1), bud["max_transition_share"])
        self.assertEqual(list(eff.values()).count("fadewhite"), 1)
        self.assertEqual(len(edl["rationale"]), len(edl["shots"]) - 1)
        issues = ve.lint(edl, check_files=False)
        self.assertFalse([i for i in issues if i["code"].startswith("transition-")], issues)


if __name__ == "__main__":
    unittest.main()
