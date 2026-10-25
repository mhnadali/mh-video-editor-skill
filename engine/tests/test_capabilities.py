"""Phase 5: the engine must say so when the installed FFmpeg lacks a filter the EDL needs, and must know every filter it uses."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
import mh_video
import vid_edl as ve
import vid_render as vr
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")

# Filters that every FFmpeg build contains (no external library behind them). Anything else the engine uses must be
# listed in mh_video.REQUIRED_FILTERS or vidlib.FEATURE_FILTERS so that `doctor` and `lint` can warn about it.
CORE = {
    "setpts", "fps", "format", "setsar", "split", "overlay", "eq", "null", "anull", "color", "aformat", "adelay", "volume",
    "apad", "atrim", "asetpts", "aresample", "sidechaincompress", "concat", "xfade", "trim", "lowpass", "aevalsrc", "sine",
    "amix", "alimiter", "loudnorm", "pan", "ebur128", "gblur", "boxblur", "geq", "hue", "colorchannelmixer", "swapuv", "lut",
    "amerge", "asplit", "settb", "tpad", "scale", "crop", "fade", "unsharp", "vignette", "tile", "drawbox", "colorbalance", "rgbashift", "blend",
}


def filter_names(graph: str) -> set[str]:
    """Filter names in a filtergraph string (expressions in single quotes are removed first)."""
    graph = re.sub(r"'[^']*'", "''", graph)
    names = set()
    for chain in re.split(r"[;\n]", graph):
        for piece in chain.split(","):
            piece = re.sub(r"^(\s*\[[^\]]*\])+", "", piece).strip()
            m = re.match(r"([a-z][a-z0-9_]*)\s*(=|$|\[)", piece)
            if m:
                names.add(m.group(1))
    return names


def full_edl() -> dict:
    return {
        "version": 1, "project": {"name": "caps", "width": 270, "height": 480, "fps": 30, "duration": 2.0, "music": None},
        "style": {"grade": {"look": "desert-gold"}, "sound_design": True, "ambient": {"level_db": -14}, "reframe": "cover"},
        "shots": [{"clip": "a.mp4", "span": [0, 1.0], "stabilize": "light", "speed": 0.5},
                  {"clip": "b.mp4", "span": [1.0, 2.0], "reframe": "blur-fill"}],
        "texts": [{"text": "salam", "start": 0.2, "end": 1.5}],
    }


class FeatureMap(unittest.TestCase):
    def test_an_edl_without_options_needs_nothing(self):
        e = full_edl()
        e["style"] = {"grade": "warm-desert"}
        e["texts"] = []
        e["shots"] = [{"clip": "a.mp4", "span": [0, 2.0]}]
        self.assertEqual(ve.features_in_use(e), [])

    def test_every_optional_feature_is_detected(self):
        used = ve.features_in_use(full_edl())
        self.assertEqual(set(used), {"colour looks and correction", "stabilisation", "slow motion by interpolation",
                                     "on-screen text", "blur-fill reframing", "sound design (whooshes, ducking)", "ambient sound"})
        self.assertTrue(set(used) <= set(vl.FEATURE_FILTERS))

    def test_native_slow_motion_and_disabled_features_are_not_flagged(self):
        e = full_edl()
        e["shots"][0]["speed_mode"] = "native"
        e["shots"][0].pop("stabilize")
        e["style"]["sound_design"] = {"whoosh": False}
        e["style"]["ambient"] = None
        used = ve.features_in_use(e)
        self.assertNotIn("slow motion by interpolation", used)
        self.assertNotIn("stabilisation", used)
        self.assertNotIn("sound design (whooshes, ducking)", used)
        self.assertNotIn("ambient sound", used)

    def test_invalid_values_do_not_crash_feature_detection(self):
        e = full_edl()
        e["shots"][0]["speed"] = "fast"
        e["shots"][0]["stabilize"] = "huge"
        ve.features_in_use(e)                                               # the validators report these, not this function

    def test_gaps_name_the_missing_filter_and_how_to_fix_it(self):
        have = {"lut3d", "curves", "minterpolate", "gblur", "overlay", "sidechaincompress", "anoisesrc", "pan", "highpass",
                "lowpass", "adelay", "atempo", "amix", "afade"}            # no libass, no vidstab
        issues = ve.ffmpeg_gaps(full_edl(), have)
        text = " ".join(i["msg"] for i in issues)
        self.assertEqual({i["code"] for i in issues}, {"ffmpeg-missing"})
        self.assertTrue(all(i["level"] == "error" for i in issues))
        self.assertIn("vidstabdetect", text)
        self.assertIn("ass", text)
        self.assertIn("winget install Gyan.FFmpeg", text)
        self.assertEqual(len(issues), 2)

    def test_a_complete_build_has_no_gaps(self):
        have = {f for names in vl.FEATURE_FILTERS.values() for f in names}
        self.assertEqual(ve.ffmpeg_gaps(full_edl(), have), [])

    def test_lint_reports_gaps_with_files_checked(self):
        e = full_edl()
        e["shots"] = [{"clip": __file__, "span": [0, 2.0], "stabilize": True}]
        e["style"] = {"grade": "warm-desert"}
        e["texts"] = []
        with mock.patch.object(vl, "available_filters", return_value={"scale"}):
            codes = {i["code"] for i in ve.lint(e, check_files=True)}
        self.assertIn("ffmpeg-missing", codes)
        with mock.patch.object(vl, "available_filters", return_value={"scale"}):
            self.assertNotIn("ffmpeg-missing", {i["code"] for i in ve.lint(e, check_files=False)})   # pure lint stays offline

    def test_lint_survives_a_machine_without_ffmpeg(self):
        e = full_edl()
        with mock.patch.object(vl, "available_filters", side_effect=vl.EngineError("FFmpeg not found")):
            ve.lint(e, check_files=True)

    def test_parse_filter_list_reads_real_ffmpeg_output(self):
        text = (" ... = Source or sink\n"
                " T.. = Timeline support\n"
                " TSC ass               V->V       Render ASS subtitles.\n"
                " ..C vidstabdetect     V->V       Extract relative transformations.\n"
                " .S. split             N->N       Pass on the input to N outputs.\n"
                "Filters:\n")
        self.assertEqual(vl.parse_filter_list(text), {"ass", "vidstabdetect", "split"})

    def test_filter_name_extraction(self):
        g = "[0:v]setpts=PTS-STARTPTS,scale=1080:1920,eq=brightness='min(1,t)';[a][b]xfade=transition=fade:duration=1[v]"
        self.assertEqual(filter_names(g), {"setpts", "scale", "eq", "xfade"})


@NEEDS_FF
@helpers.slow
class Coverage(unittest.TestCase):
    """Render a project that uses everything and check that no filter escapes the doctor / lint capability lists."""

    @classmethod
    def setUpClass(cls):
        import test_phase4_integration as p4          # module reference only: its test class must not be collected twice
        cls.p4 = p4.Everything
        cls.p4.setUpClass()

    def test_all_filters_used_by_a_full_render_are_known(self):
        seen: list[list[str]] = []
        orig = vl.run

        def recording(cmd, *a, **k):
            seen.append(list(cmd))
            return orig(cmd, *a, **k)

        work = Path(tempfile.mkdtemp(dir=helpers.tmpdir(), prefix="caps_"))
        with mock.patch.object(vl, "run", recording):
            vr.render(self.p4.path, out=work / "out_caps.mp4", encoder="libx264", workdir=work / "w", log=lambda *_: None)
        graphs = []
        for cmd in seen:
            for i, tok in enumerate(cmd[:-1]):
                if tok in ("-vf", "-af", "-lavfi", "-filter_complex"):
                    graphs.append(cmd[i + 1])
                elif tok == "-/filter_complex":
                    graphs.append(Path(cmd[i + 1]).read_text(encoding="utf-8"))
        graphs += [p.read_text(encoding="utf-8") for p in (work / "w").glob("audio_*.txt")]
        for cmd in seen:                                                     # "-i color=...,filter" style sources
            for i, tok in enumerate(cmd[:-1]):
                if tok == "-i" and "=" in cmd[i + 1] and not Path(cmd[i + 1]).exists():
                    graphs.append(cmd[i + 1])
        self.assertGreater(len(graphs), 10)
        used = set()
        for g in graphs:
            used |= filter_names(g)
        known = set(mh_video.REQUIRED_FILTERS) | {f for names in vl.FEATURE_FILTERS.values() for f in names} | CORE
        unknown = sorted(used - known)
        self.assertEqual(unknown, [], "add these filters to mh_video.REQUIRED_FILTERS or vidlib.FEATURE_FILTERS "
                                      "(or to CORE here if every FFmpeg build has them)")
        for feat in ve.features_in_use(self.p4.edl):                        # and the map lists what the feature really uses
            self.assertEqual(sorted(set(vl.FEATURE_FILTERS[feat]) - used - {"atempo"}), [], feat)   # atempo: only with speed_follow


if __name__ == "__main__":
    unittest.main()
