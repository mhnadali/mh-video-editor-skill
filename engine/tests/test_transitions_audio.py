"""Phase 3: every xfade transition is rendered and inspected (this is how rectcrop's black frame and squeezev's crash
were found), and the two-pass audio master is checked against the loudness target."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers
import vid_edl as ve
import vid_plan as vp
import vid_qa as qa
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


def probe_transition(name: str, root: Path) -> tuple[int, list[float]]:
    """Render `name` between two different test clips (4-frame overlap) and return (exit code, YAVG per frame)."""
    ff = vl.find_ffmpeg()["ffmpeg"]
    a, b = root / "ta.mp4", root / "tb.mp4"
    for path, src in ((a, "testsrc2"), (b, "rgbtestsrc")):
        if not path.exists():
            subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=s=270x480:r=30:d=1", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(path)], check=True)
    work = Path(tempfile.mkdtemp(dir=root))
    g = (f"[0:v]fps=30,settb=AVTB[a];[1:v]fps=30,settb=AVTB[b];[a][b]xfade=transition={name}:duration=0.1333:offset=0.5,"
         "signalstats,metadata=print:file=s.txt[o]")
    r = subprocess.run([ff, "-v", "error", "-i", str(a), "-i", str(b), "-filter_complex", g, "-map", "[o]", "-frames:v", "40",
                        "-f", "null", "-"], capture_output=True, text=True, cwd=work)
    s = work / "s.txt"
    ya = [float(x) for x in re.findall(r"signalstats\.YAVG=([\d.]+)", s.read_text())] if s.exists() else []
    return r.returncode, ya


@NEEDS_FF
@helpers.slow
class EveryTransition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "xf"
        cls.root.mkdir(parents=True, exist_ok=True)

    def test_every_allowed_transition_renders_without_a_black_or_crashed_frame(self):
        problems = []
        for name in sorted(vl.ALL_TRANSITIONS - vl.BANNED_TRANSITIONS):
            rc, ya = probe_transition(name, self.root)
            if rc != 0 or len(ya) < 31:                            # exact frame count varies by FFmpeg version; need enough to inspect
                problems.append(f"{name}: exit {rc}, {len(ya)} frames")
                continue
            ends = (ya[5], ya[30])
            seg = ya[14:22]
            if name in vl.DIP_TRANSITIONS:
                continue                                           # deliberate: the QA gate expects these
            if min(seg) < 0.6 * min(ends):
                problems.append(f"{name}: a frame dips to {min(seg):.0f} (ends {ends[0]:.0f}/{ends[1]:.0f})")
            if max(seg) > 1.4 * max(ends):
                problems.append(f"{name}: a frame brightens to {max(seg):.0f}")
        self.assertEqual(problems, [], "transitions that misbehave must be added to vidlib.BANNED_REASONS:\n" + "\n".join(problems))

    def test_banned_transitions_really_are_broken(self):
        """A tripwire: if FFmpeg has fixed one of the banned transitions, say so (so the ban can be lifted).

        Whether these transitions misbehave depends on the FFmpeg build - the bans were calibrated on the author's
        9.x. When a build behaves differently (e.g. squeezev does not crash on some 6.x/7.x builds) this skips with a
        note rather than failing, because the linter refuses the banned transitions on every build regardless."""
        notes = []
        for name in ("rectcrop", "circlecrop"):
            rc, ya = probe_transition(name, self.root)
            if not (rc == 0 and ya and min(ya[14:22]) < 5):
                notes.append(f"{name} did not show a black frame")
        rc, _ = probe_transition("squeezev", self.root)
        if rc == 0:
            notes.append("squeezev did not crash")
        if notes:
            ver = ".".join(str(x) for x in vl.ffmpeg_version())
            self.skipTest(f"banned transitions behave differently on FFmpeg {ver} (the linter ban still applies): "
                          + "; ".join(notes))

    def test_deliberate_dips_do_dip(self):
        _, black = probe_transition("fadeblack", self.root)
        _, white = probe_transition("fadewhite", self.root)
        self.assertLess(min(black[14:22]), 20)
        self.assertGreater(max(white[14:22]), 200)

    def test_every_banned_transition_has_a_reason_and_is_rejected_by_lint(self):
        self.assertTrue(vl.BANNED_REASONS)
        for name, reason in vl.BANNED_REASONS.items():
            self.assertTrue(reason)
            edl = {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": 4.0},
                   "style": {}, "transitions": {"1": name},
                   "shots": [{"clip": "a.mp4", "span": [0, 2.0]}, {"clip": "b.mp4", "span": [2.0, 4.0]}]}
            issues = ve.lint(edl, check_files=False)
            self.assertIn("transition-banned", {i["code"] for i in issues}, name)
            self.assertIn(reason, next(i["msg"] for i in issues if i["code"] == "transition-banned"))

    def test_planner_vocabulary_contains_no_banned_or_unknown_name(self):
        for name in vl.PLANNER_TRANSITIONS:
            self.assertIn(name, vl.ALL_TRANSITIONS)
            self.assertNotIn(name, vl.BANNED_TRANSITIONS)


class PlannerShare(unittest.TestCase):
    def test_share_and_special_cuts_for_every_size(self):
        for n in range(2, 81):
            cuts = n - 1
            drop = (n // 2) if n > 4 else None
            t = vp.auto_transitions(n, drop)
            self.assertLessEqual(len(t), cuts // 2, n)                      # never above the linter's 50%
            if cuts >= 6:
                self.assertEqual(t[str(cuts)], "fade", n)
            if drop is not None and cuts >= 2:
                self.assertEqual(t[str(drop)], "fadewhite", n)
            names = [v for v in t.values() if v != "fadewhite"]
            for nm in set(names):
                self.assertLessEqual(names.count(nm), 2 if nm == "fade" else 1, (n, nm))
            self.assertTrue(all(1 <= int(k) <= cuts for k in t))
            self.assertFalse(set(t.values()) & vl.BANNED_TRANSITIONS)

    def test_planned_edls_are_lint_clean_at_many_sizes(self):
        beats = {"duration": 30.0, "period": 0.6, "t0": 0.3, "drop_beat": 20, "file": None}
        for dur in (6.0, 9.5, 14.0, 22.0, 30.0, 45.0):
            b = dict(beats, duration=dur, drop_beat=int(dur * 0.5 / 0.6))
            body = vp.body_start_time(b, dur, 0.6, 2)
            n = vp.layout(dur, body, 0.6, 2)["shots"]
            edl = vp.plan([{"clip": f"c{i}.mp4"} for i in range(n)], b, duration=dur, period=0.6)
            issues = ve.lint(edl, check_files=False)
            self.assertEqual(issues, [], f"{dur}s: " + ve.format_issues(issues))


@NEEDS_FF
@helpers.slow
class AudioMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = helpers.tmpdir() / "master"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.music = cls.root / "m.wav"
        helpers.click_track(cls.music, bpm=100.0, first=0.4, seconds=12.0, step_at=6.0)

    def edl(self, **style):
        return {"version": 1, "project": {"name": "a", "width": 90, "height": 160, "fps": 30, "duration": 12.0,
                                         "music": vl.ffp(self.music)},
                "beats": {"t0": 0.4, "period": 0.6, "drop_beat": 10}, "style": dict({"grade": "neutral"}, **style),
                "transitions": {},
                "shots": [{"clip": "x.mp4", "span": [0, 6.0]}, {"clip": "y.mp4", "span": [6.0, 12.0]}]}

    def measure(self, wav):
        a = qa.measure_audio(wav)
        return a

    def test_master_hits_the_target_loudness_with_headroom(self):
        for target in (-15.0, -18.0, -12.0):
            wav = vr.master_audio(self.edl(loudness=target), self.root / f"t{int(-target)}", log=lambda *_: None) \
                if (self.root / f"t{int(-target)}").mkdir(exist_ok=True) is None else None
            a = self.measure(wav)
            self.assertAlmostEqual(a["lufs"], target, delta=0.8, msg=f"target {target}")
            self.assertLess(a["true_peak"], -1.0, f"target {target}")

    def test_master_is_cached(self):
        work = self.root / "cache"
        work.mkdir(exist_ok=True)
        first = vr.master_audio(self.edl(), work, log=lambda *_: None)
        mtime = first.stat().st_mtime_ns
        second = vr.master_audio(self.edl(), work, log=lambda *_: None)
        self.assertEqual(first, second)
        self.assertEqual(second.stat().st_mtime_ns, mtime)
        changed = vr.master_audio(self.edl(loudness=-18.0), work, log=lambda *_: None)
        self.assertNotEqual(changed, first)

    def test_no_music_means_no_master(self):
        e = self.edl()
        e["project"]["music"] = None
        self.assertIsNone(vr.master_audio(e, self.root / "none", log=lambda *_: None))

    def test_silent_music_is_passed_through(self):
        sil = self.root / "silent.wav"
        vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "12", str(sil)])
        e = self.edl()
        e["project"]["music"] = vl.ffp(sil)
        e["style"]["riser"] = False
        e["style"]["boom"] = False
        work = self.root / "silent_work"
        work.mkdir(exist_ok=True)
        wav = vr.master_audio(e, work, log=lambda *_: None)
        self.assertTrue(wav.is_file())

    def test_riser_and_boom_do_not_push_the_mix_over_the_target(self):
        """The bug the gate found: the synthesised sounds made the final mix 2 LU hotter than requested."""
        work = self.root / "fx"
        work.mkdir(exist_ok=True)
        with_fx = self.measure(vr.master_audio(self.edl(), work, log=lambda *_: None))
        no_fx = self.measure(vr.master_audio(self.edl(riser=False, boom=False), work, log=lambda *_: None))
        self.assertAlmostEqual(with_fx["lufs"], no_fx["lufs"], delta=0.8)

    def test_loudnorm_json_parser(self):
        text = 'noise\n[Parsed_loudnorm_0 @ 0x1] \n{\n\t"input_i" : "-20.1",\n\t"input_tp" : "-3.0"\n}\n'
        self.assertEqual(vr._loudnorm_json(text)["input_i"], "-20.1")
        with self.assertRaises(vl.EngineError):
            vr._loudnorm_json("no json here")


if __name__ == "__main__":
    unittest.main()
