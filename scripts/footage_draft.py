"""The footage track's timeline as an editable Jianying (剪映) draft.

    python scripts/footage_draft.py out/telephone_history

The drawn track has always ended in a draft (`draft.py`); this track ended in
an MP4 alone, so a retrieved shot cut at the wrong moment, a typo in a caption
or a chart that should hold longer meant another full build. This lays out the
timeline the MP4 was assembled from, which `footage_build` records beside it
in `timeline.json`:

    画面      one clip per shot, graded and cut exactly as the MP4 has it
    框架      the frame furniture: baseline, rule track and topic eyebrow
    进度条    the progress rule, sliding in across the whole video
    开场      the opening hook, as text, its translation on 开场英文
    字幕      each beat's Chinese caption, as text
    英文字幕  the English line under it, as text
    配音      each beat's narration where the MP4 has it
    配乐      the bed, faded and ducked under the voice as the mix does

The shots are the prepared clips, not their sources: the grade, the crop and
the speed-up are already in them, which is what makes the draft match the MP4.
Re-cutting a retrieved shot from its source is the editor's job, and the
source is named in shots.json.

Text is placed by the same `footage_render.caption_layout` the burnt-in
captions use and wrapped where they wrapped, so it lands on the MP4's lines.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: F401  UTF-8 stdout; see console.py

from PIL import Image

import draft as draft_mod
import footage_render as fr
import layout as layout_mod
import motion
import styles as styles_mod

from draft import SEC, _span, _us
from pyJianYingDraft import (AudioMaterial, AudioSegment, ClipSettings,
                             KeyframeProperty, ScriptFile, TextBorder,
                             TextSegment, TextStyle, Timerange, TrackSpec,
                             TrackType, TransitionType, VideoMaterial,
                             VideoSegment)

TIMELINE = "timeline.json"

PICTURE = "画面"
FURNITURE = "框架"
RULE = "进度条"
HOOK = "开场"
HOOK_EN = "开场英文"
CAPTIONS = "字幕"
ENGLISH = "英文字幕"
VOICE = "配音"
MUSIC = "配乐"


def read_timeline(out_dir):
    path = Path(out_dir) / TIMELINE
    if not path.exists():
        raise SystemExit(f"no {TIMELINE} in {out_dir} - build it with "
                         f"footage_build.py first")
    return json.loads(path.read_text(encoding="utf-8-sig"))


class FootageDraft:
    def __init__(self, out_dir, name=None):
        self.out = Path(out_dir).resolve()
        self.tl = read_timeline(self.out)
        video = self.tl["video"]
        self.W, self.H = int(video["width"]), int(video["height"])
        self.fps = int(video.get("fps", 30))
        self.lay = layout_mod.Layout(video.get("orientation"), self.W, self.H)
        self.look = styles_mod.look()
        self.name = name or self.tl.get("name") or self.out.name

    def build(self, root):
        self.dir = draft_mod._free_name(Path(root).resolve(), self.name)
        self.media = self.dir / "materials"
        self.media.mkdir(parents=True, exist_ok=True)

        script = ScriptFile(self.W, self.H, self.fps, False)
        for kind, name in ((TrackType.video, PICTURE),
                           (TrackType.video, FURNITURE),
                           (TrackType.video, RULE),
                           (TrackType.text, HOOK),
                           (TrackType.text, HOOK_EN),
                           (TrackType.text, CAPTIONS),
                           (TrackType.text, ENGLISH),
                           (TrackType.audio, VOICE),
                           (TrackType.audio, MUSIC)):
            script.append_track(TrackSpec(kind, name))

        self._add_shots(script)
        self._add_furniture(script)
        self._add_text(script)
        self._add_voice(script)
        self._add_music(script)

        content = self.dir / "draft_content.json"
        script.dump(str(content))
        draft_mod.write_meta(self.dir, self.name)
        (self.dir / draft_mod.STAMP).write_text(draft_mod._digest(content),
                                                encoding="utf-8")
        return self.dir

    # --- picture ------------------------------------------------------------

    def _add_shots(self, script):
        dissolve = float(self.tl["video"].get("dissolve", 0.0))
        shots = self.tl["shots"]
        for i, shot in enumerate(shots):
            material = VideoMaterial(str(self.out / shot["path"]))
            span = _span(shot["start"], shot["end"])
            # A clip rendered to a whole number of frames can come out up to
            # half a frame short of its slot. Asked for more than it holds,
            # Jianying refuses the segment; played a hair slower, it fills the
            # slot and nobody can see the difference.
            source = Timerange(0, min(span.duration, material.duration))
            piece = VideoSegment(material, span, source_timerange=source)
            if dissolve > 0 and i < len(shots) - 1:
                piece.add_transition(TransitionType.叠化, _us(dissolve))
            script.add_segment(piece, PICTURE)

    def _add_furniture(self, script):
        """The baseline and eyebrow as one still, and the rule sliding in.

        The MP4 fills the rule by sliding a strip in from the left over the
        whole video. Here the strip sits in a canvas-sized frame - the same
        placement rule as every other layer in either track's drafts - and
        slides by keyframing its x from one canvas width left of centre to
        centre, which is the same move.
        """
        chrome = self.tl.get("chrome") or {}
        total = float(self.tl["total"])
        base = self.out / chrome["base"] if chrome.get("base") else None
        if base is not None and base.exists():
            script.add_segment(
                VideoSegment(VideoMaterial(str(base)), _span(0.0, total)),
                FURNITURE)
        strip = self.out / chrome["rule"] if chrome.get("rule") else None
        if strip is None or not strip.exists():
            return
        frame = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        with Image.open(strip) as rule:
            frame.alpha_composite(rule.convert("RGBA"),
                                  (0, int(self.H * motion.RULE_Y)))
        path = self.media / "progress_rule.png"
        frame.save(path, "PNG")
        piece = VideoSegment(VideoMaterial(str(path)), _span(0.0, total))
        # Position is in half-canvas units: -2 is one whole width to the left.
        piece.add_keyframe(KeyframeProperty.position_x, 0, -2.0)
        piece.add_keyframe(KeyframeProperty.position_x, _us(total), 0.0)
        script.add_segment(piece, RULE)

    # --- text ---------------------------------------------------------------

    def _add_text(self, script):
        for cap in self.tl.get("captions") or []:
            self._stacked(script, cap, CAPTIONS, ENGLISH)
        hook = self.tl.get("hook")
        if hook:
            self._stacked(script, hook, HOOK, HOOK_EN,
                          center_y=int(self.H * fr.HOOK_CENTRE),
                          scale=fr.HOOK_SCALE, floor=fr.HOOK_FLOOR)

    def _stacked(self, script, entry, track, english_track, **where):
        """One caption's Chinese and English as two text segments.

        Wrapped and placed exactly as `_stacked_caption` set them in the MP4,
        rather than left to Jianying's own wrapping: its line width and this
        one's are different measurements, and a caption that breaks somewhere
        else is a caption that no longer matches the preview.
        """
        start, end = float(entry["start"]), float(entry["end"])
        if end - start < 1.0 / self.fps:
            return
        placed = fr.caption_layout(self.lay, entry["text"],
                                   entry.get("en", ""), **where)
        caption = self.look.get("caption") or {}
        fill = tuple(c / 255 for c in caption.get("fill", (255, 255, 255)))
        edge = tuple(c / 255 for c in caption.get("stroke_fill", (0, 0, 0)))
        lines = [(placed["zh"], track)]
        if placed["en"] is not None:
            lines.append((placed["en"], english_track))
        for (body, px, centre_y), name in lines:
            if not body:
                continue
            size = draft_mod.SUBTITLE_SIZE * px / max(1, self.lay.subtitle_font_px())
            script.add_segment(TextSegment(
                "\n".join(body), _span(start, end),
                style=TextStyle(size=size, bold=True, align=1, color=fill),
                border=TextBorder(color=edge, width=draft_mod.SUBTITLE_BORDER),
                clip_settings=ClipSettings(
                    transform_y=-(centre_y - self.H / 2) / (self.H / 2))),
                name)

    # --- sound --------------------------------------------------------------

    def _speech(self):
        """[(start, end)] where narration is heard, for the ducking."""
        return [(float(v["start"]), float(v["start"]) + float(v["duration"]))
                for v in self._voiced()]

    def _voiced(self):
        """The beats with real narration. A degraded beat is silence."""
        return [v for v in self.tl.get("voice") or []
                if not v.get("degraded") and (self.out / v["path"]).exists()]

    def _add_voice(self, script):
        for clip in self._voiced():
            material = AudioMaterial(str(self.out / clip["path"]))
            start = _us(clip["start"])
            length = min(_us(clip["duration"]), material.duration,
                         _us(self.tl["total"]) - start)
            if length <= 0:
                continue
            script.add_segment(
                AudioSegment(material, Timerange(start, length)), VOICE)

    def _add_music(self, script):
        music = self.tl.get("music") or {}
        beds = [dict(bed, path=str(self._resolve(bed["path"])))
                for bed in music.get("beds") or []
                if self._resolve(bed["path"]).exists()]
        if not beds:
            return
        draft_mod.lay_beds(script, beds, float(music.get("volume", 0.056)),
                           self._speech() if music.get("duck") else [],
                           lambda a, b: MUSIC)

    def _resolve(self, path):
        path = Path(path)
        return path if path.is_absolute() else self.out / path


def export(out_dir, root=None, name=None):
    """Write the draft under `root` (default: beside the MP4). Returns its folder."""
    return FootageDraft(out_dir, name=name).build(
        root or Path(out_dir) / "jianying")


def check(out_dir, draft_dir):
    """What the draft holds, against the timeline it was written from.

    Light by design. The drawn track's check rebuilds frames from its layers;
    here the picture is already-cut clips, so there is no composite to rebuild,
    and what can go wrong quietly is placement - a shot off its boundary, a
    caption dropped, a segment pointing at a file that is not there. All of
    that is in the JSON. Returns [(name, ok, detail)], as verify_output does.
    """
    out_dir = Path(out_dir)
    tl = read_timeline(out_dir)
    content = json.loads((Path(draft_dir) / "draft_content.json")
                         .read_text(encoding="utf-8-sig"))
    fps = int(tl["video"].get("fps", 30))
    frame = SEC // fps
    tracks = {t.get("name"): t.get("segments") or [] for t in content["tracks"]}

    def ranges(name):
        return [(s["target_timerange"]["start"],
                 s["target_timerange"]["start"] + s["target_timerange"]["duration"])
                for s in tracks.get(name, [])]

    rows = []
    laid = ranges(PICTURE)
    want = [(_us(s["start"]), _us(s["end"])) for s in tl["shots"]]
    off = [i + 1 for i, (got, exp) in enumerate(zip(laid, want))
           if abs(got[0] - exp[0]) > frame or abs(got[1] - exp[1]) > frame]
    rows.append(("draft: every shot on its boundary",
                 len(laid) == len(want) and not off,
                 f"{len(laid)}/{len(want)} shots"
                 + (f", off at shot {off[:4]}" if off else "")))

    captions = [c for c in tl.get("captions") or []
                if float(c["end"]) - float(c["start"]) >= 1.0 / fps]
    english = [c for c in captions if c.get("en")]
    rows.append(("draft: captions are editable text",
                 len(tracks.get(CAPTIONS, [])) == len(captions)
                 and len(tracks.get(ENGLISH, [])) == len(english),
                 f"{len(tracks.get(CAPTIONS, []))} Chinese, "
                 f"{len(tracks.get(ENGLISH, []))} English"))

    voiced = [v for v in tl.get("voice") or [] if not v.get("degraded")]
    starts = sorted(a for a, _ in ranges(VOICE))
    late = [s for s, v in zip(starts, voiced) if abs(s - _us(v["start"])) > 1000]
    rows.append(("draft: narration sits under its shots",
                 len(starts) == len(voiced) and not late,
                 f"{len(starts)}/{len(voiced)} clips"))

    total = _us(tl["total"])
    if (tl.get("music") or {}).get("beds"):
        bed = ranges(MUSIC)
        covered = bool(bed) and (min(a for a, _ in bed) <= frame
                                 and max(b for _, b in bed) >= total - frame)
        rows.append(("draft: the bed runs under the whole video", covered,
                     f"{len(bed)} piece(s)"))

    ends = [b for name in tracks for _, b in ranges(name)]
    rows.append(("draft: runs as long as the video",
                 bool(ends) and abs(max(ends) - total) <= frame,
                 f"{(max(ends) if ends else 0) / SEC:.2f}s vs {total / SEC:.2f}s"))

    paths = [m.get("path") for kind in ("videos", "audios")
             for m in content["materials"].get(kind) or []]
    missing = [Path(p).name for p in paths if p and not Path(p).exists()]
    rows.append(("draft: every file it points at exists", not missing,
                 f"{len(paths)} files" + (f", missing {missing[:3]}"
                                          if missing else "")))
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="export a footage-track build as an editable Jianying draft")
    ap.add_argument("out_dir", help="a footage build's output directory")
    ap.add_argument("--out", help="where to write the draft folder (default: "
                                  "Jianying's own drafts folder, else "
                                  "<out_dir>/jianying)")
    ap.add_argument("--name", help="draft name, as shown in Jianying")
    args = ap.parse_args()
    root = (Path(args.out) if args.out
            else draft_mod.jianying_drafts_dir() or Path(args.out_dir) / "jianying")
    path = export(args.out_dir, root=root, name=args.name)
    print(f"draft written: {path}")
    failures = 0
    for name, ok, detail in check(args.out_dir, path):
        failures += 0 if ok else 1
        print(f"  {'[ok]  ' if ok else '[FAIL]'} {name}  {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
