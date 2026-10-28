"""Repository consistency: docs, frontmatter, links, CLI commands named in the docs, examples, version."""
import compileall
import re
import unittest
from pathlib import Path

import helpers  # noqa: F401
import mh_video
import vid_edl as ve
import vidlib as vl

ROOT = Path(__file__).resolve().parent.parent.parent
MD_FILES = [p for p in ROOT.rglob("*.md") if ".git" not in p.parts]


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path} has no frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


class Frontmatter(unittest.TestCase):
    def test_skill(self):
        fm = frontmatter(ROOT / "SKILL.md")
        self.assertEqual(fm["name"], "mh-video-editor-skill")
        self.assertGreater(len(fm["description"]), 80)
        self.assertEqual(fm["name"], ROOT.name)

    def test_agent(self):
        fm = frontmatter(ROOT / "agents" / "mh-video-editor.md")
        self.assertEqual(fm["name"], "mh-video-editor")
        self.assertIn("NEEDS_DISCOVERY", (ROOT / "agents" / "mh-video-editor.md").read_text(encoding="utf-8"))
        self.assertNotIn("WebFetch", fm.get("tools", ""))          # no network tools
        self.assertIn("Bash", fm["tools"])


class Links(unittest.TestCase):
    def test_relative_markdown_links_resolve(self):
        bad = []
        for md in MD_FILES:
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (md.parent / target).exists():
                    bad.append(f"{md.relative_to(ROOT)} -> {target}")
        self.assertEqual(bad, [])

    def test_files_named_in_skill_exist(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for rel in re.findall(r"`((?:references|engine|agents|docs)/[A-Za-z0-9_./-]+)`", text):
            if rel.endswith("/") or "*" in rel or "<" in rel:
                continue
            self.assertTrue((ROOT / rel).exists(), rel)


class Commands(unittest.TestCase):
    def test_every_documented_command_exists(self):
        parser = mh_video.build_parser()
        names = set(parser._subparsers._group_actions[0].choices)       # noqa: SLF001
        docs = ["SKILL.md", "README.md", "agents/mh-video-editor.md"] + sorted(
            p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md") if p.name != "CLI.md")
        for doc in docs:
            text = (ROOT / doc).read_text(encoding="utf-8")
            for cmd in re.findall(r"mh_video\.py\"?\s+([a-z]+)", text):
                self.assertIn(cmd, names, f"{doc}: {cmd}")
        for required in ("doctor", "probe", "sheets", "score", "rank", "pick", "beats", "need", "plan", "lint", "render", "verify"):
            self.assertIn(required, names)

    def test_persian_guide_covers_every_command_group(self):
        text = (ROOT / "docs" / "GUIDE.fa.md").read_text(encoding="utf-8")
        for cmd in ("doctor", "probe", "sheets", "score", "pick", "beats", "need", "plan", "lint", "render", "qa", "color", "text"):
            self.assertRegex(text, rf'mh_video\.py"?\s+{cmd}\b', cmd)

    def test_every_reference_is_listed_in_skill_md(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for p in (ROOT / "references").glob("*.md"):
            self.assertIn(f"references/{p.name}", skill, p.name)

    def test_help_works(self):
        with self.assertRaises(SystemExit) as cm:
            mh_video.main(["--help"])
        self.assertEqual(cm.exception.code, 0)


class Code(unittest.TestCase):
    def test_scripts_compile(self):
        self.assertTrue(compileall.compile_dir(str(ROOT / "engine" / "scripts"), quiet=1, force=True))

    def test_no_control_characters_in_text_files(self):
        """A stray backspace or form feed (from a mangled escape in a scripted edit) once ended up in a test and a doc."""
        bad = []
        for p in ROOT.rglob("*"):
            if p.is_file() and p.suffix in {".py", ".md", ".json", ".yml", ".sh", ".ps1", ".txt"} and ".git" not in p.parts:
                data = p.read_bytes()
                if any(b < 32 and b not in (9, 10, 13) for b in data):
                    bad.append(p.relative_to(ROOT).as_posix())
        self.assertEqual(bad, [])

    def test_no_crlf_in_scripts(self):
        for p in list((ROOT / "engine" / "scripts").glob("*.py")) + [ROOT / "install.sh"]:
            self.assertNotIn(b"\r\n", p.read_bytes(), p.name)

    def test_no_third_party_imports(self):
        std = {"__future__", "argparse", "array", "atexit", "compileall", "concurrent", "copy", "csv", "datetime", "glob",
               "hashlib", "json", "math", "os", "pathlib", "platform", "random", "re", "shutil", "statistics", "struct",
               "subprocess", "sys", "tempfile", "time", "unittest", "wave", "typing", "bisect", "unicodedata", "ast",
               "threading", "zipfile"}
        import ast
        local = {p.stem for p in (ROOT / "engine" / "scripts").glob("*.py")} | {"helpers"}
        for p in (ROOT / "engine" / "scripts").glob("*.py"):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods = [node.module.split(".")[0]]
                for mod in mods:
                    self.assertTrue(mod in std or mod in local, f"{p.name} imports {mod}")


class Examples(unittest.TestCase):
    def test_example_edl_loads_and_lints(self):
        edl = ve.load(ROOT / "engine" / "examples" / "edl.example.json")
        issues = ve.lint(edl, check_files=False)
        self.assertEqual(issues, [], ve.format_issues(issues))
        bounds = ve.timeline(edl)
        self.assertEqual(bounds[-1], 240)
        self.assertEqual(ve.effective_transitions(edl, bounds)[ve.drop_boundary(edl, bounds)], "fadewhite")

    def test_example_clip_list_parses(self):
        import vid_plan
        items = vid_plan.parse_clip_list(ROOT / "engine" / "examples" / "clips.example.txt")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]["src_start"], 4.5)

    def test_presets_are_complete(self):
        for p in (ROOT / "engine" / "presets").glob("*.json"):
            data = vl.load_json(p)
            self.assertIn("name", data)
            self.assertTrue("grade" in data or {"width", "height", "fps"} <= set(data), p.name)


class QaDocs(unittest.TestCase):
    def test_every_check_id_is_documented(self):
        code = (ROOT / "engine" / "scripts" / "vid_qa.py").read_text(encoding="utf-8")
        ids = set(re.findall(r'"id": "([a-z-]+)"', code)) | set(re.findall(r'"id": f"(lint)-', code))
        ids |= {"restraint"}
        doc = (ROOT / "references" / "qa-gate.md").read_text(encoding="utf-8")
        missing = sorted(i for i in ids if i not in doc and not (i == "lint" and "lint-*" in doc))
        self.assertEqual(missing, [], "document these checks in references/qa-gate.md")

    def test_documented_thresholds_match_the_code(self):
        import vid_qa
        doc = (ROOT / "references" / "qa-gate.md").read_text(encoding="utf-8")
        self.assertIn(f"YAVG > {vid_qa.WHITE_YAVG:.0f} and YLOW > {vid_qa.WHITE_YLOW:.0f}", doc)
        self.assertIn(f"{vid_qa.CAST_BOTH_ABOVE:.0f}", doc)
        self.assertIn(f"{vid_qa.MAX_FILE_MB:.0f} MB", doc)
        self.assertIn(f"YAVG < {vid_qa.BLACK_YAVG:.0f}", doc)


class Version(unittest.TestCase):
    def test_version_consistent(self):
        v = (ROOT / "VERSION").read_text().strip()
        self.assertRegex(v, r"^\d+\.\d+\.\d+$")
        self.assertIn(f"## [{v}]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertIn(f"version-{v}-", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_license_present(self):
        self.assertIn("MIT License", (ROOT / "LICENSE").read_text(encoding="utf-8"))


class ShotNotes(unittest.TestCase):
    def test_example_shot_notes_use_the_documented_keys(self):
        import json
        keys = {"role", "subject", "camera", "shot_size", "motion_dir", "focus", "mood", "light", "best_start", "notes"}
        data = json.loads((ROOT / "engine" / "examples" / "shot-notes.example.json").read_text(encoding="utf-8"))
        self.assertTrue(data)
        for clip, note in data.items():
            self.assertLessEqual(set(note), keys, clip)
            if "shot_size" in note:
                self.assertIn(note["shot_size"], {"wide", "medium", "close", "detail"})
            if "motion_dir" in note:
                self.assertIn(note["motion_dir"], {"left", "right", "up", "down", "toward", "away", "none"})
        text = (ROOT / "references" / "footage-reading.md").read_text(encoding="utf-8")
        for k in keys:
            self.assertIn(f"`{k}`", text, k)


if __name__ == "__main__":
    unittest.main()
