"""Phase 5: malformed input must produce a clear message, never a raw exception.

Seeded mutation fuzzing: take valid EDLs, damage them in random ways (delete a field, swap its type, put in odd
numbers) and require that `lint`, the timeline and the planners either work or raise vidlib.EngineError. The seed is
fixed so a failure reproduces; the failing mutation is printed.
"""
import copy
import json
import math
import random
import subprocess
import sys
import unittest
from pathlib import Path

import helpers
import vid_edl as ve
import vid_sfx
import vid_text
import vidlib as vl

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = vl.load_json(ROOT / "examples" / "edl.example.json")

ODD = [None, True, False, 0, -1, 1, 2 ** 40, -2 ** 40, 0.5, -0.5, float("inf"), float("nan"), "", "x", "0:30", "ü", "‌", [], {},
       [1], [1, 2], [1, 2, 3], ["a"], {"a": 1}, [[]], 1e308, 1e-308]


def rich_edl() -> dict:
    e = copy.deepcopy(EXAMPLE)
    e["style"].update({"grade": {"look": "desert-gold", "strength": 0.5}, "correct": {"ev": 0.2},
                       "sound_design": {"whoosh": True}, "ambient": {"level_db": -14}, "reframe": "blur-fill"})
    e["shots"][0].update({"stabilize": "light", "speed": {"from": 1.0, "to": 0.5}, "focus": [0.3, 0.5], "audio": {"gain_db": 2}})
    e["texts"] = [{"text": "کویر", "start": 0.2, "end": 1.4, "position": "title"}]
    return e


def paths(node, prefix=()):
    """Every path (tuple of keys/indexes) into nested dicts and lists."""
    yield prefix
    if isinstance(node, dict):
        for k, v in node.items():
            yield from paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from paths(v, prefix + (i,))


def get(node, path):
    for k in path:
        node = node[k]
    return node


def mutate(edl: dict, rnd: random.Random) -> tuple[dict, str]:
    e = copy.deepcopy(edl)
    desc = []
    for _ in range(rnd.choice((1, 1, 2, 3))):
        cands = [p for p in paths(e) if p]
        path = rnd.choice(cands)
        parent = get(e, path[:-1])
        key = path[-1]
        what = rnd.choice(("delete", "odd", "odd", "odd", "swap-type"))
        if what == "delete":
            if isinstance(parent, dict):
                del parent[key]
            else:
                parent.pop(key)
            desc.append(f"delete {path}")
        elif what == "odd":
            val = rnd.choice(ODD)
            parent[key] = copy.deepcopy(val)
            desc.append(f"{path} = {val!r}")
        else:
            cur = parent[key]
            parent[key] = str(cur) if not isinstance(cur, str) else [cur]
            desc.append(f"{path}: swap type")
    return e, "; ".join(desc)


def exercise(edl: dict) -> None:
    """Everything the CLI does with an EDL that needs no FFmpeg."""
    issues = ve.lint(edl, check_files=False)
    ve.format_issues(issues)
    ve.has_errors(issues)
    if ve.has_errors(issues):
        return
    # an EDL that passes lint has to survive the planners that render() and sfx/qa use
    bounds = ve.timeline(edl)
    trans = ve.effective_transitions(edl, bounds)
    ve.drop_boundary(edl, bounds)
    vid_sfx.whoosh_events(trans, bounds, ve.project(edl)["fps"], vid_sfx.DEFAULT_SOUND_DESIGN)
    vid_text.validate(edl)


