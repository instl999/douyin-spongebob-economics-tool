"""Audio assembly: narration on the timeline, then music under it.

The narration track is *concatenated*, not delayed-and-mixed. Every shot's
length is defined as its own audio length plus a fixed tail, so laying the
clips end to end with those exact tails reproduces the video timeline by
construction. Mixing delayed copies instead would recompute the same offsets a
second time and let rounding drift the two apart over three minutes.
"""
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


def build_narration(pieces, out_path, rate=44100):
    """`pieces` is a list of (audio_path_or_None, seconds). Returns out_path.

    A piece with no audio contributes that many seconds of silence, which is how
    the title and ending cards keep their place in the track.
    """
    out_path = Path(out_path)
    work = out_path.parent / "_narration_parts"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("*.wav"):
        old.unlink()

    parts = []
    for i, (src, seconds) in enumerate(pieces):
        part = work / f"p{i:04d}.wav"
        if src is None:
            silence(part, seconds, rate)
        else:
            # Normalise to one format so concat cannot fail on a mismatch, and
            # pad or trim to the exact slot the timeline gave this shot.
            run([config.FFMPEG, "-y", "-v", "error", "-i", str(src),
                 "-af", f"apad=whole_dur={seconds:.3f}",
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


def mix(narration, out_path, total, bgm=None, bgm_volume=0.10,
        narration_volume=1.0, rate=44100):
    """Narration plus optional looped music, limited, trimmed to `total`."""
    inputs = ["-i", str(narration)]
    chains = [f"[0:a]volume={narration_volume:.3f}[voice]"]
    labels = ["[voice]"]

    if bgm and Path(bgm).exists():
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        fade_out_at = max(0.0, total - 2.0)
        chains.append(
            f"[1:a]volume={bgm_volume:.3f},atrim=0:{total:.3f},"
            f"afade=t=in:st=0:d=1.2,afade=t=out:st={fade_out_at:.3f}:d=2.0[bed]")
        labels.append("[bed]")

    if len(labels) == 1:
        graph = f"{chains[0]};[voice]alimiter=limit=0.97[out]"
    else:
        graph = (";".join(chains) + ";" + "".join(labels) +
                 f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:"
                 f"normalize=0,alimiter=limit=0.97[out]")

    run([config.FFMPEG, "-y", "-v", "error", *inputs,
         "-filter_complex", graph, "-map", "[out]",
         "-t", f"{total:.3f}", "-ar", str(rate), "-ac", "2", str(out_path)])
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
