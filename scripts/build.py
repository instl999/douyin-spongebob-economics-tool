"""Main entry point: a project file in, an editable Jianying draft out.

    python scripts/build.py projects/efficiency_wage.json

The MP4 beside it is the preview. The draft is the deliverable, because
automatic layout is good enough to watch and not good enough to ship without
somebody looking at it.

Every stage writes its result into the project's own output directory and skips
itself when that result is already there and still current, so a re-run after
editing one pose regenerates one sprite, and a re-run after editing the
storyboard re-renders without paying for narration again. `--from <stage>`
redoes that one stage; the stages after it consult their own caches and
re-derive only what actually changed.

Stages: plan -> assets -> voice -> storyboard -> render -> audio -> mux -> draft
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: F401  UTF-8 stdout; see console.py

import assets as assets_mod
import audio as audio_mod
import config
import plan as plan_mod
import styles as styles_mod
import tts as tts_mod
from layout import Layout

ROOT = Path(__file__).resolve().parent.parent
STAGES = ["plan", "assets", "voice", "storyboard", "render", "audio", "mux",
          "draft"]


def log(message=""):
    print(message, flush=True)


class Project:
    def __init__(self, path, out_override=None):
        self.path = Path(path).resolve()
        # utf-8-sig, not utf-8, everywhere a file a person may have edited is
        # read. Windows editors write a BOM by default, and json.loads on a
        # BOM'd file fails with "Unexpected UTF-8 BOM ... line 1 column 1",
        # which the build then blames on the project file being malformed. It
        # is not; it is fine, and utf-8-sig reads plain UTF-8 identically.
        self.data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        self.name = self.data.get("name") or self.path.stem
        self.out = Path(out_override or self.data.get("out")
                        or (ROOT / "out" / self.name)).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "voice").mkdir(exist_ok=True)
        self._speed = None                 # resolved once; see `speed` below

    @property
    def script(self):
        inline = self.data.get("script_text")
        if inline:
            return inline
        ref = self.data.get("script")
        if not ref:
            raise ValueError(f"{self.path.name}: needs `script` or `script_text`")
        p = Path(ref)
        if not p.is_absolute():
            p = (self.path.parent / ref) if (self.path.parent / ref).exists() else ROOT / ref
        return p.read_text(encoding="utf-8-sig")

    @property
    def cast(self):
        # A style key from casts/styles.json, or a path straight to a cast
        # file; missing means the registry's default style.
        ref = self.data.get("cast")
        if ref is not None and not str(ref).strip():
            ref = None
        _, p = styles_mod.resolve(ref)
        return assets_mod.Cast.load(p, root=ROOT / "casts")

    @property
    def style_key(self):
        """Which registered style this project uses, or None for a bare path."""
        key, _ = styles_mod.resolve(self.data.get("cast") or None)
        return key

    @property
    def look(self):
        """Look settings for this project: the shared ones plus its style's."""
        return styles_mod.look(self.style_key)

    @property
    def layout(self):
        return Layout(self.data.get("orientation") or styles_mod.default_orientation(),
                      style=self.style_key)

    @property
    def speed(self):
        """How fast the whole video runs. 1.0 is the baseline, 1.5 the default.

        One number for narration, shot lengths, card holds, dissolves and
        subtitles together - see timing.py for why it cannot be any of those
        on its own. `voice.speed` is still read, so projects written before
        this setting existed keep working, but a top-level `speed` wins.

        Resolved exactly once per run, by `resolve_speed`, and then held: a
        `target_seconds` fit and a configured speed are two answers to the same
        question, and `seconds()` below has to be scaling by the one the
        narration was actually spoken at.
        """
        import timing
        if self._speed is not None:
            return self._speed
        voice = self.get("voice", {}) or {}
        chosen = self.get("speed", voice.get("speed", timing.DEFAULT_SPEED))
        return timing.clamp(chosen)

    @speed.setter
    def speed(self, value):
        import timing
        self._speed = timing.clamp(value)

    def seconds(self, key, default):
        """A configured duration in timeline time.

        Project files are written at the 1.0x baseline - `tail_pad: 0.35` is
        0.35 seconds of tail in a video running at natural pace - and this is
        the one place that turns one into the length it actually holds for.
        """
        import timing
        return timing.scale(float(self.get(key, default)), self.speed)

    def resolve_speed(self):
        """The global speed to build at, honouring `target_seconds` when set.

        Returns (speed, report). An unreachable target is reported rather
        than acted on: a 95-second script cannot become a 60-second video
        by running faster alone, and saying so is more use than silently
        producing something 35 seconds too long.
        """
        import timing
        target = self.get("target_seconds")
        if target is None:
            # Nothing to fit: hold the configured speed so `seconds()` and the
            # voice stage cannot later be handed a different one.
            self._speed = self.speed
            return self._speed, None
        result = timing.fit_to_target(
            self.script, float(target),
            shot_seconds=float(self.get("shot_seconds", 5.0)),
            tail_pad=float(self.get("tail_pad", 0.35)),
            title_seconds=float(self.get("title_seconds", 2.6)),
            ending_seconds=float(self.get("ending_seconds", 4.0)),
            speed=self.speed)
        self.speed = result["speed"]
        return self.speed, result

    def get(self, key, default=None):
        return self.data.get(key, default)


# --- stages ----------------------------------------------------------------

# What each stage's output feeds. Forcing a stage has to clear the cached
# outputs of everything downstream, or the re-run quietly reuses them:
# `--from storyboard` skipped the render and reused video_mute.mp4, so an
# edited plan produced a byte-identical video and looked like the edit had
# done nothing.
DOWNSTREAM = {
    "plan": ["storyboard.json", "video_mute.mp4"],
    "assets": ["video_mute.mp4"],
    "voice": ["storyboard.json", "video_mute.mp4"],
    "storyboard": ["video_mute.mp4"],
    "render": [],
    "audio": [],
    "mux": [],
    "draft": [],
}


def invalidate(project, forced):
    """Delete cached outputs that a forced stage has just made stale."""
    stale = {name for stage in forced for name in DOWNSTREAM.get(stage, [])}
    for name in sorted(stale):
        target = project.out / name
        if target.exists():
            target.unlink(missing_ok=True)
            log(f"  {name} is stale after --from {forced[0]}, removed")


