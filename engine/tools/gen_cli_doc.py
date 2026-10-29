#!/usr/bin/env python3
"""Write docs/CLI.md from the argparse definitions in engine/scripts/mh_video.py (so the reference cannot drift).

    python engine/tools/gen_cli_doc.py            rewrite docs/CLI.md
    python engine/tools/gen_cli_doc.py --check    exit 1 if docs/CLI.md is out of date (the test suite does this too)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
import mh_video  # noqa: E402

OUT = ROOT / "docs" / "CLI.md"
PIPELINE = ["doctor", "prepare", "probe", "sheets", "score", "rank", "pick", "beats", "vision", "story", "need", "plan", "cuts", "lint", "render", "qa", "verify",
            "color", "text", "sfx", "audio", "music", "content", "clean"]
PIPE = "\\|"                                                                 # a literal pipe inside a Markdown table cell


def _subparsers(parser):
    for action in parser._actions:                                         # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):                 # noqa: SLF001
            helps = {a.dest: a.help for a in action._choices_actions}      # noqa: SLF001
            return action.choices, helps
    return {}, {}


def _options(parser) -> list[tuple[str, str, str]]:
    rows = []
    for a in parser._actions:                                              # noqa: SLF001
        if isinstance(a, (argparse._HelpAction, argparse._SubParsersAction)):   # noqa: SLF001
            continue
        name = ", ".join(a.option_strings) if a.option_strings else a.dest
        if a.option_strings and a.nargs != 0:
            name += " " + (a.metavar or a.dest.upper())
        required = "required" if (a.required if a.option_strings else True) else ""
        default = ""
        if a.option_strings and a.default not in (None, False, argparse.SUPPRESS):
            default = f"default `{a.default}`"
        if a.choices:
            default = (default + "; " if default else "") + "one of " + ", ".join(f"`{c}`" for c in a.choices)
        note = "; ".join(x for x in (required, default) if x)
        rows.append((f"`{name}`", note, (a.help or "").replace("%%", "%").replace("|", PIPE)))
    return rows


def _section(title: str, parser, help_text: str, level: int) -> list[str]:
    lines = [f"{'#' * level} `{title}`", ""]
    if help_text:
        lines += [help_text[0].upper() + help_text[1:] + ".", ""]
    usage = " ".join(parser.format_usage().replace("usage: ", "").split())
    lines += ["```", usage, "```", ""]
    rows = _options(parser)
    if rows:
        lines += ["| Argument | Required / default | Meaning |", "|---|---|---|"]
        lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
        lines.append("")
    return lines


def render() -> str:
    top = mh_video.build_parser()
    choices, helps = _subparsers(top)
    missing = set(choices) - set(PIPELINE)
    if missing:
        raise SystemExit(f"commands missing from PIPELINE in gen_cli_doc.py: {sorted(missing)}")
    out = ["# Command reference", "",
           "Generated from the program by `python engine/tools/gen_cli_doc.py`; do not edit by hand. Every command is run as",
           "`python \"<skill-dir>/engine/scripts/mh_video.py\" <command> ...`.", "",
           "Exit codes: 0 = success; 1 = a check failed (QA FAIL, lint errors, verify failed); 2 = bad input or an engine error "
           "(one-line message on stderr); 130 = interrupted. Set `MH_DEBUG=1` to get the full traceback for unexpected errors.", "",
           "| Command | What it does |", "|---|---|"]
    for name in PIPELINE:
        out.append(f"| [`{name}`](#{name}) | {helps.get(name, '')} |")
    out.append("")
    for name in PIPELINE:
        sp = choices[name]
        nested, nhelps = _subparsers(sp)
        out += _section(name, sp, helps.get(name, ""), 2)
        for sub_name, sub_parser in nested.items():
            out += _section(f"{name} {sub_name}", sub_parser, nhelps.get(sub_name, ""), 3)
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("docs/CLI.md is out of date: run python engine/tools/gen_cli_doc.py", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
