"""The one configuration file: casts/styles.json.

It answers two questions. Which styles exist, what each is called, which is
the default - and what the finished video should look like: caption size and
position, how tight the shots are, what the label colours mean, how long a
dissolve runs, how much clearance the layout keeps, how hard the matte is
choked. This module is the only place that reads it:

    styles.resolve()                  -> (key, path of the default style)
    styles.resolve("bikini_bottom")   -> (key, its registered cast file)
    styles.resolve("casts/x.json")    -> (None, that path, untouched)
    styles.look()                     -> the shared look settings
    styles.look("neon_cyberpunk")     -> the same, with that style's overrides

Every look value used to be a constant in layout.py, render.py, checks.py or
matting.py, so changing a caption size meant editing Python. The defaults
below are still the measured ones, and are what gets used if the file is
missing or a key is absent - the config can only be incomplete, never wrong
in a way that stops a build.

A cast file dropped into casts/ works without registering - it is
discovered and joins the list under its filename. Registering it in
styles.json only adds a label, a note, a different file location, or a look
override. Everything here is offline and free.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASTS_DIR = ROOT / "casts"
REGISTRY_PATH = CASTS_DIR / "styles.json"

FALLBACK_DEFAULT = "bikini_bottom"

# What the pipeline looks like when the config says nothing. These are the
# values measured off the reference videos; casts/styles.json ships with the
# same numbers written out and commented, so the file teaches what is
# adjustable, and this dict makes sure a missing or half-filled file still
# builds the same video rather than failing or drifting.
LOOK_DEFAULTS = {
    "frame": {
        "landscape": {
            "stage": [0.0, 0.0, 1.0, 0.86],
            "subtitle_size": 0.0323,
            "subtitle_y": 0.903,
            "subtitle_max_width": 0.86,
            "label_size": 0.0344,
            "image_size": "2560x1440",
        },
        "portrait": {
            "stage": [0.0, 0.14, 1.0, 0.80],
            "subtitle_size": 0.050,
            "subtitle_y": 0.845,
            "subtitle_max_width": 0.90,
            "label_size": 0.052,
            "image_size": "1440x2560",
        },
    },
    "framing": {"wide": 0.88, "medium": 1.0, "close": 1.30},
    "label_tones": {
        "neutral": [30, 30, 30],
        "good": [22, 122, 58],
        "bad": [183, 40, 30],
        "money": [176, 112, 8],
    },
    "caption": {
        "fill": [255, 255, 255],
        "stroke_fill": [0, 0, 0],
        "highlight_fill": [255, 210, 60],
    },
    "timing": {"dissolve": 0.5, "caption_fade": 0.12, "element_fade": 0.28},
    "safe_zones": {
        "edge_tolerance": 0.06,
        "min_gap": 0.012,
        "repair_gap_multiple": 2.5,
        "side_margin": 0.02,
        "max_passes": 3,
    },
    "sound": {
        "enabled": True,
        "gain": 0.34,
        "max_per_shot": 2,
        # Several per moment on purpose: the picker takes the least recently
        # used, which is what stops one swoosh carrying every cut in a video.
        "cues": {"transition": ["swoosh_up", "swoosh_down", "swoosh_soft", "tape_stop"], "money": ["cash", "coins_drop"], "good": ["sparkle", "chime"], "bad": ["thud", "error", "wobble"], "surprise": ["boing", "stab", "impact"], "wry": ["tick", "pop_cork"], "label": ["pop_cork", "tick", "riser"], "board": ["scribble"], "punchline": ["rimshot", "sting"]},
    },
    "text": {
        "native_labels": True,
        "animation": {
            "money": "放大",
            "good": "发光闪入",
            "bad": "故障闪动",
            "neutral": "弹入",
            "bubble": "打字机_I",
        },
    },
    "motion": {
        "enabled": True,
        "push_in": 0.05,
        "max_push": 0.08,
        "framings": ["close"],
    },
    "matting": {
        "chroma_lo": 40.0,
        "chroma_hi": 130.0,
        "white_lo": 8.0,
        "white_hi": 44.0,
        "choke": 0.22,
        "rim_pixels": 6.0,
    },
}


def _merge(base, over):
    """Deep-merge `over` onto `base`, one level of nesting at a time.

    Overrides are partial on purpose: a style that wants a different caption
    colour writes only that colour, and inherits the rest. A shallow update
    would silently drop every sibling key.
    """
    out = dict(base)
    for key, value in (over or {}).items():
        if key.startswith("_"):
            continue                     # comment keys are for the reader
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def look(key=None, carried=None):
    """The effective look settings: defaults, config, style, then `carried`.

    `carried` is the block a storyboard records at build time. It wins over the
    config so that re-rendering an old storyboard reproduces the video it
    described, rather than quietly picking up a caption size someone changed
    last week. Merging rather than replacing means a hand-trimmed block cannot
    leave a key missing.
    """
    merged = _merge(LOOK_DEFAULTS, registry().get("look"))
    if key:
        entry = (registry().get("styles") or {}).get(key)
        if isinstance(entry, dict):
            merged = _merge(merged, entry.get("look"))
    return _merge(merged, carried) if carried else merged


# 16:9. The format is built around a fixed background plate with characters
# standing on a ground line, and that reads best wide; portrait is a deliberate
# choice for a phone feed, not the thing to fall into by accident.
FALLBACK_ORIENTATION = "landscape"


def default_orientation():
    """The video shape to use when a project does not name one."""
    wanted = registry().get("default_orientation")
    return wanted if wanted in ("landscape", "portrait") else FALLBACK_ORIENTATION


def frame(orientation=None, key=None):
    """Just the geometry for one orientation - what Layout wants."""
    orientation = orientation or default_orientation()
    return dict((look(key).get("frame") or {}).get(orientation) or {})


def registry():
    """The parsed registry, or {} when the file is absent or unreadable."""
    if not REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def available():
    """Every usable style: {key: {file, label, note, registered}}.

    Registry entries come first; cast files in casts/ that the registry
    does not mention (and templates, and the registry itself) are then
    discovered so a new style works before it is registered. Entries with
    "hidden": true stay resolvable by key but are left out of listings.
    """
    data = registry()
    out = {}
    for key, entry in (data.get("styles") or {}).items():
        if not isinstance(entry, dict):
            entry = {}
        ref = entry.get("file") or f"casts/{key}.json"
        p = Path(ref)
        if not p.is_absolute():
            p = ROOT / ref
        out[key] = {
            "file": p,
            "label": entry.get("label") or key,
            "note": entry.get("note") or "",
            "registered": True,
            "hidden": bool(entry.get("hidden")),
        }
    for cast_file in sorted(CASTS_DIR.glob("*.json")):
        if cast_file.name.startswith("_") or cast_file == REGISTRY_PATH:
            continue
        key = cast_file.stem
        if key not in out:
            out[key] = {"file": cast_file, "label": key, "note": "",
                        "registered": False, "hidden": False}
    return out


def visible():
    """The styles a user is offered, in registry order."""
    return {k: v for k, v in available().items() if not v["hidden"]}


def default_key():
    """The registry's default, falling back to any style that exists."""
    avail = visible()
    wanted = registry().get("default") or FALLBACK_DEFAULT
    if wanted in avail:
        return wanted
    if wanted in available():
        return wanted          # hidden but still resolvable
    for key in avail:
        return key
    for key in available():
        return key
    return None


