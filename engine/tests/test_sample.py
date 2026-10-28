"""Phase 7: the zero-footage sample project builds a lint-clean EDL and renders a preview end to end."""
import sys
import unittest
from pathlib import Path

import helpers
import vidlib as vl

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "engine" / "tools"))
import make_sample  # noqa: E402

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")


@NEEDS_FF
@helpers.slow
class Sample(unittest.TestCase):
    def test_sample_project_is_lint_clean_and_renders(self):
        out = helpers.tmpdir() / "sample_proj"
        res = make_sample.make_sample(out, clips=6, seconds=3.0)
        self.assertTrue(res["lint_clean"], "generated sample EDL should lint clean")
        self.assertTrue(Path(res["edl"]).is_file())
        import vid_render as vr
        r = vr.render(res["edl"], out=out / "sample.mp4", encoder="libx264", workdir=out / "w",
                      preview=True, log=lambda *_: None)
        self.assertTrue(Path(r["out"]).is_file())
        self.assertTrue(vl.probe(r["out"])["has_video"])


if __name__ == "__main__":
    unittest.main()
