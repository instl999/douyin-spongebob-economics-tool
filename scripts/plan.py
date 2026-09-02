"""The director: narration script in, storyboard out.

**The model never writes narration.** The script is split into shots by
`split_script`, deterministically, and the model is asked only to choose which
sprites appear in each already-written shot. This is not a stylistic
preference. Given the text and asked merely to group it, a director quietly
rewrites instead: a 39-character script came back as 112 characters across
eight shots, including an invented beat about a character running an experiment
that appeared nowhere in the source. The narration is the user's product, so
the model is not given the chance to touch it.

What the model does choose - sprites, positions, framing, labels - is validated
against the cast rather than trusted. Unknown sprite names snap to another pose
of the same character or are dropped, coordinates are clamped, solid objects
are put back on the ground, and a character cannot appear twice in one shot. A
director that hallucinates should cost one element, not the run.
"""
from pathlib import Path

import ark
import styles as styles_mod

CHARS_PER_SECOND = 5.2
SENTENCE_END = "。！？!?；;"
# A closing card longer than this stops being a punchline.
ENDING_MAX_CHARS = 24
# The tone names the director may use, taken from the same config the renderer
# colours them from. Adding a tone to casts/styles.json makes it usable here
# too, rather than being silently rewritten to "neutral" by the validator.
LABEL_TONES = tuple(styles_mod.look()["label_tones"])

# How many poses one video may add to its cast. Every one is an image that gets
# generated and paid for, so this is a spending limit as much as a style rule -
# but it is also what keeps the catalogue a catalogue. Left uncapped a director
# asks for a bespoke pose per shot, the library stops being reusable, and the
# next video pays all over again. Past the cap, requests fall back to the
# nearest existing pose exactly as an unknown sprite name always has.
NEW_POSE_BUDGET = 8

# Two-figure sprites are drawn for one beat and are far less reusable than a
# pose, so they get their own, smaller allowance. They exist because a handover
# assembled from two separate cut-outs never actually connects - which makes
# them the only way some sentences can be shown at all, and also the reason not
# to reach for one when a pose would do.
NEW_INTERACTION_BUDGET = 4

SYSTEM = """You are the director of a SpongeBob-style animated explainer.

The picture is built by compositing: one fixed background plate, with cut-out
character and prop sprites placed on top of it. For each shot you choose which
sprites appear and where.

You never write or change narration - it is fixed and given to you. You never
invent a sprite; only the listed filenames exist.

Return JSON only, no commentary."""

# The director's brief lives in references/director-brief.md rather than here.
# It is 150 lines of prose that a person edits and reasons about far more often
# than the code around it, and the same argument that moved the look settings
# into casts/styles.json applies to it: tuning what the director is told should
# not mean editing Python. `{...}` placeholders are filled by build_prompt.
BRIEF_PATH = Path(__file__).resolve().parent.parent / "references" / "director-brief.md"


def brief_template():
    """The director's brief, read fresh so an edit takes effect immediately."""
    try:
        return BRIEF_PATH.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(
            f"the director's brief is missing or unreadable ({exc}). It should "
            f"be at {BRIEF_PATH}") from exc



# --- splitting -------------------------------------------------------------

SOFT_BREAK = "，、,；;：: "


def _break_up(sentence, limit):
    """Split an over-long sentence at the latest soft break that fits.

    A script written without full stops - or with only commas - otherwise
    becomes one enormous shot: 200 unpunctuated characters measured as a single
    33-second hold whose caption wrapped to eight lines and covered 664 of the
    frame's 1080 pixels, burying the characters underneath it. Falling back to
    commas, and then to a hard cut, keeps a bad script merely plain rather than
    broken.
    """
    parts, rest = [], sentence
    while len(rest) > limit:
        window = rest[:limit + 1]
        cut = max((window.rfind(ch) for ch in SOFT_BREAK), default=-1)
        if cut < limit * 0.4:          # no usable break: cut cleanly
            cut = limit
        else:
            cut += 1                   # keep the punctuation on this line
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return [p for p in parts if p]


