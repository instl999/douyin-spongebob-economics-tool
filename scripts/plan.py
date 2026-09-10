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
import hashlib
import re
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
# How much of a description survives into a prompt. Long enough for a body,
# a face and a held object; short enough that the model does not start
# illustrating subordinate clauses.
DESCRIPTION_MAX = 220

# How many separate pictures one video may ask for. Nothing is reused between
# videos any more, so this is the whole bill: at roughly twenty seconds and one
# image each, a cap is the difference between a video and an afternoon. Past it
# the extra elements are dropped, cheapest-looking first.
MAX_DRAWINGS = 40
MAX_DUOS = 6

NEW_POSE_BUDGET = 8

# Two-figure sprites are drawn for one beat and are far less reusable than a
# pose, so they get their own, smaller allowance. They exist because a handover
# assembled from two separate cut-outs never actually connects - which makes
# them the only way some sentences can be shown at all, and also the reason not
# to reach for one when a pose would do.
NEW_INTERACTION_BUDGET = 4

# Hanging boards, charts and calendars are the only thing that ever occupies
# the upper half of the frame, and they were arriving at roughly two thirds of
# a character's height. Measured across seven videos, the top 24-37% of every
# frame was empty.
MIN_BOARD_HEIGHT = 0.40

# An interaction sprite holds two figures and is nearly always framed "medium",
# where a solo reaction is framed "close" and multiplied by 1.30. At the same h
# the pair therefore lands about 80% the size of the single characters it cuts
# between, and the shot that is *most* about what is happening reads as the
# furthest away. Measured on payday: the handover came back at h=0.50 against
# solos at an effective 0.62, and at 0.60 the two shots match.
MIN_DUO_HEIGHT = 0.58

# A "wide" shot scales everything down 12%. That is worth it for three figures
# or a building and actively harmful below that: one video used wide for 13 of
# 32 shots while averaging 2.7 elements, so "wide" just meant "smaller".
WIDE_NEEDS_ELEMENTS = 3

# A panel is a room, and a room does not stop two thirds of the way across the
# picture. Below this the plate's grass shows down both sides and the wall
# reads as a screen propped on a lawn rather than as somewhere the characters
# are. Not 1.0: a little of the plate at the edges still reads as depth.
MIN_PANEL_WIDTH = 0.94
MIN_PANEL_HEIGHT = 0.45

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


# Words that carry no picture. Dropped from a filename slug so that
# "a fat stack of gold coins" and "the stack of gold coins" read as the same
# thing at a glance; the hash beside it is what actually decides identity.
SLUG_SKIP = {"a", "an", "the", "of", "in", "on", "at", "with", "and", "to",
             "his", "her", "their", "its", "one", "some", "is", "are"}
SLUG_SPLIT = re.compile(r"[^a-z0-9]+")

# What a sprite is *for*, which decides how it is placed and drawn over.
# These used to be three lists in the cast file keyed on prop filenames -
# `hanging`, `foreground`, `writable` - and a list of filenames cannot describe
# a picture that is invented for one video and never drawn again. The director
# names the role instead, and the description is read as a fallback.
SPRITE_ROLES = ("figure", "prop", "furniture", "board", "hanging")
ROLE_WORDS = (
    ("board", ("whiteboard", "chalkboard", "blackboard", "chart", "graph",
               "diagram", "poster", "noticeboard", "screen showing",
               "白板", "黑板", "图表", "海报")),
    ("hanging", ("clock", "sign", "banner", "hanging", "on the wall", "mounted",
                 "钟", "招牌", "挂")),
    ("furniture", ("counter", "desk", "table", "workbench", "bar", "cart",
                   "till", "register", "柜台", "桌", "工作台")),
)


def _slug(text, words=4, limit=28):
    """A short readable stem for a generated sprite's filename."""
    parts = [p for p in SLUG_SPLIT.split(text.lower())
             if p and p not in SLUG_SKIP]
    return "_".join(parts[:words])[:limit].strip("_") or "sprite"


