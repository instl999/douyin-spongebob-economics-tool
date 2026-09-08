"""Assemble retrieved footage into a finished shot sequence.

This is the footage track's answer to `render.py`, and it deliberately does
*not* go through it. `render.py` is built on one premise - a shot is a still
plate, cached and reused, which is why 156 frames cost 4 unique renders. Moving
footage breaks that premise, and with it the frame cache, the Jianying image
export and `verify.check_background`, which asserts the background is static to
within 1.4 grey levels. Rather than weaken all of that for the drawn track, the
footage track composites in ffmpeg and borrows only `textkit` for captions, so
subtitles land in exactly the same place under both.

The stages are:

  locate     find where inside a long source the wanted shot actually is
  prepare    one clip per shot, trimmed to the beat, scaled-and-cropped to
             cover the frame, normalised to the project fps, and graded
  fill_shot  generate a shot for a beat retrieval could not cover
  assemble   cross-dissolve the prepared clips together and burn in captions

Grading is not decoration. Clips arrive from twenty different sources, shot on
different cameras in different decades, and the single thing that makes a
retrieved montage read as one video rather than a scrapbook is putting them all
through the same look. The `vintage` preset is measured off the internet
reference: desaturated, lifted blacks, vignetted, lightly grained.
"""
import subprocess
from pathlib import Path

import config
import footage as footage_mod
import textkit

# Trim this much off the head of a retrieved clip before using it. Stock and
# archival clips routinely open on a slate, a fade-up or a static hold, and the
# useful motion starts a beat later.
HEAD_TRIM = 1.0

# Above this, a source is not one continuous shot and its thumbnail no longer
# stands for its content: it is a film, and the wanted moment has to be found
# inside it. Stock library clips sit well under this; archival newsreels ran
# 89-1080 s in testing.
LONG_SOURCE = 25.0
PROBES = 6

GRADES = {
    # Nothing at all - clips as shot. Correct when the footage is already
    # consistent, e.g. all from one library.
    "none": "",
    # Measured off the internet reference: pulled-down saturation, lifted
    # blacks, a soft vignette and enough grain to hide the upscale on archival
    # material.
    "vintage": ("eq=saturation=0.45:contrast=1.06:brightness=0.02,"
                "curves=all='0/0.06 0.5/0.5 1/0.96',"
                "vignette=PI/5,"
                "noise=alls=6:allf=t"),
    # Cooler and cleaner, for modern B-roll that should look contemporary.
    # The vignette is not decoration. Measured across the three references,
    # edge luma runs 0.50-0.89 of centre luma; this grade had no vignette at
    # all and a finished econ build measured 1.18 - edges *brighter* than the
    # middle, which is most of why it read flat next to them. PI/6 gives 0.70.
    "clean": "eq=saturation=0.92:contrast=1.04,unsharp=5:5:0.4,vignette=PI/6",
}

# A black overlay over stills, as a fraction. `colorlevels` romax is a straight
# multiply, so romax=0.85 is exactly a 15% black layer.
#
# Every reference frame sits under one. It buys three things: photographic
# depth on an otherwise flat generated still, a consistent floor so twenty
# sources stop looking like twenty sources, and headroom for white subtitles.
# Retrieved footage gets less, because it already carries its own exposure.
# Calibrated, not guessed: at 16% a generated still measured luma 83.7,
# below the references' 86-113. At 8% it measures 92.0, inside it.
SCRIM_STILL = 0.08
SCRIM_FOOTAGE = 0.05


def scrim(strength):
    if strength <= 0:
        return ""
    keep = round(1.0 - strength, 3)
    return f"colorlevels=romax={keep}:gomax={keep}:bomax={keep}"


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(map(str, cmd))}\n"
                           f"{(proc.stderr or '')[-1200:]}")
    return proc


