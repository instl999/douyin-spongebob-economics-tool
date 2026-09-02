"""Main entry point: a project file in, an editable Jianying draft out.

    python scripts/build.py projects/efficiency_wage.json

The MP4 beside it is the preview. The draft is the deliverable, because
automatic layout is good enough to watch and not good enough to ship without
somebody looking at it.

Every stage writes its result into the project's own output directory and skips
itself when that result is already there and still current, so a re-run after
editing one pose regenerates one sprite, and a re-run after editing the
storyboard re-renders without paying for narration again. `--from <stage>`
redoes that one stage; the stages after it consult their own caches and
re-derive only what actually changed.

Stages: plan -> assets -> voice -> storyboard -> render -> audio -> mux -> draft
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assets as assets_mod
import audio as audio_mod
import config
import plan as plan_mod
import styles as styles_mod
import tts as tts_mod
from layout import Layout

ROOT = Path(__file__).resolve().parent.parent
STAGES = ["plan", "assets", "voice", "storyboard", "render", "audio", "mux",
          "draft"]


def log(message=""):
    print(message, flush=True)


class Project:
    def __init__(self, path, out_override=None):
        self.path = Path(path).resolve()
        # utf-8-sig, not utf-8, everywhere a file a person may have edited is
        # read. Windows editors write a BOM by default, and json.loads on a
        # BOM'd file fails with "Unexpected UTF-8 BOM ... line 1 column 1",
        # which the build then blames on the project file being malformed. It
        # is not; it is fine, and utf-8-sig reads plain UTF-8 identically.
        self.data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        self.name = self.data.get("name") or self.path.stem
        self.out = Path(out_override or self.data.get("out")
                        or (ROOT / "out" / self.name)).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "voice").mkdir(exist_ok=True)

    @property
    def script(self):
        inline = self.data.get("script_text")
        if inline:
            return inline
        ref = self.data.get("script")
        if not ref:
            raise ValueError(f"{self.path.name}: needs `script` or `script_text`")
        p = Path(ref)
        if not p.is_absolute():
            p = (self.path.parent / ref) if (self.path.parent / ref).exists() else ROOT / ref
        return p.read_text(encoding="utf-8-sig")

    @property
    def cast(self):
        # A style key from casts/styles.json, or a path straight to a cast
        # file; missing means the registry's default style.
        ref = self.data.get("cast")
        if ref is not None and not str(ref).strip():
            ref = None
        _, p = styles_mod.resolve(ref)
        return assets_mod.Cast.load(p, root=ROOT / "casts")

    @property
    def style_key(self):
        """Which registered style this project uses, or None for a bare path."""
        key, _ = styles_mod.resolve(self.data.get("cast") or None)
        return key

    @property
    def look(self):
        """Look settings for this project: the shared ones plus its style's."""
        return styles_mod.look(self.style_key)

    @property
    def layout(self):
        return Layout(self.data.get("orientation") or styles_mod.default_orientation(),
                      style=self.style_key)

    def resolve_speed(self):
        """Speech rate to use, honouring `target_seconds` when it is set.

        Returns (speed, report). An unreachable target is reported rather
        than acted on: a 95-second script cannot become a 60-second video
        by talking faster, and saying so is more use than silently
        producing something 35 seconds too long.
        """
        import timing
        voice = self.get("voice", {}) or {}
        target = self.get("target_seconds")
        if target is None:
            return float(voice.get("speed", 1.0)), None
        result = timing.fit_to_target(
            self.script, float(target),
            shot_seconds=float(self.get("shot_seconds", 5.0)),
            tail_pad=float(self.get("tail_pad", 0.35)),
            title_seconds=float(self.get("title_seconds", 2.6)),
            ending_seconds=float(self.get("ending_seconds", 4.0)))
        return result["speed"], result

    def get(self, key, default=None):
        return self.data.get(key, default)


# --- stages ----------------------------------------------------------------

