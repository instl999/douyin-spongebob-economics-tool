"""Type: fonts, wrapping, stroked captions, keyword labels, speech bubbles.

Two details matter more than they look like they should.

Advance width, not bounding box. Laying out a mixed run - normal text with one
word recoloured - by measuring bounding boxes accumulates the left side bearing
of every segment and the highlight drifts out of place. `textlength` returns the
advance, which is what "where does the next glyph start" actually means.

A fixed line box. Line height comes from the font's own ascent and descent, not
from the height of the glyphs that happen to be on that line, so a line
containing a tall character does not sit differently from one that does not.
Without this, multi-line captions jitter as the text changes.
"""
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",      # Microsoft YaHei Bold - the closest
    r"C:\Windows\Fonts\simhei.ttf",      # SimHei
    r"C:\Windows\Fonts\msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
REGULAR_CANDIDATES = [r"C:\Windows\Fonts\msyh.ttc"] + BOLD_CANDIDATES

# The cards' brush lettering, shipped with the repository so every machine
# draws the same title. Ma Shan Zheng (马善政毛笔楷书) is a bold brush 楷书:
# calligraphic enough to read as the reference's brush title, plain enough to
# read in the second and a half the card is on screen, and it covers the whole
# of GB2312. SIL Open Font License - see assets/fonts/OFL.txt.
BRUSH_PATH = (Path(__file__).resolve().parent.parent / "assets" / "fonts"
              / "MaShanZheng-Regular.ttf")


def _first_existing(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


BOLD_PATH = _first_existing(BOLD_CANDIDATES)
REGULAR_PATH = _first_existing(REGULAR_CANDIDATES)


@lru_cache(maxsize=64)
def font(size, bold=True, face=None):
    """A font at `size`. `face="brush"` is the cards' brush lettering."""
    if face == "brush" and BRUSH_PATH.exists():
        try:
            return ImageFont.truetype(str(BRUSH_PATH), size)
        except OSError:
            pass
    path = BOLD_PATH if bold else REGULAR_PATH
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


@lru_cache(maxsize=8192)
def advance(text, size, bold=True, face=None):
    return ImageDraw.Draw(Image.new("L", (1, 1))).textlength(
        text, font=font(size, bold, face))


def line_height(size, bold=True, face=None):
    ascent, descent = font(size, bold, face).getmetrics()
    return ascent + descent


@lru_cache(maxsize=4096)
def _has_glyph(char, face):
    """Whether `face` draws `char` as itself rather than as its missing box."""
    probe = font(48, True, face)
    missing = np.asarray(probe.getmask("\U000f0000"))
    drawn = np.asarray(probe.getmask(char))
    return not (drawn.shape == missing.shape and (drawn == missing).all())


def covers(text, face):
    """True when `face` has every character of `text`.

    A brush font set in a rare character draws a box where the character
    should be, and on a title card that is the one word everybody reads. So
    the card checks first and falls back to the sans face whole, rather than
    setting a title in two alphabets.
    """
    if face != "brush" or not BRUSH_PATH.exists():
        return face != "brush"
    return all(_has_glyph(ch, face) for ch in text if not ch.isspace())


# Characters that may not open a line, and those that may not end one.
NO_LINE_START = set("\u3002\uff0c\u3001\uff01\uff1f\uff1b\uff1a\uff09\u3011\u300b\u300d\u300f\u201d\u2019%\u2026\u2014\u00b7!?,.:;)]}>")
NO_LINE_END = set("\uff08\u3010\u300a\u300c\u300e\u201c\u2018([{<")

# Where a Chinese line breaks well, in the absence of a word list. A line can
# end on punctuation, and it can end after a particle or a preposition - the
# words that close or open a phrase - or break before a conjunction. Anywhere
# else is a break inside what may be a word: greedy wrapping split 一下子 as
# 效率一 | 下子提了上来 and would split 海绵|宝宝 given the chance.
BREAK_AFTER_PUNCT = set("，、；：。！？,;:!?）)」』”…")
BREAK_AFTER_WORD = set("的了着过地得是在和与及或把被给让对向从往而但就都也又还吗呢吧啊")
BREAK_BEFORE_WORD = set("和与及或但而所因如虽即并就")


def _joined(a, b):
    """True when a break between `a` and `b` would split a word or a figure."""
    if a.isascii() and b.isascii():
        if a.isalnum() and b.isalnum():
            return True
        if (a.isdigit() and b in ".%") or (a == "." and b.isdigit()):
            return True
        if a in "'-" or b in "'-":
            return a.isalnum() or b.isalnum()
    return False


def _greedy(text, size, max_width, bold, face):
    """Greedy CJK-aware wrap. Latin words are kept whole."""
    lines, current = [], ""

    def width(s):
        return advance(s, size, bold, face)

    tokens, buf = [], ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "'-"):
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)

    def splits_word(line):
        """True if moving line's last character would break a Latin word.

        Moving one character between lines is harmless in CJK, where every
        character stands alone, and wrong in Latin script. Unguarded it turned
        "distant places." into "distant plac" / "es." in the English subtitle
        line. Only ASCII alphanumerics can be adjacent inside a word, so this
        never fires on Chinese.
        """
        return (len(line) >= 2
                and line[-1].isascii() and line[-1].isalnum()
                and line[-2].isascii() and line[-2].isalnum())

    for tok in tokens:
        candidate = current + tok
        if width(candidate) <= max_width or not current:
            current = candidate
            continue
        # Pull one character down if the next line would open on a closer, or
        # this line would end on an opener.
        if tok in NO_LINE_START and len(current) > 1 and not splits_word(current):
            lines.append(current[:-1])
            current = current[-1] + tok
        elif (current and current[-1] in NO_LINE_END and len(current) > 1
              and not splits_word(current)):
            lines.append(current[:-1])
            current = current[-1] + tok
        else:
            lines.append(current)
            current = tok
    if current:
        lines.append(current)
    lines = [ln.strip() for ln in lines if ln.strip()]

    # Orphan control. A last line holding one or two characters - the tail of a
    # sentence that just missed the previous line - reads as a mistake,
    # especially on a full-frame card where it sits alone under three full
    # lines. Pull characters back until it is not a stub, as long as the line
    # above can spare them.
    while len(lines) > 1 and len(lines[-1]) <= 2 and len(lines[-2]) > 3:
        moved = lines[-2][-1]
        if splits_word(lines[-2]):
            break                      # see splits_word: never break a word
        if advance(moved + lines[-1], size, bold, face) > max_width:
            break
        lines[-2], lines[-1] = lines[-2][:-1], moved + lines[-1]
    return lines


