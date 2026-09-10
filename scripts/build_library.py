"""Draw the one reference image per character that a style needs.

    python scripts/build_library.py casts/bikini_bottom.json

The argument is a style key from casts/styles.json or a path to a cast file;
with no argument it builds the registry's default style.

Every picture in a video is drawn from the script, for that video, and thrown
away after. The one exception is a character's reference image: identity has to
come from somewhere, and generated from a description alone the same character
drifts between shots of a single video. This draws those, one per character,
and a build will draw any that are missing anyway - so run it up front only to
get the stall out of the way, or after editing a character's `look`.

Safe to re-run. A character that already has a reference is skipped.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assets as assets_mod
import styles as styles_mod

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cast", nargs="?", default=None,
                    help="style key or cast file path (default: the registry's "
                         "default style)")
    ap.add_argument("--force", action="store_true",
                    help="redraw references that already exist")
    ap.add_argument("--workers", type=int, default=4,
                    help="how many images to generate at once (default 4)")
    ap.add_argument("--plates", action="store_true",
                    help="also generate the background plate for both orientations")
    args = ap.parse_args()

    try:
        _, cast_path = styles_mod.resolve(args.cast)
    except ValueError as exc:
        print(str(exc))
        return 2
    cast = assets_mod.Cast.load(cast_path, root=ROOT / "casts")

    problems = cast.problems()
    if problems:
        print(f"{cast_path.name} has problems; fix these first:")
        for line in problems:
            print(f"  - {line}")
        return 1

    library = assets_mod.Library(cast)
    characters = list(cast.data.get("characters") or {})
    have = [n for n in characters if cast.anchor_path(n).exists()]
    print(f"{cast.name}: {len(characters)} character(s), "
          f"{len(have)} already have a reference drawing\n")

    started = time.time()
    report = {"built": [], "failed": [], "skipped": []}
    for name in characters:
        try:
            _, made = library.build_anchor(name, assets_mod.SPRITE_SIZE,
                                           force=args.force)
        except Exception as exc:
            report["failed"].append((name, f"{type(exc).__name__}: {exc}"))
            continue
        (report["built"] if made else report["skipped"]).append(name)
        print(f"  {name:14} {'drawn' if made else 'already here'}")

    if args.plates:
        for size in ("2560x1440", "1440x2560"):
            _, made = library.build_background(
                cast.dir / f"background_{size}.png", size, force=args.force)
            print(f"  background {size}  {'generated' if made else 'cached'}")

    print(f"\ngenerated {len(report['built'])}, reused {len(report['skipped'])}, "
          f"failed {len(report['failed'])}  in {time.time() - started:.0f}s")
    for name, error in report["failed"]:
        print(f"  ! {name}: {error}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