def split_script(script, shot_seconds=5.0):
    """Sentences, then packed toward the target shot length.

    Packing matters as much as splitting: at ~5.2 spoken Chinese characters a
    second a 5s shot wants about 26 characters, and a script of short lines
    would otherwise cut every two seconds whatever the setting said.
    """
    sentences, buf = [], ""
    for ch in script:
        if ch == "\n":
            if buf.strip():
                sentences.append(buf.strip())
            buf = ""
            continue
        buf += ch
        if ch in SENTENCE_END:
            if buf.strip():
                sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    target = shot_seconds * CHARS_PER_SECOND
    limit = int(target * 1.45)

    # No sentence may exceed one shot's worth on its own.
    expanded = []
    for sentence in sentences:
        expanded.extend(_break_up(sentence, limit) if len(sentence) > limit
                        else [sentence])

    packed, current = [], ""
    for sentence in expanded:
        if current and len(current) + len(sentence) > limit:
            packed.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        packed.append(current)
    return packed


def _casting_notes(cast):
    lines = []
    for name, char in (cast.data.get("characters") or {}).items():
        role = char.get("role")
        if role:
            lines.append(f"- {name}: {role}")
    return "\n".join(lines) or (
        "- krabs: the boss / capital\n"
        "- sponge: the worker who does the task\n"
        "- patrick: asks the naive question\n"
        "- squid: the cynic\n"
        "- sandy: the analyst who explains")


# Portrait is 1080 wide against landscape's 1920, so a row of four that fits
# one shape does not fit the other. Asking for fewer elements up front beats
# letting the layout repair shrink everyone to fit: a four-sprite row in
# portrait comes back scaled to about half size, which reads as a long shot
# nobody asked for.
ORIENTATION_NOTES = {
    "landscape": "The frame is wide (16:9). Three characters fit side by side.",
    "portrait": ("The frame is TALL and NARROW (9:16). Only two things fit side "
                 "by side. Never put three or more sprites in one shot - use "
                 "two, or one character with one prop, and let the label or the "
                 "balloon carry the rest."),
}


def _catalogue_text(cast):
    """The sprite list, with what each one actually shows.

    Grouped by character so that the choice reads as "which of these bodies is
    doing the thing", which is the question, rather than as a flat list of
    filenames to pattern-match against.
    """
    brief = cast.brief()
    lines = []
    for name, (role, poses) in brief["characters"].items():
        lines.append(f"## {name}" + (f" - {role}" if role else ""))
        for filename, description in poses.items():
            lines.append(f"- {filename} - {description}")
        lines.append("")
    if brief["props"]:
        lines.append("## props")
        for filename, description in brief["props"].items():
            lines.append(f"- {filename} - {description}")
    if brief.get("interactions"):
        lines.append("")
        lines.append("## two-figure sprites already drawn (reuse these)")
        for filename, description in brief["interactions"].items():
            lines.append(f"- {filename} - {description}")
    return chr(10).join(lines)


def build_prompt(beats, cast, orientation="landscape",
                 pose_budget=NEW_POSE_BUDGET):
    catalogue = _catalogue_text(cast)
    listing = chr(10).join(f"{i}. {text}" for i, text in enumerate(beats, 1))
    portrait = orientation == "portrait"
    return brief_template().format(
        count=len(beats), beats=listing, catalogue=catalogue,
        casting=_casting_notes(cast),
        pose_budget=pose_budget,
        duo_budget=NEW_INTERACTION_BUDGET,
        lo_elements=2, hi_elements=3 if portrait else 4,
        orientation_note=ORIENTATION_NOTES.get(
            orientation, ORIENTATION_NOTES["landscape"]))


def direct(script, cast, shot_seconds=5.0, model=None, temperature=0.6,
           orientation="landscape"):
    """Split the script here; ask the model only to dress each shot."""
    beats = split_script(script, shot_seconds)
    if not beats:
        raise ValueError("the script has no sentences in it")
    data = ark.chat_json(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": build_prompt(beats, cast, orientation)}],
        model=model, temperature=temperature, max_tokens=16000)
    plan = validate(data, beats, cast,
                    max_sprites=2 if orientation == "portrait" else None)
    commit_poses(cast, plan)
    return plan


# --- validation ------------------------------------------------------------