def clean_break(before, after):
    """Whether a line may end on `before` and the next open on `after` safely.

    Safe means at punctuation, after a particle or preposition, before a
    conjunction, or at a space - places a phrase ends. Anywhere else may be
    the middle of a word, which a dictionary could tell and this cannot.
    """
    return (before in BREAK_AFTER_PUNCT or before in BREAK_AFTER_WORD
            or after in BREAK_BEFORE_WORD or before == " " or after == " ")


def _two_lines(text, size, max_width, bold, face):
    """The best place to break `text` into two lines, or None.

    Best is a break where a phrase ends, and among those the most even pair.
    Punctuation outweighs a two- or three-character difference in length and
    a particle nearly as much: 于是他决定给 | 海绵宝宝多发一点工资 is a better
    pair of lines than the even 于是他决定给海绵 | 宝宝多发一点工资, which splits
    a name down the middle. Never a line of one or two characters, never
    inside a Latin word or a figure like 7.2%.
    """
    best, best_cost = None, None
    for p in range(1, len(text)):
        before, after = text[p - 1], text[p]
        if _joined(before, after) or after in NO_LINE_START \
                or before in NO_LINE_END:
            continue
        a, b = text[:p].rstrip(), text[p:].lstrip()
        if not a or not b:
            continue
        wa, wb = advance(a, size, bold, face), advance(b, size, bold, face)
        if wa > max_width or wb > max_width:
            continue
        cost = max(wa, wb)
        if before in BREAK_AFTER_PUNCT:
            cost -= 4.0 * size
        elif before in BREAK_AFTER_WORD:
            cost -= 2.5 * size
        elif after in BREAK_BEFORE_WORD:
            cost -= 2.0 * size
        elif before == " ":
            cost -= 1.0 * size
        if min(len(a), len(b)) <= 2:
            cost += 4.0 * size
        if best_cost is None or cost < best_cost:
            best, best_cost = [a, b], cost
    return best


def wrap(text, size, max_width, bold=True, face=None, balance=True):
    """CJK-aware wrap into lines no wider than `max_width`. Latin words are kept whole.

    Newlines are hard breaks: each paragraph wraps on its own. This is also a
    correctness guard, because PIL refuses to measure a string containing a
    newline at all - `advance` must never see one.

    `balance` evens the lines out. Greedy wrapping fills the first line and
    leaves the rest - a caption came out 效率一 | 下子提了上来 - so two lines are
    split at the best phrase boundary near the middle, and more than two are
    wrapped at the narrowest width that still needs that many lines.
    """
    text = (text or "").strip()
    if not text:
        return []
    if "\n" in text or "\r" in text:
        out = []
        for para in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            out.extend(wrap(para, size, max_width, bold, face, balance))
        return out
    lines = _greedy(text, size, max_width, bold, face)
    if not balance or len(lines) < 2:
        return lines
    if len(lines) == 2:
        return _two_lines(text, size, max_width, bold, face) or lines
    low, high = advance(text, size, bold, face) / len(lines), float(max_width)
    for _ in range(12):
        middle = (low + high) / 2
        if len(_greedy(text, size, middle, bold, face)) > len(lines):
            low = middle
        else:
            high = middle
    return _greedy(text, size, high, bold, face)


