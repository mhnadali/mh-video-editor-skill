"""Phase 4: on-screen text with Persian (RTL) support, rendered by libass."""
import os
import subprocess
import unittest
from pathlib import Path

import helpers
import mh_video
import vid_edl as ve
import vid_text as vt
import vidlib as vl

NEEDS_FF = unittest.skipUnless(helpers.ffmpeg_available(), "ffmpeg not installed")
FA = "کویر مرنجاب"
FA_LONG = "سفر مهر ۱۴۰۵ | ۳۰ ثانیه از هیجان و ریتم کویر"


class Language(unittest.TestCase):
    def test_is_persian_uses_the_first_strong_letter(self):
        self.assertTrue(vt.is_persian(FA))
        self.assertTrue(vt.is_persian("123 " + FA))
        self.assertTrue(vt.is_persian("«سلام» Hello"))
        self.assertFalse(vt.is_persian("Maranjab"))
        self.assertFalse(vt.is_persian("Maranjab کویر"))
        self.assertFalse(vt.is_persian("2026"))
        self.assertFalse(vt.is_persian(""))

    def test_has_rtl(self):
        self.assertTrue(vt.has_rtl("abc کویر"))
        self.assertFalse(vt.has_rtl("abc"))

    def test_digits(self):
        self.assertEqual(vt.to_persian_digits("مهر 1405"), "مهر ۱۴۰۵")
        self.assertEqual(vt.normalise_persian("١٤٠٥"), "۱۴۰۵")
        self.assertEqual(vt.to_persian_digits("abc"), "abc")

    def test_normalise_arabic_letters_and_spaces(self):
        self.assertEqual(vt.normalise_persian("كيان   ويژه"), "کیان ویژه")
        self.assertEqual(vt.normalise_persian("  سلام  "), "سلام")
        self.assertIn("\u200c", vt.normalise_persian("ماسه\u200cها"))               # the half-space (ZWNJ) is never removed

    def test_wrap_is_balanced_and_keeps_words(self):
        lines = vt.wrap_text(FA_LONG, 25)
        self.assertEqual(len(lines), 2)
        self.assertEqual(" ".join(lines), FA_LONG)
        self.assertLess(abs(len(lines[0]) - len(lines[1])), 10)
        self.assertEqual(vt.wrap_text("short", 25), ["short"])
        self.assertEqual(vt.wrap_text("one\ntwo", 25), ["one", "two"])
        self.assertEqual(vt.wrap_text("a" * 50, 10), ["a" * 50])                     # a single long word is left alone
        words = "one two three four five six seven eight nine ten".split()
        out = vt.wrap_text(" ".join(words), 18)
        self.assertTrue(all(len(x) <= 22 for x in out))
        self.assertEqual(" ".join(out).split(), words)

    def test_ass_formatting(self):
        self.assertEqual(vt.ass_time(0), "0:00:00.00")
        self.assertEqual(vt.ass_time(61.5), "0:01:01.50")
        self.assertEqual(vt.ass_time(3725.04), "1:02:05.04")
        self.assertEqual(vt.ass_color("#FF8000"), "&H000080FF")
        self.assertEqual(vt.ass_color("ffffff", 255), "&HFFFFFFFF")
        with self.assertRaises(vl.EngineError):
            vt.ass_color("red")
        self.assertEqual(vt.ass_escape("a{b}c\\d"), "a\\{b\\}c\\\\d")

    def test_safe_areas_by_aspect(self):
        self.assertEqual(vt.safe_area(1080, 1920), vt.SAFE_VERTICAL)
        self.assertEqual(vt.safe_area(1920, 1080), vt.SAFE_LANDSCAPE)
        self.assertEqual(vt.safe_area(1080, 1080), vt.SAFE_SQUARE)