def _clamp(value, lo, hi, default):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def nearest(asset, known):
    """Map a plausible-but-missing sprite onto one the cast really has.

    Directors reach for poses that read well in the sentence - patrick_happy,
    krabs_happy - whether or not they exist. Falling back to another pose of the
    same character keeps the character in the shot, which matters far more than
    which expression they wear. A name sharing no subject with the cast is
    dropped.
    """
    stem = asset[:-4] if asset.endswith(".png") else asset
    subject = stem.split("_", 1)[0]
    if not subject:
        return None
    siblings = [k for k in known if k.startswith(subject + "_")]
    if not siblings:
        return None
    for preferred in ("stand", "explain", "point"):
        if f"{subject}_{preferred}.png" in siblings:
            return f"{subject}_{preferred}.png"
    return sorted(siblings)[0]


POSE_NAME = __import__("re").compile(r"^[a-z][a-z0-9_]{1,23}$")


def _pose_request(el, cast, known, shot_id, problems):
    """Turn a director's `new_pose` into a pose this cast can draw, or None.

    Everything about the request is checked against the cast, because the one
    thing being handed to an image model here is a sentence the director wrote.
    A malformed name would produce a sprite nothing can address; a pose that
    already exists would quietly redraw it; a description naming a second
    character would put that character on screen twice.
    """
    asset = el.get("asset") or ""
    description = (el.get("new_pose") or "").strip()
    if not description:
        return None
    stem = asset[:-4] if asset.endswith(".png") else asset
    character, _, pose = stem.partition("_")
    characters = cast.data.get("characters") or {}
    if character not in characters:
        problems.append(
            f"shot {shot_id}: new pose {asset!r} is not <character>_<pose> for "
            f"anyone in this cast, ignored")
        return None
    if not POSE_NAME.match(pose):
        problems.append(f"shot {shot_id}: {pose!r} is not a usable pose name, ignored")
        return None
    if asset in known:
        return None                       # already drawable; nothing to add
    if len(description) > 200:
        description = description[:200].rsplit(",", 1)[0]
    others = [n for n in characters if n != character and n in description.lower()]
    if others:
        problems.append(
            f"shot {shot_id}: new pose {asset!r} described {others[0]} too; "
            "a sprite holds one figure, ignored")
        return None
    return character, pose, description


def _interaction_request(el, cast, known, shot_id, problems):
    """Turn a director's `new_interaction` into a two-figure sprite, or None."""
    asset = el.get("asset") or ""
    description = (el.get("new_interaction") or "").strip()
    if not description:
        return None
    stem = asset[:-4] if asset.endswith(".png") else asset
    if not stem.startswith("duo_"):
        problems.append(
            f"shot {shot_id}: an interaction must be named "
            f"duo_<a>_<b>_<action>.png, not {asset!r}, ignored")
        return None
    members = cast.duo_members(asset)
    if len(members) != 2 or members[0] == members[1]:
        problems.append(
            f"shot {shot_id}: {asset!r} does not name two different characters "
            "in this cast, ignored")
        return None
    action = stem[len("duo_" + "_".join(members)) + 1:]
    if not POSE_NAME.match(action):
        problems.append(f"shot {shot_id}: {action!r} is not a usable "
                        "interaction name, ignored")
        return None
    if asset in known:
        return None
    if len(description) > 240:
        description = description[:240].rsplit(",", 1)[0]
    return members, action, description


