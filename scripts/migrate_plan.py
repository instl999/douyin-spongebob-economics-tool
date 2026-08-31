"""Re-run an existing plan.json through validation, without calling the model.

The validator gains rules over time - relative character heights, draw order,
grounding - and a plan written before a rule existed does not have it. This
replays the model's choices through the current validator so those rules apply,
which is far cheaper than paying for a fresh director call to get the same
shot list back.

    python scripts/migrate_plan.py out/my_video/plan.json casts/bikini_bottom.json

Then re-render with `build.py <project> --from storyboard`.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assets as assets_mod
import plan as plan_mod


def migrate(plan_path, cast_path):
    plan_path = Path(plan_path)
    original = json.loads(plan_path.read_text(encoding="utf-8"))
    cast = assets_mod.Cast.load(cast_path)

    beats = [scene["narration"] for scene in original["scenes"]]
    data = {
        "title": original.get("title"),
        "ending": original.get("ending"),
        "shots": [{"id": scene.get("id", i),
                   "framing": scene.get("framing", "medium"),
                   "elements": scene.get("elements", [])}
                  for i, scene in enumerate(original["scenes"], 1)],
    }
    updated = plan_mod.validate(data, beats, cast)
    plan_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return updated


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("cast")
    args = ap.parse_args()
    updated = migrate(args.plan, args.cast)
    print(f"{args.plan}: {len(updated['scenes'])} shots re-validated")
    for problem in updated["problems"]:
        print(f"  {problem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
