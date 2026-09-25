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
    caption_checks(suite)
    portrait_checks(suite)
    card_and_wall_checks(suite)
    label_and_cut_checks(suite)
    cue_checks(suite)


def caption_checks(suite):
    """One line per caption, broken where a phrase breaks, timed to the voice."""
    import audio as audio_mod
    import captions
    import textkit
    from layout import Layout

    tall = Layout("portrait")
    size, width = tall.subtitle_font_px(), tall.subtitle_max_px
    text = "蟹老板最近很烦恼，后厨总是慢半拍。于是他决定给海绵宝宝多发一点工资。"
    parts = captions.chunks(text, size, width)
    wide = [p for p in parts if textkit.advance(p.rstrip(captions.TRIM_TAIL),
                                               size, True) > width]
    suite.check("captions: each fits on one line",
                not wide and len(parts) >= 3, " | ".join(parts))
    suite.check("captions: a caption never splits a name",
                not any(a.endswith("海绵") and b.startswith("宝宝")
                        for a, b in zip(parts, parts[1:]))
                and "".join(parts) == text,
                "于是他决定给 | 海绵宝宝… not 海绵 | 宝宝")
    suite.check("captions: a comma does not end a caption",
                all(not p.endswith("，") for _, _, p, _, _ in
                    captions.spans(text, 6.0, size, width)))
    suite.check("wrapping: two lines break at a phrase, not in the middle",
                textkit.wrap("什么是效率工资", 151, 950, face="brush")
                == ["什么是", "效率工资"],
                str(textkit.wrap("什么是效率工资", 151, 950, face="brush")))

    # Timed to the voice: each change lands just before the phrase it
    # carries, at the pause the voice took - not by character count.
    timed = captions.spans(text, 6.0, size, width, speech=(0.3, 5.7),
                           pauses=[(1.4, 1.62), (2.9, 3.2), (3.9, 4.05)])
    starts = [round(start, 2) for start, _, _, _, _ in timed]
    suite.check("captions: they change at the voice's own pauses",
                starts[0] == 0.0 and abs(timed[-1][1] - 6.0) < 1e-9
                and 1.55 <= starts[1] <= 1.62 and 3.13 <= starts[2] <= 3.2,
                f"starts {starts}")
    blind = captions.spans(text, 6.0, size, width)
    suite.check("captions: without a measured voice they share the shot by length",
                all(b[1] - b[0] >= captions.MIN_CAPTION for b in blind)
                and blind[0][0] == 0.0 and abs(blind[-1][1] - 6.0) < 1e-9)
    heard = captions.spoken_at("发工资", text, timed)
    suite.check("captions: a label is heard when its words are said",
                heard is not None and heard > starts[-1],
                f"'发工资' at {heard:.2f}s" if heard else "not found")

    with tempfile.TemporaryDirectory() as tmp:
        clip = _tone(Path(tmp) / "voice.wav",
                     [(0.25, 1.3), (1.55, 2.6), (2.95, 3.8)], 440, 0.3, 4.2)
        measured = audio_mod.speech_pauses(clip, 1.0)
        onset, offset, pauses = measured or (0, 0, [])
        suite.check("captions: the voice's pauses are measured off the clip",
                    measured and abs(onset - 0.25) < 0.05
                    and abs(offset - 3.8) < 0.05 and len(pauses) == 2
                    and abs(pauses[0][0] - 1.3) < 0.05,
                    f"onset {onset}, offset {offset}, pauses {pauses}")


