"""Prove the installation works, without touching the network.

    python scripts/selftest.py

Every stage that does not need an API is exercised on synthetic assets: the
script splitter, the duration model, matting, the layout checks, the renderer,
the audio mix and the verifier. It builds a real four-second MP4 in a temp
directory and runs the full check suite against it.

This is the thing to run after installing, after editing anything, or when a
build fails and it is not obvious whether the pipeline or the API is at fault.
It spends nothing, so it can be run freely.
"""
import ast
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: F401  UTF-8 stdout; see console.py

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

PASS, FAIL = "[ok]  ", "[FAIL]"


class Suite:
    def __init__(self):
        self.failures = 0

    def check(self, name, condition, detail=""):
        ok = bool(condition)
        if not ok:
            self.failures += 1
        print(f"  {PASS if ok else FAIL} {name}{('  ' + detail) if detail else ''}")
        return ok


def synthetic_assets(directory):
    """A plate and three sprites, one with an enclosed hole to matte."""
    W, H = 1920, 1080
    plate = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(plate)
    for y in range(H):
        k = y / H
        draw.line([(0, y), (W, y)], fill=(int(70 + 60 * k), int(200 - 20 * k),
                                          int(205 - 25 * k)))
    draw.rectangle([0, int(H * 0.70), W, H], fill=(120, 205, 70))
    plate.save(directory / "background.png")

    for name, colour, size in (("a.png", (230, 60, 60), (420, 520)),
                               ("b.png", (250, 220, 60), (380, 470)),
                               ("prop_c.png", (150, 150, 160), (300, 260))):
        w, h = size
        sprite = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(sprite)
        d.ellipse([4, 4, w - 4, h - 4], fill=colour + (255,),
                  outline=(20, 20, 20, 255), width=8)
        d.ellipse([w * 0.3, h * 0.25, w * 0.45, h * 0.4], fill=(255, 255, 255, 255))
        sprite.save(directory / name)


def offline_project(work, speed, plan, name):
    """A Project on disk, with a plate and sprites, ready for stage_storyboard.

    Shared by the two check groups below because both need the same twelve
    lines of scaffolding and neither is about the scaffolding. What they do
    with the voice index afterwards is where they differ, and that stays in
    each.
    """
    import build as build_mod
    import shutil

    work.mkdir(parents=True, exist_ok=True)
    synthetic_assets(work)
    project_path = work / "project.json"
    project_path.write_text(json.dumps(
        {"name": name, "speed": speed, "out": str(work / "out"),
         "script_text": "".join(s["narration"] for s in plan["scenes"])},
        ensure_ascii=False), encoding="utf-8")
    project = build_mod.Project(project_path)
    for asset in ("background.png", "a.png", "b.png", "prop_c.png"):
        shutil.copy(work / asset, project.out / asset)
    return project


def speed_checks(suite):
    """The global speed moves the whole video, not only the voice.

    Every check here compares a 1.0x build against the same build at 1.5x and
    asserts the *ratio*, never an absolute number. That is deliberate: the
    failure this setting exists to prevent is one part of the video keeping its
    old length while the rest speeds up, and a ratio is the only thing that
    catches it wherever it happens. Anything that fails to divide shows up as a
    total that is not 1/1.5 of the other.
    """

    import build as build_mod
    import render as render_mod
    import timing
    import tts as tts_mod

    suite.check("speed: 1.5x is the default and 1.0x the baseline",
                timing.DEFAULT_SPEED == 1.5 and timing.BASELINE_SPEED == 1.0,
                f"default {timing.DEFAULT_SPEED}x")
    suite.check("speed: nonsense settings fall back rather than divide by zero",
                timing.clamp(0) == timing.DEFAULT_SPEED
                and timing.clamp("fast") == timing.DEFAULT_SPEED
                and timing.clamp(99) == timing.MAX_SPEED
                and timing.clamp(0.01) == timing.MIN_SPEED)

    # Narration and everything around it must divide by the SAME number, for
    # any input at all. Two guards on one parameter - `max(speed, 0.1)` in one
    # place and `clamp` in the other - is how a video ends up with its voice on
    # one clock and its pads on another, which is the whole failure this
    # setting exists to remove, reached from inside the module that defines it.
    gaps = []
    for odd in (0.25, 0.5, 1.0, 1.5, 2.0, 9.0, 0, "nonsense"):
        try:
            narration = timing.clip_seconds("一二三四五六七八九十", 1.0)                 / timing.clip_seconds("一二三四五六七八九十", odd)
            pad = timing.scale(0.35, 1.0) / timing.scale(0.35, odd)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            # Reported, not raised. A guard that throws on a setting a project
            # file can legally contain is a failure of this check, and a
            # selftest that dies instead of saying so tells nobody which check
            # it died in.
            gaps.append(f"{odd!r}: {type(exc).__name__}")
            continue
        if abs(narration - pad) > 1e-9:
            gaps.append(f"{odd!r}: {narration:.4f} vs {pad:.4f}")
    suite.check("speed: narration and pads divide by the same number",
                not gaps, "; ".join(gaps))

    # The estimate is what a user is told before anything is paid for, so it
    # has to shrink by the whole factor. It used to divide the narration and
    # leave the cards, which under-reported every speed above 1.0.
    script = "一二三四五六七八九十。" * 8
    slow = timing.estimate(script, speed=1.0)
    fast = timing.estimate(script, speed=1.5)
    suite.check("speed: the predicted length scales in full",
                abs(fast["total"] * 1.5 - slow["total"]) < 0.05
                and abs(fast["cards"] * 1.5 - slow["cards"]) < 0.01,
                f"{slow['total']:.1f}s -> {fast['total']:.1f}s")

    # A storyboard end to end, at two speeds, from narration already the length
    # the service would return at each. Everything that is NOT the narration -
    # tail pads, both cards, the dissolve, the caption fades - has to have
    # moved with it.
    plan = {
        "title": "标题",
        "scenes": [
            {"narration": "第一句话在这里说完。", "framing": "medium",
             "elements": [{"type": "sprite", "asset": "a.png", "x": 0.32,
                           "y": 0.95, "rel": 0.6},
                          {"type": "label", "text": "重点", "x": 0.72,
                           "y": 0.42, "tone": "neutral"}]},
            {"narration": "第二句话稍微长一点点。", "framing": "close",
             "elements": [{"type": "sprite", "asset": "b.png", "x": 0.5,
                           "y": 0.95, "rel": 0.6}]},
        ],
        "ending": {"text": "结语在这里", "highlight": ""},
    }

    def storyboard_at(speed, work):
        project = offline_project(work, speed, plan, "speed")
        # What the service would hand back at this speed. Faked here only
        # because the selftest is offline; the shape is what the voice stage
        # writes, and the point of the check is what the *timeline* does with
        # it - a shot is cut to the clip it actually got.
        index = {"title": {"duration": 0.9 / speed, "path": "", "words": []}}
        for i, scene in enumerate(plan["scenes"], 1):
            index[str(i)] = {"duration": (2.4 + 0.3 * i) / speed,
                             "path": "", "words": []}
        return build_mod.stage_storyboard(project, plan, index)

    with tempfile.TemporaryDirectory() as tmp:
        sb_slow, _, total_slow = storyboard_at(1.0, Path(tmp) / "slow")
        sb_fast, _, total_fast = storyboard_at(1.5, Path(tmp) / "fast")

        # A speed like 1.5x turns every duration into a repeating decimal, and
        # the draft is written in whole microseconds. Rounding a start and a
        # length separately let shot 1 end one microsecond after shot 2 began,
        # and Jianying rejects a whole track for overlapping - so the export
        # died on a video that had just rendered perfectly. Nothing above
        # catches it: the storyboard is correct, and only the conversion is
        # not. This exports one for real.
        import draft as draft_mod
        out = Path(tmp) / "fast" / "out"
        (out / "storyboard.json").write_text(
            json.dumps(sb_fast, ensure_ascii=False), encoding="utf-8")
        try:
            draft_mod.DraftBuilder(out, name="speed").build(out / "jianying")
            exported, why = True, f"{total_fast:.6f}s of repeating decimals"
        except Exception as exc:                       # noqa: BLE001 - reported
            exported, why = False, f"{type(exc).__name__}: {exc}"
        suite.check("speed: an odd-length timeline still exports a draft",
                    exported, why)

        # ...and the invariant underneath it, swept over every speed a user can
        # set. One export survives one particular set of numbers; the bug is a
        # property of the arithmetic, so this asserts the property. Rounding a
        # length instead of a boundary fails here on hundreds of boundaries.
        gaps, spans = set(), 0
        for rate in (1.25, 1.5, 1.75, 2.0):
            clock, bounds = 0.0, [0.0]
            for i in range(1, 200):
                clock += (1.7 + 0.13 * i) / rate     # repeating decimals
                bounds.append(clock)
            made = [draft_mod._span(x, y) for x, y in zip(bounds, bounds[1:])]
            spans += len(made)
            gaps |= {y.start - (x.start + x.duration)
                     for x, y in zip(made, made[1:])}
        suite.check("speed: draft segments meet exactly, never overlap",
                    gaps == {0}, f"{spans} spans, gaps {sorted(gaps)}")

    suite.check("speed: the whole timeline shortens by the factor",
                abs(total_fast * 1.5 - total_slow) < 0.02,
                f"{total_slow:.2f}s -> {total_fast:.2f}s")
    suite.check("speed: the dissolve scales with it",
                abs(sb_fast["video"]["dissolve"] * 1.5
                    - sb_slow["video"]["dissolve"]) < 1e-6,
                f"{sb_slow['video']['dissolve']:.3f}s -> "
                f"{sb_fast['video']['dissolve']:.3f}s")
    fades_slow = sb_slow["video"]["look"]["timing"]
    fades_fast = sb_fast["video"]["look"]["timing"]
    suite.check("speed: the caption and element fades scale with it",
                all(abs(fades_fast[k] * 1.5 - fades_slow[k]) < 1e-6
                    for k in ("caption_fade", "element_fade")),
                f"caption {fades_slow['caption_fade']:.3f}s -> "
                f"{fades_fast['caption_fade']:.3f}s")
    suite.check("speed: both cards scale with it",
                abs(sb_fast["title_card"]["duration"] * 1.5
                    - sb_slow["title_card"]["duration"]) < 1e-6
                and abs(sb_fast["ending_card"]["duration"] * 1.5
                        - sb_slow["ending_card"]["duration"]) < 1e-6,
                f"title {sb_slow['title_card']['duration']:.2f}s -> "
                f"{sb_fast['title_card']['duration']:.2f}s")
    # The subtitle is what a viewer would watch drift. Its spans are cut from
    # the same shot durations the picture is, so this asserts the two are one
    # clock rather than two that happen to agree.
    caps_slow = [c for s in sb_slow["scenes"] for c in s["captions"]]
    caps_fast = [c for s in sb_fast["scenes"] for c in s["captions"]]
    suite.check("speed: subtitles stay on the shots they belong to",
                len(caps_slow) == len(caps_fast)
                and all(abs(f["end"] * 1.5 - s["end"]) < 0.02
                        for s, f in zip(caps_slow, caps_fast)),
                f"{len(caps_fast)} caption spans")
    suite.check("speed: no subtitle outlives its own shot",
                all(c["end"] <= s["duration"] + 1e-6
                    for s in sb_fast["scenes"] for c in s["captions"]))

    # The renderer has to take the paced fades off the storyboard. Left on the
    # module constants it would draw 1.0x fades over a 1.5x cut.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        synthetic_assets(work)
        renderer = render_mod.Renderer(sb_fast, work)
    suite.check("speed: the renderer reads the storyboard's fades, not its own",
                abs(renderer.caption_fade * 1.5 - render_mod.CAPTION_FADE) < 1e-6
                and abs(renderer.element_fade * 1.5
                        - render_mod.ELEMENT_FADE) < 1e-6,
                f"{renderer.caption_fade:.3f}s vs module "
                f"{render_mod.CAPTION_FADE:.3f}s")

    # Narration is re-spoken rather than resampled, so 1.5x has to reach the
    # service as a rate. Resampling afterwards would shift the pitch and, worse,
    # would leave the shot lengths derived from clips of the old length.
    suite.check("speed: the voice is asked to read faster",
                tts_mod._rate(1.5) == 50 and tts_mod._rate(1.0) == 0
                and tts_mod._rate(0.5) == -50,
                f"1.5x -> speech_rate {tts_mod._rate(1.5)}")
    suite.check("speed: a shot with no clip is estimated at the same speed",
                abs(tts_mod.estimate_duration("一二三四五六七八九十", 1.5) * 1.5
                    - tts_mod.estimate_duration("一二三四五六七八九十", 1.0)) < 0.01)


