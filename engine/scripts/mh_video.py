#!/usr/bin/env python3
"""M.H. Video Editor engine: one CLI for the whole pipeline (standard library only).

  doctor                          check FFmpeg, filters, encoders
  probe   FOLDER  --work DIR      inventory footage (duration, rotation, HDR, duplicates)
  sheets  --work DIR              numbered contact sheets of the inventory (look at these!)
  beats   AUDIO   --out FILE      beat grid, tempo alternatives, drop
  score   --work DIR              measure sharpness/exposure/motion, best window, same-scene hashes
  rank    --work DIR              table of the best (or most energetic) clips
  pick    --work DIR --count N    story-ordered candidate clip list (calm intro, rising build, payoff, calm end)
  need    BEATS                   how many clips the beat grid needs
  plan    ...                     beat grid + ordered clip list -> edl.json
  lint    EDL                     effect/transition budget + file checks
  render  EDL [--preview]         build the video (cached parts, NVENC when available)
  verify  EDL VIDEO               quick stream/duration/size checks + a contact sheet of the result
  color   looks|lut|analyze|plan|scope   colour correction and grading tools
  text    fonts|import-srt|preview       on-screen text (Persian right-to-left supported)
  sfx     plan EDL                       what the sound design and ambient bed will do
  qa      VIDEO [--edl EDL]       full quality gate: black/white frames, colour cast, freezes, cut timing,
                                  loudness and peak, file size, effect/transition audit; exit 1 on errors
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vidlib as vl  # noqa: E402

REQUIRED_FILTERS = ["xfade", "concat", "zscale", "tonemap", "rgbashift", "blend", "drawbox", "gradients",
                    "anoisesrc", "sine", "loudnorm", "amix", "alimiter", "tile", "colorbalance", "vignette",
                    "unsharp", "tpad", "fade", "scale", "crop", "signalstats", "ebur128", "volumedetect", "freezedetect"]


def _filters(ff: str) -> set[str]:
    return vl.parse_filter_list(vl.run([ff, "-hide_banner", "-filters"]).stdout)


def cmd_doctor(a) -> int:
    try:
        ff = vl.find_ffmpeg(refresh=True)
    except vl.EngineError as e:
        print(f"FAIL ffmpeg: {e}")
        return 1
    ok = True
    ver = ".".join(str(x) for x in vl.ffmpeg_version())
    print(f"ok   ffmpeg {ver}: {ff['ffmpeg']}")
    print(f"ok   ffprobe: {ff['ffprobe']}")
    print(f"ok   python {sys.version.split()[0]}")
    have = _filters(ff["ffmpeg"])
    for f in REQUIRED_FILTERS:
        present = f in have
        ok &= present
        print(f"{'ok  ' if present else 'FAIL'} filter {f}")
    print("optional features (a missing one only matters when the EDL uses it; lint reports it then):")
    for feat in vl.FEATURE_FILTERS:
        miss = vl.missing_for(feat, have)
        print(f"{'ok  ' if not miss else 'warn'} {feat}" + (f": FFmpeg lacks {', '.join(miss)}" if miss else ""))
    enc = vl.run([ff["ffmpeg"], "-hide_banner", "-encoders"]).stdout
    has264 = "libx264" in enc
    ok &= has264
    print(f"{'ok  ' if has264 else 'FAIL'} encoder libx264")
    import vid_render
    nv = vid_render.nvenc_works()
    print(f"{'ok  ' if nv else 'info'} NVENC GPU encoding: "
          f"{'available (auto-selected for final renders)' if nv else 'not available, using libx264'}")
    hw = vid_render.hw_encoders()
    avail = [k for k, v in hw.items() if v]
    print(f"info hardware H.264 encoders: {', '.join(avail) if avail else 'none detected (libx264 will be used)'}")
    font = vl.find_font()
    print(f"{'ok  ' if font else 'warn'} font for contact-sheet labels: {font or 'none found (sheets will be unlabeled)'}")
    print(f"info filter-graph file option: {vl.filter_script_flag()}")
    print("\nREADY" if ok else "\nNOT READY: fix the FAIL lines above")
    return 0 if ok else 1


def cmd_probe(a) -> int:
    import vid_probe
    shifts = {}
    for item in a.clock_shift or []:
        key, _, minutes = item.rpartition("=")
        if not key or not minutes.lstrip("+-").isdigit():
            raise vl.EngineError(f"--clock-shift expects DEVICE=MINUTES (for example DJI=30), got '{item}'")
        shifts[key] = int(minutes)
    recs = vid_probe.scan(a.folder, a.recursive, utc_offset_min=int(round(a.utc_offset * 60)), device_shift_min=shifts)
    paths = vid_probe.write_inventory(recs, a.work)
    print(json.dumps(vid_probe.summarize(recs), indent=2))
    print(f"inventory: {paths['json']}")
    for r in [r for r in recs if r["error"]][:10]:
        print(f"unreadable: {r['name']}: {r['error']}")
    return 0


def cmd_sheets(a) -> int:
    import vid_probe
    import vid_sheets
    recs = vid_probe.load_inventory(a.work)
    outdir = Path(a.work) / "sheets"
    if a.only:
        import vid_score
        keep = vid_score.parse_index_spec(a.only)
        recs = [r for r in recs if r["index"] in keep and r.get("dup_of") is None]
        outdir = Path(a.work) / "sheets_selected"
        if not recs:
            raise vl.EngineError(f"--only {a.only} selects no clip")
    cols, rows = a.cols, a.rows
    if a.frames > 1 and (a.cols, a.rows) == (8, 5):
        cols, rows = max(1, 8 // a.frames), 5                    # strips are wider: fewer per row, same sheet width
    sheets = vid_sheets.clip_sheets(recs, outdir, a.size, cols, rows, a.pos, frames=a.frames)
    print("\n".join(sheets))
    print(f"{len(sheets)} sheets, {cols * rows} clips each; the yellow number is the inventory index"
          + (f"; each clip shows {a.frames} frames (start, middle, end)" if a.frames > 1 else ""))
    return 0


def cmd_beats(a) -> int:
    import vid_beats
    res = vid_beats.analyse(a.audio, a.max_seconds, bpm=a.bpm, beats_per_bar=a.beats_per_bar, first_beat=a.first_beat)
    if a.out:
        vl.save_json(a.out, res)
    print(json.dumps({k: v for k, v in res.items() if k not in ("beats", "note")}, indent=2))
    print(f"beats: {len(res['beats'])} (first: {res['beats'][:4]}); downbeats every {res.get('beats_per_bar', 4)}th from index {res['bar_phase']}")
    secs = res.get("sections") or []
    if secs:
        print(f"sections: {len(secs)} ({', '.join(s['label'] for s in secs)})")
        for s in secs:
            print(f"  {s['label']:6} {s['start']:.2f}-{s['end']:.2f}s  energy {s['energy']}")
    v = res["validation"]
    print(f"validation: second tempo {v['second_tempo_bpm']} BPM ({v['second_relation']}), drift {v['drift_pct']}%, "
          f"median onset error {v['median_onset_error_ms']} ms, bar confidence {v['bar_confidence']}")
    for note in v["notes"]:
        print(f"note: {note}")
    if res["drop_beat"] is not None:
        on_bar = (res["drop_beat"] - res["bar_phase"]) % 4 == 0
        print(f"drop at {res['drop']} s (beat {res['drop_beat']}): {'on a downbeat' if on_bar else 'NOT on a downbeat (beat ' + str((res['drop_beat'] - res['bar_phase']) % 4 + 1) + ' of the bar)'}")
    if res["confidence"] < 0.35:
        print("warning: low confidence; listen to the track and confirm the BPM before cutting")
    return 0



def cmd_score(a) -> int:
    import vid_probe
    import vid_score
    recs = vid_probe.load_inventory(a.work)
    prev = {} if a.rescore else vid_score.load_previous(a.work)
    scores = vid_score.score_inventory(recs, workers=a.workers, limit=a.limit, progress=print, previous=prev,
                                       min_duration=a.min_duration or 0.0)
    if a.min_duration:
        skipped = sum(1 for r in recs if r.get("dup_of") is None and (r.get("duration") or 0.0) < a.min_duration - 0.1)
        print(f"{skipped} clip(s) under {a.min_duration:g} s were not measured (the selection never uses them)")
    path = vid_score.save_scores(scores, a.work)
    print(json.dumps(vid_score.summarize(scores), indent=2))
    print(f"scores: {path}")
    return 0


def _load_candidates(a, skip_flagged=True, skip_similar=True):
    import vid_probe
    import vid_score
    recs = vid_probe.load_inventory(a.work)
    scores = vid_score.load_scores(a.work)
    return recs, scores


def _weaker_takes(a, recs, scores) -> set:
    """Indexes of clips that are the weaker take of a moment filmed more than once (unless --keep-repeats)."""
    if getattr(a, "keep_repeats", False):
        return set()
    import vid_filter
    res = vid_filter.exclusions(recs, scores, None, {"min_duration": 0.0, "block_flags": [], "block_shaky": False,
                                                       "block_covered": False, "block_internal_cut": False, "min_quality": 0.0})
    return {i for i, why in res["reasons"].items() if any(w.startswith("same-moment") for w in why)}


def cmd_rank(a) -> int:
    import vid_score
    recs, scores = _load_candidates(a)
    drop = (vid_score.parse_index_spec(a.exclude) or set()) | _weaker_takes(a, recs, scores)
    cands = vid_score.candidates(recs, scores, skip_flagged=not a.allow_flagged, skip_similar=not a.allow_similar,
                                 only=vid_score.parse_index_spec(a.only), exclude=drop, min_duration=a.min_duration)
    key = {"quality": lambda c: -c["quality"], "energy": lambda c: (-c["energy"], -c["quality"])}[a.by]
    print(f"{'idx':>4} {'quality':>7} {'energy':>6} {'blur':>5} {'bright':>6}  flags / name")
    for c in sorted(cands, key=key)[: a.top]:
        print(f"{c['index']:>4} {c['quality']:>7} {c['energy']:>6} {c['blur']:>5} {c['brightness']:>6}  "
              f"{','.join(c['flags']) or '-':<12} {c['name']}")
    print(f"({len(cands)} usable candidates of {len(recs)} clips)")
    return 0


def cmd_pick(a) -> int:
    import vid_plan
    import vid_score
    recs, scores = _load_candidates(a)
    drop = (vid_score.parse_index_spec(a.exclude) or set()) | _weaker_takes(a, recs, scores)
    cands = vid_score.candidates(recs, scores, skip_flagged=not a.allow_flagged, skip_similar=not a.allow_similar,
                                 only=vid_score.parse_index_spec(a.only), exclude=drop, min_duration=a.min_duration)
    intro, outro = a.intro_shots, 1
    body = a.count - intro - outro
    if body < 2:
        raise vl.EngineError("count is too small for intro + body + outro")
    frac = a.payoff_share
    if a.beats:
        b = vl.load_json(a.beats)
        if b.get("drop") and b.get("duration"):
            frac = max(0.2, min(0.8, 1.0 - b["drop"] / b["duration"]))
    payoff = max(1, round(body * frac))
    chosen = vid_score.pick(cands, intro, body - payoff, payoff, outro)
    lines = []
    for c in chosen:
        lines.append(f"{c['path']} @ {c['best_start']:.2f}    # idx {c['index']} quality {c['quality']} energy {c['energy']}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {a.out}: {len(chosen)} clips ({intro} intro, {body - payoff} build, {payoff} payoff, {outro} outro)")
    print("This is a CANDIDATE list: look at the contact sheets, swap clips you dislike, keep the story order.")
    return 0


def cmd_need(a) -> int:
    import vid_plan
    beats = vl.load_json(a.beats)
    body_t0 = vid_plan.body_start_time(beats, a.duration or beats["duration"], a.period or beats["period"], a.intro_shots,
                                       body_start=a.body_start)
    if a.follow_tempo:
        lay = vid_plan.layout_from_beats(beats, a.duration or beats["duration"], body_t0, a.intro_shots)
    elif a.pacing == "energy":
        lay = vid_plan.layout_paced(beats, a.duration or beats["duration"], body_t0, a.period or beats["period"], a.intro_shots)
    else:
        lay = vid_plan.layout(a.duration or beats["duration"], body_t0, a.period or beats["period"], a.intro_shots)
    print(json.dumps({"clips_needed": lay["shots"], "intro_shots": a.intro_shots, "body_beats": lay["last_beat"],
                      "body_starts_at": body_t0, "pacing": "tempo-follow" if a.follow_tempo else (a.pacing or "beat")}))
    return 0


def cmd_plan(a) -> int:
    import vid_plan
    beats = vl.load_json(a.beats)
    clips = vid_plan.parse_clip_list(a.clips)
    if a.scores:
        import vid_score
        n = vid_plan.apply_scores(clips, vid_score.load_scores(a.scores))
        print(f"best_start filled from scores for {n} clips")
    edl = vid_plan.plan(clips, beats, name=a.name, music=a.music or beats.get("file"), preset=a.preset,
                        duration=a.duration, intro_shots=a.intro_shots, period=a.period, grade=a.grade,
                        transitions=not a.no_transitions, align_bars=a.bar_align, pacing=a.pacing,
                        follow_tempo=a.follow_tempo, body_start=a.body_start)
    if a.smart_transitions:
        if a.no_transitions:
            raise vl.EngineError("--smart-transitions and --no-transitions contradict each other; use one")
        if not a.scores:
            raise vl.EngineError("--smart-transitions needs --scores (the work folder with scores.json)")
        import vid_cutlogic
        import vid_score
        notes = vl.load_json(a.notes) if a.notes else None
        decisions = vid_cutlogic.analyse(edl, vid_score.load_scores(a.scores), beats, notes)
        edl["transitions"] = {}
        vid_cutlogic.apply(edl, decisions, overwrite=True)
        print(vid_cutlogic.format_table(decisions, edl))
    if a.stabilize_shaky is not None:
        if not a.scores:
            raise vl.EngineError("--stabilize-shaky needs --scores (the work folder with scores.json)")
        import vid_score
        js = vid_plan.stabilize_shaky(edl, vid_score.load_scores(a.scores), a.stabilize_shaky)
        print(f"light stabilisation on {len(js)} shaky shot(s)" + (f": {', '.join(str(j) for j in js)}" if js else ""))
    vl.save_json(a.out, edl)
    print(f"wrote {a.out}: {len(edl['shots'])} shots, {len(edl['transitions'])} transitions")
    return 0


def cmd_lint(a) -> int:
    import vid_edl
    issues = vid_edl.lint(vid_edl.load(a.edl), check_files=not a.no_files)
    print(vid_edl.format_issues(issues))
    return 1 if vid_edl.has_errors(issues) else 0


def cmd_render(a) -> int:
    import vid_render
    kw = dict(preview=a.preview, encoder=a.encoder, workdir=a.work, use_cache=not a.no_cache, force=a.force, workers=a.workers,
              hwdecode=a.hwdecode, qa=a.qa, delivery=a.delivery, target_size_mb=a.target_size_mb, bitrate=a.bitrate,
              manifest=a.manifest, bundle=a.bundle, json_out=a.json, quality=a.quality)
    if a.all_formats:
        print(json.dumps(vid_render.render_formats(a.edl, None, **kw), indent=2))
    else:
        print(json.dumps(vid_render.render(a.edl, a.out, fmt=a.format, **kw), indent=2))
    return 0


def cmd_qa(a) -> int:
    import vid_qa
    scores = None
    if a.scores:
        import vid_score
        scores = vid_score.load_scores(a.scores)
    sheet = a.sheet or str(Path(a.video).with_suffix("")) + "_check.jpg"
    notes = vl.load_json(a.notes) if a.notes else None
    rep = vid_qa.run_qa(a.video, a.edl, scores=scores, sheet=sheet, notes=notes)
    out = a.json or str(Path(a.video).with_suffix("")) + ".qa.json"
    vid_qa.save_report(rep, out)
    print(vid_qa.format_report(rep))
    print(f"  report: {out}")
    if a.strict:
        return 0 if rep["verdict"] == "pass" else 1
    return 1 if rep["verdict"] == "fail" else 0


def cmd_vision(a) -> int:
    import vid_vision
    if a.action == "status":
        py = vid_vision.plugin_python()
        print(f"vision plug-in: {'ready (' + str(py) + ')' if py else 'not set up (python plugins/vision/setup.py)'}")
        if a.work:
            v = vid_vision.load(a.work)
            print(f"vision.json: {vid_vision.summarize(v) if v else 'not created yet (vision run)'}")
        return 0
    v = vid_vision.run(a.work, only=a.only, onnx=a.onnx, labels=a.labels, frames=a.frames, force=a.force)
    print(vid_vision.summarize(v))
    return 0


def cmd_story(a) -> int:
    import vid_probe
    import vid_score
    import vid_story
    import vid_vision
    inv = vid_probe.load_inventory(a.work)
    scores = vid_score.load_scores(a.work)
    vid_vision.merge_into_scores(scores, vid_vision.load(a.work))             # people and faces, if the plug-in was run
    work = Path(a.work)
    if a.action == "chapters":
        cfg = vid_story.propose_chapters(inv, scores, a.gap)
        out = a.out or str(work / "chapters.json")
        vl.save_json(out, cfg)
        print(f"{'#':>2} {'from-to':>9} {'clips':>5} {'min':>5} {'weight':>6} {'energy':>6}  name / cameras")
        for k, c in enumerate(cfg["chapters"]):
            print(f"{k:>2} {c['from']:>4}-{c['to']:<4} {c['clips']:>5} {c['minutes']:>5} {c['weight']:>6} {c['energy']:>6}  {c['name']}  {c['devices']}")
        print(f"wrote {out}: a DRAFT. Look at the footage (sheets --only RANGE --frames 3), then rename the chapters, set the weights "
              "(what the story needs, not what was filmed), the energy (calm / building / peak) and merge or split blocks.")
        return 0
    beats = vl.load_json(a.beats) if getattr(a, "beats", None) else None
    notes = vl.load_json(a.notes) if getattr(a, "notes", None) else None
    if a.action == "refine":
        import vid_moment
        sb = vl.load_json(a.storyboard)
        res = vid_moment.refine_storyboard(sb, inv, scores, a.work, workers=a.workers, log=print)
        out = Path(a.out or a.storyboard)
        vl.save_json(out, res)
        out.with_suffix(".md").write_text(vid_story.storyboard_markdown(res), encoding="utf-8")
        clips = out.with_name(out.stem + "-clips.txt")
        clips.write_text(vid_story.clip_list_text(res), encoding="utf-8")
        for r in res["rows"]:
            m = r.get("moment") or {}
            print(f"  slot {r['slot']:>2} clip {r['clip']:>3}: start {r['start']:>6.2f}s  {m.get('motion_type', m.get('error', '?')):10} "
                  f"score {m.get('score', '-')}  peak at {m.get('peak_at') if m.get('peak_at') is not None else '-'}  action ends {m.get('end_of_action', '-')}")
        print(f"wrote {out}, {out.with_suffix('.md')} and {clips}")
        return 0
    if a.action == "exclusions":
        import csv
        import vid_filter
        cfg = vl.load_json(a.chapters) if a.chapters else {}
        filters = dict(cfg.get("filters") or {})
        if a.min_duration is not None:
            filters["min_duration"] = a.min_duration
        res = vid_filter.exclusions(inv, scores, notes, filters, cfg.get("dedupe"))
        print(vid_filter.format_report(res, inv))
        by = {r["index"]: r for r in inv}
        out = Path(a.out or work / "exclusions.json")
        vl.save_json(out, {"summary": vid_filter.summarize(res, sum(1 for r in inv if r.get("dup_of") is None)),
                           "reasons": {str(i): w for i, w in sorted(res["reasons"].items())}, "clusters": res["clusters"]})
        with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["index", "taken", "device", "duration", "reasons", "name"])
            for i, why in sorted(res["reasons"].items()):
                r = by[i]
                w.writerow([i, r.get("taken", ""), r.get("device", ""), round(r.get("duration") or 0, 1), " ".join(why), r["name"]])
        print(f"wrote {out} and {out.with_suffix('.csv')}: why each clip is left out")
        return 0
    import vid_profile
    if a.action == "profile":
        gpath = vid_profile.global_path()
        if a.forget_global:
            if gpath.is_file():
                print("the global profile held:\n" + vid_profile.describe(vid_profile.load(gpath)))
                gpath.unlink()
                print(f"removed {gpath}")
            else:
                print(f"there is no global profile ({gpath})")
            return 0
        if a.promote:
            ppath = Path(a.profile or work / "profile.json")
            new = vid_profile.promote(vid_profile.load(ppath), vid_profile.load(gpath), str(ppath))
            gpath.parent.mkdir(parents=True, exist_ok=True)
            vid_profile.save(new, gpath)
            print(f"wrote {gpath} (topics, roles and weights only; clip decisions stay with the project):\n" + vid_profile.describe(new))
            return 0
        prof = vid_profile.load_for(a.work, a.profile)
        print(vid_profile.describe(prof))
        return 0
    if a.action == "feedback":
        sb = vl.load_json(a.storyboard)
        fb = vl.load_json(a.feedback)
        notes_by = vid_story.cl.notes_by_path(notes)
        views = {r["index"]: vid_story.clip_view(r, scores[r["index"]], vid_story._note(notes_by, r))      # noqa: SLF001
                 for r in inv if r["index"] in scores and "quality" in scores[r["index"]]}
        ppath = Path(a.profile or work / "profile.json")
        prof, rows = vid_profile.learn(vid_profile.load(ppath), sb, fb, inv, views)
        vid_profile.save(prof, ppath)
        out = Path(a.out or a.storyboard)
        vl.save_json(out, dict(sb, rows=rows))
        out.with_suffix(".md").write_text(vid_story.storyboard_markdown(dict(sb, rows=rows)), encoding="utf-8")
        out.with_name(out.stem + "-clips.txt").write_text(vid_story.clip_list_text(dict(sb, rows=rows)), encoding="utf-8")
        stuck = [r['slot'] for r in rows if r.get('banned')]
        if stuck:
            print(f"WARNING: slot(s) {', '.join(str(s) for s in stuck)} still use a banned clip with no alternate: run `story select` again (the ban is remembered)")
        print(vid_profile.describe(prof))
        print(f"wrote {ppath} and the updated storyboard {out}; the next `story select` uses what was learned")
        return 0
    if a.action == "review":
        import vid_review
        sb = vl.load_json(a.storyboard)
        out = Path(a.out or Path(a.storyboard).with_name("review.html"))
        vid_review.write(out, sb, inv, scores, a.work, beats=beats, notes=notes, log=print, video=not a.no_video,
                         workers=a.workers or 6)
        print(f"wrote {out}" + ("" if a.no_video else f" and the proxy videos in {out.stem}_media/ (keep them together)") +
              ": open it in a browser (it works offline); decide, then press 'Export decisions' and run "
              f"`story feedback --feedback decisions.json`")
        return 0
    cfg = vl.load_json(a.chapters)
    if a.action == "select":
        if not beats:
            raise vl.EngineError("story select needs --beats")
        profile = None
        if not a.no_profile:
            profile = vid_profile.load_for(a.work, a.profile)
            if not (profile["weights"] or profile["topics"] or profile["roles"] or profile["banned"] or profile["favoured"]
                    or profile["pins"]):
                profile = None
            else:
                print("using remembered preferences:\n" + vid_profile.describe(profile))
        approved = None
        if a.cut_from:
            src = vl.load_json(a.cut_from)
            approved = {r["clip"]: r.get("start") for r in src.get("rows") or [] if not r.get("banned")}
            if not approved:
                raise vl.EngineError(f"{a.cut_from} has no shots to cut down from")
            print(f"cut-down of {a.cut_from}: only its {len(approved)} approved shots are used, at their approved starts")
        kw = dict(duration=a.duration, body_start=a.body_start, pacing=a.pacing, intro_shots=a.intro_shots, period=a.period,
                  method=a.method, profile=profile, approved=approved)
        out = Path(a.out or work / "storyboard.json")
        if a.variants:
            sbs = vid_story.select_variants(inv, scores, beats, cfg, notes, **kw)
            for name, one in sbs.items():
                vp = out.with_name(f"{out.stem}.{name}.json")
                vl.save_json(vp, one)
                vp.with_suffix(".md").write_text(vid_story.storyboard_markdown(one), encoding="utf-8")
                vp.with_name(vp.stem + "-clips.txt").write_text(vid_story.clip_list_text(one), encoding="utf-8")
            print(vid_story.format_comparison(vid_story.compare_variants(sbs)))
            print(f"wrote {out.stem}.<variant>.json/.md and -clips.txt for {', '.join(sbs)}; show the user the comparison and the "
                  "storyboard of the variant they prefer")
            sb = sbs[a.variant]
        else:
            sb = vid_story.select(inv, scores, beats, cfg, notes, variant=a.variant, **kw)
        vl.save_json(out, sb)
        out.with_suffix(".md").write_text(vid_story.storyboard_markdown(sb), encoding="utf-8")
        clips = Path(a.clips) if a.clips else out.with_name(out.stem + "-clips.txt")
        clips.write_text(vid_story.clip_list_text(sb), encoding="utf-8")
        print(vid_story.format_storyboard(sb))
        every = sorted({i for v in sb["shortlist"].values() for i in v})
        print("shortlist to look at (clips of the storyboard and their best alternates): " + ",".join(str(i) for i in every))
        print(f"wrote {out}, {out.with_suffix('.md')} and {clips}. Show the storyboard to the user and wait for approval before `plan`.")
        return 0
    import vid_edl
    edl = vid_edl.load(a.edl)
    rep = vid_story.audit_edl(edl, inv, scores, beats, notes, cfg if a.chapters else None, chronological=not a.free_order)
    print(vid_story.format_audit(rep))
    return 0


def cmd_cuts(a) -> int:
    import vid_cutlogic
    import vid_edl
    import vid_score
    edl = vid_edl.load(a.edl)
    beats = vl.load_json(a.beats) if a.beats else None
    notes = vl.load_json(a.notes) if a.notes else None
    scores = vid_score.load_scores(a.scores)
    decisions = vid_cutlogic.analyse(edl, scores, beats, notes, keep_existing=not a.overwrite)
    print(vid_cutlogic.format_table(decisions, edl))
    proposals = []
    if a.align:
        proposals = vid_cutlogic.align_edl(edl, scores, notes)
        for p in proposals:
            print(f"align shot {p['j']}: src_start {p['old']} -> {p['new']} (continues the {p['direction']} movement of shot {p['j'] - 1})")
        if not proposals:
            print("align: no shot needs a different start")
    if not a.write:
        print("dry run: nothing written. Review the reasons against the contact sheets; --write stores transitions + rationale.")
        return 0
    vid_cutlogic.apply(edl, decisions, overwrite=a.overwrite)
    vid_cutlogic.apply_alignment(edl, proposals)
    vl.save_json(a.edl, edl)
    print(f"wrote {a.edl}: transitions and rationale ({'replaced' if a.overwrite else 'existing entries kept'})")
    return 0


def cmd_color(a) -> int:
    import vid_color
    act = a.action
    if act == "looks":
        for name, desc in vid_color.list_looks():
            print(f"{name:16} {desc}")
        return 0
    if act == "lut":
        look, strength, _, _ = vid_color.resolve_look({"look": a.look, "strength": a.strength})
        fn = vid_color._blend_towards_identity(vid_color.look_function(look), strength)
        data = vid_color.build_lut(fn, a.size)
        print(vid_color.write_cube(a.out, data, a.size, title=f"mh-{a.look}"))
        return 0
    if act == "scope":
        import vid_probe  # noqa: F401
        info = vl.probe(a.source)
        print(vid_color.scope_sheet(a.source, a.out, a.at, hdr=bool(info.get("hdr"))))
        return 0
    if act == "analyze":
        info = vl.probe(a.source)
        t = a.at if a.at is not None else info["duration"] * 0.4
        st = vid_color.measure_window(a.source, t, 1.0, bool(info.get("hdr")))
        if not st:
            raise vl.EngineError("could not measure the clip")
        corr, why = vid_color.propose_correction(st, a.strength)
        print(json.dumps({"stats": {k: round(v, 1) for k, v in st.items()}, "proposed_correct": corr, "reasons": why}, indent=2))
        if a.scope:
            print(vid_color.scope_sheet(a.source, a.scope, t, hdr=bool(info.get("hdr"))))
        return 0
    if act == "cameras":
        import vid_camera
        import vid_probe
        import vid_score
        inv = vid_probe.load_inventory(a.work)
        scores = vid_score.load_scores(a.work)
        rep = vid_camera.estimate(inv, scores, a.work, window=a.window, workers=a.workers or 6, reference=a.reference)
        vl.save_json(Path(a.work) / "camera_match.json", rep)
        print(vid_camera.format_report(rep))
        if not a.edl:
            print(f"wrote {Path(a.work) / 'camera_match.json'}; add --edl EDL --write to apply the gains to an edit")
            return 0
        import vid_edl as ve
        edl = ve.load(a.edl)
        if not a.write:
            print("(dry run: add --write to multiply the camera gains into the shots' corrections)")
            return 0
        n = vid_camera.apply_to_edl(edl, rep, inv)
        vl.save_json(a.edl, edl)
        print(f"matched the cameras of {n} shot(s) in {a.edl} (shots matched before were left alone)")
        return 0
    if act == "plan":
        import vid_edl as ve
        edl = ve.load(a.edl)
        only = None
        if a.only:
            import vid_score
            only = vid_score.parse_index_spec(a.only)
        plan = vid_color.plan_corrections(edl, Path(a.edl).resolve().parent, a.strength, a.match, a.reference, only, wb=a.wb)
        for j in sorted(plan):
            item = plan[j]
            print(f"shot {j:>3}: {json.dumps(item['correct']) if item['correct'] else 'no correction':<80} {'; '.join(item['reasons'])}")
        if a.write:
            n = vid_color.apply_plan(edl, plan, a.overwrite)
            vl.save_json(a.edl, edl)
            print(f"wrote corrections for {n} shot(s) into {a.edl}")
        else:
            print("(dry run: add --write to store these numbers in the EDL, then review with `color scope` and render)")
        return 0
    raise vl.EngineError(f"unknown color action {act}")


def cmd_text(a) -> int:
    import vid_text
    act = a.action
    if act == "templates":
        for name, desc in vid_text.list_templates():
            print(f"{name:14} {desc}")
        return 0
    if act == "fonts":
        for lang in ("fa", "en"):
            f = vid_text.pick_font(lang, None, True)
            print(f"{lang}: {f['family']}  ({f['file'] or 'not found on this machine: libass will fall back'})")
        fa = [x for x in vid_text.font_index() if any(k in x["family"].lower() for k in ("iran", "vazir", "nazanin", "sahel", "samim", "dubai", "tahoma", "yekan"))]
        for x in sorted(fa, key=lambda z: z["family"]):
            print(f"  {x['family']:22} {x['subfamily']:10} {x['path']}")
        return 0
    if act == "import-srt":
        import vid_edl as ve
        edl = ve.load(a.edl)
        items = vid_text.parse_srt(a.srt)
        for it in items:
            it.update(position=a.position, style=a.style, animation=a.animation)
        edl.setdefault("texts", []).extend(items)
        vl.save_json(a.edl, edl)
        print(f"added {len(items)} text(s) from {a.srt} to {a.edl}")
        return 0
    if act == "preview":
        import vid_edl as ve
        import vid_render as vr
        edl = ve.load(a.edl)
        p = ve.project(edl)
        w, h = vr.output_size(edl, a.format, a.scale)
        work = Path(a.edl).resolve().parent / (Path(a.edl).stem + "_work") / "text_preview"
        ass, fonts = vid_text.build_ass(edl, w, h, work)
        if ass is None:
            raise vl.EngineError("the EDL has no texts")
        src = (["-ss", f"{a.at:.2f}", "-i", vl.ffp(a.video)] if a.video else
               ["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0=0x2a3a5a:c1=0xd9a45b:x0=0:y0=0:x1={w}:y1={h}:d=1:r=30"])
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2," if a.video else "") + \
            vid_text.text_filter(ass, fonts).replace("ass=", "ass=", 1)
        # the ass filter runs on the frame at time `at`: shift timestamps so the preview shows that moment
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y"] + src + ["-vf", vf if a.video else vf, "-frames:v", "1", "-update", "1", vl.ffp(a.out)]) \
            if a.video else vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i",
                                    f"gradients=s={w}x{h}:c0=0x2a3a5a:c1=0xd9a45b:x0=0:y0=0:x1={w}:y1={h}:d={max(a.at + 1, 2):.1f}:r=30,{vf}",
                                    "-ss", f"{a.at:.2f}", "-frames:v", "1", "-update", "1", vl.ffp(a.out)])
        print(a.out)
        return 0
    raise vl.EngineError(f"unknown text action {act}")


def cmd_sfx(a) -> int:
    import vid_edl as ve
    import vid_render as vr
    import vid_sfx
    edl = ve.load(a.edl)
    bounds = ve.timeline(edl)
    trans = ve.effective_transitions(edl, bounds)
    fps = ve.project(edl)["fps"]
    sd = vid_sfx.sound_design(edl)
    print("sound design:", "off" if not sd else json.dumps(sd))
    if sd and sd["whoosh"]:
        for e in vid_sfx.whoosh_events(trans, bounds, fps, sd):
            print(f"  whoosh {e['kind']:6} at {e['t']:.2f}s  ({e['name']})")
    amb = vid_sfx.ambient_config(edl)
    print("ambient:", "off" if not amb else json.dumps(amb))
    if amb:
        plan = vr.audio_plan(edl, bounds, trans, Path(a.edl).resolve().parent, log=lambda *_: None)
        for key in plan["key"].get("ambient", [])[:-2]:
            print(f"  shot {key[0]:>3}: source {key[1]:.2f}s for {key[2]:.2f}s, gain {key[3]:+.1f} dB" + (f", tempo {key[4]:.2f}" if key[4] else ""))
    return 0


def cmd_verify(a) -> int:
    import vid_render
    sheet = a.sheet or str(Path(a.video).with_suffix("")) + "_check.jpg"
    res = vid_render.verify(a.edl, a.video, sheet)
    for c in res["checks"]:
        print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}")
    print(f"contact sheet: {res.get('sheet')}")
    return 0 if res["ok"] else 1


ARG_HELP = {
    "frames": "frames looked at per clip",
    "profile": "profile.json of remembered preferences (default: <work>/profile.json and the global one)",
    "no_profile": "ignore remembered preferences",
    "no_video": "no proxy videos: one self-contained page with frames only",
    "cut_from": "an approved storyboard.json: make another length from its shots only, at their approved starts",
    "window": "seconds between two clips of different cameras that count as the same moment",
    "force": "look at every clip again, even those whose earlier result is still valid",
    "feedback": "decisions.json exported by the review page",
    "onnx": "an image-classification model of your own (ONNX); adds scene labels",
    "labels": "text file with one label per line for --onnx",
    "variant": "how the story is told: balanced, action first, or people first",
    "variants": "write all three variants and print a comparison table",
    "method": "beam = optimise the whole sequence at once; greedy = chapter by chapter",
    "storyboard": "storyboard.json written by `story select`",
    "min_duration": "ignore clips shorter than this many seconds (default 5; 0 = all)",
    "keep_repeats": "keep every take of a moment (default: only the best take of each)",
    "chapters": "chapters.json: the story's chapters (name, from, to, weight, energy ...)",
    "body_start": "seconds where the beat-by-beat body starts (use the music's drop time to land the flash on the first body cut)",
    "pacing": "beat = one shot per beat; energy = hold calm beats, cut fast on strong ones",
    "notes": "shot-notes.json: the agent's reading of the footage (role, mood, people, topic, shot_size ...)",
    "folder": "folder with the footage", "work": "work folder (inventory, scores, sheets, beats live here)",
    "recursive": "also scan sub-folders", "size": "thumbnail width in pixels", "cols": "thumbnails per row",
    "rows": "rows per sheet", "audio": "music file (mp3, wav, m4a ...)", "out": "output file",
    "max_seconds": "analyse only the first N seconds", "workers": "parallel workers (default: min(4, CPUs))",
    "limit": "measure only the first N clips", "by": "what to sort by", "top": "how many rows to print",
    "allow_flagged": "include clips flagged dark / blurry / static ...", "allow_similar": "include look-alike clips",
    "count": "how many clips to pick", "beats": "beats.json from the `beats` command", "payoff_share": "share of the picks reserved for the payoff after the drop",
    "intro_shots": "shots before the beat grid starts", "clips": "text file: one clip path per line, optional ` @ seconds`",
    "name": "project name (also the output file name)", "music": "music file written into the EDL",
    "preset": "output format preset (reels-9x16, youtube-16x9, square-1x1)", "duration": "override the video duration in seconds",
    "period": "override the beat period in seconds", "grade": "look or preset name", "no_transitions": "hard cuts only",
    "edl": "the *.edl.json file", "no_files": "skip the file checks (offline lint)", "preview": "render at half size",
    "encoder": "video encoder (auto uses NVENC when available)", "no_cache": "re-render every part", "video": "the rendered video",
    "json": "report file", "sheet": "contact sheet file", "strict": "exit 1 on warnings as well", "look": "look name (`color looks`)",
    "strength": "how strongly the look is applied, 0-1", "at": "time in seconds", "source": "video file",
    "match": "how shots are matched to each other", "reference": "shot number to match (with --match reference)",
    "write": "store the proposal in the EDL (otherwise only print it)", "overwrite": "replace corrections that are already in the EDL",
    "srt": "SRT subtitle file", "position": "where the text sits (title, top, center, bottom, lower-third)",
    "style": "text style (title, caption, subtitle)", "animation": "fade, pop, slide-up or none", "scale": "preview size relative to the frame",
}
ARG_HELP_BY_COMMAND = {
    ("sheets", "pos"): "position in each clip (0-1) of the frame shown", ("score", "work"): "work folder containing inventory.json",
    ("beats", "out"): "write the full analysis to this beats.json", ("plan", "out"): "EDL file to write",
    ("pick", "out"): "clip list file to write", ("render", "out"): "output video (default: next to the EDL)",
    ("render", "work"): "work folder for the cached parts", ("color lut", "size"): "LUT size (33 = standard)",
    ("color lut", "out"): ".cube file to write", ("color scope", "out"): "image file to write", ("text preview", "out"): "image file to write",
    ("color plan", "only"): "shot numbers, e.g. 0-5,9", ("text preview", "at"): "time in seconds on the video timeline",
    ("text preview", "format"): "format preset to lay the text out for", ("color analyze", "at"): "time in seconds (default: 40%% into the clip)",
    ("color scope", "at"): "time in seconds", ("qa", "edl"): "the EDL the video was rendered from (lets the gate excuse planned effects)",
    ("verify", "sheet"): "contact sheet file", ("render", "format"): None,
}


def _document_arguments(parser, path: str = "") -> None:
    """Give every argument a help text (from ARG_HELP) so -h and docs/CLI.md explain themselves."""
    for action in parser._actions:                                       # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):               # noqa: SLF001
            for name, sub in action.choices.items():
                _document_arguments(sub, f"{path} {name}".strip())
        elif action.help is None and action.dest != "help":
            action.help = ARG_HELP_BY_COMMAND.get((path, action.dest)) or ARG_HELP.get(action.dest)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mh_video", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check FFmpeg, filters, encoders and optional features").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("probe", help="inventory a footage folder (duration, rotation, fps, HDR, duplicates)")
    p.add_argument("folder"); p.add_argument("--work", required=True); p.add_argument("--recursive", action="store_true")
    p.add_argument("--utc-offset", type=float, default=3.5,
                   help="hours added to a camera's UTC creation time to get local time (Iran 3.5); phones that store local time ignore it")
    p.add_argument("--clock-shift", action="append", metavar="DEVICE=MINUTES",
                   help="minutes added to the times of a camera whose clock or time zone was wrong, e.g. DJI=30 (repeatable)")
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("sheets", help="numbered contact sheets of the inventory (look at them to choose shots)")
    p.add_argument("--work", required=True); p.add_argument("--size", type=int, default=200)
    p.add_argument("--cols", type=int, default=8); p.add_argument("--rows", type=int, default=5)
    p.add_argument("--pos", type=float, default=0.4, help="position in each clip (0-1) of the frame shown")
    p.add_argument("--only", help="only these inventory indexes, e.g. 100-140,152 (duplicates skipped); written to sheets_selected")
    p.add_argument("--frames", type=int, default=1, choices=[1, 2, 3, 4],
                   help="frames per clip: 3 = start, middle and end side by side (see what happens inside a clip)")
    p.set_defaults(fn=cmd_sheets)
    p = sub.add_parser("beats", help="beat grid, tempo alternatives, downbeats and the drop of a music file")
    p.add_argument("audio"); p.add_argument("--out"); p.add_argument("--max-seconds", type=float)
    p.add_argument("--bpm", type=float, help="force this tempo instead of estimating it (tap-tempo / known BPM)")
    p.add_argument("--beats-per-bar", type=int, default=4, help="beats per bar for the downbeat grid (4 = common time, 3 = waltz)")
    p.add_argument("--first-beat", type=float, help="time in seconds of the first beat (overrides the detected phase)")
    p.set_defaults(fn=cmd_beats)
    p = sub.add_parser("score", help="measure sharpness, exposure and motion of every clip; best window; same-scene hashes")
    p.add_argument("--work", required=True); p.add_argument("--workers", type=int); p.add_argument("--limit", type=int)
    p.add_argument("--rescore", action="store_true", help="ignore earlier scores (default: reuse unchanged files)")
    p.add_argument("--min-duration", type=float, help="do not measure clips shorter than this many seconds (5 = the selection's rule; default: measure all)")
    p.set_defaults(fn=cmd_score)
    p = sub.add_parser("rank", help="table of the best (or most energetic) clips")
    p.add_argument("--work", required=True); p.add_argument("--by", default="quality", choices=["quality", "energy"])
    p.add_argument("--top", type=int, default=30); p.add_argument("--allow-flagged", action="store_true")
    p.add_argument("--allow-similar", action="store_true")
    p.add_argument("--only", help="restrict to inventory indexes, e.g. 100-330,420-445 (choose them from the contact sheets)")
    p.add_argument("--exclude", help="drop inventory indexes, e.g. 12,40-52")
    p.add_argument("--min-duration", type=float, default=5.0, help="ignore clips shorter than this many seconds (default 5; 0 = all)")
    p.add_argument("--keep-repeats", action="store_true", help="keep every take of a moment (default: only the best take of each)")
    p.set_defaults(fn=cmd_rank)
    p = sub.add_parser("pick", help="story-ordered candidate clip list (calm intro, build, payoff, calm end)")
    p.add_argument("--work", required=True); p.add_argument("--count", type=int, required=True); p.add_argument("--out", required=True)
    p.add_argument("--intro-shots", type=int, default=2); p.add_argument("--beats", help="beats.json: payoff share follows the drop")
    p.add_argument("--payoff-share", type=float, default=0.5); p.add_argument("--allow-flagged", action="store_true")
    p.add_argument("--allow-similar", action="store_true")
    p.add_argument("--only", help="restrict to inventory indexes, e.g. 100-330,420-445 (choose them from the contact sheets)")
    p.add_argument("--exclude", help="drop inventory indexes, e.g. 12,40-52")
    p.add_argument("--min-duration", type=float, default=5.0, help="ignore clips shorter than this many seconds (default 5; 0 = all)")
    p.add_argument("--keep-repeats", action="store_true", help="keep every take of a moment (default: only the best take of each)")
    p.set_defaults(fn=cmd_pick)
    p = sub.add_parser("need", help="how many clips a beat grid needs")
    p.add_argument("beats"); p.add_argument("--duration", type=float); p.add_argument("--period", type=float)
    p.add_argument("--intro-shots", type=int, default=2)
    p.add_argument("--body-start", type=float, help="seconds where the beat-by-beat body starts (default about 11 percent in); use the music's drop time to land the flash on the first body cut")
    p.add_argument("--pacing", choices=["beat", "energy"], help="energy = hold calm beats, cut fast on strong ones (Track B)")
    p.add_argument("--follow-tempo", action="store_true", help="place cuts on the variable-tempo grid (beats_refined), so late cuts do not drift (Track C)")
    p.set_defaults(fn=cmd_need)
    p = sub.add_parser("plan", help="beat grid + ordered clip list -> EDL")
    p.add_argument("--beats", required=True); p.add_argument("--clips", required=True); p.add_argument("--out", required=True)
    p.add_argument("--name", default="video"); p.add_argument("--music"); p.add_argument("--preset", default="reels-9x16")
    p.add_argument("--duration", type=float); p.add_argument("--period", type=float)
    p.add_argument("--intro-shots", type=int, default=2); p.add_argument("--grade", default="neutral")
    p.add_argument("--no-transitions", action="store_true")
    p.add_argument("--bar-align", action="store_true", help="start the body on a downbeat (needs bar_phase in beats.json)")
    p.add_argument("--body-start", type=float, help="seconds where the beat-by-beat body starts (default about 11 percent in); use the music's drop time to land the flash on the first body cut")
    p.add_argument("--pacing", choices=["beat", "energy"], help="energy = hold calm beats, cut fast on strong ones (Track B)")
    p.add_argument("--follow-tempo", action="store_true", help="place cuts on the variable-tempo grid (beats_refined), so late cuts do not drift (Track C)")
    p.add_argument("--scores", help="work folder containing scores.json: fill src_start from each clip's best window")
    p.add_argument("--smart-transitions", action="store_true",
                   help="choose cuts and transitions from the content of each shot pair, with a written reason (needs --scores)")
    p.add_argument("--notes", help="shot-notes.json written by the agent (overrides measurements; with --smart-transitions)")
    p.add_argument("--stabilize-shaky", type=float, nargs="?", const=1.5, default=None, metavar="SHAKE",
                   help="light stabilisation for shots whose clip was measured shaky (shake >= SHAKE, default 1.5; needs --scores)")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("vision", help="optional local, offline detection of people and faces (a plug-in with its own environment)")
    vsub = p.add_subparsers(dest="action", required=True)
    q = vsub.add_parser("status", help="is the plug-in set up, and has it been run on this work folder?")
    q.add_argument("--work")
    q = vsub.add_parser("run", help="look at a few frames of every clip and write vision.json (people, faces, scene labels)")
    q.add_argument("--work", required=True); q.add_argument("--only", help="inventory indexes, e.g. 100-330,420")
    q.add_argument("--frames", type=int, default=9, help="frames looked at per clip")
    q.add_argument("--onnx", help="an image-classification model of your own (ONNX); adds scene labels")
    q.add_argument("--labels", help="text file with one label per line for --onnx")
    q.add_argument("--force", action="store_true", help="look at every clip again, even those whose earlier result is still valid")
    p.set_defaults(fn=cmd_vision)
    p = sub.add_parser("story", help="story-first selection: chapters, a storyboard to approve, and an audit of an EDL")
    ssub = p.add_subparsers(dest="action", required=True)
    q = ssub.add_parser("chapters", help="draft chapters from the capture timeline (name them, weight them, then select)")
    q.add_argument("--work", required=True); q.add_argument("--out"); q.add_argument("--gap", type=float, default=35.0,
                   help="minutes of pause that start a new chapter (default 35)")
    q = ssub.add_parser("refine", help="find the best moment inside every chosen clip (sharp, steady, the action peak) and set the start times")
    q.add_argument("--work", required=True); q.add_argument("--storyboard", required=True); q.add_argument("--out")
    q.add_argument("--workers", type=int, default=4)
    q = ssub.add_parser("exclusions", help="which clips are left out and why (too short, shaky, covered, the weaker take of a moment ...)")
    q.add_argument("--work", required=True); q.add_argument("--chapters", help="chapters.json (its `filters` and `dedupe` settings apply)")
    q.add_argument("--notes"); q.add_argument("--out"); q.add_argument("--min-duration", type=float, help="seconds (default 5)")
    q = ssub.add_parser("select", help="choose the shots for the chapters and the music; writes storyboard.json/.md and a clip list")
    q.add_argument("--work", required=True); q.add_argument("--beats", required=True); q.add_argument("--chapters", required=True)
    q.add_argument("--notes", help="shot-notes.json (role, mood, people, topic, shot_size, skip)")
    q.add_argument("--duration", type=float, required=True); q.add_argument("--body-start", type=float)
    q.add_argument("--pacing", choices=["beat", "energy"], default="energy"); q.add_argument("--intro-shots", type=int, default=2)
    q.add_argument("--period", type=float); q.add_argument("--out"); q.add_argument("--clips")
    q.add_argument("--variant", default="balanced", choices=["balanced", "action", "people"],
                   help="how the story is told: balanced, action first, or people first")
    q.add_argument("--variants", action="store_true", help="write all three variants and print a comparison table")
    q.add_argument("--method", choices=["beam", "greedy"], default=None,
                   help="beam = optimise the whole sequence at once (default); greedy = chapter by chapter (the 0.15 behaviour)")
    q.add_argument("--profile", help="profile.json of remembered preferences (default: <work>/profile.json and the global one)")
    q.add_argument("--no-profile", action="store_true", help="ignore remembered preferences")
    q.add_argument("--cut-from", help="an approved storyboard.json: make a shorter (or longer) version from its shots only, at their approved starts")
    q = ssub.add_parser("review", help="write an offline HTML page to review a storyboard: frames, alternates, keep / replace / ban")
    q.add_argument("--work", required=True); q.add_argument("--storyboard", required=True); q.add_argument("--out")
    q.add_argument("--beats"); q.add_argument("--notes")
    q.add_argument("--no-video", action="store_true", help="a single self-contained page without proxy videos (frames only)")
    q.add_argument("--workers", type=int, help="parallel workers for frames and proxies (default 6)")
    q = ssub.add_parser("feedback", help="apply the decisions exported from the review page: update the storyboard and learn the user's taste")
    q.add_argument("--work", required=True); q.add_argument("--storyboard", required=True)
    q.add_argument("--feedback", required=True, help="decisions.json exported by the review page"); q.add_argument("--out")
    q.add_argument("--profile"); q.add_argument("--notes")
    q = ssub.add_parser("profile", help="show the remembered preferences; --promote carries a project's taste to every project")
    q.add_argument("--work", required=True); q.add_argument("--profile")
    q.add_argument("--promote", action="store_true", help="add this project's topic / role biases and weights to the global profile")
    q.add_argument("--forget-global", action="store_true", help="show and delete the global profile (projects keep their own)")
    q = ssub.add_parser("audit", help="check an EDL against the story principles (chapters, cameras, people, energy vs music, order)")
    q.add_argument("edl"); q.add_argument("--work", required=True); q.add_argument("--beats"); q.add_argument("--notes")
    q.add_argument("--chapters"); q.add_argument("--free-order", action="store_true", help="do not require the trip order")
    p.set_defaults(fn=cmd_story)
    p = sub.add_parser("cuts", help="name the kind of cut at every boundary and why; propose transitions (dry run unless --write)")
    p.add_argument("edl"); p.add_argument("--scores", required=True, help="work folder with scores.json")
    p.add_argument("--beats", help="beats.json (music sections mark where a transition is motivated)")
    p.add_argument("--notes", help="shot-notes.json (the agent's reading of the footage; wins over measurements)")
    p.add_argument("--align", action="store_true",
                   help="also propose src_start values so a shot keeps moving the way the previous one ends (match on action; reads frames)")
    p.add_argument("--write", action="store_true", help="store transitions and rationale (and alignments) in the EDL")
    p.add_argument("--overwrite", action="store_true", help="with --write: replace existing transitions and rationale")
    p.set_defaults(fn=cmd_cuts)
    p = sub.add_parser("lint", help="check an EDL: structure, restraint budget, files, FFmpeg capabilities")
    p.add_argument("edl"); p.add_argument("--no-files", action="store_true")
    p.set_defaults(fn=cmd_lint)
    p = sub.add_parser("render", help="render the video (cached parts; --preview for half size)")
    p.add_argument("edl"); p.add_argument("--out"); p.add_argument("--preview", action="store_true")
    p.add_argument("--encoder", default="auto", choices=["auto", "libx264", "nvenc", "videotoolbox", "qsv"]); p.add_argument("--work")
    p.add_argument("--no-cache", action="store_true"); p.add_argument("--force", action="store_true", help="render despite lint errors")
    p.add_argument("--workers", type=int, help="parallel shot renders (default: min(4, CPUs))")
    p.add_argument("--hwdecode", default="auto", choices=["auto", "none"], help="GPU decoding of source clips (auto = CUDA when available)")
    p.add_argument("--qa", action="store_true", help="run the quality gate on the result (final renders)")
    p.add_argument("--format", help="render in another format preset, e.g. youtube-16x9 or square-1x1 (reframed with blur-fill)")
    p.add_argument("--all-formats", action="store_true", help="render reels-9x16, youtube-16x9 and square-1x1 from the same EDL")
    p.add_argument("--delivery", choices=["reels", "tiktok", "shorts", "stories", "youtube", "square"],
                   help="platform delivery preset (sets the format, loudness target and container for that platform)")
    p.add_argument("--target-size-mb", type=float, help="aim for this output file size in MB (two-pass bitrate on libx264)")
    p.add_argument("--bitrate", type=int, help="target video bitrate in kbit/s (overrides constant-quality encoding)")
    p.add_argument("--manifest", action="store_true", help="write a reproducibility manifest (versions, hashes) next to the output")
    p.add_argument("--bundle", action="store_true", help="zip the EDL, brief, beats and scores for hand-off (no media)")
    p.add_argument("--json", help="also write the render result (paths, sizes, verdict) to this JSON file")
    p.add_argument("--quality", choices=["standard", "high", "max"],
                   help="constant-quality level of a final render: high (default) is close to the source, max is near lossless, standard is lighter; previews are always standard")
    p.set_defaults(fn=cmd_render)
    p = sub.add_parser("qa", help="quality gate on a rendered video: defects, loudness, cut timing, effect audit")
    p.add_argument("video"); p.add_argument("--edl"); p.add_argument("--scores", help="work folder with scores.json (audits clip quality)")
    p.add_argument("--json", help="where to write the report (default: next to the video)"); p.add_argument("--sheet")
    p.add_argument("--notes", help="shot-notes.json: the agent's shot sizes and movement override the measurements in the editorial checks")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings as well")
    p.set_defaults(fn=cmd_qa)
    p = sub.add_parser("color", help="colour correction and grading tools")
    csub = p.add_subparsers(dest="action", required=True)
    csub.add_parser("looks", help="list the built-in looks")
    q = csub.add_parser("lut", help="export a look as a 3D .cube LUT"); q.add_argument("--look", required=True); q.add_argument("--out", required=True)
    q.add_argument("--strength", type=float, default=1.0); q.add_argument("--size", type=int, default=33)
    q = csub.add_parser("scope", help="waveform / vectorscope / histogram sheet of a frame"); q.add_argument("source"); q.add_argument("--out", required=True); q.add_argument("--at", type=float, default=0.0)
    q = csub.add_parser("analyze", help="measure one frame and show what a look would do"); q.add_argument("source"); q.add_argument("--at", type=float); q.add_argument("--strength", type=float, default=0.8)
    q.add_argument("--scope", help="also write a waveform/vectorscope/histogram sheet here")
    q = csub.add_parser("plan", help="propose per-shot corrections for an EDL (dry run unless --write)"); q.add_argument("edl"); q.add_argument("--match", default="exposure", choices=["exposure", "reference", "none"])
    q.add_argument("--reference", type=int, help="shot number to match (with --match reference)"); q.add_argument("--strength", type=float, default=0.8)
    q.add_argument("--wb", choices=["off", "median", "gray-world", "skin"], help="white balance: median (default with --match exposure), gray-world (opt-in), skin (protects warmth), off")
    q.add_argument("--only", help="shot numbers, e.g. 0-5,9"); q.add_argument("--write", action="store_true"); q.add_argument("--overwrite", action="store_true")
    q = csub.add_parser("cameras", help="match the colour of the cameras to each other from clips they filmed at the same time")
    q.add_argument("--work", required=True); q.add_argument("--edl", help="an EDL to apply the camera gains to (with --write)")
    q.add_argument("--window", type=float, default=60.0, help="seconds between two clips of different cameras that count as the same moment")
    q.add_argument("--workers", type=int); q.add_argument("--write", action="store_true")
    q.add_argument("--reference", help="the camera the others are matched to (default: the one that filmed most), e.g. \"DJI OsmoPocket3\"")
    p.set_defaults(fn=cmd_color)
    p = sub.add_parser("text", help="on-screen text (Persian right-to-left supported)")
    tsub = p.add_subparsers(dest="action", required=True)
    tsub.add_parser("fonts", help="list the fonts the engine will use")
    tsub.add_parser("templates", help="list the reusable text templates (title-card, lower-third, caption ...)")
    q = tsub.add_parser("import-srt", help="append the cues of an SRT file to the EDL as texts"); q.add_argument("edl"); q.add_argument("srt"); q.add_argument("--position", default="bottom")
    q.add_argument("--style", default="caption"); q.add_argument("--animation", default="fade")
    q = tsub.add_parser("preview", help="draw the EDL texts on a frame (check Persian shaping and placement)"); q.add_argument("edl"); q.add_argument("--out", required=True); q.add_argument("--at", type=float, default=1.0)
    q.add_argument("--video", help="draw over a frame of this video instead of a gradient"); q.add_argument("--format")
    q.add_argument("--scale", type=float, default=0.5)
    p.set_defaults(fn=cmd_text)
    p = sub.add_parser("sfx", help="sound design and ambient bed")
    ssub = p.add_subparsers(dest="action", required=True)
    q = ssub.add_parser("plan", help="what the sound design and ambient bed will do"); q.add_argument("edl")
    p.set_defaults(fn=cmd_sfx)
    p = sub.add_parser("verify", help="quick stream/duration/size checks plus a contact sheet of the result")
    p.add_argument("edl"); p.add_argument("video"); p.add_argument("--sheet")
    p.set_defaults(fn=cmd_verify)
    _document_arguments(ap)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except vl.EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as e:
        # bad input files (broken JSON, a missing field, a wrong type) deserve a sentence, not a traceback;
        # MH_DEBUG=1 brings the traceback back for engine development
        if os.environ.get("MH_DEBUG"):
            raise
        print(f"error: could not process the input ({type(e).__name__}: {e}). Check the file named in the command; "
              "`lint EDL` explains problems in an EDL. Set MH_DEBUG=1 for the full traceback.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
