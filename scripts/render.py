"""Frame renderer: one fixed plate, sprites composited over it, shots dissolved.

This mirrors what the reference videos actually do, which is less than it looks
like. Measuring them frame by frame shows:

* The background is one image, byte-identical across the whole video. Frame
  deltas in character-free regions are 1-2 grey levels, i.e. compression noise.
  There is no parallax, no drifting bubble layer, no camera move.
* A shot holds completely still - consecutive-frame deltas of 0.001-0.05 - for
  several seconds at a time.
* Shots change by dissolving, about 0.5-0.65s, with everything in the shot
  fading as one group. Sprites do not slide, pop or scale in individually.

So the renderer composes each shot once and holds it. Only dissolve frames and
caption changes are unique; every other frame is a buffer the encoder has
already seen. That is what makes a three-minute 1080p render take seconds
rather than the tens of minutes a naive per-frame loop costs, and it is also
simply what the format looks like.

Frames go to ffmpeg's stdin as raw RGB24. Nothing is written to disk.
"""
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

import config
import textkit
from layout import Layout

# How much of the frame the subject fills, per shot. Measured on the
# references, the largest foreground object runs 0.51-0.86 of frame height with
# about 1.4x between the loosest and tightest shots. A run of identically framed
# shots is what makes this format read as a slideshow, so the director picks one
# of these per shot and the renderer scales the sprites accordingly. Only
# sprites scale - labels and balloons are screen furniture and stay put.
FRAMING = {"wide": 0.88, "medium": 1.0, "close": 1.30}

# Label colour carries meaning in the references - the good outcome in green,
# the bad one in red - so it is chosen by naming the meaning, not the colour.
LABEL_TONES = {
    "neutral": (30, 30, 30),
    "good": (22, 122, 58),
    "bad": (183, 40, 30),
    "money": (176, 112, 8),
}

CAPTION_FADE = 0.12       # subtitle swaps inside a held shot
ELEMENT_FADE = 0.28       # for elements that appear part-way through a shot


def smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


class Assets:
    """Loads sprites once and caches every size they get asked for."""

    def __init__(self, asset_dir):
        self.dir = Path(asset_dir)
        self._originals = {}
        self._scaled = {}

    def original(self, name):
        if name not in self._originals:
            path = self.dir / name
            if not path.exists():
                path = Path(name)
            if not path.exists():
                raise FileNotFoundError(f"asset not found: {name} (looked in {self.dir})")
            self._originals[name] = Image.open(path).convert("RGBA")
        return self._originals[name]

    def sized(self, name, height):
        key = (name, int(height))
        if key not in self._scaled:
            img = self.original(name)
            ratio = height / img.height
            self._scaled[key] = img.resize(
                (max(1, round(img.width * ratio)), max(1, int(height))), Image.LANCZOS)
        return self._scaled[key]


# --- element drawing -------------------------------------------------------

_WARNED = set()


def _warn_once(message):
    """Missing sprites are reported once each, not once per frame."""
    if message not in _WARNED:
        _WARNED.add(message)
        print(f"  ! {message} - skipping that element", flush=True)


def element_origin(el, image, lay):
    """Top-left pixel for one element - the single definition of placement.

    The renderer, the layout checks and the draft exporter all have to agree
    about where an element goes, and for a while they did not: each had its own
    copy of this arithmetic with its own default y, one 0.97, one 0.95, one
    0.5. Real storyboards hid it, because the director writes x, y and anchor
    on every element and the defaults never fire. A hand-written shot found it
    immediately, and the draft came out 12/255 away from the render.
    """
    cx, cy = lay.point(el.get("x", 0.5), el.get("y", 0.95))
    anchor = el.get("anchor", "bottom")
    if anchor == "top_left":
        return int(cx), int(cy)
    left = int(cx - image.width / 2)
    if anchor == "bottom":
        return left, int(cy - image.height)
    if anchor == "top":
        return left, int(cy)
    return left, int(cy - image.height / 2)


def _paste(canvas, sprite, origin, opacity=1.0):
    if opacity <= 0.004:
        return
    if opacity < 1.0:
        sprite = sprite.copy()
        alpha = sprite.getchannel("A").point(lambda v: int(v * opacity))
        sprite.putalpha(alpha)
    canvas.alpha_composite(sprite, origin)