def _elements(raw_elements, cast, known, shot_id, problems,
              requests=None, budget=0, duos=None, duo_budget=0):
    elements, seen = [], set()
    for el in raw_elements or []:
        kind = el.get("type", "sprite")
        if kind == "panel":
            item = {"type": "panel",
                    "x": _clamp(el.get("x"), 0.0, 1.0, 0.5),
                    "y": _clamp(el.get("y"), 0.0, 1.05, 0.9),
                    "w": _clamp(el.get("w"), 0.08, 1.0, 0.4),
                    "ph": _clamp(el.get("ph"), 0.04, 1.0, 0.28),
                    "anchor": el.get("anchor", "bottom")}
            for key in ("color", "alpha", "radius"):
                if key in el:
                    item[key] = el[key]
        elif kind in ("label", "bubble"):
            text = (el.get("text") or "").strip()
            if not text:
                continue
            item = {"type": kind, "text": text,
                    "x": _clamp(el.get("x"), 0.03, 0.97, 0.5),
                    "y": _clamp(el.get("y"), 0.05, 1.0, 0.35),
                    "anchor": el.get("anchor", "center")}
            if kind == "label":
                tone = el.get("tone", "neutral")
                item["tone"] = tone if tone in LABEL_TONES else "neutral"
            if kind == "bubble":
                item["tail"] = el.get("tail", "left")
        else:
            asset = el.get("asset") or el.get("sprite") or ""
            if asset not in known:
                # A name the catalogue does not have is usually a
                # hallucination, and snaps to a near pose. Two things make it a
                # request instead: the director saying what the body should be
                # doing, or naming two characters and what passes between them.
                # Both are the case this format is otherwise bad at.
                pair = (_interaction_request(el, cast, known, shot_id, problems)
                        if duos is not None and duo_budget > 0 else None)
                asked = (_pose_request(el, cast, known, shot_id, problems)
                         if pair is None and requests is not None and budget > 0
                         else None)
                if pair:
                    members, action, description = pair
                    asset = f"duo_{'_'.join(members)}_{action}.png"
                    known.add(asset)
                    duo_budget -= 1
                    duos.append({"asset": asset, "members": members,
                                 "action": action, "description": description,
                                 "shot": shot_id})
                    problems.append(f"shot {shot_id}: new interaction "
                                    f"{asset!r} - {description}")
                elif asked:
                    character, pose, description = asked
                    asset = f"{character}_{pose}.png"
                    known.add(asset)
                    budget -= 1
                    requests.append({"asset": asset, "character": character,
                                     "pose": pose, "description": description,
                                     "shot": shot_id})
                    problems.append(
                        f"shot {shot_id}: new pose {asset!r} - {description}")
                else:
                    swap = nearest(asset, known)
                    if not swap:
                        problems.append(
                            f"shot {shot_id}: unknown sprite {asset!r}, dropped")
                        continue
                    problems.append(f"shot {shot_id}: {asset!r} -> {swap!r}")
                    asset = swap

            anchor = el.get("anchor", "bottom")
            y = _clamp(el.get("y"), 0.05, 1.02, 0.97)
            # Whether a sprite may float is a property of the object, not of the
            # anchor the director happened to pick. Left to its own judgement a
            # director will hang a pile of gold coins at eye level like a wall
            # chart, so the cast says which props hang and everything else is
            # put on the ground whatever it asked for.
            if cast.hangs(asset):
                anchor = "center"
                y = max(0.22, min(0.62, y))
            elif anchor != "bottom" or y < 0.88:
                if y < 0.88:
                    problems.append(
                        f"shot {shot_id}: {asset!r} was floating at y={y:.2f}, "
                        "moved to the ground")
                anchor, y = "bottom", 0.97

            # Two poses of one character in a shot is always a mistake. Two
            # different props is normal, so props key on their whole name
            # rather than on the shared "prop_" prefix.
            stem = asset.rsplit(".", 1)[0]
            # An interaction sprite contains both its characters, so it claims
            # both names: without that, a shot could hold the drawn-together
            # pair *and* a separate sprite of one of them, and that character
            # would be on screen twice.
            members = cast.duo_members(asset)
            subjects = (set(members) if members
                        else {stem if stem.startswith("prop_")
                              else stem.split("_", 1)[0]})
            clash = subjects & seen
            if clash:
                problems.append(f"shot {shot_id}: {asset!r} repeats "
                                f"{sorted(clash)[0]}, dropped")
                continue
            seen |= subjects

            item = {"asset": asset,
                    "flip": bool(el.get("flip")),
                    "x": _clamp(el.get("x"), 0.03, 0.97, 0.5),
                    "y": y,
                    "h": _clamp(el.get("h"), 0.10, 0.85, 0.46),
                    "rel": cast.relative_height(asset),
                    "anchor": anchor}
        appear = el.get("appear")
        if appear:
            item["appear"] = _clamp(appear, 0.0, 30.0, 0.0)
        if el.get("z") is not None:
            item["z"] = _clamp(el.get("z"), -5.0, 5.0, 1.0)
        elements.append(item)

    # Elements composite in list order, so draw order is depth. Furniture the
    # cast marks as foreground goes last, over the legs of whoever is standing
    # at it; hanging boards go first, behind everyone. A stable sort keeps the
    # director's order within each band.
    def depth(item):
        # An explicit z wins, so an author can put a character between a
        # counter and a stove - something the automatic bands cannot express,
        # since they only know "furniture" and "not furniture".
        if item.get("z") is not None:
            return float(item["z"])
        if item.get("type") == "panel":
            return -1.0          # walls and floors sit behind everything
        asset = item.get("asset")
        if not asset:
            return 1.0
        if cast.hangs(asset):
            return 0.0
        return 2.0 if cast.in_front(asset) else 1.0

    return sorted(elements, key=depth)


