"""Offline checks for how a video is built, revised and handed over.

The build's stages, what a re-run redoes, and what the Jianying draft carries
beside the MP4. Split out of selftest.py for the reason selftest_footage.py
was: one thousand-line file is where two sessions' work collides.

Run through selftest.py, which owns the Suite and prints the summary.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def offline_env():
    """An environment in which no build can reach a paid API.

    Both keys set to empty rather than removed: config.py lets a real
    environment variable win over .env, and an empty one is still a real one.
    A selftest run on a machine holding a working key must not spend it.
    """
    env = dict(os.environ)
    env["ARK_API_KEY"] = ""
    env["VOLC_TTS_KEY"] = ""
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def sprite(path, colour, size=(300, 420)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse([4, 4, size[0] - 4, size[1] - 4],
                                  fill=colour + (255,),
                                  outline=(20, 20, 20, 255), width=6)
    image.save(path)


def plate(path, size=(1920, 1080)):
    image = Image.new("RGB", size, (90, 190, 200))
    ImageDraw.Draw(image).rectangle([0, int(size[1] * 0.7), size[0], size[1]],
                                    fill=(110, 200, 80))
    image.save(path)


def probe_cast(directory):
    import assets as assets_mod
    data = {"name": "probe", "style": "test style",
            "background": {"prompt": "a plate"},
            "characters": {
                "alice": {"look": "a woman", "role": "the worker"},
                "bob": {"look": "a man", "role": "the boss"}}}
    (directory / "probe.json").write_text(json.dumps(data), encoding="utf-8")
    return assets_mod.Cast.load(directory / "probe.json", root=directory)


def run(suite, lay):
    """Add every workflow check to `suite`."""
    stop_after_checks(suite)
    refresh_checks(suite)
    freshness_checks(suite)
    draft_parity_checks(suite)
    verification_checks(suite)


def verification_checks(suite):
    """The checks that used to pass having looked at nothing can now fail."""
    import config
    import matting
    import numpy as np
    import plan as plan_mod
    import verify as verify_mod

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        silent = work / "mute.mp4"
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=320x180:d=1", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(silent)], check=True)
        voiced = work / "voiced.mp4"
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", str(silent),
                        "-f", "lavfi", "-i", "sine=f=440:d=1", "-c:v", "copy",
                        "-c:a", "aac", "-shortest", str(voiced)], check=True)
        board = {"video": {"width": 320, "height": 180, "fps": 25},
                 "scenes": [{"duration": 1.0}]}
        rows = {}
        for name, video in (("mute", silent), ("voiced", voiced)):
            report = verify_mod.Report()
            verify_mod.check_container(report, video, board)
            rows[name] = next(r for r in report.rows if r[1] == "audio track")
        suite.check("verify: a video with no audio stream fails the audio check",
                    not rows["mute"][0] and rows["voiced"][0]
                    and rows["voiced"][2] == "aac",
                    f"mute -> {rows['mute'][2]}, voiced -> {rows['voiced'][2]}")

        def cutout(name, size, box=None, speck=None, fill=255):
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            if box:
                draw.rectangle(box, fill=(200, 60, 60, fill))
            if speck:                    # faint, like the one found for real
                draw.rectangle(speck, fill=(200, 60, 60, 57))
            image.save(work / name)
            return name

        board = {"scenes": [{"elements": [
            {"asset": cutout("good.png", (400, 600), (10, 10, 390, 590))},
            {"asset": cutout("whole.png", (1920, 1920), (0, 0, 1919, 1919))},
            {"asset": cutout("empty.png", (400, 600), (190, 290, 196, 296))},
            {"asset": cutout("held_open.png", (900, 900), (600, 300, 890, 890),
                             speck=(4, 4, 8, 8))}]}]}
        report = verify_mod.Report()
        verify_mod.check_sprites(report, work, board)
        ok, _, detail = report.rows[0]
        flagged = [name for name in ("whole.png", "empty.png", "held_open.png")
                   if name in detail]
        suite.check("verify: the video's own cut-outs are examined",
                    not ok and len(flagged) == 3 and "good.png" not in detail,
                    detail[:120])

    # A speck the key let through used to hold the crop open around it.
    key = Image.new("RGB", (600, 800), (255, 0, 255))
    draw = ImageDraw.Draw(key)
    draw.ellipse([250, 200, 450, 700], fill=(30, 90, 200))
    draw.ellipse([470, 180, 500, 210], fill=(30, 90, 200))       # a drawn drop
    draw.rectangle([6, 6, 9, 9], fill=(215, 60, 215))            # the speck
    cut, _ = matting.auto_cutout(key)
    suite.check("matting: a stray speck no longer widens the crop",
                cut.width < 300 and cut.height < 560,
                f"{cut.width}x{cut.height} for a 250x520 drawing and its drop")
    anchor = ROOT / "casts" / "flat_geo" / "anchors" / "boss.jpg"
    if anchor.exists():
        cut, _ = matting.auto_cutout(Image.open(anchor))
        alpha = np.asarray(cut.getchannel("A"))
        ys, xs = np.where(alpha > 128)
        fill = min((xs.max() - xs.min() + 1) / cut.width,
                   (ys.max() - ys.min() + 1) / cut.height)
        suite.check("matting: the flat_geo reference crops to its figure",
                    fill > 0.9, f"{cut.width}x{cut.height}, {fill:.0%} filled")

    source = (SCRIPTS / "plan.py").read_text(encoding="utf-8-sig")
    system = source[source.index('SYSTEM = """'):]
    system = system[:system.index('"""', 12)]
    suite.check("the director's system prompt fits every style",
                "SpongeBob" not in system and "filenames" not in system)
    suite.check("the director's system prompt names no catalogue",
                "listed" not in plan_mod.SYSTEM)