def build_element_image(el, assets, lay, framing=1.0, panel_color=None):
    """Return the RGBA image for one element, at final pixel size."""
    kind = el.get("type", "sprite")
    if kind == "label":
        # The references colour labels by meaning, not decoration: green on the
        # gain, red on the loss. A wall of identical dark labels throws that
        # away, so `tone` maps to the colour and the author never picks one.
        tone = LABEL_TONES.get(el.get("tone", "neutral"), LABEL_TONES["neutral"])
        return textkit.render_label(
            el["text"],
            size=lay.label_font_px(el.get("size", 1.0)),
            fill=tuple(el.get("color", tone)) + (255,),
            stroke_fill=tuple(el.get("outline", (255, 255, 255))) + (255,),
            max_width=int(lay.width * el.get("max_width", 0.34)))
    if kind == "panel":
        return textkit.render_panel(
            el.get("w", 0.4) * lay.width, el.get("ph", 0.3) * lay.height,
            fill=tuple(el.get("color", panel_color or (176, 196, 205))),
            alpha=int(el.get("alpha", 235)),
            radius=int(el.get("radius", 0) * lay.width))
    if kind == "bubble":
        return textkit.render_bubble(
            el["text"],
            size=lay.label_font_px(el.get("size", 0.82)),
            max_width=int(lay.width * el.get("max_width", 0.24)),
            tail=el.get("tail", "left"))
    return assets.sized(
        el["asset"],
        lay.sprite_height(el.get("h", 0.4) * framing * el.get("rel", 1.0)))


def compose_plate(scene, background, assets, lay, panel_color=None):
    """Background plus every element of one shot, as an RGB uint8 array.

    Elements that appear part-way through the shot are drawn here at full
    opacity as well; the frame loop fades them in over this plate.
    """
    canvas = background.copy()
    framing = FRAMING.get(scene.get("framing", "medium"), 1.0)
    for el in scene.get("elements", []):
        try:
            img = build_element_image(el, assets, lay, framing, panel_color)
        except FileNotFoundError as exc:
            # One deleted PNG should cost one element, not the whole render -
            # this runs after the storyboard, the narration and the artwork are
            # all paid for.
            _warn_once(str(exc))
            continue
        if img is None:
            continue
        _paste(canvas, img, element_origin(el, img, lay))
    return np.asarray(canvas.convert("RGB"), dtype=np.uint8)


def compose_partial(scene, background, assets, lay, opacities, panel_color=None):
    """Plate with per-element opacity - only used while something is arriving."""
    canvas = background.copy()
    framing = FRAMING.get(scene.get("framing", "medium"), 1.0)
    for el, opacity in zip(scene.get("elements", []), opacities):
        if opacity <= 0.004:
            continue
        try:
            img = build_element_image(el, assets, lay, framing, panel_color)
        except FileNotFoundError as exc:
            _warn_once(str(exc))
            continue
        if img is None:
            continue
        _paste(canvas, img, element_origin(el, img, lay), opacity)
    return np.asarray(canvas.convert("RGB"), dtype=np.uint8)


# --- cards -----------------------------------------------------------------