def _sprite_name(kind, who, shows):
    """The filename for one described sprite.

    Two shots that ask for the same picture get the same name and so are drawn
    once - which is the only dedup left now that nothing is reused between
    videos, and worth having: a talking-head script asks for the same figure
    four times. The hash is what decides that, and the slug is only there so a
    person can tell what a file is without opening it.
    """
    key = hashlib.sha256(
        "|".join([kind, ",".join(who), shows]).encode("utf-8")).hexdigest()[:6]
    if kind == "duo":
        return f"duo_{'_'.join(who)}_{_slug(shows)}_{key}.png"
    if kind == "figure":
        return f"{who[0]}_{_slug(shows)}_{key}.png"
    return f"prop_{_slug(shows)}_{key}.png"


def _role_for(el, kind, shows):
    """Whether this sprite hangs, stands in front, or just stands."""
    named = (el.get("role") or "").strip().lower()
    if named in SPRITE_ROLES:
        return named
    if kind in ("figure", "duo"):
        return "figure"
    low = shows.lower()
    for role, words in ROLE_WORDS:
        if any(word in low for word in words):
            return role
    return "prop"


def _sprite_spec(el, cast, shot_id, problems):
    """Read one described sprite off the director's answer.

    The director used to pick a filename out of a catalogue, and a catalogue is
    why every video looked like the last one: fifty-odd drawings shared by
    every script, so a pile of gold coins turned up in five videos out of
    eight. Now the sentence is described and the picture is drawn for it.
    """
    shows = (el.get("shows") or el.get("new_pose")
             or el.get("new_interaction") or "").strip()
    who = el.get("who")
    who = [who] if isinstance(who, str) else list(who or [])
    who = [str(name).strip() for name in who if str(name).strip()]

    characters = cast.data.get("characters") or {}
    unknown = [name for name in who if name not in characters]
    if unknown:
        problems.append(f"shot {shot_id}: no character called {unknown[0]!r} in "
                        "this cast, drawn without them")
        who = [name for name in who if name in characters]
    if len(who) > 2:
        problems.append(f"shot {shot_id}: {len(who)} characters in one drawing "
                        "never comes back right, kept the first two")
        who = who[:2]
    if not shows:
        problems.append(f"shot {shot_id}: an element with nothing to draw "
                        "(`shows` is empty), dropped")
        return None
    if len(shows) > DESCRIPTION_MAX:
        shows = shows[:DESCRIPTION_MAX].rsplit(",", 1)[0]

    kind = "duo" if len(who) == 2 else "figure" if who else "prop"
    return (_sprite_name(kind, who, shows), kind, who, shows,
            _role_for(el, kind, shows))


def _roster_text(cast):
    """Who exists in this cast, and what they look like.

    This replaced a catalogue of every drawing already made. The catalogue was
    the reason every video looked like the last one: fifty-odd pictures shared
    by every script, so the director's job was picking from a menu and a pile
    of gold coins turned up in five videos out of eight. What the director
    needs is who is available to act, not what has already been drawn.
    """
    lines = []
    for name, char in (cast.data.get("characters") or {}).items():
        role = (char.get("role") or "").strip()
        lines.append(f"- {name}" + (f" - {role}" if role else ""))
    return chr(10).join(lines)