def narration_alignment_checks(suite):
    """The draft's narration clips sit on the shots they belong to.

    This is check 5 of check_draft, run here because it had been reporting a
    failure on every draft ever exported and nothing noticed. It compared the
    clips against the *shot* starts, and the title card is voiced too - so the
    title was paired with shot 1, shot 1 with shot 2, and a correct draft came
    back as every clip one whole shot out of place. A check that fails on
    everything is one nobody can read a real drift out of.

    Run at two speeds on purpose. The title's clip starts `lead` into the card,
    and `lead` is a baseline number that build.py divides by the speed, so a
    1.0x-only test would pass against a constant that is wrong everywhere else.
    """
    import build as build_mod
    import check_draft
    import draft as draft_mod
    import tts as tts_mod

    plan = {
        "title": "自检标题",
        "scenes": [
            {"narration": "第一句旁白在这里。", "framing": "medium",
             "elements": [{"type": "sprite", "asset": "a.png", "x": 0.35,
                           "y": 0.95, "rel": 0.6}]},
            {"narration": "第二句旁白长一点点。", "framing": "medium",
             "elements": [{"type": "sprite", "asset": "b.png", "x": 0.65,
                           "y": 0.95, "rel": 0.6}]},
        ],
        "ending": {"text": "收尾一句", "highlight": ""},
    }

    def draft_at(speed, work, title=None):
        """A real storyboard, a real voice index and a real draft, at `speed`."""
        spoken_title = title or plan["title"]
        project = offline_project(work, speed, plan, "voice")

        # Real silent clips at this speed, written where the voice stage writes
        # them, so the draft lays its audio from the same index a build would.
        index = {}
        for key, text in [("title", spoken_title)] + [
                (str(i), s["narration"]) for i, s in enumerate(plan["scenes"], 1)]:
            out = project.out / "voice" / (
                "title.mp3" if key == "title" else f"scene_{int(key):02d}.mp3")
            made = tts_mod._silent(text, out, speed)
            index[key] = {"text": text, "path": str(made["path"]),
                          "duration": made["duration"], "speed": speed,
                          "words": [], "degraded": True}
        (project.out / "voice" / "index.json").write_text(
            json.dumps(index, ensure_ascii=False), encoding="utf-8")

        storyboard, _, _ = build_mod.stage_storyboard(
            project, dict(plan, title=spoken_title), index)
        (project.out / "storyboard.json").write_text(
            json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")
        draft_dir, _, _ = draft_mod.DraftBuilder(
            project.out, name="voice").build(project.out / "jianying")
        content = json.loads(
            (draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        return project.out, storyboard, content

    # A title too long to read before shot 1 keeps its type and loses its
    # voice. The rule lives in build.py, the draft exporter used to lay its
    # audio from the voice index instead, and the clip was still sitting there
    # - so the draft spoke a title the MP4 beside it was silent for, truncated
    # into a card sized for no speech at all. The draft is the deliverable, so
    # the two disagreeing is worse than either being wrong.
    with tempfile.TemporaryDirectory() as tmp:
        long_title = "这是一个非常非常长的标题它根本读不完还在继续读下去"
        out, sb, content = draft_at(1.5, Path(tmp) / "w", title=long_title)
        card = sb.get("title_card") or {}
        clips = [seg for tr in content["tracks"]
                 if tr["type"] == "audio" and tr.get("name") == "配音"
                 for seg in tr["segments"]]
        suite.check("narration: an unreadable title loses its voice everywhere",
                    not card.get("voice") and len(clips) == len(plan["scenes"]),
                    f"{len(clips)} clips for {len(plan['scenes'])} shots, "
                    f"card {'voiced' if card.get('voice') else 'silent'}")
        # ...and the card keeps its type, which is the half that must survive.
        suite.check("narration: ...but keeps its type on the card",
                    (card.get("text") or "") == long_title,
                    f"{len(card.get('text') or '')} characters on screen")
        expected, actual, late = check_draft.narration_drift(out, sb, content)
        suite.check("narration: the checker agrees the title is silent",
                    [k for k, _ in expected] == ["1", "2"]
                    and len(actual) == len(expected) and not late)

    for speed in (1.0, 1.5):
        with tempfile.TemporaryDirectory() as tmp:
            out, sb, content = draft_at(speed, Path(tmp) / "w")
            expected, actual, late = check_draft.narration_drift(out, sb, content)

            # The title is one of them. Without it the lists are different
            # lengths, which is the shape the old check silently zipped away.
            suite.check(f"narration: the title is a clip like any other ({speed:.1f}x)",
                        [k for k, _ in expected] == ["title", "1", "2"]
                        and len(actual) == len(expected),
                        f"expected {[k for k, _ in expected]}, "
                        f"{len(actual)} in the draft")
            suite.check(f"narration: every clip lands on its own shot ({speed:.1f}x)",
                        not late,
                        "; ".join(f"{k} off by {d / check_draft.SEC:.2f}s"
                                  for k, d in late))

            # The title's clip starts `lead` into the card, not at its start -
            # and `lead` shortens with the video, which is the half a 1.0x-only
            # test would never see.
            lead = float((sb.get("title_card") or {}).get("lead", 0.0))
            suite.check(f"narration: the title waits for the stinger ({speed:.1f}x)",
                        abs(expected[0][1] - check_draft._us(lead)) < 2
                        and lead > 0,
                        f"title clip at {expected[0][1] / check_draft.SEC:.3f}s, "
                        f"lead {lead:.3f}s")

            # ...and it still catches a clip that really has moved. Without
            # this the fix above could simply be "never report anything".
            nudged = json.loads(json.dumps(content))
            for track in nudged["tracks"]:
                if track["type"] == "audio" and track.get("name") == "配音":
                    moved = sorted(track["segments"],
                                   key=lambda s: s["target_timerange"]["start"])
                    moved[0]["target_timerange"]["start"] += 500_000
                    break
            _, _, caught = check_draft.narration_drift(out, sb, nudged)
            suite.check(f"narration: a drifted title clip is caught ({speed:.1f}x)",
                        [k for k, _ in caught] == ["title"],
                        f"reported {[k for k, _ in caught]}")


def main():
    print("cartoon-econ-video self-test (offline)\n")
    suite = Suite()

    # --- imports -----------------------------------------------------------
    try:
        import assets as assets_mod
        import audio as audio_mod
        import checks as checks_mod
        import matting
        import plan as plan_mod
        import render as render_mod
        import textkit
        import timing
        import verify as verify_mod
        from layout import Layout
        suite.check("every module imports", True)
    except Exception as exc:
        suite.check("every module imports", False, str(exc))
        return 1

    # --- casts -------------------------------------------------------------
    import styles as styles_mod
    reg_issues = styles_mod.problems()
    suite.check("style registry casts/styles.json", not reg_issues,
                reg_issues[0] if reg_issues else "")
    suite.check("registry has a usable default",
                styles_mod.default_key() is not None,
                f"default: {styles_mod.default_key()}")
    for key, entry in sorted(styles_mod.available().items()):
        issues = assets_mod.Cast.load(entry["file"],
                                      root=ROOT / "casts").problems()
        suite.check(f"cast {key}", not issues,
                    issues[0] if issues else "")
        if entry["registered"]:
            suite.check(f"cast {key} file exists", entry["file"].exists(),
                        str(entry["file"]))

    # --- the config actually drives the code ------------------------------
    # A settings file that everything ignores looks exactly like one that
    # works. These assert the wiring rather than the file: every look value
    # the modules use has to be the one styles.py resolved, so re-hardcoding
    # any of them fails here instead of silently making the file decorative.
    look = styles_mod.look()
    wired = {
        "render.FRAMING": (render_mod.FRAMING, look["framing"]),
        "render.CAPTION_FADE": (render_mod.CAPTION_FADE,
                                look["timing"]["caption_fade"]),
        "render.ELEMENT_FADE": (render_mod.ELEMENT_FADE,
                                look["timing"]["element_fade"]),
        "checks.MIN_GAP": (checks_mod.MIN_GAP, look["safe_zones"]["min_gap"]),
        "checks.SIDE_MARGIN": (checks_mod.SIDE_MARGIN,
                               look["safe_zones"]["side_margin"]),
        "checks.TOP_MARGIN": (checks_mod.TOP_MARGIN,
                              look["safe_zones"]["top_margin"]),
        "checks.EDGE_TOLERANCE": (checks_mod.EDGE_TOLERANCE,
                                  look["safe_zones"]["edge_tolerance"]),
        "matting.CHOKE": (matting.CHOKE, look["matting"]["choke"]),
        "matting.RIM_PIXELS": (matting.RIM_PIXELS, look["matting"]["rim_pixels"]),
        "plan.LABEL_TONES": (set(plan_mod.LABEL_TONES), set(look["label_tones"])),
    }
    adrift = [name for name, (got, want) in wired.items() if got != want]
    suite.check("every look setting comes from casts/styles.json", not adrift,
                f"{len(wired)} checked" if not adrift else ", ".join(adrift))

    # And an override has to reach the geometry, not just the dict.
    real_registry = styles_mod.registry
    try:
        styles_mod.registry = lambda: {
            "styles": {"_probe": {"look": {"frame": {"landscape": {
                "subtitle_size": 0.08, "subtitle_y": 0.5}}}}}}
        probe = Layout("landscape", style="_probe")
        plain = Layout("landscape")
        suite.check("a style's look override reaches the frame geometry",
                    probe.subtitle_font_px() > plain.subtitle_font_px()
                    and probe.subtitle_center_y != plain.subtitle_center_y,
                    f"caption {plain.subtitle_font_px()}px@{plain.subtitle_center_y} "
                    f"-> {probe.subtitle_font_px()}px@{probe.subtitle_center_y}")
        suite.check("an override leaves its siblings alone",
                    Layout("landscape", style="_probe").cfg["stage"]
                    == plain.cfg["stage"])
    finally:
        styles_mod.registry = real_registry

    # --- every drawing is made for one script, and only that script -------
    # There used to be a shared catalogue of about sixty drawings that every
    # video picked from, and that is why every video looked like the last one:
    # measured across eight finished videos, one pile of gold coins appeared in
    # five of them. The director describes pictures now and they are drawn per
    # video. These pin the properties that makes that work.
    with tempfile.TemporaryDirectory() as tmp:
        casts = Path(tmp)
        cast_data = {
            "name": "probe", "style": "test style",
            "background": {"prompt": "a plate"},
            "characters": {
                "alice": {"look": "a woman", "role": "the worker",
                          "relative_height": 1.0, "poses": {"stand": "standing"}},
                "bob": {"look": "a man", "role": "the boss",
                        "relative_height": 1.0, "poses": {"stand": "standing"}}},
        }
        (casts / "probe.json").write_text(json.dumps(cast_data), encoding="utf-8")
        probe = assets_mod.Cast.load(casts / "probe.json", root=casts)

        def shot(elements, sid=1, framing="medium"):
            return {"id": sid, "framing": framing, "elements": elements}

        told = plan_mod.validate({"shots": [
            shot([{"who": "alice", "shows": "both hands out taking a pay envelope",
                   "x": 0.4, "h": 0.46}]),
            shot([{"who": "alice", "shows": "both hands out taking a pay envelope",
                   "x": 0.4, "h": 0.46}], 2),
            shot([{"who": "alice", "shows": "slumped at a desk, exhausted",
                   "x": 0.4, "h": 0.46}], 3),
        ]}, ["一。", "二。", "三。"], probe)
        names = [sc["elements"][0]["asset"] for sc in told["scenes"]]
        suite.check("the same description is drawn once, a different one twice",
                    names[0] == names[1] and names[2] != names[0]
                    and len(told["drawings"]) == 2,
                    f"{len(told['drawings'])} drawings for 3 elements")

        # The point of the whole change: a video must leave nothing behind for
        # the next one to reuse, or the sameness comes straight back.
        left_behind = sorted(p.name for p in casts.rglob("*")
                             if p.is_file() and p.name != "probe.json")
        suite.check("a described drawing never enters the cast",
                    not left_behind
                    and json.loads((casts / "probe.json").read_text(
                        encoding="utf-8")) == cast_data,
                    "nothing was written beside the cast file"
                    if not left_behind else f"found {left_behind}")

        # What survives is one reference per character. Without it the same
        # character drifts between shots: 82% palette match against 98%.
        suite.check("a character's reference has a home of its own",
                    probe.anchor_path("alice").parent.name == "anchors"
                    and probe.anchor_path("alice").name == "alice.jpg",
                    str(probe.anchor_path("alice").relative_to(casts)))

        junk = plan_mod.validate({"shots": [shot([
            {"who": "carol", "shows": "waving"},
            {"who": ["alice", "bob", "carol"], "shows": "all three arguing"},
            {"who": "alice"},
        ])]}, ["一。"], probe)
        drawn = junk["scenes"][0]["elements"]
        suite.check("a director's mistakes cost an element, not the run",
                    len(drawn) == 2
                    and plan_mod.LABEL_TONES is not None,
                    f"{len(drawn)} of 3 elements survived: "
                    f"{[e['asset'][:22] for e in drawn]}")

        pair = plan_mod.validate({"shots": [shot([
            {"who": ["alice", "bob"], "shows": "alice hands bob an envelope",
             "x": 0.5, "h": 0.6},
            {"who": "alice", "shows": "standing", "x": 0.2, "h": 0.46}])]},
            ["一。"], probe)
        kept = [e["asset"] for e in pair["scenes"][0]["elements"]]
        suite.check("a two-figure drawing claims both its characters",
                    len(kept) == 1 and kept[0].startswith("duo_alice_bob_"),
                    f"kept {[k[:26] for k in kept]}")

        roles = plan_mod.validate({"shots": [shot([
            {"shows": "a whiteboard with a rising line chart", "x": 0.5, "h": 0.5},
            {"shows": "a long shop counter", "x": 0.5, "h": 0.3},
            {"shows": "a fat stack of gold coins", "x": 0.8, "h": 0.3}])]},
            ["一。"], probe)
        got = [e.get("role") for e in roles["scenes"][0]["elements"]]
        suite.check("what an object is for is read off what it is",
                    set(got) == {"board", "furniture", "prop"},
                    f"whiteboard/counter/coins -> {got}")

    house = assets_mod.Cast.load(ROOT / "casts" / "bikini_bottom.json",
                                 root=ROOT / "casts")

    # --- `--from assets` must not mean "redraw everything" -----------------
    # It used to: forcing was right when the cache keyed on a prompt
    # fingerprint inside a shared library. A drawing's filename now carries a
    # hash of what was asked for, so an edited description is already a
    # different file, and forcing only buys identical pictures at full price -
    # measured, twelve images to replace the two that had changed.
    src = (ROOT / "scripts" / "build.py").read_text(encoding="utf-8-sig")
    suite.check("restarting at the assets stage does not redraw what is there",
                'if "assets" in forced and not args.regenerate_assets:' in src,
                "--regenerate-assets is the flag that ignores the cache")

    # --- a drawing cannot be lettered --------------------------------------
    # Image models cannot spell. Asked for a whiteboard "labeled nominal and
    # real wage" one came back with `omi...wage` across it in two alphabets.
    # The words belong in a label, which is real text in the draft - so the
    # request is taken out of the description and the object is drawn blank.
    # "staring at an open payslip" must survive: it names no words.
    cases = [
        ("whiteboard divided into two sections labeled nominal and real wage",
         "whiteboard divided into two sections"),
        ("points at two labeled sections on a whiteboard behind him",
         "points at two sections on a whiteboard behind him"),
        ("a chart titled Q3 revenue by region", "a chart"),
        ("a poster 写着 涨价", "a poster"),
        ("leans forward staring at an open payslip, mouth open in surprise",
         "leans forward staring at an open payslip, mouth open in surprise"),
        ("a plain wooden counter", "a plain wooden counter"),
    ]
    wrong = [(before, plan_mod._unlettered(before, 1, []))
             for before, want in cases
             if plan_mod._unlettered(before, 1, []) != want]
    suite.check("a request for lettering is taken out of a drawing", not wrong,
                f"{len(cases)} phrasings" if not wrong
                else f"{wrong[0][0][:32]!r} -> {wrong[0][1][:32]!r}")

    # --- a drawing that never arrives --------------------------------------
    # Generation fails sometimes, and the renderer's answer to a missing PNG is
    # to skip the element - so the shot renders as an empty plate. The stand-in
    # is matched on who the drawing shows: every pair's filename begins "duo_",
    # so matching on the name put Krabs and SpongeBob in for Patrick and
    # Squidward.
    import build as build_mod
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        for name in ("duo_krabs_sponge_handover_aaaaaa.png",
                     "duo_krabs_sponge_arguing_bbbbbb.png",
                     "duo_patrick_squid_arguing_cccccc.png",
                     "sponge_standing_dddddd.png"):
            Image.new("RGBA", (8, 8)).save(out / name)
        missing = "duo_patrick_squid_paying_eeeeee.png"
        board = {"drawings": [
            {"asset": "duo_krabs_sponge_handover_aaaaaa.png", "kind": "duo",
             "who": ["krabs", "sponge"], "shows": "a handover"},
            {"asset": "duo_krabs_sponge_arguing_bbbbbb.png", "kind": "duo",
             "who": ["krabs", "sponge"], "shows": "an argument"},
            {"asset": "duo_patrick_squid_arguing_cccccc.png", "kind": "duo",
             "who": ["patrick", "squid"], "shows": "an argument"},
            {"asset": missing, "kind": "duo",
             "who": ["patrick", "squid"], "shows": "a payment"}],
            "scenes": [{"id": 1, "elements": [
                {"asset": missing, "who": ["patrick", "squid"],
                 "x": 0.5, "y": 0.97, "h": 0.6}]}]}

        class _Out:
            out = None
        project = _Out()
        project.out = out
        build_mod.reconcile_sprites(project, board, house)
        stood = board["scenes"][0]["elements"]
        suite.check("a drawing that failed is replaced by the same characters",
                    len(stood) == 1
                    and stood[0]["asset"].startswith("duo_patrick_squid_"),
                    f"-> {stood[0]['asset'] if stood else 'dropped'}")

    # --- the drawing budget is a cap, not a suggestion --------------------
    # The brief tells the director it has one and that elements past it are
    # dropped. For a while nothing enforced that: the numbers reached the
    # prompt and went no further. Every picture is generated now, so an
    # uncapped director is real money and twenty seconds each.
    greedy = [{"id": i, "framing": "medium",
               "elements": [{"who": "sponge", "x": 0.5, "h": 0.46,
                             "shows": f"doing distinct thing number {i}"}]}
              for i in range(1, plan_mod.MAX_DRAWINGS + 9)]
    capped = plan_mod.validate({"shots": greedy}, ["句。"] * len(greedy), house)
    bare = [sc["id"] for sc in capped["scenes"]
            if not any("asset" in e for e in sc["elements"])]
    suite.check("a video cannot draw more than its budget",
                len(capped["drawings"]) == plan_mod.MAX_DRAWINGS and not bare,
                f"{len(greedy)} asked, {len(capped['drawings'])} drawn, "
                f"{len(bare)} shot(s) left empty")

    # Degrading beats dropping: past the pair cap the beat is still acted, by
    # one figure instead of two, rather than leaving the frame bare.
    crowd = [{"id": i, "framing": "medium",
              "elements": [{"who": ["krabs", "sponge"], "x": 0.5, "h": 0.6,
                            "shows": f"exchange number {i}"}]}
             for i in range(1, plan_mod.MAX_DUOS + 4)]
    pairs = plan_mod.validate({"shots": crowd}, ["句。"] * len(crowd), house)
    kinds = [d["kind"] for d in pairs["drawings"]]
    suite.check("past the pair cap a beat is drawn with one figure, not none",
                kinds.count("duo") == plan_mod.MAX_DUOS
                and kinds.count("figure") == len(crowd) - plan_mod.MAX_DUOS,
                f"{len(crowd)} asked -> {kinds.count('duo')} pairs + "
                f"{kinds.count('figure')} singles")

    # --- composition: the frame was 90% empty -----------------------------
    # Measured across seven finished videos: foreground filled 8-10% of the
    # frame, the top 24-37% was dead, and the three features that would fix it
    # were used 4, 6 and 0 times in 87 shots.
    WORKER = {"who": "sponge", "shows": "at a stove, working"}
    board = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "medium", "elements": [
            dict(WORKER, x=0.3, h=0.46),
            {"shows": "a whiteboard with a chart on it", "x": 0.7, "h": 0.22,
             "anchor": "center", "y": 0.4}]}]},
        ["一句。"], house)
    plank = next(e for e in board["scenes"][0]["elements"]
                 if e.get("role") == "board")
    suite.check("a board too small to read is raised",
                plank["h"] >= plan_mod.MIN_BOARD_HEIGHT,
                f"0.22 -> {plank['h']}")

    hide = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "medium", "elements": [
            dict(WORKER, x=0.5, h=0.46),
            {"shows": "a kitchen counter", "x": 0.5, "h": 0.44}]}]},
        ["一句。"], house)
    counter = next(e for e in hide["scenes"][0]["elements"]
                   if e.get("role") == "furniture")
    suite.check("furniture cannot swallow whoever stands behind it",
                counter["h"] <= 0.46 * plan_mod.FURNITURE_SHARE + 1e-6,
                f"0.44 -> {counter['h']} against a 0.46 character")

    thin_wide = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "wide", "elements": [
            dict(WORKER, x=0.5, h=0.46)]}]},
        ["一句。"], house)
    suite.check("a wide shot with nothing in it is just a small shot",
                thin_wide["scenes"][0]["framing"] != "wide",
                f"wide -> {thin_wide['scenes'][0]['framing']}")

    borrowed = plan_mod.validate(
        {"shots": [
            {"id": 1, "framing": "medium", "elements": [dict(WORKER, x=0.3, h=0.46)]},
            {"id": 2, "framing": "medium", "elements": [
                {"type": "panel", "x": 0.5, "w": 0.8, "ph": 0.6},
                {"shows": "a fast food restaurant building", "x": 0.5, "h": 0.5}]}]},
        ["一句。", "二句。"], house)
    order = [e.get("role") or e.get("type")
             for e in borrowed["scenes"][1]["elements"]]
    suite.check("an element added after sorting still lands in depth order",
                order[0] == "panel" and "figure" in order[1:],
                f"{order} - a character inserted at index 0 renders behind "
                f"the translucent wall")

    # --- a function defined twice ----------------------------------------
    # Python keeps the later definition and says nothing. Twice while pulling
    # the shared library out, an edit left the old copy below the new one - and
    # the old `_vary_poses` had a different signature, so it would have thrown
    # on the first video with six shots while every short test passed.
    twins = []
    for module in sorted(p.name for p in (ROOT / "scripts").glob("*.py")):
        tree = ast.parse((ROOT / "scripts" / module).read_text(encoding="utf-8-sig"))
        seen = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                if node.name in seen:
                    twins.append(f"{module}:{node.name} at {seen[node.name]} "
                                 f"and {node.lineno}")
                seen[node.name] = node.lineno
    suite.check("nothing is defined twice in the same file", not twins,
                twins[0] if twins else "the later one would win, silently")

    # --- a parameter accepted and then dropped ----------------------------
    # `panel_color` was threaded from the cast into compose_plate and never
    # passed on to the function that uses it. Nothing failed; panels just kept
    # the old colour, and the same silent-edit mistake had already happened
    # twice. A parameter a function accepts and never mentions again is almost
    # always a half-finished edit.
    dropped = []
    for module in ("render.py", "checks.py", "plan.py", "assets.py",
                   "textkit.py", "build.py", "verify.py", "styles.py"):
        tree = ast.parse((Path(__file__).parent / module).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # Dunder signatures are fixed by the language - __exit__ must take
            # exc and tb whether or not it looks at them - so they cannot be
            # evidence of a half-finished edit.
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            declared = {a.arg for a in node.args.args} | {
                a.arg for a in node.args.kwonlyargs}
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            for arg in sorted(declared - used - {"self", "cls"}):
                dropped.append(f"{module}:{node.name}() takes {arg!r} unused")
    suite.check("no parameter is accepted and then ignored", not dropped,
                dropped[0] if dropped else "")

    # --- pure logic --------------------------------------------------------
    beats = plan_mod.split_script("一二三四五六七八九十。" * 6, shot_seconds=5.0)
    joined = "".join(beats)
    suite.check("splitting preserves the script",
                joined == "一二三四五六七八九十。" * 6,
                f"{len(beats)} beats, {len(joined)} chars")

    predicted = timing.clip_seconds("一二三四五六七八九十", 1.0)
    suite.check("duration model is sane", 1.5 < predicted < 3.0,
                f"10 chars -> {predicted:.2f}s")
    fit = timing.fit_to_target("一二三四五六七八九十。" * 20, 9999)
    suite.check("unreachable targets are reported", not fit["ok"])

    lay = Layout("landscape")
    suite.check("caption sits where the reference has it",
                abs(lay.subtitle_center_y / lay.height - 0.903) < 0.001,
                f"y={lay.subtitle_center_y}")
    suite.check("wrapping handles newlines",
                textkit.wrap("上\n下", 60, 900) == ["上", "下"])

    # --- matting -----------------------------------------------------------
    probe = Image.new("RGB", (200, 200), (255, 0, 255))
    ImageDraw.Draw(probe).ellipse([50, 50, 150, 150], fill=(20, 120, 220))
    cut, mode = assets_mod.matting.auto_cutout(probe)
    alpha = np.asarray(cut)[:, :, 3]
    suite.check("chroma matting keeps the subject and drops the key",
                mode == "chroma" and 0.4 < (alpha > 128).mean() < 0.95,
                f"{mode}, {(alpha > 128).mean():.2f} opaque")

    # Removing the key must not take the artwork's own warm colours with it.
    # A spill suppression that ran over every pixel scored perfectly on "no
    # magenta left" and turned SpongeBob's red tie olive, so the contamination
    # measure is useless unless it is paired with this one.
    warm = Image.new("RGB", (200, 200), (255, 0, 255))
    ImageDraw.Draw(warm).ellipse([40, 40, 160, 160], fill=(205, 35, 45))
    kept = np.asarray(assets_mod.matting.auto_cutout(warm)[0])[90:110, 90:110]
    r, g, b = (kept[..., i].mean() for i in range(3))
    suite.check("matting leaves the artwork's own warm colours alone",
                r > 175 and g < 75, f"the red circle stayed {r:.0f},{g:.0f},{b:.0f}")

    # --- labels have to survive the plate they land on --------------------
    # A white outline is the reference look and it is only an outline where
    # the plate is dark. Generated settings are not reliably dark - the first
    # one back was a cream wall - so the choice is measured, and this pins both
    # directions: nothing here should quietly go back to always-white.
    options = [tuple(look["caption"]["fill"]), tuple(look["caption"]["stroke_fill"])]
    swatch = {"type": "label", "text": "标签", "x": 0.3, "y": 0.21, "anchor": "middle"}

    class _NoAssets:
        def sized(self, *a):
            raise AssertionError("a label must not need a sprite")

        def original(self, *a):
            raise AssertionError("a label must not need a sprite")

    def _flat(colour):
        return Image.new("RGB", lay.size, colour)

    def _busy(wall, fixture):
        """A wall with something bolted across it - the case that needs an
        outline at all. The generated kitchen was cream with a steel hood."""
        plate = Image.new("RGB", lay.size, wall)
        ImageDraw.Draw(plate).rectangle(
            [0, int(lay.height * 0.16), lay.width, int(lay.height * 0.26)],
            fill=fixture)
        return plate

    def _pick(plate, tone):
        el = dict(swatch, tone=tone)
        tag = render_mod.build_element_image(el, _NoAssets(), lay)
        return render_mod.outline_against(plate, el, tag, lay, options)

    white, black = options[0], options[1]
    # A flat plate is what every shipped style has, and there the fill carries
    # the label on its own. Whatever the style asked for is left alone - this
    # is the half that keeps the seven styles looking the way they did.
    kept = [_pick(_flat(c), "money") for c in ((250, 247, 224), (18, 52, 96),
                                               (76, 224, 183))]
    suite.check("a flat plate keeps the outline the style asked for",
                all(c == white for c in kept), f"cream, deep water, aqua -> {kept}")

    # A busy one does not: no fill reads against both a cream wall and the
    # steel bolted across it, so here the outline is load-bearing and measured.
    rescued = _pick(_busy((250, 247, 224), (74, 80, 86)), "money")
    suite.check("a label on a busy plate gets an outline that reads",
                rescued == black, f"cream wall + steel hood -> {rescued}")

    # And the measurement weighs the fill as well as the ground. On the ground
    # alone this picks black behind the near-black neutral tone, where the
    # outline disappears into the letters instead of behind them.
    not_lost = _pick(_busy((30, 32, 38), (140, 145, 150)), "neutral")
    suite.check("an outline never vanishes into the letters it outlines",
                not_lost == white, f"dark plate, near-black label -> {not_lost}")

    # Contrast with the fill is a gate rather than one term among three, or the
    # two trade off: on a wooden wall that shipped a near-black label outlined
    # in black, 1.26:1 between the letters and the edge meant to define them.
    # These are the conditions that shipped it: dark wood with a near-white
    # fixture across it, where black reads well against the wood and white
    # reads badly against the fixture, so on a traded-off score black wins.
    wood = _pick(_busy((90, 68, 42), (250, 250, 250)), "neutral")
    fill = render_mod.LABEL_TONES["neutral"]
    suite.check("an outline that loses to its own fill is not chosen",
                render_mod._contrast(wood, fill) >= render_mod.MIN_INK_CONTRAST,
                f"dark wood, near-black label -> {wood}, "
                f"{render_mod._contrast(wood, fill):.1f}:1 against the letters")

    # The renderer and the draft exporter both draw labels and both used to
    # hardcode white. One measurement carried in the storyboard is what stops
    # them drifting, the way three copies of the placement arithmetic once did.
    suite.check("the draft outlines a label the way the render did",
                "el.get(\"outline\"" in (ROOT / "scripts" / "draft.py").read_text(
                    encoding="utf-8-sig"),
                "draft.py reads the outline the storyboard carries")

    # --- a generated setting is the plate, not a prop ---------------------
    # As a panel it was cover-cropped into a 2.7:1 box, which cut the top and
    # the floor off a 16:9 room and left the blank middle. Nothing should put
    # it back into an element.
    build_src = (ROOT / "scripts" / "build.py").read_text(encoding="utf-8-sig")
    suite.check("a generated setting is used as the background plate",
                '"background": _plate(' in build_src
                and "_give_a_place" not in build_src,
                "setting.png reaches the renderer as the plate")

    # --- the cost line has to be true -------------------------------------
    # USAGE was declared and reset and never incremented by anything, so every
    # run ended with "nothing was generated, everything came from cache" - a
    # build that had just spent thirteen image generations said exactly what a
    # fully cached one said. Faked at the socket so this costs nothing.
    import ark as ark_mod
    import config as config_mod

    class _Response:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    real_open, real_key = urllib.request.urlopen, config_mod.ARK_API_KEY
    real_download = ark_mod._download
    try:
        config_mod.ARK_API_KEY = "offline-selftest"
        ark_mod._download = lambda url, out, retries=3: out
        urllib.request.urlopen = lambda *a, **k: _Response(
            {"choices": [{"message": {"content": "ok"}}],
             "data": [{"url": "http://example.invalid/x.png"}]})
        ark_mod.reset_usage()
        ark_mod.chat([{"role": "user", "content": "hi"}])
        ark_mod.generate_image("a wall", Path(tempfile.gettempdir()) / "x.png")
        ark_mod.chat([{"role": "user", "content": [
            {"type": "text", "text": "what"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]}])
        counted = dict(ark_mod.USAGE)
    finally:
        urllib.request.urlopen, config_mod.ARK_API_KEY = real_open, real_key
        ark_mod._download = real_download
        ark_mod.reset_usage()
    suite.check("a run reports what it actually spent",
                counted["text_calls"] == 1 and counted["images"] == 1
                and counted["vision_calls"] == 1,
                f"{counted['text_calls']} director, {counted['images']} image, "
                f"{counted['vision_calls']} vision")

    # --- re-validating a plan must not quietly lose fields ----------------
    # migrate_plan.py rebuilt the model's answer from an allowlist of three
    # keys, so every field the validator learned to read after it was written
    # was dropped on the floor: each shot's `beat`, which the sound design
    # picks its cues from, and the video's `setting`, which is its backdrop. A
    # migrated video came out somewhere else with duller sound and said
    # nothing. This walks the validator's output back through it.
    import migrate_plan as migrate_mod
    with tempfile.TemporaryDirectory() as tmp:
        seed = plan_mod.validate(
            {"title": "标题", "setting": "a back kitchen",
             "shots": [{"id": 1, "framing": "medium",
                        "beat": {"subject": "alice", "action": "is paid",
                                 "emotion": "delighted"},
                        "elements": [{"asset": "sponge_work.png",
                                      "x": 0.5, "h": 0.46}]}]},
            ["一句。"], house)
        path = Path(tmp) / "plan.json"
        path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        again = migrate_mod.migrate(path, ROOT / "casts" / "bikini_bottom.json")
        kept = (again.get("setting") == seed["setting"]
                and again["scenes"][0].get("beat") == seed["scenes"][0].get("beat")
                and again.get("title") == seed["title"])
        suite.check("re-validating a plan keeps everything the validator reads",
                    kept, f"setting {again.get('setting')!r}, "
                          f"beat {'kept' if again['scenes'][0].get('beat') else 'LOST'}")

    # --- two figures should not be the smallest thing in the video --------
    paired = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "medium", "elements": [
            {"who": ["krabs", "sponge"], "x": 0.5, "h": 0.50,
             "shows": "krabs hands sponge an envelope and sponge takes it"}]}]},
        ["一句。"], house)
    duo = paired["scenes"][0]["elements"][0]
    close = look["framing"]["close"]
    suite.check("an interaction is not left smaller than the singles around it",
                duo["h"] >= plan_mod.MIN_DUO_HEIGHT,
                f"0.50 at medium -> {duo['h']} against a solo's "
                f"0.48x{close} = {0.48 * close:.2f}")

    # --- panels reach the frame edges -------------------------------------
    # A director writing "w": 0.30 gets a card floating in the middle of the
    # frame; a wall is supposed to reach the edges. The floor is applied in
    # validate(), so this asks validate() rather than reading the constant.
    walled = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "medium", "elements": [
            {"type": "panel", "x": 0.2, "y": 0.9, "w": 0.30, "ph": 0.20}]}]},
        ["一句。"], house)
    panel = next(el for el in walled["scenes"][0]["elements"]
                 if el.get("type") == "panel")
    suite.check("a panel is widened to sit near the frame edges",
                panel["w"] >= 0.9 and panel["ph"] >= 0.4 and panel["x"] == 0.5,
                f"0.30x0.20 -> {panel['w']:.2f}x{panel['ph']:.2f} at x={panel['x']}")

    # --- a title the script's own opening already says ---------------------
    # The card is read aloud, and the director writes the title from the
    # script it was handed, so the two are often the same sentence half a
    # second apart. Matched on meaning: the director paraphrases, so a repeat
    # is rarely a prefix of anything.
    import build as build_mod

    echo = build_mod.title_is_echo
    suite.check("opening: a reworded restatement is still a restatement",
                echo("男人不能为女人做的3件事", ["有3件事，男人不要为女人做。"]),
                "clauses swapped, no prefix in common")
    suite.check("opening: a restatement in sentence two counts too",
                echo("男人不能为女人做的3件事",
                     ["今天聊个扎心的话题。", "有3件事，男人千万不要为女人做。"])
                and not echo("男人不能为女人做的3件事",
                             ["先说点别的。", "再说点别的。",
                              "有3件事，男人千万不要为女人做。"]),
                "sentence one is a hook; past the pair it is not a stutter")
    # The near miss has to stay spoken. Silencing a title the script never
    # says loses the opening line outright, which is worse than the stutter.
    suite.check("opening: sharing a subject is not saying the same thing",
                not echo("为什么你存不下钱",
                         ["今天聊聊钱的事。", "你有没有发现，工资一到手就没了？"])
                and echo("记账", ["我建议你从记账开始。"])
                and not echo("三十岁", ["二十岁的时候你不会懂。"]),
                "quoted outright still matches at any length")

    # --- nothing is left running after the last word -----------------------
    # Every shot carries a tail so the next does not start on the same breath.
    # The last shot has no next, and the tail there was simply dead air after
    # the final subtitle - on every video this pipeline has made.
    tail_plan = {
        "title": "标题",
        "scenes": [
            {"narration": "第一句话在这里说完。", "framing": "medium",
             "elements": [{"type": "sprite", "asset": "a.png", "x": 0.32,
                           "y": 0.95, "rel": 0.6}]},
            {"narration": "第二句话稍微长一点点。", "framing": "close",
             "elements": [{"type": "sprite", "asset": "b.png", "x": 0.5,
                           "y": 0.95, "rel": 0.6}]},
        ],
        "ending": {"text": "结语在这里", "highlight": ""},
    }
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "tail"
        project = offline_project(work, 1.0, tail_plan, "tail")
        index = {"title": {"duration": 0.9, "path": "", "words": []}}
        for i, scene in enumerate(tail_plan["scenes"], 1):
            index[str(i)] = {"duration": 2.0, "path": "", "words": []}
        sb, _, _ = build_mod.stage_storyboard(project, tail_plan, index)
        shots = sb["scenes"]
        tail_pad = 0.35
        suite.check("ending: the last shot carries no tail",
                    abs(shots[-1]["duration"] - 2.0) < 1e-6
                    and abs(shots[0]["duration"] - (2.0 + tail_pad)) < 1e-6,
                    f"{shots[0]['duration']:.2f}s then {shots[-1]['duration']:.2f}s")
        last = shots[-1]["captions"][-1]
        suite.check("ending: the last subtitle reaches the end of its shot",
                    abs(last["end"] - shots[-1]["duration"]) < 1e-6,
                    f"caption ends {shots[-1]['duration'] - last['end']:.3f}s early")

    # --- the music is chosen, and chosen the same way twice ----------------
    import music as music_mod

    suite.check("music: the label is the front of the filename",
                music_mod.label("紧张Kill Drill - Robert Ruth.mp3") == "紧张"
                and music_mod.label("紧张危机Dismantle.mp3") == "紧张危机"
                # A Chinese artist credit at the END is not a label.
                and music_mod.label("舒缓Sunny Side - 岩崎太整.mp3") == "舒缓"
                and music_mod.label("Untagged.mp3") == "")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "bgm"
        folder.mkdir()
        for name in ("紧张Kill Drill.mp3", "紧张危机Dismantle.mp3",
                     "舒缓Sunny Side.mp3", "开头失落IV.mp3", "失落Rain.mp3",
                     "notes.txt"):
            (folder / name).write_bytes(b"")
        entries = music_mod.library(folder)
        suite.check("music: only playable files are in the library",
                    len(entries) == 5, f"{len(entries)} of 6 files")
        suite.check("music: a two-label track wins a two-mood script",
                    music_mod.pick(entries, ["紧张", "危机"]).name
                    == "紧张危机Dismantle.mp3"
                    and music_mod.pick(entries, ["紧张"]).name
                    == "紧张Kill Drill.mp3")
        # One bed runs under the whole video, so a cue written for an opening
        # ranks below a plain track of the same mood - below it, not out.
        suite.check("music: a track written for an opening ranks below a plain one",
                    music_mod.pick(entries, ["失落"]).name == "失落Rain.mp3"
                    and music_mod.pick(
                        [e for e in entries if e[1] == "开头失落"],
                        ["失落"]).name == "开头失落IV.mp3")
        suite.check("music: nothing matching means no music, not any music",
                    music_mod.pick(entries, ["升华"]) is None,
                    "the wrong bed is more distracting than none")
        # A rebuild has to keep the music it had, or `--from audio` rescores
        # a finished video from the order the filesystem listed the folder in.
        for name in ("紧张AAA.mp3", "紧张ZZZ.mp3"):
            (folder / name).write_bytes(b"")
        repeats = {music_mod.pick(music_mod.library(folder), ["紧张"]).name
                   for _ in range(5)}
        suite.check("music: the same script picks the same track every time",
                    repeats == {"紧张AAA.mp3"}, ", ".join(sorted(repeats)))
        # A project that never set one up keeps the bed that always shipped.
        default = Path(tmp) / "default.wav"
        default.write_bytes(b"")
        chosen, why = music_mod.choose(Path(tmp) / "absent", "文案",
                                       fallback=default)
        suite.check("music: no library falls back to the default bed",
                    chosen == default, why)

    suite.check("music: a label the model invented is dropped",
                music_mod.clean(["紧张", "波澜壮阔", "危机"]) == ["紧张", "危机"]
                and music_mod.clean("舒缓") == ["舒缓"]
                and music_mod.clean(None) == [])
    suite.check("music: the script answers when the director does not",
                "焦虑" in music_mod.moods_in("他很焦虑，晚上睡不着，总是担心明天。")
                and music_mod.moods_in("。。。") == [])
    # Both tracks quote a level in the same band. Below it the bed does
    # nothing; above it the music is a second thing to listen to while
    # somebody is talking.
    import footage_build as footage_mod
    levels = {"drawn": audio_mod.BGM_VOLUME, "footage": footage_mod.BGM_VOLUME}
    in_band = {k: -25.5 <= 20 * np.log10(v) <= -19.5 for k, v in levels.items()}
    suite.check("music: the bed sits 20-25 dB under the voice",
                all(in_band.values()),
                ", ".join(f"{k} {20 * np.log10(v):.1f} dB"
                          for k, v in levels.items()))

    # --- the draft lands where Jianying reads it ---------------------------
    # Written into the editor's own folder, because the alternative was a
    # manual copy after every single build.
    import draft as draft_mod
    import os as os_mod

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        local = tmp / "Local"
        root = local / "JianyingPro"
        default_drafts = root / "User Data" / "Projects" / draft_mod.DRAFT_LEAF
        default_drafts.mkdir(parents=True)
        kept = {k: os_mod.environ.get(k)
                for k in ("LOCALAPPDATA", "APPDATA", "JIANYING_DRAFT_DIR")}
        try:
            os_mod.environ["LOCALAPPDATA"] = str(local)
            os_mod.environ.pop("APPDATA", None)
            os_mod.environ.pop("JIANYING_DRAFT_DIR", None)
            suite.check("draft: the editor's drafts folder is found",
                        draft_mod.jianying_drafts_dir() == default_drafts)

            # Moving the library leaves the default folder in place but empty,
            # so a probe that knows only the default writes every draft where
            # the editor no longer looks - and reports success doing it.
            moved = tmp / "D" / "Drafts" / draft_mod.DRAFT_LEAF
            moved.mkdir(parents=True)
            config_dir = root / "User Data" / "Config"
            config_dir.mkdir(parents=True)
            (config_dir / "globalSetting").write_text(
                json.dumps({"other": str(tmp / "nope"),
                            "currentDraftUserPath": str(moved.parent)}),
                encoding="utf-8")
            suite.check("draft: a relocated library beats the empty default",
                        draft_mod.jianying_drafts_dir() == moved, str(moved))

            mine = tmp / "mine"
            mine.mkdir()
            os_mod.environ["JIANYING_DRAFT_DIR"] = str(mine)
            found = draft_mod.jianying_drafts_dir()
            os_mod.environ["JIANYING_DRAFT_DIR"] = str(tmp / "typo")
            try:
                draft_mod.jianying_drafts_dir()
                refused = False
            except SystemExit:
                refused = True
            suite.check("draft: an explicit folder wins and is still checked",
                        found == mine and refused,
                        "a typo is reported, not silently ignored")
        finally:
            for key, value in kept.items():
                if value is None:
                    os_mod.environ.pop(key, None)
                else:
                    os_mod.environ[key] = value

    # --- a real render, end to end ----------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        synthetic_assets(work)
        storyboard = {
            "video": {"orientation": "landscape", "width": 1920, "height": 1080,
                      "fps": 30, "background": "background.png", "dissolve": 0.4,
                      "crf": 26, "preset": "ultrafast"},
            "title_card": {"text": "自检", "duration": 1.0, "style": "title"},
            "scenes": [
                {"id": 1, "duration": 1.6, "framing": "medium",
                 "subtitle": "第一镜的字幕。",
                 "elements": [{"asset": "a.png", "x": 0.3, "y": 0.97, "h": 0.45},
                              {"asset": "b.png", "x": 0.7, "y": 0.97, "h": 0.45}]},
                {"id": 2, "duration": 1.6, "framing": "close",
                 "subtitle": "第二镜的字幕。",
                 "elements": [{"asset": "prop_c.png", "x": 0.5, "y": 0.97, "h": 0.4},
                              {"type": "label", "text": "标签", "x": 0.5, "y": 0.35}]},
            ],
            "ending_card": {"text": "结束", "duration": 1.0, "highlight": "束"},
        }
        sprites = render_mod.Assets(work)
        findings = checks_mod.inspect(storyboard, sprites, lay, repair=True)
        residual = checks_mod.inspect(storyboard, sprites, lay, repair=False)
        suite.check("layout repair converges", not residual,
                    f"{len(findings)} repaired, {len(residual)} left")

        # A head above the top edge was reported and never repaired: the
        # message went into the log and the video shipped with a decapitated
        # character. Everything else in this pass fixes what it finds.
        tall = {"video": storyboard["video"], "scenes": [{
            "id": 1, "duration": 1.0, "framing": "close", "subtitle": "x",
            "elements": [{"asset": "a.png", "x": 0.5, "y": 0.97, "h": 0.95,
                          "anchor": "bottom", "rel": 1.0}]}]}
        checks_mod.inspect(tall, sprites, lay, repair=True)
        suite.check("a head above the frame is scaled back in, not just reported",
                    not checks_mod.inspect(tall, sprites, lay, repair=False),
                    f"h 0.95 -> {tall['scenes'][0]['elements'][0]['h']}")

        video = work / "selftest.mp4"
        renderer = render_mod.Renderer(storyboard, work)
        total, frames = renderer.render(
            video, [s["duration"] for s in storyboard["scenes"]])
        suite.check("renders frames", video.exists() and frames > 100,
                    f"{frames} frames / {total:.1f}s")
        suite.check("held frames are reused, not recomputed",
                    len(renderer._frame_cache) < frames / 4,
                    f"{len(renderer._frame_cache)} unique of {frames}")

        track = audio_mod.mix(
            audio_mod.build_narration([(None, total)], work / "n.wav"),
            work / "a.wav", total)
        final = work / "final.mp4"
        audio_mod.mux(video, track, final)
        suite.check("muxes audio", final.exists())

        # --- the opening: a spoken title over a stinger --------------------
        # The title card used to hold a silent slot, so the video opened on two
        # and a half seconds of nothing. Three things have to line up now, and
        # each is checked by measuring the audio rather than by trusting the
        # call: the card is long enough for its own line, the voice waits for
        # the stinger, and the stinger is actually there.
        import build as build_mod
        import config  # bound later in main(); needed here first

        lead = audio_mod.TITLE_SFX_LEAD
        suite.check("opening: a long title lengthens its card",
                    build_mod.title_slot(2.6, 1.0, 0.35) == 2.6
                    and abs(build_mod.title_slot(2.6, 3.5, 0.35)
                            - (lead + 3.5 + 0.35)) < 1e-6,
                    f"{build_mod.title_slot(2.6, 3.5, 0.35):.2f}s for a 3.5s title")
        # ...but not without limit. The director brief asks for ten characters
        # or fewer and that is a request; a thirty-character title reads for
        # five seconds and would hold the card that long before shot 1.
        suite.check("opening: a title too long to read loses its voice",
                    not build_mod.title_voice_fits(5.3, 0.35)
                    and build_mod.title_voice_fits(2.1, 0.35)
                    and build_mod.title_slot(2.6, 30.0, 0.35)
                    <= build_mod.MAX_TITLE_SLOT,
                    f"cap {build_mod.MAX_TITLE_SLOT}s")

        spoken = work / "title_voice.wav"
        subprocess.run(
            [config.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
             "-i", "sine=f=440:d=1.2", "-ar", "44100", "-ac", "2",
             str(spoken)], check=True)
        slot = build_mod.title_slot(2.6, 1.2, 0.35)
        opening_narration = audio_mod.build_narration(
            [(spoken, slot, lead), (None, 1.0)], work / "opening.wav")

        def rms_db(path, start, length):
            raw = work / "seg.raw"
            subprocess.run(
                [config.FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}",
                 "-i", str(path), "-t", f"{length:.3f}", "-ac", "1",
                 "-ar", "8000", "-f", "s16le", str(raw)], check=True)
            data = np.fromfile(raw, dtype="<i2").astype(float) / 32768
            if not len(data):
                return -120.0
            return 20 * np.log10(max(float(np.sqrt((data ** 2).mean())), 1e-6))

        quiet = rms_db(opening_narration, 0.0, lead - 0.05)
        voiced = rms_db(opening_narration, lead + 0.1, 0.6)
        suite.check("opening: the title's voice waits for the stinger",
                    quiet < -60 < voiced,
                    f"{quiet:.0f} dB before the lead, {voiced:.0f} dB after")

        import sfx as sfx_mod
        cue_path = sfx_mod.library().get(build_mod.OPENING_SFX)
        suite.check("opening: the stinger is in the cue library",
                    cue_path is not None, build_mod.OPENING_SFX)
        # gen_sfx.py states its own invariant - everything here is generated,
        # so there is no licensing question and the library rebuilds from one
        # command. A downloaded cue was committed here by mistake and broke
        # that; this is the check that would have caught it.
        import gen_sfx
        # Every cue is generated except the opening one, which is supplied.
        # gen_sfx says so in its own docstring, and the exception is the point:
        # a synthesised stand-in for the sound these videos open on is worse
        # than none, so nothing may quietly substitute for it.
        shipped = sorted(set(sfx_mod.library()) - set(gen_sfx.GENERATORS))
        suite.check("opening: the cue is supplied, everything else is generated",
                    shipped == [build_mod.OPENING_SFX],
                    f"shipped: {shipped or 'nothing'}")
        suite.check("opening: nothing can synthesise the cue behind our backs",
                    build_mod.OPENING_SFX not in gen_sfx.GENERATORS)
        # A leftover copy in the other format shadows the shipped one silently:
        # same name, same sound, no way to tell from the build which is
        # playing. This actually happened to the installed skill directory.
        duplicates = sfx_mod.duplicate_cues()
        suite.check("opening: no cue is shadowed by a copy in another format",
                    not duplicates,
                    ", ".join(sorted(duplicates)) or "26 cues, no collisions")
        if cue_path:
            cue = [(0.0, build_mod.OPENING_SFX, cue_path, 0.7)]
            on = audio_mod.mix(opening_narration, work / "cue_on.wav",
                               slot + 1.0, bgm=None, cues=cue)
            off = audio_mod.mix(opening_narration, work / "cue_off.wav",
                                slot + 1.0, bgm=None)
            suite.check("opening: the stinger lands on the first frame",
                        rms_db(off, 0.0, 0.35) < -60 < rms_db(on, 0.0, 0.35),
                        f"{rms_db(off, 0.0, 0.35):.0f} dB without, "
                        f"{rms_db(on, 0.0, 0.35):.0f} dB with")
            # A cue's own gain overrides cue_volume. Without it the stinger is
            # mixed at the library level, which is set for a coin drop under
            # narration rather than for the accent that opens the video.
            loud = rms_db(on, 0.0, 0.35)
            soft = rms_db(audio_mod.mix(
                opening_narration, work / "cue_soft.wav", slot + 1.0,
                bgm=None, cues=[(0.0, "x", cue_path)], cue_volume=0.34),
                0.0, 0.35)
            suite.check("opening: a cue can carry its own gain",
                        loud > soft + 3,
                        f"{loud:.0f} dB at 0.7 against {soft:.0f} dB at 0.34")

        # The draft is written blind - Jianying is not installed on most
        # machines that build one - so the only thing that can be checked is
        # that its own numbers rebuild the frames the renderer drew.
        import check_draft
        import draft as draft_mod
        (work / "storyboard.json").write_text(
            json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")
        draft_dir, _, layers = draft_mod.DraftBuilder(
            work, name="selftest").build(work / "jianying")
        diffs = check_draft.compare(work, draft_dir) or []
        worst = max((d for _, d in diffs), default=99.0)
        suite.check("the Jianying draft rebuilds the same frames",
                    diffs and worst <= check_draft.MAX_MEAN_DIFF,
                    f"{len(diffs)} shots over {layers} layers, "
                    f"worst {worst:.2f}/255")

        # Sound cues are derived from the storyboard, and the two outputs must
        # derive them from the same list. Planned before the cards were
        # attached, every cue landed a title-card early and the ending sting
        # was never placed - silent, plausible, and invisible in every check.
        import sfx as sfx_mod
        durations = [s["duration"] for s in storyboard["scenes"]]
        cues = sfx_mod.plan(storyboard, durations, cast=None)
        title = float(storyboard["title_card"]["duration"])
        suite.check("sound cues fall inside the video, after the title card",
                    cues and all(title <= w <= total for w, _, _ in cues),
                    f"{len(cues)} cues from {min(w for w,_,_ in cues):.1f}s "
                    f"to {max(w for w,_,_ in cues):.1f}s of {total:.1f}s")
        punchline = set(styles_mod.look()["sound"]["cues"].get("punchline") or [])
        ending_at = [w for w, n, _ in cues if n in punchline]
        suite.check("the ending card gets its sting",
                    ending_at and ending_at[-1] > total - 5.0,
                    f"at {ending_at[-1]:.1f}s" if ending_at else "none")

        # Variety is the point, not decoration. A fixed table gave one real
        # video 53% the same effect - seven whooshes of thirteen cues - which
        # is what a viewer hears as "these don't fit" before they notice
        # anything about the sounds themselves.
        long_board = dict(storyboard, scenes=[
            dict(storyboard["scenes"][i % 2], id=i + 1,
                 beat={"emotion": e}, duration=2.0)
            for i, e in enumerate(["happy", "confused", "disappointed", "smug",
                                   "delighted", "worried", "shocked", "glum"])])
        many = sfx_mod.plan(long_board, [2.0] * 8, cast=None)
        names = [n for _, n, _ in many]
        commonest = max(names.count(n) for n in set(names)) if names else 99
        suite.check("cues do not repeat while the palette has anything left",
                    len(set(names)) >= max(4, len(names) * 0.6)
                    and commonest <= max(2, len(names) // 4),
                    f"{len(set(names))} distinct of {len(names)}, "
                    f"most-repeated {commonest}")

        # And the reaction has to come from the beat, not from the props.
        happy = sfx_mod.plan(
            dict(storyboard, scenes=[dict(storyboard["scenes"][0],
                                          beat={"emotion": "delighted"})]),
            [3.0], cast=None)
        glum = sfx_mod.plan(
            dict(storyboard, scenes=[dict(storyboard["scenes"][0],
                                          beat={"emotion": "dismayed"})]),
            [3.0], cast=None)
        suite.check("a delighted beat and a dismayed one sound different",
                    [n for _, n, _ in happy] != [n for _, n, _ in glum],
                    f"{[n for _, n, _ in happy]} vs {[n for _, n, _ in glum]}")

        # The build records the plan in the storyboard so the mix and the draft
        # cannot each derive their own; the draft reads only that.
        storyboard["sound_cues"] = [[round(w, 3), n] for w, n, _ in cues]
        (work / "storyboard.json").write_text(
            json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")

        draft_dir, _, layers = draft_mod.DraftBuilder(
            work, name="selftest").build(work / "jianying")
        content = json.loads((draft_dir / "draft_content.json").read_text(
            encoding="utf-8-sig"))
        by_kind = {}
        for track in content["tracks"]:
            name = str(track.get("name", ""))
            key = ("text" if name.startswith("文字") else
                   "sfx" if name.startswith("音效") else name)
            by_kind[key] = by_kind.get(key, 0) + len(track["segments"])
        # A camera move belongs only to the framings the config names, and it
        # has to be a move: scaling every layer where it stands makes the shot
        # pull apart instead of pushing in, so the offsets are keyframed too.
        motion = styles_mod.look()["motion"]
        moving = {}
        for track in content["tracks"]:
            if track["type"] != "video":
                continue
            for sg in track["segments"]:
                props = {kf.get("property_type")
                         for kf in sg.get("common_keyframes") or []}
                if props:
                    moving.setdefault(sg["target_timerange"]["start"], set())
                    moving[sg["target_timerange"]["start"]] |= props
        want_moving = sum(1 for sc in storyboard["scenes"]
                          if sc.get("framing") in motion["framings"])
        suite.check("only the configured framings get a camera move",
                    len(moving) == want_moving,
                    f"{len(moving)} moving shot(s), "
                    f"{want_moving} {'/'.join(motion['framings'])} in the board")
        suite.check("a move keyframes position as well as scale",
                    all({"KFTypeScaleX", "KFTypePositionX"} <= props
                        for props in moving.values()),
                    "scaling in place is not a camera move")

        wanted_labels = sum(1 for sc in storyboard["scenes"]
                            for el in sc["elements"]
                            if el.get("type") in ("label", "bubble"))
        suite.check("labels export as editable text, not pictures",
                    by_kind.get("text", 0) == wanted_labels,
                    f"{by_kind.get('text', 0)} text segments for "
                    f"{wanted_labels} label(s)")
        # Two labels in one shot both span it, and two cues can land closer
        # together than an effect is long. Jianying allows one segment per
        # track at a time, so both need parallel lanes rather than a drop.
        suite.check("overlapping text and cues get parallel lanes",
                    by_kind.get("sfx", 0) == len(cues),
                    f"{by_kind.get('sfx', 0)} cue segments")

        report = verify_mod.Report()
        duration = verify_mod.check_container(report, final, storyboard)
        verify_mod.check_background(report, final, duration)
        verify_mod.check_layout(report, storyboard, work, lay)
        bad = report.failures()
        suite.check("the verifier passes its own render", not bad,
                    "; ".join(n for _, n, _ in bad))

    speed_checks(suite)
    narration_alignment_checks(suite)

    # The newer checks live in modules of their own. Two sessions work on
    # this repo at once; a single thousand-line test file is where their work
    # is guaranteed to collide.
    import selftest_workflow
    selftest_workflow.run(suite, lay)
    import selftest_footage
    selftest_footage.run(suite, lay)

    print()
    if suite.failures:
        print(f"{suite.failures} check(s) failed - the installation is not healthy")
    else:
        print("all checks passed - the pipeline works offline; "
              "run build.py --check for the API side")
    return 1 if suite.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
