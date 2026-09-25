"""Motion graphics: the shots that footage cannot supply and stills cannot carry.

Economics is full of beats that have no photograph. A savings rate, a flow of
money from households through a bank to firms, one quantity outgrowing another
- there is no clip of any of that, and a generated still of "a rising line"
is a picture of a line rather than the line rising. The Mach reference makes
the same call: the one shot in all three references that was *made* rather than
found is its three-panel shockwave composite, built precisely because no
footage of a shockwave exists.

So this is a third asset kind alongside retrieved footage and generated stills,
and it is drawn rather than generated. That is not a stylistic preference:
these frames carry numbers, and an image model asked for "a bar chart showing
45%" produces something that looks like a chart and says something else. Every
value here is drawn from the spec by arithmetic.

Each kind builds over the first part of the shot and then holds, which is how
the references treat their graphics - the movement is the explanation, and the
hold is the beat to read it.

The spec dicts are validated in `footage.validate_graphic` before they reach
this module, including the rule that matters most: a number may only appear in
a graphic if it appears in the narration. See the note there.
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

import config
import textkit

KINDS = ("bar_chart", "line_chart", "counter", "flow", "comparison",
         "breakdown")

# Palettes per grade, so a graphic cuts against the footage it sits next to
# rather than announcing itself. The vintage set is paper and ink with a single
# red accent - the register of a printed period chart - because running a real
# chart through the vintage *film* grade would desaturate the one thing on
# screen whose colour carries meaning.
# Validated with the dataviz skill's checker rather than chosen by eye, which
# caught what eyeballing could not: the previous dark pair (#5ABED6 accent,
# #808A98 muted) sat at normal-vision deltaE 14.6 - *below* the floor of 15, so
# two bars were genuinely hard to tell apart for full-colour readers - and the
# muted slot had chroma 0.024, i.e. it was grey pretending to be a colour.
#
#   clean   #4288CC + #BE8532 on #161A20 - passes all five checks
#           (normal deltaE 24.9, CVD 22.2, both inside L 0.48-0.67)
#   vintage #B03A2E + #2E6E78 on #E8E3D6 - passes separation (normal 22.3,
#           CVD 11.8) and deliberately fails the chroma floor: a period paper
#           chart is meant to read as ink, and every bar carries a direct
#           label, which is the secondary encoding that permits it.
#
# Re-run before changing these:
#   python <skill>/scripts/validate_palette.py "#4288CC,#BE8532" #          --mode dark --surface "#161A20"
PALETTES = {
    # `muted` and `accent` are SERIES colours. `dim` is a text token - secondary
    # ink for axis labels and captions. They are separate because the skill's
    # rule is that text never wears a series colour: a caption painted in the
    # blue used for a bar reads as a label belonging to that bar.
    "vintage": {"bg": (232, 227, 214), "ink": (34, 32, 30), "muted": (46, 110, 120),
                "accent": (176, 58, 46), "grid": (198, 191, 178),
                "dim": (118, 112, 102)},
    "clean": {"bg": (35, 42, 52), "ink": (238, 240, 243), "muted": (66, 136, 204),
              "accent": (190, 133, 50), "grid": (48, 55, 66),
              "dim": (150, 158, 170)},
    "none": {"bg": (34, 34, 34), "ink": (240, 240, 240), "muted": (66, 136, 204),
             "accent": (190, 133, 50), "grid": (56, 56, 56),
             "dim": (150, 150, 150)},
}

# Mark specs from the dataviz skill: a surface-coloured gap between adjacent
# fills, and rounded ends on the free end of a bar only - the baseline end
# stays square because it is anchored to the axis.
FILL_GAP = 2
END_RADIUS = 4
# A figure printed inside a breakdown segment keeps this much of the frame's
# width clear on either side, or it is printed with the segment's label instead.
FIGURE_INSET = 0.008

# The references do not put their graphics on a flat fill. Every backdrop in
# the Mach video is a soft radial gradient, brighter in the middle, and a flat
# near-black ground is the flattest thing a frame can be - it was also dragging
# whole-video mean luma to 69 against a reference band of 86-113. Centre is
# lifted by this fraction, edges dropped by half of it.
GRADIENT = 0.30
_GRADIENTS = {}

# The Mach reference's backdrop carries a faint grid of small crosses. It is
# barely visible frame to frame and it is doing real work: a graphic frame here
# measured 2-10% of its pixels as content against that reference's 60%, and an
# empty ground is what "flat" actually looks like. Texture gives the eye
# something to sit on without competing with the data.
TEXTURE_STEP = 96          # pixels between marks
TEXTURE_ARM = 7            # half-length of each cross arm
TEXTURE_LIFT = 26          # how far above the ground the marks sit, 0-255


def backdrop(size, base):
    """A soft radial gradient ground, cached per size and colour."""
    import numpy as np

    key = (size, base)
    if key not in _GRADIENTS:
        W, H = size
        ys = np.linspace(-1.0, 1.0, H, dtype=np.float32)[:, None]
        xs = np.linspace(-1.0, 1.0, W, dtype=np.float32)[None, :]
        # Elliptical falloff, softened so no edge is visible.
        r = np.sqrt((xs * 0.72) ** 2 + ys ** 2)
        k = 1.0 + GRADIENT * (1.0 - np.clip(r, 0.0, 1.0) ** 1.4) - GRADIENT * 0.5
        plate = np.clip(np.asarray(base, dtype=np.float32)[None, None, :]
                        * k[:, :, None], 0, 255).astype("uint8")
        image = Image.fromarray(plate, "RGB")
        mark = tuple(min(255, c + TEXTURE_LIFT) for c in base)
        pen = ImageDraw.Draw(image)
        for gy in range(TEXTURE_STEP // 2, H, TEXTURE_STEP):
            for gx in range(TEXTURE_STEP // 2, W, TEXTURE_STEP):
                pen.line([(gx - TEXTURE_ARM, gy), (gx + TEXTURE_ARM, gy)],
                         fill=mark, width=2)
                pen.line([(gx, gy - TEXTURE_ARM), (gx, gy + TEXTURE_ARM)],
                         fill=mark, width=2)
        _GRADIENTS[key] = image
    return _GRADIENTS[key].copy()

# Build over the first 55% of the shot, then hold. A graphic that is still
# moving when the narration moves on reads as unfinished.
BUILD_FRACTION = 0.55
# Bars, points and nodes come in one after another rather than together.
STAGGER = 0.18
# A flow's tokens: up to this many on each arrow at once, no closer together
# than FLOW_SPACING of the frame's long side, each crossing its arrow this many
# times over the hold after the build.
FLOW_TOKENS = 3
FLOW_SPACING = 0.06
FLOW_LAPS = 2

# Everything a graphic draws stays inside this band. The bottom fifth of the
# frame belongs to the subtitles - Chinese centred on scanline 975 of 1080 with
# English under it to about 1045 - so a chart that puts its category labels at
# 0.85 of frame height is drawing them underneath the narration. Nothing here
# may cross SAFE_BOTTOM.
SAFE_TOP = 0.10
SAFE_BOTTOM = 0.78
# A portrait frame is watched inside the phone app, whose own text and buttons
# cover the bottom fifth of the screen, so its captions sit above them - and
# the graphics have to stop above the captions. Measured off
# `footage_render.caption_layout`: a Chinese line with its English under it,
# kept clear of the app, starts at 0.726 of the height, and with two lines of
# English at 0.703. A selftest holds the two together.
SAFE_BOTTOM_PORTRAIT = 0.69

# How far the composition drifts across a shot, in pixels of an oversized
# canvas. Small enough that nobody reads it as a camera move, large enough that
# the frame is not frozen next to moving footage.
DRIFT = 12


def safe_bottom(W=None, H=None):
    """The lowest a graphic may reach, as a fraction of frame height."""
    return SAFE_BOTTOM_PORTRAIT if (W and H and H > W) else SAFE_BOTTOM


def layout_floor(H, W=None):
    """The bottom the drawers lay out against, drift already reserved.

    SAFE_BOTTOM is a guarantee about the finished frame. The drift moves the
    whole composition by up to DRIFT pixels, so laying out flush against the
    guarantee puts content past it at one end of the move - measured, bar
    labels landed 6 px inside the subtitle band at t=0. Drawers use this;
    tests check the output against `safe_bottom`.
    """
    return safe_bottom(W, H) - (DRIFT / float(H))


def palette(grade):
    return PALETTES.get(grade, PALETTES["clean"])


# --- persistent furniture --------------------------------------------------
# Chrome that is identical on every made shot, so a hard cut changes the
# content and not the world. The Mach reference runs one ground under almost
# its whole length; ours changed completely at every cut, which is part of why
# a technically correct montage read as a slideshow of unrelated pictures.
#
# It is composited once over the ASSEMBLED video, not per shot. Drawn per
# shot it only appears on the kinds this module and composite.py render, so a
# build with real retrieved footage in it would show the rule blink out at
# every photographic shot and back in afterwards - furniture that comes and
# goes is worse than none. It also has to sit above the per-shot vignette,
# which is a property of the picture and not of the frame around it.
#
# This is a design decision, not a density lever: measured, the whole of it is
# worth under a percent of frame area. What fixes an empty frame is the
# build-in timing, not this.
RULE_Y = 0.028            # the progress hairline, above SAFE_TOP
EYEBROW_Y = 0.058         # the topic label, in the band SAFE_TOP leaves free
EYEBROW = 0.020           # its size as a fraction of frame width


def furniture(image, pal, spec, progress=None):
    """Draw the progress rule, topic eyebrow and baseline onto a finished frame.

    `spec` is a dict with an optional "topic". `progress` fills the rule to a
    fraction; the assembled-video path leaves it None and animates the fill in
    ffmpeg instead, because one drawbox expression is cheaper than compositing
    a thousand PNGs.
    """
    if not spec:
        return image
    W, H = image.size
    draw = ImageDraw.Draw(image)

    y = int(H * RULE_Y)
    draw.line([(0, y), (W, y)], fill=pal["grid"], width=3)
    if progress is not None:
        here = max(0.0, min(1.0, float(progress)))
        if here > 0:
            draw.line([(0, y), (int(W * here), y)], fill=pal["accent"], width=3)

    topic = (spec.get("topic") or "").strip()
    if topic:
        _text(draw, (int(W * 0.05), int(H * EYEBROW_Y)), topic,
              max(16, int(W * EYEBROW)), pal["dim"], anchor="lm")

    base = int(H * safe_bottom(W, H))
    draw.line([(int(W * 0.05), base), (int(W * 0.95), base)],
              fill=pal["grid"], width=2)
    return image


def ease(t):
    """Smoothstep, clamped. Same curve the renderer uses for its dissolves."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _progress(t, index=0, count=1):
    """How far element `index` has built at overall progress t (0..1)."""
    if count <= 1:
        return ease(t / BUILD_FRACTION if BUILD_FRACTION else 1.0)
    start = STAGGER * index / max(1, count - 1) * BUILD_FRACTION
    span = max(1e-6, BUILD_FRACTION - start)
    return ease((t - start) / span)


