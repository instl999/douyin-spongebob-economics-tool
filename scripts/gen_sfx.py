"""Synthesise the sound-effect library.

    python scripts/gen_sfx.py assets/sfx

The first version of this made six sounds out of bare sine waves and white
noise, and they were the reason the sound design did not fit. Measured:
`ding`, `coin` and `pop` had a spectral flatness of 0.001-0.003, which is
another way of saying they were pure tones - a test bench, not a bell or a
coin. `whoosh` was white noise ring-modulated by a sine, so it hissed at a
fixed 11 kHz instead of moving. Nothing in the library had a body.

What separates a production effect from a beep is mostly three things, and all
three are cheap:

* **Inharmonic partials.** A bell is not a sine. Its overtones sit at ratios
  like 2.76 and 5.40, not 2 and 3, and that is what makes it read as metal.
* **Movement.** A whoosh is a filter sweeping, and the pitch bends with it.
  A static spectrum reads as a tone however much noise is in it.
* **A transient.** Almost every punchy effect starts with a few milliseconds
  of broadband click. Without it the sound fades in and feels soft.

Everything here is generated, so there is no licensing question and the whole
library rebuilds from one command. Sounds are grouped by what they are *for*,
because that is how sfx.py chooses between them.
"""
import argparse
import wave
from pathlib import Path

import numpy as np
from scipy import signal

SR = 44100
RNG = np.random.default_rng(7)          # reproducible: same library every run


# --- building blocks -------------------------------------------------------

def _t(duration):
    return np.linspace(0, duration, max(1, int(SR * duration)), endpoint=False)


def _decay(t, rate, attack=0.002):
    """Exponential fall with a short attack, so nothing starts with a click."""
    rise = 1.0 - np.exp(-t / max(attack, 1e-6))
    return rise * np.exp(-t * rate)


def _sweep(t, f0, f1, curve=1.0):
    """A tone gliding f0 -> f1. Phase is integrated, not sampled."""
    k = (t / max(t[-1], 1e-9)) ** curve
    freq = f0 + (f1 - f0) * k
    return np.sin(2 * np.pi * np.cumsum(freq) / SR)


def _bell(t, root, partials=((1.0, 1.0), (2.76, 0.55), (5.40, 0.31),
                             (8.93, 0.16)), rate=7.0):
    """Struck metal: inharmonic partials, each dying faster than the last.

    The ratios are a tubular bell's, which is what makes this read as a bell
    rather than as a chord - harmonic partials would sound like an organ.
    """
    out = np.zeros_like(t)
    for ratio, level in partials:
        out += level * np.sin(2 * np.pi * root * ratio * t) * np.exp(
            -t * rate * (0.6 + 0.5 * ratio))
    return out / max(np.abs(out).max(), 1e-9)


def _noise(n):
    return RNG.standard_normal(n)


def _band(x, low, high):
    """Band-pass, tolerant of the edges so a sweep can reach them."""
    ny = SR / 2
    low = max(20.0, min(low, ny * 0.98))
    high = max(low * 1.05, min(high, ny * 0.99))
    b, a = signal.butter(2, [low / ny, high / ny], btype="band")
    return signal.lfilter(b, a, x)


def _moving_band(x, centres, q=1.6):
    """Push `x` through a band-pass whose centre moves over the signal.

    This is what makes a whoosh a whoosh. Done in blocks because a genuinely
    time-varying filter is far more code than the difference is worth here.
    """
    out = np.zeros_like(x)
    blocks = 24
    edges = np.linspace(0, len(x), blocks + 1).astype(int)
    for i in range(blocks):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        centre = float(np.interp(i / max(blocks - 1, 1),
                                 np.linspace(0, 1, len(centres)), centres))
        chunk = _band(x[max(0, a - 256):b], centre / q, centre * q)
        out[a:b] = chunk[-(b - a):]
    return out


def _click(n, brightness=0.6):
    """A few milliseconds of broadband transient - the 'attack' of a hit."""
    length = min(n, int(SR * 0.006))
    burst = _noise(length) * np.exp(-np.linspace(0, 6, length))
    out = np.zeros(n)
    out[:length] = burst * brightness
    return out


