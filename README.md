# 抖音「海绵宝宝经济学」同款视频制作工具 · SpongeBob Economics Explainer Video Tool

复刻抖音「海绵宝宝经济学」的同款科普视频：一段口播文案，一条命令出成片 —— AI 分镜、固定背景板 + AI 生成素材分层合成、逐镜演绎、AI 配音、字幕、标题卡、混音、编码、质检全自动。

**自带火山方舟 Agent Plan API 支持**：导演、生图、配音共用一把 `ARK_API_KEY`，全部走 Agent Plan 套餐 —— **生图不额外计费**。

画风在哪里改（详见下文 [Changing the look](#changing-the-look)）：

| 想怎么改 | 改哪里 |
| --- | --- |
| 用内置画风 | 项目 JSON 的 `cast` 字段：`casts/bikini_bottom.json`（海绵宝宝/比奇堡）或 `casts/flat_office.json`（扁平办公室） |
| 改某套画风的整体美术方向 | 对应 `casts/<画风>.json` 顶部的 `"style"` 字段（如 `casts/bikini_bottom.json` 第 4 行） |
| 换全片背景板 | 同文件 `"background"` → `"prompt"` |
| 全新画风 | 复制 `casts/_template.json`，按 `_hint_*` 注释填写，再 `python scripts/build.py --check` 校验 |

---

English documentation follows.

Turn a narration script into a finished explainer video.

> **Ships with Volcengine Ark Agent Plan API support** — the director, image
> generation and narration share one `ARK_API_KEY`, all routed through the
> Agent Plan (`/api/plan/v3`). Under an Agent Plan subscription, **image
> generation costs nothing extra**.

```bash
python scripts/build.py projects/efficiency_wage.json
```

One fixed background plate, AI-generated cut-out characters and props
composited on top of it, acting out each sentence. Shots hold still and
dissolve into one another. Narration, subtitles, title and closing cards,
music, encoding and quality control all happen in one command.

Built for Chinese-language economics explainers, but nothing in the code is
specific to that — the subject, the art style and the cast all live in a JSON
file.

---

## Contents

- [What it produces](#what-it-produces)
- [Why it is built this way](#why-it-is-built-this-way)
- [Install](#install)
- [Credentials](#credentials)
- [Making a video](#making-a-video)
- [Changing the look](#changing-the-look)
- [Command reference](#command-reference)
- [Quality control](#quality-control)
- [How it works](#how-it-works)
- [Design decisions](#design-decisions)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Repository layout](#repository-layout)
- [A note on generated artwork](#a-note-on-generated-artwork)

---

## What it produces

A finished MP4 plus an SRT sidecar:

| | |
|---|---|
| Resolution | 1920×1080 landscape or 1080×1920 portrait |
| Frame rate | 30 fps |
| Video | H.264, CRF 20 by default |
| Audio | AAC 192 kbps — narration over a music bed |
| Structure | title card → shots → closing card |
| Subtitles | burned in, plus a separate `.srt` |

Six videos were produced during development: two art styles, both orientations,
10 seconds to 2 minutes 44.

---

## Why it is built this way

Before any code was written, three finished videos in the target style were
measured frame by frame. The format turned out to be much simpler than it looks,
and several obvious guesses about it are wrong:

| Measurement | Result | What it rules out |
|---|---|---|
| Frame-to-frame delta in character-free regions | **0.7–1.4 grey levels** | Parallax, drifting layers, any camera move — that is compression noise, the plate never moves |
| Delta within a held shot | **0.001–0.05** | Breathing, floating, sway |
| Transition length | **≈ 0.65 s**, everything fading together | Hard cuts; per-element slide/pop/zoom entrances |
| Caption centre / size | **y = 90.3 %**, 0.032 × width | — |
| Character height | **≈ 0.39 of frame**, varying ~1.4× | A single fixed framing |
| Contact shadows | **none** | The ellipse under each sprite |

So this is a compositing pipeline, not a per-shot image generator. A shot is
composed once and held; only dissolve frames and caption changes are ever
recomputed. `references/reference-findings.md` has the full derivation, and
every threshold in the quality checks traces back to it.

---

## Install

```bash
pip install pillow numpy scipy
cp .env.example .env        # then fill in ARK_API_KEY
python scripts/build.py --check
python scripts/selftest.py
```

`ffmpeg` and `ffprobe` must be on `PATH`, or set `FFMPEG_BIN` / `FFPROBE_BIN`
in `.env`.

- Windows: `winget install Gyan.FFmpeg`
- macOS: `brew install ffmpeg`
- Debian/Ubuntu: `apt install ffmpeg`

A CJK font is needed for subtitles. On Windows this is found automatically
(Microsoft YaHei Bold); elsewhere install Noto Sans CJK or PingFang and the
lookup in `scripts/textkit.py` will pick it up.

Two checks confirm the install:

- **`build.py --check`** — one line per capability: ffmpeg, ffprobe, API key,
  narration, every cast file, Python packages.
- **`selftest.py`** — 15 offline checks that spend nothing. They exercise script
  splitting, the duration model, matting, layout repair, rendering, mixing and
  verification, building a real MP4 from synthetic assets. Run it after any
  edit, and whenever a build fails and it is not obvious whether the pipeline or
  the API is at fault.

---

## Credentials

**One key does everything.** `ARK_API_KEY` from
[console.volcengine.com/ark](https://console.volcengine.com/ark) covers the
director, image generation and narration. The tool ships with Volcengine Ark
**Agent Plan** API support out of the box — under an Agent Plan subscription,
image generation costs nothing extra.

Two things about the Volcengine API are easy to get wrong, and **both report
something other than the real problem**:

### The base path

Agent Plan routes through `/api/plan/v3`. On the pay-as-you-go path `/api/v3`
the same key returns:

```
401 {"error":{"code":"AuthenticationError",
     "message":"The API key or AK/SK in the request is missing or invalid."}}
```

which reads like a dead key and is not. The tell: a genuinely unknown key gets
`"The API key doesn't exist."` instead. That wording difference separates
"wrong URL" from "wrong key" in a single request.

### The speech header

Speech authenticates with a single **`X-Api-Key`** header. The widely
documented openspeech scheme — `X-Api-App-Key` plus `X-Api-Access-Key`, issued
separately from the speech console — returns:

```
401 {"header":{"code":45000010,
     "message":"load grant: requested grant not found in SaaS storage"}}
```

for every combination of values. That error names an entitlement, so the
obvious reading is that the plan does not include speech. It does. No separate
speech account is needed.

### Models and voices

| Capability | Model / resource | Notes |
|---|---|---|
| Director, vision | `doubao-seed-2.0-lite` | Thinking must be disabled — see below |
| Images | `doubao-seedream-5-0-lite` | 2K minimum; `seedream-4-0-*` is not on the plan |
| Narration | `seed-tts-2.0` | `_uranus_` voices only |

`doubao-seed-*` are reasoning models. One director call ran **over nine
minutes** with thinking on, spending most of its tokens on reasoning that never
reached the storyboard. `"thinking": {"type": "disabled"}` cuts a short call
from 4.0 s to 0.7 s at the same quality for this task.

`seed-tts-2.0` speaks through the `_uranus_` voice family. A `_moon_` voice from
the earlier models is rejected as `55000000 resource ID is mismatched with
speaker related resource`. Three are verified:

- `zh_male_yuanboxiaoshu_uranus_bigtts` — warm male narrator (default)
- `zh_female_gaolengyujie_uranus_bigtts` — cool female
- `zh_female_shuangkuaisisi_uranus_bigtts` — brisk female

Full verified endpoint behaviour is in `references/api-notes.md`.

**Without a key the run still completes.** Shot lengths fall back to a
character-count estimate and the finished video simply has no voice on it.
Every stage that degrades says so.

---

## Making a video

### 1. Write the script

One sentence per line, in `examples/<name>.txt`. **The text is used verbatim** —
see [Design decisions](#the-model-never-writes-narration).

### 2. Settle the settings

```bash
python scripts/new_project.py \
    --name my_video --title "什么是效率工资" \
    --script examples/my_video.txt \
    --orientation landscape --target 90 --voice male
```

This touches no network. It validates the cast, predicts the finished length,
and reports how many images it will have to generate — so real numbers can go
in front of whoever asked for the video before anything is spent:

```
predicted result
  script      466 characters
  shots       15
  narration   79.9s
  cards        6.6s
  total       86.5s at 1.00x speed

what this will generate
  sprites     up to 5 new image(s); 60 already in the library
  background  reuse the cached plate
  narration   15 clips
```

Exit code `2` means the requested length is not reachable from this script, and
it says by how many characters.

### 3. Check the composition

```bash
python scripts/build.py projects/my_video.json --preview
```

`out/my_video/preview.jpg` is a contact sheet of every shot with its framing and
duration. **Look at it.** Several real defects during development were caught
here and nowhere else.

### 4. Build

```bash
python scripts/build.py projects/my_video.json
```

Seven stages run, then the finished file is checked automatically and a
pass/fail table is printed.

### 5. Fix and re-run

Edit `out/my_video/plan.json` — move a coordinate, swap a sprite, add a label —
then:

```bash
python scripts/build.py projects/my_video.json --from storyboard
```

No model calls, no narration re-synthesis.

### How long will it be?

Duration is decided by the script, not by a setting. `timing.py` predicts it
from a model fitted to 45 real clips:

```
duration = 0.161 × characters + 0.484
```

Mean absolute error 0.19 s per clip against 0.36 s for a flat
characters-per-second rate; on whole videos it has landed within 0.3 s. The
intercept is the silence the service puts around every clip — across fifteen
shots that is seven seconds, which is the difference between hitting a target
and missing it.

`target_seconds` fits the speech rate to a wanted length within 0.85×–1.20×.
Outside that range, the answer is that the script is the wrong length, and it
says so rather than producing something a third too long.

---

## Changing the look

Everything about who is in the video and what it looks like lives in the cast
file. **No module needs touching.**

Where exactly the art direction lives:

| What you want to change | Where |
|---|---|
| Use a built-in style | the project JSON's `cast` field: `casts/bikini_bottom.json` (SpongeBob / Bikini Bottom) or `casts/flat_office.json` (flat office) |
| Change a style's overall art direction | the `"style"` field at the top of that `casts/<style>.json` (e.g. `casts/bikini_bottom.json` line 4) |
| Change the background plate every shot sits on | `"background"` → `"prompt"` in the same file |
| Create a brand-new style | copy `casts/_template.json`, fill it in following the `_hint_*` comments, validate with `python scripts/build.py --check`, then generate the library with `python scripts/build_library.py casts/<new>.json --plates` |

Two casts ship:

| Cast | For | Size |
|---|---|---|
| `bikini_bottom` | cartoon economics explainers | 5 characters, 26 props, 60 sprites |
| `flat_office` | flat-vector business explainers | 3 characters, 10 props, 25 sprites |

`flat_office` exists partly as proof: a completely different visual world is one
JSON file and one command, with no code changed.

### Starting a new style

```bash
cp casts/_template.json casts/my_style.json
# fill it in — the _hint_* keys explain every field and its traps
python scripts/build.py --check                       # validates every cast
python scripts/build_library.py casts/my_style.json --plates
```

Budget about 20 seconds per image; a 50–60 sprite cast takes roughly 20 minutes
with four workers. **It happens once** — later videos composite the same PNGs
and generate nothing.

### What a cast controls

```json
{
  "style":       "art direction, applied to every generated image",
  "background":  { "prompt": "the one plate every shot sits on" },
  "characters":  { "krabs": { "look": "...", "role": "...",
                              "relative_height": 0.95,
                              "poses": { "stand": "..." } } },
  "props":       { "oven": "..." },
  "hanging":     ["whiteboard", "clock"],
  "foreground":  ["desk", "counter"],
  "panel_color": [176, 196, 205]
}
```

- `hanging` — props that float at eye level rather than standing on the ground
- `foreground` — furniture drawn over the legs of whoever stands at it
- `panel_color` — the slab used to build a room over the fixed plate

The **background prompt is worth more time than anything else** — the horizon
decides whether characters look planted or adrift. Say where the ground line
goes, that the middle is empty, and that scenery stays low and at the edges.
Tall scenery framing the picture makes every character look small and lost.

---

## Command reference

| Purpose | Command |
|---|---|
| Check the API side | `python scripts/build.py --check` |
| Check the pipeline, offline and free | `python scripts/selftest.py` |
| Create a project and see its cost | `python scripts/new_project.py --name … --title … --script … --orientation …` |
| Contact sheet of the shots | `python scripts/build.py <project> --preview` |
| Build (checks the result automatically) | `python scripts/build.py <project>` |
| Check a finished video on its own | `python scripts/verify.py <project> --verbose` |
| Re-run after editing the plan | `python scripts/build.py <project> --from storyboard` |
| Generate a cast's whole library | `python scripts/build_library.py casts/<cast>.json --plates` |
| Apply new validation rules to an old plan | `python scripts/migrate_plan.py out/<name>/plan.json casts/<cast>.json` |
| Cut out a single image by hand | `python scripts/matting.py in.jpg out.png` |

### Stages

`plan → assets → voice → storyboard → render → audio → mux`

Each writes its result and skips itself if that result is current.
`--from <stage>` redoes one stage and clears the cached outputs that depend on
it. `--stop-after <stage>` ends early. `--regenerate-assets` forces new artwork.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Finished, all checks passed |
| 1 | A quality check failed |
| 2 | A problem with the script, project or cast |
| 3 | The service refused the request |
| 130 | Interrupted |

An interrupted run is always safe to repeat: every output is written to a
`.partial` file and renamed only on success, and a cached render is probed
before it is reused.

---

## Quality control

`verify.py` runs twelve checks and exits non-zero on failure, so it can gate a
loop. It runs automatically at the end of every build.

| Check | Threshold |
|---|---|
| Resolution, frame rate | must match the storyboard |
| Duration | within 0.75 s of the storyboard |
| **Background is static** | drift p75 ≤ 3.0 grey levels (reference measured 0.7–1.4) |
| Audio present, not clipped | RMS > −45 dBFS, peak < −0.5 dBFS |
| Subtitle timing, fit | no overlaps, ends before the video does |
| Sprite cutouts | nothing kept the whole frame or almost nothing |
| Plan carried through | every field the plan decided reached the storyboard |
| Shot layout | no collisions, nothing off-frame or in the caption band |

The background check is the important one: it is the property the whole format
rests on, and it is measured the same way the references were.

---

## How it works

| Module | Job |
|---|---|
| `build.py` | Stage orchestration, caching, invalidation, error handling |
| `plan.py` | Splits the script in code; asks the model only to dress each shot; validates everything it returns |
| `assets.py` | Generates, mattes and caches the sprite library; the verified title card |
| `matting.py` | Chroma cutout, edge decontamination, autocrop |
| `checks.py` | Collision and overflow detection, with auto-repair |
| `render.py` | Plates, group dissolves, framing, captions, frame stream |
| `textkit.py` | Fonts, CJK wrapping, stroked text, labels, balloons, panels, cards |
| `layout.py` | Landscape/portrait geometry |
| `timing.py` | Duration prediction and target fitting |
| `ark.py` / `tts.py` | The two APIs, with retries |
| `audio.py` | Narration track, music bed, SRT |
| `verify.py` | The twelve checks |
| `selftest.py` | Fifteen offline checks; needs no credentials |
| `preview.py` | Contact sheet |
| `new_project.py` | Settings in, project file out, with a cost and length estimate |
| `build_library.py` | Generate a cast's whole sprite catalogue in one go |
| `migrate_plan.py` | Replay an old plan through the current validator |
| `config.py` | Credentials, endpoints, model ids |
| `gen_sfx.py` | Procedural sound effects |

---

## Design decisions

### The model never writes narration

The script is split by `split_script`; the director only chooses sprites. This
is not a stylistic preference. Given the text and asked merely to *group* it, a
director rewrites instead: a 39-character script came back as 112 characters
across eight shots, including an invented beat about a character running an
experiment that appeared nowhere in the source. The narration is the user's
product, so the model is not given the chance to touch it.

### Sprites are generated once

Character consistency across fifty shots is the hard problem in this format.
Reusing a fixed library sidesteps it rather than fighting a prompt into
behaving. The background plate is cached per cast too, so every episode in a
series sits on the identical plate.

### Sprites are generated on magenta, not white

On white, the enclosed gap between a raised arm and the body is
indistinguishable from a white the character is *supposed* to have — an eye, a
glove, a plate — so the only safe rule is "remove white the border can reach",
which leaves those gaps filled in as white blobs. A saturated key has no such
ambiguity.

### Edges are colour-decontaminated

A half-transparent pixel generated over the key still holds the key colour, and
composited over the plate it reads as a pale fringe around everything. Solving
`observed = a·F + (1−a)·key` for `F` removes it. **This is the single largest
visible difference in a finished frame.**

### Text is drawn, never generated

Image models mangle exact Chinese text and numbers, and in this format the
numbers are the point. Labels, balloons and cards are drawn with PIL, so they
are correct every run. The one exception is the brush-calligraphy title card,
which is generated and then **read back by the vision model**; if the characters
do not match it retries once, then falls back to drawn text rather than shipping
garbled lettering.

### Physical facts belong to the cast, not the director

Which props hang, which are drawn in front of legs, and how tall each character
is relative to the others. Left to its own judgement a director hangs a pile of
gold coins at eye level like a wall chart, and makes the tall character shorter
than the short one in the next shot.

### Frames are cached

A shot does not change while it is held, so only dissolve and caption-change
frames are computed; everything else is a buffer the encoder has already seen.
Frames stream to ffmpeg's stdin as raw RGB24 and nothing is written to disk. A
2 minute 44 video renders in about 85 seconds.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `background is static` fails | Something is moving that should not be | The plate must be one image; check nothing has added parallax or per-shot backgrounds |
| `shot layout` reports an overlap | Two sprites collide | `--from storyboard` spreads them apart automatically; if it persists, edit `x` in `plan.json` |
| `runs off the side` | A sprite is too large or too near the edge | Reduce `h`, or pull `x` back toward 0.2–0.8 |
| `sprite cutouts` — *kept the whole frame* | Generation did not return a magenta background | Delete that PNG from `casts/<cast>/sprites/` and re-run; if it repeats, rewrite that prop's description |
| No narration | Key missing, or a wrong voice family | `--check`; the voice must be `_uranus_` |
| `duration` mismatch | `storyboard.json` was hand-edited | Delete it and re-run `--from storyboard` |
| 401 that looks like a bad key | Wrong base path | Agent Plan uses `/api/plan/v3` |
| `45000010 grant not found` | Wrong speech header | Use `X-Api-Key`, not the App Key / Access Key pair |
| A director call takes many minutes | Reasoning is on | `thinking` must be disabled |

Edit `plan.json`, never `storyboard.json` — the latter is regenerated from the
former plus the measured narration lengths.

---

## Limitations

- **Word-level caption timing is not available.** The API returns an empty
  `sentence.words` array for this resource. Shot lengths are exact, measured
  from the audio; captions are timed inside a shot by character count.
- **Depth is banded.** Panels behind, hanging props, characters, foreground
  furniture — plus an explicit `z` when that is not enough. There is no
  per-pixel occlusion.
- **Subtitle weight is synthesised.** Windows ships nothing heavier than YaHei
  Bold, so the glyph is stroked in its own colour to approximate a Heavy
  weight. Deliberately tiny — +1 px at caption size, capped at +2 px for card
  text: CJK counters are tight, and at 4.5 % they filled in completely and the
  caption became an illegible smear.
- **One background per video.** Locations are built with `panel` slabs over the
  fixed plate, which is what the references do. Swapping the plate mid-video is
  deliberately not supported.

---

## Repository layout

```
SKILL.md                  operator playbook (Chinese) - rules, workflow, failure table
README.md                 this file
.env.example              credentials template
casts/
  _template.json          annotated template for a new art style
  bikini_bottom.json      cartoon cast: 5 characters, 26 props
  flat_office.json        flat-vector cast: 3 characters, 10 props
projects/*.json           one file per video
examples/*.txt            narration scripts
scripts/                  see "How it works"
references/
  reference-findings.md   the frame-by-frame measurements the design rests on
  storyboard-schema.md    every field in plan.json and storyboard.json
  api-notes.md            verified endpoint behaviour, and the errors that mislead
assets/                   default music bed and sound effects
out/                      generated; not tracked
```

`SKILL.md` is a [Claude Code](https://claude.com/claude-code) skill definition —
drop the directory into `~/.claude/skills/` and the workflow becomes
conversational. It is equally usable as a plain CLI project; nothing depends on
that.

---

## A note on generated artwork

The sprite libraries are AI-generated in the style of existing animated
properties. That is one thing for private use and study and another for
publication, so **check your own position before publishing videos made with the
`bikini_bottom` cast commercially, or before making a repository containing
those assets public**. The `flat_office` cast raises no such question, and the
pipeline itself is style-agnostic — a cast file is all that ties it to any look.