def resolve(ref=None):
    """A style key, a cast path, or None -> (key, Path to the cast file).

    None means the registry default. A key must be registered or
    discovered; anything else is treated as a path. Unknown keys raise
    ValueError listing what does exist, so a typo names the fix.
    """
    avail = available()
    if ref is None or not str(ref).strip():
        key = default_key()
        if key is None:
            raise ValueError(
                "no style given and no casts found in casts/ - there is no "
                "default style to fall back on")
        return key, avail[key]["file"]
    ref = str(ref).strip()
    if ref in avail:
        return ref, avail[ref]["file"]
    p = Path(ref)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists() and p.suffix.lower() != ".json":
        known = ", ".join(sorted(k for k, v in avail.items() if not v["hidden"]))
        raise ValueError(
            f"{ref!r} is neither a known style nor a cast file. "
            f"known styles: {known}. styles are configured in "
            f"{REGISTRY_PATH.relative_to(ROOT).as_posix()}")
    return None, p


def describe(key=None):
    """A short 'label (note)' string for listings and logs."""
    entry = available().get(key)
    if not entry:
        return key or ""
    text = entry["label"]
    if entry["note"] and entry["note"] != entry["label"]:
        text += f" ({entry['note']})"
    return text


def problems():
    """Everything wrong with the registry itself, in plain language."""
    issues = []
    if not REGISTRY_PATH.exists():
        return issues            # no registry is legal: discovery still works
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"casts/styles.json could not be read: {exc}"]
    if not isinstance(data, dict):
        return ["casts/styles.json must be a JSON object"]
    styles = data.get("styles")
    if styles is not None and not isinstance(styles, dict):
        issues.append("`styles` must be an object of {key: {label, note, file}}")
        styles = {}
    avail = available()
    default = data.get("default")
    if default and default not in avail:
        issues.append(f"`default` names {default!r}, which is not a known "
                      "style - register it under `styles` or drop its cast "
                      "file into casts/")
    for key, entry in (styles or {}).items():
        if not isinstance(entry, dict):
            continue
        ref = entry.get("file")
        if ref:
            p = Path(ref)
            if not p.is_absolute():
                p = ROOT / p
            if not p.exists():
                issues.append(f"style {key!r} points at {ref}, which does "
                              "not exist")
        if key not in avail:
            continue
    return issues
