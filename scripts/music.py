"""Choosing the bed: one track from a folder, matched to how the script reads.

There is no music library in the repository beyond a single default file, and
there deliberately still is not one. What this module adds is the ability to
point at a folder of your own music and have the right track come out, which
is a different thing from shipping music: the tracks are licensed to whoever
collected them, and a pipeline that guesses is a pipeline that puts a crisis
cue under a joke.

The convention is the one a collected folder already arrives with - the mood
written in Chinese at the front of the filename, ahead of the track's own
title:

    紧张Kill Drill - Robert Ruth.mp3
    紧张危机Dismantle - Peter Sandberg.mp3
    舒缓Keep on the Sunny Side - 岩崎太整.mp3

The director labels the script from the same vocabulary, as one extra field on
the call it already makes, so a label never has to be translated into another
label on the way. When the model says nothing - an older plan, a stricter JSON
mode - the words in the script answer instead, so a build is never held up for
want of a mood.
"""
from pathlib import Path

SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

# The words the director may use and the words a filename may carry. They are
# deliberately the same strings: matching a label to a filename needs no
# synonym table, and a table is the part that rots when either list grows.
MOODS = ("紧张", "危机", "焦虑", "疑问", "疑惑", "失落", "转机",
         "升华", "欢乐", "舒缓", "平淡", "解释", "讲解", "措施", "解决")

# Labels that say WHERE in a video a track belongs rather than how it feels.
# One bed runs under the whole video here, so a cue written for an opening or
# an ending is a worse choice than an unlabelled track of the same mood -
# ranked below it, not excluded, because a folder may hold nothing else.
POSITIONS = ("开头", "结尾", "后期", "提出")

# What the script itself says, for when the director labels nothing. Coarse on
# purpose: this is the fallback that keeps a build in music, not a second
# opinion on a label the model did give. Common function words are left out,
# because a word that appears in every script cannot tell two apart.
KEYWORDS = {
    "紧张": ("压力", "逼", "冲突", "争", "吵", "来不及", "抢", "拼"),
    "危机": ("危机", "崩", "破产", "失业", "债", "亏损", "衰退"),
    "焦虑": ("焦虑", "担心", "害怕", "恐惧", "不安", "睡不着"),
    "疑问": ("为什么", "难道", "到底", "究竟", "怎么会"),
    "疑惑": ("困惑", "想不通", "看不懂", "奇怪"),
    "失落": ("失落", "难过", "孤独", "委屈", "后悔", "遗憾"),
    "转机": ("转机", "转折", "没想到", "突然", "直到"),
    "升华": ("终于", "明白", "释然", "放下", "从此", "值得"),
    "欢乐": ("开心", "快乐", "惊喜", "轻松", "有趣"),
    "舒缓": ("慢慢", "安静", "平静", "温柔"),
    "平淡": ("日常", "普通", "平常", "每天"),
    "解释": ("其实", "原因", "本质", "意思是"),
    "讲解": ("第一", "第二", "首先", "其次", "也就是说"),
    "措施": ("建议", "办法", "方法", "怎么办"),
    "解决": ("解决", "改善", "行动"),
}

# How many labels reach the pick. Past three the weighting below has a label
# worth a quarter of a point against a full one, which is noise.
MOOD_LIMIT = 3


def label(path):
    """The mood a file carries, read off the front of its name.

    Everything up to the first character that is not Chinese. Taking the front
    rather than searching the whole name is what keeps a Chinese artist credit
    at the END of a filename from being read as a mood.
    """
    stem = Path(path).stem
    end = 0
    while end < len(stem) and "一" <= stem[end] <= "鿿":
        end += 1
    return stem[:end]


def library(directory):
    """Every playable file in `directory`, with its label, sorted by name.

    Sorted so two equally good tracks are not chosen between by the order the
    filesystem happened to list them in. A rebuild has to keep the music it
    had, or `--from audio` quietly rescores a finished video.
    """
    directory = Path(directory) if directory else None
    if directory is None or not directory.is_dir():
        return []
    return sorted(
        ((path, label(path)) for path in directory.iterdir()
         if path.is_file() and path.suffix.lower() in SUFFIXES),
        key=lambda entry: entry[0].name)


