"""Predicting how long a finished video will be, before spending anything.

The estimate exists so the settings conversation can happen up front: a user
asking for a 60-second video needs to hear that their script is 95 seconds
*before* any images are generated, not after.

The model is fitted to 45 real clips from this voice:

    duration = 0.161 * characters + 0.484

Mean absolute error 0.19s per clip, against 0.36s for a flat characters-per-
second rate. The intercept is the leading and trailing silence the service adds
to every clip, and it is not a rounding detail - across fifteen shots it is
seven seconds of the running time, which is the difference between hitting a
target and missing it.

## Speed is a playback rate, not a voice setting

`speed` here means the whole video: 1.5 is the 1.0x cut played 1.5x faster.
Narration, shot lengths, card holds, dissolves and subtitles all divide by it
together, so nothing can drift out of step with anything else. The rule the
rest of the pipeline follows is one line long:

    a duration divides by speed, a per-second rate multiplies by it,
    and anything measured in pixels does not move.

It used to mean the speech rate alone, which is the one thing that cannot be
sped up on its own: the voice arrived early and the picture sat there holding
its old length.

Narration is **re-spoken** faster rather than resampled - the service takes a
speech rate directly - so there is no pitch shift, and shot lengths still come
from the audio that actually came back rather than from an assumption about
it. A shot cannot drift out of sync with its own narration because it is
measured from it.
"""

SECONDS_PER_CHAR = 0.1610
CLIP_OVERHEAD = 0.484

# 1.0 is the baseline: every duration in a project file, in styles.json and in
# this pipeline's own constants is written at 1.0 and means what it says.
BASELINE_SPEED = 1.0
# What a build uses when the project does not say. The measured references run
# faster than this pipeline's natural pace, and 1.0 reads as slow beside them.
DEFAULT_SPEED = 1.5
# The bounds are the speech service's own: speech_rate is a percentage offset
# in [-50, 100], so outside 0.5x-2.0x the voice could no longer be spoken at
# the rate the picture is cut to, and the two would separate again.
MIN_SPEED = 0.5
MAX_SPEED = 2.0


def clamp(speed):
    """A usable global speed, whatever the project file says."""
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    if value <= 0:
        return DEFAULT_SPEED
    return min(MAX_SPEED, max(MIN_SPEED, value))


def scale(seconds, speed):
    """A baseline duration in timeline time. The one conversion there is."""
    return float(seconds) / clamp(speed)


def count(text):
    return len([c for c in (text or "") if not c.isspace()])


def clip_seconds(text, speed=BASELINE_SPEED):
    """How long one shot's narration will be.

    Through `clamp`, like `scale` above, and that matters more than it looks:
    two different guards on the same parameter would divide the narration by
    one factor and the pads around it by another, which is the exact mismatch
    this whole setting exists to remove - reached from inside the module that
    defines it.
    """
    n = count(text)
    if not n:
        return 0.0
    return (SECONDS_PER_CHAR * n + CLIP_OVERHEAD) / clamp(speed)


def estimate(script, *, shot_seconds=5.0, tail_pad=0.35, title_seconds=2.6,
             ending_seconds=4.0, speed=BASELINE_SPEED, split=None):
    """Predicted length of the finished video, and where it goes.

    Every duration argument is a **baseline** one, as written in the project
    file; `speed` is applied here. Passing pre-scaled values would apply it
    twice, which is why nothing upstream divides first.
    """
    if split is None:
        import plan as plan_mod
        split = plan_mod.split_script
    beats = split(script, shot_seconds)
    pad = scale(tail_pad, speed)
    # The last shot carries no tail - it has no neighbour to keep off its
    # breath, and the pad there was dead air after the final subtitle. The
    # estimate is what a user is told before anything is paid for, so it has
    # to be the length the video actually comes out at, not 0.35s more.
    per_shot = [clip_seconds(b, speed) + (0.0 if i == len(beats) - 1 else pad)
                for i, b in enumerate(beats)]
    narration = sum(per_shot)
    cards = scale(title_seconds + ending_seconds, speed)
    return {
        "shots": len(beats),
        "characters": count(script),
        "narration": narration,
        "cards": cards,
        "total": narration + cards,
        "per_shot": per_shot,
        "beats": beats,
        "speed": clamp(speed),
    }

