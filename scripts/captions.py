"""Cutting a shot's narration into the captions on screen, and timing them.

Two jobs, both of which used to be approximations.

**Where the text breaks.** A caption was a whole sentence, split only at a
full stop, so most portrait captions ran to two lines and the wrap fell
wherever the line filled: 效率一 | 下子提了上来. Now a caption is one line: clauses
are packed until the next would not fit, and a clause wider than a line is
broken where a phrase breaks (see `textkit.wrap`). A comma at the end of a
caption is dropped - a line ending on one reads as unfinished.

**When each caption changes.** Timed by character count alone, a caption
changed early or late by however much the voice's pace varied. The voice's
own pauses are measured off the clip (`audio.speech_pauses`) and each change
of caption is moved to the pause nearest its estimate, so the next line lands
as the next phrase starts. Where nothing was measured - no key, a failed clip
- the estimate stands, which is what there was before.
"""
import textkit

CLAUSE_END = set("，、；：。！？,;:!?…")
# Dropped from the end of a caption; sentence-final marks stay.
TRIM_TAIL = "，、,；;：:"
# What a punctuation mark is worth in the estimate, in characters: the voice
# pauses there, so the time is real even though nothing is read.
PAUSE_WEIGHT = 1.5
# A caption switches this long before the phrase it carries is heard.
LEAD = 0.06
# No caption is shorter than this, whatever the pauses say.
MIN_CAPTION = 0.4


def clauses(text):
    """`text` split after each punctuation mark, the mark kept with its words."""
    parts, buf = [], ""
    for ch in text or "":
        buf += ch
        if ch in CLAUSE_END:
            parts.append(buf)
            buf = ""
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def chunks(text, size, max_width):
    """The narration as single-line captions, in order.

    `size` is the caption's type size in pixels and `max_width` the widest a
    line may be - the layout's own caption geometry.
    """
    def fits(line):
        return textkit.advance(line.rstrip(TRIM_TAIL), size, True) <= max_width

    out, current = [], ""
    for clause in clauses(text):
        if current and fits(current + clause):
            current += clause
            continue
        if current:
            out.append(current)
        current = clause
        if not fits(current):
            # Split into captions of their own only where every break falls
            # where a phrase ends. A break that may be inside a word - a name
            # split 海绵|宝宝 - is worse shown in two halves one after the other
            # than as one caption over two lines.
            lines = textkit.wrap(current, size, max_width)
            if len(lines) > 1 and all(
                    textkit.clean_break(a[-1], b[0])
                    for a, b in zip(lines, lines[1:])):
                out.extend(lines[:-1])
                current = lines[-1]
    if current:
        out.append(current)
    return out


def _weight(chunk):
    marks = sum(1 for ch in chunk if ch in CLAUSE_END)
    return max(1.0, len(chunk) - marks + PAUSE_WEIGHT * marks)


def spans(text, duration, size, max_width, speech=None, pauses=()):
    """[(start, end, caption, first_char, end_char)] for one shot, timed.

    `speech` is (onset, offset) - where the voice starts and stops inside the
    shot - and `pauses` the silences it took in between, both measured off the
    clip. Without them the captions share the whole shot by length, as they
    always did. The first caption starts with the shot and the last one ends
    with it either way, so nothing is ever left without a caption.
    """
    parts = chunks(text, size, max_width)
    if not parts:
        return []
    shown = [p.rstrip(TRIM_TAIL) or p for p in parts]
    starts, cursor = [], 0
    for part in parts:
        index = text.find(part, cursor)
        index = cursor if index < 0 else index
        starts.append(index)
        cursor = index + len(part)
    ends = starts[1:] + [len(text)]
    if len(parts) == 1:
        return [(0.0, duration, shown[0], starts[0], ends[0])]

    onset, offset = speech if speech else (0.0, duration)
    onset = max(0.0, min(float(onset), duration))
    offset = max(onset, min(float(offset), duration))
    if offset - onset < 0.2:
        onset, offset = 0.0, duration
    if duration < MIN_CAPTION * len(parts):
        # Too short to give every caption its minimum: share it by length.
        onset, offset, pauses = 0.0, duration, ()
    weights = [_weight(p) for p in parts]
    total = sum(weights)
    guesses, running = [], 0.0
    for w in weights[:-1]:
        running += w
        guesses.append(onset + (offset - onset) * running / total)

    bounds, used = [], set()
    share = (offset - onset) / len(parts)
    window = max(0.25, min(0.9, 0.45 * share))
    for guess in guesses:
        nearest = None
        for k, (a, b) in enumerate(pauses or ()):
            middle = (float(a) + float(b)) / 2
            if k in used or abs(middle - guess) > window:
                continue
            if nearest is None or abs(middle - guess) < nearest[0]:
                nearest = (abs(middle - guess), k)
        if nearest is None:
            bounds.append(guess)
            continue
        used.add(nearest[1])
        a, b = pauses[nearest[1]]
        bounds.append(max(float(a), float(b) - LEAD))

    # Keep them in order and long enough to read; where a pause would make a
    # caption too short, the estimate is the better answer.
    floor = MIN_CAPTION if duration >= MIN_CAPTION * len(parts) else 0.0
    for i, bound in enumerate(bounds):
        low = (bounds[i - 1] if i else 0.0) + floor
        high = duration - floor * (len(bounds) - i)
        if not low <= bound <= high:
            bounds[i] = min(max(guesses[i], low), max(low, high))
    edges = [0.0] + bounds + [duration]
    return [(edges[i], edges[i + 1], shown[i], starts[i], ends[i])
            for i in range(len(parts))]


def spoken_at(label, text, timed):
    """When `label`'s words are heard in a shot, in seconds, or None.

    A label names something the sentence says, and it lands best as the
    sentence says it. Found as the longest run of the label's characters that
    also appears in the narration - labels paraphrase, so an exact match is
    rare - and placed within its caption by its position there. `timed` is
    what `spans` returned. None when the label shares under two characters
    with the narration in a row, which is most of the time a label is a
    summary rather than a quotation.
    """
    words = "".join(ch for ch in label or "" if not ch.isspace()
                    and ch not in CLAUSE_END)
    best = (0, -1)
    for i in range(len(words)):
        for j in range(len(words), i + 1, -1):
            found = text.find(words[i:j])
            if found >= 0:
                if j - i > best[0]:
                    best = (j - i, found)
                break
    length, found = best
    if length < 2:
        return None
    for start, end, _, first, last in timed:
        if first <= found < last:
            share = (found - first) / max(1, last - first)
            return start + (end - start) * share
    return None
