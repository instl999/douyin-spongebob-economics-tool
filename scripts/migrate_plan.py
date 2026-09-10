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
import styles as styles_mod


def migrate(plan_path, cast_path):
    plan_path = Path(plan_path)
    original = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    _, cast_file = styles_mod.resolve(cast_path)
    cast = assets_mod.Cast.load(cast_file)

    stale = [scene.get("id", i) for i, scene in enumerate(original["scenes"], 1)
             if any("asset" in el and not el.get("shows")
                    for el in scene.get("elements") or [])]
    if stale:
        raise SystemExit(
            f"{plan_path} was written when sprites were picked from a shared "
            f"library.\n"
            f"Its elements name drawings instead of describing them "
            f"(shots {stale[:5]}).\n"
            "There is nothing to replay them through: what each picture "
            "should show was never written down.\n"
            "Re-plan it instead, with\n"
            "    python scripts/build.py <project> --from plan")

    beats = [scene["narration"] for scene in original["scenes"]]
    # The whole plan goes back in, not a copy of the three fields this script
    # happened to know about. That allowlist silently dropped every field the
    # validator learned to read afterwards: each shot's `beat` - which is what
    # the sound design picks cues from - and later the video's `setting`, so a
    # migrated video lost its backdrop and nothing said so. `validate` already
    # accepts "scenes" as an alias for "shots" and fills in its own defaults.
    updated = plan_mod.validate(original, beats, cast)
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