def fetch(clip, cache_dir):
    """Download a clip once, keyed by provider and id."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".webm" if clip["provider"] == "commons" else ".mp4"
    path = cache_dir / f"{clip['provider']}_{clip['id']}{suffix}"
    if path.exists() and path.stat().st_size > 0:
        return path
    # Stream to disk rather than through ffmpeg: seeking inside an HTTP webm
    # gave "File ended prematurely" often enough to be worth the round trip.
    data = footage_mod._get(clip["download_url"], timeout=300)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def locate(clip, needs, seconds, src, model=None, probes=PROBES):
    """Find *where inside* a long source the wanted shot actually is.

    Ranking scores a single thumbnail. That is fine for a 15-second stock clip,
    which is one continuous shot, and badly wrong for archival film: the
    switchboard operator that scored 5 in testing lived somewhere inside an
    89-second Dutch newsreel, and cutting five seconds off the head produced a
    bombed-out street instead. Everything reported success - coverage said 40%,
    the render completed, the subtitle was correctly placed - and the picture
    was simply the wrong one.

    So for anything longer than a single shot, sample frames across the source,
    score each on the same rubric, and cut around the best. Costs a handful of
    vision calls, and only for clips already chosen.
    """
    duration = float(clip.get("duration") or 0)
    if duration <= LONG_SOURCE:
        return HEAD_TRIM if duration >= seconds + HEAD_TRIM else 0.0

    key = footage_mod._cache_key("locate", clip.get("provider"), clip.get("id"),
                                 needs, seconds)

    def probe():
        frames_dir = footage_mod.CACHE / "probes"
        frames_dir.mkdir(parents=True, exist_ok=True)
        # Keep clear of the head and tail: titles at the front, credits and
        # fade-outs at the back.
        usable = max(0.0, duration - seconds)
        best = {"start": 0.0, "score": -1, "why": "no probe succeeded"}
        for i in range(probes):
            t = usable * (0.05 + 0.9 * i / max(1, probes - 1))
            shot = frames_dir / f"{key}_{i:02d}.jpg"
            try:
                _run([config.FFMPEG, "-y", "-v", "error", "-i", str(src),
                      "-ss", f"{t:.3f}", "-frames:v", "1",
                      "-vf", "scale=640:-2", str(shot)])
            except RuntimeError:
                continue
            verdict = footage_mod.score_image(shot, needs, model=model)
            if verdict["score"] > best["score"]:
                best = {"start": round(t, 3), **verdict}
            if best["score"] >= 5:      # nothing will beat it; stop paying
                break
        return best

    return footage_mod._cached_json(key, probe)["start"]


def prepare(clip, out_path, seconds, size, fps=30, grade="vintage",
            head_trim=HEAD_TRIM, cache_dir=None, needs=None, model=None):
    """Cut one shot to length and shape. Returns the written path.

    Pass `needs` to have a long source located by content rather than cut from
    the head - see `locate`. Without it, long archival film is cut blind.
    """
    src = fetch(clip, cache_dir or (footage_mod.CACHE / "clips"))
    W, H = size
    if needs:
        start = locate(clip, needs, seconds, src, model=model)
    else:
        # Only skip the head if the clip is long enough to afford it.
        start = head_trim if clip.get("duration", 0) >= seconds + head_trim else 0.0
    # setsar=1 is not cosmetic. Sources arrive with whatever pixel aspect they
    # were encoded with - a 384x288 Commons upscale came out 1571:1440 while
    # generated fill was 20384:20385 - and `concat` refuses to join streams
    # whose SAR differs, even at identical pixel dimensions. xfade tolerated
    # it, so this only surfaced when transitions became hard cuts.
    chain = [f"scale={W}:{H}:force_original_aspect_ratio=increase",
             f"crop={W}:{H}", "setsar=1", f"fps={fps}"]
    if GRADES.get(grade):
        chain.append(GRADES[grade])
    if scrim(SCRIM_FOOTAGE):
        chain.append(scrim(SCRIM_FOOTAGE))
    # A clip shorter than its beat is looped rather than slowed: a slowed clip
    # reads as an effect, a looped one usually just reads as a longer shot.
    loop = ["-stream_loop", "-1"] if clip.get("duration", 0) < seconds else []
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run([config.FFMPEG, "-y", "-v", "error", *loop, "-ss", f"{start:.3f}",
          "-i", str(src), "-t", f"{seconds:.3f}",
          "-vf", ",".join(chain), "-an",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
          "-pix_fmt", "yuv420p", str(out_path)])
    return out_path


# --- generated fill --------------------------------------------------------
#
# Retrieval will not cover every beat, and a hole is worse than an imperfect
# shot: the reference videos never once cut to nothing. The Mach video answers
# the same problem the same way - its three-panel shockwave sequence is the one
# shot in all three references that was *made* rather than found, because no
# footage of that exists either.
#
# Two rules keep a generated shot from announcing itself. It must be
# photographic rather than illustrated, and it must go through the identical
# grade and an equivalent slow move. A still that holds dead still next to
# moving footage reads instantly as a slide.

FILL_REGISTERS = {
    "vintage": ("black and white documentary photograph, 1950s, film grain, "
                "natural available light, candid, archival newsreel still"),
    "clean": ("documentary photograph, natural light, shallow depth of field, "
              "photojournalism, candid, unposed"),
    "none": "documentary photograph, natural light, candid",
}

# Every generated frame carries this. Lettering is the fastest way to tell a
# generated shot from a real one, and a model asked for a photograph will put a
# sign in it unless told not to.
FILL_NEGATIVE = ("no text, no letters, no words, no captions, no watermark, "
                 "no logo, no illustration, no cartoon, not a drawing")

# Slow moves, one per shot, cycled so consecutive fills never match. Values are
# fractions of the oversize margin travelled across the whole shot.
FILL_MOVES = (("x", 0.0, 1.0), ("x", 1.0, 0.0), ("y", 0.0, 1.0), ("y", 1.0, 0.0))
FILL_OVERSIZE = 1.18


def fill_prompt(needs, grade="vintage"):
    register = FILL_REGISTERS.get(grade, FILL_REGISTERS["clean"])
    return f"{needs}. {register}. {FILL_NEGATIVE}"


def fill_shot(needs, out_path, seconds, size, fps=30, grade="vintage",
              move_index=0, cache_dir=None, seed=None):
    """Generate one shot for a beat retrieval could not cover.

    Returns (path, image_path). The image is cached by prompt, so re-running a
    build does not pay for it twice.
    """
    import ark

    W, H = size
    cache_dir = Path(cache_dir or (footage_mod.CACHE / "fill"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    prompt = fill_prompt(needs, grade)
    image = cache_dir / f"{footage_mod._cache_key('fill', prompt, W, H)}.png"
    if not image.exists():
        ark.generate_image(
            prompt, image,
            size=ark.SIZE_LANDSCAPE if W >= H else ark.SIZE_PORTRAIT,
            seed=seed)

    axis, start, end = FILL_MOVES[move_index % len(FILL_MOVES)]
    big_w, big_h = int(W * FILL_OVERSIZE), int(H * FILL_OVERSIZE)
    # crop takes `t` in its position expressions, which makes a linear move a
    # one-liner. zoompan can do a true push but is fussy about frame counts and
    # silently produces a stutter when it disagrees with -t.
    travel_x = (f"(in_w-out_w)*({start}+({end}-{start})*t/{seconds:.3f})"
                if axis == "x" else "(in_w-out_w)/2")
    travel_y = (f"(in_h-out_h)*({start}+({end}-{start})*t/{seconds:.3f})"
                if axis == "y" else "(in_h-out_h)/2")
    chain = [f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase",
             f"crop={W}:{H}:x='{travel_x}':y='{travel_y}'", "setsar=1",
             f"fps={fps}"]
    if GRADES.get(grade):
        chain.append(GRADES[grade])
    if scrim(SCRIM_STILL):
        chain.append(scrim(SCRIM_STILL))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run([config.FFMPEG, "-y", "-v", "error", "-loop", "1", "-i", str(image),
          "-t", f"{seconds:.3f}", "-vf", ",".join(chain), "-an",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
          "-pix_fmt", "yuv420p", str(out_path)])
    return out_path, image


# --- captions --------------------------------------------------------------

# All three references carry a second, smaller English line under the Chinese.
# Measured off the internet reference at 1280x720 and scaled: the English sits
# just under the Chinese at roughly 55% of its size.
EN_SCALE = 0.50
# Clear space between the bottom of the Chinese block and the centre of the
# English line, in multiples of the English size.
EN_GAP = 0.85
# The lowest the English line may reach, as a fraction of frame height. A
# two-line Chinese caption pushes the whole block down; past this it is
# shifted up rather than allowed to run off the bottom.
CAPTION_FLOOR = 0.975
# The opening hook: bigger than a subtitle, centred high in frame, and
# never allowed to grow down into the subtitle band beneath it.
HOOK_SCALE = 1.15
HOOK_CENTRE = 0.44
HOOK_FLOOR = 0.72


def _stacked_caption(lay, look, text, english, highlight=None,
                     center_y=None, scale=1.0, floor=CAPTION_FLOOR):
    """Chinese over English, positioned off the *rendered* block, not a guess.

    Offsetting the English by a fixed multiple of the Chinese size works only
    while the Chinese is one line. On a two-line caption the second line landed
    straight on top of the English, because the offset was measured from the
    Chinese block's centre and the block had grown downward around it. Only the
    real block height knows how tall it actually got.

    `center_y` and `scale` exist so the opening hook goes through this same
    code. It did not, and it had the identical bug independently: a two-line
    hook printed its translation across its own second line.
    """
    from PIL import Image

    caption = look["caption"]
    main_px = max(12, int(round(lay.subtitle_font_px() * scale)))
    anchor_y = lay.subtitle_center_y if center_y is None else int(center_y)
    en_px = max(12, int(round(main_px * EN_SCALE)))

    def draw(center_y, size, body, hl=None):
        return textkit.render_caption(
            lay.size, body, size=size, center_y=center_y,
            max_width=lay.subtitle_max_px,
            fill=tuple(caption["fill"]) + (255,),
            stroke_fill=tuple(caption["stroke_fill"]) + (255,),
            highlight_fill=tuple(caption["highlight_fill"]) + (255,),
            highlight=hl)

    def block(body, size):
        """Exact height and stroke padding of a rendered caption block.

        Mirrors render_caption's own layout. Measuring the returned bbox
        instead does not work: it is clamped to the frame, so a block that
        already overflows reports a bottom of exactly the frame height and the
        overflow reads as far smaller than it is.
        """
        lines = textkit.wrap(body, size, lay.subtitle_max_px)
        if not lines:
            return 0, 0
        lh = textkit.line_height(size, True)
        total = lh * len(lines) + int(size * 0.20) * (len(lines) - 1)
        return total, max(2, round(size * 0.085)) + 6

    zh_total, zh_pad = block(text, main_px)
    en_total, en_pad = block(english, en_px) if english else (0, 0)
    inner_gap = int(round(en_px * EN_GAP))

    zh_top = anchor_y - zh_total // 2
    en_top = zh_top + zh_total + inner_gap
    bottom = (en_top + en_total + en_pad) if en_total else (zh_top + zh_total + zh_pad)
    # Only ever move the caption up, and only as far as it must go: the
    # references keep it on the same scanline shot after shot, so a one-line
    # beat must not drift just because a neighbouring beat needed two.
    shift = max(0, bottom - int(lay.height * floor))

    zh_layer, _ = draw(anchor_y - shift, main_px, text, highlight)
    if not en_total:
        return zh_layer
    en_layer, _ = draw(en_top + en_total // 2 - shift, en_px, english)
    if en_layer is None:
        return zh_layer
    return (en_layer if zh_layer is None
            else Image.alpha_composite(zh_layer, en_layer))


def caption_pngs(spans, lay, look, workdir):
    """One transparent PNG per caption span, Chinese over English.

    Geometry comes from the same `Layout` and the same `textkit.render_caption`
    the drawn track uses, so a subtitle lands on the identical scanline in both
    tracks - 90.3% of frame height - without the rule being written down twice.

    Both lines are flattened into one PNG rather than overlaid separately: the
    assemble filter graph chains an overlay per caption, and doubling that on a
    five-minute video doubles a graph that is already the slowest part.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, span in enumerate(spans):
        start, end, text, highlight = span[:4]
        english = span[4] if len(span) > 4 else ""
        layer = _stacked_caption(lay, look, text, english, highlight)
        if layer is None:
            continue
        path = workdir / f"cap_{i:04d}.png"
        layer.save(path)
        out.append((start, end, path))
    return out


