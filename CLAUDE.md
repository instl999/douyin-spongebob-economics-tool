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
- New style: copy `casts/_template.json`, fill the `_hint_*` fields, then
  `python scripts/build.py --check`.

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

Failures in this pipeline are quiet. Verify a run by opening a frame, not by
trusting a clean exit.
