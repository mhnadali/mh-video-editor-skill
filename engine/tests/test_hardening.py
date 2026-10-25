"""Phase 5: concurrency, atomic writes and Python-version compatibility."""
import ast
import json
import os
import re
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers
import vid_color as vc
import vidlib as vl

ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 9)                      # what README and the installers promise


class AtomicWrites(unittest.TestCase):
    def test_cube_appears_only_when_complete(self):
        d = helpers.tmpdir() / "atomic_cube"
        target = d / "x.cube"
        seen = []
        real_replace = os.replace

        def spy(src, dst):
            seen.append((Path(dst).exists(), Path(src).read_text(encoding="ascii").count("\n")))
            return real_replace(src, dst)

        data = [(0.0, 0.0, 0.0)] * 125                                     # a 5x5x5 cube
        with mock.patch.object(os, "replace", spy):
            vc.write_cube(target, data, 5)
        self.assertEqual(seen, [(False, 129)])                              # the final name did not exist before the swap
        self.assertEqual(vc.read_cube(target)["size"], 5)
        self.assertEqual([p.name for p in d.iterdir()], ["x.cube"])        # no temp leftovers

    def test_parallel_workers_building_the_same_look_get_a_complete_file(self):
        d = helpers.tmpdir() / "atomic_luts"
        look = dict(vc.LOOKS["desert-gold"])
        paths, errors = [], []

        def worker():
            try:
                p = vc.ensure_look_lut(look, 0.8, d)
                paths.append(p)
                vc.read_cube(p)                                             # raises if the file is empty or truncated
            except Exception as e:                                          # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(paths)), 1)
        self.assertEqual([p.suffix for p in d.iterdir()], [".cube"])

    def test_unsynchronised_writers_of_one_file_never_corrupt_it(self):
        """What two separate processes on one LUT folder amount to: no lock, same destination, readers at the same time."""
        d = helpers.tmpdir() / "atomic_race"
        target = d / "shared.cube"
        data = [(0.1, 0.2, 0.3)] * 125
        errors = []

        def writer():
            try:
                for _ in range(15):
                    vc.write_cube(target, data, 5)
                    for _try in range(50):
                        try:
                            vc.read_cube(target)                    # raises EngineError if the file were truncated
                            break
                        except PermissionError:                     # Windows refuses to open a file while it is swapped
                            time.sleep(0.01)
            except Exception as e:                                          # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=writer) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual([p.name for p in d.iterdir()], ["shared.cube"])

    def test_save_json_keeps_the_old_file_when_writing_fails(self):
        p = helpers.tmpdir() / "atomic_json" / "edl.json"
        vl.save_json(p, {"ok": 1})
        with self.assertRaises(TypeError):
            vl.save_json(p, {"bad": object()})                              # not serialisable: fails half way through
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"ok": 1})
        self.assertEqual([q.name for q in p.parent.iterdir()], ["edl.json"])

    def test_save_json_roundtrips_unicode(self):
        p = helpers.tmpdir() / "atomic_json" / "fa.json"
        vl.save_json(p, {"text": "کویر مرنجاب ‌نیم‌فاصله"})
        self.assertEqual(vl.load_json(p)["text"], "کویر مرنجاب ‌نیم‌فاصله")


class Discovery(unittest.TestCase):
    """find_ffmpeg: environment override, search order and the message when nothing is found."""

    def setUp(self):
        self._saved = vl._FF_CACHE
        vl._FF_CACHE = None

    def tearDown(self):
        vl._FF_CACHE = self._saved

    def fake_dir(self, name):
        d = helpers.tmpdir() / name
        d.mkdir(exist_ok=True)
        exe = ".exe" if os.name == "nt" else ""
        for tool in ("ffmpeg", "ffprobe"):
            (d / f"{tool}{exe}").write_bytes(b"")
        return d

    def test_mh_ffmpeg_dir_is_searched_when_path_has_nothing(self):
        d = self.fake_dir("ff_env")
        with mock.patch.object(vl.shutil, "which", return_value=None), mock.patch.dict(os.environ, {"MH_FFMPEG_DIR": str(d)}):
            found = vl.find_ffmpeg(refresh=True)
        self.assertEqual(Path(found["ffmpeg"]).parent, d)
        self.assertEqual(Path(found["ffprobe"]).parent, d)

    def test_path_wins_over_the_fallback_folders(self):
        d = self.fake_dir("ff_env2")
        with mock.patch.object(vl.shutil, "which", side_effect=lambda t: f"/on/path/{t}"),                 mock.patch.dict(os.environ, {"MH_FFMPEG_DIR": str(d)}):
            self.assertEqual(vl.find_ffmpeg(refresh=True)["ffmpeg"], "/on/path/ffmpeg")

    def test_nothing_found_explains_how_to_fix_it(self):
        with mock.patch.object(vl.shutil, "which", return_value=None), mock.patch.object(vl, "_candidate_dirs", return_value=[]):
            with self.assertRaises(vl.EngineError) as cm:
                vl.find_ffmpeg(refresh=True)
        msg = str(cm.exception)
        self.assertIn("winget install Gyan.FFmpeg", msg)
        self.assertIn("MH_FFMPEG_DIR", msg)

    def test_candidate_folders_include_the_environment_override_first(self):
        with mock.patch.dict(os.environ, {"MH_FFMPEG_DIR": "X:/custom"}):
            self.assertEqual(vl._candidate_dirs()[0], "X:/custom")