def stage_plan(project, force=False):
    target = project.out / "plan.json"
    if target.exists() and not force:
        log("  plan.json already present - reusing")
        plan = json.loads(target.read_text(encoding="utf-8-sig"))
        # A person editing a drawing's `shows` is asking for it to be drawn
        # again, and the filename that decides whether it is was only ever
        # computed when the director answered. Re-derived here, so the edit
        # reaches the assets stage as a new file instead of "already here".
        changes = plan_mod.refresh_drawings(plan, project.cast)
        for line in changes:
            log(f"  ~ {line}")
        if changes:
            target.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        missing = [d["asset"] for d in plan.get("drawings") or []
                   if not (project.out / d["asset"]).exists()]
        log(f"  {len(plan.get('drawings') or [])} drawings in the plan, "
            f"{len(missing)} not drawn yet")
        return plan

    cast = project.cast
    shot_seconds = float(project.get("shot_seconds", 5.0))
    if config.have_ark():
        result = plan_mod.direct(project.script, cast, shot_seconds,
                                 model=project.get("director_model"),
                                 orientation=project.get("orientation")
                                 or styles_mod.default_orientation())
    else:
        log("  ! ARK_API_KEY not set - falling back to the offline plan")
        result = plan_mod.offline_plan(project.script, cast, shot_seconds)

    for problem in result.get("problems", []):
        log(f"  ! {problem}")
    drawings = result.get("drawings") or []
    log(f"  {len(result['scenes'])} shots, {len(drawings)} drawings to make")
    # Drawings are the whole bill now - nothing comes from a library - so what
    # this video is about to spend is listed rather than buried in `problems`.
    for spec in drawings:
        who = "+".join(spec["who"]) if spec["who"] else "prop"
        log(f"  + {who:16} {spec['shows'][:66]}")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return result


def stage_assets(project, plan, force=False):
    cast = project.cast
    lay = project.layout
    library = assets_mod.Library(cast, log=log)

    background = project.out / "background.png"
    _, made = library.build_background(background, lay.image_size, force=force)
    log(f"  background.png  {'generated' if made else 'cached'}")

    # One reference per character, drawn once and committed. It is the only
    # thing that survives a video, and without it the same character drifts
    # from shot to shot - which is worse than the sameness that dropping the
    # library was meant to cure.
    for name in sorted({who for spec in plan.get("drawings") or []
                        for who in spec.get("who") or []}):
        try:
            _, made = library.build_anchor(name, assets_mod.SPRITE_SIZE)
            if made:
                log(f"  anchor          drew {name} - this cast's reference")
        except Exception as exc:
            log(f"  ! no anchor for {name} ({type(exc).__name__}); "
                f"{name} will be drawn from description alone")

    drawings = plan.get("drawings") or []
    failed = []
    for i, spec in enumerate(drawings, 1):
        try:
            _, made = library.build_drawing(spec, project.out,
                                            assets_mod.SPRITE_SIZE, force=force)
        except Exception as exc:
            failed.append((spec["asset"], f"{type(exc).__name__}: {exc}"))
            continue
        log(f"  [{i}/{len(drawings)}] {spec['asset']}  "
            f"{'drawn' if made else 'already here'}")
    for name, err in failed:
        log(f"  ! {name}: {err}")
    title_text = project.get("title") or plan.get("title") or ""
    if title_text and project.get("ai_title", True):
        card, note = library.build_title_card(
            title_text, project.out / "title_card.png", lay.image_size,
            force=force)
        log(f"  title_card.png  {note}")
        plan["_title_card_image"] = "title_card.png" if card else None

    setting = (plan.get("setting") or "").strip()
    if setting and project.get("fallback_setting", True):
        try:
            _, made = library.build_setting(
                setting, project.out / "setting.png", lay.image_size, force=force)
            log(f"  setting.png     {'generated' if made else 'cached'}  {setting}")
        except Exception as exc:
            # A missing backdrop costs the shots their room, not the run - but
            # a catch this broad turns a typo into something that reads like a
            # service outage, so the kind of error is part of the message.
            log(f"  ! backdrop could not be generated "
                f"({type(exc).__name__}): {str(exc)[:90]}")

    reconcile_sprites(project, plan, cast)
    return background


def reconcile_sprites(project, plan, cast):
    """Stand in another drawing for any that did not reach the disk.

    A drawing can fail to arrive - a quota wall, a content filter, a dropped
    connection - and the renderer's answer to a missing PNG is to skip that
    element. That is the wrong answer here: a shot that asked for two figures
    and got neither renders as an empty plate.

    The stand-in used to come from the shared catalogue. There is none now, so
    it comes from this video's own drawings - another picture of the same
    character, which is a closer match than a library pose ever was, since both
    were drawn for this script.

    Matched on who a drawing *shows*, which the element carries, rather than on
    its filename. Every pair's name begins "duo_", so parsing the name put a
    drawing of Krabs and SpongeBob in for one of Patrick and Squidward.
    """
    present = {path.name for path in project.out.glob("*.png")}
    shows = {d["asset"]: tuple(d.get("who") or ())
             for d in plan.get("drawings") or []}
    for scene in plan.get("scenes") or []:
        kept, on_screen = [], set()
        for el in scene.get("elements") or []:
            asset = el.get("asset")
            who = tuple(el.get("who") or shows.get(asset) or ())
            if not asset or asset in present:
                on_screen |= set(who)
                kept.append(el)
                continue
            if not who:
                # A prop is only itself. Another object drawn for a different
                # sentence is not a substitute for this one.
                log(f"  ! {asset} was not drawn, and a prop has no stand-in")
                continue
            # One character twice in a shot is a worse picture than one once.
            swap = next((name for name in sorted(present)
                         if name != asset and shows.get(name) == who
                         and not (set(who) & on_screen)), None)
            if swap:
                log(f"  ! {asset} was not drawn, standing in {swap}")
                on_screen |= set(who)
                kept.append(dict(el, asset=swap, shows=shows.get(swap, ""),
                                 rel=cast.relative_height(swap)))
            else:
                log(f"  ! {asset} was not drawn and has no stand-in, dropped")
        scene["elements"] = kept


TITLE_SFX_LEAD = audio_mod.TITLE_SFX_LEAD
# A name in the sfx library, not a path: that is what a cue is. `""` in a
# project's `opening_sfx` turns it off.
# The supplied opening cue, by name in assets/sfx. It is shipped, not
# generated: gen_sfx builds every other cue in that folder, and this one is a
# specific sound the videos are known by.
OPENING_SFX = "opening_dong"
# Unity. The opening cue plays exactly as supplied - no gain, no normalising,
# no levelling against the narration. Earlier versions tuned this (0.7, then
# 0.42 once the cue was generated at -12 dB) and both were wrong for the same
# reason: the cue is the user's own file and is meant to sound the way it
# sounds.
OPENING_GAIN = 1.0


# The longest the card may hold before shot 1. The director brief asks for a
# title of ten characters or fewer, and this file's own findings say what that
# is worth: a prompt is a request, check the output. A thirty-character title
# reads for five seconds, and nothing stopped the card growing to fit it.
#
# Baseline seconds, like every duration written in this file: the callers below
# divide by the global speed. A cap that did not would let the opening keep its
# full length while everything after it ran 1.5x faster, which is the whole
# mismatch the setting exists to prevent.
MAX_TITLE_SLOT = 4.5