# How much to thicken the glyph itself, as a fraction of the type size.
# Windows ships nothing heavier than YaHei Bold, and the references are visibly
# heavier than that - closer to a Heavy weight. Stroking the fill in its own
# colour synthesises the missing weight without needing a font file installed,
# which matters because this is visible in every single frame of every video.
FAUX_WEIGHT = 0.016
MAX_FAUX_PIXELS = 2


def draw_runs(draw, x, y, runs, size, bold=True, stroke=0,
              stroke_fill=(0, 0, 0, 255), weight=FAUX_WEIGHT, face=None):
    """Draw [(text, colour), ...] left to right from x, returning the end x.

    Drawn in two passes: the outline first at its full width, then the fill
    stroked in its own colour. Doing it the other way round, or in one pass,
    would have the thickened fill eat into the outline and leave the black edge
    looking thin and patchy against a busy plate.
    """
    f = font(size, bold, face)
    extra = min(MAX_FAUX_PIXELS, max(0, round(size * weight))) if weight else 0
    for text, colour in runs:
        if not text:
            continue
        if stroke:
            draw.text((x, y), text, font=f, fill=colour,
                      stroke_width=stroke + extra, stroke_fill=stroke_fill)
        if extra:
            draw.text((x, y), text, font=f, fill=colour,
                      stroke_width=extra, stroke_fill=colour)
        else:
            draw.text((x, y), text, font=f, fill=colour)
        x += advance(text, size, bold, face)
    return x


def split_highlight(line, keyword):
    """Split one line into runs, marking occurrences of keyword."""
    if not keyword or keyword not in line:
        return [(line, False)]
    runs, rest = [], line
    while keyword and keyword in rest:
        head, _, rest = rest.partition(keyword)
        if head:
            runs.append((head, False))
        runs.append((keyword, True))
    if rest:
        runs.append((rest, False))
    return runs