class Srt(unittest.TestCase):
    def test_parse(self):
        p = helpers.tmpdir() / "a.srt"
        p.write_text("\ufeff1\n00:00:01,000 --> 00:00:03,500\nسلام\nدنیا\n\n2\n00:01:02,250 --> 00:01:04,000\nHello\n", encoding="utf-8")
        items = vt.parse_srt(p)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["text"], "سلام\nدنیا")
        self.assertAlmostEqual(items[0]["start"], 1.0)
        self.assertAlmostEqual(items[0]["end"], 3.5)
        self.assertAlmostEqual(items[1]["start"], 62.25)

    def test_crlf_and_blank_blocks(self):
        p = helpers.tmpdir() / "b.srt"
        p.write_bytes(b"1\r\n00:00:00,500 --> 00:00:01,500\r\nx\r\n\r\n\r\n2\r\n00:00:02,000 --> 00:00:03,000\r\ny\r\n")      # real CRLF bytes
        self.assertEqual([i["text"] for i in vt.parse_srt(p)], ["x", "y"])
        p.write_bytes(b"1\n00:00:00,500 --> 00:00:01,500\nx\n\n2\n00:00:02,000 --> 00:00:03,000\ny")                            # LF, no final newline
        self.assertEqual([i["text"] for i in vt.parse_srt(p)], ["x", "y"])
        p.write_bytes(b"1\r00:00:00,500 --> 00:00:01,500\rx\r\r2\r00:00:02,000 --> 00:00:03,000\ry\r")                      # old Mac CR
        self.assertEqual([i["text"] for i in vt.parse_srt(p)], ["x", "y"])


class Fonts(unittest.TestCase):
    def test_name_table_of_a_real_font(self):
        arial = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
        if not arial.is_file():
            self.skipTest("no arial.ttf on this machine")
        names = vt.read_font_names(arial)
        self.assertEqual(names["family"], "Arial")
        self.assertEqual(names["subfamily"], "Regular")

    def test_garbage_is_none(self):
        p = helpers.tmpdir() / "junk.ttf"
        p.write_bytes(b"not a font" * 20)
        self.assertIsNone(vt.read_font_names(p))
        self.assertIsNone(vt.read_font_names(helpers.tmpdir() / "missing.ttf"))

    def test_name_normalisation_matches_variants(self):
        self.assertEqual(vt._norm("IRANSans(FaNum)"), vt._norm("IRANSansFaNum"))
        self.assertEqual(vt._norm("Segoe UI"), "segoeui")

    def test_pick_font_always_returns_a_family(self):
        for lang in ("fa", "en"):
            f = vt.pick_font(lang)
            self.assertTrue(f["family"])
        f = vt.pick_font("fa", "No Such Font Family")
        self.assertTrue(f["family"])                                               # falls through the preference list

    def test_persian_preference_finds_an_installed_persian_capable_font(self):
        f = vt.pick_font("fa", None, True)
        if f["file"] is None:
            self.skipTest("no Persian-capable font installed")
        self.assertIn(f["family"], vt.PERSIAN_FONT_PREFERENCE + [vt.pick_font("fa")["family"]])