# How many of the script's opening sentences the title is checked against.
# Two: sentence one is usually a hook and sentence two is the line the title
# was taken from, and a title repeated as the second thing said is the same
# stutter as one repeated as the first.
TITLE_ECHO_SHOTS = 2

# A restatement shares most of the title's characters AND a fair share of its
# character pairs. Characters alone match any two Chinese sentences built from
# the same common words; pairs alone miss a title whose clause the narration
# reorders, which is how an opening line usually restates a headline.
TITLE_ECHO_CHARACTERS = 0.8
TITLE_ECHO_PAIRS = 0.5

# Below this a title is too short for those ratios to mean anything: two
# characters are wholly contained in half the sentences ever written.
TITLE_ECHO_MIN_LENGTH = 4

TITLE_ECHO_NOISE = set("，,、。.；;：:！!？?～~—-…" + chr(34) + "'“”‘’()（）《》〈〉[]【】")


def _echo_key(text):
    """`text` reduced to the characters that carry its meaning."""
    return "".join(c for c in text
                   if not c.isspace() and c not in TITLE_ECHO_NOISE)


def _pairs(text):
    return {text[i:i + 2] for i in range(len(text) - 1)}


def title_is_echo(title, narrations, lookahead=TITLE_ECHO_SHOTS):
    """True when the script's opening already says what the title says.

    The card is read aloud, and the director writes the title from the script
    it was handed - so the title and the first thing said are often the same
    sentence, delivered half a second apart. Speaking both opens the video on
    a stutter.

    Matched on meaning, not characters. The director paraphrases, so a repeat
    is rarely a prefix: a title comes back with its clauses swapped and a word
    changed, matches no prefix of anything, and is still the same sentence
    twice to anyone watching. Checked across the opening pair because the line
    the title came from lands in sentence two as often as in sentence one.

    The ratios are deliberately strict. Silencing a title the script never
    says loses the opening line outright, which is a worse video than the
    stutter this prevents, so a near miss is left to be spoken.
    """
    wanted = _echo_key(title or "")
    if not wanted:
        return False
    opening = _echo_key("".join(narrations[:max(1, lookahead)]))
    if not opening:
        return False
    # Said outright, either way round. No length guard - a quotation is a
    # quotation however short.
    if wanted in opening or opening in wanted:
        return True
    if len(wanted) < TITLE_ECHO_MIN_LENGTH:
        return False
    shared = sum(c in opening for c in wanted) / len(wanted)
    pairs = _pairs(wanted)
    overlap = (len(pairs & _pairs(opening)) / len(pairs)) if pairs else 0.0
    return shared >= TITLE_ECHO_CHARACTERS and overlap >= TITLE_ECHO_PAIRS


def title_voice_fits(spoken, tail, lead=TITLE_SFX_LEAD, cap=MAX_TITLE_SLOT):
    """Whether reading the title aloud leaves shot 1 starting in time."""
    return lead + spoken + tail <= cap


def title_slot(configured, spoken, tail, lead=TITLE_SFX_LEAD, cap=MAX_TITLE_SLOT):
    """How long the title card holds once it has a line to read.

    The configured length is a floor, not the answer. A title is read aloud
    now, and any title past about eight characters does not fit in the 2.6 s
    the card used to hold for - it would cut to shot 1 mid-word.

    It is not an unbounded ceiling either. Callers drop the voice-over rather
    than let the card run past `cap`; this clamps as a second line of defence.
    """
    if spoken <= 0:
        return configured
    return min(max(configured, lead + spoken + tail), max(configured, cap))


def stage_voice(project, plan, force=False, speed=None):
    voice = project.get("voice", {}) or {}
    speaker = voice.get("speaker")
    speed = float(speed if speed is not None else project.speed)
    index_path = project.out / "voice" / "index.json"
    index = (json.loads(index_path.read_text(encoding="utf-8-sig"))
             if index_path.exists() and not force else {})

    degraded = 0
    # The title card is read aloud like any other line. It used to hold a
    # silent slot in the narration track, so the video opened on two and a half
    # seconds of nothing while the card sat there.
    title_text = project.get("title") or plan.get("title") or ""
    # Skipped here rather than only at placement, because the clip costs a
    # call to the speech service whether or not anything lays it down.
    if title_text and title_is_echo(
            title_text, [s["narration"] for s in plan["scenes"]]):
        log("  title voice skipped: the script's opening already says it")
        title_text = ""
    jobs = [("title", title_text)] if title_text else []
    jobs += [(str(i), scene["narration"])
             for i, scene in enumerate(plan["scenes"], 1)]

    for key, text in jobs:
        cached = index.get(key)
        # A shot that fell back to silence is cached like any other, so without
        # this a transient network fault becomes a permanent hole: every later
        # run sees matching text and skips it. Degraded entries are retried
        # whenever narration is actually available.
        stale = cached and cached.get("degraded") and config.have_tts()
        # A clip spoken at another speed is the wrong clip, however well it
        # matches the text. Without this, changing `speed` on a built project
        # re-cut every shot to the new pace and kept the old narration, which
        # is exactly the mismatch the setting exists to remove - and the run
        # would report eight cached shots and look entirely successful.
        was = float(cached.get("speed", 1.0)) if cached else speed
        respeed = abs(was - speed) > 1e-6
        if (cached and not stale and not respeed and cached.get("text") == text
                and Path(cached["path"]).exists()):
            continue
        label = "title" if key == "title" else f"shot {key}"
        if stale:
            log(f"  {label}: retrying (was silent from an earlier failure)")
        elif respeed and cached and cached.get("text") == text:
            log(f"  {label}: re-reading at {speed:.2f}x (was {was:.2f}x)")
        out = project.out / "voice" / (
            "title.mp3" if key == "title" else f"scene_{int(key):02d}.mp3")
        try:
            result = tts_mod.synth(text, out, speaker=speaker, speed=speed)
        except tts_mod.TTSError as exc:
            log(f"  ! {label}: {exc}")
            log("    falling back to an estimated duration for this shot")
            result = tts_mod._silent(text, out, speed)
        if result["degraded"]:
            degraded += 1
        index[key] = {"text": text, "path": str(result["path"]),
                      "duration": result["duration"], "speed": speed,
                      "words": result["words"], "degraded": result["degraded"]}
        log(f"  {label:>8}: {result['duration']:5.2f}s"
            f"{'  (estimated - no TTS)' if result['degraded'] else ''}")

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    if degraded:
        reason = ("set ARK_API_KEY for narration" if not config.have_tts()
                  else "the calls above failed after retrying; re-run "
                       "`--from voice` to fill just those shots")
        log(f"  ! {degraded} shot(s) have no real narration - {reason}")
    return index