def _fmt(value, unit=""):
    if isinstance(value, float) and not value.is_integer():
        text = f"{value:.1f}"
    else:
        text = f"{int(round(value))}"
    return text + (unit or "")


def _text(draw, xy, body, size, fill, anchor="mm", bold=True):
    draw.text(xy, body, font=textkit.font(size, bold), fill=fill, anchor=anchor)


def _bar(draw, x0, y0, x1, y1, fill, radius=END_RADIUS):
    """A bar with its free end rounded and its baseline end square."""
    if y1 - y0 < radius * 2 or x1 - x0 < radius * 2:
        draw.rectangle([x0, y0, x1, y1], fill=fill)
        return
    try:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill,
                               corners=(True, True, False, False))
    except (TypeError, ValueError):        # Pillow without per-corner support
        draw.rectangle([x0, y0, x1, y1], fill=fill)


def _nice_step(span, target=5):
    """A round gridline interval: 1, 2, 2.5 or 5 times a power of ten."""
    if span <= 0:
        return 1.0
    raw = span / max(1, target)
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for factor in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= factor * magnitude:
            return factor * magnitude
    return 10.0 * magnitude


def _blend(colour, ground, k):
    """`colour` at opacity k over `ground`, as an opaque RGB triple.

    The drawers are handed an ImageDraw on an RGB image, not the image, so
    there is no alpha to composite with. Pre-blending against the known
    background colour is the same result for a flat fill, and it keeps the
    drawers' signature - a chart kind that needed the image would need every
    other kind to be rewritten to match.
    """
    return tuple(int(g + (c - g) * k) for c, g in zip(colour, ground))


