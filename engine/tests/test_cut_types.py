"""Phase 3 of the cut craft arc: per-transition overlap (`transition_frames`), J-cut / L-cut for clip sound, and motion
alignment. All pure Python: the timing model is checked on the filter graph, not on rendered media."""
import random
import unittest
from unittest import mock

import helpers  # noqa: F401
import vid_cutlogic as cl
import vid_editorial as ed
import vid_edl as ve
import vid_render as vr
import vid_sfx as sfx
import vidlib as vl


def edl_of(n, seconds_each=1.0, fps=30):
    return {"version": 1, "project": {"width": 90, "height": 160, "fps": fps, "duration": n * seconds_each, "music": None},
            "style": {"drop_flash": False, "end_flare": False, "zoom": 0},
            "shots": [{"clip": f"c{i}.mp4", "span": [i * seconds_each, (i + 1) * seconds_each]} for i in range(n)]}


def graph(edl, trans):
    bounds = ve.timeline(edl)
    parts = []
    for j in range(len(edl["shots"])):
        pl = vr.part_plan(edl, j, bounds, trans, 1.0)
        parts.append({"n": pl["n"], "path": f"p{j}.mp4"})
    return vr.build_graph(edl, parts, bounds, trans, 1.0), bounds


class Overlap(unittest.TestCase):
    def test_default_and_override(self):
        e = edl_of(6)
        e["transition_frames"] = {"2": 8, "3": 5, "4": 99, "5": True}
        ov = ve.overlap_frames(e, {1: "fade", 2: "fade", 3: "fade", 4: "fade", 5: "fade"})
        self.assertEqual(ov, {1: 4, 2: 8, 3: 4, 4: 4, 5: 4})          # odd, huge or boolean values fall back to the default

    def test_part_plan_splits_the_overlap_over_both_shots(self):
        e = edl_of(6)
        e["transition_frames"] = {"2": 10}
        trans = {2: "dissolve"}
        bounds = ve.timeline(e)
        a, b = vr.part_plan(e, 1, bounds, trans, 1.0), vr.part_plan(e, 2, bounds, trans, 1.0)
        self.assertEqual((a["head"], a["tail"]), (0, 5))
        self.assertEqual((b["head"], b["tail"]), (5, 0))
        self.assertEqual(a["n"], 30 + 5)

    def test_assembled_length_equals_project_length_for_any_overlaps(self):
        rnd = random.Random(11)
        for _ in range(80):
            n = rnd.randint(3, 24)
            e = edl_of(n, rnd.choice([0.5, 0.8, 1.0, 1.4]))
            trans = {j: rnd.choice(vl.PLANNER_TRANSITIONS) for j in range(1, n) if rnd.random() < 0.45}
            tf = {str(j): rnd.choice([2, 4, 6, 8, 12, 16]) for j in trans}
            e["transition_frames"] = tf
            lens = [b - a for a, b in zip(ve.timeline(e), ve.timeline(e)[1:])]
            ovs = ve.overlap_frames(e, trans)
            if any(ovs.get(j, 0) // 2 + ovs.get(j + 1, 0) // 2 > ln - 2 for j, ln in enumerate(lens)):
                continue
            (lines, _extra), bounds = graph(e, trans)                      # build_graph raises if the assembled length is wrong
            x = [ln for ln in lines if "xfade" in ln]
            self.assertEqual(len(x), len(trans))
            for ln in x:
                j = int(ln.split("[x")[-1].rstrip("]"))
                self.assertIn(f"duration={ovs[j] / 30:.4f}", ln)

    def test_lint_validates_transition_frames(self):
        e = edl_of(6)
        e["transitions"] = {"2": "fade"}
        e["transition_frames"] = {"2": 8}
        self.assertFalse([i for i in ve.lint(e, check_files=False) if i["level"] == "error"])
        for bad in ({"2": 7}, {"2": 1}, {"2": 30}, {"x": 4}, {"9": 4}, {"2": 4.0}):
            e["transition_frames"] = bad
            self.assertTrue(ve.has_errors(ve.lint(e, check_files=False)), bad)
        e["transition_frames"] = []
        self.assertTrue(ve.has_errors(ve.lint(e, check_files=False)))
        e["transition_frames"] = {"4": 8}
        self.assertIn("transition-frames-unused", [i["code"] for i in ve.lint(e, check_files=False)])

    def test_lint_rejects_overlaps_a_short_shot_cannot_hold(self):
        e = edl_of(4, 0.4)                                     # 12-frame shots
        e["transitions"] = {"1": "fade", "2": "fade"}
        e["transition_frames"] = {"1": 24, "2": 24}
        self.assertIn("transition-frames", [i["code"] for i in ve.lint(e, check_files=False)])


class QaWindows(unittest.TestCase):
    def test_a_long_dip_is_expected_for_its_whole_length(self):
        import vid_qa
        e = edl_of(6)
        e["transitions"] = {"3": "fadeblack"}
        short = vid_qa.expected_events(e, 30, 6.0)
        e["transition_frames"] = {"3": 24}                               # 0.8 s dip: 0.4 s each side of the cut
        long = vid_qa.expected_events(e, 30, 6.0)
        t = 3.0
        inside = lambda ev, x: any(a <= x <= b for a, b in ev["black"])
        self.assertFalse(inside(short, t + 0.38))
        self.assertTrue(inside(long, t + 0.38) and inside(long, t - 0.38))
        e["transitions"] = {"3": "fadewhite"}
        e["transition_frames"] = {"3": 24}
        wl = vid_qa.expected_events(e, 30, 6.0)
        self.assertTrue(any(a <= t + 0.38 <= b and a <= t - 0.38 for a, b in wl["white"]))


class QaEditorialIds(unittest.TestCase):
    """Found on the first real run: editorial warnings had no `id`, so `qa` crashed when printing the report."""

    def test_editorial_warnings_become_qa_checks_with_an_id(self):
        import vid_qa
        e = edl_of(6)
        scores = {i: {"index": i, "path": f"c{i}.mp4", "shot_size": "wide", "motion_dx": 0.0, "motion_dy": 0.0,
                      "motion_mag": 0.0, "hash": i, "quality": 80, "energy": 50, "best_start": 0.0, "flags": []} for i in range(6)}
        checks, audit = vid_qa.audit_edit(e, scores)
        self.assertTrue(any(c.get("id") == "shot-size-repeat" for c in checks), [c.get("id") for c in checks])
        for c in checks:
            self.assertIn("id", c, c)
            self.assertIn("level", c)
        counts = {k: sum(1 for c in checks if c["level"] == k) for k in ("error", "warn", "info", "ok")}
        report = {"video": "x.mp4", "verdict": "warn", "checks": checks, "quality_score": 94, "duration": 6.0, "frames": 180,
                  "size_mb": 1.0, "counts": counts}
        self.assertIn("shot-size-repeat", vid_qa.format_report(report))


class QaNotes(unittest.TestCase):
    def test_notes_override_the_measured_shot_size_in_the_editorial_checks(self):
        import vid_qa
        e = edl_of(6)
        scores = {i: {"index": i, "path": f"c{i}.mp4", "shot_size": "close", "motion_dx": 0.0, "motion_dy": 0.0,
                      "motion_mag": 0.0, "hash": i * 977, "quality": 80, "energy": 50, "best_start": 0.0, "flags": []} for i in range(6)}
        checks, _ = vid_qa.audit_edit(e, scores)
        self.assertTrue(any(c["id"] == "shot-size-repeat" for c in checks))
        notes = {f"c{i}.mp4": {"shot_size": ("wide", "medium", "detail")[i % 3]} for i in range(6)}
        checks, audit = vid_qa.audit_edit(e, scores, notes)
        self.assertFalse(any(c["id"] == "shot-size-repeat" for c in checks))
        self.assertEqual(audit["distinct_shot_sizes"], 3)                      # detail counts as close

    def test_override_score_turns_a_direction_into_motion(self):
        s = cl.override_score({"shot_size": "wide", "motion_dx": 3.0}, {"motion_dir": "left"})
        self.assertLess(s["motion_dx"], 0)
        self.assertEqual(s["shot_size"], "wide")
        self.assertEqual(cl.override_score(None, None), {})


class FitFrames(unittest.TestCase):
    def test_closing_and_dissolves_breathe_but_short_shots_shrink_them(self):
        decisions = [{"j": 1, "type": "soften", "transition": "dissolve"}, {"j": 2, "type": "closing", "transition": "fade"}]
        bounds = [0, 30, 40, 70]                               # shot 1 is only 10 frames
        out = cl.fit_frames(decisions, bounds)
        ov = {d["j"]: d["frames"] for d in out}
        self.assertLessEqual(ov[1] // 2 + ov[2] // 2, 10 - 2)
        self.assertTrue(all(v % 2 == 0 and v >= 2 for v in ov.values()))

    def test_apply_stores_non_default_frames_and_lint_accepts_it(self):
        e = edl_of(6)
        scores = {i: {"index": i, "path": f"c{i}.mp4", "shot_size": "wide" if i % 2 == 0 else "close", "motion_dx": 0.0,
                      "motion_dy": 0.0, "motion_mag": 0.0, "focus_x": 0.5, "focus_y": 0.5, "brightness": 100.0,
                      "saturation": 50.0, "energy": 50.0, "hash": 0 if i % 2 == 0 else (1 << 40) - 1} for i in range(6)}
        cl.apply(e, cl.analyse(e, scores))
        self.assertEqual(e["transition_frames"]["5"], 8)       # the closing fade
        self.assertFalse(ve.has_errors(ve.lint(e, check_files=False)))
        graph(e, ve.effective_transitions(e))                  # still assembles to the project length


class LeadLag(unittest.TestCase):
    seg = {"j": 2, "clip": "c.mp4", "start": 3.0, "seconds": 1.0, "delay": 2.0, "out_seconds": 1.0, "gain_db": 0.0, "tempo": None}

    def test_j_cut_starts_earlier_and_l_cut_lingers(self):
        s = sfx.apply_lead_lag(self.seg, {"lead": 0.4, "lag": 0.25})
        self.assertEqual((s["delay"], s["start"], s["out_seconds"], s["seconds"]), (1.6, 2.6, 1.65, 1.65))
        self.assertEqual((s["lead"], s["lag"]), (0.4, 0.25))

    def test_lead_is_limited_by_the_start_of_the_video_and_of_the_clip(self):
        first = dict(self.seg, delay=0.2)
        self.assertEqual(sfx.apply_lead_lag(first, {"lead": 0.8})["delay"], 0.0)
        early = dict(self.seg, start=0.1)
        self.assertEqual(sfx.apply_lead_lag(early, {"lead": 0.8})["start"], 0.0)

    def test_nothing_requested_changes_nothing(self):
        self.assertIs(sfx.apply_lead_lag(self.seg, {}), self.seg)
        self.assertIs(sfx.apply_lead_lag(self.seg, {"lead": "x"}), self.seg)

    def test_ambient_segments_apply_lead_lag_but_not_to_retimed_shots(self):
        e = edl_of(3)
        e["style"]["ambient"] = {"level_db": -14}
        e["shots"][1]["audio"] = {"lead": 0.5, "lag": 0.3}
        e["shots"][2]["audio"] = {"lead": 0.5, "speed_follow": True}
        w = [{"j": j, "clip": f"c{j}.mp4", "start0": 2.0, "seconds": 1.0, "out_start": float(j), "out_seconds": 1.0,
              "speed": ({"from": 0.5, "to": 0.5} if j == 2 else None), "has_audio": True} for j in range(3)]
        with mock.patch.object(sfx, "mean_volume", return_value=-20.0):
            segs = sfx.ambient_segments(e, w, -20.0)
        by = {s["j"]: s for s in segs}
        self.assertEqual((by[1]["delay"], by[1]["out_seconds"]), (0.5, 1.8))
        self.assertNotIn("lead", by[0])
        self.assertNotIn("lead", by[2])                        # a retimed shot keeps its sound as it was
        lines, label = sfx.ambient_lines(segs, 1, sfx.DEFAULT_AMBIENT)
        self.assertTrue(lines and label)

    def test_validation(self):
        e = edl_of(3)
        e["style"]["ambient"] = True
        e["shots"][0]["audio"] = {"lead": 0.5}
        self.assertEqual([i for i in sfx.validate(e) if i["level"] == "error"], [])
        for bad in ({"lead": -1}, {"lag": 5}, {"lead": "a"}, {"lag": True}):
            e["shots"][0]["audio"] = bad
            self.assertTrue([i for i in sfx.validate(e) if i["level"] == "error"], bad)


class Alignment(unittest.TestCase):
    def test_align_start_picks_the_window_that_continues_the_movement(self):
        cands = [(0.0, 0.0, 0.0, 0.0), (0.75, -3.0, 0.0, 3.0), (1.5, 3.0, 0.0, 3.0), (2.25, -2.0, 0.0, 2.0)]
        self.assertEqual(cl.align_start(cands, "left", 2.0), 2.25)
        self.assertEqual(cl.align_start(cands, "left", 0.8), 0.75)
        self.assertEqual(cl.align_start(cands, "right", 0.0), 1.5)
        self.assertIsNone(cl.align_start(cands, "up", 0.0))
        self.assertIsNone(cl.align_start([], "left", 0.0))

    def test_align_edl_proposes_only_where_needed(self):
        e = edl_of(3)
        e["shots"][1]["src_start"] = 0.0
        scores = {0: {"index": 0, "path": "c0.mp4", "motion_dx": -4.0, "motion_dy": 0.0, "motion_mag": 4.0},
                  1: {"index": 1, "path": "c1.mp4", "motion_dx": 0.0, "motion_dy": 0.0, "motion_mag": 0.0},
                  2: {"index": 2, "path": "c2.mp4", "motion_dx": 0.0, "motion_dy": 0.0, "motion_mag": 0.0}}

        def measure(clip, dur, need):
            return [(0.0, 0.0, 0.0, 0.0), (1.5, -3.0, 0.0, 3.0)]

        with mock.patch.object(vl, "probe", return_value={"duration": 6.0}):
            props = cl.align_edl(e, scores, measure=measure)
        self.assertEqual(props, [{"j": 1, "direction": "left", "old": 0.0, "new": 1.5}])
        cl.apply_alignment(e, props)
        self.assertEqual(e["shots"][1]["src_start"], 1.5)

    def test_already_continuing_shots_are_left_alone(self):
        e = edl_of(2)
        scores = {0: {"index": 0, "path": "c0.mp4", "motion_dx": -4.0, "motion_dy": 0.0, "motion_mag": 4.0},
                  1: {"index": 1, "path": "c1.mp4", "motion_dx": -3.0, "motion_dy": 0.0, "motion_mag": 3.0}}
        with mock.patch.object(vl, "probe", return_value={"duration": 6.0}):
            self.assertEqual(cl.align_edl(e, scores, measure=lambda *a: [(1.5, -3.0, 0.0, 3.0)]), [])


def solid(rgb, w=16, h=9):
    return bytes(rgb) * (w * h)


class ContentFeatures(unittest.TestCase):
    def test_dominant_hue_follows_the_colourful_pixels(self):
        hue, sat = ed.dominant_hue([solid((20, 60, 220))])
        self.assertAlmostEqual(hue, 0.64, delta=0.03)                         # blue
        hue_r, _ = ed.dominant_hue([solid((220, 30, 20))])
        self.assertLess(ed.hue_distance(hue_r, 0.0), 0.03)                    # red sits at 0 on the circle
        self.assertIsNone(ed.dominant_hue([solid((128, 128, 128))])[0])       # grey has no hue
        self.assertEqual(ed.dominant_hue([]), (None, 0.0))
        # a grey road under a small patch of blue sky still reads as blue
        mixed = bytearray(solid((120, 120, 120)))
        mixed[:3 * 30] = solid((20, 60, 220), 10, 3)
        self.assertAlmostEqual(ed.dominant_hue([bytes(mixed)])[0], 0.64, delta=0.03)

    def test_hue_distance_is_circular(self):
        self.assertAlmostEqual(ed.hue_distance(0.95, 0.05), 0.1, places=3)
        self.assertIsNone(ed.hue_distance(None, 0.3))

    def test_flux_spots_a_cut_inside_a_clip(self):
        dark, bright = bytes([10]) * 100, bytes([240]) * 100
        self.assertEqual(ed.flux([dark, dark, dark]), 0.0)
        self.assertGreater(ed.flux([dark, dark, bright]), 200)
        self.assertEqual(ed.flux([dark]), 0.0)
        self.assertFalse(ed.internal_cut([dark] * 6))
        self.assertTrue(ed.internal_cut([dark, dark, dark, bright, bright, bright]))
        steady = [bytes([(i * 50 + x * 7) % 256 for x in range(100)]) for i in range(6)]
        self.assertFalse(ed.internal_cut(steady), "a fast pan changes every step alike: not a cut")
        self.assertTrue(ed.internal_cut([dark, bright]), "two frames: only an extreme jump counts")
        self.assertFalse(ed.internal_cut([dark, bytes([60]) * 100]))

    def test_local_motion_separates_a_moving_subject_from_a_still_scene(self):
        def scene(bx):
            buf = bytearray(ed.GW * ed.GH)
            for i in range(len(buf)):
                buf[i] = (i * 37) % 200                                     # fixed texture
            for y in range(8, 16):
                for x in range(bx, bx + 6):
                    buf[y * ed.GW + x] = 255
            return bytes(buf)
        still = ed.local_motion([scene(10), scene(10), scene(10)])
        moving = ed.local_motion([scene(10), scene(20), scene(30)])
        self.assertLess(still, 0.05)
        self.assertGreater(moving, still)

    def test_internal_cut_warning_and_colour_match(self):
        e = edl_of(4)
        feats = [{}, {"internal_cut": True}, {}, {}]
        issues = ed.editorial_issues(e, feats, [0, 30, 60, 90, 120], {})
        self.assertEqual([i["code"] for i in issues if i["code"] == "internal-cut"], ["internal-cut"])
        a = {"size": "wide", "hash": 0, "hue": 0.60, "colourfulness": 0.4, "brightness": 100.0, "saturation": 50.0,
             "dx": 0.0, "dy": 0.0, "mag": 0.0, "focus": None, "energy": 50.0}
        b = dict(a, hash=(1 << 40) - 1, hue=0.62, size="medium")
        d = cl.classify(a, b, {})
        self.assertEqual(d["type"], "colour-match")
        self.assertGreater(cl.content_change(a, dict(b, hue=0.1, brightness=200.0)), cl.content_change(a, b))
        self.assertEqual(cl.content_change(a, dict(a)), 0.0)


@unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
@helpers.slow
class RenderLongTransitionsAndJLCuts(unittest.TestCase):
    """End to end with real media: longer dissolves and J/L-cuts keep the video exactly as long as the project."""

    def test_render_keeps_the_project_length(self):
        import vid_qa
        root = helpers.tmpdir() / "cuttypes"
        root.mkdir(parents=True, exist_ok=True)
        clips = [helpers.make_clip(root / f"k{i}.mp4", helpers.SOURCES[i % 4], size="180x320", seconds=3.0,
                                   audio="sine=f=%d" % (200 + 80 * i)) for i in range(6)]
        music = root / "m.wav"
        helpers.click_track(music, seconds=6.0, step_at=None)
        edl = {"version": 1, "project": {"name": "ct", "width": 180, "height": 320, "fps": 30, "duration": 6.0,
                                         "music": vl.ffp(music)},
               "style": {"grade": "neutral", "zoom": 0, "drop_flash": False, "end_flare": False, "riser": False,
                         "boom": False, "ambient": {"level_db": -12}},
               "shots": [{"clip": vl.ffp(c), "span": [float(i), float(i + 1)], "src_start": 1.0} for i, c in enumerate(clips)],
               "transitions": {"2": "dissolve", "4": "slideleft"}, "transition_frames": {"2": 12, "4": 6},
               "rationale": {"2": {"type": "soften", "why": "test"}, "4": {"type": "motion-bridge", "why": "test"}}}
        edl["shots"][3]["audio"] = {"lead": 0.4, "lag": 0.2}
        path = root / "ct.edl.json"
        vl.save_json(path, edl)
        issues = ve.lint(edl)
        self.assertFalse(ve.has_errors(issues), ve.format_issues(issues))
        res = vr.render(path, encoder="libx264", workdir=root / "ct_work", log=lambda *_: None)
        out = res["out"]
        info = vl.probe(out)
        self.assertAlmostEqual(info["duration"], 6.0, delta=0.08)
        self.assertTrue(info["has_audio"])
        report = vid_qa.run_qa(out, path)
        self.assertNotIn("FAIL", str(report.get("verdict")))


if __name__ == "__main__":
    unittest.main()