def moods_in(script, limit=MOOD_LIMIT):
    """Moods read straight off the script, for when the director labels none."""
    counted = [(sum(script.count(word) for word in words), -i, mood)
               for i, (mood, words) in enumerate(KEYWORDS.items())]
    return [mood for hits, _, mood in sorted(counted, reverse=True) if hits][:limit]


def clean(moods):
    """The labels a plan offers, keeping only ones a filename could carry.

    A word the model invented matches nothing and would only push a real label
    down the ranking, so it is dropped rather than carried.
    """
    if isinstance(moods, str):
        moods = [moods]
    kept = []
    for item in moods or []:
        mood = str(item).strip()
        if mood in MOODS and mood not in kept:
            kept.append(mood)
    return kept[:MOOD_LIMIT]


# Which positional labels were written for which part of a video. A track
# labelled for an opening is the wrong bed for a whole video and the right one
# for its first section, so once a video has sections the same label that
# ranked a track down ranks it up where it belongs.
SECTION_POSITIONS = {"open": ("开头",), "middle": ("提出", "后期"),
                     "close": ("结尾",)}


def score(tag, moods, role=None):
    """How well one label answers a ranked list of moods.

    `role` is where in the video the bed will run - "open", "middle" or
    "close" - when the video is in sections. None means one bed for the whole
    video, which is how every positional label ranks down.
    """
    if not tag:
        return 0.0
    total = 0.0
    for rank, mood in enumerate(moods):
        if mood and mood in tag:
            # Earlier labels weigh more and later ones still count, so a track
            # labelled 紧张危机 beats a plain 紧张 for a script that is both.
            total += 1.0 / (rank + 1)
    if total:
        wanted = SECTION_POSITIONS.get(role, ())
        for position in POSITIONS:
            if position in tag:
                total += 0.25 if position in wanted else -0.25
                break
    return total


def pick(entries, moods, role=None, avoid=()):
    """The best bed among `entries`, or None when none of them fit.

    None is a real answer. A folder holding nothing for this script means a
    video with no music, which beats an arbitrary track running under three
    minutes of narration: the wrong bed is more distracting than no bed, and
    it is the one choice in the video that nobody plays back to check.

    `avoid` is tracks already playing next to this one. They are passed over
    while anything else fits, so two sections do not crossfade into the same
    track, and taken when nothing else does.
    """
    chosen, best, fallback, fallback_best = None, 0.0, None, 0.0
    for path, tag in entries:
        fit = score(tag, moods, role)
        if path in avoid:
            if fit > fallback_best:
                fallback, fallback_best = path, fit
            continue
        if fit > best:
            chosen, best = path, fit
    return chosen or fallback


# Sections of a video, as the director marks them: where the feeling turns.
# Past four the video is a medley; a section shorter than three shots is a
# change of track the viewer hears and cannot account for.
MAX_SECTIONS = 4
MIN_SECTION_SHOTS = 3

# Seconds on the timeline. Music is a cue, not a clock - it is left at natural
# pace when the video is sped up - so these do not divide by the speed.
CROSSFADE = 2.0
FADE_IN = 1.2
FADE_OUT = 2.0


def clean_sections(raw, shots):
    """The director's sections, made safe: [{"from": shot id, "mood": [...]}].

    Sorted, starting at shot 1, no section shorter than MIN_SECTION_SHOTS,
    at most MAX_SECTIONS, each with moods a filename could carry. Anything
    unusable is dropped rather than guessed at - a video with no sections is
    the one-bed video it always was.
    """
    found = {}
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = int(entry.get("from"))
        except (TypeError, ValueError):
            continue
        moods = clean(entry.get("mood"))
        if 1 <= start <= shots and moods:
            found.setdefault(start, moods)
    if not found:
        return []
    ordered = sorted(found.items())
    if ordered[0][0] != 1:
        ordered[0] = (1, ordered[0][1])
    kept = [ordered[0]]
    for start, moods in ordered[1:]:
        if start - kept[-1][0] >= MIN_SECTION_SHOTS and \
                shots - start + 1 >= MIN_SECTION_SHOTS:
            kept.append((start, moods))
    kept = kept[:MAX_SECTIONS]
    return [{"from": start, "mood": moods} for start, moods in kept] \
        if len(kept) > 1 else []


