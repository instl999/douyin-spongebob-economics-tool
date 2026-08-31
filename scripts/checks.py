"""Geometry checks on a storyboard, with auto-repair for the fixable ones.

These exist because the director places elements by coordinate without ever
seeing the picture, so it cannot know that a 900px-wide Krusty Krab at x=0.62
lands on top of Mr. Krabs at x=0.45. Sprite sizes are only known here, once the
PNGs are on disk, which makes this the first point where "do these two things
collide" is even answerable.

Overlaps are repaired rather than reported, because the fix is unambiguous and
the alternative is a picture with two characters merged into one blob.

Repair order matters and was got wrong twice. Scaling has to come before
placement, or a sprite too wide for the frame is placed and then still too
wide; and nudging a stray sprite back inside the frame has to come *before* the
standing row is laid out, or the nudge shoves a character back into the
neighbour the layout just moved it away from. The row layout runs last and is
authoritative. `inspect` then re-checks its own work and repeats up to
MAX_PASSES, so a residual can never reach the renderer.
"""
EDGE_TOLERANCE = 0.06     # how far a sprite may lean past the frame edge
MIN_GAP = 0.012           # closer than this reads as one mass, and is reported
# What the repair aims for. Deliberately wider than MIN_GAP: spacing to exactly
# the reporting threshold leaves the result half a pixel under it once the
# coordinates are rounded and the sprite is re-measured, so the repair would
# report the same overlap it just fixed, for ever.
REPAIR_GAP = MIN_GAP * 2.5
SIDE_MARGIN = 0.02
MAX_PASSES = 3


def _extent(el, assets, lay, framing):
    """Pixel bounding box of one element, or None if it cannot be sized."""
    if el.get("type", "sprite") != "sprite":
        return None
    try:
        # Must match render.build_element_image exactly, or the collision
        # geometry describes a picture nobody renders.
        img = assets.sized(
            el["asset"],
            lay.sprite_height(el.get("h", 0.4) * framing * el.get("rel", 1.0)))
    except (FileNotFoundError, KeyError):
        return None
    cx, cy = lay.point(el.get("x", 0.5), el.get("y", 0.97))
    anchor = el.get("anchor", "bottom")
    left = cx - img.width / 2
    top = cy - img.height if anchor == "bottom" else cy - img.height / 2
    return [left, top, left + img.width, top + img.height]


def _name(el):
    return el.get("asset") or el.get("type", "element")


def _repair_scene(scene, assets, lay, framing, W):
    """One repair pass over one shot. Returns what it changed."""
    findings = []
    elements = scene.get("elements", [])
    sprites = [(el, box) for el, box in
               ((e, _extent(e, assets, lay, framing)) for e in elements) if box]
    if not sprites:
        return findings

    # 1. Nothing may be wider than the frame. Placement cannot fix size.
    limit = W * (1.0 - 2 * SIDE_MARGIN)
    for el, box in sprites:
        width = box[2] - box[0]
        if width <= limit:
            continue
        shrink = limit / width
        el["h"] = round(el.get("h", 0.4) * shrink, 4)
        centre = (box[0] + box[2]) / 2
        width *= shrink
        box[0], box[2] = centre - width / 2, centre + width / 2
        findings.append(f"shot {scene.get('id','?')}: {_name(el)} was wider than "
                        f"the frame, scaled to {shrink:.2f}")

    standing = sorted((pair for pair in sprites
                       if pair[0].get("anchor", "bottom") == "bottom"),
                      key=lambda pair: pair[1][0])
    floating = [pair for pair in sprites if pair not in standing]

    # 2. Nudge the free-floating ones inside, before the row is committed.
    margin = W * SIDE_MARGIN
    for el, box in floating:
        shift = (margin - box[0]) if box[0] < margin else (
            (W - margin) - box[2] if box[2] > W - margin else 0.0)
        if shift:
            el["x"] = round(el.get("x", 0.5) + shift / W, 4)
            box[0] += shift
            box[2] += shift
            findings.append(f"shot {scene.get('id','?')}: {_name(el)} moved back "
                            "inside the frame")

    # 3. The standing row, last and authoritative.
    if len(standing) >= 1:
        gaps = REPAIR_GAP * W * max(0, len(standing) - 1)
        needed = sum(box[2] - box[0] for _, box in standing) + gaps
        available = W * (1.0 - 2 * SIDE_MARGIN)
        overlapping = any(
            standing[i + 1][1][0] - standing[i][1][2] < MIN_GAP * W
            for i in range(len(standing) - 1))
        off_frame = any(box[0] < -EDGE_TOLERANCE * W
                        or box[2] > W + EDGE_TOLERANCE * W
                        for _, box in standing)
        # Only intervene when something is wrong. The director's placement
        # carries intent - a character at 0.30 with the prop they are using at
        # 0.64 is a deliberate pairing - and redistributing a row that already
        # works would throw that away.
        if overlapping or off_frame:
            if needed > available:
                shrink = available / needed
                for el, box in standing:
                    el["h"] = round(el.get("h", 0.4) * shrink, 4)
                    width = (box[2] - box[0]) * shrink
                    centre = (box[0] + box[2]) / 2
                    box[0], box[2] = centre - width / 2, centre + width / 2
                findings.append(
                    f"shot {scene.get('id','?')}: the row needed "
                    f"{needed / W:.2f} frame widths, scaled to {shrink:.2f}")
            widths = [box[2] - box[0] for _, box in standing]
            slack = max(0.0, available - sum(widths) - gaps)
            cursor = margin + slack / 2
            for (el, box), width in zip(standing, widths):
                el["x"] = round((cursor + width / 2) / W, 4)
                box[0], box[2] = cursor, cursor + width
                cursor += width + REPAIR_GAP * W
            findings.append(
                f"shot {scene.get('id','?')}: "
                f"{'overlapping' if overlapping else 'off-frame'} row of "
                f"{len(standing)} re-spaced")
    return findings


