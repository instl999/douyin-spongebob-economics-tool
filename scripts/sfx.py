"""Decide where the sound effects go, and which one each moment gets.

Sound is the one kind of emphasis that costs nothing to render, works in the
MP4 and the draft alike, and does not touch a single pixel - so none of the
measurements the picture is built on are at risk.

The first version of this put a cue where the storyboard gave a reason and
picked the sound from a fixed table: one transition sound, one label sound, one
money sound. On a real video that came out **53% the same effect** - seven
whooshes out of thirteen cues - which is what "the sound effects don't fit"
sounds like even before you notice the effects themselves were sine waves.

Two things fix that, and both are here rather than in the library.

**Choose on meaning, not on furniture.** The director already writes down what
each sentence depicts - subject, action, object, emotion, relation - and that
is a far better basis for a cue than which props happen to be on screen. A
delighted beat and a dismayed beat should not share a sound, and until now
they did.

**Never take the same one twice running.** Each category holds several
interchangeable effects and the least-recently-used one wins, so a video with
seven cuts gets four different swooshes rather than one seven times. This is
the whole difference between sound design and a sound.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render as render_mod
import styles as styles_mod

ROOT = Path(__file__).resolve().parent.parent
SFX_DIR = ROOT / "assets" / "sfx"

# A transition cue lands slightly before the cut, the way an editor would place
# it: the sound leads the picture, so the change feels caused rather than
# noticed. Everything else lands on its own moment.
TRANSITION_LEAD = 0.08

# What the director's free-text emotion means for sound. Matched as substrings
# against a lowercased beat, English and Chinese together, because the field is
# prose - "sponge delighted, krabs grudging", "confused then surprised" - and
# not a controlled vocabulary. First category to match wins, so the more
# specific reactions are listed before the plainer ones.
EMOTION_WORDS = [
    ("surprise", ("surpris", "shock", "astonish", "confus", "puzzl", "disbelief",
                  "惊", "意外", "震惊", "困惑", "疑惑", "没想到")),
    ("bad", ("sad", "dismay", "disappoint", "worried", "anxious", "angry",
             "frustrat", "glum", "grim", "bleak", "regret", "沮丧", "难过",
             "失望", "焦虑", "生气", "愁", "郁闷", "无奈")),
    ("good", ("delight", "happy", "pleased", "excit", "proud", "cheer",
              "relie", "satisf", "eager", "开心", "高兴", "兴奋", "满意",
              "得意", "欣慰", "喜")),
    ("wry", ("smug", "cynic", "sarcas", "wry", "dry", "deadpan", "冷笑",
             "得瑟", "嘲", "讽")),
]


def library():
    """{name: path} for every effect on disk."""
    if not SFX_DIR.is_dir():
        return {}
    return {f.stem: f for f in sorted(SFX_DIR.glob("*.wav"))}


def _has(scene, predicate):
    return any(predicate(el) for el in scene.get("elements") or [])


def _emotion(scene):
    """Which reaction category this shot's beat calls for, or None."""
    beat = scene.get("beat") or {}
    text = " ".join(str(beat.get(k) or "")
                    for k in ("emotion", "action")).lower()
    if not text.strip():
        return None
    for category, words in EMOTION_WORDS:
        if any(word in text for word in words):
            return category
    return None


class _Rotor:
    """Hands out the least-recently-used effect in a category.

    Deliberately global across the video rather than per category: a video
    should not open with a swoosh and a chime that happen to be the two
    brightest things in the library, and remembering what has just been heard
    is what stops that.
    """

    def __init__(self, have):
        self.have = have
        self.used = {}
        self.clock = 0

    def take(self, *groups):
        """Least-recently-used across `groups`, preferring the earlier ones.

        Groups rather than one list because a category can simply run out: a
        video about wages has four money reveals and there are two cha-chings,
        so the fourth would repeat the second. Ranking by "when was this last
        heard, then how well does it fit" spends the whole palette before it
        repeats anything, which is what stops a sound design sounding like a
        sound.
        """
        options = []
        for rank, names in enumerate(groups):
            for name in names or []:
                if name in self.have and name not in [o[0] for o in options]:
                    options.append((name, rank))
        if not options:
            return None
        self.clock += 1
        pick = min(options, key=lambda o: (self.used.get(o[0], -1), o[1], o[0]))[0]
        self.used[pick] = self.clock
        return pick


