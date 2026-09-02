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

from PIL import Image

import render as render_mod
import styles as styles_mod
from layout import from_video as layout_from_video

import pyJianYingDraft as jy
from pyJianYingDraft import (AudioSegment, ClipSettings, ScriptFile, TextBorder,
                             TextIntro, TextSegment, TextStyle, Timerange,
                             TrackSpec, TrackType, TransitionType, VideoMaterial,
                             VideoSegment)

SEC = 1_000_000

# Records the draft_content.json this exporter wrote, so a later run can tell
# its own output apart from one a person has since edited.
STAMP = ".exported"


def _us(seconds):
    return int(round(float(seconds) * SEC))


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
        self.panel_color = self.sb.get("panel_color")
        self.look = styles_mod.look(carried=video.get("look"))
        self.text_config = self.look.get("text") or {}
        self._frames = {}
        self._lanes = {}
        self._warned = set()

    # --- materials --------------------------------------------------------

    def _frame_for(self, el, framing):
        """One element, centred in its own canvas-sized transparent frame.

        Returns (path, dx, dy): the material, and how far that frame has to
        move, in whole pixels.
        """
        img = render_mod.build_element_image(el, self.assets, self.lay, framing,
                                             self.panel_color)
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
        key = (ident, kind, el.get("tone"), el.get("tail"), img.width, img.height)
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
        # Above the picture, below the subtitles: emphasis text belongs over
        # the characters and under the caption, which is where a person editing
        # this would expect to find it.
        script.append_track(TrackSpec(TrackType.audio, "配音"))

        self._add_background(script, segments)
        self._add_elements(script, segments)
        self._add_voice(script, segments)
        self._add_sfx(script)
        self._add_subtitles(script)

        content = self.dir / "draft_content.json"
        script.dump(str(content))
        self._write_meta()
        (self.dir / STAMP).write_text(_digest(content), encoding="utf-8")
        return self.dir, total, layers

    def _add_background(self, script, segments):
        """The plate under the shots, and the black cards either side of them."""
        body_start = body_end = None
        for seg in segments:
            if seg.kind == "scene":
                if body_start is None:
                    body_start = seg.start
                body_end = seg.end
                continue
            card = self._card_png(seg.data, seg.kind)
            script.add_segment(
                VideoSegment(VideoMaterial(str(card)),
                             Timerange(_us(seg.start), _us(seg.duration))),
                "背景")
        if body_start is None:
            return
        plate = self.project / self.sb["video"].get("background", "background.png")
        script.add_segment(
            VideoSegment(VideoMaterial(str(plate)),
                         Timerange(_us(body_start), _us(body_end - body_start))),
            "背景")

    def _add_elements(self, script, segments):
        previous = {}
        for seg in segments:
            if seg.kind != "scene":
                continue
            framing = render_mod.FRAMING.get(seg.data.get("framing", "medium"), 1.0)
            for i, el in enumerate(seg.data.get("elements", [])):
                if self._add_native_text(script, el, seg):
                    continue
                built = self._frame_for(el, framing)
                if built is None:
                    continue
                path, dx, dy = built
                track = f"图层{i + 1}"
                clip = ClipSettings(transform_x=dx / (self.W / 2),
                                    transform_y=-dy / (self.H / 2),
                                    alpha=float(el.get("opacity", 1.0)))
                segment = VideoSegment(
                    VideoMaterial(str(path)),
                    Timerange(_us(seg.start), _us(seg.duration)),
                    clip_settings=clip)
                # A dissolve is only meaningful where two segments actually
                # touch on the same lane - Jianying has nothing to blend across
                # a gap, and the renderer's own dissolve is what this mimics.
                last = previous.get(track)
                if self.dissolve and last and abs(last[1] - seg.start) < 1e-6:
                    last[0].add_transition(TransitionType.叠化)
                script.add_segment(segment, track)
                previous[track] = (segment, seg.end)

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

        tone = el.get("tone", "neutral")
        colour = self.look["label_tones"].get(tone,
                                              self.look["label_tones"]["neutral"])
        # Jianying's own imported subtitles use size 5, which is the only fixed
        # point available for this scale. A label is sized against the caption
        # in pixels, so the same ratio carries over.
        caption_px = max(1, self.lay.subtitle_font_px())
        size = 5.0 * self.lay.label_font_px(el.get("size", 1.0)) / caption_px

        image = render_mod.build_element_image(el, self.assets, self.lay)
        left, top = render_mod.element_origin(el, image, self.lay)
        dx = (left + image.width / 2) - self.W / 2
        dy = (top + image.height / 2) - self.H / 2

        segment = TextSegment(
            text, Timerange(_us(seg.start), _us(seg.duration)),
            style=TextStyle(size=size, align=1, bold=True,
                            color=tuple(c / 255 for c in colour)),
            border=TextBorder(color=(1.0, 1.0, 1.0), width=28.0),
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
            script, TrackType.text, "文字", _us(seg.start), _us(seg.end)))
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
            if seg.kind != "scene":
                continue
            entry = index.get(str(seg.data.get("id", seg.index + 1)))
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
            length = min(_us(seg.duration),
                         _us(entry.get("duration", seg.duration)),
                         material.duration)
            script.add_segment(
                AudioSegment(material, Timerange(_us(seg.start), length)), "配音")

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
        for when, _name, path in cues:
            material = jy.AudioMaterial(str(path))
            start = _us(when)
            script.add_segment(
                AudioSegment(material, Timerange(start, material.duration),
                             volume=gain),
                self._lane(script, TrackType.audio, "音效",
                           start, start + material.duration))

    def _add_subtitles(self, script):
        """Captions as a real subtitle track, not baked pixels.

        This is the one piece of text worth handing to Jianying natively: the
        SRT is already cut to the same spans the renderer used, and import_srt
        reproduces Jianying's own subtitle styling, so it looks native and
        stays editable.
        """
        srt = next(iter(sorted(self.project.glob("*.srt"))), None)
        if srt is not None:
            script.import_srt(str(srt), "字幕")

    def _write_meta(self):
        """Jianying needs a meta file beside the content to list the draft."""
        template = Path(jy.__file__).parent / "assets" / "draft_meta_info.json"
        meta = json.loads(template.read_text(encoding="utf-8-sig"))
        meta["draft_name"] = self.name
        meta["draft_fold_path"] = str(self.dir)
        meta["draft_root_path"] = str(self.dir.parent)
        (self.dir / "draft_meta_info.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def jianying_drafts_dir():
    """Where Jianying keeps its projects on Windows, if it is installed."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    guess = (Path(local) / "JianyingPro" / "User Data" / "Projects"
             / "com.lveditor.draft")
    return guess if guess.is_dir() else None


def main():
    ap = argparse.ArgumentParser(
        description="export a built project as an editable Jianying draft")
    ap.add_argument("project", help="a directory holding storyboard.json")
    ap.add_argument("--out", help="where to write the draft folder "
                                  "(default: <project>/jianying)")
    ap.add_argument("--name", help="draft name, as shown in Jianying")
    ap.add_argument("--install", action="store_true",
                    help="write straight into Jianying's own drafts folder")
    ap.add_argument("--no-dissolve", action="store_true",
                    help="omit the cross-dissolves between shots")
    args = ap.parse_args()

    builder = DraftBuilder(args.project, name=args.name,
                           dissolve=not args.no_dissolve)
    if args.install:
        root = jianying_drafts_dir()
        if root is None:
            raise SystemExit("Jianying's drafts folder was not found - "
                             "pass --out to choose a location instead")
    else:
        root = Path(args.out) if args.out else Path(args.project) / "jianying"

    path, total, layers = builder.build(root)
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6
    print(f"draft written: {path}")
    print(f"  {total:.1f}s, {layers} element layers, {size:.0f} MB")
    if not args.install:
        print("  move this folder into Jianying's drafts directory, or re-run "
              "with --install")


if __name__ == "__main__":
    main()
