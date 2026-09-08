"""Audio assembly: narration on the timeline, then music under it.

The narration track is *concatenated*, not delayed-and-mixed. Every shot's
length is defined as its own audio length plus a fixed tail, so laying the
clips end to end with those exact tails reproduces the video timeline by
construction. Mixing delayed copies instead would recompute the same offsets a
second time and let rounding drift the two apart over three minutes.
"""
import json
import re
import subprocess
from pathlib import Path

import config


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(str(c) for c in cmd)}\n"
                           f"{proc.stderr[-2000:]}")
    return proc.stdout


def silence(path, seconds, rate=44100):
    run([config.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"anullsrc=r={rate}:cl=stereo", "-t", f"{max(seconds, 0.001):.3f}",
         "-c:a", "pcm_s16le", str(path)])
    return path


# The title card's stinger lands first and the title is read over its decay.
# Measured off the cue: the impact peaks in its first 0.25 s and is 10 dB down
# by 0.5 s, so a voice starting at 0.45 s speaks into the tail rather than over
# the hit. It lives here because the draft writer needs the same number to place
# the same two clips, and two copies of an offset that must agree is how a
# soundtrack drifts out of sync with its own draft.
TITLE_SFX_LEAD = 0.45


def build_narration(pieces, out_path, rate=44100):
    """`pieces` is a list of (audio_path_or_None, seconds[, lead]). -> out_path.

    A piece with no audio contributes that many seconds of silence, which is how
    the ending card keeps its place in the track.

    The optional third element delays the audio inside its own slot. The title
    card needs it: its stinger has to land before the voice starts, and padding
    only ever went on the END of a piece, so there was nowhere to put an
    opening beat. A missing `lead` means zero, so every existing two-element
    caller is unchanged.
    """
    out_path = Path(out_path)
    work = out_path.parent / "_narration_parts"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("*.wav"):
        old.unlink()

    parts = []
    for i, piece in enumerate(pieces):
        src, seconds = piece[0], piece[1]
        lead = float(piece[2]) if len(piece) > 2 else 0.0
        part = work / f"p{i:04d}.wav"
        if src is None:
            silence(part, seconds, rate)
        else:
            # Normalise to one format so concat cannot fail on a mismatch, and
            # pad or trim to the exact slot the timeline gave this shot.
            delay = ""
            if lead > 0:
                ms = int(round(lead * 1000))
                delay = f"adelay={ms}|{ms},"
            run([config.FFMPEG, "-y", "-v", "error", "-i", str(src),
                 "-af", f"{delay}apad=whole_dur={seconds:.3f}",
                 "-t", f"{seconds:.3f}", "-ar", str(rate), "-ac", "2",
                 "-c:a", "pcm_s16le", str(part)])
        parts.append(part)

    listing = work / "concat.txt"
    listing.write_text(
        "".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    run([config.FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c:a", "pcm_s16le", "-ar", str(rate), "-ac", "2",
         str(out_path)])
    return out_path


# What the live-action references measure at: -16.4 to -18.7 LUFS integrated,
# with a loudness range of 3.1-3.5 LU on two of the three, and no silence
# longer than 0.4 s in five to seven minutes. That narrow a range is not an
# accident of the material - it is a compressed bed under compressed narration,
# and it is a large part of why those videos read as expensive.
# See references/footage-findings.md.
TARGET_LUFS = -17.0
TARGET_LRA = 3.5
TARGET_PEAK = -1.5


def measure(path):
    """Integrated loudness and range of a track, via ebur128. {} on failure."""
    proc = subprocess.run(
        [config.FFMPEG, "-nostats", "-i", str(path), "-af", "ebur128",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = {}
    for key in ("I", "LRA"):
        for line in (proc.stderr or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}:"):
                try:
                    out[key] = float(stripped.split()[1])
                except (IndexError, ValueError):
                    pass
    return out


def normalize(src, out_path, target_i=TARGET_LUFS, target_lra=TARGET_LRA,
              target_peak=TARGET_PEAK, rate=44100):
    """Bring a finished track to the reference loudness without flattening it.

    Measure first, then apply one fixed gain. loudnorm is used only to measure.

    Single-pass loudnorm runs in dynamic mode: it does not just move the level,
    it *compresses toward* the LRA target, and raising that target does not
    release it. Measured on one mix whose natural range was 3.6 LU:

        loudnorm=I=-17:LRA=3.5   ->  I -17.7, LRA 2.1
        loudnorm=I=-17:LRA=7     ->  I -17.9, LRA 2.7
        loudnorm=I=-17:LRA=11    ->  I -17.9, LRA 2.7

    Passing measured values with `linear=true` is supposed to avoid that, and
    is a request rather than a guarantee - loudnorm silently reverts to dynamic
    when the required gain would push the true peak past TP. Two mixes 0.2 LU
    apart went 3.6 -> 3.6 and 3.4 -> 2.1 through the identical call, which is
    exactly the kind of failure that shows up as "sounds flat" and nothing else.

    So the gain is computed here and applied with `volume`, capped so the peak
    cannot exceed TP. A fixed gain cannot change LRA at all, which makes the
    result deterministic: whatever range the mix had, it keeps.
    """
    probe = subprocess.run(
        [config.FFMPEG, "-hide_banner", "-i", str(src),
         "-af", f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_peak}"
                ":print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    measured = None
    blob = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", probe.stderr or "", re.S)
    if blob:
        try:
            measured = json.loads(blob.group(0))
        except json.JSONDecodeError:
            measured = None

    if measured:
        # Apply the gain ourselves rather than asking loudnorm for linear mode.
        # `linear=true` is a request, not a guarantee: loudnorm silently drops
        # back to dynamic when the gain would push the true peak past TP, and
        # the only symptom is a crushed LRA. Two mixes 0.2 LU apart went 3.6 ->
        # 3.6 and 3.4 -> 2.1 through the identical call. A fixed gain cannot
        # change LRA at all, so this is deterministic.
        try:
            input_i = float(measured["input_i"])
            input_tp = float(measured["input_tp"])
        except (KeyError, TypeError, ValueError):
            input_i = input_tp = None
        if input_i is not None:
            wanted = target_i - input_i
            headroom = target_peak - input_tp
            gain = min(wanted, headroom)
            # level=false matters: alimiter defaults to level=true, which
            # auto-levels the result up to the ceiling and undoes the gain we
            # just computed. With it left on, a track aimed at -17 LUFS came
            # out at -15.7.
            chain = (f"volume={gain:.3f}dB,"
                     f"alimiter=limit={10 ** (target_peak / 20):.4f}:level=false")
            run([config.FFMPEG, "-y", "-v", "error", "-i", str(src),
                 "-af", chain, "-ar", str(rate), "-ac", "2",
                 "-c:a", "pcm_s16le", str(out_path)])
            return out_path

    # No usable measurement: fall back to single-pass loudnorm, which is
    # over-compressed but correctly levelled - better than not normalising.
    run([config.FFMPEG, "-y", "-v", "error", "-i", str(src),
         "-af", f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_peak}",
         "-ar", str(rate), "-ac", "2", "-c:a", "pcm_s16le", str(out_path)])
    return out_path


def mix(narration, out_path, total, bgm=None, bgm_volume=0.10,
        narration_volume=1.0, rate=44100, cues=(), cue_volume=0.34,
        loudness=None, duck=False):
    """Narration, optional music, and any sound cues, limited and trimmed.

    `cues` is [(seconds, name, path[, gain])] from sfx.plan. Each one becomes
    its own ffmpeg input delayed to its moment, which keeps the mix a single
    pass - the alternative, rendering a cue bed and overlaying it, means
    writing and reading another full-length wav for what is usually under a
    second of audio. The optional fourth element overrides `cue_volume` for one
    cue: the title card's stinger is a deliberate accent and sits well above
    the level the library cues want.

    `loudness` is a target LUFS; when given the mix is normalised to it as a
    final step. Left None the track is only limited, which is what the drawn
    track has always done - changing its sound is not this parameter's job.

    `duck` pulls the music under the voice. Worth +0.3 LU of loudness range at
    a 0.10 bed on the footage track, and off by default because the drawn
    track's mix was measured without it.
    """
    inputs = ["-i", str(narration)]
    has_bed = bool(bgm and Path(bgm).exists())
    # Only split the voice when something downstream keys off it. An
    # unconnected filter output is a hard error, not a warning, so a spare
    # [voicekey] would break every music-free mix.
    ducking = has_bed and duck
    chains = [f"[0:a]volume={narration_volume:.3f}"
              + (",asplit=2[voice][voicekey]" if ducking else "[voice]")]
    labels = ["[voice]"]
    # Counted, not derived from len(inputs): a looped input is four argv items
    # and a plain one is two, so halving the list numbers every cue wrongly and
    # ffmpeg rejects the whole graph with "Invalid file index".
    count = 1

    if has_bed:
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        count += 1
        fade_out_at = max(0.0, total - 2.0)
        chains.append(
            f"[1:a]volume={bgm_volume:.3f},atrim=0:{total:.3f},"
            f"afade=t=in:st=0:d=1.2,afade=t=out:st={fade_out_at:.3f}:d=2.0[bed0]")
        if ducking:
            # An earlier measurement said ducking did nothing; that was taken
            # through single-pass loudnorm, whose own compression swamped the
            # effect. Re-measure audio changes downstream of normalisation, not
            # upstream of it.
            chains.append(
                f"[bed0][voicekey]sidechaincompress=threshold=0.03:ratio=8:"
                f"attack=25:release=350[bed]")
        else:
            chains.append("[bed0]anull[bed]")
        labels.append("[bed]")

    for cue in cues:
        when, _name, path = cue[0], cue[1], cue[2]
        gain = float(cue[3]) if len(cue) > 3 else cue_volume
        index = count
        count += 1
        inputs += ["-i", str(path)]
        delay = max(0, int(round(float(when) * 1000)))
        chains.append(
            f"[{index}:a]volume={gain:.3f},aformat=channel_layouts=stereo,"
            f"adelay={delay}|{delay}[sfx{index}]")
        labels.append(f"[sfx{index}]")

    if len(labels) == 1:
        graph = f"{chains[0]};[voice]alimiter=limit=0.97[out]"
    else:
        graph = (";".join(chains) + ";" + "".join(labels) +
                 f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:"
                 f"normalize=0,alimiter=limit=0.97[out]")

    out_path = Path(out_path)
    target = out_path if loudness is None else out_path.with_suffix(".raw.wav")
    run([config.FFMPEG, "-y", "-v", "error", *inputs,
         "-filter_complex", graph, "-map", "[out]",
         "-t", f"{total:.3f}", "-ar", str(rate), "-ac", "2", str(target)])
    if loudness is not None:
        normalize(target, out_path, target_i=loudness, rate=rate)
        target.unlink(missing_ok=True)
    return out_path


def mux(video, audio, out_path):
    run([config.FFMPEG, "-y", "-v", "error", "-i", str(video), "-i", str(audio),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", str(out_path)])
    return out_path


def write_srt(entries, out_path):
    """entries: (start, end, text). A sidecar for reposting or translation."""
    def stamp(seconds):
        ms = int(round(seconds * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines.append(f"{i}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return out_path