def carried(storyboard):
    """The cue list a storyboard recorded, resolved back to paths.

    Planned once, when the storyboard is built and the cast is in hand, then
    carried like `look` and `panel_color`. The mix and the draft then cannot
    disagree - which they did: the draft plans without a cast, could not tell a
    writable board from any other prop, and quietly produced one cue fewer than
    the MP4.
    """
    have = library()
    out = []
    for entry in storyboard.get("sound_cues") or []:
        try:
            when, name = float(entry[0]), str(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        if name in have:
            out.append((when, name, have[name]))
    return out


def plan(storyboard, durations, cast=None, look=None):
    """Return [(seconds, name, path)] for the whole video, in time order.

    `cast` is optional and only sharpens the board cue - without it a writable
    prop cannot be told from any other prop, and that cue is simply skipped
    rather than fired on everything.
    """
    look = look or styles_mod.look(carried=(storyboard.get("video") or {}).get("look"))
    config = look.get("sound") or {}
    have = library()
    if not config.get("enabled", True) or not have:
        return []

    palette = config.get("cues") or {}
    per_shot = int(config.get("max_per_shot", 2))
    rotor = _Rotor(have)
    segments, _ = render_mod.build_timeline(storyboard, durations)
    cues = []

    for segment in segments:
        if segment.kind == "ending":
            name = rotor.take(palette.get("punchline"))
            if name:
                cues.append((segment.start, name))
            continue
        if segment.kind != "scene":
            continue

        scene, shot = segment.data, []

        # The cut itself, on everything but the first shot: there is nothing to
        # cut from, and a swoosh over the title card reads as a mistake.
        if segment.index > 0:
            name = rotor.take(palette.get("transition"))
            if name:
                shot.append((max(0.0, segment.start - TRANSITION_LEAD), name))

        # Then one cue for what the shot is *about*. A figure appearing on
        # screen earns a cha-ching; a pile of coins merely lying in the frame
        # does not, and treating those alike put a money cue on half the shots
        # and left the reactions unheard. So: a money *label* first, because
        # that is a reveal, then the reaction, then whatever furniture is left.
        moment = None
        if _has(scene, lambda el: el.get("tone") == "money"
                and el.get("type") in ("label", "bubble")):
            moment = "money"
        elif _emotion(scene):
            moment = _emotion(scene)
        elif _has(scene, lambda el: "coin" in (el.get("asset") or "")
                  or "money" in (el.get("asset") or "")):
            moment = "money"
        elif _has(scene, lambda el: el.get("type") in ("label", "bubble")):
            moment = "label"
        elif cast is not None and _has(
                scene, lambda el: el.get("asset") and cast.writable(el["asset"])):
            moment = "board"

        if moment:
            # The reaction is the natural second choice for anything: it is
            # about the sentence rather than about the furniture, so it fits
            # wherever the first choice has just been heard.
            reaction = _emotion(scene)
            name = rotor.take(palette.get(moment),
                              palette.get(reaction) if reaction != moment else None,
                              palette.get("label"))
            if name:
                appear = min((float(el.get("appear", 0.0))
                              for el in scene.get("elements") or []
                              if el.get("type") in ("label", "bubble")),
                             default=0.0)
                shot.append((segment.start + max(appear, 0.18), name))

        cues.extend(sorted(shot)[:per_shot])

    return [(when, name, have[name]) for when, name in sorted(cues)]


def describe(cues):
    """One line for the build log: how many, how varied."""
    counts = {}
    for _, name, _ in cues:
        counts[name] = counts.get(name, 0) + 1
    listing = ", ".join(f"{name}x{n}" if n > 1 else name
                        for name, n in sorted(counts.items()))
    return f"{len(counts)} distinct: {listing}"