def hook_png(text, english, lay, look, out_path, max_lines=1):
    """The centred opening line the references overlay on their first shot.

    They do not use a title card at all: the video opens straight on footage
    with the hook set in the middle of frame, bracketed, the translation under
    it, and the ordinary subtitle still running along the bottom.

    Returns None when the line is too long to be a hook. The reference hook is
    nine characters - 「你一定听过这个声音」 - and a beat that needs two lines at
    hook size is not a hook, it is the subtitle printed twice in a larger font.
    The bottom subtitle already carries the words either way.
    """
    size = int(round(lay.subtitle_font_px() * HOOK_SCALE))
    body = f"「{text}」"
    if len(textkit.wrap(body, size, lay.subtitle_max_px)) > max_lines:
        return None
    layer = _stacked_caption(lay, look, body, english,
                             center_y=int(lay.height * HOOK_CENTRE),
                             scale=HOOK_SCALE, floor=HOOK_FLOOR)
    if layer is None:
        return None
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    layer.save(out_path)
    return Path(out_path)


# --- assembly --------------------------------------------------------------

def total_duration(durations, dissolve):
    """Runtime after cross-dissolves. Each transition eats `dissolve` seconds."""
    return sum(durations) - dissolve * max(0, len(durations) - 1)


def xfade_offsets(durations, dissolve):
    """When each xfade starts.

    The k-th transition begins at sum(d[0..k]) - (k+1)*dissolve, because every
    transition already completed has eaten `dissolve` seconds out of the
    running total. Getting this wrong does not error - it silently drifts the
    back half of the video out of sync with the narration, which is why it is
    a separate function with a test rather than arithmetic inside a loop.
    """
    offsets, elapsed = [], 0.0
    for k, d in enumerate(durations):
        if k == 0:
            elapsed = d
            continue
        offsets.append(elapsed - dissolve)
        elapsed += d - dissolve
    return offsets


