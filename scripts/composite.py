"""Layered shots: a designed ground, a cut-out subject, type, annotation.

This is the thing the footage track was missing, and the reason its output kept
measuring correct and reading flat.

Measured from the references (`references/footage-findings.md`): the Mach video
is barely footage at all. One persistent backdrop runs under almost every shot;
subjects are **cut out** and placed on it, often on a rounded card with a soft
shadow; a large typographic layer carries the point - 「比值」, 「音障」, "1887年" -
and annotations are drawn over the top: dashed outlines around the two
scientists, a red arrow labelling 后掠翼, magnifier circles. The bilingual
subtitle is small and clearly secondary. A reference graphic frame is about 60%
content; a full-frame photo under a caption, which is what this track built
before, was 2-10%.

Architecturally this is the *drawn* track's model - fixed plate plus composited
cutouts - with photographic elements instead of cartoon sprites, so it reuses
that track's matting outright rather than inventing a second one.

Layers, back to front:

    backdrop      motion.backdrop - gradient and cross-hatch, shared with the
                  graphics so the two kinds of made shot sit in one world
    card          optional rounded panel with a soft shadow under the subject
    subject       a generated image on a plain ground, matted to transparent
    headline      the large type layer, NOT the subtitle
    annotation    a label on a leader line pointing into the subject

Everything stays above `motion.SAFE_BOTTOM`; the bottom fifth belongs to the
burnt-in subtitle, which this module never draws.
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import config
import matting
import motion
import textkit

LAYOUTS = ("subject_left", "subject_right", "subject_center", "card")

# Type sizes as a fraction of frame width. The headline is a design element and
# is far larger than the 0.032 the subtitle uses - in the references it is the
# first thing read.
HEADLINE = 0.072
SUBLINE = 0.030
NOTE = 0.026

# Build in over the first part of the shot, then hold, matching motion.py so a
# composite and a chart cut together without a change of rhythm.
#
# The subject and the headline do not fade in at all. Sampling the density of
# every frame of a build showed the frame at each hard cut was 0.6-8.5% full
# and climbing: every layer ramped up from zero alpha, so for about a second
# after each of seven cuts there was effectively an empty screen, seven times
# in thirty-five seconds. That reads as cheap far more loudly than a low
# average density does.
#
# The references cut TO a finished composition and animate an annotation over
# it - the red arrow labelling 后掠翼, the dashed outline. So here: the
# composition is complete on the first frame, the subject and headline only
# SETTLE into place (pure translation, full opacity), and the note is the one
# thing that draws itself on.
SUBJECT_SETTLE = 0.22          # how long the subject's small rise takes
HEADLINE_SETTLE = 0.16
NOTE_IN = (0.45, 0.78)

SUBJECT_PROMPT = (
    "{subject}. Isolated on a plain pure white background, studio photograph, "
    "sharp focus, evenly lit, the whole object visible and centred, "
    "no shadow on the background, no text, no letters, no watermark, no logo, "
    "not an illustration."
)


def _ease(t):
    return motion.ease(t)


def _window(t, span):
    lo, hi = span
    return _ease((t - lo) / max(1e-6, hi - lo))


# --- the subject -----------------------------------------------------------

def subject_png(description, cache_dir=None, seed=None):
    """Generate a subject on a plain ground and matte it to transparent.

    Cached by prompt. The matting is the drawn track's `auto_cutout`, which
    already picks between the white-background and chroma paths and has the
    spill suppression that stops a white rim appearing on a dark backdrop.
    """
    import ark

    cache_dir = Path(cache_dir or (motion.Path(__file__).resolve().parent.parent
                                   / "cache" / "footage" / "subjects"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    prompt = SUBJECT_PROMPT.format(subject=description)
    key = _key(prompt)
    cut = cache_dir / f"{key}.png"
    if cut.exists():
        return cut
    raw = cache_dir / f"{key}_raw.png"
    if not raw.exists():
        ark.generate_image(prompt, raw, size=ark.SIZE_SQUARE, seed=seed)
    matted, _mode = matting.auto_cutout(Image.open(raw))
    matted.save(cut, "PNG")
    return cut


def _key(*parts):
    import hashlib
    return hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:16]


# --- drawing ---------------------------------------------------------------

def _fit(image, box_w, box_h):
    """Scale to fit a box, preserving aspect, never upscaling past 2x."""
    w, h = image.size
    k = min(box_w / max(1, w), box_h / max(1, h), 2.0)
    return image.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)


def _card(canvas, box, pal, radius=28):
    """A rounded panel with a soft drop shadow, as the references use."""
    x0, y0, x1, y1 = box
    pad = 26
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [x0 + 6, y0 + 12, x1 + 6, y1 + 12], radius=radius, fill=(0, 0, 0, 120))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(pad)))
    panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    face = tuple(min(255, c + 26) for c in pal["bg"]) + (255,)
    ImageDraw.Draw(panel).rounded_rectangle([x0, y0, x1, y1], radius=radius,
                                            fill=face)
    canvas.alpha_composite(panel)


def _leader(draw, start, end, pal, progress):
    """A thin leader line from a label into the subject, drawing itself on."""
    x0, y0 = start
    x1, y1 = end
    tx = x0 + (x1 - x0) * progress
    ty = y0 + (y1 - y0) * progress
    draw.line([(x0, y0), (tx, ty)], fill=pal["accent"], width=3)
    if progress > 0.92:
        r = 7
        draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=pal["accent"])


def frame(spec, size, t, grade="clean", subject_image=None,
          drift=motion.DRIFT):
    """One RGB frame of a composite shot at progress t."""
    pal = motion.palette(grade)
    W, H = size
    pad = max(0, int(drift))
    CW, CH = W + 2 * pad, H + 2 * pad

    canvas = motion.backdrop((CW, CH), pal["bg"]).convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    layout = spec.get("layout") if spec.get("layout") in LAYOUTS else "subject_left"

    top = int(CH * motion.SAFE_TOP)
    floor = int(CH * motion.layout_floor(CH))
    band = floor - top

    # Every side layout reserves half the frame for the subject. With no
    # subject to put there - generation refused the prompt, or matting ate the
    # whole image - that half stays empty and the shot is a headline floating
    # in a void, which is worse than the flat full-frame photo this module
    # replaced. A composite with nothing to composite is a title card, so
    # centre the type and own it.
    if subject_image is None:
        layout = "subject_center"

    # --- where each layer lives -------------------------------------------
    # Subject boxes are deliberately large. Measured, a reference frame is
    # 30-61% content and the first pass here was 6-12%: the subject sat at
    # about 30% of frame width where the references put it at 40-60%, and the
    # rest of the frame was ground. A cut-out that reads as an illustration
    # rather than a subject is the whole failure this module exists to fix.
    if layout == "subject_center":
        subj_box = (int(CW * 0.22), top + int(band * 0.28),
                    int(CW * 0.78), floor)
        text_x, text_anchor = CW // 2, "mm"
        # 0.11 of the band leaves room for the subject underneath. With no
        # subject there is nothing underneath, so the same offset pins the type
        # to the ceiling above two thirds of empty frame; centre it instead.
        text_y = top + int(band * (0.11 if subject_image is not None else 0.42))
    elif layout == "card":
        subj_box = (int(CW * 0.48), top + int(band * 0.06),
                    int(CW * 0.96), top + int(band * 0.92))
        text_x, text_anchor = int(CW * 0.06), "lm"
        text_y = top + int(band * 0.42)
    elif layout == "subject_right":
        subj_box = (int(CW * 0.48), top + int(band * 0.02),
                    int(CW * 0.98), floor)
        text_x, text_anchor = int(CW * 0.05), "lm"
        text_y = top + int(band * 0.40)
    else:                                   # subject_left
        subj_box = (int(CW * 0.02), top + int(band * 0.02),
                    int(CW * 0.52), floor)
        text_x, text_anchor = int(CW * 0.95), "rm"
        text_y = top + int(band * 0.40)

    if layout == "card":
        _card(canvas, subj_box, pal)

    # --- subject -----------------------------------------------------------
    placed = None
    if subject_image is not None:
        p = _ease(t / SUBJECT_SETTLE)
        art = _fit(subject_image, subj_box[2] - subj_box[0],
                   subj_box[3] - subj_box[1])
        cx = (subj_box[0] + subj_box[2]) // 2
        cy = (subj_box[1] + subj_box[3]) // 2
        # A short rise into place. Opacity stays at full throughout: a
        # half-transparent photograph does not read as an arrival, it reads as
        # a rendering fault, and it leaves the first frame after the cut empty.
        oy = int((1.0 - p) * CH * 0.024)
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(art, (cx - art.width // 2, cy - art.height // 2 + oy), art)
        canvas.alpha_composite(layer)
        placed = (cx, cy + oy, art.width, art.height)

    # --- headline ----------------------------------------------------------
    text_bottom = top
    head = (spec.get("headline") or "").strip()
    if head:
        hp = _ease(t / HEADLINE_SETTLE)
        size_px = max(30, int(W * HEADLINE))
        head_y = text_y + int((1.0 - hp) * CH * 0.018)
        # The column has to stop short of whatever sits beside it. In the
        # card layout the panel starts at 0.52, so a 0.46 column running
        # from 0.08 ran underneath it.
        column = int(CW * {"subject_center": 0.80, "card": 0.38}.get(layout, 0.40))
        bottom = _draw_text(canvas, (text_x, head_y), head, size_px,
                            tuple(pal["ink"]) + (255,), text_anchor,
                            max_width=column)
        text_bottom = bottom
        sub = (spec.get("sub") or "").strip()
        if sub:
            sub_px = max(18, int(W * SUBLINE))
            text_bottom = _draw_text(
                canvas, (text_x, bottom + int(sub_px * 0.9)), sub, sub_px,
                tuple(pal["dim"]) + (255,), text_anchor[0] + "t",
                max_width=column)

    # --- annotation --------------------------------------------------------
    note = (spec.get("note") or "").strip()
    if note and placed:
        np_ = _window(t, NOTE_IN)
        if np_ > 0.01:
            cx, cy, aw, ah = placed
            # The label goes in the subject's OWN upper-outer corner, not
            # beside the subject. Placing it beside meant offsetting from the
            # subject towards the rest of the frame - and the rest of the frame
            # is exactly where the headline column is, so the annotation landed
            # on the headline in every layout. It also ran off the edge when
            # the subject was wide. The subject box and the text column never
            # overlap by construction, so anchoring inside the subject is in
            # frame and clear of the type for all four layouts.
            outward = -1 if layout in ("subject_right", "card") else 1
            edge = cx + outward * (aw // 2)
            lx = edge - outward * int(CW * 0.02)
            note_px = max(18, int(W * NOTE))
            ly = cy - int(ah * 0.34)
            if layout == "subject_center":
                # The only layout that stacks its type ABOVE the subject, so
                # the only one where a note placed high in the subject box
                # collides with it. Clamped against the type's MEASURED bottom
                # - clamping against the headline's position instead still
                # overlapped, because the English sub-line hangs below it.
                ly = max(ly, text_bottom + int(CH * 0.035) + note_px)
            _leader(draw, (lx, ly), (cx, cy - int(ah * 0.05)), pal, np_)
            if np_ > 0.35:
                _draw_text(canvas, (lx, ly - int(CH * 0.035)), note, note_px,
                           tuple(pal["accent"]) + (255,),
                           "lm" if outward < 0 else "rm",
                           max_width=int(CW * 0.24), plate=pal["bg"])

    out = canvas.convert("RGB")
    if not pad:
        return out
    x = min(max(int(round(pad * (1.0 - math.cos(math.pi * t)))), 0), 2 * pad)
    y = min(max(int(round(pad * (1.0 - math.cos(math.pi * t * 0.62)))), 0), 2 * pad)
    return out.crop((x, y, x + W, y + H))


def _draw_text(canvas, xy, body, size, fill, anchor, max_width, plate=None):
    """Wrapped text with a soft shadow. Returns the block's bottom edge.

    Returning the bottom is not a convenience. Placing a second line at a fixed
    multiple of the first line's SIZE assumes the first is one line, and this
    is the third place in this codebase where that assumption produced two
    pieces of text drawn on top of each other - the bilingual caption, the
    opening hook, and here. Callers stack off the measured bottom.

    `plate` puts a rounded chip of that colour behind the block. The annotation
    is drawn over the subject itself, so whether a drop shadow is enough to
    read it depends entirely on what the image model happened to put there - a
    label over a bright machine panel was unreadable. A chip does not depend on
    the picture.
    """
    lines = textkit.wrap(body, size, max_width)
    if not lines:
        return xy[1]
    lh = textkit.line_height(size, True)
    gap = int(size * 0.18)
    total = lh * len(lines) + gap * (len(lines) - 1)
    x, y = xy
    top = y - total // 2 if anchor.endswith("m") else y
    font = textkit.font(size, True)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    align = {"l": "la", "r": "ra", "m": "ma"}[anchor[0]]
    if plate is not None:
        widest = int(max(textkit.advance(line, size, True) for line in lines))
        px = {"l": (x, x + widest), "r": (x - widest, x),
              "m": (x - widest // 2, x + widest // 2)}[anchor[0]]
        mx, my = int(size * 0.42), int(size * 0.30)
        pen.rounded_rectangle([px[0] - mx, top - my, px[1] + mx, top + total + my],
                              radius=int(size * 0.34),
                              fill=tuple(plate) + (216,))
    for i, line in enumerate(lines):
        ly = top + i * (lh + gap)
        pen.text((x + 3, ly + 4), line, font=font, fill=(0, 0, 0, 150),
                 anchor=align)
        pen.text((x, ly), line, font=font, fill=fill, anchor=align)
    canvas.alpha_composite(layer)
    return top + total


# --- driver ----------------------------------------------------------------

def render(spec, out_path, seconds, size, fps=30, grade="clean",
           cache_dir=None):
    """Render a composite shot to its own clip."""
    art = None
    if spec.get("subject"):
        art = Image.open(subject_png(spec["subject"], cache_dir)).convert("RGBA")

    W, H = size
    frames = max(1, int(round(seconds * fps)))
    chain = ["setsar=1"]
    if motion.TEXTURE.get(grade):
        chain.append(motion.TEXTURE[grade])
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
            t = i / max(1, frames - 1)
            proc.stdin.write(
                frame(spec, size, t, grade, subject_image=art).tobytes())
    finally:
        proc.stdin.close()
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg exited {code} rendering a composite")
    return out_path