# What each stage's output feeds. Forcing a stage has to clear the cached
# outputs of everything downstream, or the re-run quietly reuses them:
# `--from storyboard` skipped the render and reused video_mute.mp4, so an
# edited plan produced a byte-identical video and looked like the edit had
# done nothing.
DOWNSTREAM = {
    "plan": ["storyboard.json", "video_mute.mp4"],
    "assets": ["video_mute.mp4"],
    "voice": ["storyboard.json", "video_mute.mp4"],
    "storyboard": ["video_mute.mp4"],
    "render": [],
    "audio": [],
    "mux": [],
    "draft": [],
}


def invalidate(project, forced):
    """Delete cached outputs that a forced stage has just made stale."""
    stale = {name for stage in forced for name in DOWNSTREAM.get(stage, [])}
    for name in sorted(stale):
        target = project.out / name
        if target.exists():
            target.unlink(missing_ok=True)
            log(f"  {name} is stale after --from {forced[0]}, removed")


def stage_plan(project, force=False):
    target = project.out / "plan.json"
    if target.exists() and not force:
        log("  plan.json already present - reusing")
        return json.loads(target.read_text(encoding="utf-8-sig"))

    cast = project.cast
    shot_seconds = float(project.get("shot_seconds", 5.0))
    if config.have_ark():
        result = plan_mod.direct(project.script, cast, shot_seconds,
                                 model=project.get("director_model"),
                                 orientation=project.get("orientation")
                                 or styles_mod.default_orientation())
    else:
        log("  ! ARK_API_KEY not set - falling back to the offline plan")
        result = plan_mod.offline_plan(project.script, cast, shot_seconds)

    for problem in result.get("problems", []):
        log(f"  ! {problem}")
    log(f"  {len(result['scenes'])} shots, "
        f"{len(plan_mod.used_sprites(result))} distinct sprites")
    # New poses are the one thing here that spends money and changes the cast,
    # so they are reported as their own line rather than buried in `problems`.
    for req in result.get("new_poses") or []:
        log(f"  + new pose {req['asset']} (shot {req['shot']}): "
            f"{req['description']}")
    for req in result.get("new_interactions") or []:
        log(f"  + new interaction {req['asset']} (shot {req['shot']}): "
            f"{req['description']}")
    if result.get("new_poses") or result.get("new_interactions"):
        added = (len(result.get("new_poses") or [])
                 + len(result.get("new_interactions") or []))
        log(f"    {added} sprite(s) added to this cast - drawn once, "
            f"reused by later videos")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return result


def stage_assets(project, plan, force=False):
    cast = project.cast
    lay = project.layout
    library = assets_mod.Library(cast, log=log)

    background = project.out / "background.png"
    _, made = library.build_background(background, lay.image_size, force=force)
    log(f"  background.png  {'generated' if made else 'cached'}")

    wanted = set(plan_mod.used_sprites(plan))
    if wanted:
        report = library.build_all(assets_mod.SPRITE_SIZE, only=wanted, force=force)
        if report["failed"]:
            for name, err in report["failed"]:
                log(f"  ! {name}: {err}")
    title_text = project.get("title") or plan.get("title") or ""
    if title_text and project.get("ai_title", True):
        card, note = library.build_title_card(
            title_text, project.out / "title_card.png", lay.image_size,
            force=force)
        log(f"  title_card.png  {note}")
        plan["_title_card_image"] = "title_card.png" if card else None

    assets_mod.link_into(project.out, cast, names=wanted)
    reconcile_sprites(project, plan, cast)
    return background


def reconcile_sprites(project, plan, cast):
    """Stand in an existing pose for any sprite that did not reach the disk.

    A pose the director asked for can fail to arrive - a quota wall, a content
    filter, a dropped connection - and the renderer's answer to a missing PNG
    is to skip that element. That is the wrong answer here. A shot that asked
    for two new poses and got neither lost both its characters and rendered as
    an empty plate, which is worse than the generic casting the request was
    meant to improve on. A near pose is always better than nobody.
    """
    present = {n for n in cast.catalogue() if (project.out / n).exists()}
    for scene in plan.get("scenes") or []:
        kept, subjects = [], set()
        for el in scene.get("elements") or []:
            asset = el.get("asset")
            if not asset or asset in present:
                if asset:
                    subjects.add(_subject_of(asset))
                kept.append(el)
                continue
            swap = plan_mod.nearest(asset, present)
            # The stand-in may be a pose of someone already in the shot, and
            # one character twice is a worse picture than one character once.
            if swap and _subject_of(swap) not in subjects:
                log(f"  ! {asset} was not generated, standing in {swap}")
                subjects.add(_subject_of(swap))
                kept.append(dict(el, asset=swap,
                                 rel=cast.relative_height(swap)))
            else:
                log(f"  ! {asset} was not generated and has no stand-in, dropped")
        scene["elements"] = kept


