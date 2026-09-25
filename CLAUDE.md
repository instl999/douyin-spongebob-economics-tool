# cartoon_video_pipeline

Narration script in, **editable Jianying (剪映) draft out**. The MP4 rendered
beside it is a preview, not the deliverable — automatic layout is good enough to
watch and not good enough to ship unreviewed.

Do not confuse with `../AutoReel`, which targets DaVinci Resolve instead.

## Commands

```bash
python scripts/build.py projects/efficiency_wage.json   # full run
python scripts/build.py <proj> --preview                # the planned shots, free
python scripts/build.py --check                         # readiness, then exit
python scripts/build.py <proj> --from render            # redo one stage onward
python scripts/build_library.py clay --plates           # a style's references
```

Useful flags: `--stop-after <stage>`, `--out <dir>`, `--regenerate-assets`,
`--no-draft`, `--draft-here`, `--no-verify`, `--speed X`, `--workers N`.

**`--preview` costs nothing but the plan.** It runs straight after the plan
stage and writes `preview.jpg`: drawings already made are used, the rest are
labelled stand-ins the shape the real ones will be, and lines not yet spoken
are timed by estimate. It works in `out/<name>/preview/` and leaves the
build's own files alone. It used to run after assets and voice - the whole
bill - to catch a bad composition before paying for one.

## Stages

`plan → assets → voice → storyboard → render → audio → mux → draft`

Every stage caches into the project's output dir and **skips itself when its
result is current**. Editing one shot's `shows` redraws that one picture;
editing the storyboard re-renders without paying for narration again.
`--from <stage>` redoes that stage, and later stages re-derive only what
changed.

`--from assets` deliberately does **not** force: a drawing's filename carries a
hash of its description, so an edited description is already a different file.
`--regenerate-assets` is the flag that ignores the cache, and on this track
that is the whole bill again.

## Drawings

**There is no sprite library.** Every picture in a video is generated for that
video from the director's description of the line it illustrates, and nothing
is reused by the next video - that reuse is what made every output look alike
(one pile of gold coins appeared in five videos out of eight). Two elements
described in the same words are drawn once; identity is carried by one
committed reference image per character in `casts/<style>/anchors/`, which is
the only drawing that outlives a video.

Cost is therefore per video, not per style: about one image per element, ~20s
each, capped by `plan.MAX_DRAWINGS`, made `workers` at a time (project setting
or `--workers`, default 4). Narration clips are spoken the same way.
`new_project.py` states the expected range before anything is built.

A video's own bookkeeping - the title lettering and its setting's fingerprints
- lives beside it in `out/<name>/assets.json`, not in the cast's committed
`manifest.json`, which only records the per-style plate. Old entries in the
manifest are still read and move across when used, so nothing is redrawn.

## Styles

Seven built in. `casts/styles.json` is the single source of truth for which
styles exist, their labels, and the default (`bikini_bottom`). A style needs
`build_library.py` once to draw its character references - one image per
character, not a whole library.

- Pick a style: the project JSON's `cast` field (a key like `"clay"`, or a path).
- Change a style's art direction: `"style"` at the top of `casts/<style>.json`.
- Change the background plate: same file, `"background" → "prompt"`.
- A script that names no location gets a backdrop generated from its own
  subject (`setting.png`), used as the plate for every shot. Turn it off with
  `"fallback_setting": false` in the project JSON.
- New style: copy `casts/_template.json`, fill the `_hint_*` fields, then
  `python scripts/build.py --check`.

## Portrait

A portrait video is watched inside the phone app, which draws its own text
and buttons over the bottom fifth of the screen and a column down the right.
The portrait frame (`look.frame` in `casts/styles.json`) lists those as
`ui_zones` and keeps everything that matters out of them: the stage ends at
0.735 of the height, the caption is centred at 0.775, the layout check keeps
labels left of the right-hand column (`safe_right`), and a yellow title bar
holds the video's question across the top of every shot (a project's
`title_bar` gives it other words, or `false` removes it). `--preview` outlines
the zones in red. Cards, bubbles and labels are sized per orientation in the
same file, since a size that suits 1920 pixels across is small across 1080.

## Speed

`speed` is one number for the whole video and **1.5 is the default** — the
project file's `speed`, or `--speed` on either track. 1.0 is the baseline:
every duration written in a project file, in `casts/styles.json` and in this
pipeline's own constants means what it says at 1.0, and `timing.scale` is the
one place that turns one into timeline time.

It is not a voice setting, and that is the whole point. `voice.speed` used to
be the speech rate alone, which is the one thing that cannot be sped up on its
own: the narration arrived early and the picture sat there holding its old
length. The rule now is one line — **a duration divides by speed, a per-second
rate multiplies by it, and anything in pixels does not move** — and it is
applied at exactly one point per track, `stage_storyboard` in the drawn track
and `build` in the footage one.

