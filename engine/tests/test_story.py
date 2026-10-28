"""Story-first selection: chapters, slots against the music, the choice inside a chapter, the storyboard and the audit.
Pure Python on synthetic inventories (the end-to-end parts at the bottom need FFmpeg)."""
import json
import unittest

import helpers
import vid_story as st
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def inv_rec(i, device="iPhone", minute=None, dur=10.0, dup=None, error=None):
    minute = i if minute is None else minute
    return {"index": i, "path": f"clip{i}.mp4", "name": f"clip{i}.mp4", "duration": dur, "dup_of": dup, "error": error,
            "device": device, "taken": f"2026-10-08 {6 + minute // 60:02d}:{minute % 60:02d}:00",
            "taken_epoch": 1791400000.0 + minute * 60.0}


def score(i, q=80.0, energy=50.0, h=None, flags=None, **extra):
    s = {"index": i, "path": f"clip{i}.mp4", "quality": q, "energy": energy, "best_start": 1.0, "flags": flags or [],
         "hash": (i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1) if h is None else h, "shot_size": ("wide", "medium", "close")[i % 3],
         "wins": [[1.0, q, energy / 10.0], [3.0, q, energy / 5.0], [5.0, q, energy / 8.0]]}
    s.update(extra)
    return s


def world(n=60, gap_after=None, devices=("iPhone",)):
    gap_after = gap_after or {}
    minute, inv, sc = 0, [], {}
    for i in range(n):
        inv.append(inv_rec(i, devices[i % len(devices)], minute))
        sc[i] = score(i, 60 + (i * 7) % 35, (i * 13) % 100)
        minute += 1 + gap_after.get(i, 0)
    return inv, sc


BEATS = {"period": 0.6, "t0": 0.2, "duration": 45.0, "beats": [round(0.2 + 0.6 * k, 3) for k in range(74)],
         "beat_strength": [0.9 if k % 2 == 0 else 0.3 for k in range(74)],
         "sections": [{"label": "intro", "start": 0.2, "end": 8.0, "energy": 0.1},
                      {"label": "drop", "start": 8.6, "end": 20.0, "energy": 0.8},
                      {"label": "build", "start": 20.6, "end": 30.0, "energy": 0.3},
                      {"label": "drop", "start": 30.6, "end": 45.0, "energy": 0.9}]}


def cfg_of(*chapters, **extra):
    return dict({"chapters": list(chapters)}, **extra)


def chap(name, a, b, weight=1, energy="building", **kw):
    return dict({"name": name, "from": a, "to": b, "weight": weight, "energy": energy}, **kw)


class Chapters(unittest.TestCase):
    def test_draft_chapters_follow_the_pauses_and_dampen_big_blocks(self):
        inv, sc = world(40, gap_after={19: 90})
        cfg = st.propose_chapters(inv, sc, gap_min=35)
        self.assertEqual([(c["from"], c["to"]) for c in cfg["chapters"]], [(0, 19), (20, 39)])
        small = st.propose_chapters(world(40, gap_after={4: 90})[0], sc)["chapters"]
        self.assertEqual([(c["from"], c["to"]) for c in small], [(0, 4), (5, 39)])
        self.assertLess(small[1]["weight"] / small[0]["weight"], (35 / 5) ** 0.99, "weight grows slower than the footage")
        self.assertTrue(all(0 <= c["energy"] <= 1 for c in cfg["chapters"]))

    def test_duplicates_and_unreadable_clips_are_ignored(self):
        inv, sc = world(10)
        inv[3]["dup_of"] = 1
        inv[4]["error"] = "bad"
        cfg = st.propose_chapters(inv, sc)
        self.assertEqual(sum(c["clips"] for c in cfg["chapters"]), 8)
        with self.assertRaises(vl.EngineError):
            st.propose_chapters([inv_rec(0, error="x")], {})

    def test_validation(self):
        good = cfg_of(chap("a", 0, 5), chap("b", 6, 9, energy=0.7))
        self.assertIs(st.validate_chapters(good), good)
        for bad in ({}, {"chapters": []}, cfg_of({"name": "x", "from": 0}),
                    cfg_of(chap("a", 5, 2)), cfg_of(chap("a", 0, 5), chap("b", 3, 9)),
                    cfg_of(chap("a", 0, 5, weight=-1)), cfg_of(chap("a", 0, 5, energy="wild")),
                    cfg_of(chap("a", 0, 5, energy=2.0)), cfg_of(chap("a", 0, 5, order="random")),
                    cfg_of(chap("a", 0, 5), hook="flashback")):
            with self.assertRaises(vl.EngineError, msg=str(bad)):
                st.validate_chapters(bad)


