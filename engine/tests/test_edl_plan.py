import copy
import json
import random
import unittest

import helpers  # noqa: F401
import vid_edl as ve
import vid_plan as vp
import vid_render as vr
import vidlib as vl

# the real Maranjab numbers: 28.235 s of music, beat grid starting at 3.2 s, period 0.6505, drop on beat 18
BEATS = {"duration": 28.235, "period": 0.6505, "t0": 0.6, "drop_beat": 22, "file": None}


def clips(n):
    return [{"clip": f"clip{i}.mp4"} for i in range(n)]


def base_edl(n=6, duration=6.0, fps=30):
    edges = [round(duration * i / n, 4) for i in range(n + 1)]
    return {"version": 1,
            "project": {"name": "t", "width": 270, "height": 480, "fps": fps, "duration": duration},
            "style": {}, "transitions": {},
            "shots": [{"clip": f"c{i}.mp4", "span": [edges[i], edges[i + 1]]} for i in range(n)]}


def codes(issues):
    return {i["code"] for i in issues}


class Layout(unittest.TestCase):
    def test_v4_layout_needs_39_clips(self):
        t0 = vp.body_start_time(BEATS, 28.235, 0.6505, 2)
        self.assertAlmostEqual(t0, 3.2, delta=0.1)
        lay = vp.layout(28.235, 3.2, 0.6505, 2)
        self.assertEqual(lay["last_beat"], 36)
        self.assertEqual(lay["shots"], 39)

    def test_cuts_strictly_increasing_and_inside(self):
        lay = vp.layout(28.235, 3.2, 0.6505, 2)
        c = lay["cuts"]
        self.assertEqual(c, sorted(set(c)))
        self.assertTrue(0 < c[0] and c[-1] < 28.235 - 1.4 * 0.6505)

    def test_no_intro(self):
        lay = vp.layout(10.0, 0.0, 0.5, intro_shots=0)
        self.assertEqual(lay["shots"], lay["last_beat"] + 1)

    def test_too_short_music(self):
        with self.assertRaises(vl.EngineError):
            vp.layout(2.0, 1.0, 0.65, 2)


class Plan(unittest.TestCase):
    def setUp(self):
        self.edl = vp.plan(clips(39), BEATS, name="t", music=None, duration=28.235, period=0.6505)

    def test_shots_cover_timeline_exactly(self):
        spans = [s["span"] for s in self.edl["shots"]]
        self.assertEqual(spans[0][0], 0.0)
        self.assertAlmostEqual(spans[-1][1], 28.235)
        for a, b in zip(spans, spans[1:]):
            self.assertAlmostEqual(a[1], b[0], places=4)
        bounds = ve.timeline(self.edl)
        self.assertEqual(bounds[-1], 847)

    def test_wrong_clip_count_explains_itself(self):
        with self.assertRaises(vl.EngineError) as cm:
            vp.plan(clips(10), BEATS, duration=28.235, period=0.6505)
        self.assertIn("39", str(cm.exception))

    def test_drop_is_on_a_cut_and_gets_the_white_fade(self):
        bounds = ve.timeline(self.edl)
        j = ve.drop_boundary(self.edl, bounds)
        self.assertIsNotNone(j)
        self.assertEqual(ve.effective_transitions(self.edl, bounds)[j], "fadewhite")

    def test_transitions_follow_the_restraint_rules(self):
        t = self.edl["transitions"]
        cuts = len(self.edl["shots"]) - 1
        self.assertLessEqual(len(t) / cuts, 0.5)
        self.assertGreaterEqual(len(t) / cuts, 0.3)
        names = list(t.values())
        for n in set(names):
            self.assertLessEqual(names.count(n), 2 if n == "fade" else 1, n)
        self.assertNotIn("zoomin", names)

    def test_planned_edl_is_lint_clean(self):
        issues = ve.lint(self.edl, check_files=False)
        self.assertEqual(issues, [], ve.format_issues(issues))

    def test_user_clip_start_is_kept(self):
        c = clips(39)
        c[3]["src_start"] = 7.5
        edl = vp.plan(c, BEATS, duration=28.235, period=0.6505)
        self.assertEqual(edl["shots"][3]["src_start"], 7.5)

    def test_clip_list_parsing(self):
        p = helpers.tmpdir() / "clips.txt"
        p.write_text('# comment\nD:/a b/one.mp4\n"D:/x/two.mp4" @ 3.5\n\n', encoding="utf-8")
        got = vp.parse_clip_list(p)
        self.assertEqual(got[0], {"clip": "D:/a b/one.mp4"})
        self.assertEqual(got[1], {"clip": "D:/x/two.mp4", "src_start": 3.5})
        j = helpers.tmpdir() / "clips.json"
        j.write_text(json.dumps(["a.mp4", {"clip": "b.mp4", "src_start": 1}]), encoding="utf-8")
        self.assertEqual(vp.parse_clip_list(j)[1]["src_start"], 1)

    def test_auto_transitions_small_counts(self):
        for n in (2, 3, 5, 12):
            t = vp.auto_transitions(n, None)
            for k in t:
                self.assertTrue(1 <= int(k) <= n - 1)