def _report_scene(scene, assets, lay, framing, W, H, caption_top):
    """What is still wrong with one shot, changing nothing."""
    findings = []
    sid = scene.get("id", "?")
    elements = scene.get("elements", [])
    sprites = [(el, box) for el, box in
               ((e, _extent(e, assets, lay, framing)) for e in elements) if box]

    if not [e for e in elements if "asset" in e]:
        findings.append(f"shot {sid}: no sprites at all - the frame is empty")

    standing = sorted((p for p in sprites
                       if p[0].get("anchor", "bottom") == "bottom"),
                      key=lambda p: p[1][0])
    for i in range(len(standing) - 1):
        (a, abox), (b, bbox) = standing[i], standing[i + 1]
        if bbox[0] - abox[2] < MIN_GAP * W:
            findings.append(f"shot {sid}: {_name(a)} and {_name(b)} overlap")

    for el, box in sprites:
        if box[0] < -EDGE_TOLERANCE * W or box[2] > W + EDGE_TOLERANCE * W:
            findings.append(f"shot {sid}: {_name(el)} runs off the side "
                            f"(x {box[0]:.0f}..{box[2]:.0f} of {W})")
        if box[1] < -EDGE_TOLERANCE * H:
            findings.append(f"shot {sid}: {_name(el)} is cut off at the top")
        if el.get("anchor", "bottom") != "bottom" and box[3] > caption_top:
            findings.append(f"shot {sid}: {_name(el)} hangs into the caption")

    for el in elements:
        if el.get("type") not in ("label", "bubble"):
            continue
        _, y = lay.point(el.get("x", 0.5), el.get("y", 0.35))
        if y > caption_top:
            findings.append(f"shot {sid}: the {el['type']} "
                            f"{el.get('text','')!r} sits in the caption band")
    return findings


def inspect(storyboard, assets, lay, repair=True):
    """Walk every shot. Returns a list of human-readable findings.

    With repair on, this loops until the shots stop changing, so what it
    returns is a description of a picture that is actually clean rather than
    one repair's worth of good intentions.
    """
    import render as render_mod

    W, H = lay.size
    caption_top = lay.subtitle_center_y - lay.subtitle_font_px() * 1.1
    findings = []

    for scene in storyboard.get("scenes", []):
        framing = render_mod.FRAMING.get(scene.get("framing", "medium"), 1.0)
        if repair:
            for _ in range(MAX_PASSES):
                changed = _repair_scene(scene, assets, lay, framing, W)
                findings.extend(changed)
                if not changed:
                    break
        findings.extend(_report_scene(scene, assets, lay, framing, W, H,
                                      caption_top))
    return findings


def summarise(findings, log=print):
    if not findings:
        log("  layout: nothing to flag")
        return 0
    for line in findings:
        log(f"  {line}")
    return len(findings)
