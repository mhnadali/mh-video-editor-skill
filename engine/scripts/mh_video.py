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
    recs = vid_probe.scan(a.folder, a.recursive)
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
    sheets = vid_sheets.clip_sheets(recs, Path(a.work) / "sheets", a.size, a.cols, a.rows, a.pos)
    print("\n".join(sheets))
    print(f"{len(sheets)} sheets, {a.cols * a.rows} clips each; the yellow number is the inventory index")
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
    scores = vid_score.score_inventory(recs, workers=a.workers, limit=a.limit, progress=print, previous=prev)
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


def cmd_rank(a) -> int:
    import vid_score
    recs, scores = _load_candidates(a)
    cands = vid_score.candidates(recs, scores, skip_flagged=not a.allow_flagged, skip_similar=not a.allow_similar,
                                 only=vid_score.parse_index_spec(a.only), exclude=vid_score.parse_index_spec(a.exclude))
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
    cands = vid_score.candidates(recs, scores, skip_flagged=not a.allow_flagged, skip_similar=not a.allow_similar,
                                 only=vid_score.parse_index_spec(a.only), exclude=vid_score.parse_index_spec(a.exclude))
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
    body_t0 = vid_plan.body_start_time(beats, a.duration or beats["duration"], a.period or beats["period"], a.intro_shots)
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
                        follow_tempo=a.follow_tempo)
    if a.smart_transitions:
        if not a.scores:
            raise vl.EngineError("--smart-transitions needs --scores (the work folder with scores.json)")
        import vid_cutlogic
        import vid_score
        notes = vl.load_json(a.notes) if a.notes else None
        decisions = vid_cutlogic.analyse(edl, vid_score.load_scores(a.scores), beats, notes)
        edl["transitions"] = {}
        vid_cutlogic.apply(edl, decisions, overwrite=True)
        print(vid_cutlogic.format_table(decisions, edl))
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
              manifest=a.manifest, bundle=a.bundle, json_out=a.json)
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
    rep = vid_qa.run_qa(a.video, a.edl, scores=scores, sheet=sheet)
    out = a.json or str(Path(a.video).with_suffix("")) + ".qa.json"
    vid_qa.save_report(rep, out)
    print(vid_qa.format_report(rep))
    print(f"  report: {out}")
    if a.strict:
        return 0 if rep["verdict"] == "pass" else 1
    return 1 if rep["verdict"] == "fail" else 0


def cmd_cuts(a) -> int:
    import vid_cutlogic
    import vid_edl
    import vid_score
    edl = vid_edl.load(a.edl)
    beats = vl.load_json(a.beats) if a.beats else None
    notes = vl.load_json(a.notes) if a.notes else None
    decisions = vid_cutlogic.analyse(edl, vid_score.load_scores(a.scores), beats, notes)
    print(vid_cutlogic.format_table(decisions, edl))
    if not a.write:
        print("dry run: nothing written. Review the reasons against the contact sheets; --write stores transitions + rationale.")
        return 0
    vid_cutlogic.apply(edl, decisions, overwrite=a.overwrite)
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
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("sheets", help="numbered contact sheets of the inventory (look at them to choose shots)")
    p.add_argument("--work", required=True); p.add_argument("--size", type=int, default=200)
    p.add_argument("--cols", type=int, default=8); p.add_argument("--rows", type=int, default=5)
    p.add_argument("--pos", type=float, default=0.4, help="position in each clip (0-1) of the frame shown")
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
    p.set_defaults(fn=cmd_score)
    p = sub.add_parser("rank", help="table of the best (or most energetic) clips")
    p.add_argument("--work", required=True); p.add_argument("--by", default="quality", choices=["quality", "energy"])
    p.add_argument("--top", type=int, default=30); p.add_argument("--allow-flagged", action="store_true")
    p.add_argument("--allow-similar", action="store_true")
    p.add_argument("--only", help="restrict to inventory indexes, e.g. 100-330,420-445 (choose them from the contact sheets)")
    p.add_argument("--exclude", help="drop inventory indexes, e.g. 12,40-52")
    p.set_defaults(fn=cmd_rank)
    p = sub.add_parser("pick", help="story-ordered candidate clip list (calm intro, build, payoff, calm end)")
    p.add_argument("--work", required=True); p.add_argument("--count", type=int, required=True); p.add_argument("--out", required=True)
    p.add_argument("--intro-shots", type=int, default=2); p.add_argument("--beats", help="beats.json: payoff share follows the drop")
    p.add_argument("--payoff-share", type=float, default=0.5); p.add_argument("--allow-flagged", action="store_true")
    p.add_argument("--allow-similar", action="store_true")
    p.add_argument("--only", help="restrict to inventory indexes, e.g. 100-330,420-445 (choose them from the contact sheets)")
    p.add_argument("--exclude", help="drop inventory indexes, e.g. 12,40-52")
    p.set_defaults(fn=cmd_pick)
    p = sub.add_parser("need", help="how many clips a beat grid needs")
    p.add_argument("beats"); p.add_argument("--duration", type=float); p.add_argument("--period", type=float)
    p.add_argument("--intro-shots", type=int, default=2)
    p.add_argument("--pacing", choices=["beat", "energy"], help="energy = hold calm beats, cut fast on strong ones (Track B)")
    p.add_argument("--follow-tempo", action="store_true", help="place cuts on the variable-tempo grid (beats_refined), so late cuts do not drift (Track C)")
    p.set_defaults(fn=cmd_need)
    p = sub.add_parser("plan", help="beat grid + ordered clip list -> EDL")
    p.add_argument("--beats", required=True); p.add_argument("--clips", required=True); p.add_argument("--out", required=True)
    p.add_argument("--name", default="video"); p.add_argument("--music"); p.add_argument("--preset", default="reels-9x16")
    p.add_argument("--duration", type=float); p.add_argument("--period", type=float)
    p.add_argument("--intro-shots", type=int, default=2); p.add_argument("--grade", default="warm-desert")
    p.add_argument("--no-transitions", action="store_true")
    p.add_argument("--bar-align", action="store_true", help="start the body on a downbeat (needs bar_phase in beats.json)")
    p.add_argument("--pacing", choices=["beat", "energy"], help="energy = hold calm beats, cut fast on strong ones (Track B)")
    p.add_argument("--follow-tempo", action="store_true", help="place cuts on the variable-tempo grid (beats_refined), so late cuts do not drift (Track C)")
    p.add_argument("--scores", help="work folder containing scores.json: fill src_start from each clip's best window")
    p.add_argument("--smart-transitions", action="store_true",
                   help="choose cuts and transitions from the content of each shot pair, with a written reason (needs --scores)")
    p.add_argument("--notes", help="shot-notes.json written by the agent (overrides measurements; with --smart-transitions)")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("cuts", help="name the kind of cut at every boundary and why; propose transitions (dry run unless --write)")
    p.add_argument("edl"); p.add_argument("--scores", required=True, help="work folder with scores.json")
    p.add_argument("--beats", help="beats.json (music sections mark where a transition is motivated)")
    p.add_argument("--notes", help="shot-notes.json (the agent's reading of the footage; wins over measurements)")
    p.add_argument("--write", action="store_true", help="store transitions and rationale in the EDL")
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
    p.set_defaults(fn=cmd_render)
    p = sub.add_parser("qa", help="quality gate on a rendered video: defects, loudness, cut timing, effect audit")
    p.add_argument("video"); p.add_argument("--edl"); p.add_argument("--scores", help="work folder with scores.json (audits clip quality)")
    p.add_argument("--json", help="where to write the report (default: next to the video)"); p.add_argument("--sheet")
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