def _subject_of(asset):
    stem = asset[:-4] if asset.endswith(".png") else asset
    return stem if stem.startswith("prop_") else stem.split("_", 1)[0]


def stage_voice(project, plan, force=False, speed=None):
    voice = project.get("voice", {}) or {}
    speaker = voice.get("speaker")
    speed = float(speed if speed is not None else voice.get("speed", 1.0))
    index_path = project.out / "voice" / "index.json"
    index = (json.loads(index_path.read_text(encoding="utf-8-sig"))
             if index_path.exists() and not force else {})

    degraded = 0
    for i, scene in enumerate(plan["scenes"], 1):
        key = str(i)
        text = scene["narration"]
        cached = index.get(key)
        # A shot that fell back to silence is cached like any other, so without
        # this a transient network fault becomes a permanent hole: every later
        # run sees matching text and skips it. Degraded entries are retried
        # whenever narration is actually available.
        stale = cached and cached.get("degraded") and config.have_tts()
        if (cached and not stale and cached.get("text") == text
                and Path(cached["path"]).exists()):
            continue
        if stale:
            log(f"  shot {i}: retrying (was silent from an earlier failure)")
        out = project.out / "voice" / f"scene_{i:02d}.mp3"
        try:
            result = tts_mod.synth(text, out, speaker=speaker, speed=speed)
        except tts_mod.TTSError as exc:
            log(f"  ! shot {i}: {exc}")
            log("    falling back to an estimated duration for this shot")
            result = tts_mod._silent(text, out)
        if result["degraded"]:
            degraded += 1
        index[key] = {"text": text, "path": str(result["path"]),
                      "duration": result["duration"],
                      "words": result["words"], "degraded": result["degraded"]}
        log(f"  shot {i:>2}: {result['duration']:5.2f}s"
            f"{'  (estimated - no TTS)' if result['degraded'] else ''}")

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    if degraded:
        reason = ("set ARK_API_KEY for narration" if not config.have_tts()
                  else "the calls above failed after retrying; re-run "
                       "`--from voice` to fill just those shots")
        log(f"  ! {degraded} shot(s) have no real narration - {reason}")
    return index