def reachable(script, *, shot_seconds=5.0, tail_pad=0.35, title_seconds=2.6,
              ending_seconds=4.0):
    """The shortest and longest this script can honestly be made."""
    fastest = estimate(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                       title_seconds=title_seconds,
                       ending_seconds=ending_seconds, speed=MAX_SPEED)
    slowest = estimate(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                       title_seconds=title_seconds,
                       ending_seconds=ending_seconds, speed=MIN_SPEED)
    return fastest["total"], slowest["total"]


def fit_to_target(script, target_seconds, *, shot_seconds=5.0, tail_pad=0.35,
                  title_seconds=2.6, ending_seconds=4.0,
                  speed=DEFAULT_SPEED):
    """Pick a global speed that lands near `target_seconds`.

    `speed` is what the project asked for, and is what comes back when there is
    no target to fit. A target overrides it, because a video with a length to
    hit has already answered the question this setting asks.

    Returns {"speed", "estimate", "ok", "note"}. `ok` is False when the target
    is simply not reachable, which is a script-length problem and has to be
    said plainly rather than papered over: squeezing a 95-second script into 60
    seconds means cutting words, not only playing them faster.
    """
    low, high = reachable(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                          title_seconds=title_seconds,
                          ending_seconds=ending_seconds)
    asked = clamp(speed)
    natural = estimate(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                       title_seconds=title_seconds,
                       ending_seconds=ending_seconds, speed=asked)

    if target_seconds is None:
        return {"speed": asked, "estimate": natural, "ok": True,
                "note": f"no target given; running at {asked:.2f}x"}

    if not (low <= target_seconds <= high):
        overshoot = target_seconds < low
        chars_now = count(script)
        # At the fastest sane speed, how much script actually fits? The cards
        # and the tails are shortened by that speed too, so they have to be
        # measured at it rather than at their written length.
        budget = max(0.0, target_seconds
                     - scale(title_seconds + ending_seconds, MAX_SPEED))
        per_shot_overhead = (CLIP_OVERHEAD + tail_pad) / MAX_SPEED
        shots = max(1, natural["shots"])
        fits = int(max(0.0, budget - per_shot_overhead * shots)
                   * MAX_SPEED / SECONDS_PER_CHAR)
        note = (
            f"{target_seconds:.0f}s is not reachable from this script: it runs "
            f"{low:.0f}-{high:.0f}s between {MAX_SPEED:.2g}x and "
            f"{MIN_SPEED:.2g}x. "
            + (f"To land near {target_seconds:.0f}s the script needs to be about "
               f"{fits} characters instead of {chars_now} - cut roughly "
               f"{max(0, chars_now - fits)}."
               if overshoot else
               f"To fill {target_seconds:.0f}s the script needs roughly "
               f"{int((target_seconds - high) * MAX_SPEED / SECONDS_PER_CHAR)} "
               "more characters, or longer card holds."))
        return {"speed": MAX_SPEED if overshoot else MIN_SPEED,
                "estimate": natural, "ok": False, "note": note}

    # Bisect on speed; the relationship is monotone and smooth.
    lo, hi = MIN_SPEED, MAX_SPEED
    for _ in range(40):
        mid = (lo + hi) / 2
        total = estimate(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                         title_seconds=title_seconds,
                         ending_seconds=ending_seconds, speed=mid)["total"]
        if total > target_seconds:
            lo = mid
        else:
            hi = mid
    fitted = round((lo + hi) / 2, 3)
    final = estimate(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                     title_seconds=title_seconds,
                     ending_seconds=ending_seconds, speed=fitted)
    return {"speed": fitted, "estimate": final, "ok": True,
            "note": f"{fitted:.2f}x lands at {final['total']:.1f}s"}


def describe(result):
    """A few lines an operator can read out to whoever asked for the video."""
    est = result["estimate"]
    lines = [
        f"  script      {est['characters']} characters",
        f"  shots       {est['shots']}",
        f"  narration   {est['narration']:.1f}s",
        f"  cards       {est['cards']:.1f}s",
        f"  total       {est['total']:.1f}s at {result['speed']:.2f}x speed",
    ]
    if not result["ok"]:
        lines.append(f"  ! {result['note']}")
    return "\n".join(lines)