def validate(data, beats, cast, max_sprites=None):
    """Attach the model's choices to the beats. Narration comes from `beats`."""
    known = set(cast.catalogue())
    problems = []

    by_id = {}
    for raw in data.get("shots") or data.get("scenes") or []:
        try:
            by_id[int(raw.get("id"))] = raw
        except (TypeError, ValueError):
            continue

    scenes, recent, requests, duos = [], [], [], []
    for i, narration in enumerate(beats, 1):
        raw = by_id.get(i, {})
        if not raw:
            problems.append(f"shot {i}: the director skipped it, left bare")

        framing = raw.get("framing", "medium")
        if framing not in ("wide", "medium", "close"):
            framing = "medium"
        # Break a run of three identical framings, which reads as a slideshow.
        if recent[-2:] == [framing, framing]:
            problems.append(f"shot {i}: framing varied to break a run of {framing}")
            framing = "close" if framing != "close" else "medium"
        recent.append(framing)

        elements = _elements(raw.get("elements"), cast, known, i, problems,
                             requests, NEW_POSE_BUDGET - len(requests),
                             duos, NEW_INTERACTION_BUDGET - len(duos))
        if max_sprites:
            sprites = [e for e in elements if "asset" in e]
            if len(sprites) > max_sprites:
                keep = {id(e) for e in sprites[:max_sprites]}
                elements = [e for e in elements
                            if "asset" not in e or id(e) in keep]
                problems.append(
                    f"shot {i}: {len(sprites)} sprites is too many for this "
                    f"frame, kept {max_sprites}")
        # A shot of scenery with nobody in it is a set, not a shot. Diagram-only
        # shots are fine - the references have several - so a hanging board or
        # chart counts as carrying the shot on its own, but a counter and a
        # queue with no actor does not.
        has_character = any("asset" in e and not e["asset"].startswith("prop_")
                            for e in elements)
        has_diagram = any("asset" in e and cast.hangs(e["asset"])
                          for e in elements)
        if elements and not has_character and not has_diagram:
            borrowed = next(
                (dict(e) for scene in reversed(scenes) for e in scene["elements"]
                 if "asset" in e and not e["asset"].startswith("prop_")), None)
            if borrowed:
                borrowed["x"] = 0.22
                problems.append(
                    f"shot {i}: scenery with nobody in it, added "
                    f"{borrowed['asset']}")
                elements.insert(0, borrowed)

        if not any("asset" in e for e in elements):
            # An empty shot renders as a bare plate with a caption floating on
            # it for five seconds, which looks like a bug to a viewer. Holding
            # the previous setup is what an editor would do, and it is always
            # better than nothing on screen.
            carried = [dict(e) for e in scenes[-1]["elements"]
                       if "asset" in e] if scenes else []
            if carried:
                problems.append(
                    f"shot {i}: nothing usable came back, holding shot {i - 1}'s setup")
                elements = carried + [e for e in elements if "asset" not in e]
            else:
                problems.append(f"shot {i}: no usable elements and nothing to hold")
        scene = {"id": i, "narration": narration, "framing": framing,
                 "elements": elements}
        # What the director understood the sentence to depict. Kept so that a
        # shot that looks wrong can be read rather than guessed at: the beat
        # says whether the casting missed the meaning or the meaning was read
        # wrong in the first place.
        beat = raw.get("beat")
        if isinstance(beat, dict):
            scene["beat"] = {k: beat.get(k) for k in
                             ("subject", "action", "object", "emotion", "relation")
                             if beat.get(k)}
        scenes.append(scene)

    # Rebalance the framing. The "no three in a row" rule stops a run but does
    # not create variety: a 32-shot video came back 16 medium, 13 wide and only
    # 3 close, which reads flat because nothing ever gets near a face. The
    # shots with the fewest elements are the ones a close-up suits - one
    # character making one point - so those get promoted until roughly a fifth
    # of the video is close.
    close_target = max(1, round(len(scenes) * 0.20))
    close_now = [s for s in scenes if s["framing"] == "close"]
    if len(scenes) >= 5 and len(close_now) < close_target:
        candidates = sorted(
            (s for s in scenes if s["framing"] != "close"),
            key=lambda s: (len([e for e in s["elements"] if "asset" in e]), s["id"]))
        for scene in candidates[:close_target - len(close_now)]:
            neighbours = [t["framing"] for t in scenes
                          if abs(t["id"] - scene["id"]) == 1]
            if "close" in neighbours:
                continue          # do not create the run we just avoided
            scene["framing"] = "close"
            problems.append(f"shot {scene['id']}: framing raised to close for variety")

    for scene in scenes:
        _draw_together(scene, problems)
    _vary_poses(scenes, cast, problems)

    ending = data.get("ending") or {}
    if isinstance(ending, str):
        ending = {"text": ending}
    ending_text = (ending.get("text") or "").strip() or beats[-1]
    # A closing card is a punchline held on screen, not a paragraph. When the
    # director gives nothing and the last beat is a long sentence, keep only
    # its final clause - four wrapped lines of body text on black reads as a
    # mistake, not an ending.
    if len(ending_text) > ENDING_MAX_CHARS:
        for mark in ("，", "。", "、", ","):
            cut = ending_text.rfind(mark, 0, len(ending_text) - 1)
            if cut > len(ending_text) * 0.25:
                ending_text = ending_text[cut + 1:].strip()
                break
        ending_text = ending_text[:ENDING_MAX_CHARS].strip()

    return {
        "title": (data.get("title") or "").strip(),
        "ending": {"text": ending_text,
                   "highlight": (ending.get("highlight") or "").strip() or None},
        "scenes": scenes,
        "new_poses": requests,
        "new_interactions": duos,
        "problems": problems,
    }


