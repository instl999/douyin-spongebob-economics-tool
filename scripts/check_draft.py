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
    """The segment showing at `t_us`, preferring the one that just began.

    A dissolve makes the outgoing clip run past the incoming one's start, so
    two segments genuinely cover that instant. Taking the first in track order
    returns the shot that is on its way out: the check then compared shot 6's
    picture against shot 7's plate and called three working shots broken.
    """
    covering = [seg for seg in track["segments"]
                if seg["target_timerange"]["start"] <= t_us
                < seg["target_timerange"]["start"]
                + seg["target_timerange"]["duration"]]
    if not covering:
        return None
    return max(covering, key=lambda seg: seg["target_timerange"]["start"])


# How a keyframed property is named in the file, and which of ours it drives.
KEYFRAME_PROPERTIES = {
    "KFTypeScaleX": "scale", "KFTypeScaleY": "scale",
    "KFTypePositionX": "x", "KFTypePositionY": "y",
}


def keyframed(segment, offset_us):
    """{scale, x, y} the keyframes say at `offset_us` into a segment.

    Missing entries mean the property is not keyframed and the static clip
    value stands. Interpolation is linear because that is the curve the
    exporter writes ("Line"); anything else here would be describing a
    different video from the one in the file.
    """
    out = {}
    for track in segment.get("common_keyframes") or []:
        name = KEYFRAME_PROPERTIES.get(track.get("property_type"))
        points = sorted(track.get("keyframe_list") or [],
                        key=lambda k: k.get("time_offset", 0))
        if not name or not points:
            continue
        first, last = points[0], points[-1]
        if offset_us <= first["time_offset"]:
            value = first["values"][0]
        elif offset_us >= last["time_offset"]:
            value = last["values"][0]
        else:
            before = max((k for k in points
                          if k["time_offset"] <= offset_us),
                         key=lambda k: k["time_offset"])
            after = min((k for k in points if k["time_offset"] > offset_us),
                        key=lambda k: k["time_offset"])
            span = after["time_offset"] - before["time_offset"]
            k = (offset_us - before["time_offset"]) / span if span else 0.0
            value = (before["values"][0]
                     + k * (after["values"][0] - before["values"][0]))
        out[name] = value
    return out


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
        moving = keyframed(seg, t_us - seg["target_timerange"]["start"])
        # transform is in half-canvas units, y positive up. Static scale is 1
        # by construction and asserted below; a camera push-in expresses itself
        # as keyframes instead, which win where they exist.
        offset_x = moving.get("x", clip["transform"]["x"])
        offset_y = moving.get("y", clip["transform"]["y"])
        scale = moving.get("scale", clip["scale"]["x"])
        if abs(scale - 1.0) > 1e-6:
            layer = layer.resize((max(1, round(W * scale)),
                                  max(1, round(H * scale))), Image.LANCZOS)
        # The layer scales about its own centre, and that centre sits where the
        # offset puts it - which is what makes scaling every layer of a shot by
        # one factor read as a camera move rather than as things growing.
        centre_x = W / 2 + offset_x * W / 2
        centre_y = H / 2 - offset_y * H / 2
        left = round(centre_x - layer.width / 2)
        top = round(centre_y - layer.height / 2)
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
        # The shot's first instant, not its middle. A push-in is keyframed from
        # 1.0 at the start, so this is the one moment where the draft and the
        # renderer's static plate are meant to be identical; sampling mid-shot
        # would compare a zoomed frame against an unzoomed one and call a
        # working camera move a defect.
        # _us, not int(): the exporter rounds and this truncated, so an
        # accumulated float start of 32.9959999s landed one microsecond inside
        # the previous shot and compared shot 6's picture against shot 7's
        # plate. A millisecond further in is clear of the boundary either way,
        # and a push-in has moved by 0.001% of nothing by then.
        got = rebuild(draft_dir, _us(seg.start) + 1000, materials, W, H)
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

    look = styles_mod.look(carried=video.get("look"))

    # 6. the camera move is a camera move.
    #    Scaling every layer of a shot in place is not a push-in: the layers
    #    grow where they stand and the composition pulls apart. It only reads
    #    as a camera if each layer's offset scales with it, which means
    #    position keyframes beside the scale one. That is checkable without
    #    Jianying: the last frame of a moving shot has to be its first frame
    #    enlarged about the canvas centre, and nothing else.
    motion = (look.get("motion") or {})
    ceiling = 1.0 + float(motion.get("max_push", 0.08))
    moving, over, wrong = [], [], []
    for track in content["tracks"]:
        if track["type"] != "video":
            continue
        for seg in track["segments"]:
            scales = [k["values"][0]
                      for kf in seg.get("common_keyframes") or []
                      if kf.get("property_type") in ("KFTypeScaleX", "KFTypeScaleY")
                      for k in kf.get("keyframe_list") or []]
            if not scales:
                continue
            moving.append(seg["target_timerange"]["start"])
            if max(scales) > ceiling + 1e-6 or min(scales) < 1.0 - 1e-6:
                over.append(round(max(scales), 3))

    for start in sorted(set(moving))[:3]:
        span = next(sg["target_timerange"]["duration"]
                    for tr in content["tracks"] if tr["type"] == "video"
                    for sg in tr["segments"]
                    if sg["target_timerange"]["start"] == start
                    and sg.get("common_keyframes"))
        first = rebuild(draft_dir, start + 1000, materials, W, H)
        last = rebuild(draft_dir, start + span - 1000, materials, W, H)
        zoom = 1.0 + float(motion.get("push_in", 0.05))
        grown = Image.fromarray(np.uint8(np.clip(first, 0, 255))).resize(
            (round(W * zoom), round(H * zoom)), Image.LANCZOS)
        left, top = (grown.width - W) // 2, (grown.height - H) // 2
        expect = np.asarray(grown.crop((left, top, left + W, top + H)),
                            dtype=np.int16)
        drift = float(np.abs(last - expect).mean())
        if drift > 6.0:
            wrong.append((round(start / SEC, 2), round(drift, 1)))

    if over:
        failures.append(f"a camera move scales past the {ceiling:.2f} ceiling: {over[:3]}")
    if wrong:
        failures.append(f"a camera move is not a zoom about the centre: {wrong[:3]}")
    print(f"  {'ok ' if not (over or wrong) else 'FAIL'} "
          f"{len(set(moving))} shot(s) push in, within {ceiling:.2f}x and "
          f"about the centre")

    # 7. emphasis text and sound cues, where the config asks for them
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

    # 8. tracks that should exist
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
