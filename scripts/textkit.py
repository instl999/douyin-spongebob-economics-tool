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

from PIL import Image, ImageDraw, ImageFont

BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",      # Microsoft YaHei Bold - the closest
    r"C:\Windows\Fonts\simhei.ttf",      # SimHei
    r"C:\Windows\Fonts\msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
REGULAR_CANDIDATES = [r"C:\Windows\Fonts\msyh.ttc"] + BOLD_CANDIDATES


def _first_existing(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


BOLD_PATH = _first_existing(BOLD_CANDIDATES)
REGULAR_PATH = _first_existing(REGULAR_CANDIDATES)


@lru_cache(maxsize=64)
def font(size, bold=True):
    path = BOLD_PATH if bold else REGULAR_PATH
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


@lru_cache(maxsize=8192)
def advance(text, size, bold=True):
    return ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=font(size, bold))


def line_height(size, bold=True):
    ascent, descent = font(size, bold).getmetrics()
    return ascent + descent


# Characters that may not open a line, and those that may not end one.
NO_LINE_START = set("\u3002\uff0c\u3001\uff01\uff1f\uff1b\uff1a\uff09\u3011\u300b\u300d\u300f\u201d\u2019%\u2026\u2014\u00b7!?,.:;)]}>")
NO_LINE_END = set("\uff08\u3010\u300a\u300c\u300e\u201c\u2018([{<")


def wrap(text, size, max_width, bold=True):
    """Greedy CJK-aware wrap. Latin words are kept whole.

    Newlines are hard breaks: each paragraph wraps on its own. This is also a
    correctness guard, because PIL refuses to measure a string containing a
    newline at all - `advance` must never see one.
    """
    text = (text or "").strip()
    if not text:
        return []
    if "\n" in text or "\r" in text:
        out = []
        for para in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            out.extend(wrap(para, size, max_width, bold))
        return out
    lines, current = [], ""

    def width(s):
        return advance(s, size, bold)

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

    for tok in tokens:
        candidate = current + tok
        if width(candidate) <= max_width or not current:
            current = candidate
            continue
        # Pull one character down if the next line would open on a closer, or
        # this line would end on an opener.
        if tok in NO_LINE_START and len(current) > 1:
            lines.append(current[:-1])
            current = current[-1] + tok
        elif current and current[-1] in NO_LINE_END and len(current) > 1:
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
        if advance(moved + lines[-1], size, bold) > max_width:
            break
        lines[-2], lines[-1] = lines[-2][:-1], moved + lines[-1]
    return lines


# How much to thicken the glyph itself, as a fraction of the type size.
# Windows ships nothing heavier than YaHei Bold, and the references are visibly
# heavier than that - closer to a Heavy weight. Stroking the fill in its own
# colour synthesises the missing weight without needing a font file installed,
# which matters because this is visible in every single frame of every video.
FAUX_WEIGHT = 0.016
MAX_FAUX_PIXELS = 2


def draw_runs(draw, x, y, runs, size, bold=True, stroke=0,
              stroke_fill=(0, 0, 0, 255), weight=FAUX_WEIGHT):
    """Draw [(text, colour), ...] left to right from x, returning the end x.

    Drawn in two passes: the outline first at its full width, then the fill
    stroked in its own colour. Doing it the other way round, or in one pass,
    would have the thickened fill eat into the outline and leave the black edge
    looking thin and patchy against a busy plate.
    """
    f = font(size, bold)
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
        x += advance(text, size, bold)
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


def render_bubble(text, *, size, max_width, tail="left", bold=False,
                  fill=(255, 255, 255, 245), outline=(20, 20, 20, 255)):
    """A rounded speech balloon sized to its text, with a tail on one side."""
    lines = wrap(text, size, max_width, bold)
    if not lines:
        return None
    lh = line_height(size, bold)
    gap = int(size * 0.22)
    pad_x, pad_y = int(size * 0.75), int(size * 0.55)
    tw = int(max(advance(l, size, bold) for l in lines))
    th = lh * len(lines) + gap * (len(lines) - 1)
    w, h = tw + pad_x * 2, th + pad_y * 2
    tail_h = int(size * 0.85)

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

    for i, line in enumerate(lines):
        x = (w - advance(line, size, bold)) / 2
        draw.text((x, pad_y + i * (lh + gap)), line, font=font(size, bold),
                  fill=(20, 20, 20, 255))
    return img


def render_card(size_wh, text, *, size, max_width, highlight=None,
                fill=(255, 255, 255, 255), highlight_fill=(255, 196, 46, 255),
                offset=None, offset_fill=(150, 150, 150, 255), stroke=None,
                stroke_fill=(0, 0, 0, 255), center_y=None):
    """Full-frame card text.

    Explicit newlines are honoured and each line is wrapped on its own, so a
    line the author chose to break stays broken there and a phrase never splits
    across lines the way a single auto-wrapped run would split it.

    `offset` draws a displaced copy underneath - the grey shadow the reference
    title cards use behind their red brush lettering.
    """
    W, H = size_wh
    if stroke is None:
        stroke = max(3, round(size * 0.055))
    lines = wrap(text, size, max_width, True) or [""]

    lh = line_height(size, True)
    gap = int(size * 0.26)
    total = lh * len(lines) + gap * (len(lines) - 1)
    top = (center_y if center_y is not None else H // 2) - total // 2

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    def place(dx, dy, force=None):
        for i, line in enumerate(lines):
            runs = split_highlight(line, highlight)
            line_w = sum(advance(t, size, True) for t, _ in runs)
            x = (W - line_w) / 2 + dx
            y = top + i * (lh + gap) + dy
            coloured = [(t, force or (highlight_fill if hl else fill))
                        for t, hl in runs]
            draw_runs(draw, x, y, coloured, size, True, stroke, stroke_fill)

    if offset:
        place(offset[0], offset[1], force=offset_fill)
    place(0, 0)
    return layer


def render_panel(width, height, fill=(176, 196, 205), alpha=235, radius=0,
                 outline=None, line_width=3):
    """A flat slab used as a wall, a floor or a dock.

    The references build locations out of these rather than swapping the
    background: a pale blue-grey rectangle behind Mr. Krabs is a quay, a large
    grey one filling the upper left is the outside of a building. It is the one
    device that lets a single fixed plate carry a scene set somewhere else, so
    it is worth having even though it is only a rounded rectangle.
    """
    width, height = max(1, int(width)), max(1, int(height))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    body = tuple(fill) + (max(0, min(255, int(alpha))),)
    if radius > 0:
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=int(radius),
                               fill=body,
                               outline=(tuple(outline) + (255,)) if outline else None,
                               width=line_width)
    else:
        draw.rectangle([0, 0, width - 1, height - 1], fill=body,
                       outline=(tuple(outline) + (255,)) if outline else None,
                       width=line_width)
    return img
