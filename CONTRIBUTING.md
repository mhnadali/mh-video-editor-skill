# Contributing

## Before and after any change
1. Run the tests: `cd engine/tests && python run_tests.py` (about 5-9 minutes with FFmpeg; `--fast` skips the slow end-to-end renders and takes about 2 minutes; without FFmpeg the end-to-end tests skip). `python run_tests.py -k name` runs a subset.
2. Check coverage when you add code: `python coverage_report.py` (Python 3.12+). New code needs tests; the repository sits at about 96 %.
3. If you changed a command or an argument: `python engine/tools/gen_cli_doc.py` regenerates `docs/CLI.md` (a test fails when it is stale). Every argument needs a help text (`ARG_HELP` in `mh_video.py`).
4. Update `CHANGELOG.md` and `VERSION` (and the badge in `README.md`); a test checks they agree.

## Rules
- The engine uses only the Python standard library and targets **Python 3.9+**: no `match`, no `X | Y` outside annotations (every script starts with `from __future__ import annotations`), no f-string that reuses its own quote character, no 3.10+ library functions. A static test enforces this because CI is the only place that runs 3.9.
- FFmpeg is the only external dependency. A new filter must be listed in `mh_video.REQUIRED_FILTERS` (every build has it) or in `vidlib.FEATURE_FILTERS` (optional, so `doctor` and `lint` can explain its absence); `test_capabilities` renders a project that uses everything and fails on an unlisted filter. Tests that need an optional filter use `helpers.requires_filters(...)`.
- Bad input produces a message, not a traceback: put shape and type checks for new EDL fields in `vid_edl.structure_errors` (the mutation fuzzer in `test_fuzz.py` fails when a damaged field is only caught by the generic safety net).
- Files that several workers or processes may touch (LUTs, caches, JSON) are written through `vl.save_json` / `vl.replace_file` (atomic).
- Every bug fix gets a test and a line in `references/pitfalls.md`.
- Changes that add visible effects must respect the budget in `references/effects-and-transitions.md` or change it deliberately, with a reason.
- Documentation is part of the change: `SKILL.md` (workflow), `references/` (details), the Persian guide `docs/GUIDE.fa.md` when user-visible behaviour changes.
- Scripts and Markdown use LF line endings (`.gitattributes` enforces it).

## Writing tests
- Use the synthetic fixtures in `engine/tests/helpers.py` (`make_clip`, `click_track`, `make_hlg_clip` ...); never real footage.
- Mark tests that render for many seconds with `@helpers.slow` so `--fast` can skip them.
- Pure functions get seeded randomised tests (see `test_fuzz.py`), rendered output gets pixel or frame-count checks, not just "file exists".
- Name output files of a test so they cannot overwrite their own input (`out_*.mp4`).

## Release checklist
1. `python engine/tests/run_tests.py` on your machine, and the CI run green on Linux and Windows.
2. `python engine/tools/gen_cli_doc.py`, `CHANGELOG.md`, `VERSION`, README badge.
3. `./install.sh` / `.\install.ps1` into a temporary home and `doctor` there.
4. Tag after the maintainer agrees; pushing is the maintainer's decision.