def portrait_checks(suite):
    """Portrait keeps clear of what a phone draws over the video."""
    import checks as checks_mod
    import render as render_mod
    from layout import Layout
    from PIL import Image, ImageDraw

    tall = Layout("portrait")
    zones = tall.cfg.get("ui_zones") or []
    bottom = min((y0 for x0, y0, x1, y1 in zones if x0 == 0.0), default=1.0)
    caption_low = (tall.subtitle_center_y + tall.subtitle_font_px() * 0.75) \
        / tall.height
    feet = (tall.stage_y + tall.stage_h * 0.97) / tall.height
    suite.check("portrait: a one-line caption sits above the app's own text",
                caption_low < bottom and feet < tall.subtitle_center_y / tall.height,
                f"caption to {caption_low:.3f}, app text from {bottom}, "
                f"feet at {feet:.3f}")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        Image.new("RGB", (1080, 1920), (90, 190, 200)).save(work / "bg.png")
        body = Image.new("RGBA", (300, 700), (0, 0, 0, 0))
        ImageDraw.Draw(body).ellipse([0, 0, 299, 699], fill=(230, 60, 60, 255))
        body.save(work / "a.png")
        board = {
            "video": {"orientation": "portrait", "width": 1080, "height": 1920,
                      "background": "bg.png"},
            "title_bar": {"text": "什么是效率工资"},
            "scenes": [{"id": 1, "duration": 1.0, "framing": "close",
                        "elements": [{"asset": "a.png", "x": 0.86, "y": 0.97,
                                      "h": 0.46},
                                     {"type": "label", "text": "工资", "x": 0.5,
                                      "y": 0.0, "anchor": "center"}]}]}
        assets = render_mod.Assets(work)
        checks_mod.inspect(board, assets, tall, repair=True)
        left = checks_mod.inspect(board, assets, tall, repair=False)
        sprite = board["scenes"][0]["elements"][0]
        image = assets.sized("a.png", tall.sprite_height(
            sprite["h"] * render_mod.FRAMING["close"]))
        x0, _ = render_mod.element_origin(sprite, image, tall)
        suite.check("portrait: nothing is left under the app's buttons",
                    not left and x0 + image.width <= tall.width - tall.safe_right_px,
                    f"right edge {x0 + image.width} of "
                    f"{tall.width - tall.safe_right_px}")
        label = board["scenes"][0]["elements"][1]
        tag = render_mod.build_element_image(label, assets, tall)
        _, top = render_mod.element_origin(label, tag, tall)
        suite.check("portrait: nothing sits under the title bar",
                    top >= checks_mod.ceiling_for(board, tall) - 1,
                    f"label top {top}, bar ends {checks_mod.ceiling_for(board, tall)}")

        renderer = render_mod.Renderer(board, work)
        frame = np.frombuffer(renderer.frame(0.5, render_mod.build_timeline(
            board, [1.0])[0]), dtype=np.uint8).reshape(1920, 1080, 3)
        bar = frame[int(1920 * 0.066), 80]        # inside the bar, clear of the type
        suite.check("portrait: the title bar is drawn over the shot",
                    bar[0] > 200 and bar[1] > 170 and bar[2] < 80,
                    f"pixel {bar.tolist()} at the bar")