# Every effect is matched to this loudness, measured over its loudest 100 ms,
# then peak-limited. Normalising to peak instead - which the first version did -
# leaves a 14 dB spread across the library, because a short tick and a
# sustained boing at the same peak are nowhere near the same loudness to the
# ear. In a finished mix that meant one cue inaudible at -1.8 dB and another
# jumping out at +21 dB. Levels are a property of a library, not of a mix.
TARGET_LOUDNESS = -12.0
PEAK_CEILING = 0.92


def _loudness(x, window=0.1):
    """RMS of the loudest `window` seconds, in dB - what the ear latches onto."""
    n = int(SR * window)
    if len(x) <= n:
        return 20 * np.log10(max(np.sqrt(np.mean(x ** 2)), 1e-9))
    energy = np.convolve(x ** 2, np.ones(n) / n, mode="valid")
    return 20 * np.log10(max(np.sqrt(energy.max()), 1e-9))


def _norm(x, peak=PEAK_CEILING):
    """Level to a common loudness, then keep the peak in bounds."""
    x = x / max(np.abs(x).max(), 1e-9)
    gain = 10 ** ((TARGET_LOUDNESS - _loudness(x)) / 20)
    x = x * gain
    top = np.abs(x).max()
    if top > peak:
        x = x * (peak / top)
    return x


def _stereo(x, spread=0.0):
    """Mono to stereo, optionally with a little width."""
    if spread <= 0:
        return np.column_stack([x, x])
    delay = int(SR * 0.004 * spread)
    right = np.concatenate([np.zeros(delay), x])[:len(x)]
    return np.column_stack([x, right])


# --- transitions -----------------------------------------------------------
# The most-repeated cue in any video, so this is where variety matters most.

def swoosh_up(dur=0.42):
    t = _t(dur)
    air = _moving_band(_noise(len(t)), np.geomspace(300, 6000, 12))
    body = _sweep(t, 180, 900, curve=1.7) * 0.25
    return _norm((air + body) * _decay(t, 5.0, attack=0.06))


def swoosh_down(dur=0.40):
    t = _t(dur)
    air = _moving_band(_noise(len(t)), np.geomspace(5000, 400, 12))
    body = _sweep(t, 700, 150, curve=0.8) * 0.25
    return _norm((air + body) * _decay(t, 4.5, attack=0.05))


def swoosh_soft(dur=0.34):
    t = _t(dur)
    air = _moving_band(_noise(len(t)), np.geomspace(700, 2600, 10)) * 0.7
    return _norm(air * _decay(t, 7.0, attack=0.10))


def tape_stop(dur=0.45):
    """The pitch dropping away, for a hard change of subject."""
    t = _t(dur)
    tone = _sweep(t, 620, 60, curve=2.2)
    grit = _band(_noise(len(t)), 200, 2500) * 0.35
    return _norm((tone + grit) * _decay(t, 4.0, attack=0.01))


# --- reveals and emphasis --------------------------------------------------

def sparkle(dur=0.75):
    """Three bright bells in quick succession - something arrives."""
    t = _t(dur)
    out = np.zeros_like(t)
    for i, root in enumerate((1568.0, 2093.0, 2637.0)):
        offset = int(SR * 0.055 * i)
        tail = t[:len(t) - offset]
        out[offset:] += _bell(tail, root, rate=9.0) * (0.9 ** i)
    return _norm(out)


def chime(dur=0.85):
    t = _t(dur)
    return _norm(_bell(t, 1046.5, rate=5.0) + _click(len(t), 0.25))


def riser(dur=0.65):
    """Tension into a cut: noise and a tone climbing together."""
    t = _t(dur)
    air = _moving_band(_noise(len(t)), np.geomspace(400, 9000, 14))
    tone = _sweep(t, 220, 1400, curve=2.4) * 0.4
    swell = (t / max(t[-1], 1e-9)) ** 1.6
    return _norm((air + tone) * swell)


def impact(dur=0.55):
    """A soft thump with a bright edge - lands a reveal."""
    t = _t(dur)
    low = np.sin(2 * np.pi * np.cumsum(np.linspace(150, 45, len(t))) / SR)
    return _norm(low * _decay(t, 9.0, attack=0.001)
                 + _click(len(t), 0.5)
                 + _band(_noise(len(t)), 800, 4000) * _decay(t, 26.0) * 0.3)