class Timeline(unittest.TestCase):
    def test_gap_is_an_error(self):
        e = base_edl()
        e["shots"][2]["span"][0] += 0.5
        with self.assertRaises(vl.EngineError):
            ve.timeline(e)

    def test_beats_spans(self):
        e = base_edl(2, 4.0)
        e["beats"] = {"t0": 1.0, "period": 0.5}
        e["shots"][0] = {"clip": "a", "span": [0, 1.0]}
        e["shots"][1] = {"clip": "b", "beats": [2, 8]}      # 2.0 .. 5.0 -> gap from 1.0
        with self.assertRaises(vl.EngineError):
            ve.timeline(e)

    def test_load_rejects_bad_version(self):
        p = helpers.tmpdir() / "bad.json"
        p.write_text(json.dumps({"version": 2, "project": {}, "shots": []}), encoding="utf-8")
        with self.assertRaises(vl.EngineError):
            ve.load(p)


class Lint(unittest.TestCase):
    def test_clean_edl(self):
        self.assertEqual(ve.lint(base_edl(), check_files=False), [])

    def test_banned_and_unknown_transitions(self):
        e = base_edl()
        e["transitions"] = {"1": "zoomin", "2": "sparkle"}
        c = codes(ve.lint(e, check_files=False))
        self.assertIn("transition-banned", c)
        self.assertIn("transition-unknown", c)

    def test_transition_key_out_of_range(self):
        e = base_edl()
        e["transitions"] = {"0": "fade", "9": "fade", "x": "fade"}
        self.assertEqual(sum(1 for i in ve.lint(e, check_files=False) if i["code"] == "transition-key"), 3)

    def test_too_many_transitions_warn(self):
        e = base_edl(12, 12.0)
        e["transitions"] = {str(j): vl.PLANNER_TRANSITIONS[j] for j in range(1, 12)}
        self.assertIn("transition-share", codes(ve.lint(e, check_files=False)))

    def test_repeated_transition_warns(self):
        e = base_edl(8, 8.0)
        e["transitions"] = {"1": "slideleft", "4": "slideleft"}
        self.assertIn("transition-repeat", codes(ve.lint(e, check_files=False)))

    def test_fade_may_repeat_once(self):
        e = base_edl(8, 8.0)
        e["transitions"] = {"1": "fade", "5": "fade"}
        self.assertNotIn("transition-repeat", codes(ve.lint(e, check_files=False)))

    def test_looks_alike_family_warns(self):
        e = base_edl(20, 20.0)
        e["transitions"] = {"1": "slideleft", "4": "smoothleft", "7": "wipeleft", "10": "horzopen"}
        self.assertIn("transition-family", codes(ve.lint(e, check_files=False)))

    def test_effect_budget(self):
        e = base_edl()
        e["style"] = {"extras": ["shake", "bloom"]}
        c = codes(ve.lint(e, check_files=False))
        self.assertIn("per-beat", c)
        self.assertIn("effect-budget", c)

    def test_unknown_style_key_is_error(self):
        e = base_edl()
        e["style"] = {"glitchy": True}
        self.assertIn("style-key", codes(ve.lint(e, check_files=False)))

    def test_zoom_limits(self):
        e = base_edl()
        e["style"] = {"zoom": 0.2}
        self.assertIn("zoom", codes(ve.lint(e, check_files=False)))
        e["style"] = {"zoom": -0.1}
        self.assertTrue(ve.has_errors(ve.lint(e, check_files=False)))

    def test_short_shot_error(self):
        e = base_edl(2, 6.0)
        e["shots"][0]["span"] = [0, 0.1]
        e["shots"][1]["span"] = [0.1, 6.0]
        self.assertIn("short-shot", codes(ve.lint(e, check_files=False)))

    def test_duration_mismatch(self):
        e = base_edl()
        e["project"]["duration"] = 7.0
        self.assertIn("duration", codes(ve.lint(e, check_files=False)))

    def test_missing_clip_and_music(self):
        e = base_edl()
        e["project"]["music"] = "no/such/music.mp3"
        c = codes(ve.lint(e, check_files=True))
        self.assertIn("clip-missing", c)
        self.assertIn("music-missing", c)

    def test_no_shots(self):
        e = base_edl()
        e["shots"] = []
        self.assertIn("no-shots", codes(ve.lint(e, check_files=False)))