def build_prompt(beats, cast, orientation="landscape",
                 pose_budget=MAX_DRAWINGS):
    catalogue = _roster_text(cast)
    listing = chr(10).join(f"{i}. {text}" for i, text in enumerate(beats, 1))
    portrait = orientation == "portrait"
    return brief_template().format(
        count=len(beats), beats=listing, catalogue=catalogue,
        casting=_casting_notes(cast),
        pose_budget=pose_budget,
        duo_budget=MAX_DUOS,
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
    return plan


# --- validation ------------------------------------------------------------

def _clamp(value, lo, hi, default):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _elements(raw_elements, cast, shot_id, problems, drawings):
    elements, seen = [], set()
    for el in raw_elements or []:
        kind = el.get("type", "sprite")
        if kind == "panel":
            item = {"type": "panel",
                    "x": 0.5,          # a wall is centred; nothing else reads
                    "y": _clamp(el.get("y"), 0.0, 1.05, 0.9),
                    "w": _clamp(el.get("w"), MIN_PANEL_WIDTH, 1.0,
                                MIN_PANEL_WIDTH),
                    "ph": _clamp(el.get("ph"), MIN_PANEL_HEIGHT, 1.0,
                                 MIN_PANEL_HEIGHT),
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
            spec = _sprite_spec(el, cast, shot_id, problems)
            if spec is None:
                continue
            asset, kind, who, shows, role = spec
            drawings.setdefault(asset, {"asset": asset, "kind": kind,
                                        "who": who, "shows": shows})

            anchor = el.get("anchor", "bottom")
            y = _clamp(el.get("y"), 0.05, 1.02, 0.97)
            # Whether a sprite may float is a property of the object, not of the
            # anchor the director happened to pick. Left to its own judgement a
            # director will hang a pile of gold coins at eye level like a wall
            # chart, so the cast says which props hang and everything else is
            # put on the ground whatever it asked for.
            if role in ("board", "hanging"):
                anchor = "center"
                y = max(0.22, min(0.62, y))
                # A board or chart is usually what the shot is *about*, and it
                # was coming out at 0.27-0.35 of frame height against a
                # character's 0.42-0.50 - which reads as a sticker on a wall of
                # empty sky rather than as the subject. The floor is the
                # difference between a diagram and a decoration.
                item_h = _clamp(el.get("h"), 0.10, 0.85, 0.46)
                if item_h < MIN_BOARD_HEIGHT:
                    problems.append(
                        f"shot {shot_id}: {asset!r} at h={item_h:.2f} is too "
                        f"small to read, raised to {MIN_BOARD_HEIGHT}")
                    el = dict(el, h=MIN_BOARD_HEIGHT)
            elif anchor != "bottom" or y < 0.88:
                if y < 0.88:
                    problems.append(
                        f"shot {shot_id}: {asset!r} was floating at y={y:.2f}, "
                        "moved to the ground")
                anchor, y = "bottom", 0.97

            # One character twice in a shot is always a mistake, and a
            # drawing of two characters claims both of them - otherwise a shot
            # could hold the pair *and* a separate drawing of one of them, and
            # that character would be on screen twice. Two different props in
            # one shot is normal, so a prop only clashes with itself.
            subjects = set(who) if who else {asset}
            clash = subjects & seen
            if clash:
                problems.append(f"shot {shot_id}: {asset!r} repeats "
                                f"{sorted(clash)[0]}, dropped")
                continue
            seen |= subjects

            item_h = _clamp(el.get("h"), 0.10, 0.85, 0.46)
            if kind == "duo" and item_h < MIN_DUO_HEIGHT:
                problems.append(
                    f"shot {shot_id}: {asset!r} at h={item_h:.2f} puts two "
                    f"figures further away than the singles around them, "
                    f"raised to {MIN_DUO_HEIGHT}")
                item_h = MIN_DUO_HEIGHT
            item = {"asset": asset,
                    "role": role,
                    # The description stays on the element, not only in the
                    # `drawings` list. It is what makes a saved plan replayable
                    # through a newer validator, and it is what a person edits
                    # when they want a shot drawn differently.
                    "who": list(who),
                    "shows": shows,
                    "flip": bool(el.get("flip")),
                    "x": _clamp(el.get("x"), 0.03, 0.97, 0.5),
                    "y": y,
                    "h": item_h,
                    "rel": cast.relative_height(asset),
                    "anchor": anchor}
        appear = el.get("appear")
        if appear:
            item["appear"] = _clamp(appear, 0.0, 30.0, 0.0)
        if el.get("z") is not None:
            item["z"] = _clamp(el.get("z"), -5.0, 5.0, 1.0)
        elements.append(item)

    return in_depth_order(elements)


def in_depth_order(elements):
    """Sort into draw order: walls, boards, characters, then furniture.

    Elements composite in list order, so list order *is* depth. A stable sort
    keeps the director's order within each band.

    This is a module-level function rather than a closure because it has to be
    re-applied. Two later repairs insert an element into an already-sorted
    list - the borrowed character for a shot of empty scenery, and the carried
    setup for a shot that came back with nothing - and both put it at index 0,
    which is *behind* everything. The result was a character rendered under a
    translucent wall, washed out, while the props sat crisply in front.
    """
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
        role = item.get("role", "prop")
        if role in ("board", "hanging"):
            return 0.0
        return 2.0 if role == "furniture" else 1.0

    return sorted(elements, key=depth)


def validate(data, beats, cast, max_sprites=None):
    """Attach the model's choices to the beats. Narration comes from `beats`."""
    problems = []
    drawings = {}

    by_id = {}
    for raw in data.get("shots") or data.get("scenes") or []:
        try:
            by_id[int(raw.get("id"))] = raw
        except (TypeError, ValueError):
            continue

    scenes, recent = [], []
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

        elements = _elements(raw.get("elements"), cast, i, problems, drawings)
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
        has_diagram = any(e.get("role") in ("board", "hanging")
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
                elements = in_depth_order(elements + [borrowed])

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
                elements = in_depth_order(
                    carried + [e for e in elements if "asset" not in e])
            else:
                problems.append(f"shot {i}: no usable elements and nothing to hold")
        if framing == "wide" and sum(
                1 for e in elements if "asset" in e) < WIDE_NEEDS_ELEMENTS:
            problems.append(
                f"shot {i}: wide with too little in it, framed medium instead")
            framing = "medium"
            recent[-1] = framing

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
        _waist_high(scene, problems)
        _draw_together(scene, problems)
    _vary_poses(scenes, problems, drawings)

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
        "setting": (data.get("setting") or "").strip(),
        "ending": {"text": ending_text,
                   "highlight": (ending.get("highlight") or "").strip() or None},
        "scenes": scenes,
        # Everything this video needs drawn, once each. Nothing is looked up in
        # a library and nothing survives to the next video.
        "drawings": [drawings[name] for name in sorted(drawings)],
        "problems": problems,
    }


# How close two interacting characters stand, before collision repair opens
# whatever gap their actual widths need. Deliberately tighter than they will
# end up: the repair only ever pushes apart, so asking for too little produces
# the closest legal spacing, which is what an interaction wants.
INTERACTION_SPAN = 0.18

# How tall a piece of foreground furniture may be relative to the character
# standing behind it. Furniture is drawn *over* their legs on purpose, and at
# equal height that stops being an overlap and becomes a wall: a counter at
# h=0.40 in front of a character at h=0.46 left a pair of feet and a sliver of
# face. Waist-high is what "standing behind the counter" looks like.
FURNITURE_SHARE = 0.62


def _waist_high(scene, problems):
    """Shrink foreground furniture that would hide whoever stands at it."""
    people = [el for el in scene["elements"]
              if el.get("asset") and not el["asset"].startswith("prop_")]
    if not people:
        return
    for el in scene["elements"]:
        if el.get("role") != "furniture":
            continue
        # Whoever this furniture is drawn over: the nearest character in x,
        # which is the one the director put it with.
        near = min(people, key=lambda c: abs(c.get("x", 0.5) - el.get("x", 0.5)))
        if abs(near.get("x", 0.5) - el.get("x", 0.5)) > 0.20:
            continue                      # beside them, not in front of them
        ceiling = round(float(near.get("h", 0.46)) * FURNITURE_SHARE, 3)
        if float(el.get("h", 0.4)) > ceiling:
            problems.append(
                f"shot {scene['id']}: {el['asset']!r} at h={el.get('h')} would hide "
                f"{near['asset']}, lowered to {ceiling}")
            el["h"] = ceiling


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


def _vary_poses(scenes, problems, drawings):
    """Stop one drawing from carrying a whole video.

    Measured on a finished 32-shot video: the same picture of Mr. Krabs
    appeared seven times, so 22% of the shots were identical, and nothing
    noticed. The director writes one shot at a time and never sees the whole,
    which is exactly what a pass over the finished list can fix.

    It used to fix it by swapping in another pose from the catalogue. There is
    no catalogue now - two shots share a picture only because the director
    described them in the same words - so the surplus is *re-described*
    instead, using what that shot's own beat says is happening in it. A drawing
    that repeats is a drawing whose description ignored the sentence, and the
    beat is where the sentence was already read.
    """
    import math
    if len(scenes) < 6:
        return                     # too short for repetition to read as a tic
    limit = max(2, math.ceil(len(scenes) / 8))

    counts = {}
    for scene in scenes:
        for el in scene["elements"]:
            asset = el.get("asset")
            if asset and not asset.startswith("prop_"):
                counts[asset] = counts.get(asset, 0) + 1

    for asset, seen in sorted(counts.items(), key=lambda kv: -kv[1]):
        if seen <= limit:
            continue
        spec = drawings.get(asset)
        if not spec or not spec["who"]:
            continue
        # Later shots give way first: the first few uses established the
        # character, the tail is where it turns into wallpaper.
        surplus = [sc for sc in scenes
                   if any(e.get("asset") == asset for e in sc["elements"])][limit:]
        for scene in surplus:
            beat = scene.get("beat") or {}
            extra = ", ".join(str(beat.get(k)).strip() for k in ("action", "emotion")
                              if (beat.get(k) or "").strip())
            if not extra or extra.lower() in spec["shows"].lower():
                continue
            shows = f"{spec['shows']}, {extra}"[:DESCRIPTION_MAX]
            fresh = _sprite_name(spec["kind"], spec["who"], shows)
            if fresh == asset or any(e.get("asset") == fresh
                                     for e in scene["elements"]):
                continue
            drawings.setdefault(fresh, {"asset": fresh, "kind": spec["kind"],
                                        "who": list(spec["who"]), "shows": shows})
            for el in scene["elements"]:
                if el.get("asset") == asset:
                    el["asset"] = fresh
                    break
            counts[asset] -= 1
            counts[fresh] = counts.get(fresh, 0) + 1
            problems.append(f"shot {scene['id']}: the same drawing was used "
                            f"{seen} times, re-described from its beat")


def used_sprites(plan):
    return sorted({el["asset"] for scene in plan["scenes"]
                   for el in scene["elements"] if "asset" in el})


def offline_plan(script, cast, shot_seconds=5.0):
    """A storyboard without the model: one character per beat, no props.

    Not what you would ship, but it keeps the render path exercisable when Ark
    is unreachable, and it makes the shape of a plan obvious.
    """
    people = list(cast.data.get("characters") or {})
    framings = ["medium", "close", "wide"]
    scenes, drawings = [], {}
    for i, beat in enumerate(split_script(script, shot_seconds)):
        elements = []
        if people:
            who = [people[i % len(people)]]
            shows = "standing squarely, talking to the viewer"
            asset = _sprite_name("figure", who, shows)
            drawings.setdefault(asset, {"asset": asset, "kind": "figure",
                                        "who": who, "shows": shows})
            elements.append({"asset": asset, "role": "figure", "x": 0.5,
                             "y": 0.97, "h": 0.46, "anchor": "bottom",
                             "rel": cast.relative_height(asset)})
        scenes.append({"id": i + 1, "narration": beat,
                       "framing": framings[i % 3], "elements": elements})
    return {"title": "", "setting": "",
            "ending": {"text": scenes[-1]["narration"] if scenes else "",
                       "highlight": None},
            "scenes": scenes,
            "drawings": [drawings[n] for n in sorted(drawings)],
            "problems": ["offline plan: no director was used"]}