class Validation(unittest.TestCase):
    def check(self, texts, duration=10.0):
        return {i["code"] for i in vt.validate({"project": {"duration": duration}, "texts": texts})}

    def test_valid(self):
        self.assertEqual(vt.validate({"project": {"duration": 10}, "texts": [{"text": FA, "start": 1, "end": 4}]}), [])
        self.assertEqual(vt.validate({"project": {"duration": 10}}), [])

    def test_errors_and_warnings(self):
        self.assertIn("text-empty", self.check([{"text": "  ", "start": 0, "end": 2}]))
        self.assertIn("text-time", self.check([{"text": "x"}]))
        self.assertIn("text-time", self.check([{"text": "x", "start": 3, "end": 1}]))
        self.assertIn("text-time", self.check([{"text": "x", "start": 8, "end": 12}]))
        self.assertIn("text-short", self.check([{"text": "x", "start": 1, "end": 1.4}]))
        self.assertIn("text-position", self.check([{"text": "x", "start": 0, "end": 2, "position": "left"}]))
        self.assertIn("text-style", self.check([{"text": "x", "start": 0, "end": 2, "style": "huge"}]))
        self.assertIn("text-animation", self.check([{"text": "x", "start": 0, "end": 2, "animation": "spin"}]))
        self.assertIn("text-lang", self.check([{"text": "x", "start": 0, "end": 2, "lang": "de"}]))
        self.assertIn("text-custom", self.check([{"text": "x", "start": 0, "end": 2, "position": "custom"}]))
        self.assertIn("text-color", self.check([{"text": "x", "start": 0, "end": 2, "color": "red"}]))
        self.assertIn("text-font", self.check([{"text": "x", "start": 0, "end": 2, "font_file": "nope.ttf"}]))
        self.assertIn("text-size", self.check([{"text": "x", "start": 0, "end": 2, "size": 5}]))
        self.assertIn("texts", {i["code"] for i in vt.validate({"texts": "x"})})

    def test_overload(self):
        many = [{"text": f"t{i}", "start": i * 2, "end": i * 2 + 2} for i in range(5)]
        self.assertIn("text-overload", self.check(many, 10.0))
        self.assertNotIn("text-overload", self.check(many[:2], 10.0))

    def test_safe_area_warnings(self):
        e = {"texts": [{"text": "x", "start": 0, "end": 2, "position": "custom", "x": 0.5, "y": 0.02},
                       {"text": "y", "start": 0, "end": 2, "position": "custom", "x": 0.5, "y": 0.5},
                       {"text": "z", "start": 0, "end": 2, "size": 400}]}
        codes = [i["code"] for i in vt.safe_issues(e, 1080, 1920)]
        self.assertEqual(codes.count("text-safe-area"), 1)
        self.assertEqual(codes.count("text-size"), 1)

    def test_linter_reaches_text_rules(self):
        edl = {"version": 1, "project": {"width": 90, "height": 160, "fps": 30, "duration": 4.0}, "style": {}, "transitions": {},
               "shots": [{"clip": "a.mp4", "span": [0, 4.0]}], "texts": [{"text": "x", "start": 3, "end": 1}]}
        self.assertIn("text-time", {i["code"] for i in ve.lint(edl, check_files=False)})