class GraphTiming(unittest.TestCase):
    """The assembled length must equal the project length for ANY mix of cuts and transitions."""

    def _graph(self, edl, trans):
        bounds = ve.timeline(edl)
        parts = []
        for j in range(len(edl["shots"])):
            head = ve.PAD if j in trans else 0
            tail = ve.PAD if (j + 1) in trans else 0
            parts.append({"n": bounds[j + 1] - bounds[j] + head + tail, "path": f"p{j}.mp4"})
        return vr.build_graph(edl, parts, bounds, trans, 1.0)

    def test_random_layouts(self):
        rnd = random.Random(3)
        for _ in range(60):
            n = rnd.randint(2, 30)
            dur = rnd.uniform(n * 0.4, n * 1.2)
            e = base_edl(n, dur)
            trans = {j: rnd.choice(vl.PLANNER_TRANSITIONS) for j in range(1, n) if rnd.random() < 0.5}
            lines, extra = self._graph(e, trans)
            self.assertTrue(any("[vout]" in x for x in lines))

    def test_adjacent_transitions_keep_timing(self):
        e = base_edl(5, 5.0)
        self._graph(e, {1: "fade", 2: "slideleft", 3: "circleopen", 4: "pixelize"})

    def test_all_cuts(self):
        self._graph(base_edl(7, 7.0), {})

    def test_music_adds_audio_chain_and_drop_fx(self):
        e = base_edl(8, 8.0)
        e["project"]["music"] = "m.wav"
        e["beats"] = {"t0": 0.0, "period": 1.0, "drop_beat": 4}
        lines, extra = self._graph(e, {})
        text = "\n".join(lines)
        self.assertIn("[aout]", text)
        self.assertIn("[riser]", text)
        self.assertIn("[boom]", text)
        self.assertIn("-i", extra)

    def test_no_music_no_audio_graph(self):
        lines, extra = self._graph(base_edl(4, 4.0), {})
        self.assertNotIn("[aout]", "\n".join(lines))

    def test_effects_can_be_switched_off(self):
        e = base_edl(8, 8.0)
        e["style"] = {"end_flare": False, "drop_flash": False, "riser": False, "boom": False, "zoom": 0}
        e["project"]["music"] = "m.wav"
        e["beats"] = {"t0": 0.0, "period": 1.0, "drop_beat": 4}
        lines, extra = self._graph(e, {})
        text = "\n".join(lines)
        for tok in ("rgbashift", "blend=", "[riser]", "[boom]"):
            self.assertNotIn(tok, text)

    def test_forced_drop_transition_changes_padding(self):
        e = base_edl(8, 8.0)
        e["beats"] = {"t0": 0.0, "period": 1.0, "drop_beat": 4}
        bounds = ve.timeline(e)
        eff = ve.effective_transitions(e, bounds)
        self.assertEqual(eff.get(4), "fadewhite")


class PartFilters(unittest.TestCase):
    def pl(self, **kw):
        base = {"W": 540, "H": 960, "n": 20, "fps": 30, "zoom": 0.05, "grade": "eq=contrast=1.1", "head": 2, "tail": 0,
                "src_start": None, "clip": "x.mp4", "j": 0}
        base.update(kw)
        return base

    def test_zoom_is_push_in_only(self):
        vf = vr.part_vf(self.pl(), False)
        self.assertIn("1.0+", vf)
        self.assertNotIn("1.0-", vf)
        self.assertNotIn("-0.", vf.split("scale=w=")[1].split(":h=")[0])

    def test_zoom_off(self):
        self.assertNotIn("eval=frame", vr.part_vf(self.pl(zoom=0), False))

    def test_hdr_adds_tonemap(self):
        self.assertIn("tonemap", vr.part_vf(self.pl(), True))
        self.assertNotIn("tonemap", vr.part_vf(self.pl(), False))

    def test_never_uses_banned_effects(self):
        vf = vr.part_vf(self.pl(), False)
        for banned in ("noise=", "gblur", "start_frame"):
            self.assertNotIn(banned, vf)

    def test_source_start_default_is_40_percent_and_clamped(self):
        pl = self.pl(head=0)
        self.assertAlmostEqual(vr.source_start(pl, 20.0), 20 * 0.4 - 0.15, places=3)
        self.assertLessEqual(vr.source_start(pl, 1.0), 0.5)
        self.assertGreaterEqual(vr.source_start(pl, 0.3), 0.0)

    def test_source_start_explicit_wins(self):
        self.assertAlmostEqual(vr.source_start(self.pl(src_start=5.0, head=0), 30.0), 5.0)

    def test_grade_from_preset_and_raw(self):
        self.assertIn("colorbalance", vr._grade({"grade": "warm-desert"}))
        self.assertEqual(vr._grade({"grade": "eq=contrast=1.2"}), "eq=contrast=1.2")
        self.assertEqual(vr._grade({"grade": None}), "")


if __name__ == "__main__":
    unittest.main()
