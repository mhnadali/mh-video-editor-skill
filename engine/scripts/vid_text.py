"""Phase 4: on-screen text, with first-class Persian (right-to-left, joined letters, Persian digits).

Text is rendered by libass (the `ass` filter: HarfBuzz shaping + FriBidi bidi), from an ASS file this module writes
from the EDL's `texts` list. Fonts are located by reading the `name` table of real font files (so a family such as
"IRANSans(FaNum)" is found exactly) and the chosen files are copied into the work folder and handed to libass with
`fontsdir`, which makes the result independent of how the OS font service behaves.

EDL:
  "texts": [{"text": "کویر مرنجاب", "start": 0.4, "end": 3.0,
             "position": "title",        # title | center | top | bottom | lower-third | custom (x, y in 0..1)
             "style": "title",           # title | caption | subtitle (size and weight presets)
             "lang": "auto",             # auto | fa | en
             "animation": "fade",        # fade | pop | slide-up | none
             "digits": "auto",           # auto (Persian digits in Persian text) | fa | en
             "size": null, "color": "#FFFFFF", "outline": "#000000", "outline_width": null, "shadow": 2,
             "box": false, "font": null, "font_file": null, "align": null}]
  "style": {"text": {"font": null, "fontsdir": null}}      # optional defaults

Positions respect the platform safe areas of the format (Reels/TikTok/Shorts cover the top 12% and the bottom 20%).
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import unicodedata
from pathlib import Path

import vidlib as vl

POSITIONS = ("title", "center", "top", "bottom", "lower-third", "custom")
STYLES = ("title", "caption", "subtitle")
ANIMATIONS = ("fade", "pop", "slide-up", "none")
LANGS = ("auto", "fa", "en")
DIGITS = ("auto", "fa", "en")
ALIGNS = ("center", "left", "right")

# Reusable text presets (phase 10): a `template` on a text fills in position/style/animation defaults, so a caption or
# lower-third is one word instead of three fields. Any explicit field on the text overrides the template.
TEXT_TEMPLATES: dict[str, dict] = {
    "title-card":  {"position": "title", "style": "title", "animation": "fade"},
    "lower-third": {"position": "lower-third", "style": "caption", "animation": "slide-up"},
    "caption":     {"position": "bottom", "style": "caption", "animation": "fade"},
    "subtitle":    {"position": "bottom", "style": "subtitle", "animation": "none"},
    "location":    {"position": "top", "style": "caption", "animation": "fade"},
    "end-card":    {"position": "center", "style": "title", "animation": "pop"},
}


def apply_template(t: dict) -> dict:
    """Expand a text's `template` into concrete defaults (explicit fields win). Unknown templates are left for validate()."""
    name = t.get("template")
    base = TEXT_TEMPLATES.get(name)
    if not base:
        return t
    merged = dict(base)
    merged.update({k: v for k, v in t.items() if k != "template"})
    return merged


def list_templates() -> list[tuple[str, str]]:
    return [(k, f"{v['position']} / {v['style']} / {v['animation']}") for k, v in TEXT_TEMPLATES.items()]

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹")

# fraction of the frame that platform UI covers (top, bottom, left/right) by aspect ratio
SAFE_VERTICAL = {"top": 0.12, "bottom": 0.20, "side": 0.07}
SAFE_LANDSCAPE = {"top": 0.07, "bottom": 0.10, "side": 0.05}
SAFE_SQUARE = {"top": 0.08, "bottom": 0.12, "side": 0.06}

# (size as a fraction of the frame height, bold, outline fraction of size)
STYLE_PRESETS = {"title": (0.058, True, 0.06), "caption": (0.036, True, 0.08), "subtitle": (0.030, False, 0.09)}

PERSIAN_FONT_PREFERENCE = ["IRANSansFaNum", "IRANSans", "Vazirmatn", "Vazir", "Sahel", "Samim", "B Nazanin", "Dubai",
                           "Tahoma", "Segoe UI", "Arial"]
LATIN_FONT_PREFERENCE = ["Segoe UI", "Arial", "Tahoma", "Calibri", "Noto Sans", "DejaVu Sans"]


# --------------------------------------------------------------------------- fonts

