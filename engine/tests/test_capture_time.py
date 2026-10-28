"""Capture time: container tags, cross-device ordering, clock shifts and the chronological inventory."""
import unittest

import helpers  # noqa: F401
import vid_probe as vp
import vidlib as vl


class Parsing(unittest.TestCase):
    def test_utc_creation_time(self):
        self.assertEqual(vl.parse_utc("2026-10-08T03:29:26.000000Z"), 1791430166.0)
        for bad in (None, "", "garbage", "1904-01-01T00:00:00.000000Z", "0000-00-00T00:00:00Z"):
            self.assertIsNone(vl.parse_utc(bad), bad)

    def test_apple_local_time_keeps_its_offset(self):
        self.assertEqual(vl.parse_local("2026-10-08T05:33:23+0330"), ("2026-10-08 05:33:23", 210))
        self.assertEqual(vl.parse_local("2026-10-08T05:33:23-0500"), ("2026-10-08 05:33:23", -300))
        self.assertEqual(vl.parse_local("2026-10-08T05:33:23+03:00"), ("2026-10-08 05:33:23", 180))
        self.assertIsNone(vl.parse_local("yesterday"))
        self.assertIsNone(vl.parse_local(None))

    def test_probe_reads_the_tags(self):
        info = vl.parse_probe({"format": {"duration": "2", "tags": {
            "creation_time": "2026-10-08T02:03:23.000000Z", "com.apple.quicktime.creationdate": "2026-10-08T05:33:23+0330",
            "com.apple.quicktime.model": "iPhone 17 Pro Max"}}, "streams": [{"codec_type": "video", "width": 4, "height": 4}]})
        self.assertEqual(info["device"], "iPhone 17 Pro Max")
        self.assertEqual(info["created_local"], "2026-10-08T05:33:23+0330")
        self.assertEqual(info["created_utc"], vl.parse_utc("2026-10-08T02:03:23Z"))


def rec(i, name, device=None, local=None, off=None, utc=None, mtime=0, dur=5.0, size=100):
    return {"index": i, "name": name, "device": device, "taken_local": local, "taken_offset_min": off,
            "created_utc": utc, "mtime": mtime, "duration": dur, "size": size, "error": None, "dup_of": None}


class Times(unittest.TestCase):
    def test_phone_local_time_and_camera_utc_agree_on_one_timeline(self):
        phone = rec(0, "IMG_1.MOV", "iPhone 17 Pro Max", "2026-10-08 06:00:00", 210)
        cam = rec(1, "DJI_1.MP4", "DJI OsmoPocket3", utc=vl.parse_utc("2026-10-08T02:30:30Z"))    # 06:00:30 local
        vp.assign_times([phone, cam])
        self.assertEqual((phone["time_source"], cam["time_source"]), ("device-local", "container-utc"))
        self.assertEqual(cam["taken"], "2026-10-08 06:00:30")
        self.assertEqual(round(cam["taken_epoch"] - phone["taken_epoch"]), 30)

    def test_a_camera_with_a_wrong_clock_can_be_shifted(self):
        cam = rec(0, "DJI_1.MP4", "DJI OsmoPocket3", utc=vl.parse_utc("2026-10-08T02:30:30Z"))
        vp.assign_times([cam], device_shift_min={"dji": 30})
        self.assertEqual(cam["taken"], "2026-10-08 06:30:30")
        self.assertEqual(cam["clock_shift_min"], 30)

    def test_file_time_is_the_last_resort(self):
        r = rec(0, "x.mp4", mtime=1791430000)
        vp.assign_times([r])
        self.assertEqual(r["time_source"], "file-mtime")

    def test_chronological_reindex_keeps_duplicates_consistent(self):
        a = rec(0, "a.mov", "iPhone", "2026-10-08 09:00:00", 210)
        b = rec(1, "b.mov", "iPhone", "2026-10-08 07:00:00", 210)
        c = rec(2, "c.mov", "iPhone", "2026-10-08 08:00:00", 210)
        c["dup_of"] = 0                                         # c is a copy of a
        recs = [a, b, c]
        vp.assign_times(recs)
        out = vp.chronological(recs)
        self.assertEqual([r["name"] for r in out], ["b.mov", "c.mov", "a.mov"])
        self.assertEqual([r["index"] for r in out], [0, 1, 2])
        self.assertEqual(out[1]["dup_of"], 2)                   # c now points at a's new index
        self.assertIsNone(out[0]["dup_of"])

    def test_timeline_csv(self):
        import csv
        recs = [rec(0, "a.mov", "iPhone", "2026-10-08 07:00:00", 210, dur=10.0),
                rec(1, "b.mov", "iPhone", "2026-10-08 07:01:00", 210, dur=5.0)]
        vp.assign_times(recs)
        out = helpers.tmpdir() / "tl"
        out.mkdir(exist_ok=True)
        path = vp.write_timeline(vp.chronological(recs), out)
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        self.assertEqual([r["name"] for r in rows], ["a.mov", "b.mov"])
        self.assertEqual(rows[1]["gap_s"], "50")                # 60 s start-to-start minus the 10 s clip


class Cli(unittest.TestCase):
    def test_clock_shift_argument_is_validated(self):
        import mh_video
        a = mh_video.build_parser().parse_args(["probe", "nowhere", "--work", "w", "--clock-shift", "DJI"])
        with self.assertRaises(vl.EngineError):
            mh_video.cmd_probe(a)


if __name__ == "__main__":
    unittest.main()
