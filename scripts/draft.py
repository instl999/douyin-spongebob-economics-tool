"""Export a built project as a Jianying (剪映) draft that can still be edited.

Rendering to MP4 freezes every decision. This writes the same timeline as a
Jianying project instead, so a shot that needs its character moved 40px left is
a drag in the editor rather than another full run of the pipeline. The MP4
becomes the preview; this is the deliverable.

Two facts about Jianying's coordinate system shape this file.

`transform_x`/`transform_y` are in units of half the canvas, y positive upward.
That is documented, and it checks out from the other side: Jianying's own
imported subtitles sit at transform_y = -0.8, which is 90% of the way down the
frame, and this pipeline independently settled on 90.3% for its captions.

`scale` is documented nowhere, and the two plausible readings - fit the
material inside the canvas, or fill the canvas with it - disagree for any
material whose aspect ratio is not the canvas's. Guessing wrong would mis-size
every element in the draft, and Jianying is not installed here to settle it. So
this exporter never depends on it: every element is written out centred in its
own canvas-sized transparent frame. A canvas-aspect material fits and fills
identically, so scale 1.0 means the same thing under either reading, and each
element lands on exactly the pixel the renderer would have chosen.

The cost is that a layer's selection box in Jianying is the whole canvas rather
than the artwork. Dragging and scaling still behave normally - the element sits
at the centre of its own frame, so it scales about itself - but the handles sit
further out than the picture. That is the price of being certain about
placement without being able to test, and it is the right way round: a wrong
scale is a broken draft, oversized handles are a mild annoyance.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: F401  UTF-8 stdout; see console.py

import audio as audio_mod

from PIL import Image

import render as render_mod
import styles as styles_mod
import textkit
from layout import from_video as layout_from_video

import pyJianYingDraft as jy
from pyJianYingDraft import (AudioSegment, ClipSettings, IntroType,
                             KeyframeProperty, ScriptFile, TextBorder,
                             TextIntro, TextSegment, TextStyle, Timerange,
                             TrackSpec, TrackType, TransitionType, VideoMaterial,
                             VideoSegment)

SEC = 1_000_000

# Records the draft_content.json this exporter wrote, so a later run can tell
# its own output apart from one a person has since edited.
STAMP = ".exported"

# Jianying's own size for imported subtitles, and the fixed point every other
# text size here is scaled against. The stroke is Jianying's default
# thickness: the MP4's captions are white on a black edge, and an imported
# subtitle with no edge at all was unreadable on a bright plate.
SUBTITLE_SIZE = 5.0
SUBTITLE_BORDER = 40.0

# The track the portrait title bar sits on: above every element layer, never
# pushed in with the shot under it, and left out of the camera-move check.
CHROME_TRACK = "标题栏"


def _us(seconds):
    return int(round(float(seconds) * SEC))


def _span(start, end):
    """A Jianying timerange from two boundaries, never from a length.

    Rounding a start and a duration separately lets one segment end a
    microsecond after the next begins, and Jianying rejects the whole track for
    overlapping. It only shows on durations that are not round numbers, which
    is every duration once the video runs at a speed like 1.5x: shot 1 ran to
    4498667 while shot 2 began at 4498666, and the export died on a video that
    had just rendered perfectly.

    So the boundaries are rounded, once each, and the length is their
    difference - which also guarantees consecutive segments meet exactly.
    """
    a, b = _us(start), _us(end)
    return Timerange(a, max(0, b - a))


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _free_name(root, name):
    """Where to write, without destroying a draft somebody has worked on.

    The build re-exports on every run, and this is the deliverable: if it has
    been opened in Jianying and adjusted, overwriting it throws that away
    silently, which is the worst way to lose work. A draft this exporter wrote
    and nobody has touched is replaced in place; anything else gets the next
    free name beside it.
    """
    target = root / name
    stamp, content = target / STAMP, target / "draft_content.json"
    if not content.exists():
        return target
    if stamp.exists() and stamp.read_text(encoding="utf-8-sig").strip() == _digest(content):
        return target
    for n in range(2, 100):
        candidate = root / f"{name}_v{n}"
        if not (candidate / "draft_content.json").exists():
            print(f"  ! {target.name} has been edited since it was exported - "
                  f"writing {candidate.name} instead")
            return candidate
    raise SystemExit(f"too many drafts under {root}; delete some")


class DraftBuilder:
    def __init__(self, project_dir, name=None, dissolve=True):
        self.project = Path(project_dir).resolve()
        storyboard = self.project / "storyboard.json"
        if not storyboard.exists():
            raise SystemExit(f"no storyboard.json in {self.project} - build it first")
        self.sb = json.loads(storyboard.read_text(encoding="utf-8-sig"))
        self.name = name or self.project.name
        self.dissolve = dissolve

        video = self.sb.get("video", {})
        self.W = int(video.get("width", 1920))
        self.H = int(video.get("height", 1080))
        self.fps = int(video.get("fps", 30))
        self.lay = layout_from_video(video)
        self.assets = render_mod.Assets(self.project)
        self.panel_color = render_mod.panel_color_of(self.sb)
        self.look = styles_mod.look(carried=video.get("look"))
        # The one duration in this file that is not read off a segment. The
        # storyboard's `look` has already been put on the video's clock by
        # build.py, so its element fade is the right ceiling here too - a flat
        # 0.3 s was a third of a short shot at 1.5x.
        self.element_fade = float(
            (self.look.get("timing") or {}).get("element_fade", 0.28))
        self.text_config = self.look.get("text") or {}
        self._frames = {}
        self._lanes = {}
        self._moved = set()
        self._warned = set()

    # --- materials --------------------------------------------------------

    def _frame_for(self, el, framing, bare=False):
        """One element, centred in its own canvas-sized transparent frame.

        Returns (path, dx, dy): the material, and how far that frame has to
        move, in whole pixels. `bare` is a balloon without its words.
        """
        img = render_mod.build_element_image(el, self.assets, self.lay, framing,
                                             self.panel_color, bare=bare)
        if img is None:
            return None
        # The renderer's own placement, not a second copy of it - see
        # render.element_origin for what a second copy cost.
        left, top = render_mod.element_origin(el, img, self.lay)
        # Offset of the frame, not of the element's centre. Both describe the
        # same move, but the centre of an odd-width sprite falls on a half
        # pixel, and that half pixel rounds one way here and the other way
        # against the frame's own floor-divided paste - a 1px slip that showed
        # up as a portrait shot 2.36/255 away from the render. Whole pixels
        # throughout, and the transform is exact.
        fx, fy = (self.W - img.width) // 2, (self.H - img.height) // 2
        dx, dy = left - fx, top - fy

        kind = el.get("type", "sprite")
        ident = el.get("asset") if kind == "sprite" else f"{kind}:{el.get('text', '')}"
        # Exact size, not a rounded bucket. Sharing a material between two
        # elements that render a couple of pixels apart saves a file and makes
        # the offsets above wrong for whichever one did not build it.
        key = (ident, kind, el.get("tone"), el.get("tail"), bare,
               img.width, img.height)
        path = self._frames.get(key)
        if path is None:
            stem = Path(ident).stem if kind == "sprite" else kind
            path = self.media / f"{len(self._frames):03d}_{stem}.png"
            frame = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
            frame.alpha_composite(img, (fx, fy))
            frame.save(path, "PNG")
            self._frames[key] = path
        return path, dx, dy

    def _card_png(self, card, stem):
        rgb = render_mod.compose_card(card, self.lay, self.assets)
        path = self.media / f"card_{stem}.png"
        Image.fromarray(rgb).save(path, "PNG")
        return path

    # --- build ------------------------------------------------------------

    def build(self, out_root):
        self.dir = _free_name(Path(out_root).resolve(), self.name)
        self.media = self.dir / "materials"
        self.media.mkdir(parents=True, exist_ok=True)

        script = ScriptFile(self.W, self.H, self.fps, False)
        scenes = self.sb.get("scenes", [])
        durations = [float(s.get("duration", 3.0)) for s in scenes]
        segments, total = render_mod.build_timeline(self.sb, durations)

        # One track per simultaneous element, so nothing has to share a lane.
        # The storyboard's element order is already depth-sorted, so element i
        # of every shot belongs on the same layer, and the layers stack in the
        # order the renderer composites them.
        layers = max([len(s.get("elements", [])) for s in scenes] or [1])
        script.append_track(TrackSpec(TrackType.video, "背景"))
        for i in range(layers):
            script.append_track(TrackSpec(TrackType.video, f"图层{i + 1}"))
        chrome = render_mod.chrome_for(self.sb, self.lay)
        if chrome is not None:
            script.append_track(TrackSpec(TrackType.video, CHROME_TRACK))
        # Above the picture, below the subtitles: emphasis text belongs over
        # the characters and under the caption, which is where a person editing
        # this would expect to find it.
        script.append_track(TrackSpec(TrackType.audio, "配音"))

        self._add_background(script, segments)
        self._add_elements(script, segments)
        if chrome is not None:
            self._add_chrome(script, segments, chrome)
        self._add_voice(script, segments)
        self._add_sfx(script)
        self._add_music(script, segments)
        self._add_subtitles(script)

        content = self.dir / "draft_content.json"
        script.dump(str(content))
        self._write_meta()
        (self.dir / STAMP).write_text(_digest(content), encoding="utf-8")
        return self.dir, total, layers

    def _add_background(self, script, segments):
        """The plate under the shots, and the black cards either side of them."""
        # Only whether the body exists matters here; the plate is laid one
        # segment per shot below, so the body's end was left over from when it
        # was a single span and nothing has read it since.
        body_start = None
        for seg in segments:
            if seg.kind == "scene":
                if body_start is None:
                    body_start = seg.start
                continue
            card = self._card_png(seg.data, seg.kind)
            piece = VideoSegment(VideoMaterial(str(card)),
                                 _span(seg.start, seg.end))
            # The MP4 brushes the title on left to right over `title_wipe`;
            # Jianying's own wipe intro is the same move, and stays editable.
            wipe = float((self.look.get("timing") or {}).get("title_wipe", 0))
            if seg.kind == "title" and wipe > 0:
                piece.add_animation(IntroType.画面擦除, _us(wipe))
            script.add_segment(piece, "背景")
        if body_start is None:
            return
        plate = VideoMaterial(str(
            self.project / self.sb["video"].get("background", "background.png")))
        # One plate segment per shot rather than one for the whole body. The
        # picture is identical either way; what it buys is a camera move that
        # belongs to a shot, since a push-in across a single 45-second segment
        # would be one continuous 45-second zoom.
        for seg in segments:
            if seg.kind != "scene":
                continue
            piece = VideoSegment(plate, _span(seg.start, seg.end))
            self._push_in(piece, seg, 0.0, 0.0)
            script.add_segment(piece, "背景")

    def _add_elements(self, script, segments):
        previous = {}
        for seg in segments:
            if seg.kind != "scene":
                continue
            framing = render_mod.FRAMING.get(seg.data.get("framing", "medium"), 1.0)
            for i, el in enumerate(seg.data.get("elements", [])):
                bare = False
                if self._add_native_text(script, el, seg):
                    if el.get("type") != "bubble":
                        continue
                    # A balloon exported as text alone lost the balloon: the
                    # MP4 had a speech bubble and the draft had words floating
                    # on the plate. The balloon goes on this layer, drawn
                    # empty, and the words sit on it as editable text.
                    bare = True
                built = self._frame_for(el, framing, bare=bare)
                if built is None:
                    continue
                path, dx, dy = built
                track = f"图层{i + 1}"
                clip = ClipSettings(transform_x=dx / (self.W / 2),
                                    transform_y=-dy / (self.H / 2),
                                    alpha=float(el.get("opacity", 1.0)))
                # An element that arrives part-way through starts part-way
                # through here as well. The renderer has faded these in since
                # the beginning and the draft ignored it, which nobody noticed
                # because nothing had ever set `appear`.
                appear = max(0.0, min(float(el.get("appear", 0.0) or 0.0),
                                      seg.duration * 0.8))
                segment = VideoSegment(
                    VideoMaterial(str(path)),
                    _span(seg.start + appear, seg.end),
                    clip_settings=clip)
                if appear > 0:
                    segment.add_fade(_us(min(self.element_fade, appear)), 0)
                self._push_in(segment, seg, clip.transform_x, clip.transform_y,
                              offset=appear)
                # A dissolve is only meaningful where two segments actually
                # touch on the same lane - Jianying has nothing to blend across
                # a gap, and the renderer's own dissolve is what this mimics.
                last = previous.get(track)
                if (self.dissolve and last and not appear
                        and abs(last[1] - seg.start) < 1e-6
                        and seg.data.get("transition") != "cut"):
                    last[0].add_transition(TransitionType.叠化)
                script.add_segment(segment, track)
                previous[track] = (segment, seg.end)

    def _add_chrome(self, script, segments, chrome):
        """The title bar over every shot, as one still on a track of its own.

        One segment from the first shot to the last, so it neither blinks at
        a cut nor rides a push-in: it is the frame's furniture, not a layer of
        any shot.
        """
        shots = [seg for seg in segments if seg.kind == "scene"]
        if not shots:
            return
        path = self.media / "chrome_title_bar.png"
        chrome.save(path, "PNG")
        script.add_segment(
            VideoSegment(VideoMaterial(str(path)),
                         _span(shots[0].start, shots[-1].end)),
            CHROME_TRACK)

    def _push_in(self, segment, seg, transform_x, transform_y, offset=0.0):
        """A slow zoom across one shot, or nothing. True if it moved.

        Every layer in a shot is a canvas-sized frame, so scaling them all by
        the same factor about the canvas centre *is* a camera move - but only
        if their offsets scale with them. A layer scaled in place stays where
        it was, and the shot pulls apart instead of pushing in. Hence position
        keyframes alongside the scale one, and hence both ends keyframed: a
        keyframed property stops reading the static transform, so a start
        keyframe that was left out would snap the element to the centre.
        """
        motion = self.look.get("motion") or {}
        if not motion.get("enabled", True):
            return False
        if seg.data.get("framing") not in (motion.get("framings") or []):
            return False
        amount = min(float(motion.get("push_in", 0.05)),
                     float(motion.get("max_push", 0.08)))
        if amount <= 0:
            return False
        # The zoom belongs to the *shot*, not to this segment. An element that
        # arrives part-way through has a shorter segment, and keyframing it
        # from 1.0 over its own length would zoom it slower than everything
        # around it - the layers drift apart and the move stops being a camera.
        # It starts at whatever scale the shot has already reached.
        span = max(seg.duration - offset, 1e-6)
        begun = 1.0 + amount * (offset / max(seg.duration, 1e-6))
        ended = 1.0 + amount
        end = _us(span)
        for prop, start_value, end_value in (
                (KeyframeProperty.uniform_scale, begun, ended),
                (KeyframeProperty.position_x,
                 transform_x * begun, transform_x * ended),
                (KeyframeProperty.position_y,
                 transform_y * begun, transform_y * ended)):
            segment.add_keyframe(prop, 0, start_value)
            segment.add_keyframe(prop, end, end_value)
        self._moved.add(seg.data.get("id"))
        return True

    def _add_native_text(self, script, el, seg):
        """Export a label or balloon as real Jianying text. True if it did.

        A drawn label is pixel-exact and completely inert: it cannot be
        retyped, restyled, or made to arrive. As a TextSegment it can do all
        three, and Jianying's own text animations are what "kinetic emphasis"
        means here - 145 of them, chosen by what the label is *for* rather than
        by picking one per label.

        The MP4 keeps the drawn version either way, so the still render, its
        measurements and its checks are untouched by this.
        """
        kind = el.get("type")
        if kind not in ("label", "bubble") or not self.text_config.get(
                "native_labels", True):
            return False
        text = (el.get("text") or "").strip()
        if not text:
            return False

        # Jianying's own imported subtitles use size 5, which is the only fixed
        # point available for this scale. A label is sized against the caption
        # in pixels, so the same ratio carries over.
        caption_px = max(1, self.lay.subtitle_font_px())
        image = render_mod.build_element_image(el, self.assets, self.lay)
        left, top = render_mod.element_origin(el, image, self.lay)
        if kind == "bubble":
            # The words go on the balloon's body, which is not the middle of
            # its image once the tail hangs below it, and they wrap where the
            # MP4's did. Dark and unoutlined, like the balloon they sit in.
            px = self.lay.label_font_px(el.get("size", render_mod.BUBBLE_SIZE))
            lines, body_w, body_h, _ = textkit.bubble_body(
                text, size=px, max_width=render_mod.text_width(el, self.lay))
            text = "\n".join(lines) or text
            centre = (left + body_w / 2, top + body_h / 2)
            colour, bold, border = textkit.BUBBLE_INK[:3], False, None
        else:
            tone = el.get("tone", "neutral")
            colour = self.look["label_tones"].get(
                tone, self.look["label_tones"]["neutral"])
            px = self.lay.label_font_px(el.get("size", 1.0))
            centre = (left + image.width / 2, top + image.height / 2)
            bold = True
            # Whatever the renderer measured against the plate, so a label
            # that needed a dark edge in the MP4 has one here too.
            border = TextBorder(color=tuple(c / 255 for c in
                                            el.get("outline", (255, 255, 255))),
                                width=28.0)
        size = 5.0 * px / caption_px
        dx, dy = centre[0] - self.W / 2, centre[1] - self.H / 2

        appear = max(0.0, min(float(el.get("appear", 0.0) or 0.0),
                              seg.duration * 0.8))
        segment = TextSegment(
            text, _span(seg.start + appear, seg.end),
            style=TextStyle(size=size, align=1, bold=bold,
                            color=tuple(c / 255 for c in colour)),
            border=border,
            clip_settings=ClipSettings(transform_x=dx / (self.W / 2),
                                       transform_y=-dy / (self.H / 2)))
        animation = (self.text_config.get("animation") or {}).get(
            "bubble" if kind == "bubble" else tone)
        if animation:
            try:
                segment.add_animation(getattr(TextIntro, animation))
            except AttributeError:
                # A name that is not one of Jianying's costs the animation,
                # not the label. The config lists 145 valid ones; a typo in it
                # should not lose the text.
                self._warn(f"unknown text animation {animation!r}, "
                           f"leaving {text!r} static")
        script.add_segment(segment, self._lane(
            script, TrackType.text, "文字",
            _us(seg.start + appear), _us(seg.end)))
        return True

    def _lane(self, script, kind, base, start, end):
        """First track of `base` where [start, end) is free, made if needed.

        A Jianying track holds one segment at a time, and two things here
        routinely want the same instant: a shot with two labels, both spanning
        it, and two sound cues a quarter-second apart when the effect is longer
        than that. Spreading them over parallel lanes is what an editor does;
        the alternatives are dropping one or truncating it.
        """
        lanes = self._lanes.setdefault(base, [])
        for name, free_at in lanes:
            if free_at <= start:
                lanes[lanes.index((name, free_at))] = (name, end)
                return name
        name = base if not lanes else f"{base}{len(lanes) + 1}"
        script.append_track(TrackSpec(kind, name))
        lanes.append((name, end))
        return name

    def _warn(self, message):
        if message not in self._warned:
            self._warned.add(message)
            print(f"  ! {message}", flush=True)

    def _add_voice(self, script, segments):
        index_path = self.project / "voice" / "index.json"
        if not index_path.exists():
            return
        index = json.loads(index_path.read_text(encoding="utf-8-sig"))
        for seg in segments:
            # The title card is voiced too, and its clip starts after the
            # stinger rather than at the card's own start. Skipping every
            # non-scene segment here left the draft's title silent while the
            # rendered MP4 beside it spoke - the two disagreeing is worse than
            # either being wrong, because the draft is the deliverable.
            if seg.kind == "title":
                # The storyboard decides whether the title is spoken at all,
                # and this file does not get a second opinion. A title too long
                # to read before shot 1 keeps its type and loses its voice, and
                # that rule lives in build.py - so consulting the voice index
                # here found the clip still sitting there and spoke a title the
                # MP4 beside it was silent for, truncated into a card sized for
                # no speech. The draft is the deliverable; the two disagreeing
                # is worse than either being wrong.
                #
                # `lead` comes from the same place for the same reason: it is a
                # baseline number that build.py divides by the video's speed,
                # so the constant would put the draft's title 0.45s in while
                # the MP4's sat at 0.30s.
                card = self.sb.get("title_card") or {}
                if not card.get("voice"):
                    continue
                lead = float(card.get("lead", audio_mod.TITLE_SFX_LEAD))
                entry = {"path": card["voice"]}
            elif seg.kind == "scene":
                lead = 0.0
                entry = index.get(str(seg.data.get("id", seg.index + 1)))
            else:
                continue
            if not entry:
                continue
            # Strictly the path the voice stage recorded. A sibling .wav is not
            # the same audio - stale ones from earlier builds sit in the same
            # folder, and one of them was 4.8s against its mp3's 1.9s.
            source = Path(entry["path"])
            if not source.exists():
                continue
            material = jy.AudioMaterial(str(source))
            # The shot is never shorter than its line by design, and a clip
            # that overran would push everything after it out of sync. Clamp to
            # the file as well: a degraded retry can come back short, and
            # asking for more than exists is an error rather than silence.
            slot = _span(seg.start + lead, seg.end)
            length = min(slot.duration,
                         _us(entry.get("duration", seg.duration)),
                         material.duration)
            script.add_segment(
                AudioSegment(material, Timerange(slot.start, length)),
                "配音")

    def _add_sfx(self, script):
        """The same cues the mix uses, on their own track.

        Separate from the narration so they can be muted, moved or replaced
        without touching the voice - which is the first thing anyone wants to
        do with someone else's sound design.
        """
        import sfx as sfx_mod
        cues = sfx_mod.carried(self.sb)
        gain = float((self.look.get("sound") or {}).get("gain", 0.34))
        # Cues can land closer together than an effect is long - a whoosh on
        # the cut and a coin a quarter-second later - and a Jianying track
        # holds one segment at a time. Alternating lanes is what an editor
        # does; the alternative is truncating the sound, which is audible.
        for cue in cues:
            when, path = cue[0], cue[2]
            # A cue may carry its own gain; the draft has to honour the same
            # one the mix used, or the stinger is loud in the MP4 and quiet in
            # the draft beside it.
            level = float(cue[3]) if len(cue) > 3 else gain
            material = jy.AudioMaterial(str(path))
            start = _us(when)
            script.add_segment(
                AudioSegment(material, Timerange(start, material.duration),
                             volume=level),
                self._lane(script, TrackType.audio, "音效",
                           start, start + material.duration))

    def _add_subtitles(self, script):
        """Captions as a real subtitle track, not baked pixels.

        The SRT is already cut to the same spans the renderer used, so the
        timing carries over as it is. The look does not come with it: an
        imported subtitle is plain white with no edge, which is unreadable
        over a bright plate, and it sits at Jianying's default height whatever
        this layout put the caption at. So it is imported against a styled
        template - the MP4's fill, a stroke, bold, and the caption's own line.
        """
        srt = next(iter(sorted(self.project.glob("*.srt"))), None)
        if srt is None:
            return
        caption = self.look.get("caption") or {}
        fill = tuple(c / 255 for c in caption.get("fill", (255, 255, 255)))
        edge = tuple(c / 255 for c in caption.get("stroke_fill", (0, 0, 0)))
        place = ClipSettings(transform_y=-(self.lay.subtitle_center_y - self.H / 2)
                             / (self.H / 2))
        template = TextSegment(
            "字幕", Timerange(0, SEC),
            style=TextStyle(size=SUBTITLE_SIZE, bold=True, align=1, color=fill,
                            auto_wrapping=True,
                            max_line_width=float(
                                self.lay.cfg.get("subtitle_max_width", 0.86))),
            border=TextBorder(color=edge, width=SUBTITLE_BORDER),
            clip_settings=place)
        script.import_srt(str(srt), "字幕", style_reference=template,
                          clip_settings=place)

    def _speech(self, segments):
        """[(start, end)] seconds where narration is heard, from the voice index.

        The same clips `_add_voice` lays, at the same places: the title's
        after its lead, each shot's from the shot's start for as long as the
        clip runs.
        """
        index_path = self.project / "voice" / "index.json"
        index = (json.loads(index_path.read_text(encoding="utf-8-sig"))
                 if index_path.exists() else {})
        card = self.sb.get("title_card") or {}
        spans = []
        for seg in segments:
            if seg.kind == "title" and card.get("voice"):
                lead = float(card.get("lead", audio_mod.TITLE_SFX_LEAD))
                entry = index.get("title") or {}
                length = float(entry.get("duration") or seg.duration - lead)
                spans.append((seg.start + lead,
                              min(seg.end, seg.start + lead + length)))
            elif seg.kind == "scene":
                entry = index.get(str(seg.data.get("id", seg.index + 1))) or {}
                if entry.get("duration") and not entry.get("degraded"):
                    spans.append((seg.start, min(seg.end, seg.start
                                                 + float(entry["duration"]))))
        return spans

    def _add_music(self, script, segments):
        """The beds the mix used, on their own track, ducked where it ducked.

        The storyboard records which beds run where, and `lay_beds` lays
        exactly those.
        """
        music = self.sb.get("music") or {}
        beds = [bed for bed in music.get("beds") or []
                if Path(bed["path"]).exists()]
        if not beds:
            return
        lay_beds(script, beds, float(music.get("volume", audio_mod.BGM_VOLUME)),
                 self._speech(segments) if music.get("duck") else [],
                 lambda a, b: self._lane(script, TrackType.audio, "配乐", a, b))

    def _write_meta(self):
        write_meta(self.dir, self.name)


def write_meta(folder, name):
    """Jianying needs a meta file beside the content to list the draft."""
    template = Path(jy.__file__).parent / "assets" / "draft_meta_info.json"
    meta = json.loads(template.read_text(encoding="utf-8-sig"))
    meta["draft_name"] = name
    meta["draft_fold_path"] = str(folder)
    meta["draft_root_path"] = str(Path(folder).parent)
    (Path(folder) / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def lay_beds(script, beds, level, speech, lane):
    """Music beds as audio segments: looped to their spans, faded, ducked.

    `beds` is [{path, start, end, fade_in, fade_out}] - what the mix was
    given - at `level`. `speech` is [(start, end)] seconds where narration is
    heard, empty for a mix that did not duck. `lane(start_us, end_us)` names
    the track a piece goes on, so two sections overlapping to crossfade can
    sit on parallel lanes.

    Jianying has no sidechain, so the dip under the voice is drawn as volume
    keyframes along the narration the draft carries. Shared by both tracks'
    drafts: the drawn track's sections and the footage track's single bed are
    the same thing laid the same way.
    """
    attack, release = audio_mod.DUCK_ATTACK, audio_mod.DUCK_RELEASE
    under = level * 10 ** (-audio_mod.DUCK_DB / 20)
    # Lines closer together than the compressor can recover between are one
    # dip, as they are in the mix: the bed does not bob up for the tail
    # between two shots and straight back down.
    dips = []
    for a, b in sorted(speech or []):
        if dips and a - attack <= dips[-1][1] + release:
            dips[-1][1] = max(dips[-1][1], b)
        else:
            dips.append([a, b])

    def volume_at(t):
        for a, b in dips:
            if a <= t <= b:
                return under
            if a - attack < t < a:
                return level + (under - level) * (t - (a - attack)) / attack
            if b < t < b + release:
                return under + (level - under) * (t - b) / release
        return level

    def envelope(start, end):
        """[(offset_us, volume)] across one piece of a bed."""
        times = {start, end}
        for a, b in dips:
            times |= {t for t in (a - attack, a, b, b + release)
                      if start < t < end}
        return [(_us(t - start), volume_at(t)) for t in sorted(times)]

    for bed in beds:
        material = jy.AudioMaterial(str(bed["path"]))
        loop = material.duration / SEC
        start, end = float(bed["start"]), float(bed["end"])
        pieces, cursor = [], start
        while end - cursor > 0.05 and loop > 0:
            pieces.append((cursor, min(end, cursor + loop)))
            cursor += loop
        for n, (a, b) in enumerate(pieces):
            piece = AudioSegment(material, _span(a, b),
                                 volume=1.0 if dips else level)
            fade_in = float(bed.get("fade_in", 0)) if n == 0 else 0.0
            fade_out = (float(bed.get("fade_out", 0))
                        if n == len(pieces) - 1 else 0.0)
            if fade_in or fade_out:
                piece.add_fade(_us(min(fade_in, (b - a) / 2)),
                               _us(min(fade_out, (b - a) / 2)))
            if dips:
                for offset, volume in envelope(a, b):
                    piece.add_keyframe(offset, volume)
            script.add_segment(piece, lane(_us(a), _us(b)))


# Where an installed editor keeps its user data, and the folder holding drafts
# inside it. The mainland build is checked before the international one: a
# machine carrying both is a 剪映 user who also has CapCut, not the reverse.
APP_DIRS = ("JianyingPro", "CapCut")
DRAFT_LEAF = "com.lveditor.draft"


def _app_roots():
    """Every directory an installed 剪映 / CapCut keeps its user data in."""
    bases = []
    for variable in ("LOCALAPPDATA", "APPDATA"):
        value = (os.environ.get(variable) or "").strip()
        if value:
            bases.append(Path(value))
    home = Path.home()
    # Windows is the supported platform. The macOS location costs one stat
    # call and turns "not found" into "it just worked" for anyone on one.
    bases += [home / "AppData" / "Local", home / "Movies"]
    roots = []
    for base in bases:
        for app in APP_DIRS:
            candidate = base / app
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
    return roots


def _relocated(app_root):
    """The drafts folder the user moved to, as Jianying itself recorded it.

    Jianying can keep its draft library on another disk, and a machine that
    has moved it still has the default folder sitting there empty - so a probe
    that knows only the default finds a directory, calls it a hit, and writes
    every draft where the editor no longer looks. Nothing about that looks
    wrong: the build succeeds and the draft list stays empty.

    The path is in Jianying's own settings file, under a key that has been
    renamed between versions. Rather than bet on one name, any string value
    naming a directory that exists is taken, keys mentioning drafts first.
    """
    settings = app_root / "User Data" / "Config" / "globalSetting"
    try:
        data = json.loads(settings.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    named = [(key, value) for key, value in data.items()
             if isinstance(value, str) and value.strip()]
    named.sort(key=lambda pair: "draft" not in pair[0].lower())
    for _key, value in named:
        candidate = Path(value.strip()).expanduser()
        # Settings hold the library root; the drafts live in the bundle-id
        # folder under it, and some versions record that folder directly.
        if candidate.name != DRAFT_LEAF:
            candidate = candidate / DRAFT_LEAF
        if candidate.is_dir():
            return candidate
    return None


def jianying_drafts_dir():
    """Where Jianying keeps its projects on this machine, if it is installed.

    JIANYING_DRAFT_DIR overrides the search, for a machine with two installs
    or a layout this does not know. It is checked rather than trusted: a typo
    there is a mistake to report, not a reason to quietly use somewhere else.
    """
    configured = (os.environ.get("JIANYING_DRAFT_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_dir():
            raise SystemExit(f"JIANYING_DRAFT_DIR does not exist: {path}")
        return path
    for root in _app_roots():
        moved = _relocated(root)
        if moved is not None:
            return moved
        default = root / "User Data" / "Projects" / DRAFT_LEAF
        if default.is_dir():
            return default
    return None


def main():
    ap = argparse.ArgumentParser(
        description="export a built project as an editable Jianying draft")
    ap.add_argument("project", help="a directory holding storyboard.json")
    ap.add_argument("--out", help="where to write the draft folder "
                                  "(default: Jianying's own drafts folder, "
                                  "else <project>/jianying)")
    ap.add_argument("--name", help="draft name, as shown in Jianying")
    ap.add_argument("--install", action="store_true",
                    help="deprecated: this is the default. Kept so existing "
                         "commands and scripts keep working")
    ap.add_argument("--no-dissolve", action="store_true",
                    help="omit the cross-dissolves between shots")
    args = ap.parse_args()

    builder = DraftBuilder(args.project, name=args.name,
                           dissolve=not args.no_dissolve)
    # Into the editor's own folder unless somewhere else was asked for. The
    # draft is the deliverable and it is only useful open in Jianying, so the
    # default that made every build end in a manual copy was the wrong one.
    if args.out:
        root = Path(args.out)
    else:
        root = jianying_drafts_dir()
        if root is None:
            if args.install:
                raise SystemExit("Jianying's drafts folder was not found - "
                                 "pass --out to choose a location instead")
            root = Path(args.project) / "jianying"

    path, total, layers = builder.build(root)
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6
    print(f"draft written: {path}")
    print(f"  {total:.1f}s, {layers} element layers, {size:.0f} MB")
    if root == Path(args.project) / "jianying":
        print("  Jianying's drafts folder was not found, so this is beside the "
              "render - move it there, or set JIANYING_DRAFT_DIR")


if __name__ == "__main__":
    main()