def _font_dirs() -> list[Path]:
    dirs = []
    if os.name == "nt":
        win = os.environ.get("WINDIR", "C:/Windows")
        dirs.append(Path(win) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts", Path("/Library/Fonts"),
                 Path("/System/Library/Fonts")]
    extra = os.environ.get("MH_FONT_DIR")
    if extra:
        dirs.insert(0, Path(extra))
    return [d for d in dirs if d.is_dir()]


def read_font_names(path: str | os.PathLike) -> dict | None:
    """Family / subfamily / full name of a TrueType or OpenType file (name table, ids 1, 2, 4, 16, 17), or None."""
    try:
        data = Path(path).read_bytes()
        if data[:4] == b"ttcf":                                   # collection: use the first font
            off = struct.unpack(">I", data[12:16])[0]
        else:
            off = 0
        num = struct.unpack(">H", data[off + 4:off + 6])[0]
        table = None
        for i in range(num):
            rec = data[off + 12 + 16 * i: off + 28 + 16 * i]
            if rec[:4] == b"name":
                table = struct.unpack(">II", rec[8:16])
                break
        if not table:
            return None
        base, _length = table
        count, string_off = struct.unpack(">HH", data[base + 2:base + 6])
        names: dict[int, str] = {}
        for i in range(count):
            pid, eid, lid, nid, ln, so = struct.unpack(">HHHHHH", data[base + 6 + 12 * i: base + 18 + 12 * i])
            raw = data[base + string_off + so: base + string_off + so + ln]
            try:
                text = raw.decode("utf-16-be") if pid in (0, 3) else raw.decode("latin-1")
            except UnicodeDecodeError:
                continue
            if nid in (1, 2, 4, 16, 17) and text and (nid not in names or (pid == 3 and lid == 0x409)):
                names[nid] = text
        return {"family": names.get(16) or names.get(1, ""), "subfamily": names.get(17) or names.get(2, ""),
                "full": names.get(4, ""), "legacy_family": names.get(1, "")}
    except (OSError, struct.error, IndexError):
        return None


_FONT_INDEX: list[dict] | None = None


def font_index(refresh: bool = False) -> list[dict]:
    """All installed fonts: [{'path', 'family', 'subfamily', 'full'}] (cached; only .ttf/.otf/.ttc are read)."""
    global _FONT_INDEX
    if _FONT_INDEX is not None and not refresh:
        return _FONT_INDEX
    out = []
    for d in _font_dirs():
        for p in d.rglob("*"):
            if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
                names = read_font_names(p)
                if names and names["family"]:
                    out.append({"path": str(p), **names})
    _FONT_INDEX = out
    return out


