"""Phase 5: the command line: help for everything, a generated reference that cannot drift, exit codes."""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

import helpers
import mh_video

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "engine" / "tools"))
import gen_cli_doc  # noqa: E402


def walk(parser, path=()):
    """Yield (path, parser) for every command and sub-command."""
    for action in parser._actions:                                          # noqa: SLF001
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            for name, sub in action.choices.items():
                yield path + (name,), sub
                yield from walk(sub, path + (name,))


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = mh_video.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Help(unittest.TestCase):
    def test_every_command_and_subcommand_prints_help(self):
        seen = 0
        for path, _ in walk(mh_video.build_parser()):
            code, out, _ = run(*path, "-h")
            self.assertEqual(code, 0, path)
            self.assertIn("usage:", out, path)
            seen += 1
        self.assertGreaterEqual(seen, 22)                                   # 16 commands + 6 sub-commands

    def test_every_argument_explains_itself(self):
        missing = []
        for path, parser in walk(mh_video.build_parser()):
            for a in parser._actions:                                       # noqa: SLF001
                if a.dest in ("help",) or hasattr(a, "choices") and isinstance(a.choices, dict):
                    continue
                if not a.help:
                    missing.append(f"{' '.join(path)} {a.dest}")
        self.assertEqual(missing, [], "add the argument to ARG_HELP in mh_video.py")

    def test_every_command_has_a_one_line_description(self):
        choices, helps = gen_cli_doc._subparsers(mh_video.build_parser())  # noqa: SLF001
        for name in choices:
            self.assertTrue(helps.get(name), name)


class Reference(unittest.TestCase):
    def test_cli_reference_is_up_to_date(self):
        current = (ROOT / "docs" / "CLI.md").read_text(encoding="utf-8")
        self.assertEqual(current, gen_cli_doc.render(), "run: python engine/tools/gen_cli_doc.py")

    def test_every_command_is_mentioned_in_skill_md(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for name in gen_cli_doc.PIPELINE:
            self.assertIn(name, skill, name)

    def test_the_command_list_in_the_tool_is_complete(self):
        choices, _ = gen_cli_doc._subparsers(mh_video.build_parser())      # noqa: SLF001
        self.assertEqual(set(choices), set(gen_cli_doc.PIPELINE))


class ExitCodes(unittest.TestCase):
    def test_lint_exit_codes_and_messages(self):
        d = helpers.tmpdir() / "cli_exit"
        d.mkdir(exist_ok=True)
        good = json.loads((ROOT / "engine" / "examples" / "edl.example.json").read_text(encoding="utf-8"))
        (d / "good.json").write_text(json.dumps(good), encoding="utf-8")
        bad = dict(good, shots=[])
        (d / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
        code, out, _ = run("lint", str(d / "good.json"), "--no-files")
        self.assertEqual(code, 0)
        self.assertIn("OK", out)
        code, out, _ = run("lint", str(d / "bad.json"), "--no-files")
        self.assertEqual(code, 1)
        self.assertIn("no-shots", out)

    def test_engine_errors_are_exit_2_with_one_line(self):
        code, out, err = run("lint", str(helpers.tmpdir() / "nope.json"))
        self.assertEqual(code, 2)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertTrue(err.startswith("error:"))

    def test_unexpected_input_errors_are_exit_2_not_a_traceback(self):
        d = helpers.tmpdir() / "cli_exit"
        d.mkdir(exist_ok=True)
        (d / "weird.json").write_text('{"version": 1, "project": {}, "shots": [{"clip": "a.mp4", "span": [0, 1]}]}', encoding="utf-8")
        code, _, err = run("sfx", "plan", str(d / "weird.json"))
        self.assertIn(code, (0, 2))
        self.assertNotIn("Traceback", err)

    def test_unexpected_exceptions_become_one_line_unless_debugging(self):
        import os
        from unittest import mock

        def boom(_args):
            raise KeyError("shots")

        parser = mh_video.build_parser()
        with mock.patch.object(mh_video, "build_parser", return_value=parser):
            for action in parser._actions:                                  # noqa: SLF001
                if hasattr(action, "choices") and isinstance(action.choices, dict):
                    action.choices["need"].set_defaults(fn=boom)
            code, out, err = run("need", "x.json")
            self.assertEqual(code, 2)
            self.assertIn("could not process the input (KeyError", err)
            self.assertEqual(len(err.strip().splitlines()), 1)
            with mock.patch.dict(os.environ, {"MH_DEBUG": "1"}):
                with self.assertRaises(KeyError):
                    mh_video.main(["need", "x.json"])

    def test_ctrl_c_exits_130(self):
        from unittest import mock

        def interrupted(_args):
            raise KeyboardInterrupt

        parser = mh_video.build_parser()
        with mock.patch.object(mh_video, "build_parser", return_value=parser):
            for action in parser._actions:                                  # noqa: SLF001
                if hasattr(action, "choices") and isinstance(action.choices, dict):
                    action.choices["need"].set_defaults(fn=interrupted)
            code, _, err = run("need", "x.json")
        self.assertEqual(code, 130)
        self.assertIn("interrupted", err)

    def test_unknown_command_is_an_argparse_error(self):
        code, _, err = run("frobnicate")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_missing_required_argument(self):
        code, _, err = run("probe")
        self.assertEqual(code, 2)
        self.assertIn("required", err)


if __name__ == "__main__":
    unittest.main()
