# Releasing

The engine has no build step; a release is a tag plus a GitHub Release whose notes come from the changelog.

## Checklist
1. `cd engine/tests && python run_tests.py` is green locally, and CI is green on Linux, macOS and Windows.
2. `python engine/tools/gen_cli_doc.py` (regenerates `docs/CLI.md`; a test fails if it is stale).
3. Update `CHANGELOG.md` (a new `## [x.y.z] - DATE` section), `VERSION`, and the `version-x.y.z` badge in `README.md`. A test checks the three agree.
4. Install into a temporary home and run `doctor` there: `./install.sh` / `.\install.ps1`.
5. Commit, then tag and push the tag:
   ```bash
   git tag v0.6.0
   git push origin v0.6.0
   ```
   The `release` workflow verifies the tag matches `VERSION`, extracts the matching changelog section, and creates the GitHub Release.

## Branch protection (maintainer, one-time in GitHub settings)
On `main`, require pull requests and the status checks `python`, `full` and `coverage` to pass before merging, and keep the branch up to date. These are repository settings; they cannot be set from the codebase.

## Versioning
[SemVer](https://semver.org/): patch for fixes, minor for backward-compatible features (new commands, flags, looks, delivery presets), major for a breaking change to the EDL format or the CLI contract.