def card_and_wall_checks(suite):
    """Cards in brush lettering, brushed on; walls a room, not frosted glass."""
    import render as render_mod
    import textkit
    from layout import Layout

    wide = Layout("landscape")
    suite.check("cards: the brush lettering ships with the repository",
                textkit.BRUSH_PATH.exists()
                and (textkit.BRUSH_PATH.parent / "OFL.txt").exists())
    suite.check("cards: a character the brush font lacks falls back whole",
                textkit.covers("什么是效率工资", "brush")
                and not textkit.covers("什么是\U00020000", "brush"))

    card = {"text": "什么是效率工资", "duration": 1.0, "style": "title",
            "size": 0.095}
    full = render_mod.compose_card(card, wide)
    half = render_mod.brush_reveal(full, 0.5)
    ink, shown = full.sum(axis=2) > 60, half.sum(axis=2) > 60
    quarter = wide.width * 3 // 4
    suite.check("cards: the title is brushed on from the left",
                ink[:, quarter:].any() and shown[:, :wide.width // 3].any()
                and not shown[:, quarter:].any(),
                "halfway through, the left is lettered and the right still dark")

    def lit(glow):
        layer = textkit.render_card(
            wide.size, "心气", size=134, max_width=1600, highlight="心气",
            face="brush", glow=glow)
        return int((np.asarray(layer)[:, :, 3] > 10).sum())
    suite.check("cards: the closing keyword glows",
                lit(True) > lit(False) * 1.3,
                f"{lit(True)} lit pixels against {lit(False)} without the glow")

    panel = {"type": "panel", "x": 0.5, "y": 0.99, "w": 1.0, "ph": 0.5}
    wall = np.asarray(render_mod.build_element_image(
        panel, None, wide, panel_color=(176, 196, 205)))
    floor_row = wall.shape[0] - 8
    suite.check("walls: opaque, with a floor under the characters' feet",
                wall[:, :, 3].min() == 255
                and wall[floor_row, 10, :3].sum() < wall[20, 10, :3].sum() - 40,
                f"wall {wall[20, 10, :3].tolist()}, floor "
                f"{wall[floor_row, 10, :3].tolist()}")


def label_and_cut_checks(suite):
    """Labels sit on what they name; a shared character cuts, not dissolves."""
    import build as build_mod
    import checks as checks_mod
    import render as render_mod
    from layout import Layout
    from PIL import Image, ImageDraw

    wide = Layout("landscape")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        body = Image.new("RGBA", (300, 600), (0, 0, 0, 0))
        ImageDraw.Draw(body).ellipse([0, 0, 299, 599], fill=(230, 60, 60, 255))
        body.save(work / "krabs_x_000000.png")
        body.save(work / "sponge_y_000000.png")
        board = {"video": {"orientation": "landscape"}, "scenes": [
            {"id": 1, "framing": "medium", "elements": [
                {"asset": "krabs_x_000000.png", "who": ["krabs"], "x": 0.25,
                 "y": 0.97, "h": 0.45},
                {"asset": "sponge_y_000000.png", "who": ["sponge"], "x": 0.75,
                 "y": 0.97, "h": 0.45},
                {"type": "label", "text": "老板", "x": 0.25, "y": 0.1,
                 "anchor": "center"},
                {"type": "label", "text": "员工", "for": "sponge", "x": 0.5,
                 "y": 0.5, "anchor": "center"},
                {"type": "label", "text": "效率", "x": 0.5, "y": 0.12,
                 "anchor": "center"}]}]}
        assets = render_mod.Assets(work)
        checks_mod.inspect(board, assets, wide, repair=True)
        els = board["scenes"][0]["elements"]
        heads = {}
        for el in els[:2]:
            img = assets.sized(el["asset"], wide.sprite_height(el["h"]))
            heads[el["who"][0]] = render_mod.element_origin(el, img, wide)[1]

        def bottom(el):
            tag = render_mod.build_element_image(el, assets, wide)
            return render_mod.element_origin(el, tag, wide)[1] + tag.height

        over = heads["krabs"] - bottom(els[2])
        named = heads["sponge"] - bottom(els[3])
        suite.check("labels: one floating over a character comes down to it",
                    0 <= over <= 0.05 * wide.height,
                    f"{over}px above the head")
        suite.check("labels: one naming a character sits over that character",
                    0 <= named <= 0.05 * wide.height
                    and abs(els[3]["x"] - 0.75) < 0.02,
                    f"x {els[3]['x']}, {named}px above the head")
        suite.check("labels: one over nobody is left where it was put",
                    els[4]["y"] == 0.12)

    shots = [{"elements": [{"who": ["krabs", "sponge"]}]},
             {"elements": [{"asset": "sponge_waving_1.png"}]},
             {"elements": [{"who": ["patrick"]}]}]
    suite.check("cuts: shots sharing a character are recognised",
                build_mod._characters(shots[0]) & build_mod._characters(shots[1])
                and not build_mod._characters(shots[1])
                & build_mod._characters(shots[2]))

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        Image.new("RGB", (1920, 1080), (90, 190, 200)).save(work / "bg.png")
        for name, colour in (("a.png", (230, 60, 60)), ("b.png", (60, 60, 230))):
            Image.new("RGBA", (300, 500), colour + (255,)).save(work / name)
        board = {"video": {"orientation": "landscape", "width": 1920,
                           "height": 1080, "background": "bg.png",
                           "dissolve": 0.5},
                 "scenes": [
                     {"id": 1, "duration": 1.0, "elements": [
                         {"asset": "a.png", "x": 0.3, "y": 0.97, "h": 0.4}]},
                     {"id": 2, "duration": 1.0, "transition": "cut",
                      "elements": [{"asset": "b.png", "x": 0.7, "y": 0.97,
                                    "h": 0.4}]}]}
        renderer = render_mod.Renderer(board, work)
        segments, _ = render_mod.build_timeline(board, [1.0, 1.0])
        just_after = np.frombuffer(renderer.frame(1.05, segments), dtype=np.uint8)
        settled = np.frombuffer(renderer.frame(1.9, segments), dtype=np.uint8)
        suite.check("cuts: a cut shows the new shot at once, with no ghost",
                    np.array_equal(just_after, settled))


def cue_checks(suite):
    """Cues keep their distance, and meaning outranks the cut."""
    import sfx as sfx_mod

    scenes = [{"id": i + 1, "duration": 2.0, "framing": "medium",
               "beat": {"emotion": e},
               "elements": [{"type": "label", "text": "标签", "tone": "money"}]}
              for i, e in enumerate(["happy", "worried", "shocked", "glum"])]
    board = {"video": {"speed": 1.0}, "scenes": scenes,
             "ending_card": {"text": "完", "duration": 1.5}}
    cues = sfx_mod.plan(board, [2.0] * 4, taken=[0.0])
    times = [when for when, _, _ in cues]
    gap = min((b - a for a, b in zip(times, times[1:])), default=99)
    punch = set(sfx_mod.styles_mod.look()["sound"]["cues"]["punchline"])
    swooshes = set(sfx_mod.styles_mod.look()["sound"]["cues"]["transition"])
    suite.check("cues: no two land closer than the minimum gap",
                gap >= 2.5 - 1e-6 and times and times[0] >= 2.5 - 1e-6,
                f"{len(cues)} cues, closest {gap:.2f}s apart")
    suite.check("cues: the closing sting survives, the swooshes give way",
                cues and cues[-1][1] in punch
                and not any(name in swooshes for _, name, _ in cues),
                ", ".join(name for _, name, _ in cues))


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