# How close two interacting characters stand, before collision repair opens
# whatever gap their actual widths need. Deliberately tighter than they will
# end up: the repair only ever pushes apart, so asking for too little produces
# the closest legal spacing, which is what an interaction wants.
INTERACTION_SPAN = 0.18


def _draw_together(scene, problems):
    """Put characters who are doing something to each other within reach.

    The director gets the casting right and the spacing wrong, because the
    two-character default is x=0.30 and x=0.70 and it applies that to people
    handing something over. Krabs held out a pay envelope, SpongeBob had both
    hands open to take it, and they stood 40% of the frame apart with the
    envelope nowhere near his hands - so it read as two characters, one of whom
    happened to be holding an envelope.

    `relation` already says who is acting on whom. This uses it.
    """
    relation = ((scene.get("beat") or {}).get("relation") or "").lower()
    if not relation:
        return
    people = [el for el in scene["elements"]
              if el.get("asset") and not el["asset"].startswith("prop_")
              and not el["asset"].startswith("duo_")]
    named = [el for el in people
             if el["asset"].rsplit(".", 1)[0].split("_", 1)[0] in relation]
    if len(named) != 2:
        return                     # not a two-party action; nothing to close up
    left, right = sorted(named, key=lambda el: el.get("x", 0.5))
    span = right.get("x", 0.5) - left.get("x", 0.5)
    if span <= INTERACTION_SPAN:
        return                     # already within reach
    middle = (left.get("x", 0.5) + right.get("x", 0.5)) / 2
    left["x"] = round(middle - INTERACTION_SPAN / 2, 4)
    right["x"] = round(middle + INTERACTION_SPAN / 2, 4)
    problems.append(
        f"shot {scene['id']}: {relation} - closed the gap from "
        f"{span:.2f} to {INTERACTION_SPAN:.2f} so the action reads")