def render_caption(size_wh, text, *, size, center_y, max_width, bold=True,
                   fill=(255, 255, 255, 255), stroke=None,
                   stroke_fill=(0, 0, 0, 255), highlight=None,
                   highlight_fill=(255, 210, 60, 255), align_bottom=False):
    """An RGBA overlay holding one caption. Returns (image, bbox) or (None, None)."""
    lines = wrap(text, size, max_width, bold)
    if not lines:
        return None, None
    if stroke is None:
        stroke = max(2, round(size * 0.085))

    lh = line_height(size, bold)
    gap = int(size * 0.20)
    total = lh * len(lines) + gap * (len(lines) - 1)
    top = center_y - total // 2 if not align_bottom else center_y - total

    layer = Image.new("RGBA", size_wh, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    W = size_wh[0]
    for i, line in enumerate(lines):
        runs = split_highlight(line, highlight)
        line_w = sum(advance(t, size, bold) for t, _ in runs)
        x = (W - line_w) / 2
        y = top + i * (lh + gap)
        coloured = [(t, highlight_fill if hl else fill) for t, hl in runs]
        draw_runs(draw, x, y, coloured, size, bold, stroke, stroke_fill)

    pad = stroke + 6
    bbox = (0, max(0, top - pad), W, min(size_wh[1], top + total + pad))
    return layer, bbox


def render_label(text, *, size, bold=True, fill=(30, 30, 30, 255),
                 stroke_fill=(255, 255, 255, 255), stroke=None, max_width=None):
    """A tight keyword label - dark text, white outline - as its own RGBA image."""
    if stroke is None:
        stroke = max(3, round(size * 0.14))
    lines = wrap(text, size, max_width or 10 ** 6, bold)
    if not lines:
        return None
    lh = line_height(size, bold)
    gap = int(size * 0.16)
    w = int(max(advance(l, size, bold) for l in lines)) + stroke * 2 + 8
    h = lh * len(lines) + gap * (len(lines) - 1) + stroke * 2 + 8
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        x = (w - advance(line, size, bold)) / 2
        draw.text((x, stroke + 4 + i * (lh + gap)), line, font=font(size, bold),
                  fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return img


# The colour of the words in a balloon, shared with the draft's text for them.
BUBBLE_INK = (20, 20, 20, 255)


def bubble_body(text, *, size, max_width, bold=False):
    """(lines, body width, body height, tail height) of a speech balloon.

    The body is the rounded box the words sit in; the tail hangs below it. The
    draft needs this to put editable words at the body's centre, which is not
    the centre of the image once the tail is added.
    """
    lines = wrap(text, size, max_width, bold)
    if not lines:
        return [], 0, 0, 0
    lh = line_height(size, bold)
    gap = int(size * 0.22)
    pad_x, pad_y = int(size * 0.75), int(size * 0.55)
    tw = int(max(advance(l, size, bold) for l in lines))
    th = lh * len(lines) + gap * (len(lines) - 1)
    return lines, tw + pad_x * 2, th + pad_y * 2, int(size * 0.85)


def render_bubble(text, *, size, max_width, tail="left", bold=False,
                  fill=(255, 255, 255, 245), outline=(20, 20, 20, 255),
                  words=True):
    """A rounded speech balloon sized to its text, with a tail on one side.

    `words=False` draws the balloon alone, sized for the words, so the draft
    can set them over it as text a person can still retype.
    """
    lines, w, h, tail_h = bubble_body(text, size=size, max_width=max_width,
                                      bold=bold)
    if not lines:
        return None
    lh = line_height(size, bold)
    gap = int(size * 0.22)
    pad_y = int(size * 0.55)

    img = Image.new("RGBA", (w, h + tail_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = min(int(size * 0.9), h // 2)
    line_w = max(2, int(size * 0.05))
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius,
                           fill=fill, outline=outline, width=line_w)
    # Tail: fill a triangle, then re-stroke only its two free edges so the
    # balloon outline stays unbroken where the tail meets it.
    if tail in ("left", "right"):
        bx = int(w * (0.22 if tail == "left" else 0.78))
        pts = [(bx - int(size * 0.42), h - line_w),
               (bx + int(size * 0.42), h - line_w),
               (bx + (int(size * 0.1) if tail == "left" else -int(size * 0.1)),
                h + tail_h - 2)]
        draw.polygon(pts, fill=fill)
        draw.line([pts[0], pts[2]], fill=outline, width=line_w)
        draw.line([pts[1], pts[2]], fill=outline, width=line_w)

    for i, line in enumerate(lines if words else []):
        x = (w - advance(line, size, bold)) / 2
        draw.text((x, pad_y + i * (lh + gap)), line, font=font(size, bold),
                  fill=BUBBLE_INK)
    return img


def render_card(size_wh, text, *, size, max_width, highlight=None,
                fill=(255, 255, 255, 255), highlight_fill=(255, 196, 46, 255),
                offset=None, offset_fill=(150, 150, 150, 255), stroke=None,
                stroke_fill=(0, 0, 0, 255), center_y=None, face=None,
                glow=False):
    """Full-frame card text.

    Explicit newlines are honoured and each line is wrapped on its own, so a
    line the author chose to break stays broken there and a phrase never splits
    across lines the way a single auto-wrapped run would split it.

    `offset` draws a displaced copy underneath - the grey shadow the reference
    title cards use behind their red brush lettering. `face="brush"` sets the
    card in the brush lettering, falling back to the sans face whole when the
    text has a character the brush font lacks. `glow` puts a soft halo of the
    highlight colour behind the highlighted words, as the reference's closing
    card does.
    """
    W, H = size_wh
    if face == "brush" and not covers(text, face):
        face = None
    # A brush stroke is already heavy; the faux weight the sans face needs to
    # look like the references' Heavy would only blot it.
    weight = 0 if face == "brush" else FAUX_WEIGHT
    if stroke is None:
        stroke = max(3, round(size * 0.055))
    lines = wrap(text, size, max_width, True, face) or [""]

    lh = line_height(size, True, face)
    gap = int(size * 0.26)
    total = lh * len(lines) + gap * (len(lines) - 1)
    top = (center_y if center_y is not None else H // 2) - total // 2

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    def place(target, dx, dy, force=None, only_highlight=False):
        draw = ImageDraw.Draw(target)
        for i, line in enumerate(lines):
            runs = split_highlight(line, highlight)
            line_w = sum(advance(t, size, True, face) for t, _ in runs)
            x = (W - line_w) / 2 + dx
            y = top + i * (lh + gap) + dy
            if only_highlight:
                coloured = [(t, highlight_fill if hl else (0, 0, 0, 0))
                            for t, hl in runs]
                draw_runs(draw, x, y, coloured, size, True, 0, stroke_fill,
                          weight, face)
                continue
            coloured = [(t, force or (highlight_fill if hl else fill))
                        for t, hl in runs]
            draw_runs(draw, x, y, coloured, size, True, stroke, stroke_fill,
                      weight, face)

    if glow and highlight:
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        place(halo, 0, 0, only_highlight=True)
        halo = halo.filter(ImageFilter.GaussianBlur(max(4, size * 0.16)))
        # Twice: one blur pass is a smudge, two read as light.
        layer.alpha_composite(halo)
        layer.alpha_composite(halo)
    if offset:
        place(layer, offset[0], offset[1], force=offset_fill)
    place(layer, 0, 0)
    return layer


def render_title_bar(size_wh, text, *, top, height, fill=(255, 214, 0),
                     ink=(22, 22, 22), size=0.056, radius=0.018, margin=0.04):
    """The fixed question across the top of a portrait video, as a full frame.

    Portrait leaves the top third of a phone screen empty above the
    characters, and a viewer who scrolls in mid-video has no idea what it is
    about. A bar holding the video's own title answers both - the convention
    of knowledge videos on a phone feed. Everything is a fraction of the frame:
    `top` and `height` of its height, `size`, `radius` and `margin` of its
    width. The type shrinks to fit rather than wrapping: a bar is one line.
    """
    W, H = size_wh
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text = " ".join((text or "").split())
    if not text:
        return layer
    x0, x1 = int(W * margin), int(W * (1 - margin))
    y0, y1 = int(H * top), int(H * (top + height))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=int(W * radius),
                           fill=tuple(fill) + (255,))
    px = int(W * size)
    room = (x1 - x0) - 2 * int(W * 0.03)
    while px > 12 and advance(text, px, True) > room:
        px -= 2
    lh = line_height(px, True)
    x = (W - advance(text, px, True)) / 2
    y = (y0 + y1) / 2 - lh / 2
    draw_runs(draw, x, y, [(text, tuple(ink) + (255,))], px, True)
    return layer


def _shade(colour, k):
    return tuple(max(0, min(255, int(round(c * k)))) for c in colour[:3])


def render_panel(width, height, fill=(176, 196, 205), alpha=255, radius=0,
                 outline=None, line_width=3, floor_top=None):
    """A wall behind everyone, standing on a floor, used to set a shot indoors.

    The references build locations out of these rather than swapping the
    background: a pale blue-grey rectangle behind Mr. Krabs is a quay, a large
    grey one filling the upper left is the outside of a building. It is the one
    device that lets a single fixed plate carry a scene set somewhere else.

    It used to be one flat translucent slab, and it read as frosted glass: the
    plate's horizon, grass and coral showed straight through it, and nothing
    said where the wall stopped and the ground began. It is opaque now, lit a
    little from above, and when `floor_top` is given - the pixel row, inside
    the panel, where the characters' ground line falls - it stands on a floor
    with a skirting board along the join, so the characters stand in a room.
    """
    width, height = max(1, int(width)), max(1, int(height))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    opacity = max(0, min(255, int(alpha)))
    base = tuple(fill)[:3]
    wall = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(wall)
    floor_at = height if floor_top is None else max(0, min(height, int(floor_top)))
    # A gentle vertical gradient on the wall: lighter at the top, a shade
    # darker where it meets the floor. Flat colour is what made it a slab.
    span = max(1, floor_at)
    for y in range(floor_at):
        k = 1.06 - 0.10 * (y / span)
        draw.line([(0, y), (width, y)], fill=_shade(base, k) + (opacity,))
    if floor_at < height:
        skirting = max(3, int(height * 0.012))
        draw.rectangle([0, floor_at, width, min(height, floor_at + skirting)],
                       fill=_shade(base, 0.62) + (opacity,))
        floor = _shade(base, 0.80)
        for y in range(floor_at + skirting, height):
            k = 1.0 - 0.10 * ((y - floor_at) / max(1, height - floor_at))
            draw.line([(0, y), (width, y)], fill=_shade(floor, k) + (opacity,))
    # A darker line along the top edge, so the wall ends where it means to.
    draw.rectangle([0, 0, width, max(2, int(height * 0.006))],
                   fill=_shade(base, 0.78) + (opacity,))
    if radius > 0 or outline:
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, width - 1, height - 1], radius=int(max(0, radius)), fill=255)
        img.paste(wall, (0, 0), mask)
        if outline:
            ImageDraw.Draw(img).rounded_rectangle(
                [0, 0, width - 1, height - 1], radius=int(max(0, radius)),
                outline=tuple(outline) + (255,), width=line_width)
        return img
    return wall
