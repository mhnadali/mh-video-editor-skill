import unittest

import helpers  # noqa: F401  (sets sys.path)
import vidlib as vl


class ParseProbe(unittest.TestCase):
    def test_rate_fractions(self):
        self.assertAlmostEqual(vl._rate("30000/1001"), 29.97, places=2)
        self.assertEqual(vl._rate("30/1"), 30.0)
        self.assertEqual(vl._rate("0/0"), 0.0)
        self.assertEqual(vl._rate(None), 0.0)
        self.assertEqual(vl._rate("25"), 25.0)

    def test_rotation_swaps_dimensions(self):
        data = {"streams": [{"codec_type": "video", "width": 3840, "height": 2160, "avg_frame_rate": "30/1",
                             "side_data_list": [{"rotation": -90}]}], "format": {"duration": "12.5", "size": "100"}}
        info = vl.parse_probe(data)
        self.assertEqual((info["width"], info["height"]), (2160, 3840))
        self.assertEqual(info["rotation"], -90)
        self.assertAlmostEqual(info["duration"], 12.5)

    def test_no_rotation_keeps_dimensions(self):
        data = {"streams": [{"codec_type": "video", "width": 1728, "height": 3072, "avg_frame_rate": "25/1"}],
                "format": {"duration": "3"}}
        info = vl.parse_probe(data)
        self.assertEqual((info["width"], info["height"]), (1728, 3072))

    def test_legacy_rotate_tag(self):
        data = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080, "tags": {"rotate": "90"}}],
                "format": {"duration": "1"}}
        self.assertEqual(vl.parse_probe(data)["height"], 1920)

    def test_hdr_detection(self):
        hlg = {"streams": [{"codec_type": "video", "width": 10, "height": 10, "color_transfer": "arib-std-b67"}], "format": {}}
        sdr = {"streams": [{"codec_type": "video", "width": 10, "height": 10, "color_transfer": "bt709"}], "format": {}}
        self.assertTrue(vl.parse_probe(hlg)["hdr"])
        self.assertFalse(vl.parse_probe(sdr)["hdr"])

    def test_audio_only_file(self):
        info = vl.parse_probe({"streams": [{"codec_type": "audio", "sample_rate": "44100", "channels": 2}],
                               "format": {"duration": "28.2"}})
        self.assertFalse(info["has_video"])
        self.assertTrue(info["has_audio"])

    def test_empty_probe_is_safe(self):
        info = vl.parse_probe({})
        self.assertEqual(info["duration"], 0.0)
        self.assertFalse(info["has_video"])


class Helpers(unittest.TestCase):
    def test_family_and_banned(self):
        self.assertEqual(vl.family_of("slideleft"), "horizontal")
        self.assertEqual(vl.family_of("vertopen"), "vertical")
        self.assertIsNone(vl.family_of("nonsense"))
        self.assertIn("zoomin", vl.BANNED_TRANSITIONS)
        self.assertNotIn("zoomin", vl.ALL_TRANSITIONS - {"zoomin"})

    def test_planner_vocabulary_is_valid(self):
        for name in vl.PLANNER_TRANSITIONS:
            self.assertIn(name, vl.ALL_TRANSITIONS)
            self.assertNotIn(name, vl.BANNED_TRANSITIONS)
        self.assertEqual(len(vl.PLANNER_TRANSITIONS), len(set(vl.PLANNER_TRANSITIONS)))

    def test_paths(self):
        self.assertEqual(vl.ffp("C:\\a b\\c.mp4"), "C:/a b/c.mp4")
        self.assertEqual(vl.filter_escape_path("C:\\Windows\\Fonts\\a.ttf"), "C\\:/Windows/Fonts/a.ttf")

    def test_frames_and_even(self):
        self.assertEqual(vl.frames(28.235, 30), 847)
        self.assertEqual(vl.even(539.9), 540)
        self.assertEqual(vl.even(135), 136)
        self.assertEqual(vl.even(1), 2)

    def test_load_json_errors(self):
        with self.assertRaises(vl.EngineError):
            vl.load_json("definitely/missing.json")

    def test_presets_load(self):
        for name in ("warm-desert", "neutral", "reels-9x16", "youtube-16x9", "square-1x1"):
            self.assertTrue(vl.load_preset(name))
        self.assertEqual(vl.load_preset("reels-9x16")["height"], 1920)

    @unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
    def test_ffmpeg_found_and_versioned(self):
        ff = vl.find_ffmpeg()
        self.assertTrue(ff["ffmpeg"] and ff["ffprobe"])
        self.assertGreaterEqual(vl.ffmpeg_version()[0], 4)
        self.assertIn(vl.filter_script_flag(), ("-/filter_complex", "-filter_complex_script"))


if __name__ == "__main__":
    unittest.main()
