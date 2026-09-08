# cartoon_video_pipeline

Narration script in, **editable Jianying (剪映) draft out**. The MP4 rendered
beside it is a preview, not the deliverable — automatic layout is good enough to
watch and not good enough to ship unreviewed.

Do not confuse with `../AutoReel`, which targets DaVinci Resolve instead.

## Commands

```bash
python scripts/build.py projects/efficiency_wage.json   # full run
python scripts/build.py --check                         # readiness, then exit
python scripts/build.py <proj> --from render            # redo one stage onward
python scripts/build_library.py clay --plates           # build a style's assets
```

Useful flags: `--stop-after <stage>`, `--out <dir>`, `--regenerate-assets`,
`--preview`, `--no-draft`, `--no-verify`.

## Stages

`plan → assets → voice → storyboard → render → audio → mux → draft`

Every stage caches into the project's output dir and **skips itself when its
result is current**. Editing one pose regenerates one sprite; editing the
storyboard re-renders without paying for narration again. `--from <stage>` redoes
that stage, and later stages re-derive only what changed.

## Styles

Seven built in. `casts/styles.json` is the single source of truth for which
styles exist, their labels, and the default (`bikini_bottom`, the only one
shipping a prepopulated asset library). Other styles need `build_library.py`
once, ~20s per image, then reused forever.

- Pick a style: the project JSON's `cast` field (a key like `"clay"`, or a path).
- Change a style's art direction: `"style"` at the top of `casts/<style>.json`.
- Change the background plate: same file, `"background" → "prompt"`.
- A script that names no location gets a backdrop generated from its own
  subject (`setting.png`), used as the plate for every shot. Turn it off with
  `"fallback_setting": false` in the project JSON.
- New style: copy `casts/_template.json`, fill the `_hint_*` fields, then
  `python scripts/build.py --check`.

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
and an optional annotation on a leader line. Four layouts: `subject_left`,
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
stay above `motion.SAFE_BOTTOM` so nothing is drawn under the subtitles.

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
| transitions | 0.5s dissolve | **hard cut** (references: 59 cuts / 309s) |
| opening | calligraphy title card, **spoken over a stinger** | hook overlaid on shot 1, no card |
| ending | 金句 card | none; ends on footage |
| subtitles | Chinese | Chinese + English beneath |
| loudness | limited only | normalised to -17 LUFS, LRA preserved |

Do not "fix" the footage track to match the drawn one. Each set of rules was
measured off its own references.

## The opening (drawn track)

The title card is read aloud. It used to hold a silent slot, so every video
opened on two and a half seconds of nothing while the calligraphy sat there.

The stinger is **an ordinary sound cue at t=0** — `opening_dong`, recorded into
`storyboard["sound_cues"]` like every other. It is not a separate mechanism in
`mix()`, because `carried()` is read by both the mix and the draft writer, and
a cue living anywhere else is a cue the two can disagree about. It carries its
own gain (the optional fourth element of a cue), since an opening accent sits
about 6 dB above the level the library cues want.

The title's narration starts `audio.TITLE_SFX_LEAD` (0.45s) later, so it speaks
into the cue's decay rather than over the impact — measured off the cue, which
peaks in its first 0.25s and is 10 dB down by 0.5s. `build_narration` takes an
optional third element per piece for that delay; padding only ever went on the
*end*, so there was nowhere to put an opening beat.

The card holds for `build.title_slot(...)`: the configured `title_seconds` as a
floor, extended when the line needs longer. A fixed 2.6s would cut to shot 1
mid-word on any title past about eight characters.

Per-project overrides: `opening_sfx` (a library name; `""` turns it off) and
`title_seconds`.

The title is **not** an SRT cue. The draft imports the SRT as a native subtitle
track, so a cue there printed the title a second time in small white text under
the calligraphy card that already says it.

## Tests

`python scripts/selftest.py` runs everything offline, 141 checks. The
footage-track half lives in `scripts/selftest_footage.py` and is called from
the end of it — two Claude Code sessions work on this repo at once, and one
thousand-line test file is the single place their work is guaranteed to
collide.

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
