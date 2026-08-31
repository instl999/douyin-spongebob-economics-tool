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
import ark

CHARS_PER_SECOND = 5.2
SENTENCE_END = "。！？!?；;"
# A closing card longer than this stops being a punchline.
ENDING_MAX_CHARS = 24
# Kept in step with render.LABEL_TONES.
LABEL_TONES = ("neutral", "good", "bad", "money")

SYSTEM = """You are the director of a SpongeBob-style animated explainer.

The picture is built by compositing: one fixed background plate, with cut-out
character and prop sprites placed on top of it. For each shot you choose which
sprites appear and where.

You never write or change narration - it is fixed and given to you. You never
invent a sprite; only the listed filenames exist.

Return JSON only, no commentary."""

TEMPLATE = """# Shots

The narration is already split. Do not change it, merge it, or add to it.
Return exactly {count} shots, with these ids.

{beats}

# Sprites you may use (exact filenames, nothing else exists)

{catalogue}

# Casting

{casting}

# For each shot

Pick {lo_elements}-{hi_elements} elements that *act out* what that shot's sentence
says. Put the prop the sentence is about next to the character, and change who
is on screen when the subject changes. One lone character is a wasted shot.

{orientation_note}

## Framing

Give every shot a "framing" of "wide", "medium" or "close". It scales the whole
shot, and it is how you vary the picture without moving anything.

- "close" for one character making a point, or a reaction
- "medium" for two characters, or a character with the prop they are using
- "wide" for three characters, or a big prop like a building

**Vary it.** A run of identically framed shots reads as a slideshow. Never use
the same value more than twice in a row, and aim for roughly a fifth of the
video to be "close" - a video that never gets near a face reads flat.

## Setting a shot somewhere else

The background never changes. When the narration names a *place* - an office, a
meeting, a shop, a warehouse, a dock, a kitchen - put a flat slab behind
everyone and the shot reads as being there:

{{"type": "panel", "x": 0.5, "y": 0.99, "w": 0.55, "ph": 0.34}}

x,y is the bottom centre in stage coordinates; w and ph are fractions of the
frame. Add the furniture that belongs there on top of it - a desk, a counter, a
meeting table - and the place is built.

**Look through the shot list for every sentence that names a location and give
those shots a panel.** It is the only way this format can leave the default
setting, so a video whose script mentions an office and never shows one has
missed something. Do not put one on every shot; shots that are about an idea
rather than a place do not need one.

## Coordinates

x and y are 0-1 across the stage. y is where the *bottom* of a sprite sits.

- Characters on the ground: y 0.96-1.0, h 0.42-0.52.
  y = 1.0 is the ground line, so 0.97 means "standing on it"
- Props are usually smaller than the people using them: h 0.20-0.35 for a
  hand-held or table-top object, 0.35-0.50 for furniture, 0.50-0.65 only for a
  building. A stack of banknotes as tall as a person reads as a mistake
- One character: x 0.5. Two: x 0.30 and x 0.70. Three: 0.22, 0.5, 0.78
- A prop a character uses goes beside them, e.g. character x 0.34, prop x 0.64
- Furniture a character works AT - a counter, a desk, a sink, a table - goes at
  almost the same x as that character (within 0.06), not beside them. It is
  drawn over their legs, so they read as standing behind it. Use this to build
  a place: a counter plus an oven plus a character is a kitchen
- Boards, charts and maps hang at eye level: "anchor": "center", y 0.34-0.46
- Never leave a solid object floating in open water. Anything not hanging on a
  wall stands on the ground like everyone else
- Labels: "type": "label" with "text", "anchor": "center", just above or below
  the thing they name, y 0.20-0.55. Two or three words - a figure, a name, a
  before/after. Never a whole sentence. Add "tone": "good" when it names an
  improvement, "bad" for a loss or a problem, "money" for a figure or a price,
  and leave it off otherwise - it colours the label green, red or amber
- Speech bubbles: "type": "bubble" with "text", "anchor": "center", y 0.18-0.34,
  "tail": "left" or "right" leaning back toward the speaker. Under 15
  characters, used sparingly, for a character's own line
- Anything arriving part-way through a shot: "appear": seconds from shot start
- Draw order is worked out for you: walls behind, then hanging boards, then
  characters, then furniture over their legs. Only set "z" (a number, lower is
  further back) when you need something the bands cannot express - a character
  standing between two pieces of furniture

Never put the same character on screen twice in one shot. Do not overlap two
characters.

# Output

{{"title": "<= 10 characters, the question the video answers",
  "ending": {{"text": "the closing line, may contain \\n", "highlight": "<= 4 characters from it"}},
  "shots": [
    {{"id": 1, "framing": "medium",
      "elements": [
        {{"asset": "krabs_point.png", "x": 0.32, "y": 0.97, "h": 0.46}},
        {{"type": "label", "text": "涨工资", "x": 0.70, "y": 0.42, "anchor": "center"}}
      ]}}
  ]}}"""


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


def build_prompt(beats, cast, orientation="landscape"):
    catalogue = chr(10).join(f"- {n}" for n in sorted(cast.catalogue()))
    listing = chr(10).join(f"{i}. {text}" for i, text in enumerate(beats, 1))
    portrait = orientation == "portrait"
    return TEMPLATE.format(
        count=len(beats), beats=listing, catalogue=catalogue,
        casting=_casting_notes(cast),
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
    return validate(data, beats, cast,
                    max_sprites=2 if orientation == "portrait" else None)


# --- validation ------------------------------------------------------------

def _clamp(value, lo, hi, default):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _nearest(asset, known):
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


def _elements(raw_elements, cast, known, shot_id, problems):
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
                swap = _nearest(asset, known)
                if not swap:
                    problems.append(f"shot {shot_id}: unknown sprite {asset!r}, dropped")
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
            subject = stem if stem.startswith("prop_") else stem.split("_", 1)[0]
            if subject in seen:
                problems.append(f"shot {shot_id}: {asset!r} repeats {subject}, dropped")
                continue
            seen.add(subject)

            item = {"asset": asset,
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

        elements = _elements(raw.get("elements"), cast, known, i, problems)
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
        scenes.append({"id": i, "narration": narration, "framing": framing,
                       "elements": elements})

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
        "problems": problems,
    }


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