class SlotsAndMusic(unittest.TestCase):
    def test_music_energy_is_relative_to_the_track(self):
        self.assertEqual(st.music_energy_at(BEATS, 2.0), 0.0)
        self.assertEqual(st.music_energy_at(BEATS, 40.0), 1.0)
        self.assertAlmostEqual(st.music_energy_at(BEATS, 25.0), (0.3 - 0.1) / 0.8, places=3)
        self.assertEqual(st.music_energy_at({"sections": []}, 5.0), 0.5)

    def test_slots_cover_the_whole_edit(self):
        slots = st.make_slots(BEATS, 45.0, None, "energy", 2)
        self.assertAlmostEqual(sum(s["len"] for s in slots), 45.0, places=2)
        self.assertEqual([s["i"] for s in slots], list(range(len(slots))))
        self.assertTrue(all(0 <= s["music"] <= 1 for s in slots))

    def test_peak_chapters_land_on_loud_music(self):
        slots = st.make_slots(BEATS, 45.0, None, "beat", 2)
        chapters = [chap("calm start", 0, 9, energy="calm"), chap("peak", 10, 19, energy="peak"),
                    chap("calm middle", 20, 29, energy="calm"), chap("peak end", 30, 39, energy="peak")]
        counts = st.allocate(chapters, slots)
        self.assertEqual(sum(counts), len(slots))
        flat = [0.0] * len(slots)
        pos = 0
        for c, n in zip(chapters, counts):
            for _ in range(n):
                flat[pos] = st.chapter_energy(c)
                pos += 1
        even = len(slots) // 4
        even_cost = sum(abs(s["music"] - st.chapter_energy(chapters[min(i // max(even, 1), 3)])) for i, s in enumerate(slots))
        chosen_cost = sum(abs(s["music"] - e) for s, e in zip(slots, flat))
        self.assertLessEqual(chosen_cost, even_cost + 1e-9)
        for c, n in zip(chapters, counts):
            self.assertGreaterEqual(n, 1)

    def test_counts_stay_near_the_weights(self):
        slots = st.make_slots(BEATS, 45.0, None, "beat", 2)
        chapters = [chap("a", 0, 9, weight=1), chap("b", 10, 19, weight=3)]
        a, b = st.allocate(chapters, slots)
        total = a + b
        self.assertGreaterEqual(b / total, 0.6)
        self.assertEqual(st.allocate([chap("a", 0, 9, weight=0), chap("b", 10, 19)], slots)[0], 0)

    def test_flex_lets_a_peak_chapter_take_more_of_the_loud_music(self):
        slots = st.make_slots(BEATS, 45.0, None, "beat", 2)
        chapters = [chap("a", 0, 9, weight=1, energy="calm"), chap("b", 10, 19, weight=1, energy="peak"),
                    chap("c", 20, 29, weight=1, energy="calm")]
        tight = st.allocate(chapters, slots, flex=0.1)
        loose = st.allocate(chapters, slots, flex=0.6)
        self.assertEqual(sum(tight), sum(loose))
        self.assertGreaterEqual(max(abs(l - t) for l, t in zip(loose, tight)), 1)

    def test_impossible_requests_are_explained(self):
        slots = st.make_slots(BEATS, 45.0, None, "energy", 2)
        many = [chap(f"c{i}", i * 2, i * 2 + 1) for i in range(len(slots) + 5)]
        with self.assertRaises(vl.EngineError):
            st.allocate(many, slots)
        with self.assertRaises(vl.EngineError):
            st.allocate([chap("a", 0, 5, weight=0)], slots)


class Selection(unittest.TestCase):
    def run_select(self, inv, sc, chapters, notes=None, **kw):
        return st.select(inv, sc, BEATS, chapters, notes, duration=45.0, pacing="energy", **kw)

    def basic(self, **kw):
        inv, sc = world(90, devices=("iPhone", "iPhone", "DJI"))
        cfg = cfg_of(chap("start", 0, 29, energy="calm"), chap("action", 30, 59, energy="peak"),
                     chap("end", 60, 89, energy="calm"), **kw)
        return inv, sc, cfg

    def test_a_full_storyboard_with_reasons_and_alternates(self):
        inv, sc, cfg = self.basic()
        sb = self.run_select(inv, sc, cfg)
        self.assertEqual(len(sb["rows"]), sb["slots"])
        self.assertEqual(sb["warnings"], [])
        clips = [r["clip"] for r in sb["rows"]]
        self.assertEqual(len(clips), len(set(clips)), "no clip twice")
        for r in sb["rows"]:
            self.assertTrue(r["why"])
            self.assertLessEqual(len(r["alternates"]), 2)
            self.assertNotIn(r["clip"], r["alternates"])
            self.assertGreaterEqual(r["start"], 0)

    def test_the_trip_order_is_kept(self):
        inv, sc, cfg = self.basic()
        sb = self.run_select(inv, sc, cfg)
        epochs = [inv[r["clip"]]["taken_epoch"] for r in sb["rows"]]
        self.assertEqual(epochs, sorted(epochs))
        order = [r["chapter"] for r in sb["rows"]]
        self.assertEqual(order, sorted(order, key=["start", "action", "end"].index))

    def test_chapters_are_filled_from_their_own_range(self):
        inv, sc, cfg = self.basic()
        sb = self.run_select(inv, sc, cfg)
        ranges = {c["name"]: (c["from"], c["to"]) for c in cfg["chapters"]}
        for r in sb["rows"]:
            a, b = ranges[r["chapter"]]
            self.assertTrue(a <= r["clip"] <= b)

    def test_bad_clips_are_left_out_unless_forced(self):
        inv, sc, cfg = self.basic()
        sc[40]["flags"] = ["blurry"]
        sc[41]["internal_cut"] = True
        for i in range(30, 60):
            if i not in (40, 41):
                sc[i]["quality"] = 20.0                            # everything else is poor: the flagged ones would otherwise win
        sc[40]["quality"] = sc[41]["quality"] = 99.0
        notes = {"clip44.mp4": {"skip": True}}
        sb = self.run_select(inv, sc, cfg, notes)
        picked = {r["clip"] for r in sb["rows"]}
        self.assertFalse(picked & {40, 41, 44})
        cfg["chapters"][1]["must"] = [40]
        self.assertIn(40, {r["clip"] for r in self.run_select(inv, sc, cfg, notes)["rows"]})

    def test_duplicates_of_the_same_scene_are_not_both_chosen(self):
        inv, sc, cfg = self.basic()
        for i in range(30, 60):
            sc[i]["hash"] = 0b1011 if i % 2 == 0 else (1 << 62)       # two scenes only
        sb = self.run_select(inv, sc, cfg)
        action = [r for r in sb["rows"] if r["chapter"] == "action"]
        hashes = [sc[r["clip"]]["hash"] for r in action]
        self.assertLessEqual(len(action), len(action))
        self.assertGreaterEqual(len(set(hashes)), 2)

    def test_the_underused_camera_and_people_are_looked_after(self):
        inv, sc, cfg = self.basic(min_people_share=0.3)
        for i in range(90):
            sc[i]["quality"] = 70.0
            sc[i]["energy"] = 50.0
        notes = {f"clip{i}.mp4": {"role": "portrait"} for i in range(0, 90, 7)}
        sb = self.run_select(inv, sc, cfg, notes)
        people = sum(1 for r in sb["rows"] if r["people"]) / len(sb["rows"])
        self.assertGreaterEqual(people, 0.2)
        share = sb["coverage"]["devices_in_edit"].get("DJI", 0)
        self.assertGreaterEqual(share, 0.2)                        # DJI is a third of the library

    def test_topics_are_capped(self):
        inv, sc, cfg = self.basic(max_per_topic=2)
        notes = {f"clip{i}.mp4": {"topic": "food"} for i in range(30, 60, 2)}
        sb = self.run_select(inv, sc, cfg, notes)
        self.assertLessEqual(sum(1 for r in sb["rows"] if r["topic"] == "food"), 3)

    def test_action_slots_start_on_the_action_moment(self):
        inv, sc, cfg = self.basic()
        notes = {f"clip{i}.mp4": {"role": "action", "mood": "intense"} for i in range(30, 60)}
        sb = self.run_select(inv, sc, cfg, notes)
        row = next(r for r in sb["rows"] if r["chapter"] == "action")
        self.assertEqual(row["start_kind"], "action moment")
        self.assertEqual(row["start"], 3.0)                        # the window with the most motion in `score()`

    def test_energy_order_matches_clips_to_the_music(self):
        inv, sc, cfg = self.basic()
        cfg["chapters"][1]["order"] = "energy"
        sb = self.run_select(inv, sc, cfg)
        rows = [r for r in sb["rows"] if r["chapter"] == "action"]
        pairs = sorted((r["music"], r["energy"]) for r in rows)
        self.assertGreaterEqual(pairs[-1][1], pairs[0][1])          # the loudest slot gets at least as lively a clip

    def test_teaser_opens_with_the_best_action_of_the_peak_chapter(self):
        inv, sc, cfg = self.basic(hook="teaser", teaser_chapter="action")
        sb = self.run_select(inv, sc, cfg)
        first = sb["rows"][0]
        self.assertTrue(first["chapter"].endswith("(teaser)"))
        self.assertTrue(30 <= first["clip"] <= 59)
        self.assertEqual(len(sb["rows"]), sb["slots"])
        self.assertNotIn(first["clip"], [r["clip"] for r in sb["rows"][1:]])

    def test_a_thin_chapter_is_reported(self):
        inv, sc, cfg = self.basic()
        cfg["chapters"][1] = chap("action", 30, 32, energy="peak")
        sb = self.run_select(inv, sc, cfg)
        self.assertTrue(any("only" in w or "usable" in w for w in sb["warnings"]), sb["warnings"])

    def test_shortlist_holds_the_picks_and_their_alternates(self):
        inv, sc, cfg = self.basic()
        sb = self.run_select(inv, sc, cfg)
        for name, items in sb["shortlist"].items():
            picked = {r["clip"] for r in sb["rows"] if r["chapter"] == name}
            self.assertTrue(picked <= set(items))
            self.assertGreater(len(items), len(picked))

    def test_the_most_striking_clip_lands_on_the_loudest_slot_of_an_energy_chapter(self):
        inv, sc, cfg = self.basic()
        cfg["chapters"][1]["order"] = "energy"
        for i in range(30, 60):
            sc[i]["energy"] = 20.0
            sc[i]["quality"] = 70.0
        sc[45]["energy"] = 99.0
        sb = self.run_select(inv, sc, cfg)
        rows = [r for r in sb["rows"] if r["chapter"] == "action"]
        loudest = max(rows, key=lambda r: r["music"])
        self.assertEqual(loudest["clip"], 45)

    def test_the_slot_of_the_drop_gets_the_loudest_energy(self):
        beats = dict(BEATS, drop=4.4)                              # on the beat grid: 0.2 + 7 * 0.6
        slots = st.make_slots(beats, 45.0, 4.4, "energy", 2)
        drops = [s for s in slots if s["drop"]]
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["music"], 1.0)
        self.assertAlmostEqual(drops[0]["t"], 4.4, places=2)
        self.assertFalse(slots[0]["drop"])

    def test_the_topic_cap_is_per_chapter(self):
        inv, sc, cfg = self.basic(max_per_topic=1)
        notes = {f"clip{i}.mp4": {"topic": "dunes"} for i in range(90)}
        sb = self.run_select(inv, sc, cfg, notes)
        per_chapter = {}
        for r in sb["rows"]:
            per_chapter[r["chapter"]] = per_chapter.get(r["chapter"], 0) + 1
        self.assertGreater(len(per_chapter), 1)
        self.assertEqual(len(sb["rows"]), sb["slots"], "a global cap would have starved the later chapters")

    def test_outputs(self):
        inv, sc, cfg = self.basic()
        sb = self.run_select(inv, sc, cfg)
        md = st.storyboard_markdown(sb)
        self.assertIn("## Shots", md)
        self.assertEqual(md.count("\n| "), len(sb["rows"]) + len(sb["chapters"]) + 2)
        text = st.clip_list_text(sb)
        self.assertEqual(len([ln for ln in text.splitlines() if ln and not ln.startswith("#")]), len(sb["rows"]))
        self.assertIn(" @ ", text)
        self.assertIn("cameras", st.format_storyboard(sb))


def edl_for(clips, seconds=1.0):
    return {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": seconds * len(clips), "music": None},
            "style": {"drop_flash": False, "end_flare": False},
            "shots": [{"clip": f"clip{c}.mp4", "span": [i * seconds, (i + 1) * seconds]} for i, c in enumerate(clips)]}


class Audit(unittest.TestCase):
    def test_trip_order_and_missing_chapters(self):
        inv, sc = world(60)
        cfg = cfg_of(chap("a", 0, 19), chap("b", 20, 39), chap("c", 40, 59))
        rep = st.audit_edl(edl_for([5, 3, 8, 25, 27]), inv, sc, None, None, cfg)
        codes = [f["code"] for f in rep["findings"]]
        self.assertIn("trip-order", codes)
        self.assertIn("chapter-missing", codes)
        self.assertEqual(rep["stats"]["order_inversions"], 1)
        self.assertNotIn("trip-order", [f["code"] for f in st.audit_edl(edl_for([5, 3]), inv, sc, None, None, None, chronological=False)["findings"]])

    def test_energy_against_the_music(self):
        inv, sc = world(60)
        for i in range(60):
            sc[i]["energy"] = 5.0                                    # every clip is calm
        edl = edl_for(list(range(0, 45)))
        rep = st.audit_edl(edl, inv, sc, BEATS)
        self.assertTrue(any(f["code"] == "energy-mismatch" for f in rep["findings"]))
        for i in range(60):
            sc[i]["energy"] = 10.0 + 80.0 * st.music_energy_at(BEATS, i + 0.5)
        rep = st.audit_edl(edl, inv, sc, BEATS)
        self.assertGreater(rep["stats"]["energy_correlation"], 0.8)
        self.assertFalse(any(f["code"] == "energy-curve" for f in rep["findings"]))

    def test_reading_time_people_topics_cameras(self):
        inv, sc = world(30, devices=("iPhone", "iPhone", "iPhone", "DJI"))
        notes = {f"clip{i}.mp4": {"role": "portrait", "topic": "food"} for i in range(5)}
        edl = edl_for([0, 1, 2, 3, 4, 8, 12, 16], seconds=0.6)
        cfg = cfg_of(chap("a", 0, 29), min_people_share=0.9)
        rep = st.audit_edl(edl, inv, sc, None, notes, cfg)
        codes = [f["code"] for f in rep["findings"]]
        for want in ("short-reading-time", "topic-run", "few-people"):
            self.assertIn(want, codes, codes)
        self.assertIn("camera-underused", [f["code"] for f in st.audit_edl(edl_for([0, 1, 2, 4, 5, 6]), inv, sc)["findings"]])
        self.assertIn("story audit", st.format_audit(rep))

    def test_unknown_clips_do_not_crash(self):
        inv, sc = world(5)
        rep = st.audit_edl(edl_for([99, 98]), inv, sc)
        self.assertEqual(rep["findings"][0]["code"], "no-inventory-match")


class Cli(unittest.TestCase):
    def test_commands_exist_and_chapters_are_written(self):
        import mh_video
        root = helpers.tmpdir() / "storycli"
        root.mkdir(exist_ok=True)
        inv, sc = world(30, gap_after={14: 120})
        vl.save_json(root / "inventory.json", {"summary": {}, "clips": inv})
        vl.save_json(root / "scores.json", {"summary": {}, "scores": list(sc.values())})
        parser = mh_video.build_parser()
        a = parser.parse_args(["story", "chapters", "--work", str(root)])
        self.assertEqual(mh_video.cmd_story(a), 0)
        cfg = json.loads((root / "chapters.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cfg["chapters"]), 2)
        vl.save_json(root / "beats.json", BEATS)
        vl.save_json(root / "ch.json", cfg_of(chap("a", 0, 14), chap("b", 15, 29)))
        a = parser.parse_args(["story", "select", "--work", str(root), "--beats", str(root / "beats.json"),
                               "--chapters", str(root / "ch.json"), "--duration", "45", "--pacing", "beat"])
        self.assertEqual(mh_video.cmd_story(a), 0)
        self.assertTrue((root / "storyboard.json").is_file() and (root / "storyboard.md").is_file())
        self.assertTrue((root / "storyboard-clips.txt").is_file())
        with self.assertRaises(SystemExit):
            parser.parse_args(["story", "select", "--work", str(root)])


@NEEDS_FF
@helpers.slow
class RealMedia(unittest.TestCase):
    def test_score_keeps_every_window_and_the_sound_level(self):
        import vid_score as vs
        root = helpers.tmpdir() / "storymedia"
        root.mkdir(exist_ok=True)
        loud = helpers.make_clip(root / "loud.mp4", "testsrc2", size="320x568", seconds=6.0, audio="anoisesrc=c=brown:a=0.5")
        quiet = helpers.make_clip(root / "quiet.mp4", "smptebars", size="320x568", seconds=6.0, audio="anoisesrc=c=brown:a=0.01")
        silent = helpers.make_clip(root / "silent.mp4", "testsrc", size="320x568", seconds=6.0)
        recs = []
        for i, p in enumerate((loud, quiet, silent)):
            info = vl.probe(p)
            recs.append({"index": i, "path": vl.ffp(p), "name": p.name, "size": 10 + i, "mtime": 1, "duration": info["duration"],
                         "has_audio": info["has_audio"], "dup_of": None, "error": None})
        out = vs.score_inventory(recs, hw=False)
        by = {s["index"]: s for s in out}
        self.assertTrue(by[0]["wins"] and all(len(w) == 3 for w in by[0]["wins"]))
        self.assertGreater(by[0]["audio_db"], by[1]["audio_db"] + 10)
        self.assertNotIn("audio_db", by[2])
        self.assertGreater(by[0]["sound"], by[1]["sound"])

    def test_contact_sheets_of_a_subset_with_three_frames(self):
        import vid_sheets
        root = helpers.tmpdir() / "storysheets"
        root.mkdir(exist_ok=True)
        clips = helpers.make_clips(root / "c", 3, 3.0)
        recs = []
        for i, p in enumerate(clips):
            recs.append({"index": 10 + i, "path": vl.ffp(p), "name": p.name, "duration": 3.0, "error": None})
        sheets = vid_sheets.clip_sheets(recs, root / "out", size=80, cols=2, rows=2, frames=3)
        self.assertEqual(len(sheets), 1)
        w, h = vl.probe(sheets[0])["width"], vl.probe(sheets[0])["height"]
        self.assertGreater(w, 2 * 80 * 2)                              # strips of three frames are wide
        one = vid_sheets.clip_sheets(recs, root / "out1", size=80, cols=3, rows=1, frames=1)
        self.assertEqual(len(one), 1)


if __name__ == "__main__":
    unittest.main()
