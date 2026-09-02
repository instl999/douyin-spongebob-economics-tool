"""Prove an exported Jianying draft holds the picture it is supposed to.

    python scripts/check_draft.py out/<project>

The draft is written blind: Jianying is not installed here, so nothing about
opening it can be observed. What *can* be observed is whether the numbers in
draft_content.json describe the same frame the renderer would draw. This reads
the draft back with no knowledge of how it was written, rebuilds each shot from
its materials and transforms alone, and compares that against the renderer's
own plate for the same shot.

A pass means the placement maths survives a round trip through the draft
format. It does not mean Jianying agrees about `scale` - see draft.py for why
that question is dodged rather than answered - so this also asserts the thing
that makes it moot: every material a video track uses really does share the
canvas's aspect ratio, the one case where fit and fill are the same transform.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import render as render_mod
import styles as styles_mod
from layout import from_video as layout_from_video

SEC = 1_000_000
# Two frames of the same shot, one rebuilt through the draft and one straight
# from the renderer, differ only by PNG round-tripping. Anything above a couple
# of levels per channel means a transform is wrong, not that a pixel drifted.
MAX_MEAN_DIFF = 2.0


def _us(seconds):
    return int(round(float(seconds) * SEC))


def duration_of(sb):
    """Total seconds the storyboard describes, cards included."""
    durations = [float(s.get("duration", 3.0)) for s in sb.get("scenes", [])]
    _, total = render_mod.build_timeline(sb, durations)
    return total


def _covering(track, t_us):
    for seg in track["segments"]:
        start = seg["target_timerange"]["start"]
        if start <= t_us < start + seg["target_timerange"]["duration"]:
            return seg
    return None


def rebuild(draft_dir, t_us, materials, W, H):
    """The frame the draft describes at `t_us`, composited from scratch."""
    content = json.loads((draft_dir / "draft_content.json").read_text(
        encoding="utf-8"))
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    for track in content["tracks"]:
        if track["type"] != "video":
            continue
        seg = _covering(track, t_us)
        if seg is None:
            continue
        path = materials[seg["material_id"]]
        layer = Image.open(path).convert("RGBA")
        # Every material here shares the canvas's aspect ratio, so "fit inside
        # the canvas" and "fill the canvas" are the same operation and this one
        # line models both. That equivalence is the whole reason the exporter
        # can place things without knowing which one Jianying means; check 1
        # asserts it rather than assuming it.
        if layer.size != (W, H):
            layer = layer.resize((W, H), Image.LANCZOS)
        clip = seg["clip"]
        # transform is in half-canvas units, y positive up; scale is left at 1
        # by construction, and asserted to be so below.
        left = round(clip["transform"]["x"] * W / 2)
        top = round(-clip["transform"]["y"] * H / 2)
        alpha = float(clip.get("alpha", 1.0))
        if alpha < 1.0:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        canvas.alpha_composite(layer, (left, top))
    return np.asarray(canvas.convert("RGB"), dtype=np.int16)


def compare(project, draft_dir):
    """Mean per-pixel difference between the draft's shots and the renderer's.

    Returns None when there is nothing to compare. Kept separate from `main`
    so the build can run it inline the moment it writes a draft, rather than
    relying on anyone to check afterwards.
    """
    project, draft_dir = Path(project).resolve(), Path(draft_dir).resolve()
    sb = json.loads((project / "storyboard.json").read_text(encoding="utf-8-sig"))
    video = sb.get("video", {})
    W, H = int(video.get("width", 1920)), int(video.get("height", 1080))
    lay = layout_from_video(video)
    assets = render_mod.Assets(project)
    background = Image.open(
        project / video.get("background", "background.png")).convert("RGBA")
    if background.size != (W, H):
        background = background.resize((W, H), Image.LANCZOS)
    content = json.loads((draft_dir / "draft_content.json").read_text(
        encoding="utf-8"))
    materials = {m["id"]: m["path"] for m in content["materials"]["videos"]}

    look = styles_mod.look(carried=video.get("look"))
    native_labels = bool((look.get("text") or {}).get("native_labels", True))
    durations = [float(s.get("duration", 3.0)) for s in sb.get("scenes", [])]
    segments, _ = render_mod.build_timeline(sb, durations)
    diffs = []
    for seg in segments:
        if seg.kind != "scene":
            continue
        mid = (seg.start + seg.end) / 2          # away from any dissolve
        got = rebuild(draft_dir, int(mid * SEC), materials, W, H)
        # Labels exported as real Jianying text are not on a video track, so
        # the renderer's plate has to be composed without them or every shot
        # with a label reads as a mismatch. This is the one place the two
        # outputs deliberately differ.
        scene = seg.data
        if native_labels:
            scene = dict(scene, elements=[
                el for el in scene.get("elements", [])
                if el.get("type") not in ("label", "bubble")])
        want = render_mod.compose_plate(scene, background, assets, lay,
                                        sb.get("panel_color"))
        diffs.append((seg.data.get("id", seg.index + 1),
                      float(np.abs(got - np.asarray(want, dtype=np.int16)).mean())))
    return diffs or None


def main():
    ap = argparse.ArgumentParser(description="verify an exported Jianying draft")
    ap.add_argument("project")
    ap.add_argument("--draft", help="draft folder (default: <project>/jianying/*)")
    args = ap.parse_args()

    project = Path(args.project).resolve()
    if args.draft:
        draft_dir = Path(args.draft)
    else:
        # Newest, not first alphabetically: an edited draft is exported beside
        # the old one as <name>_v2, and checking <name> instead would report on
        # a draft nobody asked about.
        candidates = sorted((project / "jianying").glob("*/draft_content.json"),
                            key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise SystemExit("no draft found - run scripts/draft.py first")
        draft_dir = candidates[-1].parent

    sb = json.loads((project / "storyboard.json").read_text(encoding="utf-8-sig"))
    video = sb.get("video", {})
    W, H = int(video.get("width", 1920)), int(video.get("height", 1080))
    content = json.loads((draft_dir / "draft_content.json").read_text(
        encoding="utf-8"))
    materials = {m["id"]: m["path"] for m in content["materials"]["videos"]}

    failures = []

    # 1. every material a video track uses shares the canvas's aspect ratio.
    #    Not its exact size - the plate is generated oversized on purpose - but
    #    the ratio, which is what makes fit and fill the same transform and so
    #    makes scale 1.0 mean one thing instead of two.
    used = {seg["material_id"] for tr in content["tracks"] if tr["type"] == "video"
            for seg in tr["segments"]}
    want_ratio = W / H
    odd = []
    for mid in used:
        with Image.open(materials[mid]) as im:
            if abs(im.width / im.height - want_ratio) > 0.002:
                odd.append(f"{Path(materials[mid]).name} is {im.size}")
    if odd:
        failures.append(f"{len(odd)} material(s) are not canvas-aspect: {odd[:3]}")
    print(f"  {'ok ' if not odd else 'FAIL'} all {len(used)} video materials are "
          f"{want_ratio:.3f}:1 like the canvas, so scale 1.0 is unambiguous")

    # 2. nothing relies on scale
    scaled = [seg["id"] for tr in content["tracks"] if tr["type"] == "video"
              for seg in tr["segments"]
              if abs(seg["clip"]["scale"]["x"] - 1.0) > 1e-6
              or abs(seg["clip"]["scale"]["y"] - 1.0) > 1e-6]
    if scaled:
        failures.append(f"{len(scaled)} segment(s) set a scale")
    print(f"  {'ok ' if not scaled else 'FAIL'} no segment depends on scale")

    # 3. every referenced file is on disk
    missing = [p for p in materials.values() if not Path(p).exists()]
    for mats in (content["materials"].get("audios") or []):
        if not Path(mats["path"]).exists():
            missing.append(mats["path"])
    if missing:
        failures.append(f"{len(missing)} material file(s) missing")
    print(f"  {'ok ' if not missing else 'FAIL'} all "
          f"{len(materials) + len(content['materials'].get('audios') or [])} "
          f"material files exist")

    # 4. the picture itself, shot by shot
    diffs = compare(project, draft_dir) or []
    worst = sorted(diffs, key=lambda r: -r[1])[:3]
    bad = [d for d in diffs if d[1] > MAX_MEAN_DIFF]
    if bad:
        failures.append(f"{len(bad)} shot(s) differ from the renderer: {bad[:3]}")
    print(f"  {'ok ' if not bad else 'FAIL'} {len(diffs)} shots match the renderer "
          f"(mean {np.mean([d for _, d in diffs]):.2f}/255, "
          f"worst shot {worst[0][0]} at {worst[0][1]:.2f})")

    # 5. narration lands on its own shot. A picture that matches the render
    #    while the voice sits half a second out is still a broken draft, and
    #    nothing above would notice.
    durations = [float(s.get("duration", 3.0)) for s in sb.get("scenes", [])]
    shots, _ = render_mod.build_timeline(sb, durations)
    starts = [_us(s.start) for s in shots if s.kind == "scene"]
    # The voice track by name, not every audio track: sound cues live on audio
    # tracks too, and counting those as narration made a correct draft report
    # seven clips 21 seconds out of place.
    audio = [seg["target_timerange"]["start"]
             for tr in content["tracks"]
             if tr["type"] == "audio" and tr.get("name") == "配音"
             for seg in tr["segments"]]
    drift = [abs(a - b) for a, b in zip(sorted(audio), starts)]
    late = [d for d in drift if d > SEC // 100]        # 10ms
    if audio and late:
        failures.append(f"{len(late)} narration clip(s) off their shot by up to "
                        f"{max(late) / SEC:.2f}s")
    print(f"  {'ok ' if audio and not late else '-- '} {len(audio)} narration "
          f"clips, aligned to their shots"
          f"{'' if audio else ' (none - voice stage not run)'}")

    # 6. emphasis text and sound cues, where the config asks for them
    look = styles_mod.look(carried=video.get("look"))
    wants_text = bool((look.get("text") or {}).get("native_labels", True))
    labels = sum(1 for sc in sb.get("scenes", [])
                 for el in sc.get("elements", [])
                 if el.get("type") in ("label", "bubble"))
    exported = [seg for tr in content["tracks"]
                if tr["type"] == "text" and str(tr.get("name", "")).startswith("文字")
                for seg in tr["segments"]]
    if wants_text and labels and len(exported) != labels:
        failures.append(f"{labels} labels in the storyboard but {len(exported)} "
                        "exported as text")
    print(f"  {'ok ' if not (wants_text and labels and len(exported) != labels) else 'FAIL'}"
          f" {len(exported)} of {labels} labels are editable text"
          f"{'' if wants_text else ' (native labels off; drawn instead)'}")

    cues = [seg for tr in content["tracks"]
            if tr["type"] == "audio" and str(tr.get("name", "")).startswith("音效")
            for seg in tr["segments"]]
    late = [seg for seg in cues
            if seg["target_timerange"]["start"] > _us(duration_of(sb))]
    if late:
        failures.append(f"{len(late)} sound cue(s) start after the video ends")
    print(f"  {'ok ' if not late else 'FAIL'} {len(cues)} sound cue(s), "
          f"all inside the video")

    # 7. tracks that should exist
    names = {tr.get("name") for tr in content["tracks"]}
    for wanted, why in (("背景", "background"), ("配音", "narration"),
                        ("字幕", "subtitles")):
        if wanted not in names:
            failures.append(f"no {wanted} ({why}) track")
    print(f"  {'ok ' if len(names) >= 4 else 'FAIL'} tracks: "
          f"{', '.join(sorted(n for n in names if n))}")

    print()
    for line in failures:
        print(f"  FAIL {line}")
    if not failures:
        print("draft verified: it describes the same frames the renderer draws")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