# A shot shorter than this has no room for anything to arrive; the element
# would still be fading when the shot ends. Baseline seconds - `_stagger` is
# given the speed and divides both of these, so a 1.5x video does not silently
# lose every staggered label to a floor written for 1.0x shots.
STAGGER_MIN_SHOT = 3.6
# How far into the shot emphasis text lands. A quarter in is after the sentence
# has started saying the thing, which is the order an explainer wants: hear it,
# then see it. A fraction of the shot, so it needs no scaling: it is already
# expressed in the only clock that matters here.
STAGGER_FRACTION = 0.26
STAGGER_MAX = 1.4


def _paced_look(look, speed):
    """The style's look with its durations put on the video's clock.

    Only `timing` moves. Sizes, colours and clearances are measured in pixels
    and fractions of the frame, and a frame does not get smaller because the
    video runs faster.
    """
    import timing as timing_mod
    paced = dict(look)
    paced["timing"] = {k: timing_mod.scale(v, speed)
                       for k, v in (look.get("timing") or {}).items()}
    return paced


def _plate(project, plan):
    """Which image the whole video sits on: the script's own room, or the cast's.

    A generated setting is the *plate*, not a prop. The first version made it a
    panel - a wall-shaped element pasted over the cast background - and that was
    wrong twice over. A panel box is about 2.7:1, so cover-cropping a 16:9 room
    into it cut away the top and the floor and left the blank middle: the
    Krusty Krab kitchen arrived as a cream slab. And a slab is what it read as,
    because the cast's own plate still showed around its edges.

    As the plate it is full-bleed, behind everything, identical in every shot -
    so the frame cache and the "background is static" check hold unchanged, and
    the draft exporter picks it up without knowing a setting exists.
    """
    if not project.get("fallback_setting", True) or not (
            plan.get("setting") or "").strip():
        return "background.png"
    if (project.out / "setting.png").is_file():
        return "setting.png"
    # The plan asked for a room and there is no room: generation failed, or an
    # earlier stage was skipped past. Falling back to the cast plate is right,
    # doing it quietly is not - the video simply comes out somewhere else.
    log("  ! no setting.png; falling back to the cast's own plate")
    return "background.png"


def _stagger(elements, duration, speed=1.0):
    """Let the emphasis text arrive rather than being there from the start.

    `appear` has existed since the first renderer, is faded in over
    `element_fade`, and across seven finished videos was used exactly **zero**
    times - so every shot was a tableau held for five or six seconds with
    nothing developing in it. The director can still set it deliberately; this
    guarantees the floor, because the measurement says asking has not worked.

    Only labels and balloons, and only when there is more than one thing on
    screen: a lone label that is not there yet is an empty frame.
    """
    import timing
    if duration < timing.scale(STAGGER_MIN_SHOT, speed):
        return
    text = [el for el in elements if el.get("type") in ("label", "bubble")]
    if not text or len(elements) < 2:
        return
    when = round(min(timing.scale(STAGGER_MAX, speed),
                     duration * STAGGER_FRACTION), 2)
    for el in text:
        el.setdefault("appear", when)


def _ink(storyboard, project, lay, look):
    """Pick each label's outline from the plate it will sit on.

    Decided once, here, and written into the storyboard - the same way `look`
    and `panel_color` are carried - because the renderer and the draft exporter
    both draw these and had a white outline hardcoded in each. Two copies of an
    appearance decision is how the placement arithmetic ended up with three
    different default y values, and this is the same shape of mistake caught
    before it is made.

    Only shots that already have a plate are touched, and a label whose author
    set `outline` explicitly keeps it.
    """
    import render as render_mod
    labels = [el for scene in storyboard.get("scenes") or []
              for el in scene.get("elements") or []
              if el.get("type") == "label" and not el.get("outline")]
    if not labels:
        return

    pinned = (look.get("text") or {}).get("label_outline", "auto")
    if pinned != "auto":
        for el in labels:
            el["outline"] = list(pinned)
        return

    name = (storyboard.get("video") or {}).get("background")
    if not name or not (project.out / name).is_file():
        return
    options = [tuple(look["caption"]["fill"]), tuple(look["caption"]["stroke_fill"])]
    assets = render_mod.Assets(project.out)
    plate = render_mod.plate_for(assets, name, lay)
    for el in labels:
        image = render_mod.build_element_image(el, assets, lay)
        el["outline"] = list(render_mod.outline_against(
            plate, el, image, lay, options))