def chrome_png(topic, lay, grade, out_path):
    """The persistent frame furniture, as two transparent overlays.

    `base` is the rule's track, the topic eyebrow and the baseline: fixed for
    the whole video, so it sits on footage and made shots alike and cannot
    blink out at a shot boundary. `rule` is a plain accent strip the assembler
    slides in from the left to fill the track.

    Returns {"base": path, "rule": path}.
    """
    from PIL import Image

    import motion

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    motion.furniture(base, motion.palette(grade), {"topic": topic})
    base.save(out_path, "PNG")

    strip = out_path.with_name(out_path.stem + "_rule.png")
    width = lay.size[0]
    Image.new("RGBA", (width, 3),
              tuple(motion.palette(grade)["accent"]) + (255,)).save(strip, "PNG")
    return {"base": out_path, "rule": strip}


def assemble(shot_paths, durations, out_path, dissolve=0.5, captions=None,
             chrome=None, grade="clean"):
    """Cross-dissolve the prepared shots together and burn in captions.

    `chrome` is the path from `chrome_png`; its progress rule is filled here
    with a single drawbox whose width is an expression in t, rather than by
    compositing a different PNG for every frame.
    """
    if not shot_paths:
        raise ValueError("nothing to assemble")
    inputs, filters = [], []
    for path in shot_paths:
        inputs += ["-i", str(path)]

    # Hard cuts are the default because that is what the references do. The
    # internet reference has 59 detectable cuts in 309 s at a 5.2 s mean shot -
    # essentially every boundary is a cut, not a fade. The drawn track's
    # measured rule is the opposite (whole-shot dissolves), and carrying that
    # convention across without re-measuring made every transition a cross-fade
    # here, which reads as a slideshow and hides the cut from scene detection.
    current = "0:v"
    if dissolve > 0:
        for k, offset in enumerate(xfade_offsets(durations, dissolve), start=1):
            label = f"x{k}"
            filters.append(
                f"[{current}][{k}:v]xfade=transition=fade:"
                f"duration={dissolve}:offset={offset:.3f}[{label}]")
            current = label
    elif len(shot_paths) > 1:
        streams = "".join(f"[{i}:v]" for i in range(len(shot_paths)))
        filters.append(f"{streams}concat=n={len(shot_paths)}:v=1:a=0[cat]")
        current = "cat"
    elapsed = total_duration(durations, dissolve)

    if chrome:
        import motion

        # The rule is a strip slid in from the left, not a box grown in place.
        # `drawbox` accepts `w=iw*t/D`, exits 0, writes a file - and draws the
        # box at full width on every frame: in this build its size expressions
        # are not re-evaluated per frame. `overlay`'s x IS, exactly, so the
        # animation lives there. Verified by measuring the filled fraction at
        # three timestamps rather than by reading the exit code.
        inputs += ["-i", str(chrome["base"]), "-i", str(chrome["rule"])]
        base_idx = len(shot_paths)
        filters.append(f"[{current}][{base_idx}:v]overlay=0:0[chrome]")
        filters.append(
            f"[chrome][{base_idx + 1}:v]overlay="
            f"x=-w+w*t/{max(0.001, elapsed):.3f}:y=H*{motion.RULE_Y}[rule]")
        current = "rule"

    if captions:
        for i, (start, end, png) in enumerate(captions):
            inputs += ["-i", str(png)]
            idx = len(shot_paths) + (2 if chrome else 0) + i
            label = f"c{i}"
            filters.append(
                f"[{current}][{idx}:v]overlay=0:0:"
                f"enable='between(t,{start:.3f},{end:.3f})'[{label}]")
            current = label

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [config.FFMPEG, "-y", "-v", "error", *inputs]
    if filters:
        cmd += ["-filter_complex", ";".join(filters), "-map", f"[{current}]"]
    else:
        cmd += ["-map", "0:v"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(out_path)]
    _run(cmd)
    return out_path, elapsed


def contact_sheet(video, out_path, cols=5, rows=2, width=320):
    """One frame per tile across the whole video, for looking at the result.

    The failure mode of an automated montage is not a crash, it is a sequence
    of individually-plausible shots that do not add up. That is only visible by
    looking, so make looking cheap.
    """
    n = cols * rows
    dur = float(subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True).stdout.strip() or 0)
    if dur <= 0:
        raise RuntimeError(f"could not read duration of {video}")
    # fps= resamples to an exact cadence; the `select='lt(mod(t,step),...)'`
    # form drops or duplicates a frame when step lands between frame times, and
    # `tile` then pads the shortfall with a black cell that reads as a bug in
    # the video rather than in the contact sheet.
    step = dur / n
    _run([config.FFMPEG, "-y", "-v", "error", "-i", str(video),
          "-vf", (f"fps=1/{step:.6f},scale={width}:-1,"
                  f"tile={cols}x{rows}:padding=2:margin=2"),
          "-frames:v", "1", str(out_path)])
    return out_path
