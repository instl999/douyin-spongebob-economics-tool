"""Offline checks for how a video looks and sounds.

Captions, cards, panels, labels, cuts, cues and the music under them - the
parts a viewer sees and hears first. Split out of selftest.py for the reason
selftest_footage.py was: one thousand-line file is where two sessions' work
collides.

Run through selftest.py, which owns the Suite and prints the summary.
"""
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import config

ROOT = Path(__file__).resolve().parent.parent


def run(suite, lay):
    """Add every look-and-sound check to `suite`."""
    music_checks(suite)


def _tone(path, spans, frequency, amplitude, total, rate=44100):
    """A wav holding a sine at `frequency` inside each (start, end) span."""
    t = np.arange(int(total * rate)) / rate
    wave = np.zeros_like(t)
    for start, end in spans:
        on = (t >= start) & (t < end)
        wave[on] = amplitude * np.sin(2 * np.pi * frequency * t[on])
    stereo = np.repeat((wave * 32767).astype("<i2")[:, None], 2, axis=1)
    import wave as wave_mod
    with wave_mod.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(stereo.tobytes())
    return path


def _band_db(path, start, end, frequency, rate=44100):
    """Level of one frequency in a stretch of a file, in dB."""
    raw = subprocess.run(
        [config.FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t",
         f"{end - start:.3f}", "-i", str(path), "-ac", "1", "-ar", str(rate),
         "-f", "s16le", "-"], capture_output=True).stdout
    data = np.frombuffer(raw, dtype="<i2").astype(float) / 32768
    if not len(data):
        return -120.0
    spectrum = np.abs(np.fft.rfft(data * np.hanning(len(data))))
    freqs = np.fft.rfftfreq(len(data), 1 / rate)
    band = spectrum[(freqs > frequency * 0.9) & (freqs < frequency * 1.1)]
    return 20 * np.log10(max(float(np.sqrt((band ** 2).sum())), 1e-9))


def music_checks(suite):
    """The bed follows the script, and gets out of the voice's way."""
    import audio as audio_mod
    import music as music_mod

    kept = music_mod.clean_sections([
        {"from": 1, "mood": ["疑问"]},
        {"from": 3, "mood": ["转机"]},        # two shots after the first
        {"from": 4, "mood": ["转机", "波澜壮阔"]},
        {"from": 40, "mood": ["升华"]},       # past the end
        {"from": 9, "mood": ["升华"]},
        {"from": 10, "mood": ["欢乐"]}], 12)
    suite.check("music: sections are cleaned to a few that can each be heard",
                [s["from"] for s in kept] == [1, 4, 9]
                and kept[1]["mood"] == ["转机"],
                f"{[s['from'] for s in kept]}")
    suite.check("music: one section is no sections",
                music_mod.clean_sections([{"from": 1, "mood": ["紧张"]}], 8) == [])

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for name in ("开头疑问A.mp3", "疑问B.mp3", "转机C.mp3",
                     "结尾升华D.mp3", "升华E.mp3"):
            (folder / name).write_bytes(b"")
        sections = [{"from": 1, "mood": ["疑问"]}, {"from": 5, "mood": ["转机"]},
                    {"from": 9, "mood": ["升华"]}]
        starts = {i: 2.0 + (i - 1) * 3.0 for i in range(1, 13)}
        beds, why = music_mod.plan_beds(folder, "脚本", ["疑问"], sections,
                                        starts=starts, total=40.0)
        names = [Path(b["path"]).name for b in beds]
        suite.check("music: each section gets the track written for its place",
                    names == ["开头疑问A.mp3", "转机C.mp3", "结尾升华D.mp3"], why)
        overlap = beds[0]["end"] - beds[1]["start"] if len(beds) > 1 else 0
        suite.check("music: neighbouring sections crossfade",
                    abs(overlap - music_mod.CROSSFADE) < 1e-6
                    and beds[1]["fade_in"] == music_mod.CROSSFADE
                    and beds[0]["start"] == 0.0 and beds[-1]["end"] == 40.0,
                    f"{overlap:.2f}s overlap at shot 5")
        # A section nothing fits keeps the bed before it, rather than the
        # music dropping out halfway through a video.
        held, _ = music_mod.plan_beds(
            folder, "脚本", ["疑问"],
            [{"from": 1, "mood": ["疑问"]}, {"from": 5, "mood": ["紧张"]}],
            starts=starts, total=40.0)
        suite.check("music: a section nothing fits holds the bed it follows",
                    len(held) == 1 and held[0]["end"] == 40.0,
                    f"{[Path(b['path']).name for b in held]}")

    # Ducked, the bed is heard between lines and gets out of the way of them.
    # Measured on the mix itself: a 1 kHz "voice" in bursts over a 200 Hz bed,
    # so the two can be read apart by frequency.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        speech = [(0.5, 2.5), (4.0, 6.0)]
        voice = _tone(work / "voice.wav", speech, 1000, 0.20, 7.0)
        bed = _tone(work / "bed.wav", [(0.0, 7.0)], 200, 0.5, 7.0)
        mixed = audio_mod.mix(
            voice, work / "mix.wav", 7.0,
            beds=[{"path": str(bed), "start": 0.0, "end": 7.0,
                   "fade_in": 0.05, "fade_out": 0.05}],
            bgm_volume=audio_mod.BGM_DUCKED_VOLUME, duck=True)
        under = _band_db(mixed, 1.2, 2.4, 200)
        between = _band_db(mixed, 3.2, 3.9, 200)
        spoken = _band_db(mixed, 1.2, 2.4, 1000)
        suite.check("music: ducked, the bed sits 20-30 dB under the voice",
                    20 <= spoken - under <= 30,
                    f"{spoken - under:.1f} dB under while speaking")
        suite.check("music: ...and comes up between lines",
                    between - under >= 6,
                    f"+{between - under:.1f} dB in the gap")
