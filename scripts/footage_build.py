"""Script in, footage-track MP4 and its editable Jianying draft out.

    python scripts/footage_build.py --script examples/telephone_history.txt

The draft is written from `timeline.json`, the record of what the MP4 was
assembled from, by `footage_draft.py`: into Jianying's own drafts folder when
one is found, beside the MP4 with `--draft-here`, not at all with `--no-draft`.

The drawn track's `build.py` is not reused. It is organised around a cast, a
sprite library and a static plate, none of which exist here, and threading a
second asset model through it would complicate the track that already works.
What *is* reused is everything below the picture: `plan.split_script` for the
beats, `tts` for narration, the same tail convention, `textkit` for captions
and `audio` for the mix - so the two tracks stay in sync where it matters.

The stage order is forced by one dependency: shot length comes from the real
narration audio, not from an estimate, so voice runs before anything looks for
a picture. Getting this backwards is how a montage ends up cut to a rhythm the
narration does not have.

Coverage is filled, never left. Retrieval covered 0/4 beats of a modern script
and 2/5 of an archival one, and a hole is worse than an imperfect shot - the
references never once cut to nothing. Beats retrieval cannot serve are
generated instead, photographic and through the identical grade, which is the
same answer the Mach reference gives with its one made shot.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: F401  UTF-8 stdout; see console.py

import audio as audio_mod
import captions as captions_mod
import config
import footage as footage_mod
import footage_render as fr
import layout as layout_mod
import composite as composite_mod
import motion
import plan as plan_mod
import styles as styles_mod
import tts as tts_mod

ROOT = Path(__file__).resolve().parent.parent

# Every duration below is a **baseline** one, written for a video at 1.0x, and
# `paced()` puts them on the clock the run actually uses. The picture is sped
# up with them: retrieved footage is played faster in `footage_render.prepare`
# rather than merely cut shorter, so a montage under 1.5x narration is not a
# reel of 1.0x clips truncated to fit.

# The gap between one shot's narration ending and the next beginning. Same
# value the drawn track uses, so both cut to the same rhythm.
TAIL_PAD = 0.35
# Hard cuts, not dissolves. Measured: the internet reference has 59 hard cuts
# in 309 s at a 5.2 s mean shot, so essentially every boundary is a cut. The
# drawn track dissolves, and inheriting that here made the montage read as a
# slideshow. Raise this above 0 to cross-fade instead.
DISSOLVE = 0.0
# A caption clears this long before its shot ends, so two are never legible at
# once. Must stay below the shortest shot.
CAPTION_GAP = 0.12
HOOK_IN, HOOK_HOLD = 0.6, 3.4
# A quieter bed than the drawn track uses, and now a round number in the band
# both tracks quote: 0.056 is -25 dB. The 0.05 it replaces was -26 dB, chosen
# from a measurement that 0.10 -> 0.05 recovers 0.3 LU of range on this
# narration - a decibel either side of that is not what the measurement was
# about, and one stated band across both tracks is worth more than the digit.
BGM_VOLUME = audio_mod.BGM_VOLUME


def paced(seconds, speed):
    """A baseline duration at this run's speed. See timing.py."""
    import timing
    return timing.scale(seconds, speed)


def log(message=""):
    print(message, flush=True)