class Compat(unittest.TestCase):
    """The README promises Python 3.9+. Only 3.12+ is installed here, so look for newer features statically."""

    NEW_NAMES = {"pairwise": (3, 10), "TypeAlias": (3, 10), "ParamSpec": (3, 10), "tomllib": (3, 11), "ExceptionGroup": (3, 11),
                 "StrEnum": (3, 11), "Self": (3, 11), "batched": (3, 12), "UTC": (3, 11), "bit_count": (3, 10),
                 "aiter": (3, 10), "anext": (3, 10), "cbrt": (3, 11), "exp2": (3, 11), "linear_regression": (3, 10),
                 "correlation": (3, 10), "covariance": (3, 10), "hardlink_to": (3, 10), "sumprod": (3, 12), "root_dir": (3, 10)}

    def files(self):
        return list((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "tests").glob("test_*.py")) + [ROOT / "tests" / "helpers.py"]

    def test_no_syntax_or_calls_newer_than_the_minimum(self):
        problems = []
        for path in self.files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                where = f"{path.name}:{getattr(node, 'lineno', '?')}"
                if hasattr(ast, "Match") and isinstance(node, ast.Match):
                    problems.append(f"{where} match statement (3.10)")
                if hasattr(ast, "TryStar") and isinstance(node, ast.TryStar):
                    problems.append(f"{where} except* (3.11)")
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "strict" and isinstance(node.func, ast.Name) and node.func.id == "zip":
                            problems.append(f"{where} zip(strict=) (3.10)")
                        if kw.arg in ("slots", "kw_only"):
                            problems.append(f"{where} {kw.arg}= (3.10)")
                    if isinstance(node.func, ast.Name) and node.func.id == "isinstance":
                        for arg in node.args[1:]:
                            if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.BitOr):
                                problems.append(f"{where} isinstance(x, A | B) (3.10)")
                name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
                if name in self.NEW_NAMES and self.NEW_NAMES[name] > MIN_PYTHON:
                    problems.append(f"{where} {name} (Python {'.'.join(map(str, self.NEW_NAMES[name]))})")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        for part in ((getattr(node, "module", None) or "").split(".")[0], a.name.split(".")[0]):
                            if self.NEW_NAMES.get(part, (0, 0)) > MIN_PYTHON:
                                problems.append(f"{where} import {part}")
        self.assertEqual(problems, [])

    def test_old_grammar_still_parses(self):
        """ast.parse with feature_version=(3, 9) rejects match statements, except*, parenthesised context managers ..."""
        bad = []
        for path in self.files():
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=MIN_PYTHON)
            except SyntaxError as e:
                bad.append(f"{path.name}:{e.lineno} {e.msg}")
        self.assertEqual(bad, [])

    def test_fstrings_use_the_pre_3_12_rules(self):
        """3.12 allows the string's own quote character and backslashes inside {...}; 3.9 to 3.11 do not."""
        bad = []
        for path in self.files():
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.JoinedStr):
                    continue
                whole = ast.get_source_segment(source, node) or ""
                m = re.match(r"[rRbBuU]*[fF][rRbBuU]*(\"\"\"|'''|\"|')", whole)
                if not m:
                    continue
                quote = m.group(1)
                for part in node.values:
                    if isinstance(part, ast.FormattedValue):
                        inner = ast.get_source_segment(source, part.value) or ""
                        if quote[0] in inner and len(quote) == 1 or "\\" in inner:
                            bad.append(f"{path.name}:{node.lineno}")
        self.assertEqual(sorted(set(bad)), [], "f-string expression reuses the quote character or contains a backslash")

    def test_modules_with_modern_annotations_postpone_their_evaluation(self):
        """`str | None` in a signature is a runtime error on 3.9 unless `from __future__ import annotations` is present."""
        bad = []
        for path in self.files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            has_future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and any(a.name == "annotations" for a in n.names)
                             for n in tree.body)
            if has_future:
                continue
            for node in ast.walk(tree):
                anns = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    anns += [a.annotation for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs if a.annotation]
                    anns += [node.returns] if node.returns else []
                elif isinstance(node, ast.AnnAssign):
                    anns.append(node.annotation)
                for ann in anns:
                    if any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(ann)):
                        bad.append(f"{path.name}:{node.lineno}")
        self.assertEqual(bad, [], "add `from __future__ import annotations` at the top of these files")

    def test_every_script_declares_future_annotations(self):
        for path in (ROOT / "scripts").glob("*.py"):
            self.assertIn("from __future__ import annotations", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
