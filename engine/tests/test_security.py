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

# Heavy third-party / ML runtimes. The promise is standard library + FFmpeg only; perception that needs a model lives
# in the optional plug-in (plugins/vision), which has its own virtual environment and is started as a separate process.
# The engine process must import none of these, so the skill installs and runs with nothing but Python and FFmpeg.
ML_DEPS = {"numpy", "scipy", "sklearn", "pandas", "torch", "tensorflow", "onnx", "onnxruntime", "tokenizers",
           "transformers", "cv2", "PIL", "Pillow", "skimage", "matplotlib", "librosa", "soundfile", "moviepy", "ffmpeg"}


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0]


class NoNetworkImports(unittest.TestCase):
    def test_no_engine_script_imports_a_network_module(self):
        bad = [f"{p.name}: {m}" for p in SCRIPTS.glob("*.py") for m in _imported_modules(p) if m in FORBIDDEN]
        self.assertEqual(bad, [], "engine scripts must never import a networking module")

    def test_tools_do_not_import_a_network_module(self):
        bad = [f"{p.name}: {m}" for p in (ROOT / "engine" / "tools").glob("*.py") for m in _imported_modules(p) if m in FORBIDDEN]
        self.assertEqual(bad, [])

    def test_engine_imports_no_heavy_or_ml_dependency(self):
        """The zero-dependency promise and the plug-in boundary: nothing under engine/ pulls in a model runtime."""
        bad = []
        for sub in ("scripts", "tools"):
            for p in (ROOT / "engine" / sub).glob("*.py"):
                bad += [f"{sub}/{p.name}: {m}" for m in _imported_modules(p) if m in ML_DEPS]
        self.assertEqual(bad, [], "the engine must stay standard-library + FFmpeg; a model belongs in plugins/vision")

    def test_engine_never_imports_from_the_plugin_package(self):
        """vid_vision / vid_content talk to the plug-in only as a subprocess; they must not import its code in-process."""
        bad = [f"{p.name}: {m}" for p in SCRIPTS.glob("*.py") for m in _imported_modules(p)
               if m in {"vision_run", "content_run", "plugins"}]
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
