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

# Two calls, deliberately. Asking one question - "here is the line, does the
# picture act it out?" - produced a judge that said no to everything. Shown a
# frame of Mr. Krabs handing SpongeBob an envelope, it answered False to "Mr.
# Krabs hands the pay envelope to SpongeBob"; asked about the same frame with
# the line "SpongeBob is sleeping", it described the picture, unprompted, as
# "SpongeBob and Mr. Krabs standing, handing over an envelope". It could see
# the action perfectly well. Being told what to look for was what broke it:
# the criterion primed it to reject, and it rejected.
#
# So the eye and the judgement are split. The vision call never sees the
# narration, which also stops the narration talking it into seeing things.
DESCRIBE = """这是一条动画视频里的一个画面。用一句话说清楚画面里在发生什么：
谁在做什么动作，手里有什么，表情如何。20-30字，只描述看到的，不要评价。"""

JUDGE = """一条科普视频里，某一镜的旁白是：「{narration}」

这一镜的画面内容是：「{depicts}」

判断这个画面有没有把旁白的意思演出来。标准：一个看不到字幕、听不到声音的人，
看这个画面，能不能看懂旁白在说的那件事。

宽严要把握好：
- 画面是静止的，不要因为「动作没有完成」「看不出正在进行」就判否
- 人物只是站在相关的东西旁边、没有任何动作，判否
- 抽象的道理（比如「这就是两者的区别」）用图表、标签、表情来表达，算演出来了

只输出 JSON：
{{"acts_out": true 或 false,
  "missing": "如果没演出来，缺的是什么，15字以内；演出来了就写空字符串"}}"""


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
        depicts = ark.read_image_text(path, DESCRIBE, max_tokens=200).strip()
    finally:
        path.unlink(missing_ok=True)

    raw = ark.chat([{"role": "user", "content": JUDGE.format(
        narration=scene.get("subtitle", ""), depicts=depicts)}],
        temperature=0.0, max_tokens=200).strip()
    text = raw.removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError:
        # A judge that answers in prose is not a failed shot.
        return {"depicts": depicts, "acts_out": None, "missing": ""}
    verdict["depicts"] = depicts
    return verdict


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
        print("\nThis judge is deliberately strict and will reject shots that "
              "read fine to a person. Use the descriptions above to decide "
              "which of these are actually wrong.")
        print("To fix one: edit its `elements` in plan.json - usually the pose "
              "is wrong, or the prop is beside someone rather than being used "
              "- then re-run `build.py <project> --from storyboard`.")
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