def _gridlines(draw, pal, left, right, base, usable, lo, hi, W, unit=""):
    """Horizontal rules at round values between lo and hi, each labelled.

    Present from the first frame, unlike the data. Two things follow from that.
    A chart whose bars grow out of an empty rectangle is a blank screen for the
    half-second after a hard cut - measured, 6.4% of the frame was covered at
    the cut against 26.8% once grown, and there are seven cuts in a
    thirty-five-second video. And a chart with no scale asks the viewer to take
    the printed figure on faith; gridlines are how a reader checks one bar
    against another.
    """
    span = float(hi) - float(lo)
    if span <= 0 or usable <= 0:
        return
    step = _nice_step(span)
    size = max(18, int(W * 0.019))
    value = math.ceil(float(lo) / step) * step
    guard = 0
    while value <= hi * 1.0001 and guard < 40:
        guard += 1
        if abs(value - lo) > 1e-9:                # the baseline draws itself
            y = int(base - usable * (value - lo) / span)
            draw.line([(left, y), (right, y)], fill=pal["grid"], width=1)
            _text(draw, (left - int(W * 0.015), y), _fmt(value, unit), size,
                  pal["dim"], anchor="rm")
        value += step


# --- the kinds -------------------------------------------------------------

def _title(draw, spec, pal, W, H):
    title = (spec.get("title") or "").strip()
    if title:
        _text(draw, (W // 2, int(H * (SAFE_TOP + 0.035))), title,
              max(34, int(W * 0.034)), pal["ink"], anchor="mm")
    return int(H * 0.21) if title else int(H * SAFE_TOP)


def draw_bar_chart(draw, spec, pal, W, H, t):
    """Vertical bars growing from a baseline, each labelled with its value."""
    items = spec["items"]
    top = _title(draw, spec, pal, W, H)
    base = int(H * (layout_floor(H, W) - 0.075))  # leave room for the labels below
    left, right = int(W * 0.12), int(W * 0.88)
    span = right - left
    slot = span / max(1, len(items))
    bar_w = min(slot * 0.62, W * 0.17)
    peak = max((abs(float(i["value"])) for i in items), default=1.0) or 1.0
    usable = base - top - int(H * 0.04)

    _gridlines(draw, pal, left - 20, right + 20, base, usable, 0.0, peak, W,
               spec.get("unit", ""))
    draw.line([(left - 20, base), (right + 20, base)], fill=pal["grid"], width=2)
    for i, item in enumerate(items):
        p = _progress(t, i, len(items))
        value = float(item["value"])
        height = usable * (abs(value) / peak) * p
        cx = left + slot * (i + 0.5)
        x0, x1 = cx - bar_w / 2, cx + bar_w / 2
        # Two bars are two entities, so colour carries identity and they take
        # the two validated slots in fixed order - that is the pair the
        # validator was run on, and drawing both in one hue meant it never
        # actually appeared on screen. Three or more bars are magnitude across
        # categories, not identity, so they share one hue and only the
        # highlighted one lifts: a hue per category is the rainbow-bar
        # anti-pattern.
        hot = spec.get("highlight") and item.get("label") == spec["highlight"]
        if len(items) == 2:
            shade = pal["accent"] if (i == 1) != bool(
                spec.get("highlight") and items[0]["label"] == spec["highlight"]
            ) else pal["muted"]
        else:
            shade = pal["accent"] if hot else pal["muted"]
        _bar(draw, x0, base - height, x1, base, shade)
        if p > 0.25:
            _text(draw, (cx, base - height - int(H * 0.038)),
                  _fmt(value * p, spec.get("unit", "")),
                  max(26, int(W * 0.028)), pal["ink"], anchor="mm")
        _text(draw, (cx, base + int(H * 0.042)), str(item.get("label", "")),
              max(24, int(W * 0.025)), pal["ink"], anchor="mm")


def draw_line_chart(draw, spec, pal, W, H, t):
    """One or more series drawing on left to right."""
    series = spec["series"]
    top = _title(draw, spec, pal, W, H)
    base = int(H * (layout_floor(H, W) - 0.06))
    left, right = int(W * 0.12), int(W * 0.88)
    usable = base - top - int(H * 0.04)
    every = [float(v) for s in series for v in s["points"]]
    lo, hi = min(every), max(every)
    rng = (hi - lo) or 1.0
    _gridlines(draw, pal, left, right, base, usable, lo, hi, W,
               spec.get("unit", ""))
    draw.line([(left, base), (right, base)], fill=pal["grid"], width=2)

    for si, s in enumerate(series):
        pts = [float(v) for v in s["points"]]
        colour = pal["accent"] if si == 0 else pal["muted"]
        shown = ease(t / BUILD_FRACTION) * (len(pts) - 1)
        coords = []
        for i, v in enumerate(pts):
            if i > shown + 1:
                break
            x = left + (right - left) * i / max(1, len(pts) - 1)
            y = base - usable * (v - lo) / rng
            if i <= shown:
                coords.append((x, y))
            else:                       # partial segment to the moving head
                px, py = coords[-1]
                k = shown - (i - 1)
                coords.append((px + (x - px) * k, py + (y - py) * k))
        if len(coords) > 1:
            # One series gets the area under it filled. A single stroke on an
            # empty field measured 6% of the frame covered - the thinnest shot
            # this track produces, and it looks it. Two or more series do not:
            # overlapping translucent fills stop the reader telling which
            # series owns which region, which is the whole point of the chart.
            if len(series) == 1:
                draw.polygon(coords + [(coords[-1][0], base), (coords[0][0], base)],
                             fill=_blend(colour, pal["bg"], 0.22))
            draw.line(coords, fill=colour, width=max(4, int(W * 0.005)),
                      joint="curve")
        if coords:
            hx, hy = coords[-1]
            r = max(6, int(W * 0.006))
            draw.ellipse([hx - r, hy - r, hx + r, hy + r], fill=colour)
            if s.get("label"):
                # The coloured head marker beside it carries the identity; the
                # word itself stays in ink.
                _text(draw, (hx, hy - int(H * 0.045)), str(s["label"]),
                      max(22, int(W * 0.022)), pal["ink"], anchor="mm")

    for i, label in enumerate(spec.get("x_labels") or []):
        x = left + (right - left) * i / max(1, len(spec["x_labels"]) - 1)
        _text(draw, (x, base + int(H * 0.038)), str(label),
              max(22, int(W * 0.022)), pal["dim"], anchor="mm")


def draw_counter(draw, spec, pal, W, H, t):
    """One number counting up. The simplest graphic and often the right one."""
    value = float(spec["value"])
    p = ease(t / BUILD_FRACTION)
    _text(draw, (W // 2, int(H * 0.40)), _fmt(value * p, spec.get("unit", "")),
          max(110, int(W * 0.175)), pal["accent"], anchor="mm")
    if spec.get("caption"):
        _text(draw, (W // 2, int(H * 0.60)), str(spec["caption"]),
              max(30, int(W * 0.032)), pal["ink"], anchor="mm")


def draw_flow(draw, spec, pal, W, H, t):
    """Labelled nodes, arrows between them, and tokens streaming along.

    This is the money-flow shot: households -> bank -> firms. The movement is
    the whole point, so it keeps moving after the nodes have settled - and it
    moves along the arrows. A single token travelling node centre to node
    centre spent most of its trip hidden under the boxes, and the arrows it
    ran along were drawn in the gridline colour, a shade off the ground.

    A portrait frame stacks the chain top to bottom. Across 1080 pixels four
    boxes left no arrow between them to speak of.
    """
    nodes = spec["nodes"]
    count = len(nodes)
    top = _title(draw, spec, pal, W, H)
    vertical = H > W
    if vertical:
        bottom = int(H * (layout_floor(H, W) - 0.06))
        pitch = (bottom - top) / max(1, count)
        centres = [(W / 2, top + pitch * (i + 0.5)) for i in range(count)]
        box_w, box_h = int(W * 0.58), int(min(H * 0.10, pitch * 0.56))
        axis = (0.0, 1.0)
    else:
        cy = (top + int(H * (layout_floor(H, W) - 0.08))) // 2
        left, right = int(W * 0.15), int(W * 0.85)
        pitch = (right - left) / max(1, count - 1)
        centres = [(left + pitch * i, cy) for i in range(count)]
        box_w, box_h = int(min(W * 0.19, pitch * 0.64)), int(H * 0.20)
        axis = (1.0, 0.0)
    reach = (box_w if axis[0] else box_h) / 2 + max(12, int(W * 0.008))
    stroke = max(4, int(min(W, H) * 0.005))
    head = max(14, int(min(W, H) * 0.016))

    links = []
    for i, ((ax, ay), (bx, by)) in enumerate(zip(centres, centres[1:])):
        start = (ax + axis[0] * reach, ay + axis[1] * reach)
        end = (bx - axis[0] * reach, by - axis[1] * reach)
        links.append((start, end))
        p = _progress(t, i, count)
        if p <= 0:
            continue
        tip = (start[0] + (end[0] - start[0]) * p,
               start[1] + (end[1] - start[1]) * p)
        # The shaft stops at the head's base, so the point stays sharp.
        base = (tip[0] - axis[0] * head * 0.8, tip[1] - axis[1] * head * 0.8)
        draw.line([start, base if p > 0.9 else tip], fill=pal["dim"],
                  width=stroke)
        if p > 0.9:
            across = (axis[1] * head * 0.62, axis[0] * head * 0.62)
            draw.polygon([tip, (tip[0] - axis[0] * head + across[0],
                                tip[1] - axis[1] * head + across[1]),
                          (tip[0] - axis[0] * head - across[0],
                           tip[1] - axis[1] * head - across[1])],
                         fill=pal["dim"])

    # Once the arrows are drawn, a stream of tokens runs along every one of
    # them at once: a flow reads as continuous, a lone dot as a cursor.
    if t > BUILD_FRACTION and links:
        run = (t - BUILD_FRACTION) / max(1e-6, 1.0 - BUILD_FRACTION)
        radius = max(8, int(min(W, H) * 0.009))
        for (sx, sy), (ex, ey) in links:
            # As many as the arrow has room for: three on a short arrow
            # between four boxes ran together into one smudge.
            room = math.hypot(ex - sx, ey - sy) / (max(W, H) * FLOW_SPACING)
            tokens = max(1, min(FLOW_TOKENS, int(room)))
            for k in range(tokens):
                where = (run * FLOW_LAPS + k / tokens) % 1.0
                # Each grows out of the arrow's tail and shrinks into its
                # head, so none pops into being mid-shaft.
                r = radius * min(1.0, where / 0.15, (1.0 - where) / 0.15)
                if r < 1.0:
                    continue
                x, y = sx + (ex - sx) * where, sy + (ey - sy) * where
                draw.ellipse([x - r, y - r, x + r, y + r], fill=pal["accent"])

    face = _blend(pal["muted"], pal["bg"], 0.16)
    size = max(26, int(W * 0.027)) if not vertical else max(30, int(W * 0.05))
    for i, ((x, y), name) in enumerate(zip(centres, nodes)):
        p = _progress(t, i, count)
        if p <= 0.02:
            continue
        h = box_h * ease(min(1.0, p * 1.4))
        draw.rounded_rectangle(
            [x - box_w / 2, y - h / 2, x + box_w / 2, y + h / 2],
            radius=max(4, int(min(box_w, h) * 0.14)), fill=face,
            outline=pal["muted"], width=max(3, int(W * 0.0025)))
        # The label arrives with the box, not half a second after it. Waiting
        # until p > 0.5 left three empty rectangles on screen for a beat, which
        # reads as a rendering fault rather than an animation.
        if p > 0.22:
            fade = min(1.0, (p - 0.22) / 0.25)
            ink = _blend(pal["ink"], face, fade)
            px, lines = _fitted_label(str(name), size, box_w * 0.84, 2)
            lh = textkit.line_height(px, True)
            for n, line in enumerate(lines):
                _text(draw, (x, y + (n - (len(lines) - 1) / 2) * lh), line,
                      px, ink, anchor="mm")

    if spec.get("caption"):
        _text(draw, (W // 2, int(H * (layout_floor(H, W) - 0.03))), str(spec["caption"]),
              max(26, int(W * 0.027)), pal["dim"], anchor="mm")


def _fitted_label(text, size, width, max_lines):
    """(size, lines) for text wrapped into `width`, shrunk to fit `max_lines`.

    A flow node's label is up to fourteen characters, and set on one line at
    the node's type size that is twice as wide as the node.
    """
    lines = textkit.wrap(text, size, width)
    while len(lines) > max_lines and size > 18:
        size -= 2
        lines = textkit.wrap(text, size, width)
    return size, lines


def draw_comparison(draw, spec, pal, W, H, t):
    """Two quantities side by side, which is most of applied economics."""
    left_item, right_item = spec["left"], spec["right"]
    top = _title(draw, spec, pal, W, H)
    base = int(H * (layout_floor(H, W) - 0.075))
    usable = base - top - int(H * 0.04)
    peak = max(abs(float(left_item["value"])), abs(float(right_item["value"]))) or 1.0
    bar_w = int(W * 0.23)

    for i, (item, cx, colour) in enumerate((
            (left_item, int(W * 0.33), pal["muted"]),
            (right_item, int(W * 0.67), pal["accent"]))):
        p = _progress(t, i, 2)
        value = float(item["value"])
        h = usable * (abs(value) / peak) * p
        _bar(draw, cx - bar_w / 2, base - h, cx + bar_w / 2, base, colour)
        # show_values is cleared by footage.validate_graphic when the numbers
        # are not stated in the narration. The bars still say which is larger;
        # printing a figure nobody said would be inventing one.
        if p > 0.25 and spec.get("show_values", True):
            _text(draw, (cx, base - h - int(H * 0.042)),
                  _fmt(value * p, spec.get("unit", "")),
                  max(34, int(W * 0.036)), pal["ink"], anchor="mm")
        _text(draw, (cx, base + int(H * 0.042)), str(item.get("label", "")),
              max(28, int(W * 0.030)), pal["ink"], anchor="mm")

    draw.line([(int(W * 0.18), base), (int(W * 0.82), base)],
              fill=pal["grid"], width=2)


def draw_breakdown(draw, spec, pal, W, H, t):
    """Parts of a whole, as one bar filling left to right.

    The kind that was missing. "GDP is consumption, investment, government
    spending and net exports" has no photograph, and asking an image model for
    a diagram of it returns a whiteboard with empty boxes on it - which is
    exactly what the first long build produced.

    Segments are proportional when the narration gave figures and equal when it
    did not. Equal segments say "these are the four parts" without claiming a
    split nobody stated.
    """
    items = spec["items"]
    top = _title(draw, spec, pal, W, H)
    left, right = int(W * 0.10), int(W * 0.90)
    span = right - left
    bar_top = top + int(H * 0.10)
    bar_h = int(H * 0.20)

    values = [abs(float(i.get("value") or 0)) for i in items]
    total = sum(values)
    weights = ([v / total for v in values] if total > 0
               else [1.0 / len(items)] * len(items))

    grown = ease(t / BUILD_FRACTION)
    x = left
    for i, (item, weight) in enumerate(zip(items, weights)):
        seg = span * weight
        shown = max(0.0, min(seg, span * grown - (x - left)))
        if shown <= 0:
            continue
        hot = spec.get("highlight") and item.get("label") == spec["highlight"]
        shade = pal["accent"] if hot else pal["muted"]
        if not hot:
            # Alternate tint so neighbouring segments stay distinguishable
            # without inventing a colour per category.
            k = 0.72 + 0.28 * (i % 2)
            shade = tuple(int(c * k + b * (1 - k))
                          for c, b in zip(pal["muted"], pal["bg"]))
        # A surface-coloured gap between adjacent fills; flush segments read
        # as one bar with colour changes rather than as separate parts.
        draw.rectangle([x, bar_top, x + max(1, shown - FILL_GAP),
                        bar_top + bar_h], fill=shade)
        if shown > seg * 0.9:
            label_y = bar_top + bar_h + int(H * 0.055) + (
                int(H * 0.055) if i % 2 else 0)
            cx = x + seg / 2
            label = str(item.get("label", ""))
            label_px = max(22, int(W * 0.023))
            value_px = max(20, int(W * 0.021))
            figure = (_fmt(float(item["value"]), spec.get("unit", ""))
                      if total > 0 and spec.get("show_values", True) else "")
            # A figure too wide for its own segment moves out to the label.
            # Drawn inside regardless, the 4% of net exports ran past the
            # segment's edges onto the ground - and it is set in the ground's
            # own colour, so the part outside simply vanished.
            room = seg - 2 * FIGURE_INSET * W
            if figure and textkit.advance(figure, value_px, True) > room:
                label, figure = f"{label} {figure}", ""
            # And the label stays in frame: the last segment's centre can sit
            # close enough to the edge that a long label crosses it.
            half = textkit.advance(label, label_px, True) / 2
            lx = min(max(cx, W * 0.03 + half), W * 0.97 - half)
            draw.line([(cx, bar_top + bar_h), (cx, label_y - int(H * 0.022))],
                      fill=pal["grid"], width=2)
            _text(draw, (lx, label_y), label, label_px, pal["ink"], anchor="mm")
            if figure:
                _text(draw, (cx, bar_top + bar_h / 2), figure, value_px,
                      pal["bg"], anchor="mm")
        x += seg


DRAWERS = {
    "breakdown": draw_breakdown,
    "bar_chart": draw_bar_chart,
    "line_chart": draw_line_chart,
    "counter": draw_counter,
    "flow": draw_flow,
    "comparison": draw_comparison,
}


# --- driver ----------------------------------------------------------------

def frame(spec, size, t, grade="clean", drift=DRIFT):
    """One RGB frame at progress t (0..1).

    The whole composition drifts a few pixels across the shot. Without it a
    graphic builds for 55% of its duration and then holds *perfectly* still
    while every footage shot around it keeps moving, which is the slideshow
    problem in miniature - the same defect that made whole-shot dissolves wrong
    for this track.

    The drift is a crop window moving over an oversized canvas, so it is pure
    translation: no resampling, and the type stays exactly as sharp as it was
    drawn. Scaling to fake a push would soften every label on screen.
    """
    pal = palette(grade)
    W, H = size
    drawer = DRAWERS.get(spec.get("kind"))
    if drawer is None:
        raise ValueError(f"unknown graphic kind {spec.get('kind')!r}; "
                         f"have {sorted(DRAWERS)}")
    pad = max(0, int(drift))
    # Lay out against the OUTPUT size and pad around it, never against the
    # padded canvas: laying out against the larger canvas scales every position
    # by ~1% and pushed the bar labels 7 px into the subtitle band. The border
    # is only ever flat background, because nothing is drawn outside
    # SAFE_TOP..SAFE_BOTTOM.
    inner = backdrop((W, H), pal["bg"])
    drawer(ImageDraw.Draw(inner), spec, pal, W, H, t)
    if not pad:
        return inner
    canvas = backdrop((W + 2 * pad, H + 2 * pad), pal["bg"])
    canvas.paste(inner, (pad, pad))
    # Slightly different rates on the two axes so the move reads as a drift
    # rather than a slide along one diagonal.
    x = int(round(pad * (1.0 - math.cos(math.pi * t)) / 1.0))
    y = int(round(pad * (1.0 - math.cos(math.pi * t * 0.62))))
    x = min(max(x, 0), 2 * pad)
    y = min(max(y, 0), 2 * pad)
    return canvas.crop((x, y, x + W, y + H))


# Grain and vignette only. The full `vintage` chain desaturates, and on a chart
# the colour is the meaning - the accent bar stops reading as the highlighted
# one. Texture is enough to sit it in the same world as the footage.
TEXTURE = {
    "vintage": "vignette=PI/5,noise=alls=5:allf=t",
    "clean": "vignette=PI/6",
    "none": "",
}


def render(spec, out_path, seconds, size, fps=30, grade="clean"):
    """Render a graphic to its own clip. Returns the written path."""
    W, H = size
    frames = max(1, int(round(seconds * fps)))
    chain = ["setsar=1"]
    if TEXTURE.get(grade):
        chain.append(TEXTURE[grade])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [config.FFMPEG, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-framerate", str(fps), "-i", "-", "-an",
           "-vf", ",".join(chain),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(frames):
            proc.stdin.write(
                frame(spec, size, i / max(1, frames - 1), grade).tobytes())
    finally:
        proc.stdin.close()
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg exited {code} rendering a {spec.get('kind')}")
    return out_path