def draft_parity_checks(suite):
    """The draft holds what the MP4 holds: walls, balloons, captions, music."""
    import audio as audio_mod
    import check_draft
    import draft as draft_mod
    import numpy as np
    import render as render_mod
    import tts as tts_mod
    from layout import from_video

    violet = [88, 72, 130]               # neon_cyberpunk's panel colour
    bed = ROOT / "assets" / "bgm_default.wav"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        plate(work / "background.png")
        sprite(work / "a.png", (230, 60, 60))
        board = {
            "video": {"orientation": "landscape", "width": 1920, "height": 1080,
                      "fps": 30, "background": "background.png",
                      "dissolve": 0.3, "panel_color": violet},
            "scenes": [
                {"id": 1, "duration": 2.4, "framing": "medium", "subtitle": "一。",
                 "captions": [{"text": "第一句字幕", "start": 0.0, "end": 2.4}],
                 "elements": [
                     {"type": "panel", "x": 0.5, "y": 0.99, "w": 0.94, "ph": 0.5},
                     {"asset": "a.png", "x": 0.3, "y": 0.97, "h": 0.4},
                     {"type": "bubble", "text": "我更卖力了！", "tail": "left",
                      "x": 0.7, "y": 0.25, "anchor": "center"}]},
                {"id": 2, "duration": 2.0, "framing": "close", "subtitle": "二。",
                 "captions": [{"text": "第二句字幕", "start": 0.0, "end": 2.0}],
                 "elements": [
                     {"asset": "a.png", "x": 0.5, "y": 0.97, "h": 0.4},
                     {"type": "label", "text": "标签", "x": 0.5, "y": 0.3,
                      "anchor": "center"}]}],
            "music": {"volume": 0.16, "duck": True, "beds": [
                {"path": str(bed), "start": 0.0, "end": 2.6,
                 "fade_in": 1.2, "fade_out": 2.0},
                {"path": str(bed), "start": 1.6, "end": 4.4,
                 "fade_in": 2.0, "fade_out": 2.0}]},
        }
        index = {}
        (work / "voice").mkdir()
        for i, scene in enumerate(board["scenes"], 1):
            made = tts_mod._silent(scene["subtitle"], work / "voice" /
                                   f"scene_{i:02d}.mp3", 1.0)
            index[str(i)] = {"text": scene["subtitle"], "path": str(made["path"]),
                             "duration": 1.6, "words": [], "degraded": False}
        (work / "voice" / "index.json").write_text(json.dumps(index),
                                                    encoding="utf-8")
        (work / "storyboard.json").write_text(json.dumps(board, ensure_ascii=False),
                                              encoding="utf-8")
        audio_mod.write_srt([(0.0, 2.4, "第一句字幕"), (2.4, 4.4, "第二句字幕")],
                            work / "parity.srt")
        path, _, _ = draft_mod.DraftBuilder(work, name="parity").build(
            work / "jianying")
        content = json.loads((path / "draft_content.json").read_text(
            encoding="utf-8"))

        lay = from_video(board["video"])
        materials = {m["id"]: m["path"] for m in content["materials"]["videos"]}
        panel_file = next(p for p in materials.values() if "panel" in Path(p).name)
        drawn = np.asarray(Image.open(panel_file).convert("RGBA"))
        want = np.asarray(render_mod.build_element_image(
            board["scenes"][0]["elements"][0], render_mod.Assets(work), lay,
            panel_color=render_mod.Renderer(board, work).panel_color))
        got_rgb = np.median(drawn[drawn[:, :, 3] > 0][:, :3], axis=0)
        want_rgb = np.median(want[want[:, :, 3] > 0][:, :3], axis=0)
        suite.check("draft: a panel is the colour the MP4 drew it",
                    np.abs(got_rgb - want_rgb).max() <= 2,
                    f"draft {got_rgb.astype(int).tolist()}, "
                    f"render {want_rgb.astype(int).tolist()}")

        texts = {seg["material_id"]: seg for tr in content["tracks"]
                 if tr["type"] == "text" for seg in tr["segments"]}
        words = [json.loads(m["content"])["text"]
                 for m in content["materials"]["texts"]]
        balloon = any("bubble" in Path(p).name for p in materials.values())
        suite.check("draft: a speech bubble keeps its balloon under the words",
                    balloon and any("卖力" in w for w in words),
                    "balloon picture + editable words")
        diffs = check_draft.compare(work, path) or []
        suite.check("draft: ...and still rebuilds the frames the MP4 drew",
                    diffs and max(d for _, d in diffs) <= check_draft.MAX_MEAN_DIFF,
                    ", ".join(f"shot {i} {d:.2f}" for i, d in diffs))

        subs = [m for m in content["materials"]["texts"]
                if json.loads(m["content"])["text"].endswith("字幕")]
        strokes = [json.loads(m["content"])["styles"][0].get("strokes")
                   for m in subs]
        wanted_y = -(lay.subtitle_center_y - lay.height / 2) / (lay.height / 2)
        placed = [texts[m["id"]]["clip"]["transform"]["y"] for m in subs
                  if m["id"] in texts]
        suite.check("draft: subtitles carry the MP4's outline",
                    subs and all(strokes), f"{len(subs)} subtitle(s)")
        suite.check("draft: subtitles sit on the MP4's caption line",
                    placed and all(abs(y - wanted_y) < 1e-3 for y in placed),
                    f"y {placed[:1]} vs {wanted_y:.3f}")

        music = [seg for tr in content["tracks"] if tr["type"] == "audio"
                 and str(tr.get("name", "")).startswith("配乐")
                 for seg in tr["segments"]]
        lanes = {tr.get("name") for tr in content["tracks"]
                 if str(tr.get("name", "")).startswith("配乐")}
        ducked = [seg for seg in music
                  if any(kf.get("property_type") == "KFTypeVolume"
                         for kf in seg.get("common_keyframes") or [])]
        suite.check("draft: the music the mix used is in the draft",
                    len(music) >= 2 and len(lanes) == 2,
                    f"{len(music)} segment(s) on {len(lanes)} lane(s): "
                    "two sections crossfading need two")
        suite.check("draft: the music dips under the narration",
                    ducked and content["materials"].get("audio_fades"),
                    f"{len(ducked)} keyframed, "
                    f"{len(content['materials'].get('audio_fades') or [])} fades")


