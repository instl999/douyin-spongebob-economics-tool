"""Turn confirmed settings into a project file, and say what they will cost.

    python scripts/new_project.py --name sunk_cost --title "什么是沉没成本" \
        --script examples/sunk_cost.txt --orientation landscape --target 90

This exists so the settings conversation happens once, up front, and produces
something checkable. It writes nothing to the network: the duration it reports
is predicted from the script, and the image count is the worst case from the
cast's own library, so an operator can put real numbers in front of whoever
asked for the video before a single call is made.

It refuses to write a project whose cast is broken, because the alternative is
discovering that ten minutes into generation.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assets as assets_mod
import timing

ROOT = Path(__file__).resolve().parent.parent

VOICES = {
    "male": "zh_male_yuanboxiaoshu_uranus_bigtts",
    "female": "zh_female_gaolengyujie_uranus_bigtts",
    "female-brisk": "zh_female_shuangkuaisisi_uranus_bigtts",
}


def resolve(path):
    p = Path(path)
    return p if p.is_absolute() or p.exists() else ROOT / path


def build(args):
    cast_path = resolve(args.cast)
    if not cast_path.exists():
        raise SystemExit(f"no cast at {cast_path}")
    cast = assets_mod.Cast.load(cast_path, root=ROOT / "casts")
    problems = cast.problems()
    if problems:
        print(f"the cast {cast_path.name} has problems that would break the run:")
        for line in problems:
            print(f"  - {line}")
        raise SystemExit(1)

    script_path = resolve(args.script)
    if not script_path.exists():
        raise SystemExit(f"no script at {script_path}")
    script = script_path.read_text(encoding="utf-8")
    if not script.strip():
        raise SystemExit(f"{script_path} is empty")

    voice = VOICES.get(args.voice, args.voice)
    project = {
        "name": args.name,
        "title": args.title,
        "script": str(script_path.relative_to(ROOT)).replace("\\", "/")
                  if ROOT in script_path.parents else str(script_path),
        "cast": str(cast_path.relative_to(ROOT)).replace("\\", "/")
                if ROOT in cast_path.parents else str(cast_path),
        "orientation": args.orientation,
        "shot_seconds": args.shot_seconds,
        "voice": {"speaker": voice, "speed": 1.0},
    }
    if args.target:
        project["target_seconds"] = args.target

    fit = timing.fit_to_target(
        script, args.target, shot_seconds=args.shot_seconds,
        title_seconds=2.6, ending_seconds=4.0)

    out = ROOT / "projects" / f"{args.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(project, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    # Worst-case image count: everything this cast can make that is not built.
    catalogue = set(cast.catalogue())
    built = {p.name for p in cast.sprites.glob("*.png")} if cast.sprites.exists() else set()
    to_build = len(catalogue - built)
    plate = cast.dir / f"background_{'1440x2560' if args.orientation == 'portrait' else '2560x1440'}.png"

    print(f"wrote {out.relative_to(ROOT)}\n")
    print("settings")
    print(f"  title       {args.title}")
    print(f"  style       {cast.name}  ({len(cast.data.get('characters') or {})} characters, "
          f"{len(cast.data.get('props') or {})} props)")
    print(f"  orientation {args.orientation}"
          f"  {'1080x1920' if args.orientation == 'portrait' else '1920x1080'}")
    print(f"  voice       {voice}")
    print()
    print("predicted result")
    print(timing.describe(fit))
    print()
    print("what this will generate")
    print(f"  sprites     up to {to_build} new image(s); "
          f"{len(built)} already in the library")
    print(f"  background  {'reuse the cached plate' if plate.exists() else 'one new plate'}")
    print(f"  narration   {fit['estimate']['shots']} clips")
    print()
    if not fit["ok"]:
        print("BEFORE BUILDING: the requested length is not reachable. Either")
        print("change the script or accept the predicted length above.\n")
    print("next")
    print(f"  python scripts/build.py projects/{args.name}.json --preview")
    return 0 if fit["ok"] else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="short slug; names the output folder")
    ap.add_argument("--title", required=True, help="the on-screen opening title")
    ap.add_argument("--script", required=True, help="path to the narration txt")
    ap.add_argument("--cast", default="casts/bikini_bottom.json")
    ap.add_argument("--orientation", default="landscape",
                    choices=["landscape", "portrait"])
    ap.add_argument("--target", type=float, default=None,
                    help="wanted length in seconds; the speech rate is fitted to it")
    ap.add_argument("--voice", default="male",
                    help="male / female / female-brisk, or a full speaker id")
    ap.add_argument("--shot-seconds", type=float, default=5.0,
                    help="how much narration one shot carries")
    return build(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