def stage_storyboard(project, plan, voice_index):
    import timing
    lay = project.layout
    speed = project.speed
    # The look's own durations are baseline seconds like everything else, and
    # this is where they become timeline time. Scaled once, here, and carried
    # in the storyboard - the renderer, the draft exporter and the checker all
    # read them from there, so none of the three can be left at 1.0 while the
    # other two run faster.
    look = _paced_look(project.look, speed)
    tail = project.seconds("tail_pad", 0.35)
    scenes, pieces, srt = [], [], []
    clock = 0.0
    lead = timing.scale(TITLE_SFX_LEAD, speed)
    cap = timing.scale(MAX_TITLE_SLOT, speed)

    title_text = project.get("title") or plan.get("title") or ""
    title_seconds = project.seconds("title_seconds", 2.6) if title_text else 0.0
    # The clip the title actually gets, after the fit rule below has had its
    # say - not whatever the voice index happens to hold. It is carried in the
    # storyboard because the mix is not the only thing that lays this audio:
    # the draft exporter does too, and deriving it there from the index again
    # meant the two disagreed whenever the rule fired.
    title_voice = None
    if title_seconds:
        # The card holds for as long as its own line needs, never less than the
        # configured minimum. Reading it aloud inside a fixed 2.6 s would clip
        # any title longer than about eight characters, and the card would cut
        # to shot 1 mid-word.
        entry = voice_index.get("title", {})
        spoken = float(entry.get("duration") or 0.0)
        audio_path = entry.get("path")
        if spoken and title_is_echo(
                title_text, [s["narration"] for s in plan["scenes"]]):
            # A clip left over from a build before the title changed, or from
            # one made before this rule existed. The index still holds it, and
            # `--from storyboard` would otherwise speak it.
            log("  title voice dropped: the script's opening already says it")
            spoken, audio_path = 0.0, None
        if spoken and not title_voice_fits(spoken, tail, lead, cap):
            # Too long to read before shot 1 has to start. The card stays; only
            # its voice goes. Clamping instead would cut the title mid-word.
            log(f"  title voice dropped: {spoken:.1f}s of speech would hold the "
                f"card past {cap:.1f}s - ask the director for a shorter title")
            spoken, audio_path = 0.0, None
        title_seconds = title_slot(title_seconds, spoken, tail, lead, cap)
        if audio_path and Path(audio_path).exists():
            title_voice = str(audio_path)
        pieces.append((title_voice, title_seconds, lead))
        # Deliberately NOT an SRT cue, even though it is now spoken. The draft
        # imports the SRT as a native subtitle track, so a cue here printed the
        # title a second time in small white text under the calligraphy card
        # that already says it. The card is the title's caption.
        clock += title_seconds

    last_shot = len(plan["scenes"])
    for i, scene in enumerate(plan["scenes"], 1):
        entry = voice_index.get(str(i), {})
        # Every shot carries a tail so its neighbour does not start on the
        # same breath - except the last one, which has no neighbour. Held
        # there it was simply dead air: a third of a second of silence after
        # the final subtitle, before the closing card or before the file
        # stops, on every video this pipeline has ever made.
        shot_tail = 0.0 if i == last_shot else tail
        # The estimate is only reached when a shot has no real clip. It has to
        # be asked at the same speed the rest of the video runs at, or the one
        # shot the service dropped becomes the one shot still at 1.0x.
        duration = float(entry.get("duration") or
                         tts_mod.estimate_duration(scene["narration"], speed)) + shot_tail
        audio_path = entry.get("path")
        pieces.append((audio_path if audio_path and Path(audio_path).exists() else None,
                       duration))
        built = {"id": i, "subtitle": scene["narration"],
                 "framing": scene.get("framing", "medium"),
                 "elements": scene["elements"], "duration": duration}
        _stagger(built["elements"], duration, speed)
        # The director's reading of the sentence carries through. Sound cues
        # are chosen from the emotion and the action, and without this the
        # storyboard - which is all the cue planner sees - would have nothing
        # to choose on but which props happen to be present.
        if scene.get("beat"):
            built["beat"] = scene["beat"]
        spans = _caption_spans(scene["narration"], duration)
        built["captions"] = [{"text": t, "start": s, "end": e} for s, e, t in spans]
        for s, e, t in spans:
            srt.append((clock + s, clock + e, t))
        scenes.append(built)
        clock += duration

    ending = plan.get("ending") or {}
    ending_text = ending.get("text") or ""
    ending_seconds = project.seconds("ending_seconds", 4.0) if ending_text else 0.0
    if ending_seconds:
        pieces.append((None, ending_seconds))
        clock += ending_seconds

    storyboard = {
        "video": {
            "orientation": (project.get("orientation")
                            or styles_mod.default_orientation()),
            "width": lay.width, "height": lay.height,
            "fps": int(project.get("fps", 30)),
            "background": _plate(project, plan),
            # A project's own `dissolve` is written at the baseline like the
            # style's, so it goes through the same division; the style's has
            # already had it applied by `_paced_look`.
            "dissolve": (project.seconds("dissolve", 0.5)
                         if project.get("dissolve") is not None
                         else float(look["timing"]["dissolve"])),
            "crf": int(project.get("crf", 20)),
            "preset": project.get("preset", "veryfast"),
            # What the whole video was built at. Nothing downstream needs to
            # scale anything by it - every duration here is already timeline
            # time - it is recorded so a storyboard says what it is.
            "speed": speed,
            "panel_color": list(project.cast.panel_color),
            # Carried with the storyboard so the renderer, the draft exporter
            # and the draft checker all use one set of numbers. Looking them up
            # again from casts/styles.json would let an edit between render and
            # export silently put the two out of step.
            "look": look,
        },
        "scenes": scenes,
    }
    if title_text:
        storyboard["title_card"] = {
            "text": title_text, "duration": title_seconds, "style": "title",
            "size": float(project.get("title_size", 0.082))}
        # `voice` is a decision, and it is written either way - `null` rather
        # than an absent key - so a storyboard says plainly that a title is
        # deliberately silent instead of leaving a reader to wonder whether
        # the field was forgotten.
        #
        # It is the only record that the rule fired: a title too long to read
        # before shot 1 keeps its type and loses its voice. The draft exporter
        # went back to the voice index instead, found the clip still sitting
        # there, and spoke a title the MP4 beside it was silent for, truncated
        # into a card sized for no speech at all.
        storyboard["title_card"]["voice"] = title_voice
        if title_voice:
            # How far into the card the voice starts, and meaningless without
            # one. Carried rather than re-read from audio.TITLE_SFX_LEAD,
            # which is a baseline number and would put the draft's title
            # 0.45s in while the MP4's sat at 0.30s.
            storyboard["title_card"]["lead"] = lead
        card_image = plan.get("_title_card_image")
        if card_image and (project.out / card_image).exists():
            storyboard["title_card"]["image"] = card_image
    if ending_text:
        storyboard["ending_card"] = {"text": ending_text,
                                     "highlight": ending.get("highlight"),
                                     "duration": ending_seconds,
                                     "size": float(project.get("ending_size", 0.062))}

    # Sprite sizes are only knowable now the PNGs exist, so collisions are
    # found and spread apart here rather than guessed at by the director.
    import checks as checks_mod
    import render as render_mod
    findings = checks_mod.inspect(
        storyboard, render_mod.Assets(project.out), lay, repair=True)
    for line in findings:
        log(f"  {line}")

    _ink(storyboard, project, lay, look)

    # Cues are planned last, after both cards are attached and after the
    # layout repair has moved things. Planned any earlier, build_timeline sees
    # no title card, so every cue lands 2.6s early and the ending sting is
    # never placed at all - which is precisely the kind of silent, plausible
    # wrongness this pipeline is full of traps for.
    import sfx as sfx_mod
    storyboard["sound_cues"] = [
        [round(when, 3), name] for when, name, _ in sfx_mod.plan(
            storyboard, [s["duration"] for s in scenes],
            cast=project.cast, look=look)]

    # The title card's stinger is a cue at t=0 like any other, recorded here
    # rather than laid into the mix separately. `carried` is read by BOTH the
    # mix and the draft writer, so a cue that lives anywhere else is a cue the
    # two can disagree about - which is the failure the cue list was moved onto
    # the storyboard to prevent in the first place.
    opening = project.get("opening_sfx", OPENING_SFX)
    for name, (paths, winner) in sfx_mod.duplicate_cues().items():
        others = ", ".join(p.name for p in paths if p != winner)
        log(f"  ! cue '{name}' exists more than once; using {winner.name} "
            f"and ignoring {others}")
    if title_text and opening and opening not in sfx_mod.library():
        # Supplied, never synthesised. There is no fallback to generate one:
        # the opening cue is a specific sound the videos are known by, and a
        # stand-in that merely resembles it is worse than saying it is missing.
        log(f"  ! opening cue '{opening}' is not in assets/sfx - the video "
            f"will open without one")
    if title_text and opening and opening in sfx_mod.library():
        storyboard["sound_cues"].insert(
            0, [0.0, opening, float(project.get("opening_volume", OPENING_GAIN))])

    (project.out / "storyboard.json").write_text(
        json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
    audio_mod.write_srt(srt, project.out / f"{project.name}.srt")
    return storyboard, pieces, clock


def _caption_spans(text, duration):
    """Split a shot's narration into on-screen captions timed by length."""
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。！？；!?;" and len(buf.strip()) >= 10:
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    if not parts:
        return []
    if len(parts) == 1:
        return [(0.0, duration, parts[0])]
    weights = [max(1, len(p)) for p in parts]
    total = sum(weights)
    spans, cursor = [], 0.0
    for part, w in zip(parts, weights):
        span = duration * w / total
        spans.append((cursor, cursor + span, part))
        cursor += span
    return spans


# The code that decides what a frame looks like. A change to any of these is a
# different picture from the same storyboard, so it is part of what a cached
# render was made from.
RENDER_SOURCES = ("render.py", "textkit.py", "layout.py")


def render_fingerprint(project, storyboard):
    """Everything the mute render was made from, as one short hash.

    The storyboard, every file it points at, and the renderer's own code. A
    cached render used to be reused whenever it existed and was readable, so
    an edited plan re-derived its storyboard and its draft and then shipped
    the old picture: moving a character and changing a label produced a
    byte-identical MP4, a draft that disagreed with it, and a check that said
    the two matched because it compared the draft with the storyboard.
    """
    import hashlib
    digest = hashlib.sha256(json.dumps(
        storyboard, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    video = storyboard.get("video") or {}
    names = {video.get("background", "background.png")}
    for scene in storyboard.get("scenes") or []:
        names |= {el["asset"] for el in scene.get("elements") or []
                  if el.get("asset")}
    for key in ("title_card", "ending_card"):
        image = (storyboard.get(key) or {}).get("image")
        if image:
            names.add(image)
    for name in sorted(names):
        path = project.out / name
        if path.exists():
            stat = path.stat()
            digest.update(f"{name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        else:
            digest.update(f"{name}:missing".encode())
    here = Path(__file__).resolve().parent
    for source in RENDER_SOURCES:
        digest.update((here / source).read_bytes())
    return digest.hexdigest()[:16]


def stage_render(project, storyboard, force=False):
    import render as render_mod
    target = project.out / "video_mute.mp4"
    record = project.out / "video_mute.json"
    fingerprint = render_fingerprint(project, storyboard)
    if target.exists() and not force:
        try:
            made_from = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            made_from = {}
        # Atomic writes stop an interrupted run leaving a truncated file, but a
        # cached render can still be damaged by something outside this process.
        # One ffprobe is cheap, and turns `moov atom not found` three stages
        # later into a line that says what happened and fixes itself.
        expected = sum(s["duration"] for s in storyboard.get("scenes", []))
        readable = tts_mod.probe_duration(target)
        current = made_from.get("fingerprint") == fingerprint
        if current and readable > 0 and readable >= expected * 0.5:
            log("  video_mute.mp4 is current - reusing")
            return target
        log("  video_mute.mp4 was made from a different storyboard - "
            "rendering it again" if readable > 0 and not current else
            f"  video_mute.mp4 is unreadable or truncated "
            f"({readable:.1f}s on disk) - rendering it again")
        target.unlink(missing_ok=True)
    durations = [s["duration"] for s in storyboard["scenes"]]
    renderer = render_mod.Renderer(storyboard, project.out)
    started = time.time()

    # Carriage-return progress is unreadable in a redirected log, so only
    # use it on a terminal; otherwise report at intervals.
    live = sys.stdout.isatty()

    def progress(done, total):
        pct = 100.0 * done / max(total, 1)
        if live:
            print(f"\r  frames {done}/{total} ({pct:4.1f}%)", end="", flush=True)
        elif done % max(total // 10, 1) < renderer.fps:
            log(f"  frames {done}/{total} ({pct:4.1f}%)")

    with Atomic(target) as partial:
        total, frames = renderer.render(partial, durations, progress=progress)
    # Written only once the render has landed, so an interrupted run leaves
    # a record that matches nothing rather than one vouching for a half file.
    record.write_text(json.dumps({"fingerprint": fingerprint}),
                      encoding="utf-8")
    if live:
        print()
    log(f"  {frames} frames / {total:.1f}s rendered in {time.time() - started:.1f}s")
    return target


def _resolve(value, default=None):
    """A project path, relative to the repository unless it is absolute."""
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def stage_audio(project, pieces, total, storyboard=None, script="", moods=()):
    import music as music_mod

    narration = audio_mod.build_narration(pieces, project.out / "narration.wav")
    # `bgm` names one track and is not second-guessed; `bgm_library` is a
    # folder to choose from. Neither set means the library at its default
    # location, falling back to the single bed that has always shipped.
    named = project.get("bgm", None)
    if named is not None:
        # Present and empty is how a project has always said "no music", and
        # it stays a choice rather than an invitation to search a folder.
        chosen = _resolve(named) if named else None
        bgm = chosen if chosen and chosen.exists() else None
        log(f"  music: {bgm.name if bgm else 'none (set by the project)'}")
    else:
        bgm, why = music_mod.choose(
            _resolve(project.get("bgm_library"), ROOT / "assets" / "bgm"),
            script, moods, fallback=ROOT / "assets" / "bgm_default.wav")
        log(f"  music: {why}")
    cues = []
    if storyboard is not None:
        import sfx as sfx_mod
        cues = sfx_mod.carried(storyboard)
        if cues:
            log(f"  {len(cues)} sound cue(s): {sfx_mod.describe(cues)}")

    track = project.out / "audio.wav"
    with Atomic(track) as partial:
        audio_mod.mix(narration, partial, total, bgm=bgm,
                      bgm_volume=float(project.get("bgm_volume",
                                                   audio_mod.BGM_VOLUME)),
                      cues=cues,
                      cue_volume=float(project.get(
                          "sfx_volume", project.look["sound"].get("gain", 0.34))))
    return track


class Atomic:
    """Write to a sibling `.partial` file and rename only on success.

    A render killed part-way used to leave a truncated video_mute.mp4 that the
    next run happily reused, and ffmpeg then died on `moov atom not found` -
    a message that tells an operator nothing about which file to delete. With
    the rename deferred to the end, an interrupted run leaves nothing behind
    for the next one to pick up.
    """

    def __init__(self, target):
        self.target = Path(target)
        # The extension has to stay on the end: ffmpeg picks the container
        # from it, and `video_mute.mp4.partial` makes it guess and fail.
        self.partial = self.target.with_name(
            f"{self.target.stem}.partial{self.target.suffix}")

    def __enter__(self):
        self.partial.unlink(missing_ok=True)
        return self.partial

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None and self.partial.exists():
            self.target.unlink(missing_ok=True)
            self.partial.replace(self.target)
        else:
            self.partial.unlink(missing_ok=True)
        return False


def report_usage(log=log):
    """What this run actually spent on the APIs."""
    import ark
    import tts as tts_mod
    a, t = ark.USAGE, tts_mod.USAGE
    if not any([a["images"], a["text_calls"], a["vision_calls"], t["clips"]]):
        log("  api: nothing was generated, everything came from cache")
        return
    parts = []
    if a["images"]:
        parts.append(f"{a['images']} image(s)")
    if a["text_calls"]:
        parts.append(f"{a['text_calls']} director call(s)")
    if a["vision_calls"]:
        parts.append(f"{a['vision_calls']} vision check(s)")
    if t["clips"]:
        parts.append(f"{t['clips']} narration clip(s), {t['characters']} chars")
    log(f"  api: {', '.join(parts)}")
    if t["retries"]:
        log(f"       {t['retries']} narration retry/retries were needed")
    log(f"       {a['seconds'] + t['seconds']:.0f}s waiting on the service")


def tidy(project, log=log):
    """Delete intermediates that are free to rebuild.

    A finished project was leaving about 45 MB of scratch behind - the
    per-shot WAV fragments and the two uncompressed mix stages - which across a
    series adds up faster than the videos do. What survives is what costs money
    or time to make again: the narration mp3s, the sprite copies, and the mute
    render. The audio stages rebuild from those in seconds with no API calls.
    """
    freed = 0
    victims = [project.out / "_narration_parts", project.out / "narration.wav",
               project.out / "audio.wav"]
    for victim in victims:
        if not victim.exists():
            continue
        if victim.is_dir():
            freed += sum(f.stat().st_size for f in victim.rglob("*") if f.is_file())
            shutil.rmtree(victim, ignore_errors=True)
        else:
            freed += victim.stat().st_size
            victim.unlink(missing_ok=True)
    if freed:
        log(f"  tidied {freed / 1e6:.0f} MB of rebuildable intermediates")


def stage_mux(project, video, audio_track):
    out = project.out / f"{project.name}.mp4"
    with Atomic(out) as partial:
        audio_mod.mux(video, audio_track, partial)
    return out


def stage_draft(project, install=True):
    """Write the editable Jianying project and prove it matches the render.

    Written straight into Jianying's own drafts folder when one can be found,
    because the alternative was a folder beside the mp4 and a line of output
    asking the operator to move it - a manual copy after every single build,
    for a deliverable whose whole point is that it can still be edited.
    """
    import check_draft
    import draft as draft_mod

    root = draft_mod.jianying_drafts_dir() if install else None
    builder = draft_mod.DraftBuilder(project.out, name=project.name)
    path, total, layers = builder.build(root or project.out / "jianying")
    log(f"  {path.name}: {total:.1f}s over {layers} element layers")
    if root is None and install:
        log("  Jianying's drafts folder was not found - move this into it, or "
            "set JIANYING_DRAFT_DIR")
    # Checked here rather than left to the operator: the draft is written
    # blind - Jianying is not needed to write one and may not be installed -
    # so the only thing standing between a wrong transform and a broken
    # project the user opens is this comparison against the renderer.
    diffs = check_draft.compare(project.out, path) or []
    off = [f"shot {i}" for i, d in diffs if d > check_draft.MAX_MEAN_DIFF]
    if off:
        raise QualityError(f"the draft does not match the render at {', '.join(off)}"
                         f" - run scripts/check_draft.py {project.out} for detail")
    if diffs:
        log(f"  matches the render to "
            f"{max(d for _, d in diffs):.2f}/255 at worst, over {len(diffs)} shots")
    return path


# --- checks ----------------------------------------------------------------

def check():
    ok = True
    for name, tool in (("ffmpeg", config.FFMPEG), ("ffprobe", config.FFPROBE)):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
            log(f"  [ok]   {name}")
        except Exception:
            ok = False
            log(f"  [MISS] {name} - install it or set {name.upper()}_BIN")
    for label, ready, note in config.describe():
        log(f"  [{'ok' if ready else 'MISS'}]   {label}  {note}")
        ok = ok and ready
    # The style registry is the one config file users hand-edit; validate it,
    # then every style it offers (plus any cast file dropped into casts/).
    for issue in styles_mod.problems():
        log(f"  [MISS] style registry  {issue}")
        ok = False
    for key, entry in styles_mod.visible().items():
        try:
            issues = assets_mod.Cast.load(entry["file"],
                                          root=ROOT / "casts").problems()
        except Exception as exc:
            issues = [f"could not be read: {exc}"]
        label = entry["label"] if entry["label"] != key else ""
        log(f"  [{'ok' if not issues else 'MISS'}]   cast {key}"
            f"{'  ' + label if label else ''}"
            f"  {'valid' if not issues else issues[0]}")
        ok = ok and not issues
    try:
        import numpy, PIL, scipy                       # noqa: F401
        log("  [ok]   numpy / pillow / scipy")
    except ImportError as exc:
        ok = False
        log(f"  [MISS] python packages: {exc}")
    return ok


def run_build():
    import timing
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", help="path to a project json")
    ap.add_argument("--check", action="store_true", help="report readiness and exit")
    ap.add_argument("--from", dest="from_stage", choices=STAGES,
                    help="redo this stage even if its output is already there")
    ap.add_argument("--stop-after", choices=STAGES, help="stop once this stage is done")
    ap.add_argument("--out", help="write this project's output here "
                                 "(default: <skill>/out/<name>)")
    ap.add_argument("--regenerate-assets", action="store_true",
                    help="re-generate every sprite, ignoring the cache "
                         "(slow and costs money; the cache already notices edited prompts)")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the automatic check of the finished file")
    ap.add_argument("--preview", action="store_true",
                    help="write a contact sheet of the shots and stop")
    ap.add_argument("--no-draft", action="store_true",
                    help="skip the editable Jianying project, leaving only the mp4")
    ap.add_argument("--draft-here", action="store_true",
                    help="write the draft beside the mp4 instead of into "
                         "Jianying's own drafts folder")
    ap.add_argument("--speed", type=float, default=None, metavar="X",
                    help="how fast the whole video runs - narration, shots, "
                         "cards and subtitles together. 1.0 is natural pace; "
                         f"the project's own `speed`, else {timing.DEFAULT_SPEED}")
    args = ap.parse_args()

    if args.check or not args.project:
        log("Readiness")
        ready = check()
        if not args.project:
            log("\nGive a project file to build, e.g. "
                "python scripts/build.py projects/example.json")
        return 0 if ready else 1

    project = Project(args.project, out_override=args.out)
    if args.speed is not None:
        # Set on the data, not on the resolved speed, so `target_seconds` still
        # gets the last word - a project that names a length has already said
        # what pace it wants, and two answers to one question is how the voice
        # and the picture came to disagree in the first place.
        project.data["speed"] = args.speed
    # --from names one stage to redo, not everything downstream. Later
    # stages have their own caches - assets fingerprint each prompt, voice
    # keys on the text - so they re-derive exactly what actually changed.
    # Forcing the whole tail would regenerate every sprite on any edit,
    # which is minutes of API calls to reproduce identical files.
    forced = [args.from_stage] if args.from_stage else []
    # `--from assets` means start there, not redraw everything. A drawing's
    # filename carries a hash of what was asked for, so an edited description
    # is already a different file and gets drawn; forcing only buys an
    # identical picture at full price, which is what `--from assets` used to
    # do - twelve images to replace two that had actually changed.
    # `--regenerate-assets` is the flag that means ignore the cache.
    if "assets" in forced and not args.regenerate_assets:
        forced.remove("assets")
    if args.regenerate_assets:
        forced.append("assets")

    def should(stage):
        return stage in forced

    def done(stage):
        """True when --stop-after names this stage, so the run ends here."""
        return args.stop_after == stage

    log(f"[{project.name}] {project.layout.describe()}")

    # A cast is the one file users hand-edit, so check it before spending
    # ten minutes discovering that a prop name in `hanging` matches nothing.
    cast_problems = project.cast.problems()
    if cast_problems:
        log(f"\n{project.get('cast')} cannot be used as it is:")
        for line in cast_problems:
            log(f"  - {line}")
        return 1
    log("\n[1/8] plan")
    plan = stage_plan(project, force=should("plan"))
    invalidate(project, forced)

    import timing
    speed, fit = project.resolve_speed()
    # Say so when the number being built at is not the number that was asked
    # for. `clamp` is deliberately forgiving - a typo in a project file should
    # not kill a ten-minute build - but forgiving and silent is how somebody
    # ends up wondering why `"speed": 15` produced a normal-looking video.
    asked = args.speed if args.speed is not None else project.get(
        "speed", (project.get("voice", {}) or {}).get("speed"))
    if fit is None and asked is not None:
        try:
            wanted = float(asked)
        except (TypeError, ValueError):
            wanted = None
        if wanted is None or abs(wanted - speed) > 1e-6:
            log(f"  ! speed {asked!r} is not usable "
                f"({timing.MIN_SPEED}-{timing.MAX_SPEED}x); "
                f"building at {speed:.2f}x")
    estimate = timing.estimate(
        project.script, shot_seconds=float(project.get("shot_seconds", 5.0)),
        tail_pad=float(project.get("tail_pad", 0.35)),
        title_seconds=float(project.get("title_seconds", 2.6)),
        ending_seconds=float(project.get("ending_seconds", 4.0)), speed=speed)
    log(f"  predicted length {estimate['total']:.1f}s at {speed:.2f}x speed"
        + ("" if abs(speed - timing.BASELINE_SPEED) < 1e-6 else
           f" ({estimate['total'] * speed:.1f}s at 1.00x)"))
    if fit and not fit["ok"]:
        log(f"  ! {fit['note']}")
    # Checked here and after assets as well as after every later stage. It
    # used to be checked from voice onward only, so `--stop-after plan` - the
    # way to read the plan before paying for it - went on to draw every
    # picture and speak every line, which is the whole bill.
    if done("plan"):
        return 0

    log("\n[2/8] assets")
    stage_assets(project, plan, force=should("assets"))
    if done("assets"):
        return 0

    log("\n[3/8] voice")
    voice_index = stage_voice(project, plan, force=should("voice"), speed=speed)
    if done("voice"):
        return 0

    log("\n[4/8] storyboard")
    storyboard, pieces, total = stage_storyboard(project, plan, voice_index)
    log(f"  {len(storyboard['scenes'])} shots, {total:.1f}s total")

    if args.preview:
        import preview as preview_mod
        sheet = preview_mod.contact_sheet(storyboard, project.out,
                                          project.out / "preview.jpg")
        log(f"\npreview: {sheet}")
        return 0
    if done("storyboard"):
        return 0

    log("\n[5/8] render")
    video = stage_render(project, storyboard, force=should("render"))
    if done("render"):
        return 0

    log("\n[6/8] audio")
    track = stage_audio(project, pieces, total, storyboard,
                        script=project.script, moods=plan.get("mood", ()))
    if done("audio"):
        return 0

    log("\n[7/8] mux")
    final = stage_mux(project, video, track)
    if done("mux"):
        return 0

    log("\n[8/8] draft")
    draft_path = (None if args.no_draft
                  else stage_draft(project, install=not args.draft_here))
    log("")
    report_usage()
    log(f"\nfinished: {final}")
    if draft_path:
        # The mp4 is the preview; this is what gets handed over, because it is
        # the one a person can still fix.
        log(f"editable draft: {draft_path}")

    # Verification runs by default. An operator who has to remember to check
    # their own output eventually will not, and the failures this catches -
    # a drifting plate, a silent track, two characters merged into one blob -
    # are exactly the ones that stay invisible until somebody watches the file.
    if args.no_verify:
        tidy(project)
        return 0
    log("")
    import verify as verify_mod
    code = verify_mod.run(project)
    if code == 0:
        # Only tidy a build that passed: the intermediates are what you inspect
        # when it did not.
        log("")
        tidy(project)
    return code


# Problems with what the user supplied - a missing script, an empty one, a
# cast that will not parse, a rejected key - are not bugs and should not
# look like one. A traceback tells an operator nothing they can act on, and
# tells a less careful one to start editing the pipeline.
INPUT_ERRORS = (FileNotFoundError, ValueError, json.JSONDecodeError)


class QualityError(Exception):
    """An output the pipeline built does not match what it should have built.

    Deliberately not a ValueError: those are classified as the user's input
    being wrong, and a draft that disagrees with the render is the pipeline's
    fault, not theirs. Telling someone to go fix their script when the bug is
    here wastes their time and hides the bug.
    """


def main():
    try:
        return run_build()
    except KeyboardInterrupt:
        log("\ninterrupted - nothing was corrupted; re-run to carry on from the last finished stage")
        return 130
    except QualityError as exc:
        log(f"\nstopped: {exc}")
        log("this is a fault in the pipeline, not in your input.")
        return 1
    except INPUT_ERRORS as exc:
        log(f"\nstopped: {exc}")
        log("this is a problem with the project, the script or the cast - not with the pipeline.")
        log("run `python scripts/selftest.py` if you want to rule the pipeline out.")
        return 2
    except Exception as exc:
        import ark
        import tts as tts_mod
        if isinstance(exc, (ark.ArkError, tts_mod.TTSError)):
            log(f"\nthe service refused the request: {exc}")
            log("check `python scripts/build.py --check`; references/api-notes.md lists what each error actually means.")
            return 3
        raise


if __name__ == "__main__":
    raise SystemExit(main())