class Ass(unittest.TestCase):
    def build(self, texts, w=540, h=960, name="a"):
        work = helpers.tmpdir() / f"ass_{name}"
        ass, fonts = vt.build_ass({"project": {"duration": 6.0}, "texts": texts}, w, h, work)
        return ass, fonts, ass.read_text(encoding="utf-8-sig")

    def test_no_texts_no_file(self):
        self.assertEqual(vt.build_ass({"texts": []}, 540, 960, helpers.tmpdir() / "none"), (None, None))

    def test_resolution_styles_and_events(self):
        ass, fonts, text = self.build([{"text": FA, "start": 0.5, "end": 3.0}, {"text": "Maranjab", "start": 1, "end": 4, "position": "bottom"}])
        self.assertIn("PlayResX: 540", text)
        self.assertIn("PlayResY: 960", text)
        self.assertEqual(text.count("Dialogue:"), 2)
        self.assertEqual(text.count("Style: S"), 2)
        self.assertTrue(ass.read_bytes().startswith(b"\xef\xbb\xbf"))                      # UTF-8 with BOM, as libass expects

    def test_persian_gets_rtl_embeddings_and_digits(self):
        _, _, text = self.build([{"text": "مهر 1405", "start": 0, "end": 3}])
        self.assertIn("\u202b", text)
        self.assertIn("\u202c", text)
        self.assertIn("۱۴۰۵", text)
        self.assertNotIn("1405", text)

    def test_english_gets_no_embeddings_and_digits_kept(self):
        _, _, text = self.build([{"text": "Oct 2026", "start": 0, "end": 3}])
        self.assertNotIn("\u202b", text)
        self.assertIn("2026", text)

    def test_digits_option(self):
        _, _, t1 = self.build([{"text": "مهر 1405", "start": 0, "end": 3, "digits": "en"}], name="d1")
        self.assertIn("1405", t1)
        _, _, t2 = self.build([{"text": "Oct 2026", "start": 0, "end": 3, "digits": "fa"}], name="d2")
        self.assertIn("۲۰۲۶", t2)

    def test_every_line_of_a_wrapped_rtl_text_is_an_rtl_paragraph(self):
        _, _, text = self.build([{"text": FA_LONG, "start": 0, "end": 3, "style": "caption"}])
        line = [l for l in text.splitlines() if l.startswith("Dialogue")][0]
        self.assertGreaterEqual(line.count("\\N"), 1)
        self.assertEqual(line.count("\u202b"), line.count("\u202c"))

    def test_positions(self):
        _, _, text = self.build([{"text": "a", "start": 0, "end": 3, "position": p} for p in ("title", "center", "top", "bottom", "lower-third")])
        ys = [int(l.split("\\pos(")[1].split(",")[1].split(")")[0]) for l in text.splitlines() if l.startswith("Dialogue")]
        self.assertEqual(sorted(ys), ys[:2] and sorted(ys))
        title, center, top, bottom, lower = ys
        self.assertLess(top, title)
        self.assertLess(title, center)
        self.assertLess(center, lower)
        self.assertLess(lower, bottom)
        self.assertLessEqual(bottom, 960 * 0.80)                                          # above the bottom UI zone
        self.assertGreaterEqual(top, 960 * 0.12)

    def test_animations(self):
        for anim, tag in (("fade", "\\fad(300,300)"), ("pop", "\\t(0,220"), ("slide-up", "\\move("), ("none", "\\pos(")):
            _, _, text = self.build([{"text": "a", "start": 0, "end": 3, "animation": anim}], name="an_" + anim)
            self.assertIn(tag, text, anim)
        _, _, none = self.build([{"text": "a", "start": 0, "end": 3, "animation": "none"}], name="an_n2")
        self.assertNotIn("\\fad", none)

    def test_box_style_and_colours(self):
        _, _, text = self.build([{"text": "a", "start": 0, "end": 3, "box": True, "color": "#FFD700", "outline": "#112233"}])
        style = [l for l in text.splitlines() if l.startswith("Style: S0")][0]
        self.assertIn(",3,", style)                                                       # BorderStyle 3 = opaque box
        self.assertIn("&H0000D7FF", style)                                                # gold, BGR order
        self.assertIn("&H00332211", style)

    def test_braces_in_text_are_escaped(self):
        _, _, text = self.build([{"text": "a {b} c", "start": 0, "end": 3}])
        self.assertIn("\\{b\\}", text)

    def test_fonts_are_copied_next_to_the_ass(self):
        ass, fonts, _ = self.build([{"text": FA, "start": 0, "end": 3}], name="fonts")
        f = vt.pick_font("fa", None, True)
        if f["file"]:
            self.assertTrue((fonts / Path(f["file"]).name).is_file())

    def test_font_file_overrides_the_family(self):
        arial = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
        if not arial.is_file():
            self.skipTest("no arial.ttf")
        _, fonts, text = self.build([{"text": "hello", "start": 0, "end": 3, "font_file": str(arial)}], name="ff")
        self.assertIn("Style: S0,Arial,", text)
        self.assertTrue((fonts / "arial.ttf").is_file())

    def test_filter_string(self):
        ass, fonts, _ = self.build([{"text": "a", "start": 0, "end": 3}], name="flt")
        f = vt.text_filter(ass, fonts)
        self.assertTrue(f.startswith("ass=filename='"))
        self.assertIn("fontsdir='", f)


def ink(png_gray: bytes, w: int, h: int, threshold: int = 150):
    """Bounding box (x0, y0, x1, y1) of bright pixels, or None."""
    xs, ys = [], []
    for y in range(h):
        row = png_gray[y * w:(y + 1) * w]
        for x, v in enumerate(row):
            if v >= threshold:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def ink_count(buf: bytes, threshold: int = 150) -> int:
    return sum(1 for v in buf if v >= threshold)