def _norm(name: str) -> str:
    """'IRANSans(FaNum)' and 'IRANSansFaNum' name the same family: compare letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_font(family: str, bold: bool = False) -> dict | None:
    """Best installed font for `family` (case-insensitive exact match on family or legacy family), regular or bold."""
    want = _norm(family)
    cands = [f for f in font_index() if _norm(f["family"]) == want or _norm(f["legacy_family"]) == want]
    if not cands:
        return None
    def score(f):
        sub = f["subfamily"].lower()
        is_bold = "bold" in sub and "semi" not in sub and "extra" not in sub
        is_plain = sub in ("regular", "normal", "")
        return (0 if (is_bold if bold else is_plain) else 1, len(sub))
    return sorted(cands, key=score)[0]


def pick_font(lang: str, requested: str | None = None, bold: bool = False) -> dict:
    """{'family': ASS font name, 'file': path or None}. Preference list for Persian starts with IRANSans (FaNum)."""
    order = ([requested] if requested else []) + (PERSIAN_FONT_PREFERENCE if lang == "fa" else LATIN_FONT_PREFERENCE)
    for fam in order:
        f = find_font(fam, bold)
        if f:
            return {"family": f["family"], "file": f["path"], "bold_native": "bold" in f["subfamily"].lower()}
    return {"family": requested or ("Tahoma" if lang == "fa" else "Arial"), "file": None, "bold_native": False}


# --------------------------------------------------------------------------- text preparation

def is_persian(text: str) -> bool:
    """True if the first strong letter is Arabic-script (Persian, Arabic, Urdu): the paragraph is right-to-left."""
    for ch in text:
        if ch.isalpha():
            return unicodedata.bidirectional(ch) in ("R", "AL")
    return False


def has_rtl(text: str) -> bool:
    return any(unicodedata.bidirectional(c) in ("R", "AL") for c in text)


def normalise_persian(text: str) -> str:
    """Arabic yeh/kaf -> Persian yeh/kaf, Arabic-Indic digits -> Persian digits, ZWNJ kept, stray spaces collapsed."""
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ى", "ی")
    text = text.translate(ARABIC_INDIC)
    return re.sub(r"[ \t]+", " ", text).strip()


def to_persian_digits(text: str) -> str:
    return text.translate(PERSIAN_DIGITS)


def wrap_text(text: str, max_chars: int) -> list[str]:
    """Greedy word wrap into balanced lines (existing newlines are kept)."""
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        if len(para) <= max_chars or len(words) == 1:
            lines.append(para)
            continue
        n_lines = -(-len(para) // max_chars)
        lines += _balanced(words, n_lines)
    return lines


def _balanced(words: list[str], n_lines: int) -> list[str]:
    """Split words into n_lines lines with the least ragged right edge (minimum squared slack), by dynamic programming."""
    n = len(words)
    lens = [len(w) for w in words]
    total = sum(lens) + n - 1
    ideal = total / n_lines
    INF = float("inf")

    def width(i, j):                       # words[i:j] joined
        return sum(lens[i:j]) + (j - i - 1)

    best = [[INF] * (n + 1) for _ in range(n_lines + 1)]
    cut = [[0] * (n + 1) for _ in range(n_lines + 1)]
    best[0][0] = 0.0
    for k in range(1, n_lines + 1):
        for j in range(k, n + 1):
            for i in range(k - 1, j):
                if best[k - 1][i] == INF:
                    continue
                cost = best[k - 1][i] + (width(i, j) - ideal) ** 2
                if cost < best[k][j]:
                    best[k][j], cut[k][j] = cost, i
    if best[n_lines][n] == INF:
        return [" ".join(words)]
    out, j = [], n
    for k in range(n_lines, 0, -1):
        i = cut[k][j]
        out.append(" ".join(words[i:j]))
        j = i
    return list(reversed(out))


def ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def ass_time(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS '&HAABBGGRR' (alpha 0 opaque .. 255 transparent)."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", hex_color or "")
    if not m:
        raise vl.EngineError(f"colour '{hex_color}' is not #RRGGBB")
    rr, gg, bb = m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6]
    return f"&H{alpha:02X}{bb}{gg}{rr}".upper().replace("&H", "&H")


def safe_area(width: int, height: int) -> dict:
    r = width / height
    return SAFE_VERTICAL if r < 0.8 else SAFE_SQUARE if r < 1.2 else SAFE_LANDSCAPE


# --------------------------------------------------------------------------- SRT import

def parse_srt(path: str | os.PathLike) -> list[dict]:
    """SRT file -> list of {'text', 'start', 'end'} (UTF-8, with or without BOM)."""
    raw = Path(path).read_bytes().decode("utf-8-sig")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")                      # CRLF, lone CR and doubled CR all become LF
    out = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = block.split("\n")
        m = None
        for i, ln in enumerate(lines[:3]):
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", ln.strip())
            if m:
                text = "\n".join(lines[i + 1:]).strip()
                a = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4].ljust(3, "0")[:3]) / 1000
                b = int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7]) + int(m[8].ljust(3, "0")[:3]) / 1000
                if text:
                    out.append({"text": text, "start": a, "end": b})
                break
    return out


# --------------------------------------------------------------------------- validation (used by the EDL linter)

def validate(edl: dict) -> list[dict]:
    texts = edl.get("texts")
    if texts is None:
        return []
    out: list[dict] = []

    def add(level, code, msg):
        out.append({"level": level, "code": code, "msg": msg})

    dur = float((edl.get("project") or {}).get("duration") or 0)
    if not isinstance(texts, list):
        return [{"level": "error", "code": "texts", "msg": "texts must be a list"}]
    on_screen = 0.0
    for i, t0 in enumerate(texts):
        where = f"text {i}"
        if isinstance(t0, dict) and t0.get("template") is not None and t0["template"] not in TEXT_TEMPLATES:
            add("error", "text-template", f"{where}: template '{t0['template']}' is unknown ({', '.join(TEXT_TEMPLATES)})")
            continue
        t = apply_template(t0) if isinstance(t0, dict) else t0
        if not isinstance(t, dict) or not str(t.get("text", "")).strip():
            add("error", "text-empty", f"{where}: needs non-empty 'text'")
            continue
        try:
            a, b = float(t["start"]), float(t["end"])
        except (KeyError, TypeError, ValueError):
            add("error", "text-time", f"{where}: needs numeric start and end (seconds)")
            continue
        if not (0 <= a < b):
            add("error", "text-time", f"{where}: start must be >= 0 and before end")
        elif dur and b > dur + 0.01:
            add("warn", "text-time", f"{where}: ends at {b:.2f}s after the video ({dur:.2f}s)")
        elif b - a < 0.8:
            add("warn", "text-short", f"{where}: only {b - a:.1f}s on screen: too short to read")
        on_screen += max(0.0, b - a)
        for key, allowed in (("position", POSITIONS), ("style", STYLES), ("animation", ANIMATIONS), ("lang", LANGS),
                             ("digits", DIGITS), ("align", ALIGNS)):
            v = t.get(key)
            if v is not None and v not in allowed:
                add("error", f"text-{key}", f"{where}: {key} '{v}' is unknown ({', '.join(allowed)})")
        if t.get("position") == "custom":
            xy = (t.get("x"), t.get("y"))
            if not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in xy):
                add("error", "text-custom", f"{where}: position 'custom' needs x and y between 0 and 1")
        for key in ("color", "outline"):
            if t.get(key) is not None:
                try:
                    ass_color(t[key])
                except vl.EngineError as e:
                    add("error", "text-color", f"{where}: {e}")
        if t.get("font_file") and not os.path.isfile(t["font_file"]):
            add("error", "text-font", f"{where}: font file not found: {t['font_file']}")
        if t.get("size") is not None and not (10 <= float(t["size"]) <= 400):
            add("error", "text-size", f"{where}: size {t['size']} is outside 10-400 (pixels at the output height)")
    if dur and on_screen > 0.6 * dur and len(texts) > 3:
        add("warn", "text-overload", f"text is on screen {on_screen / dur:.0%} of the time: a reel reads better with less")
    return out


def safe_issues(edl: dict, width: int, height: int) -> list[dict]:
    """Warnings for text that would sit under platform UI (custom positions and oversized fonts)."""
    out = []
    safe = safe_area(width, height)
    for i, t in enumerate(edl.get("texts") or []):
        if t.get("position") == "custom" and isinstance(t.get("x"), (int, float)) and isinstance(t.get("y"), (int, float)):
            if t["y"] < safe["top"] or t["y"] > 1 - safe["bottom"] or t["x"] < safe["side"] or t["x"] > 1 - safe["side"]:
                out.append({"level": "warn", "code": "text-safe-area",
                            "msg": f"text {i}: custom position ({t['x']:.2f}, {t['y']:.2f}) is inside the platform UI zone "
                                   f"(keep y between {safe['top']:.2f} and {1 - safe['bottom']:.2f})"})
        size = float(t.get("size") or STYLE_PRESETS[t.get("style", "title")][0] * height)
        if size > 0.12 * height:
            out.append({"level": "warn", "code": "text-size", "msg": f"text {i}: font {size:.0f}px is more than 12% of the frame height"})
    return out


# --------------------------------------------------------------------------- ASS generation

def build_ass(edl: dict, width: int, height: int, work: str | os.PathLike) -> tuple[Path | None, Path | None]:
    """Write <work>/texts.ass and copy the fonts it needs to <work>/fonts. Returns (ass path, fonts dir) or (None, None)."""
    texts = edl.get("texts") or []
    if not texts:
        return None, None
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    fonts_dir = work / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    default = ((edl.get("style") or {}).get("text") or {})
    safe = safe_area(width, height)
    styles: dict[str, str] = {}
    events: list[str] = []
    for i, t in enumerate(texts):
        t = apply_template(t)
        raw = str(t["text"])
        lang = t.get("lang", "auto")
        if lang == "auto":
            lang = "fa" if is_persian(raw) else "en"
        digits = t.get("digits", "auto")
        txt = normalise_persian(raw) if lang == "fa" else raw
        if digits == "fa" or (digits == "auto" and lang == "fa"):
            txt = to_persian_digits(txt)
        kind = t.get("style", "title" if (t.get("position") in ("title", None)) else "caption")
        frac, bold, outline_frac = STYLE_PRESETS[kind]
        size = float(t.get("size") or round(frac * height))
        font = pick_font(lang, t.get("font") or default.get("font"), bold)
        if t.get("font_file"):
            names = read_font_names(t["font_file"]) or {}
            font = {"family": names.get("family") or Path(t["font_file"]).stem, "file": t["font_file"],
                    "bold_native": "bold" in (names.get("subfamily") or "").lower()}
        if font["file"]:
            dst = fonts_dir / Path(font["file"]).name
            if not dst.exists():
                shutil.copy2(font["file"], dst)
        ass_bold = -1 if (bold and not font.get("bold_native")) else 0
        outline_w = float(t["outline_width"]) if t.get("outline_width") is not None else round(size * outline_frac, 1)
        shadow = float(t.get("shadow", 2))
        box = bool(t.get("box"))
        primary = ass_color(t.get("color", "#FFFFFF"))
        outline = ass_color(t.get("outline", "#000000"))
        back = ass_color(t.get("box_color", "#000000"), 96 if box else 255)
        sname = f"S{i}"
        border_style = 3 if box else 1
        styles[sname] = (f"Style: {sname},{font['family']},{size:g},{primary},{primary},{outline},{back},{ass_bold},0,0,0,100,100,0,0,"
                         f"{border_style},{outline_w:g},{shadow:g},5,0,0,0,1")
        rtl = lang == "fa" or has_rtl(txt)
        align = t.get("align") or "center"
        position = t.get("position") or "title"
        max_chars = max(12, int((width * (1 - 2 * safe["side"])) / (size * 0.52)))
        lines = wrap_text(txt, max_chars)
        body = r"\N".join(ass_escape(ln) for ln in lines)
        if rtl:
            body = "\u202b" + body.replace(r"\N", "\u202c\\N\u202b") + "\u202c"   # keep every line a right-to-left paragraph
        left, right = width * safe["side"], width * (1 - safe["side"])
        top, bottom = height * safe["top"], height * (1 - safe["bottom"])
        if position == "custom":
            x, y = float(t["x"]) * width, float(t["y"]) * height
        else:
            x = (left + right) / 2 if align == "center" else (right if (align == "right") else left)
            y = {"title": height * 0.30, "center": height * 0.5, "top": top + size * 0.7, "bottom": bottom - size * 0.7,
                 "lower-third": height * 0.72}[position]
            if position == "lower-third" and t.get("align") is None:
                x = right if rtl else left
                align = "right" if rtl else "left"
        an = {"center": 5, "left": 4, "right": 6}[align]
        a, b = float(t["start"]), float(t["end"])
        anim = t.get("animation", "fade")
        tags = [rf"\an{an}"]
        if anim == "slide-up":
            tags.append(rf"\move({x:.0f},{y + size * 0.8:.0f},{x:.0f},{y:.0f},0,350)")
        else:
            tags.append(rf"\pos({x:.0f},{y:.0f})")
        if anim in ("fade", "slide-up"):
            tags.append(r"\fad(300,300)")
        elif anim == "pop":
            tags.append(r"\fscx82\fscy82\t(0,220,\fscx100\fscy100)\fad(120,200)")
        events.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},{sname},,0,0,0,,{{{''.join(tags)}}}{body}")
    header = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
             "[V4+ Styles]",
             "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
             "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding"]
    body_lines = header + list(styles.values()) + ["", "[Events]",
                                                  "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"] + events
    ass = work / "texts.ass"
    ass.write_text("\n".join(body_lines) + "\n", encoding="utf-8-sig")
    return ass, fonts_dir


def text_filter(ass: Path, fonts_dir: Path) -> str:
    return f"ass=filename='{vl.filter_escape_path(ass)}':fontsdir='{vl.filter_escape_path(fonts_dir)}'"
