"""Ask a vision model whether each shot actually acts out its sentence.

    python scripts/critique.py out/<project>

Everything else in this pipeline checks properties it can compute: the plate
does not move, nothing overlaps, the draft matches the render, the audio is not
silent. None of that can tell you the thing that actually matters, which is
whether the picture performs the sentence. That is a judgement about meaning,
and the only tool here that can make it is the vision model.

So each shot is composed without its caption - the caption would give the
answer away - shown to the model, and the model is asked what the picture
depicts and whether someone watching with the sound off would use the same verb
as the narration. Shots that fail are listed with what the model saw instead,
which is usually enough to see what went wrong: the wrong pose, a prop parked
beside someone rather than being used, or a beat the director read wrong in the
first place.

This costs one vision call per shot, which is why it is a command rather than
part of every build.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

import ark
import render as render_mod
from layout import from_video as layout_from_video

# The model is judging composition and action, not reading fine print, and a
# 1920x1080 frame costs several times more to send for no gain in judgement.
REVIEW_WIDTH = 768

QUESTION = """这是一条科普视频里的一个画面，声音关掉了。

这一镜的旁白是：「{narration}」

先说画面里实际发生了什么，再判断它有没有把旁白演出来。判断标准只有一条：
一个看不到字幕、听不到声音的人，看这个画面，会不会说出旁白里那个动作。
人物只是站在相关的东西旁边，不算演出来。

只输出 JSON，不要别的：
{{"depicts": "画面里在发生什么，20字以内",
  "acts_out": true 或 false,
  "missing": "如果没演出来，缺的是什么动作，15字以内；演出来了就写空字符串"}}"""


def review_shot(scene, background, assets, lay, panel_color, workdir):
    """Compose one shot without its caption and ask what it shows."""
    plate = render_mod.compose_plate(scene, background, assets, lay, panel_color)
    image = Image.fromarray(plate)
    image = image.resize(
        (REVIEW_WIDTH, round(image.height * REVIEW_WIDTH / image.width)),
        Image.LANCZOS)
    path = workdir / f"_critique_{scene.get('id', 0):02d}.jpg"
    image.save(path, "JPEG", quality=88)
    try:
        raw = ark.read_image_text(
            path, QUESTION.format(narration=scene.get("subtitle", "")),
            max_tokens=300)
    finally:
        path.unlink(missing_ok=True)
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # A model that answers in prose is not a failed shot, and should not be
        # reported as one.
        return {"depicts": text[:60], "acts_out": None, "missing": ""}


def run(project, limit=None, verbose=False):
    project = Path(project).resolve()
    sb = json.loads((project / "storyboard.json").read_text(encoding="utf-8-sig"))
    video = sb.get("video", {})
    lay = layout_from_video(video)
    assets = render_mod.Assets(project)
    background = Image.open(
        project / video.get("background", "background.png")).convert("RGBA")
    if background.size != lay.size:
        background = background.resize(lay.size, Image.LANCZOS)
    panel_color = sb.get("panel_color")

    scenes = sb.get("scenes", [])[:limit] if limit else sb.get("scenes", [])
    weak, unclear = [], 0
    print(f"[{project.name}] asking what {len(scenes)} shots depict\n")
    for scene in scenes:
        try:
            verdict = review_shot(scene, background, assets, lay, panel_color,
                                  project)
        except Exception as exc:
            print(f"  shot {scene.get('id')}: could not be reviewed - "
                  f"{str(exc)[:90]}")
            continue
        acts = verdict.get("acts_out")
        mark = {True: "[ok]  ", False: "[WEAK]", None: "[??]  "}[
            acts if acts in (True, False) else None]
        if acts is None:
            unclear += 1
        elif not acts:
            weak.append((scene.get("id"), scene.get("subtitle", ""), verdict))
        line = f"  {mark} shot {scene.get('id'):2d}  {verdict.get('depicts', '')}"
        print(line)
        if verbose or acts is False:
            print(f"          旁白: {scene.get('subtitle', '')}")
            if verdict.get("missing"):
                print(f"          缺: {verdict['missing']}")

    print()
    scored = len(scenes) - unclear
    if scored:
        print(f"{scored - len(weak)}/{scored} shots act out their line"
              + (f", {unclear} unclear" if unclear else ""))
    for shot_id, narration, verdict in weak:
        print(f"  shot {shot_id}: {narration}")
        print(f"    shows {verdict.get('depicts', '')!r}, "
              f"missing {verdict.get('missing', '')!r}")
    if weak:
        print("\nTo fix: edit those shots' `elements` in plan.json - usually the "
              "pose is wrong, or the prop is beside someone rather than being "
              "used - then re-run `build.py <project> --from storyboard`.")
    return 1 if weak else 0


def main():
    ap = argparse.ArgumentParser(
        description="ask a vision model whether each shot acts out its sentence")
    ap.add_argument("project", help="a directory holding storyboard.json")
    ap.add_argument("--limit", type=int,
                    help="only review the first N shots (cheaper)")
    ap.add_argument("--verbose", action="store_true",
                    help="print the narration beside every shot, not just failures")
    args = ap.parse_args()
    return run(args.project, limit=args.limit, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