def stop_after_checks(suite):
    """`--stop-after` stops where it says, including before anything is paid."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = work / "stop.json"
        project.write_text(json.dumps(
            {"name": "stop", "out": str(work / "out"), "speed": 1.5,
             "script_text": "第一句在这里。第二句也在这里。"},
            ensure_ascii=False), encoding="utf-8")
        for stage, must_not_reach in (("plan", "[2/8]"), ("assets", "[3/8]")):
            done = subprocess.run(
                [sys.executable, str(SCRIPTS / "build.py"), str(project),
                 "--stop-after", stage, "--no-verify"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=offline_env(), timeout=300)
            reached = must_not_reach in done.stdout
            spoke = (work / "out" / "voice" / "index.json").exists()
            suite.check(f"--stop-after {stage} stops there",
                        done.returncode == 0 and not reached and not spoke,
                        f"exit {done.returncode}, "
                        f"{'ran on past it' if reached else 'stopped'}"
                        f"{', narration was requested' if spoke else ''}")


def refresh_checks(suite):
    """Editing a description in plan.json asks for that picture again."""
    import plan as plan_mod

    with tempfile.TemporaryDirectory() as tmp:
        cast = probe_cast(Path(tmp))
        answer = {"shots": [
            {"id": 1, "framing": "medium", "elements": [
                {"who": "alice", "shows": "holding a pay envelope", "x": 0.4}]},
            {"id": 2, "framing": "close", "elements": [
                {"who": "alice", "shows": "holding a pay envelope", "x": 0.4}]},
            {"id": 3, "framing": "medium", "elements": [
                {"who": "bob", "shows": "pointing at the door", "x": 0.6}]}]}
        plan = plan_mod.validate(answer, ["一。", "二。", "三。"], cast)
        untouched = json.loads(json.dumps(plan))
        suite.check("an unedited plan is left exactly as it was",
                    not plan_mod.refresh_drawings(plan, cast)
                    and plan == untouched)

        before = plan["scenes"][1]["elements"][0]["asset"]
        plan["scenes"][1]["elements"][0]["shows"] = "tearing open a pay envelope"
        changes = plan_mod.refresh_drawings(plan, cast)
        after = plan["scenes"][1]["elements"][0]["asset"]
        names = {d["asset"] for d in plan["drawings"]}
        suite.check("an edited description becomes a new drawing",
                    after != before and after in names
                    and plan["scenes"][0]["elements"][0]["asset"] == before
                    and before in names,
                    f"{len(changes)} change(s): {changes[0] if changes else ''}")

        # Edited in the list instead: every shot using it follows.
        entry = next(d for d in plan["drawings"] if d["asset"] == before)
        entry["shows"] = "counting a stack of banknotes"
        plan_mod.refresh_drawings(plan, cast)
        followed = plan["scenes"][0]["elements"][0]
        suite.check("a drawing re-described in the list moves every shot with it",
                    followed["asset"] != before
                    and followed["shows"] == "counting a stack of banknotes"
                    and before not in {d["asset"] for d in plan["drawings"]})

        # A variation written before `_vary_poses` kept the description with
        # the name must be healed, not reverted to the picture it replaced.
        legacy = plan_mod.validate(answer, ["一。", "二。", "三。"], cast)
        element = legacy["scenes"][2]["elements"][0]
        varied = element["shows"] + ", grinning"
        fresh = plan_mod._sprite_name("figure", ["bob"], varied)
        legacy["drawings"].append({"asset": fresh, "kind": "figure",
                                   "who": ["bob"], "shows": varied})
        element["asset"] = fresh                 # name moved, shows left behind
        plan_mod.refresh_drawings(legacy, cast)
        suite.check("an old variation keeps its drawing and gets its description",
                    element["asset"] == fresh and element["shows"] == varied)

    # Through the build: the stage that reads plan.json re-derives it.
    import build as build_mod
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "p.json").write_text(json.dumps(
            {"name": "edit", "out": str(work / "out"),
             "script_text": "一。二。"}, ensure_ascii=False), encoding="utf-8")
        project = build_mod.Project(work / "p.json")
        house = project.cast
        plan = plan_mod.validate({"shots": [
            {"id": 1, "elements": [{"who": "sponge", "shows": "waving"}]},
            {"id": 2, "elements": [{"who": "krabs", "shows": "frowning"}]}]},
            ["一。", "二。"], house)
        plan["scenes"][0]["elements"][0]["shows"] = "waving both hands"
        (project.out / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        quiet, build_mod.log = build_mod.log, lambda *a, **k: None
        try:
            got = build_mod.stage_plan(project)
        finally:
            build_mod.log = quiet
        saved = json.loads((project.out / "plan.json").read_text(encoding="utf-8"))
        wanted = plan_mod._sprite_name("figure", ["sponge"], "waving both hands")
        suite.check("a reused plan.json is re-derived and saved",
                    got["scenes"][0]["elements"][0]["asset"] == wanted
                    and saved["scenes"][0]["elements"][0]["asset"] == wanted
                    and wanted in {d["asset"] for d in saved["drawings"]})


def freshness_checks(suite):
    """A cached render is reused only while it is the render of this board."""
    import build as build_mod

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "p.json").write_text(json.dumps(
            {"name": "fresh", "out": str(work / "out")}), encoding="utf-8")
        project = build_mod.Project(work / "p.json")
        plate(project.out / "background.png")
        sprite(project.out / "a.png", (230, 60, 60))
        board = {
            "video": {"orientation": "landscape", "width": 1920, "height": 1080,
                      "fps": 30, "background": "background.png",
                      "dissolve": 0.2, "crf": 30, "preset": "ultrafast"},
            "scenes": [{"id": 1, "duration": 0.6, "framing": "medium",
                        "subtitle": "一句。",
                        "elements": [{"asset": "a.png", "x": 0.3, "y": 0.97,
                                      "h": 0.4}]}]}
        lines = []
        quiet, build_mod.log = build_mod.log, lines.append
        try:
            build_mod.stage_render(project, board)
            first = (project.out / "video_mute.mp4").stat().st_mtime_ns
            lines.clear()
            build_mod.stage_render(project, board)
            reused = any("reusing" in line for line in lines)
            board["scenes"][0]["elements"][0]["x"] = 0.7
            lines.clear()
            build_mod.stage_render(project, board)
            redone = any("rendering it again" in line for line in lines)
            second = (project.out / "video_mute.mp4").stat().st_mtime_ns
        finally:
            build_mod.log = quiet
        suite.check("an unchanged storyboard reuses its render", reused)
        suite.check("a changed storyboard is rendered again",
                    redone and second != first,
                    "the old picture used to ship with the new draft")

        # A picture redrawn under the same name is a different render too.
        lines.clear()
        build_mod.log = lines.append
        try:
            sprite(project.out / "a.png", (60, 60, 230))
            build_mod.stage_render(project, board)
        finally:
            build_mod.log = quiet
        suite.check("a redrawn sprite is rendered again",
                    any("rendering it again" in line for line in lines))