Three consequences worth knowing before editing anything here:

- **Narration is re-spoken, not resampled.** The service takes a `speech_rate`
  directly, so there is no pitch shift, and shot lengths still come from the
  audio that actually came back. A shot cannot drift out of sync with its own
  narration because it is measured from it. The voice cache keys on the speed
  as well as the text, so changing `speed` re-reads rather than re-cutting old
  clips to a new rhythm.
- **Retrieved footage is played faster, not cut shorter.** `footage_render.prepare`
  applies `setpts`, and `locate` scores the window the cut will actually play.
  A shot cut shorter and a shot played faster are the same length on the
  timeline and identical in every report; the selftest tells them apart by
  sampling a frame partway through a red-then-blue source.
- **Music and sound cues are left at natural pace.** They are cues, not a
  clock. The opening stinger played 1.5× is a different sound, and nothing in
  the picture is timed against the bed.

The bounds, 0.5×–2.0×, are the speech service's own (`speech_rate` is a
percentage offset in [-50, 100]). Outside them the voice could not be spoken at
the rate the picture is cut to, which is the mismatch the setting exists to
prevent.

## Two tracks

**Drawn** (`build.py`) is the original: a fixed plate, AI sprites composited on
top, whole-shot dissolves, Jianying draft out.

**Footage** (`footage_build.py`) is the live-action track, added for the
documentary-style references measured in `references/footage-findings.md`:

```bash
python scripts/footage_build.py --script examples/telephone_history.txt
python scripts/compare_to_reference.py out/<name>/<name>.mp4
```

It does NOT go through `render.py`, whose whole design is static cached plates.
It composites in ffmpeg and borrows only `textkit` for captions, so subtitles
land on the same scanline in both tracks.

It ends in a Jianying draft too. `footage_build` records what the MP4 was
assembled from in `timeline.json`, and `footage_draft.py` lays the draft from
that alone: the shot clips as cut and graded, the frame furniture with the
progress rule keyframed, the hook and captions as text on the MP4's own lines
(`footage_render.caption_layout` places both), each beat's narration, and the
bed ducked as the mix ducks it. `--no-draft` and `--draft-here` work as on the
drawn track, and the draft is checked for placement as part of verify.

`footage.py` retrieves: the model turns each beat into English search queries,
Pexels (needs a free `PEXELS_API_KEY`) and Wikimedia Commons (keyless,
archival) return candidates, and a vision model scores each candidate's
thumbnail. Beats retrieval cannot cover are generated instead - never left
empty. `footage_render.py` cuts, grades, hard-cuts and subtitles them.

Every beat gets one of four shot kinds. The director declares it in a
required `shot` field - not inferred from which spec happens to appear,
because when composite was merely an optional field the model never once
chose it across a whole build:

| kind | when | module |
|---|---|---|
| **composite** | the beat names or defines a thing - a term, an object | `composite.py` |
| **graphic** | a quantity, share, rate, trend, comparison, or money moving | `motion.py` |
| footage | the beat describes a scene or an action | `footage.py` |
| generated | retrieval found nothing usable | `footage_render.fill_shot` |

