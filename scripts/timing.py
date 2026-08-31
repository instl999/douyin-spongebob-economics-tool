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
"""
import math

SECONDS_PER_CHAR = 0.1610
CLIP_OVERHEAD = 0.484
# Speech rate is clamped well inside the API's [-50, 100] range: past about
# +/-20% the narration stops sounding like a person reading and starts sounding
# like a tape being played at the wrong speed.
MIN_SPEED = 0.85
MAX_SPEED = 1.20


def count(text):
    return len([c for c in (text or "") if not c.isspace()])


def clip_seconds(text, speed=1.0):
    """How long one shot's narration will be."""
    n = count(text)
    if not n:
        return 0.0
    return (SECONDS_PER_CHAR * n + CLIP_OVERHEAD) / max(speed, 0.1)


def estimate(script, *, shot_seconds=5.0, tail_pad=0.35, title_seconds=2.6,
             ending_seconds=4.0, speed=1.0, split=None):
    """Predicted length of the finished video, and where it goes."""
    if split is None:
        import plan as plan_mod
        split = plan_mod.split_script
    beats = split(script, shot_seconds)
    per_shot = [clip_seconds(b, speed) + tail_pad for b in beats]
    narration = sum(per_shot)
    return {
        "shots": len(beats),
        "characters": count(script),
        "narration": narration,
        "cards": title_seconds + ending_seconds,
        "total": narration + title_seconds + ending_seconds,
        "per_shot": per_shot,
        "beats": beats,
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
                  title_seconds=2.6, ending_seconds=4.0):
    """Pick a speech rate that lands near `target_seconds`.

    Returns {"speed", "estimate", "ok", "note"}. `ok` is False when the target
    is simply not reachable by changing the delivery, which is a script-length
    problem and has to be said plainly rather than papered over: squeezing a
    95-second script into 60 seconds means cutting words, not talking faster.
    """
    low, high = reachable(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                          title_seconds=title_seconds,
                          ending_seconds=ending_seconds)
    natural = estimate(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                       title_seconds=title_seconds,
                       ending_seconds=ending_seconds, speed=1.0)

    if target_seconds is None:
        return {"speed": 1.0, "estimate": natural, "ok": True,
                "note": "no target given; using natural pace"}

    if not (low <= target_seconds <= high):
        overshoot = target_seconds < low
        chars_now = count(script)
        # At the fastest sane delivery, how much script actually fits?
        budget = max(0.0, target_seconds - title_seconds - ending_seconds)
        per_shot_overhead = (CLIP_OVERHEAD / MAX_SPEED) + tail_pad
        shots = max(1, natural["shots"])
        fits = int(max(0.0, budget - per_shot_overhead * shots)
                   * MAX_SPEED / SECONDS_PER_CHAR)
        note = (
            f"{target_seconds:.0f}s is not reachable from this script: it runs "
            f"{low:.0f}-{high:.0f}s between the fastest and slowest sane "
            f"delivery. "
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
    speed = round((lo + hi) / 2, 3)
    final = estimate(script, shot_seconds=shot_seconds, tail_pad=tail_pad,
                     title_seconds=title_seconds,
                     ending_seconds=ending_seconds, speed=speed)
    return {"speed": speed, "estimate": final, "ok": True,
            "note": f"speaking at {speed:.2f}x lands at {final['total']:.1f}s"}


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
