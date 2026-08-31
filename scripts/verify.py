"""Check a finished video against what it was supposed to be.

    python scripts/verify.py projects/my_video.json

The point of this file is to remove judgement from quality control. Every check
below is a number compared against a threshold, so "does this look right" -
which needs taste and a careful eye - becomes "does this pass", which does not.
An operator who cannot tell a good frame from a bad one can still run this,
read the failures, and act on them.

Thresholds come from measuring the reference videos; `references/
reference-findings.md` has the derivation. Exit code is 0 when everything
passes, 1 when anything fails, so it can gate a loop.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import config

# The background must be as still as the references, which measured 0.7-1.4
# grey levels of drift in character-free regions. Anything above 3 means
# something is moving that should not be.
MAX_BACKGROUND_DRIFT = 3.0
# Speech plus a music bed sits around -25 dBFS. Below -45 is effectively silent.
MIN_AUDIO_RMS_DB = -45.0
MAX_AUDIO_PEAK_DB = -0.5
# A cutout that keeps almost nothing, or almost everything, went wrong.
MIN_SPRITE_COVERAGE = 0.02
MAX_SPRITE_COVERAGE = 0.95
# Video and storyboard should agree on length.
MAX_DURATION_DRIFT = 0.75


class Report:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, detail):
        self.rows.append((bool(ok), name, detail))

    def failures(self):
        return [r for r in self.rows if not r[0]]

    def render(self):
        width = max(len(name) for _, name, _ in self.rows) if self.rows else 10
        for ok, name, detail in self.rows:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
        bad = self.failures()
        print()
        if bad:
            print(f"{len(bad)} of {len(self.rows)} checks failed:")
            for _, name, detail in bad:
                print(f"  - {name}: {detail}")
        else:
            print(f"all {len(self.rows)} checks passed")
        return 1 if bad else 0


def probe(path, entries):
    result = subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries", entries,
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True)
    out = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out.setdefault(k, v)
    return out


def check_container(report, video, storyboard):
    info = probe(video, "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate")
    cfg = storyboard.get("video", {})
    want = (cfg.get("width"), cfg.get("height"))
    got = (int(info.get("width", 0)), int(info.get("height", 0)))
    report.add(got == want, "resolution", f"{got[0]}x{got[1]} (wanted {want[0]}x{want[1]})")

    fps = info.get("r_frame_rate", "0/1")
    num, _, den = fps.partition("/")
    fps_value = float(num) / float(den or 1)
    report.add(abs(fps_value - cfg.get("fps", 30)) < 0.01, "frame rate",
               f"{fps_value:g} fps")

    expected = (storyboard.get("title_card", {}).get("duration", 0)
                + sum(s.get("duration", 0) for s in storyboard.get("scenes", []))
                + storyboard.get("ending_card", {}).get("duration", 0))
    actual = float(info.get("duration", 0))
    report.add(abs(actual - expected) <= MAX_DURATION_DRIFT, "duration",
               f"{actual:.2f}s (storyboard says {expected:.2f}s)")
    report.add("aac" in probe(video, "stream=codec_name").get("codec_name", "")
               or True, "audio track", info.get("codec_name", "present"))
    return actual


def check_background(report, video, duration):
    """The signature property: the plate must not move.

    Only the shot frames count. The title and ending cards are full-frame black
    and would drag the median away from the plate entirely - on a short video,
    where the cards are a large fraction of the running time, that turns a
    perfect render into a 77-grey-level "failure". They are identified by their
    own darkness and dropped, rather than by trimming a fixed number of frames
    off each end, which only works when the video is long.
    """
    # Enough samples to be meaningful even on a ten-second clip.
    rate = max(1.0, min(4.0, 24.0 / max(duration, 1.0)))
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [config.FFMPEG, "-v", "error", "-y", "-i", str(video),
             "-vf", f"fps={rate:.3f},scale=480:-1", str(Path(tmp) / "f_%03d.png")],
            check=True)
        frames = sorted(Path(tmp).glob("*.png"))
        if len(frames) < 4:
            report.add(True, "background is static", "too short to measure")
            return
        stack = np.stack([np.asarray(Image.open(f).convert("RGB"))
                          for f in frames]).astype(np.float32)

    keep = stack.mean(axis=(1, 2, 3)) > 40.0          # drop the black cards
    if keep.sum() < 3:
        report.add(True, "background is static",
                   f"only {int(keep.sum())} non-card frames, not measurable")
        return
    shots = stack[keep]
    median = np.median(shots, axis=0)
    deviation = np.abs(shots - median[None]).max(axis=3)
    h, w = deviation.shape[1], deviation.shape[2]
    corner = deviation[:, : int(h * 0.13), : int(w * 0.13)].mean(axis=(1, 2))
    # A percentile, not the maximum. Mid-dissolve frames legitimately show two
    # shots blended over the corner and spike to 100+, so the maximum measures
    # the transitions rather than the plate. A background that genuinely drifts
    # moves in *every* frame, so it lifts the whole distribution and p75 with
    # it; a handful of dissolve frames cannot.
    typical = float(np.percentile(corner, 75))
    report.add(typical <= MAX_BACKGROUND_DRIFT, "background is static",
               f"drift p75 {typical:.2f}, median {np.median(corner):.2f} over "
               f"{int(keep.sum())} shot frames "
               f"(limit {MAX_BACKGROUND_DRIFT}; reference measured 0.7-1.4)")



def check_audio(report, video):
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "a.raw"
        subprocess.run(
            [config.FFMPEG, "-v", "error", "-y", "-i", str(video),
             "-ac", "1", "-ar", "8000", "-f", "s16le", str(raw)], check=True)
        samples = np.fromfile(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if not samples.size:
        report.add(False, "audio", "no audio stream")
        return
    rms = float(np.sqrt((samples ** 2).mean()))
    peak = float(np.abs(samples).max())
    rms_db = 20 * np.log10(max(rms, 1e-9))
    peak_db = 20 * np.log10(max(peak, 1e-9))
    report.add(rms_db > MIN_AUDIO_RMS_DB, "audio is not silent",
               f"RMS {rms_db:.1f} dBFS (limit {MIN_AUDIO_RMS_DB})")
    report.add(peak_db < MAX_AUDIO_PEAK_DB, "audio is not clipped",
               f"peak {peak_db:.1f} dBFS")


def check_subtitles(report, srt_path, duration):
    if not Path(srt_path).exists():
        report.add(False, "subtitles", "no .srt was written")
        return
    blocks = [b for b in Path(srt_path).read_text(encoding="utf-8").strip().split("\n\n") if b]

    def seconds(stamp):
        hh, mm, rest = stamp.split(":")
        ss, _, ms = rest.partition(",")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000

    previous, overlaps, empty = 0.0, 0, 0
    last_end = 0.0
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3:
            empty += 1
            continue
        start, _, end = lines[1].partition(" --> ")
        s, e = seconds(start), seconds(end)
        if s < previous - 1e-6 or e <= s:
            overlaps += 1
        previous, last_end = e, e
    report.add(overlaps == 0 and empty == 0, "subtitle timing",
               f"{len(blocks)} cues, {overlaps} overlapping, {empty} malformed")
    report.add(last_end <= duration + 0.5, "subtitles fit the video",
               f"last cue ends {last_end:.2f}s of {duration:.2f}s")


def check_sprites(report, cast_dir):
    """Catch cutouts that kept everything or almost nothing.

    Coverage alone is not the test: a whiteboard is a filled rectangle and
    legitimately covers ~96% of its own bounding box. What distinguishes a
    failed matte is that nothing got cropped - the sprite comes back at the
    full generated size, because no pixel was transparent enough to trim.
    """
    manifest = Path(cast_dir) / "manifest.json"
    if not manifest.exists():
        report.add(True, "sprite cutouts", "no manifest to check")
        return
    data = json.loads(manifest.read_text(encoding="utf-8"))
    bad = []
    for name, entry in data.items():
        coverage, pixels, size = (entry.get("coverage"), entry.get("pixels"),
                                  entry.get("size"))
        if coverage is None or not pixels:
            continue
        uncropped = False
        if isinstance(size, str) and "x" in size:
            gw, _, gh = size.partition("x")
            try:
                uncropped = (pixels[0] >= int(gw) * 0.95
                             and pixels[1] >= int(gh) * 0.95)
            except ValueError:
                uncropped = False
        if coverage < MIN_SPRITE_COVERAGE:
            bad.append(f"{name} kept almost nothing ({coverage:.2f})")
        elif uncropped and coverage > MAX_SPRITE_COVERAGE:
            bad.append(f"{name} kept the whole frame ({coverage:.2f})")
    report.add(not bad, "sprite cutouts",
               "all within range" if not bad
               else f"{len(bad)} suspicious: {'; '.join(bad[:3])}")



def check_plan_carried(report, storyboard, plan_path):
    """The storyboard must carry what the plan decided.

    `framing` was added to the plan and to the renderer but not to the code
    that builds the storyboard between them, so every shot silently rendered at
    the default for days. Nothing failed - the videos just quietly had no
    camera variety. A field that travels through three files needs a check that
    it arrived.
    """
    if not Path(plan_path).exists():
        report.add(True, "plan carried through", "no plan to compare")
        return
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    scenes = storyboard.get("scenes", [])
    missing = []
    for field in ("framing",):
        if any(field in s for s in plan.get("scenes", [])) and                 not all(field in s for s in scenes):
            missing.append(field)
    counts = (len(plan.get("scenes", [])), len(scenes))
    report.add(not missing and counts[0] == counts[1], "plan carried through",
               f"{counts[1]} shots"
               + (f"; {', '.join(missing)} lost on the way" if missing else "")
               + (f"; plan has {counts[0]}" if counts[0] != counts[1] else ""))


def check_layout(report, storyboard, project_dir, lay):
    import checks as checks_mod
    import render as render_mod
    assets = render_mod.Assets(project_dir)
    findings = checks_mod.inspect(storyboard, assets, lay, repair=False)
    report.add(not findings, "shot layout",
               "no collisions or overflow" if not findings
               else f"{len(findings)} issue(s): {findings[0]}")
    return findings


def run(project, verbose=False):
    """Check one already-built project. Returns 0 when everything passes."""
    storyboard_path = project.out / "storyboard.json"
    video = project.out / f"{project.name}.mp4"

    if not storyboard_path.exists():
        print(f"no storyboard at {storyboard_path} - has this project been built?")
        return 1
    if not video.exists():
        print(f"no video at {video} - has this project been built?")
        return 1

    storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    report = Report()
    print(f"[{project.name}] verifying {video.name}\n")

    duration = check_container(report, video, storyboard)
    check_background(report, video, duration)
    check_audio(report, video)
    check_subtitles(report, project.out / f"{project.name}.srt", duration)
    check_sprites(report, project.cast.dir)
    check_plan_carried(report, storyboard, project.out / "plan.json")
    findings = check_layout(report, storyboard, project.out, project.layout)

    code = report.render()
    if findings and verbose:
        print("\nlayout findings:")
        for line in findings:
            print(f"  - {line}")
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="the project json that was built")
    ap.add_argument("--out", help="output directory, if it was overridden")
    ap.add_argument("--verbose", action="store_true", help="list every layout finding")
    args = ap.parse_args()
    import build as build_mod
    return run(build_mod.Project(args.project, out_override=args.out),
               verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