@NEEDS_FF
@helpers.requires_filters("ass")
class Rendering(unittest.TestCase):
    """Real libass renders over a flat dark frame; text is white, so ink = bright pixels."""
    W, H = 540, 960

    def frame(self, texts, at=1.5, name="r", w=None, h=None):
        w, h = w or self.W, h or self.H
        work = helpers.tmpdir() / f"rend_{name}"
        ass, fonts = vt.build_ass({"project": {"duration": 6.0}, "texts": texts}, w, h, work)
        vf = f"color=c=0x202020:s={w}x{h}:r=30:d=6,{vt.text_filter(ass, fonts)}"
        r = subprocess.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", vf, "-ss", f"{at}", "-frames:v", "1",
                            "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))
        return r.stdout

    def test_persian_title_renders_and_is_centred(self):
        buf = self.frame([{"text": FA, "start": 0.2, "end": 5.8, "animation": "none"}], name="title")
        box = ink(buf, self.W, self.H)
        self.assertIsNotNone(box)
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        self.assertAlmostEqual(cx, self.W / 2, delta=self.W * 0.04)
        self.assertAlmostEqual(cy, self.H * 0.30, delta=self.H * 0.04)
        self.assertGreater(box[2] - box[0], self.W * 0.2)

    def test_nothing_before_start_or_after_end(self):
        t = [{"text": FA, "start": 2.0, "end": 4.0, "animation": "none"}]
        self.assertEqual(ink_count(self.frame(t, at=1.0, name="early")), 0)
        self.assertGreater(ink_count(self.frame(t, at=3.0, name="mid")), 100)
        self.assertEqual(ink_count(self.frame(t, at=5.0, name="late")), 0)

    def test_fade_in_is_gradual(self):
        t = [{"text": FA, "start": 1.0, "end": 5.0, "animation": "fade"}]
        early = ink_count(self.frame(t, at=1.05, name="f1"), 100)
        mid = ink_count(self.frame(t, at=3.0, name="f2"), 100)
        self.assertLess(early, mid)

    def test_positions_respect_the_safe_zones(self):
        safe = vt.SAFE_VERTICAL
        for pos in ("top", "bottom", "title", "center", "lower-third"):
            buf = self.frame([{"text": FA, "start": 0, "end": 5, "position": pos, "animation": "none"}], name="p_" + pos)
            box = ink(buf, self.W, self.H)
            self.assertIsNotNone(box, pos)
            self.assertGreaterEqual(box[1], self.H * safe["top"] - 2, pos)
            self.assertLessEqual(box[3], self.H * (1 - safe["bottom"]) + 2, pos)
            self.assertGreaterEqual(box[0], self.W * safe["side"] - 2, pos)
            self.assertLessEqual(box[2], self.W * (1 - safe["side"]) + 2, pos)

    def test_lower_third_aligns_with_the_reading_direction(self):
        fa = ink(self.frame([{"text": "آفرود در دل ماسه", "start": 0, "end": 5, "position": "lower-third", "animation": "none"}], name="lt_fa"), self.W, self.H)
        en = ink(self.frame([{"text": "Off-road in the dunes", "start": 0, "end": 5, "position": "lower-third", "animation": "none"}], name="lt_en"), self.W, self.H)
        self.assertGreater(fa[0], self.W / 2 - 80)                                       # Persian block hugs the right side
        self.assertLess(en[0], self.W * 0.15)                                            # English block hugs the left side

    def test_long_persian_wraps_into_two_lines_inside_the_frame(self):
        one = ink(self.frame([{"text": FA, "start": 0, "end": 5, "style": "caption", "animation": "none"}], name="w1"), self.W, self.H)
        two = ink(self.frame([{"text": FA_LONG, "start": 0, "end": 5, "style": "caption", "animation": "none"}], name="w2"), self.W, self.H)
        self.assertGreater(two[3] - two[1], (one[3] - one[1]) * 1.6)                     # taller: more than one line
        self.assertLessEqual(two[2] - two[0], self.W * 0.9)                              # but never wider than the frame

    def test_persian_letters_are_joined_not_isolated(self):
        """A word written with joined forms is narrower than the same letters forced apart by ZWNJ (isolated forms)."""
        joined = ink(self.frame([{"text": "سلام", "start": 0, "end": 5, "animation": "none", "size": 90}], name="j1", w=540, h=960), self.W, self.H)
        spaced = ink(self.frame([{"text": "س\u200cل\u200cا\u200cم", "start": 0, "end": 5, "animation": "none", "size": 90}], name="j2"), self.W, self.H)
        self.assertLess(joined[2] - joined[0], spaced[2] - spaced[0])

    def test_rtl_order_puts_the_first_letter_on_the_right(self):
        """'اب' (alef, beh) and 'با' (beh, alef) differ only in order. The alef is a tall thin stroke, so the end of the word where it
        sits reaches higher. Right-to-left puts the alef of the first word at the RIGHT end and of the second word at the LEFT end."""
        def edge_tops(text, name):
            buf = self.frame([{"text": text, "start": 0, "end": 5, "animation": "none", "size": 170}], name=name)
            box = ink(buf, self.W, self.H)
            span = (box[2] - box[0]) * 0.2

            def top(x0, x1):
                return min(y for y in range(box[1], box[3] + 1) for x in range(int(x0), int(x1) + 1) if buf[y * self.W + x] >= 150)
            return top(box[0], box[0] + span), top(box[2] - span, box[2])             # (left edge top, right edge top)
        l1, r1 = edge_tops("اب", "rtl1")
        l2, r2 = edge_tops("با", "rtl2")
        self.assertLess(r1, l1)                                                         # alef (tall) at the right in the first word
        self.assertLess(l2, r2)                                                         # alef (tall) at the left in the second word

    def test_mixed_scripts_render(self):
        buf = self.frame([{"text": "Maranjab کویر 2026", "start": 0, "end": 5, "animation": "none"}], name="mixed")
        self.assertGreater(ink_count(buf), 200)

    def test_box_and_outline_styles_render(self):
        buf = self.frame([{"text": FA, "start": 0, "end": 5, "box": True, "animation": "none"}], name="box")
        self.assertGreater(ink_count(buf), 100)

    def test_unknown_font_name_falls_back_instead_of_failing(self):
        buf = self.frame([{"text": FA, "start": 0, "end": 5, "font": "Totally Unknown Font", "animation": "none"}], name="unk")
        self.assertGreater(ink_count(buf), 100)

    def test_landscape_frame(self):
        buf = self.frame([{"text": FA, "start": 0, "end": 5, "animation": "none"}], name="land", w=960, h=540)
        box = ink(buf, 960, 540)
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box[1], 540 * vt.SAFE_LANDSCAPE["top"] - 2)

    def test_cli_fonts_and_import(self):
        self.assertEqual(mh_video.main(["text", "fonts"]), 0)
        srt = helpers.tmpdir() / "cli.srt"
        srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nسلام\n\n2\n00:00:02,500 --> 00:00:04,000\nHi\n", encoding="utf-8")
        edl = {"version": 1, "project": {"width": 270, "height": 480, "fps": 30, "duration": 5.0}, "style": {}, "transitions": {},
               "shots": [{"clip": "a.mp4", "span": [0, 5.0]}]}
        p = helpers.tmpdir() / "cli_text.edl.json"
        vl.save_json(p, edl)
        self.assertEqual(mh_video.main(["text", "import-srt", str(p), str(srt), "--position", "bottom"]), 0)
        data = vl.load_json(p)
        self.assertEqual(len(data["texts"]), 2)
        self.assertEqual(data["texts"][0]["position"], "bottom")
        self.assertEqual(ve.lint(data, check_files=False), [])
        out = helpers.tmpdir() / "cli_text.png"
        self.assertEqual(mh_video.main(["text", "preview", str(p), "--out", str(out), "--at", "1.0"]), 0)
        self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()
