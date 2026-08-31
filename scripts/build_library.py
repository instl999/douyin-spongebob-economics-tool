"""Generate every sprite a cast can produce, once.

    python scripts/build_library.py casts/bikini_bottom.json

A normal build only makes the sprites the storyboard actually asked for, which
is right for one video and wrong for a new cast: the first few videos then pay
a generation stall each, and the director is choosing from whatever happens to
exist rather than from the whole catalogue. Building the library up front makes
every later video pure compositing.

Safe to re-run. Anything whose prompt has not changed is skipped.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assets as assets_mod

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cast", nargs="?", default="casts/bikini_bottom.json")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even sprites whose prompt is unchanged")
    ap.add_argument("--workers", type=int, default=4,
                    help="how many images to generate at once (default 4)")
    ap.add_argument("--plates", action="store_true",
                    help="also generate the background plate for both orientations")
    args = ap.parse_args()

    cast_path = Path(args.cast)
    if not cast_path.is_absolute() and not cast_path.exists():
        cast_path = ROOT / args.cast
    cast = assets_mod.Cast.load(cast_path, root=ROOT / "casts")

    problems = cast.problems()
    if problems:
        print(f"{cast_path.name} has problems; fix these first:")
        for line in problems:
            print(f"  - {line}")
        return 1

    library = assets_mod.Library(cast)
    catalogue = cast.catalogue()
    built = {p.name for p in cast.sprites.glob("*.png")}
    print(f"{cast.name}: {len(catalogue)} sprites in the catalogue, "
          f"{len(built)} already on disk\n")

    started = time.time()
    report = library.build_all(assets_mod.SPRITE_SIZE, force=args.force,
                               workers=max(1, args.workers))

    if args.plates:
        for size in ("2560x1440", "1440x2560"):
            _, made = library.build_background(
                cast.dir / f"background_{size}.png", size, force=args.force)
            print(f"  background {size}  {'generated' if made else 'cached'}")

    print(f"\ngenerated {len(report['built'])}, reused {len(report['cached'])}, "
          f"failed {len(report['failed'])}  in {time.time() - started:.0f}s")
    for name, error in report["failed"]:
        print(f"  ! {name}: {error}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
