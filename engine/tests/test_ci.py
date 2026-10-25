"""The GitHub Actions workflow cannot be run locally, so check what can be checked: structure, referenced files, versions."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "mh-video-editor-skill.yml"


@unittest.skipUnless(WORKFLOW.is_file(), "workflow file is not part of this checkout")
class Workflow(unittest.TestCase):
    text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.is_file() else ""

    def test_plain_yaml_hygiene(self):
        self.assertNotIn("\t", self.text, "YAML forbids tabs")
        self.assertNotIn("\r", self.text)
        self.assertTrue(self.text.endswith("\n"))
        for n, line in enumerate(self.text.splitlines(), 1):
            indent = len(line) - len(line.lstrip(" "))
            self.assertEqual(indent % 2, 0, f"line {n}: indentation is not a multiple of 2")

    def test_triggers_and_jobs(self):
        for needle in ("\non:\n", "  push:", "  pull_request:", "  workflow_dispatch:", "\njobs:\n", "permissions:", "contents: read"):
            self.assertIn(needle, self.text, needle)
        jobs = re.findall(r"^  ([a-z][a-z0-9_-]*):\s*$", self.text.split("\njobs:\n", 1)[1], re.M)
        self.assertEqual(jobs, ["python", "full", "coverage"])

    def test_every_job_has_a_timeout_and_a_runner(self):
        body = self.text.split("\njobs:\n", 1)[1]
        blocks = re.split(r"^  [a-z][a-z0-9_-]*:\s*$", body, flags=re.M)[1:]
        self.assertEqual(len(blocks), 3)
        for b in blocks:
            self.assertIn("runs-on:", b)
            self.assertIn("timeout-minutes:", b)

    def test_actions_are_pinned_to_a_major_version(self):
        uses = re.findall(r"uses:\s*(\S+)", self.text)
        self.assertGreaterEqual(len(uses), 4)
        for u in uses:
            self.assertRegex(u, r"^[\w.-]+/[\w.-]+@v\d+$", u)

    def test_files_and_scripts_it_runs_exist(self):
        for rel in set(re.findall(r"(engine/[\w./-]+\.py|install\.(?:sh|ps1))", self.text)):
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_runs_from_the_repository_root(self):
        self.assertNotIn("working-directory", self.text)

    def test_oldest_supported_python_is_in_the_matrix(self):
        self.assertIn('"3.9"', self.text)

    def test_no_path_filters(self):
        self.assertNotIn("paths:", self.text)

    def test_coverage_floor_is_below_what_is_measured_here(self):
        m = re.search(r"coverage_report\.py --min (\d+)", self.text)
        self.assertIsNotNone(m)
        self.assertLessEqual(int(m.group(1)), 90)           # measured 94 % on Windows; Linux CI lacks NVENC and some fonts


if __name__ == "__main__":
    unittest.main()