class Fuzz(unittest.TestCase):
    def run_many(self, base: dict, count: int, seed: int):
        rnd = random.Random(seed)
        bad = []
        for _ in range(count):
            e, desc = mutate(base, rnd)
            try:
                exercise(e)
            except vl.EngineError:
                pass
            except Exception as ex:                                  # noqa: BLE001 - this is exactly what the test hunts for
                bad.append(f"{type(ex).__name__}: {ex} <- {desc}")
        self.assertEqual(bad[:8], [], f"{len(bad)} of {count} mutations raised a raw exception")

    def test_example_edl_mutations(self):
        self.run_many(EXAMPLE, 1500, seed=11)

    def test_rich_edl_mutations(self):
        self.run_many(rich_edl(), 1500, seed=23)

    def test_unmutated_edls_are_clean(self):
        for e in (EXAMPLE, rich_edl()):
            self.assertFalse(ve.has_errors(ve.lint(copy.deepcopy(e), check_files=False)))


class Specific(unittest.TestCase):
    def test_damage_is_explained_by_a_specific_message(self):
        """The generic safety net ('wrong kind or shape') must stay a last resort: a field that can be named, is named."""
        for base, seed in ((EXAMPLE, 31), (rich_edl(), 32)):
            rnd = random.Random(seed)
            generic = []
            for _ in range(2000):
                e, desc = mutate(base, rnd)
                if any(i["code"] == "edl-malformed" and "wrong kind or shape" in i["msg"] for i in ve.lint(e, check_files=False)):
                    generic.append(desc)
            self.assertEqual(generic[:5], [], "add a field check to vid_edl.structure_errors for these")


class TopLevelShapes(unittest.TestCase):
    """Things that are not even an EDL."""

    def test_wrong_top_level_values(self):
        for bad in ([], "x", 3, None, {}, {"shots": 3}, {"shots": [3]}, {"shots": [{}]}, {"project": 3, "shots": []},
                    {"shots": [{"clip": 3, "span": "a"}], "project": {"width": "w", "height": 1, "duration": "d"}}):
            try:
                ve.lint(bad, check_files=False)                       # type: ignore[arg-type]
            except vl.EngineError:
                pass
            except Exception as ex:                                  # noqa: BLE001
                self.fail(f"{bad!r}: {type(ex).__name__}: {ex}")


class Cli(unittest.TestCase):
    """The command line turns broken files into one-line errors with exit code 2 (no traceback)."""

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "mh_video.py"), *args], capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def test_broken_inputs_never_show_a_traceback(self):
        d = helpers.tmpdir() / "cli_bad"
        d.mkdir(exist_ok=True)
        (d / "broken.json").write_text("{not json", encoding="utf-8")
        (d / "list.json").write_text("[1, 2]", encoding="utf-8")
        (d / "empty.json").write_text("", encoding="utf-8")
        (d / "nofields.json").write_text('{"version": 1, "shots": [{"span": "a"}]}', encoding="utf-8")
        cases = [("lint", str(d / "missing.json")), ("lint", str(d / "broken.json")), ("lint", str(d / "list.json")),
                 ("lint", str(d / "empty.json")), ("lint", str(d / "nofields.json"), "--no-files"),
                 ("render", str(d / "broken.json")), ("sfx", "plan", str(d / "list.json")),
                 ("need", str(d / "broken.json")), ("color", "plan", str(d / "nofields.json")),
                 ("text", "import-srt", str(d / "nofields.json"), str(d / "missing.srt")),
                 ("probe", str(d / "nonexistent-folder"), "--work", str(d / "w")),
                 ("qa", str(d / "no.mp4")), ("beats", str(d / "no.wav"))]
        for args in cases:
            code, out, err = self.run_cli(*args)
            self.assertNotEqual(code, 0, args)
            self.assertNotIn("Traceback", err, f"{args}: {err}")
            self.assertTrue(err.strip(), f"{args}: no message on stderr")

    def test_debug_variable_restores_the_traceback(self):
        d = helpers.tmpdir() / "cli_bad"
        d.mkdir(exist_ok=True)
        (d / "broken2.json").write_text("{not json", encoding="utf-8")
        import os
        env = dict(os.environ, MH_DEBUG="1")
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "mh_video.py"), "lint", str(d / "broken2.json")],
                           capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