def stage_storyboard(project, plan, voice_index):
    lay = project.layout
    look = project.look
    tail = float(project.get("tail_pad", 0.35))
    scenes, pieces, srt = [], [], []
    clock = 0.0

    title_text = project.get("title") or plan.get("title") or ""
    title_seconds = float(project.get("title_seconds", 2.6)) if title_text else 0.0
    if title_seconds:
        pieces.append((None, title_seconds))
        clock += title_seconds

    for i, scene in enumerate(plan["scenes"], 1):
        entry = voice_index.get(str(i), {})
        duration = float(entry.get("duration") or
                         tts_mod.estimate_duration(scene["narration"])) + tail
        audio_path = entry.get("path")
        pieces.append((audio_path if audio_path and Path(audio_path).exists() else None,
                       duration))
        built = {"id": i, "subtitle": scene["narration"],
                 "framing": scene.get("framing", "medium"),
                 "elements": scene["elements"], "duration": duration}
        spans = _caption_spans(scene["narration"], duration)
        built["captions"] = [{"text": t, "start": s, "end": e} for s, e, t in spans]
        for s, e, t in spans:
            srt.append((clock + s, clock + e, t))
        scenes.append(built)
        clock += duration

    ending = plan.get("ending") or {}
    ending_text = ending.get("text") or ""
    ending_seconds = float(project.get("ending_seconds", 4.0)) if ending_text else 0.0
    if ending_seconds:
        pieces.append((None, ending_seconds))
        clock += ending_seconds

    storyboard = {
        "video": {
            "orientation": (project.get("orientation")
                            or styles_mod.default_orientation()),
            "width": lay.width, "height": lay.height,
            "fps": int(project.get("fps", 30)),
            "background": "background.png",
            "dissolve": float(project.get("dissolve",
                                          look["timing"]["dissolve"])),
            "crf": int(project.get("crf", 20)),
            "preset": project.get("preset", "veryfast"),
            "panel_color": list(project.cast.panel_color),
            # Carried with the storyboard so the renderer, the draft exporter
            # and the draft checker all use one set of numbers. Looking them up
            # again from casts/styles.json would let an edit between render and
            # export silently put the two out of step.
            "look": look,
        },
        "scenes": scenes,
    }
    if title_text:
        storyboard["title_card"] = {
            "text": title_text, "duration": title_seconds, "style": "title",
            "size": float(project.get("title_size", 0.082))}
        card_image = plan.get("_title_card_image")
        if card_image and (project.out / card_image).exists():
            storyboard["title_card"]["image"] = card_image
    if ending_text:
        storyboard["ending_card"] = {"text": ending_text,
                                     "highlight": ending.get("highlight"),
                                     "duration": ending_seconds,
                                     "size": float(project.get("ending_size", 0.062))}

    # Sprite sizes are only knowable now the PNGs exist, so collisions are
    # found and spread apart here rather than guessed at by the director.
    import checks as checks_mod
    import render as render_mod
    findings = checks_mod.inspect(
        storyboard, render_mod.Assets(project.out), lay, repair=True,
        cast=project.cast)
    for line in findings:
        log(f"  {line}")

    (project.out / "storyboard.json").write_text(
        json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
    audio_mod.write_srt(srt, project.out / f"{project.name}.srt")
    return storyboard, pieces, clock


def _caption_spans(text, duration):
    """Split a shot's narration into on-screen captions timed by length."""
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。！？；!?;" and len(buf.strip()) >= 10:
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    if not parts:
        return []
    if len(parts) == 1:
        return [(0.0, duration, parts[0])]
    weights = [max(1, len(p)) for p in parts]
    total = sum(weights)
    spans, cursor = [], 0.0
    for part, w in zip(parts, weights):
        span = duration * w / total
        spans.append((cursor, cursor + span, part))
        cursor += span
    return spans


def stage_render(project, storyboard, force=False):
    import render as render_mod
    target = project.out / "video_mute.mp4"
    if target.exists() and not force:
        # Atomic writes stop an interrupted run leaving a truncated file, but a
        # cached render can still be damaged by something outside this process.
        # One ffprobe is cheap, and turns `moov atom not found` three stages
        # later into a line that says what happened and fixes itself.
        expected = sum(s["duration"] for s in
                       json.loads((project.out / "storyboard.json").read_text(
                           encoding="utf-8")).get("scenes", [])) if (
                       project.out / "storyboard.json").exists() else 0
        readable = tts_mod.probe_duration(target)
        if readable > 0 and (not expected or readable >= expected * 0.5):
            log("  video_mute.mp4 already present - reusing")
            return target
        log(f"  video_mute.mp4 is unreadable or truncated "
            f"({readable:.1f}s on disk) - rendering it again")
        target.unlink(missing_ok=True)
    durations = [s["duration"] for s in storyboard["scenes"]]
    renderer = render_mod.Renderer(storyboard, project.out)
    started = time.time()

    # Carriage-return progress is unreadable in a redirected log, so only
    # use it on a terminal; otherwise report at intervals.
    live = sys.stdout.isatty()

    def progress(done, total):
        pct = 100.0 * done / max(total, 1)
        if live:
            print(f"\r  frames {done}/{total} ({pct:4.1f}%)", end="", flush=True)
        elif done % max(total // 10, 1) < renderer.fps:
            log(f"  frames {done}/{total} ({pct:4.1f}%)")

    with Atomic(target) as partial:
        total, frames = renderer.render(partial, durations, progress=progress)
    if live:
        print()
    log(f"  {frames} frames / {total:.1f}s rendered in {time.time() - started:.1f}s")
    return target


def stage_audio(project, pieces, total):
    narration = audio_mod.build_narration(pieces, project.out / "narration.wav")
    bgm = project.get("bgm", "assets/bgm_default.wav")
    if bgm:
        bgm_path = Path(bgm)
        if not bgm_path.is_absolute():
            bgm_path = ROOT / bgm
        bgm = bgm_path if bgm_path.exists() else None
    track = project.out / "audio.wav"
    with Atomic(track) as partial:
        audio_mod.mix(narration, partial, total, bgm=bgm,
                      bgm_volume=float(project.get("bgm_volume", 0.10)))
    return track


class Atomic:
    """Write to a sibling `.partial` file and rename only on success.

    A render killed part-way used to leave a truncated video_mute.mp4 that the
    next run happily reused, and ffmpeg then died on `moov atom not found` -
    a message that tells an operator nothing about which file to delete. With
    the rename deferred to the end, an interrupted run leaves nothing behind
    for the next one to pick up.
    """

    def __init__(self, target):
        self.target = Path(target)
        # The extension has to stay on the end: ffmpeg picks the container
        # from it, and `video_mute.mp4.partial` makes it guess and fail.
        self.partial = self.target.with_name(
            f"{self.target.stem}.partial{self.target.suffix}")

    def __enter__(self):
        self.partial.unlink(missing_ok=True)
        return self.partial

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None and self.partial.exists():
            self.target.unlink(missing_ok=True)
            self.partial.replace(self.target)
        else:
            self.partial.unlink(missing_ok=True)
        return False


def report_usage(log=log):
    """What this run actually spent on the APIs."""
    import ark
    import tts as tts_mod
    a, t = ark.USAGE, tts_mod.USAGE
    if not any([a["images"], a["text_calls"], a["vision_calls"], t["clips"]]):
        log("  api: nothing was generated, everything came from cache")
        return
    parts = []
    if a["images"]:
        parts.append(f"{a['images']} image(s)")
    if a["text_calls"]:
        parts.append(f"{a['text_calls']} director call(s)")
    if a["vision_calls"]:
        parts.append(f"{a['vision_calls']} vision check(s)")
    if t["clips"]:
        parts.append(f"{t['clips']} narration clip(s), {t['characters']} chars")
    log(f"  api: {', '.join(parts)}")
    if t["retries"]:
        log(f"       {t['retries']} narration retry/retries were needed")
    log(f"       {a['seconds'] + t['seconds']:.0f}s waiting on the service")


def tidy(project, log=log):
    """Delete intermediates that are free to rebuild.

    A finished project was leaving about 45 MB of scratch behind - the
    per-shot WAV fragments and the two uncompressed mix stages - which across a
    series adds up faster than the videos do. What survives is what costs money
    or time to make again: the narration mp3s, the sprite copies, and the mute
    render. The audio stages rebuild from those in seconds with no API calls.
    """
    freed = 0
    victims = [project.out / "_narration_parts", project.out / "narration.wav",
               project.out / "audio.wav"]
    for victim in victims:
        if not victim.exists():
            continue
        if victim.is_dir():
            freed += sum(f.stat().st_size for f in victim.rglob("*") if f.is_file())
            shutil.rmtree(victim, ignore_errors=True)
        else:
            freed += victim.stat().st_size
            victim.unlink(missing_ok=True)
    if freed:
        log(f"  tidied {freed / 1e6:.0f} MB of rebuildable intermediates")


def stage_mux(project, video, audio_track):
    out = project.out / f"{project.name}.mp4"
    with Atomic(out) as partial:
        audio_mod.mux(video, audio_track, partial)
    return out


def stage_draft(project):
    """Write the editable Jianying project and prove it matches the render."""
    import check_draft
    import draft as draft_mod

    builder = draft_mod.DraftBuilder(project.out, name=project.name)
    path, total, layers = builder.build(project.out / "jianying")
    log(f"  {path.name}: {total:.1f}s over {layers} element layers")
    # Checked here rather than left to the operator: the draft is written
    # blind - Jianying is not needed to write one and may not be installed -
    # so the only thing standing between a wrong transform and a broken
    # project the user opens is this comparison against the renderer.
    diffs = check_draft.compare(project.out, path) or []
    off = [f"shot {i}" for i, d in diffs if d > check_draft.MAX_MEAN_DIFF]
    if off:
        raise QualityError(f"the draft does not match the render at {', '.join(off)}"
                         f" - run scripts/check_draft.py {project.out} for detail")
    if diffs:
        log(f"  matches the render to "
            f"{max(d for _, d in diffs):.2f}/255 at worst, over {len(diffs)} shots")
    return path


# --- checks ----------------------------------------------------------------

def check():
    ok = True
    for name, tool in (("ffmpeg", config.FFMPEG), ("ffprobe", config.FFPROBE)):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
            log(f"  [ok]   {name}")
        except Exception:
            ok = False
            log(f"  [MISS] {name} - install it or set {name.upper()}_BIN")
    for label, ready, note in config.describe():
        log(f"  [{'ok' if ready else 'MISS'}]   {label}  {note}")
        ok = ok and ready
    # The style registry is the one config file users hand-edit; validate it,
    # then every style it offers (plus any cast file dropped into casts/).
    for issue in styles_mod.problems():
        log(f"  [MISS] style registry  {issue}")
        ok = False
    for key, entry in styles_mod.visible().items():
        try:
            issues = assets_mod.Cast.load(entry["file"],
                                          root=ROOT / "casts").problems()
        except Exception as exc:
            issues = [f"could not be read: {exc}"]
        label = entry["label"] if entry["label"] != key else ""
        log(f"  [{'ok' if not issues else 'MISS'}]   cast {key}"
            f"{'  ' + label if label else ''}"
            f"  {'valid' if not issues else issues[0]}")
        ok = ok and not issues
    try:
        import numpy, PIL, scipy                       # noqa: F401
        log("  [ok]   numpy / pillow / scipy")
    except ImportError as exc:
        ok = False
        log(f"  [MISS] python packages: {exc}")
    return ok


def run_build():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", help="path to a project json")
    ap.add_argument("--check", action="store_true", help="report readiness and exit")
    ap.add_argument("--from", dest="from_stage", choices=STAGES,
                    help="redo this stage even if its output is already there")
    ap.add_argument("--stop-after", choices=STAGES, help="stop once this stage is done")
    ap.add_argument("--out", help="write this project's output here "
                                 "(default: <skill>/out/<name>)")
    ap.add_argument("--regenerate-assets", action="store_true",
                    help="re-generate every sprite, ignoring the cache "
                         "(slow and costs money; the cache already notices edited prompts)")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the automatic check of the finished file")
    ap.add_argument("--preview", action="store_true",
                    help="write a contact sheet of the shots and stop")
    ap.add_argument("--no-draft", action="store_true",
                    help="skip the editable Jianying project, leaving only the mp4")
    args = ap.parse_args()

    if args.check or not args.project:
        log("Readiness")
        ready = check()
        if not args.project:
            log("\nGive a project file to build, e.g. "
                "python scripts/build.py projects/example.json")
        return 0 if ready else 1

    project = Project(args.project, out_override=args.out)
    # --from names one stage to redo, not everything downstream. Later
    # stages have their own caches - assets fingerprint each prompt, voice
    # keys on the text - so they re-derive exactly what actually changed.
    # Forcing the whole tail would regenerate every sprite on any edit,
    # which is minutes of API calls to reproduce identical files.
    forced = [args.from_stage] if args.from_stage else []
    if args.regenerate_assets:
        forced.append("assets")

    def should(stage):
        return stage in forced

    def done(stage):
        """True when --stop-after names this stage, so the run ends here."""
        return args.stop_after == stage

    log(f"[{project.name}] {project.layout.describe()}")

    # A cast is the one file users hand-edit, so check it before spending
    # ten minutes discovering that a prop name in `hanging` matches nothing.
    cast_problems = project.cast.problems()
    if cast_problems:
        log(f"\n{project.get('cast')} cannot be used as it is:")
        for line in cast_problems:
            log(f"  - {line}")
        return 1
    log("\n[1/8] plan")
    plan = stage_plan(project, force=should("plan"))
    invalidate(project, forced)

    import timing
    speed, fit = project.resolve_speed()
    estimate = timing.estimate(
        project.script, shot_seconds=float(project.get("shot_seconds", 5.0)),
        tail_pad=float(project.get("tail_pad", 0.35)),
        title_seconds=float(project.get("title_seconds", 2.6)),
        ending_seconds=float(project.get("ending_seconds", 4.0)), speed=speed)
    log(f"  predicted length {estimate['total']:.1f}s at {speed:.2f}x speed")
    if fit and not fit["ok"]:
        log(f"  ! {fit['note']}")

    log("\n[2/8] assets")
    stage_assets(project, plan, force=should("assets"))

    log("\n[3/8] voice")
    voice_index = stage_voice(project, plan, force=should("voice"), speed=speed)
    if done("voice"):
        return 0

    log("\n[4/8] storyboard")
    storyboard, pieces, total = stage_storyboard(project, plan, voice_index)
    log(f"  {len(storyboard['scenes'])} shots, {total:.1f}s total")

    if args.preview:
        import preview as preview_mod
        sheet = preview_mod.contact_sheet(storyboard, project.out,
                                          project.out / "preview.jpg")
        log(f"\npreview: {sheet}")
        return 0
    if done("storyboard"):
        return 0

    log("\n[5/8] render")
    video = stage_render(project, storyboard, force=should("render"))
    if done("render"):
        return 0

    log("\n[6/8] audio")
    track = stage_audio(project, pieces, total)
    if done("audio"):
        return 0

    log("\n[7/8] mux")
    final = stage_mux(project, video, track)
    if done("mux"):
        return 0

    log("\n[8/8] draft")
    draft_path = None if args.no_draft else stage_draft(project)
    log("")
    report_usage()
    log(f"\nfinished: {final}")
    if draft_path:
        # The mp4 is the preview; this is what gets handed over, because it is
        # the one a person can still fix.
        log(f"editable draft: {draft_path}")

    # Verification runs by default. An operator who has to remember to check
    # their own output eventually will not, and the failures this catches -
    # a drifting plate, a silent track, two characters merged into one blob -
    # are exactly the ones that stay invisible until somebody watches the file.
    if args.no_verify:
        tidy(project)
        return 0
    log("")
    import verify as verify_mod
    code = verify_mod.run(project)
    if code == 0:
        # Only tidy a build that passed: the intermediates are what you inspect
        # when it did not.
        log("")
        tidy(project)
    return code


# Problems with what the user supplied - a missing script, an empty one, a
# cast that will not parse, a rejected key - are not bugs and should not
# look like one. A traceback tells an operator nothing they can act on, and
# tells a less careful one to start editing the pipeline.
INPUT_ERRORS = (FileNotFoundError, ValueError, json.JSONDecodeError)


class QualityError(Exception):
    """An output the pipeline built does not match what it should have built.

    Deliberately not a ValueError: those are classified as the user's input
    being wrong, and a draft that disagrees with the render is the pipeline's
    fault, not theirs. Telling someone to go fix their script when the bug is
    here wastes their time and hides the bug.
    """


def main():
    try:
        return run_build()
    except KeyboardInterrupt:
        log("\ninterrupted - nothing was corrupted; re-run to carry on from the last finished stage")
        return 130
    except QualityError as exc:
        log(f"\nstopped: {exc}")
        log("this is a fault in the pipeline, not in your input.")
        return 1
    except INPUT_ERRORS as exc:
        log(f"\nstopped: {exc}")
        log("this is a problem with the project, the script or the cast - not with the pipeline.")
        log("run `python scripts/selftest.py` if you want to rule the pipeline out.")
        return 2
    except Exception as exc:
        import ark
        import tts as tts_mod
        if isinstance(exc, (ark.ArkError, tts_mod.TTSError)):
            log(f"\nthe service refused the request: {exc}")
            log("check `python scripts/build.py --check`; references/api-notes.md lists what each error actually means.")
            return 3
        raise


if __name__ == "__main__":
    raise SystemExit(main())
