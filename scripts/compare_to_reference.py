"""Measure a finished video against the live-action references.

    python scripts/compare_to_reference.py out/telephone/telephone_history.mp4
    python scripts/compare_to_reference.py mine.mp4 --against ref1.mp4 ref2.mp4

"Comparable to the reference videos" is not a feeling. The references were
measured (references/footage-findings.md) and every number there is one this
script reproduces on any file, so a build can be held to them instead of to an
impression.

What it does NOT measure is whether the shots mean anything - whether the
picture matches what is being said. Nothing here catches the failure where a
technically perfect montage illustrates the wrong ideas; only looking does.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: F401  UTF-8 stdout; see console.py

import audio as audio_mod
import config

# Measured from the three reference videos. Ranges are min-max across them,
# widened only where a single reference is an obvious outlier (the Mach video's
# long composited sequences make its cut rate unrepresentative).
REFERENCE = {
    "loudness_lufs": (-18.7, -16.4),
    "loudness_range_lu": (3.1, 7.5),
    "mean_shot_seconds": (5.0, 6.5),
    "long_silences": (0, 1),
    # NOT a reference band. The references sit at 30-73% by this measure and
    # this track will not, because they are photographic and it is designed -
    # see frame_ink. This is a floor: below it a frame is not sparse, it is
    # blank, and something has failed to draw.
    "emptiest_frame_pct": (4.0, 100.0),
}


def probe(path):
    out = subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries",
         "format=duration", "-show_entries",
         "stream=width,height,r_frame_rate,codec_type",
         "-of", "json", str(path)], capture_output=True, text=True)
    info = json.loads(out.stdout or "{}")
    video = next((s for s in info.get("streams", [])
                  if s.get("codec_type") == "video"), {})
    return {
        "duration": float((info.get("format") or {}).get("duration") or 0),
        "width": video.get("width"), "height": video.get("height"),
        "fps": video.get("r_frame_rate", "?"),
    }


def count_cuts(path, threshold=0.3):
    """Hard cuts, the same way the references were counted.

    Deliberately not the `movie=` lavfi source, which takes the path *inside* a
    filter string where a Windows drive colon is a filter-argument separator.
    Escaping it correctly is possible and gets mangled again by any shell in
    between; when it failed here it did not error, it silently reported zero
    cuts on a video with 59. Passing the file as an ordinary -i argument has no
    escaping surface at all, and `-f null` prints the surviving frame count.
    """
    out = subprocess.run(
        [config.FFMPEG, "-nostats", "-i", str(path),
         "-vf", f"select='gt(scene,{threshold})'", "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = ""
    for line in (out.stderr or "").splitlines():
        if line.startswith("frame="):
            last = line
    if not last:
        return 0
    try:
        return int(last.split("frame=")[1].split()[0])
    except (IndexError, ValueError):
        return 0


def frame_ink(path, step=0.5, threshold=12):
    """How much of each sampled frame carries content rather than ground.

    A pixel counts when it differs from a heavily blurred copy of its own
    frame, so a smooth backdrop reads as empty and type, edges and subjects
    read as content.

    Read the FLOOR, not the mean. Chasing the references' mean was a mistake
    worth writing down: this measure rewards local detail, which photographic
    footage has in quantity and clean graphic design deliberately does not. A
    line chart that reads beautifully scores 7% here and a bar chart scores
    29%; the difference is texture, not quality. What the number is genuinely
    good for is catching a frame that is actually empty - which is how the
    build-in timing bug was found, with 0.6% at the cut climbing to 30% a
    second later, seven times in one video.

    Returns (values, times) sampled every `step` seconds.
    """
    import numpy as np
    from PIL import Image, ImageFilter

    duration = probe(path)["duration"]
    values, times = [], []
    with tempfile.TemporaryDirectory() as work:
        shot = Path(work) / "f.png"
        moment = step
        while moment < duration:
            subprocess.run(
                [config.FFMPEG, "-y", "-v", "error", "-ss", str(moment),
                 "-i", str(path), "-frames:v", "1", str(shot)],
                capture_output=True)
            if shot.exists():
                image = Image.open(shot).convert("L")
                width, height = image.size
                # The bottom fifth is the subtitle band; it is not the design
                # layer and it is present on every frame, so counting it would
                # put a floor under the measure that hides an empty picture.
                image = image.crop((0, 0, width, int(height * 0.82)))
                sharp = np.asarray(image, dtype=np.int16)
                soft = np.asarray(image.filter(
                    ImageFilter.GaussianBlur(width / 24.0)), dtype=np.int16)
                values.append(float((np.abs(sharp - soft) > threshold).mean()))
                times.append(moment)
                shot.unlink()
            moment += step
    return values, times


def count_silences(path, noise_db=-50, min_seconds=0.4):
    out = subprocess.run(
        [config.FFMPEG, "-nostats", "-i", str(path), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_seconds}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (out.stderr or "").count("silence_start")


def declared_shots(path):
    """The shot count the build recorded, if this video came from one.

    Scene detection cannot count cuts in this track's own output. Every made
    shot shares one backdrop and one palette by design, so a hard cut between
    a chart and a composite scores far below any usable threshold: measured on
    a seven-shot build, `gt(scene,0.3)` found 0, 0.2 found 1 and 0.05 found 15.
    The report then said "0 cuts, mean shot 35.3s" and flagged the mean as out
    of band - a confident wrong number pointing at a problem that does not
    exist, which is the failure this whole file is supposed to prevent.

    shots.json is written by the builder and knows exactly. Use it when it is
    there; fall back to scene detection, and say which was used, when it is not.
    """
    manifest = Path(path).parent / "shots.json"
    if not manifest.exists():
        return None
    try:
        rows = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return len(rows) if isinstance(rows, list) and rows else None


def measure(path):
    info = probe(path)
    declared = declared_shots(path)
    cuts = (declared - 1) if declared else count_cuts(path)
    loud = audio_mod.measure(path)
    shots = cuts + 1
    ink, _times = frame_ink(path)
    return {
        "path": str(path),
        "duration": info["duration"],
        "size": f"{info['width']}x{info['height']}",
        "cuts": cuts,
        "cuts_from": "shots.json" if declared else "scene detection",
        "mean_shot_seconds": (info["duration"] / shots) if shots else 0.0,
        "loudness_lufs": loud.get("I"),
        "loudness_range_lu": loud.get("LRA"),
        "long_silences": count_silences(path),
        "emptiest_frame_pct": 100.0 * min(ink) if ink else None,
    }


def _in_range(value, bounds):
    if value is None:
        return False
    lo, hi = bounds
    return lo <= value <= hi


def report(mine, references=()):
    rows = []
    for key, bounds in REFERENCE.items():
        value = mine.get(key)
        rows.append((key, value, bounds, _in_range(value, bounds)))

    print(f"\n{mine['path']}")
    print(f"  {mine['size']}  {mine['duration']:.1f}s  "
          f"{mine['cuts']} cuts  mean shot {mine['mean_shot_seconds']:.1f}s"
          f"  ({mine.get('cuts_from', 'scene detection')})")
    print("\n  against the reference band")
    for key, value, (lo, hi), ok in rows:
        # Two decimals, because one made the report contradict itself: a mean
        # shot of 4.996 s printed as "5.0" and was then marked outside a band
        # of "5.0 to 6.5". Round the display and the reader has to guess which
        # of the two numbers is lying.
        shown = "n/a" if value is None else f"{value:.2f}"
        print(f"    {'[ok]  ' if ok else '[off] '} {key:<20} {shown:>8}   "
              f"reference {lo} to {hi}")

    for ref in references:
        m = measure(ref)
        print(f"\n  reference: {Path(ref).name[:56]}")
        print(f"    {m['size']}  {m['duration']:.1f}s  {m['cuts']} cuts  "
              f"mean shot {m['mean_shot_seconds']:.1f}s  "
              f"{m['loudness_lufs']} LUFS  LRA {m['loudness_range_lu']}  "
              f"{m['long_silences']} silences")

    off = [k for k, _v, _b, ok in rows if not ok]
    print()
    if off:
        print(f"{len(off)} measure(s) outside the reference band: {', '.join(off)}")
    else:
        print("every measured property sits inside the reference band")
    print("This says nothing about whether the shots illustrate the script. "
          "Look at the video.")
    return len(off)


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--against", nargs="*", default=[],
                    help="reference videos to measure alongside")
    args = ap.parse_args()
    if not Path(args.video).exists():
        raise SystemExit(f"no such file: {args.video}")
    return 1 if report(measure(args.video), args.against) else 0


if __name__ == "__main__":
    raise SystemExit(main())