def plan_beds(directory, script, moods=(), sections=(), starts=None,
              total=0.0, fallback=None):
    """The beds under a whole video: [{path, start, end, fade_in, fade_out}].

    Returns (beds, why). With sections, each gets the track that fits its own
    mood and its place - an opening, the middle, the close - and neighbours
    crossfade over CROSSFADE seconds. A section nothing fits keeps the bed
    before it rather than dropping into silence halfway through a video.
    Without sections this is `choose`, one bed start to finish.

    `starts` maps a shot id to where that shot begins on the timeline.
    """
    def one(path, why):
        if path is None:
            return [], why
        return [{"path": str(path), "start": 0.0, "end": float(total),
                 "fade_in": FADE_IN, "fade_out": FADE_OUT}], why

    entries = library(directory)
    sections = list(sections or [])
    if not entries or len(sections) < 2 or not starts:
        return one(*choose(directory, script, moods, fallback=fallback))

    picks = []
    for i, section in enumerate(sections):
        role = ("open" if i == 0 else
                "close" if i == len(sections) - 1 else "middle")
        near = {picks[-1][1]} if picks and picks[-1][1] else set()
        path = pick(entries, section["mood"], role=role, avoid=near)
        if path is None and i == 0:
            path = pick(entries, clean(moods) or moods_in(script), role=role)
        if path is None and picks:
            path = picks[-1][1]             # hold the bed rather than drop it
        picks.append((section, path))
    if all(path is None for _, path in picks):
        return one(*choose(directory, script, moods, fallback=fallback))

    # Neighbours with the same track are one bed, not a crossfade into itself.
    merged = []
    for section, path in picks:
        if merged and merged[-1][1] == path:
            continue
        merged.append((section, path))

    beds, why = [], []
    for i, (section, path) in enumerate(merged):
        boundary = float(starts.get(section["from"], 0.0))
        following = (float(starts.get(merged[i + 1][0]["from"], total))
                     if i + 1 < len(merged) else float(total))
        start = 0.0 if i == 0 else max(0.0, boundary - CROSSFADE / 2)
        end = (float(total) if i + 1 == len(merged)
               else min(float(total), following + CROSSFADE / 2))
        if path is None or end - start <= 0.1:
            continue
        beds.append({"path": str(path), "start": round(start, 3),
                     "end": round(end, 3),
                     "fade_in": FADE_IN if i == 0 else CROSSFADE,
                     "fade_out": FADE_OUT if i + 1 == len(merged) else CROSSFADE})
        why.append(f"from shot {section['from']} {'/'.join(section['mood'])}"
                   f" -> {Path(path).name}")
    return beds, "; ".join(why)


def choose(directory, script, moods=(), fallback=None):
    """Which track goes under this video, and one line saying why.

    Two different failures, answered differently.

    **No mood could be worked out** - no label from the director and nothing
    in the script's own words - means nothing has been decided about this
    video, so it keeps the default bed it would have had before a library
    existed. Silence would be a decision made by an absence.

    **A mood was worked out and no track carries it** means the opposite:
    somebody has said which moods they own and this is not one of them. That
    yields no music, because the wrong bed is more distracting than none.
    """
    def default(reason):
        if fallback and Path(fallback).exists():
            return Path(fallback), f"{reason}; default bed ({Path(fallback).name})"
        return None, f"no music: {reason} and no default bed"

    entries = library(directory)
    if not entries:
        return default("no library")
    wanted = clean(moods) or moods_in(script)
    if not wanted:
        return default("nothing said how the script should feel")
    chosen = pick(entries, wanted)
    if chosen is None:
        return None, f"no music: no track is labelled {'/'.join(wanted)}"
    return chosen, f"{'/'.join(wanted)} -> {chosen.name}"
