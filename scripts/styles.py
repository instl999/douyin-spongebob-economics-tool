"""The art-style registry: casts/styles.json.

A style is a cast file (casts/_template.json explains the format); the
registry is the one file that says which styles exist, what each is called
for humans, and which one is the default. This module is the only place
that reads it:

    styles.resolve()                  -> (key, path of the default style)
    styles.resolve("bikini_bottom")   -> (key, its registered cast file)
    styles.resolve("casts/x.json")    -> (None, that path, untouched)

A cast file dropped into casts/ works without registering - it is
discovered and joins the list under its filename. Registering it in
styles.json only adds a label, a note, or a different file location.
Everything here is offline and free.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASTS_DIR = ROOT / "casts"
REGISTRY_PATH = CASTS_DIR / "styles.json"

FALLBACK_DEFAULT = "bikini_bottom"


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