# --- money -----------------------------------------------------------------

def cash(dur=0.7):
    """Cha-ching: a metallic hit, then the bell."""
    t = _t(dur)
    hit = _band(_noise(len(t)), 2000, 9000) * _decay(t, 40.0) * 0.6
    offset = int(SR * 0.09)
    bell = np.zeros_like(t)
    bell[offset:] = _bell(t[:len(t) - offset], 1318.5, rate=6.0) * 0.9
    return _norm(hit + bell)


def coins_drop(dur=0.6):
    """Several small metallic pings, unevenly spaced."""
    t = _t(dur)
    out = np.zeros_like(t)
    for i, root in enumerate((2093.0, 1760.0, 2637.0, 1975.0)):
        offset = int(SR * (0.04 + 0.075 * i + 0.02 * RNG.random()))
        if offset >= len(t):
            break
        out[offset:] += _bell(t[:len(t) - offset], root,
                              rate=16.0) * (0.85 ** i)
    return _norm(out)


# --- reactions -------------------------------------------------------------
# Chosen from the beat's emotion, which nothing used before.

def boing(dur=0.5):
    """Comedy spring - surprise, a double-take."""
    t = _t(dur)
    wobble = 1 + 0.5 * np.sin(2 * np.pi * 11 * t) * np.exp(-t * 5)
    freq = 420 * wobble * np.exp(-t * 1.6)
    tone = np.sin(2 * np.pi * np.cumsum(freq) / SR)
    return _norm(tone * _decay(t, 5.0, attack=0.004))


def pop_cork(dur=0.16):
    """A pop with a body, not a beep: resonant, brief, wooden."""
    t = _t(dur)
    ring = np.sin(2 * np.pi * np.cumsum(
        np.linspace(760, 320, len(t))) / SR) * _decay(t, 40.0, attack=0.0008)
    return _norm(ring + _click(len(t), 0.7))


def wobble(dur=0.6):
    """Confusion: a tone sagging and wavering."""
    t = _t(dur)
    freq = 500 * np.exp(-t * 1.2) * (1 + 0.18 * np.sin(2 * np.pi * 6.5 * t))
    tone = signal.sawtooth(2 * np.pi * np.cumsum(freq) / SR, 0.5) * 0.7
    return _norm(_band(tone, 120, 3000) * _decay(t, 4.0, attack=0.02))


def thud(dur=0.45):
    """Disappointment: low, dull, no sparkle."""
    t = _t(dur)
    low = np.sin(2 * np.pi * np.cumsum(np.linspace(120, 38, len(t))) / SR)
    return _norm(low * _decay(t, 11.0, attack=0.003)
                 + _band(_noise(len(t)), 90, 700) * _decay(t, 18.0) * 0.4)


def error(dur=0.5):
    """Two descending buzzes - the wrong answer."""
    t = _t(dur)
    out = np.zeros_like(t)
    half = len(t) // 2
    for i, freq in enumerate((330.0, 247.0)):
        a = i * half
        seg = t[:half]
        buzz = signal.square(2 * np.pi * freq * seg, 0.35) * 0.5
        out[a:a + half] = _band(buzz, 150, 2200) * _decay(seg, 12.0, attack=0.004)
    return _norm(out)


def stab(dur=0.4):
    """A sharp bright hit - the moment something is pointed at."""
    t = _t(dur)
    chord = sum(np.sin(2 * np.pi * f * t) for f in (587.3, 880.0, 1174.7))
    return _norm(chord * _decay(t, 14.0, attack=0.002) + _click(len(t), 0.4))


# --- punchline -------------------------------------------------------------

