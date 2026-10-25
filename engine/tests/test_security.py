"""Security invariant: the engine never reaches the network. It only runs FFmpeg on local files (see SECURITY.md).

Two guards: a static scan that no engine script imports a networking module, and a runtime check that a representative
set of engine operations complete with the socket layer disabled.
"""
import ast
import unittest
from pathlib import Path
from unittest import mock

import vid_edl as ve
import vid_plan
import vid_color as vc

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "engine" / "scripts"

# Modules that would let code reach the network. The engine must import none of them.
FORBIDDEN = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "asyncio",
             "xmlrpc", "webbrowser", "requests", "httpx", "aiohttp", "urllib3", "paramiko"}


class NoNetworkImports(unittest.TestCase):
    def test_no_engine_script_imports_a_network_module(self):
        bad = []
        for p in SCRIPTS.glob("*.py"):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module.split(".")[0]]
                for m in mods:
                    if m in FORBIDDEN:
                        bad.append(f"{p.name}: {m}")
        self.assertEqual(bad, [], "engine scripts must never import a networking module")

    def test_tools_do_not_import_a_network_module(self):
        bad = []
        for p in (ROOT / "engine" / "tools").glob("*.py"):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module.split(".")[0]]
                bad += [f"{p.name}: {m}" for m in mods if m in FORBIDDEN]
        self.assertEqual(bad, [])


class NoNetworkAtRuntime(unittest.TestCase):
    def test_pure_operations_work_with_sockets_disabled(self):
        import socket

        def deny(*a, **k):
            raise AssertionError("the engine tried to open a socket")

        edl = {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": 4.0, "music": None},
               "style": {"grade": "warm-desert"}, "transitions": {"1": "fade"},
               "shots": [{"clip": "a.mp4", "span": [0, 2.0]}, {"clip": "b.mp4", "span": [2.0, 4.0]}]}
        clips = helpers_tmp_clip_list()
        with mock.patch.object(socket, "socket", deny), mock.patch.object(socket, "create_connection", deny):
            ve.lint(edl, check_files=False)                       # structure + budget, no files, no network
            items = vid_plan.parse_clip_list(clips)
            self.assertEqual(len(items), 2)
            lut = vc.build_lut(vc.look_function(vc.LOOKS["desert-gold"]), 5)
            self.assertEqual(len(lut), 125)


def helpers_tmp_clip_list() -> Path:
    import helpers
    p = helpers.tmpdir() / "sec_clips.txt"
    p.write_text("one.mp4 @ 1.0\ntwo.mp4\n", encoding="utf-8")
    return p


if __name__ == "__main__":
    unittest.main()