def compose_card(card, lay, assets=None):
    """Full-frame text on black - the opening title and the closing line."""
    W, H = lay.size
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    image_name = card.get("image")
    if image_name and assets:
        try:
            art = assets.original(image_name)
            ratio = min(W * 0.88 / art.width, H * 0.8 / art.height)
            art = art.resize((int(art.width * ratio), int(art.height * ratio)),
                             Image.LANCZOS)
            canvas.alpha_composite(art, ((W - art.width) // 2, (H - art.height) // 2))
            return np.asarray(canvas.convert("RGB"), dtype=np.uint8)
        except FileNotFoundError:
            pass

    text = card.get("text", "")
    size = int(lay.width * card.get("size", 0.072))
    if card.get("style") == "title":
        # The references open on red lettering with a grey copy behind it.
        fill = tuple(card.get("color", (222, 28, 28))) + (255,)
        offset = (int(size * 0.06), int(size * 0.06))
        offset_fill = (176, 176, 176, 255)
    else:
        fill = tuple(card.get("color", (255, 255, 255))) + (255,)
        offset, offset_fill = None, (150, 150, 150, 255)
    layer = textkit.render_card(
        (W, H), text, size=size, max_width=int(W * 0.84),
        highlight=card.get("highlight"), fill=fill,
        stroke=max(3, size // 18), offset=offset, offset_fill=offset_fill)
    canvas.alpha_composite(layer)
    return np.asarray(canvas.convert("RGB"), dtype=np.uint8)


# --- timeline --------------------------------------------------------------

class Segment:
    __slots__ = ("kind", "start", "end", "data", "index", "pos")

    def __init__(self, kind, start, end, data, index, pos):
        self.kind, self.start, self.end = kind, start, end
        self.data, self.index, self.pos = data, index, pos

    @property
    def duration(self):
        return self.end - self.start


def build_timeline(storyboard, durations):
    """Lay the cards and shots out in time. `durations` is per-scene seconds."""
    segments, t = [], 0.0
    title = storyboard.get("title_card")
    if title:
        d = float(title.get("duration", 2.6))
        segments.append(Segment("title", t, t + d, title, 0, len(segments)))
        t += d
    for i, scene in enumerate(storyboard.get("scenes", [])):
        d = durations[i]
        segments.append(Segment("scene", t, t + d, scene, i, len(segments)))
        t += d
    ending = storyboard.get("ending_card")
    if ending:
        d = float(ending.get("duration", 4.0))
        segments.append(Segment("ending", t, t + d, ending, 0, len(segments)))
        t += d
    return segments, t


def scene_captions(scene, duration):
    """Caption spans for one shot.

    An explicit `captions` list wins. Otherwise the shot's own subtitle is split
    at punctuation and spread across the shot by character count, because a 5s
    shot holds far more text than belongs on screen at once.
    """
    given = scene.get("captions")
    if given:
        return [(float(c.get("start", 0.0)), float(c.get("end", duration)),
                 c.get("text", ""), c.get("highlight")) for c in given]
    text = (scene.get("subtitle") or "").strip()
    if not text:
        return []
    highlight = scene.get("subtitle_highlight")
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。！？；" and len(buf.strip()) >= 8:
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    if len(parts) <= 1:
        return [(0.0, duration, text, highlight)]
    weights = [max(1, len(p)) for p in parts]
    total = sum(weights)
    spans, cursor = [], 0.0
    for part, w in zip(parts, weights):
        span = duration * w / total
        spans.append((cursor, cursor + span, part, highlight))
        cursor += span
    return spans


# --- the frame stream ------------------------------------------------------

class Renderer:
    def __init__(self, storyboard, workdir, lay=None):
        self.sb = storyboard
        self.workdir = Path(workdir)
        cfg = storyboard.get("video", {})
        self.lay = lay or Layout(cfg.get("orientation", "landscape"),
                                 cfg.get("width"), cfg.get("height"))
        self.fps = int(cfg.get("fps", 30))
        self.dissolve = float(cfg.get("dissolve", 0.5))
        # Carried in the storyboard rather than looked up from the cast, so a
        # storyboard renders on its own without the cast file being present.
        self.panel_color = tuple(cfg.get("panel_color") or (176, 196, 205))
        self.assets = Assets(self.workdir)
        bg_name = cfg.get("background", "background.png")
        self.background = self._load_background(bg_name)
        self._plates = {}
        self._captions = {}
        self._frame_cache = {}
        self._caption_spans_cache = {}

    def _load_background(self, name):
        img = self.assets.original(name)
        W, H = self.lay.size
        if img.size != (W, H):
            # Cover the frame, then centre-crop: never letterbox the plate.
            ratio = max(W / img.width, H / img.height)
            img = img.resize((max(W, round(img.width * ratio)),
                              max(H, round(img.height * ratio))), Image.LANCZOS)
            left, top = (img.width - W) // 2, (img.height - H) // 2
            img = img.crop((left, top, left + W, top + H))
        return img.convert("RGBA")

    # --- cached pieces ----------------------------------------------------
    def plate(self, segment):
        key = (segment.kind, segment.index)
        if key not in self._plates:
            if segment.kind == "scene":
                self._plates[key] = compose_plate(
                    segment.data, self.background, self.assets, self.lay,
                    self.panel_color)
            else:
                self._plates[key] = compose_card(segment.data, self.lay, self.assets)
        return self._plates[key]

    def caption_layer(self, text, highlight):
        key = (text, highlight)
        if key not in self._captions:
            layer, bbox = textkit.render_caption(
                self.lay.size, text,
                size=self.lay.subtitle_font_px(),
                center_y=self.lay.subtitle_center_y,
                max_width=self.lay.subtitle_max_px,
                highlight=highlight)
            if layer is None:
                self._captions[key] = (None, None, None)
            else:
                arr = np.asarray(layer, dtype=np.float32)
                y0, y1 = bbox[1], bbox[3]
                self._captions[key] = (arr[y0:y1, :, :3], arr[y0:y1, :, 3:4] / 255.0,
                                       (y0, y1))
        return self._captions[key]

    # --- per-frame --------------------------------------------------------
    def _segment_at(self, t, segments):
        for seg in segments:
            if seg.start <= t < seg.end:
                return seg
        return segments[-1]

    def _element_opacities(self, scene, local_t):
        """None when everything is fully up - the common case, so no work."""
        opacities, partial = [], False
        for el in scene.get("elements", []):
            appear = float(el.get("appear", 0.0) or 0.0)
            if appear <= 0.0:
                opacities.append(1.0)
                continue
            o = smoothstep((local_t - appear) / ELEMENT_FADE)
            opacities.append(o)
            if o < 0.999:
                partial = True
        return opacities if partial else None

    def frame(self, t, segments):
        seg = self._segment_at(t, segments)
        local_t = t - seg.start

        # Dissolve into this segment from the previous one.
        blend = None
        if self.dissolve > 0 and local_t < self.dissolve and seg.pos > 0:
            prev = segments[seg.pos - 1]
            blend = (prev, smoothstep(local_t / self.dissolve))

        opacities = (self._element_opacities(seg.data, local_t)
                     if seg.kind == "scene" else None)

        caption = None
        if seg.kind == "scene":
            spans = self._caption_spans(seg)
            for start, end, text, highlight in spans:
                if start <= local_t < end:
                    fade = min(smoothstep((local_t - start) / CAPTION_FADE),
                               smoothstep((end - local_t) / CAPTION_FADE))
                    caption = (text, highlight, fade)
                    break

        # Hold frames repeat; cache them by everything that can vary.
        cacheable = blend is None and opacities is None and (
            caption is None or caption[2] >= 0.999)
        key = (seg.kind, seg.index, caption[0] if caption else None,
               caption[1] if caption else None)
        if cacheable and key in self._frame_cache:
            return self._frame_cache[key]

        if opacities is not None:
            base = compose_partial(seg.data, self.background, self.assets,
                                   self.lay, opacities, self.panel_color)
        else:
            base = self.plate(seg)

        if blend is not None:
            prev_seg, k = blend
            prev_plate = self.plate(prev_seg)
            base = (prev_plate.astype(np.float32) * (1.0 - k)
                    + base.astype(np.float32) * k).astype(np.uint8)

        if caption is not None:
            base = self._apply_caption(base, *caption)

        buf = base.tobytes()
        if cacheable:
            self._frame_cache[key] = buf
        return buf

    def _caption_spans(self, seg):
        """Caption spans for a shot, cached on the renderer.

        Deliberately not cached on the scene dict: that would add a private
        key to the caller's storyboard, which then shows up in anything that
        serialises it after a render.
        """
        key = (seg.kind, seg.index)
        cached = self._caption_spans_cache.get(key)
        if cached is None:
            cached = scene_captions(seg.data, seg.duration)
            self._caption_spans_cache[key] = cached
        return cached

    def _apply_caption(self, base, text, highlight, fade):
        rgb, alpha, span = self.caption_layer(text, highlight)
        if rgb is None:
            return base
        y0, y1 = span
        a = alpha * fade
        out = base.copy()
        region = out[y0:y1].astype(np.float32)
        out[y0:y1] = (region * (1.0 - a) + rgb * a).astype(np.uint8)
        return out

    # --- driver -----------------------------------------------------------
    def render(self, out_path, durations, progress=None):
        segments, total = build_timeline(self.sb, durations)
        frames = max(1, int(round(total * self.fps)))
        W, H = self.lay.size
        cmd = [
            config.FFMPEG, "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
            "-framerate", str(self.fps), "-i", "-",
            "-an", "-c:v", "libx264", "-preset",
            self.sb.get("video", {}).get("preset", "veryfast"),
            "-crf", str(self.sb.get("video", {}).get("crf", 20)),
            "-pix_fmt", "yuv420p", str(out_path),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for i in range(frames):
                proc.stdin.write(self.frame(i / self.fps, segments))
                if progress and i % self.fps == 0:
                    progress(i, frames)
        finally:
            proc.stdin.close()
            code = proc.wait()
        if code != 0:
            raise RuntimeError(f"ffmpeg exited {code} while encoding video")
        if progress:
            progress(frames, frames)
        return total, frames
