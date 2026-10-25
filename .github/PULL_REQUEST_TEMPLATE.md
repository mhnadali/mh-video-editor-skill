# Pull request

## What and why
<!-- One or two sentences: what changes, and the problem it solves. -->

## How it was verified
- [ ] `cd engine/tests && python run_tests.py` passes locally (or `--fast` with a note on what was skipped)
- [ ] `python engine/tools/gen_cli_doc.py` run if a command or argument changed
- [ ] `CHANGELOG.md`, `VERSION` and the README badge updated if user-visible behaviour changed
- [ ] New code has tests; a bug fix has a regression test and a line in `references/pitfalls.md`
- [ ] Documentation updated (`SKILL.md`, `references/`, the Persian guide for user-visible changes)

## Notes for the reviewer
<!-- Anything that needs a human eye: a look you cannot judge from numbers, a platform you could not test. -->