**A composite is the references' default shot**, and the layer this track was
missing. Backdrop, a cut-out subject (generated on a plain ground, then run
through the drawn track's `matting.auto_cutout`), a large headline beside it,
and an optional annotation on a leader line. The annotation is placed from the
cut-out's own alpha - beside the subject, clear of it, with the leader landing
on it; a fixed fraction of the subject's box printed it across a standing
figure's face. Four layouts: `subject_left`,
`subject_right`, `subject_center`, `card`. Measured, a reference graphic frame
is ~60% content; a full-frame photo under a caption is 2-10%, which is what
"technically correct but flat" looked like.

**The cut lands on a finished frame.** Subjects and headlines settle into
place at full opacity; they never fade up from nothing. Charts still grow their
bars, but their scaffold - title, axis, gridlines - is drawn before any data.
Every layer fading in from zero left each of seven hard cuts landing on a bare
backdrop for about a second. The annotation is the one thing that draws itself
on, which is what the references animate too.

**Frame furniture is composited over the assembled video**, not per shot: a
progress rule, an optional `--topic` eyebrow, and a baseline. Per shot it would
only appear on the kinds `motion.py` and `composite.py` render, so it would
blink out at every retrieved photographic shot. `--topic` defaults to blank on
purpose - nothing in the script reliably names the subject in the narration's
own language, and a wrong label on every frame is worse than none.

**Motion graphics are drawn, never generated.** An image model asked for "a bar
chart showing 45%" produces something that looks like a chart and says
something else. Six kinds: `counter`, `bar_chart`, `line_chart`, `comparison`,
`flow`, `breakdown`. Each builds over the first 55% of the shot then holds, and all of them
stay above `motion.safe_bottom` so nothing is drawn under the subtitles: 0.78
of the height in landscape, 0.69 in portrait, where the captions sit higher to
clear the phone app's own controls (`footage_render.caption_floor`).

**A number reaches the screen only if the narration says it.**
`footage.validate_graphic` checks every value against the numbers actually
spoken in the beat - Arabic or Chinese, including 百分之七点二 - and refuses the
graphic otherwise. An ungrounded `comparison` keeps its bars and loses its
figures, so the shape still says which is larger without inventing a statistic.
This is a guarantee in code rather than an instruction in a prompt, because
these are finance videos and a chart is read as data whatever the voiceover
said. Do not relax it.

Where the two tracks deliberately disagree, and why:

| | drawn | footage |
|---|---|---|
| transitions | 0.5s dissolve; a cut when two shots share a character | **hard cut** (references: 59 cuts / 309s) |
| opening | calligraphy title card, **spoken over a stinger** | hook overlaid on shot 1, no card |
| ending | 金句 card | none; ends on footage |
| last shot | **no tail pad** - the card cuts in on the last word | **no tail pad** - the file ends on it |
| subtitles | Chinese, a phrase at a time | a line of Chinese at a time, the beat's English beneath |

Do not "fix" the footage track to match the drawn one. Each set of rules was
measured off its own references.

What they share: captions change at the pauses the voice actually took
(`audio.speech_pauses`, measured once per clip and kept in the voice index),
broken where a phrase breaks rather than wherever a line fills
(`captions.chunks`, `textkit.wrap`); and both mixes are normalised to -17 LUFS
with the loudness range preserved (`"loudness": null` in a drawn project only
limits it).

## The opening (drawn track)

The title card is read aloud. It used to hold a silent slot, so every video
opened on two and a half seconds of nothing while the calligraphy sat there.

The stinger is **an ordinary sound cue at t=0** — `opening_dong`, recorded into
`storyboard["sound_cues"]` like every other. It is not a separate mechanism in
`mix()`, because `carried()` is read by both the mix and the draft writer, and
a cue living anywhere else is a cue the two can disagree about. It carries its
own gain (the optional fourth element of a cue) - unity, below.

The title's narration starts `audio.TITLE_SFX_LEAD` (0.45s) later, so it speaks
into the cue's decay rather than over the impact — measured off the cue, which
peaks in its first 0.25s and is 10 dB down by 0.5s. `build_narration` takes an
optional third element per piece for that delay; padding only ever went on the
*end*, so there was nowhere to put an opening beat.

The card holds for `build.title_slot(...)`: the configured `title_seconds` as a
floor, extended when the line needs longer. A fixed 2.6s would cut to shot 1
mid-word on any title past about eight characters.

Past `MAX_TITLE_SLOT` the card keeps its type and **loses its voice** — and the
storyboard's `title_card.voice` is the only record of that. Both the mix and
the draft exporter read it; the exporter used to go back to `voice/index.json`,
where the clip is still sitting, and spoke a title the MP4 beside it was silent
for, truncated into a card sized for no speech. Same shape as the sound cues
above, and the same reason: a decision that lives in two places is a decision
the two can disagree about.

Per-project overrides: `opening_sfx` (a library name; `""` turns it off) and
`title_seconds`.

The cue itself is **supplied, not synthesised**: `assets/sfx/opening_dong.mp3`
is a specific sound the videos are known by. `gen_sfx.py` builds every other
cue in that folder and says so in its docstring, and the selftest holds both
halves - every other cue has a generator, and this one must not, since a
synthesised stand-in for it is worse than none. If it is missing the build
says so and opens without a cue rather than substituting one.

It plays at **unity gain** (`build.OPENING_GAIN = 1.0`), exactly as supplied:
not normalised, not levelled against the narration. Earlier versions tuned it
(0.7, then 0.42) and were wrong for the same reason both times - it is meant to
sound the way it sounds. A project's `opening_volume` overrides it.

The title is **not** an SRT cue. The draft imports the SRT as a native subtitle
track, so a cue there printed the title a second time in small white text under
the calligraphy card that already says it.

**A title the script's own opening already says is not read aloud at all.** The
director writes the title from the script it was handed, so the card and the
first thing said are often the same sentence delivered half a second apart.
`build.title_is_echo` compares meaning rather than characters - shared
characters and shared character pairs - across the first two shots, because the
director paraphrases (a repeat is rarely a prefix) and because the line a title
came from lands in shot two as often as in shot one. It is checked in
`stage_voice`, so the clip is never paid for, and again in `stage_storyboard`,
which catches one left in the index by an earlier build.

The thresholds are deliberately strict: silencing a title the script never says
loses the opening line outright, which is a worse video than the stutter.

## The ending

**The last shot carries no tail pad.** Every other shot ends with `tail_pad`
(0.35s at baseline) so the next does not start on the same breath. The last
shot has no next, and that pad was dead air between the final subtitle and
either the 金句 card or the end of the file - on every video the pipeline had
made. The card itself is unchanged: it holds `ending_seconds` with text on
screen, which is content rather than trailing time.

## Music

`music.py` chooses the beds from a folder, matched to how the script reads.

The library is `assets/bgm/`, committed with the repository; its README says
the repository's licence covers the code and not the music. `bgm_library` in a
project points at another folder. Filenames carry a Chinese mood label before
the track title - `紧张Kill Drill - Robert Ruth.mp3` - and the label is read off
the front, up to the first non-Chinese character, so an artist credit at the
end is not mistaken for one.

The director supplies the script's own labels as a `mood` field on the call it
already makes; a second request for one word would double what choosing a bed
costs. `music.moods_in` reads the script directly when the model returns none.

It may also mark where the script turns, as `sections` (at most 4, each at
least 3 shots). Each section then gets the bed that fits its own mood and
place, and neighbours crossfade over 2s; with no sections one bed runs start to
finish. The storyboard records the beds (`music.beds`) and the mix and the
draft both lay them from that record - the draft used to have no music at all.

Four rules worth not undoing:

- A label naming a **position** (`开头`, `结尾`, `后期`, `提出`) ranks below a
  plain track of the same mood under a whole video, and above it under the
  section it names. Ranked down, not excluded - a folder may hold nothing else.
- **A mood that matches nothing means no music.** The wrong bed is more
  distracting than none, and it is the one choice in a video that nobody plays
  back to check. That is not the same as **no mood at all** - no label and no
  keyword hit decides nothing about the video, so it keeps `bgm_default.wav`
  rather than going silent on an absence.
- **Ties break by filename**, so `--from audio` cannot rescore a finished video.
- `bgm` in the project JSON names one track and is not second-guessed.

Under the voice both tracks sit in the same band, 20-25 dB down.
`audio.BGM_VOLUME` = 0.056 (-25 dB) is the footage track's level. The drawn
track ducks: its bed sits at `BGM_DUCKED_VOLUME` = 0.16 when nobody is talking,
so the cards and the breaths between shots are not silent, and the sidechain
pulls it about 10 dB further down under speech (`DUCK_DB`, which the draft
draws as volume keyframes, having no sidechain). `duck_music: false` in a
project turns that off and goes back to a constant 0.056.

## Tests

`python scripts/selftest.py` runs everything offline, about 300 checks, in a
few minutes. It calls `selftest_workflow.py` (stages, caching, the draft,
verification, the free preview), `selftest_look.py` (layout, captions, music,
cues) and `selftest_footage.py` (the footage track, including an offline build
that writes and checks its draft) from the end of it — two Claude Code sessions
work on this repo at once, and one thousand-line test file is the single place
their work is guaranteed to collide. Builds it spawns run with both keys set
empty, so a machine holding real ones never spends them.

## Environment

One Ark Agent Plan key drives director, image generation and narration. See
`scripts/config.py` for the full list: `ARK_API_KEY`, `ARK_BASE_URL`,
`ARK_IMAGE_MODEL`, `ARK_TEXT_MODEL`, `TTS_KEY`, `TTS_RESOURCE_ID`, `TTS_URL`,
`TTS_SPEAKER`, `FFMPEG`, `FFPROBE`.

## Gotchas

Two Volcengine errors here name the wrong cause. Check these before believing
an entitlement or credential problem:

- **Agent Plan routes through `/api/plan/v3`.** On `/api/v3` the same key returns
  `401 ... is missing or invalid`, which reads as a dead key. A genuinely unknown
  key says "doesn't exist" instead — that wording difference is the fastest tell.
- **Speech uses a single `X-Api-Key` header.** The widely documented openspeech
  scheme (`X-Api-App-Key` + `X-Api-Access-Key`) returns `45000010 grant not
  found`, which reads as "speech isn't on the plan" and is just the wrong header.

Two more that report success while doing nothing:

- **`drawbox` size expressions are not re-evaluated per frame** in this ffmpeg,
  and a comma inside a filter option is a filtergraph chain separator. Both
  make `drawbox=w=iw*t/D` exit 0 and draw at full width forever. `overlay`'s
  `x` does honour `t`; animate with that.
- **Scene detection cannot count this track's cuts.** Made shots share a
  backdrop, so `gt(scene,0.3)` finds 0 on a seven-shot video. Read
  `shots.json`, which `compare_to_reference.py` now does.

`emptiest_frame_pct` in that report is a **floor, not a band**: the references
sit at 30-73% because they are photographic, and a clean line chart that reads
beautifully scores 7%. It catches blank frames; it does not rank designs.

Failures in this pipeline are quiet. Verify a run by opening a frame, not by
trusting a clean exit.