def stage_voice(beats, out_dir, speaker=None, speed=1.0, force=False):
    """Narrate every beat. Returns [(path_or_None, seconds, degraded)].

    Cached by beat text, speaker and speed, the way the drawn track caches its
    voice stage. Narration is the one stage that is both expensive and totally
    determined by its inputs, so re-running a build to fix a caption should not
    re-synthesise twenty unchanged lines - and a build interrupted after voice
    should resume rather than start over.

    A shot that fell back to silence is deliberately NOT treated as current: it
    is cached like any other entry, so without this a single network blip
    becomes a permanent hole that every later run skips over.
    """
    voice_dir = out_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    index_path = voice_dir / "index.json"
    index = {}
    if index_path.exists() and not force:
        try:
            index = json.loads(index_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            index = {}

    pieces, degraded, reused = [], 0, 0
    for i, text in enumerate(beats, 1):
        target = voice_dir / f"beat_{i:02d}.mp3"
        cached = index.get(str(i))
        current = (cached and cached.get("text") == text
                   and cached.get("speaker") == (speaker or "")
                   and float(cached.get("speed", 1.0)) == float(speed)
                   and not cached.get("degraded")
                   and Path(cached["path"]).exists())
        if current:
            result = {"path": Path(cached["path"]), "duration": cached["duration"],
                      "degraded": False}
            reused += 1
        else:
            try:
                result = tts_mod.synth(text, target, speaker=speaker, speed=speed)
            except tts_mod.TTSError as exc:
                log(f"  ! beat {i}: {exc}; using an estimated duration")
                result = tts_mod._silent(text, target, speed)
            log(f"  beat {i:>2}: {result['duration']:5.2f}s"
                f"{'  (estimated, no audio)' if result['degraded'] else ''}")
        if result["degraded"]:
            degraded += 1
        entry = {"text": text, "path": str(result["path"]),
                 "duration": result["duration"],
                 "speaker": speaker or "", "speed": float(speed),
                 "degraded": bool(result["degraded"])}
        # Where the voice starts, stops and pauses, for the captions to change
        # at. Measured once per clip and kept with it; a silent stand-in has
        # nothing to measure, and its captions are timed by length instead.
        if current and cached.get("speech"):
            entry["speech"] = cached["speech"]
            entry["pauses"] = cached.get("pauses") or []
        elif not result["degraded"]:
            measured = audio_mod.speech_pauses(result["path"], speed)
            if measured:
                entry["speech"] = [measured[0], measured[1]]
                entry["pauses"] = [list(pause) for pause in measured[2]]
        index[str(i)] = entry
        # Every beat but the last ends on the pause before the next. The last
        # has no next: its pad was dead air after the final subtitle, at the
        # very end of the file - the drawn track dropped it for the same reason.
        tail = paced(TAIL_PAD, speed) if i < len(beats) else 0.0
        pieces.append((result["path"], result["duration"] + tail,
                       result["degraded"]))

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    if reused:
        log(f"  {reused}/{len(beats)} beats reused from cache")
    if degraded:
        log(f"  ! {degraded} beat(s) have no real narration")
    return pieces


def stage_pictures(plans, durations, out_dir, lay, grade,
                   min_score=footage_mod.MIN_SCORE, speed=1.0):
    """One prepared clip per beat: retrieved where possible, generated where not.

    Returns (paths, rows) where each row records what the beat actually got, so
    the report can say which shots are real footage and which are fill.

    `speed` reaches retrieved footage only. A composite, a motion graphic and
    generated fill are all drawn to fill exactly the seconds they are given, so
    they are already on the video's clock; a retrieved clip is somebody else's
    footage running at its own pace, and is the one thing here that has to be
    played faster rather than merely cut shorter.
    """
    shots_dir = out_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    paths, rows, fills = [], [], 0
    for i, (entry, seconds) in enumerate(zip(plans, durations)):
        target = shots_dir / f"shot_{i:03d}.mp4"
        chosen = entry.get("chosen")
        graphic = entry.get("graphic")
        comp = entry.get("composite")
        if comp:
            composite_mod.render(comp, target, seconds=seconds, size=lay.size,
                                 grade=grade)
            rows.append({"id": entry["id"], "kind": "composite",
                         "layout": comp["layout"], "spec": comp,
                         "notes": entry.get("graphic_notes", [])})
            log(f"  shot {i + 1:>2}  composite {comp['layout']:<14}  "
                f"{comp['headline'][:24]}")
            paths.append(target)
            continue
        if graphic:
            motion.render(graphic, target, seconds=seconds, size=lay.size,
                          grade=grade)
            # The whole spec, not just its kind: shots.json is the record of
            # what the video actually claims, and for a chart that is the
            # numbers. Knowing a beat was "a bar_chart" says nothing about
            # whether the bars were right.
            rows.append({"id": entry["id"], "kind": "graphic",
                         "graphic": graphic["kind"], "spec": graphic,
                         "notes": entry.get("graphic_notes", [])})
            log(f"  shot {i + 1:>2}  graphic   {graphic['kind']:<12}"
                f"        {(graphic.get('title') or graphic.get('caption') or '')[:34]}")
            for note in entry.get("graphic_notes", []):
                log(f"           ! {note}")
            paths.append(target)
            continue
        if chosen and chosen.get("score", 0) >= min_score:
            src = fr.fetch(chosen, footage_mod.CACHE / "clips")
            start = fr.locate(chosen, entry["needs"], seconds, src,
                              speed=speed)
            fr.prepare(chosen, target, seconds=seconds, size=lay.size,
                       grade=grade, needs=entry["needs"], speed=speed)
            rows.append({"id": entry["id"], "kind": "footage",
                         "score": chosen["score"], "cut_at": round(start, 2),
                         "provider": chosen["provider"],
                         "page_url": chosen.get("page_url", ""),
                         "credit": chosen.get("credit", ""),
                         "low_res": chosen.get("low_res", False)})
            log(f"  shot {i + 1:>2}  footage   score {chosen['score']} "
                f"cut@{start:6.1f}s  {entry['needs'][:44]}")
        else:
            fr.fill_shot(entry["needs"], target, seconds=seconds,
                         size=lay.size, grade=grade, move_index=fills)
            fills += 1
            rows.append({"id": entry["id"], "kind": "generated",
                         "needs": entry["needs"]})
            log(f"  shot {i + 1:>2}  generated              "
                f"      {entry['needs'][:44]}")
        paths.append(target)
    return paths, rows


def caption_spans(plans, durations, dissolve=DISSOLVE, gap=CAPTION_GAP,
                  lay=None, speech=None):
    """Bilingual caption spans on the assembled timeline.

    Shots overlap by `dissolve`, so a caption's start is the running total
    minus every transition already completed - the same arithmetic as
    `xfade_offsets`, and wrong in the same invisible way if it drifts.

    Consecutive spans must not overlap. They did: a caption ended at
    `seconds - dissolve*0.6` while the next began at `seconds - dissolve`,
    leaving 0.2 s where both were drawn, and the video came out with the
    subtitle visibly doubled on every transition. The end is now pinned to the
    next start rather than computed independently of it.

    Given the layout, a beat's Chinese is shown a line at a time. Every
    sampled reference frame carries two lines - one of Chinese, the English
    under it - and a whole beat set as one caption ran to two or three lines,
    whose block climbed out of the subtitle band into the picture: a flow
    chart's own caption was printed underneath it. The lines change where the
    voice pauses when the clip was measured (`speech`, one voice-index entry
    per beat) and by length when it was not, as the drawn track times its
    captions. The English is the beat's one translation and stays up across
    all of its lines. Without a layout, one caption per beat, as before.
    """
    spans, t = [], 0.0
    heard = list(speech or [])
    for i, (entry, seconds) in enumerate(zip(plans, durations)):
        next_start = t + seconds - dissolve
        end = max(t, next_start - gap)
        english = entry.get("en", "")
        if lay is None:
            spans.append((t, end, entry["beat"], None, english))
        else:
            clip = (heard[i] if i < len(heard) else None) or {}
            for a, b, text, _first, _last in captions_mod.spans(
                    entry["beat"], end - t, lay.subtitle_font_px(),
                    lay.subtitle_max_px, speech=clip.get("speech"),
                    pauses=clip.get("pauses") or ()):
                spans.append((t + a, t + b, text, None, english))
        t = next_start
    return spans


def voice_index(out_dir):
    """The voice stage's record: per beat, its clip, length and pauses."""
    path = Path(out_dir) / "voice" / "index.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def verify_output(path, expected_seconds, lay, narration=None):
    """What the file actually turned out to be, against the reference numbers."""
    probe = subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries",
         "format=duration", "-show_entries", "stream=width,height",
         "-of", "json", str(path)], capture_output=True, text=True)
    info = json.loads(probe.stdout or "{}")
    duration = float((info.get("format") or {}).get("duration") or 0)
    video = next((s for s in info.get("streams", []) if s.get("width")), {})
    loud = audio_mod.measure(path)
    narration_lra = (audio_mod.measure(narration).get("LRA")
                     if narration else "?")
    return [
        ("duration matches the timeline", abs(duration - expected_seconds) < 0.5,
         f"{duration:.2f}s vs {expected_seconds:.2f}s"),
        ("frame size", (video.get("width"), video.get("height")) == lay.size,
         f"{video.get('width')}x{video.get('height')}"),
        ("loudness in the reference band",
         loud.get("I") is not None and -19.5 <= loud["I"] <= -15.0,
         f"{loud.get('I')} LUFS (references: -16.4 to -18.7)"),
        # Both sides of the band, because over-compression is the failure this
        # catches - a one-sided "<= 8.0" once passed a track at LRA 2.1.
        #
        # The floor is 2.8, not the references' 3.1, and that is a real ceiling
        # rather than a fudge: a mix cannot have more range than its source,
        # and this TTS delivers narration at LRA 3.0-3.8 depending on the take.
        # At 3.0 in, 2.9 out is the arithmetic working, not the mix failing.
        # Raising the floor to 3.1 would fail builds nothing in the pipeline
        # can fix. `verify` prints the narration's own range beside it so the
        # two are never confused.
        # Test what the mix controls, not what the TTS handed it. A mix cannot
        # have more dynamic range than its source, and this engine delivers
        # narration anywhere from LRA 2.5 to 3.8 depending on the take. Judging
        # the mix against the references' 3.1 floor fails builds for something
        # no stage in this pipeline can change; judging it against its own
        # source catches the thing that *is* a bug - normalisation flattening
        # the track, which is how LRA 3.6 once became 2.1.
        ("the mix preserves the narration's dynamics",
         loud.get("LRA") is not None and (
             not isinstance(narration_lra, float)
             or loud["LRA"] >= narration_lra - 0.3),
         f"{loud.get('LRA')} LU from a narration at {narration_lra} "
         f"(references: 3.1-7.5)"),
    ]


