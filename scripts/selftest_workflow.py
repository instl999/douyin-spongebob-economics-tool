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