def rimshot(dur=0.9):
    """Ba-dum-tss. Two drum hits and a cymbal, for the closing line."""
    t = _t(dur)
    out = np.zeros_like(t)
    for offset_s, freq in ((0.0, 220.0), (0.14, 165.0)):
        offset = int(SR * offset_s)
        seg = t[:len(t) - offset]
        drum = np.sin(2 * np.pi * np.cumsum(
            np.linspace(freq, freq * 0.45, len(seg))) / SR)
        out[offset:] += drum * _decay(seg, 22.0, attack=0.001) * 0.8
    cym = int(SR * 0.30)
    seg = t[:len(t) - cym]
    out[cym:] += _band(_noise(len(seg)), 3000, 14000) * _decay(seg, 6.0) * 0.55
    return _norm(out)


def sting(dur=0.8):
    """A short rising chord that lands - the point of the whole video."""
    t = _t(dur)
    chord = sum(np.sin(2 * np.pi * f * t) for f in (392.0, 523.3, 659.3, 784.0))
    swell = np.minimum(1.0, t / 0.06)
    return _norm(chord * swell * np.exp(-t * 3.2) + _click(len(t), 0.3))


# --- small marks -----------------------------------------------------------

def tick(dur=0.09):
    """A typewriter-ish mark for text arriving."""
    t = _t(dur)
    return _norm(_band(_noise(len(t)), 1200, 6000) * _decay(t, 70.0)
                 + np.sin(2 * np.pi * 1800 * t) * _decay(t, 90.0) * 0.4)


def scribble(dur=0.5):
    """Writing on a board: rough strokes, not a hiss."""
    t = _t(dur)
    out = np.zeros_like(t)
    for i in range(6):
        a = int(len(t) * (i / 6 + 0.01 * RNG.random()))
        b = min(len(t), a + int(SR * 0.055))
        seg = _noise(b - a)
        out[a:b] += _band(seg, 900 + 500 * RNG.random(), 5200) * np.hanning(b - a)
    return _norm(out * 0.8)


def whoosh(dur=0.38):
    """Kept under its old name so an existing config still resolves."""
    return swoosh_up(dur)


def pop(dur=0.16):
    return pop_cork(dur)


def ding(dur=0.85):
    return chime(dur)


def coin(dur=0.7):
    return cash(dur)


def splash(dur=0.4):
    return swoosh_soft(dur)


GENERATORS = {
    # transitions
    "swoosh_up": swoosh_up, "swoosh_down": swoosh_down,
    "swoosh_soft": swoosh_soft, "tape_stop": tape_stop,
    # reveals
    "sparkle": sparkle, "chime": chime, "riser": riser, "impact": impact,
    # money
    "cash": cash, "coins_drop": coins_drop,
    # reactions
    "boing": boing, "pop_cork": pop_cork, "wobble": wobble, "thud": thud,
    "error": error, "stab": stab,
    # punchline
    "rimshot": rimshot, "sting": sting,
    # marks
    "tick": tick, "scribble": scribble,
    # the original six names, so an old config still resolves
    "whoosh": whoosh, "pop": pop, "ding": ding, "coin": coin, "splash": splash,
}

# A little stereo width on the airy ones and none on the short marks, which
# read as sharper when they are dead centre.
WIDTH = {"swoosh_up": 0.8, "swoosh_down": 0.8, "swoosh_soft": 0.6,
         "riser": 0.7, "sparkle": 0.5, "rimshot": 0.4, "whoosh": 0.8,
         "splash": 0.6}


def write_wav(path, samples):
    samples = np.clip(samples, -1.0, 1.0)
    if samples.ndim == 1:
        samples = np.column_stack([samples, samples])
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((samples * 32767).astype(np.int16).tobytes())


def generate_all(output_dir, only=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for name, make in GENERATORS.items():
        if only and name not in only:
            continue
        mono = make()
        write_wav(output_dir / f"{name}.wav", _stereo(mono, WIDTH.get(name, 0.0)))
        made.append(name)
        print(f"  [ok] {name}.wav  {len(mono) / SR:.2f}s")
    print(f"{len(made)} effect(s) written to {output_dir}")
    return made


def main():
    ap = argparse.ArgumentParser(description="build the sound-effect library")
    ap.add_argument("out", nargs="?", default="assets/sfx")
    ap.add_argument("--only", nargs="*", help="rebuild just these")
    args = ap.parse_args()
    generate_all(args.out, only=set(args.only) if args.only else None)


if __name__ == "__main__":
    main()