def write_timeline(out_dir, name, lay, orientation, grade, speed, dissolve,
                   total, shot_paths, durations, rows, spans, pieces, chrome,
                   hook_span=None, beds=()):
    """Record the assembled timeline as `timeline.json`, for the draft.

    Everything the MP4 was put together from, at the times it was put there:
    where each shot starts and ends, each caption and the hook, the frame
    furniture, each beat's narration and the music bed. The draft is laid
    from this and nothing else, so the two cannot drift apart by one of them
    re-deriving a number the other already decided.

    Paths under the output directory are stored relative to it, so the folder
    can be moved; the bed usually lives elsewhere and is stored absolute.
    """
    import os

    root = Path(out_dir).resolve()
    spoken = voice_index(root)

    def rel(path):
        return os.path.relpath(Path(path).resolve(), root)

    offsets = [0.0] + fr.xfade_offsets(durations, dissolve)
    ends = offsets[1:] + [total]
    starts, clock = [], 0.0
    for seconds in durations:
        starts.append(clock)
        clock += seconds
    timeline = {
        "name": name,
        "video": {"width": lay.width, "height": lay.height, "fps": 30,
                  "orientation": orientation, "grade": grade,
                  "speed": speed, "dissolve": dissolve},
        "total": total,
        "shots": [{"path": rel(path), "start": round(a, 4), "end": round(b, 4),
                   "kind": row.get("kind")}
                  for path, a, b, row in zip(shot_paths, offsets, ends, rows)],
        "captions": [{"start": round(a, 4), "end": round(b, 4), "text": text,
                      "en": english or ""}
                     for a, b, text, _highlight, english in spans],
        "hook": ({"text": hook_span[0], "en": hook_span[1],
                  "start": round(hook_span[2], 4),
                  "end": round(hook_span[3], 4)} if hook_span else None),
        "chrome": {key: rel(path) for key, path in (chrome or {}).items()},
        # Narration is laid back to back at the shots' own lengths, as
        # audio.build_narration lays it for the MP4. Each clip's length is
        # what the voice stage measured - the slot also holds the pause
        # after it, and the draft ducks the music under speech, not pauses.
        "voice": [{"path": rel(path), "start": round(start, 4),
                   "duration": round(float((spoken.get(str(i)) or {}).get(
                       "duration", seconds)), 4),
                   "degraded": bool(degraded)}
                  for i, ((path, seconds, degraded), start)
                  in enumerate(zip(pieces, starts), 1)],
        "music": ({"beds": list(beds), "volume": BGM_VOLUME, "duck": True}
                  if beds else None),
    }
    (root / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    return timeline


def build(script_path, out_dir, orientation="landscape", grade="vintage",
          provider=None, speaker=None, speed=None, bgm=None, limit=0,
          hook=None, revoice=False, topic="", draft=True, draft_here=False):
    import music as music_mod
    import timing
    speed = timing.clamp(timing.DEFAULT_SPEED if speed is None else speed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lay = layout_mod.Layout(orientation)
    look = styles_mod.look()
    provider = provider or config.FOOTAGE_PROVIDERS

    script = Path(script_path).read_text(encoding="utf-8-sig")
    beats = plan_mod.split_script(script, 5.0)
    if limit:
        beats = beats[:limit]
    if not beats:
        raise SystemExit("the script has no sentences in it")
    log(f"{len(beats)} beats  |  {lay.describe()}  |  grade={grade}  "
        f"|  speed={speed:.2f}x  |  providers={provider}")

    log("\nvoice")
    pieces = stage_voice(beats, out_dir, speaker=speaker, speed=speed,
                         force=revoice)
    durations = [seconds for _, seconds, _ in pieces]

    log("\nfootage")
    for reason in footage_mod.unusable_providers(provider):
        log(f"  ! {reason}")
    results = footage_mod.select_footage(
        beats, durations=durations, orientation=orientation,
        provider=provider, progress=lambda m: None, speed=speed)
    cov = footage_mod.coverage(results)
    if cov["beats"]:
        log(f"  retrieval covered {cov['matched']}/{cov['beats']} beats "
            f"({cov['ratio']:.0%}); the rest are generated")
    else:
        # "covered 0/0 beats (0%)" reads as a total failure. It is the
        # opposite: every beat is a chart or a composite, so none of them
        # wanted footage and nothing was searched for.
        log("  no beat asked for footage; every shot is drawn or composited")

    log("\npictures")
    shot_paths, rows = stage_pictures(results, durations, out_dir, lay, grade,
                                      speed=speed)

    log("\nassemble")
    heard = voice_index(out_dir)
    spans = caption_spans(results, durations, dissolve=paced(DISSOLVE, speed),
                          gap=paced(CAPTION_GAP, speed), lay=lay,
                          speech=[heard.get(str(i))
                                  for i in range(1, len(results) + 1)])
    caps = fr.caption_pngs(spans, lay, look, out_dir / "captions")
    if hook is None:
        hook = beats[0]
    # The hook is large centred text, and so is a counter. When beat 1 is a
    # graphic the two land on top of each other - the first build opened on
    # "4.3%" with the hook printed straight through it. The bottom subtitle
    # still carries the line, so the overlay is simply dropped.
    opens_on_graphic = bool(results and (results[0].get("graphic")
                                         or results[0].get("composite")))
    hook_path = None if opens_on_graphic else fr.hook_png(
        hook, results[0].get("en", ""), lay, look,
        out_dir / "captions" / "hook.png")
    if opens_on_graphic:
        log("  hook skipped: shot 1 is a made shot with its own type")
    if hook_path:
        hook_in = paced(HOOK_IN, speed)
        caps = [(hook_in, hook_in + paced(HOOK_HOLD, speed), hook_path)] + caps
    mute = out_dir / "video_mute.mp4"
    chrome = fr.chrome_png(topic, lay, grade, out_dir / "captions" / "chrome.png")
    _, total = fr.assemble(shot_paths, durations, mute,
                           dissolve=paced(DISSOLVE, speed),
                           captions=caps, chrome=chrome, grade=grade)
    log(f"  {len(shot_paths)} shots, {total:.2f}s, {len(caps)} overlays"
        f"{', topic eyebrow' if topic else ''}")

    log("\naudio")
    narration = audio_mod.build_narration(
        [(p, s) for p, s, _ in pieces], out_dir / "narration.wav")
    # Chosen from the library the drawn track uses, by the same labels. An
    # explicit --bgm is somebody naming a track and is not second-guessed.
    if bgm:
        bed, why = Path(bgm), Path(bgm).name
    else:
        bed, why = music_mod.choose(ROOT / "assets" / "bgm", script,
                                    fallback=ROOT / "assets" / "bgm_default.wav")
    log(f"  music: {why}")
    # `bed and bed.exists()`, not `bed.exists()` alone: no music leaves this
    # None, and Path("") is Path(".") - a directory that exists, which ffmpeg
    # is then handed as a music file.
    # Written out as the bed itself rather than left to mix()'s single-file
    # default, so the record the draft lays from is the one the mix used.
    beds = ([{"path": str(Path(bed).resolve()), "start": 0.0,
              "end": float(total), "fade_in": music_mod.FADE_IN,
              "fade_out": music_mod.FADE_OUT}]
            if bed and bed.exists() else [])
    # A quieter bed than the drawn track uses; see audio.BGM_VOLUME.
    # duck is passed explicitly, not left to the default: the drawn track's mix
    # was measured without it and owns that default, and ducking is worth
    # +0.3 LU of range here - the difference between just inside the reference
    # band and just outside it.
    track = audio_mod.mix(narration, out_dir / "audio.wav", total,
                          beds=beds, bgm_volume=BGM_VOLUME,
                          loudness=audio_mod.TARGET_LUFS, duck=True)
    log(f"  mixed and normalised to {audio_mod.TARGET_LUFS} LUFS")

    final = out_dir / f"{Path(script_path).stem}.mp4"
    audio_mod.mux(mute, track, final)

    audio_mod.write_srt([(s, e, t) for s, e, t, _h, _en in spans],
                        out_dir / f"{Path(script_path).stem}.srt")
    (out_dir / "shots.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    credit_lines = footage_mod.credits(results)
    if credit_lines:
        (out_dir / "CREDITS.txt").write_text(
            "\n".join(credit_lines), encoding="utf-8")
    write_timeline(out_dir, Path(script_path).stem, lay, orientation, grade,
                   speed, paced(DISSOLVE, speed), total, shot_paths,
                   durations, rows, spans, pieces, chrome,
                   hook_span=((f"「{hook}」", results[0].get("en", ""),
                               caps[0][0], caps[0][1]) if hook_path else None),
                   beds=beds)

    checks = verify_output(final, total, lay, narration=narration)
    if draft:
        import draft as draft_mod
        import footage_draft

        root = None if draft_here else draft_mod.jianying_drafts_dir()
        draft_dir = footage_draft.export(out_dir, root=root)
        log(f"\ndraft  {draft_dir}")
        if root is None and not draft_here:
            log("  Jianying's drafts folder was not found - move this into "
                "it, or set JIANYING_DRAFT_DIR")
        checks += footage_draft.check(out_dir, draft_dir)

    log(f"\nverify  {final}")
    failures = 0
    for name, ok, detail in checks:
        failures += 0 if ok else 1
        log(f"  {'[ok]  ' if ok else '[FAIL]'} {name}  {detail}")

    # Count each kind rather than deriving "everything else is generated":
    # once graphics existed, that subtraction reported three charts as three
    # generated stills and the summary stopped describing the video.
    # Every kind that exists, derived from the rows rather than a hand-written
    # list: this line has now silently dropped a kind twice - "generated" when
    # graphics arrived, "composite" when composites did - and a summary that
    # omits a third of the video is worse than no summary.
    counted = {}
    for row in rows:
        counted[row["kind"]] = counted.get(row["kind"], 0) + 1
    order = ("footage", "composite", "graphic", "generated")
    parts = [f"{counted.get(k, 0)} {label}" for k, label in
             zip(order, ("retrieved", "composites", "graphics", "generated"))]
    for other in sorted(set(counted) - set(order)):
        parts.append(f"{counted[other]} {other}")
    log("\n" + ", ".join(parts) + f", {total:.1f}s")
    if credit_lines:
        log(f"attribution for {len(credit_lines)} clip(s) in CREDITS.txt")
    return final, failures


def main():
    ap = argparse.ArgumentParser(
        description=(__doc__ or "footage build").splitlines()[0])
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", default="", help="output directory")
    ap.add_argument("--orientation", default="landscape",
                    choices=["landscape", "portrait"])
    ap.add_argument("--grade", default="vintage", choices=sorted(fr.GRADES))
    ap.add_argument("--provider", default="")
    ap.add_argument("--speaker", default="")
    ap.add_argument("--speed", type=float, default=None, metavar="X", help=(
        "how fast the whole video runs - narration, shot lengths, the "
        "retrieved footage itself, captions and the hook together. 1.0 is "
        "natural pace; default 1.5"))
    ap.add_argument("--bgm", default="")
    ap.add_argument("--hook", default="", help="opening line; defaults to beat 1")
    ap.add_argument("--topic", default="", help=(
        "short label shown top-left on every made shot. Left blank by "
        "default: a wrong one is worse than none, and nothing in the script "
        "reliably names the video's subject in the narration's own language"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--revoice", action="store_true",
                    help="re-synthesise narration even if cached")
    ap.add_argument("--no-draft", action="store_true",
                    help="write the MP4 only, no Jianying draft")
    ap.add_argument("--draft-here", action="store_true",
                    help="write the draft beside the mp4 instead of into "
                         "Jianying's own drafts folder")
    args = ap.parse_args()

    out = Path(args.out) if args.out else ROOT / "out" / Path(args.script).stem
    _, failures = build(
        args.script, out, orientation=args.orientation, grade=args.grade,
        provider=args.provider or None, speaker=args.speaker or None,
        speed=args.speed, bgm=args.bgm or None, limit=args.limit,
        hook=args.hook or None, revoice=args.revoice, topic=args.topic,
        draft=not args.no_draft, draft_here=args.draft_here)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