def _vary_poses(scenes, cast, problems):
    """Stop one pose from carrying a whole video.

    Measured on a finished 32-shot video: krabs_stand appeared seven times, so
    22% of the shots were the same picture, and nothing anywhere noticed. The
    director picks per shot and never sees the whole, which is exactly the kind
    of thing a pass over the finished list can fix and a prompt cannot.

    Only the surplus moves, and only onto a pose of the same character that the
    video is leaning on least, so the swap costs nothing already generated and
    the character stays who they are.
    """
    import math
    if len(scenes) < 6:
        return                     # too short for repetition to read as a tic
    limit = max(2, math.ceil(len(scenes) / 8))

    counts = {}
    for scene in scenes:
        for el in scene["elements"]:
            asset = el.get("asset")
            if (asset and not asset.startswith("prop_")
                    and not asset.startswith("duo_")):
                counts[asset] = counts.get(asset, 0) + 1

    for asset, seen in sorted(counts.items(), key=lambda kv: -kv[1]):
        if seen <= limit:
            continue
        character = asset.rsplit(".", 1)[0].split("_", 1)[0]
        poses = ((cast.data.get("characters") or {})
                 .get(character, {}).get("poses", {}))
        # Learned poses are excluded as targets. They were drawn for one
        # sentence - "holding out a pay envelope" - and are used once, which is
        # exactly what makes a least-used rule reach for them. Spreading an
        # action across unrelated shots is a worse error than the repetition.
        siblings = [f"{character}_{pose}.png" for pose in poses
                    if not cast.is_learned(f"{character}_{pose}.png")]
        if len(siblings) < 2:
            continue
        original = poses.get(asset.rsplit(".", 1)[0].split("_", 1)[1], "")
        # Later shots give way first: the first few uses established the
        # character, the tail is where it turns into wallpaper.
        surplus = [sc for sc in scenes
                   if any(e.get("asset") == asset for e in sc["elements"])][limit:]
        for scene in surplus:
            here = {e.get("asset") for e in scene["elements"]}
            options = [sib for sib in siblings
                       if sib != asset and sib not in here]
            if not options:
                continue
            # Least-used first, then whichever reads closest to the pose being
            # replaced, so a standing shot becomes another standing shot rather
            # than jumping to someone sitting behind a desk.
            def fit(candidate):
                other = poses.get(candidate.rsplit(".", 1)[0].split("_", 1)[1], "")
                shared = len(set(original.split()) & set(other.split()))
                return (counts.get(candidate, 0), -shared, candidate)

            swap = min(options, key=fit)
            for el in scene["elements"]:
                if el.get("asset") == asset:
                    el["asset"] = swap
                    el["rel"] = cast.relative_height(swap)
                    break
            counts[asset] -= 1
            counts[swap] = counts.get(swap, 0) + 1
            problems.append(f"shot {scene['id']}: {asset} appeared {seen} times, "
                            f"varied to {swap}")


def commit_poses(cast, plan):
    """Write the poses a plan asked for into the cast's sidecar.

    Separate from `validate` on purpose. Validation is called from the build,
    from migrate_plan, and from the tests, and it used to write to disk as a
    side effect of being asked whether a plan was well-formed - so replaying an
    old plan silently taught the cast new poses. The build calls this once, on
    the plan it is actually going to make.
    """
    for request in plan.get("new_poses") or []:
        cast.learn_pose(request["character"], request["pose"],
                        request["description"])
    for request in plan.get("new_interactions") or []:
        cast.learn_interaction(request["members"], request["action"],
                               request["description"])
    return (len(plan.get("new_poses") or [])
            + len(plan.get("new_interactions") or []))


def used_sprites(plan):
    return sorted({el["asset"] for scene in plan["scenes"]
                   for el in scene["elements"] if "asset" in el})


def offline_plan(script, cast, shot_seconds=5.0):
    """A storyboard without the model: alternating characters, no props.

    Not what you would ship, but it keeps the render path exercisable when Ark
    is unreachable, and it makes the shape of a plan obvious.
    """
    known = sorted(cast.catalogue())
    people = [n for n in known if not n.startswith("prop_")] or known
    framings = ["medium", "close", "wide"]
    scenes = []
    for i, beat in enumerate(split_script(script, shot_seconds)):
        sprite = people[i % len(people)] if people else None
        scenes.append({
            "id": i + 1, "narration": beat, "framing": framings[i % 3],
            "elements": ([{"asset": sprite, "x": 0.5, "y": 0.97, "h": 0.46,
                           "anchor": "bottom"}] if sprite else [])})
    return {"title": "", "ending": {"text": scenes[-1]["narration"] if scenes else "",
                                    "highlight": None},
            "scenes": scenes, "problems": ["offline plan: no director was used"]}
