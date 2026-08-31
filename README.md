# 抖音「海绵宝宝经济学」同款视频制作工具 · SpongeBob Economics Explainer Video Tool

复刻抖音「海绵宝宝经济学」的同款科普视频：一段口播文案，一条命令出成片 —— AI 分镜、固定背景板 + AI 生成素材分层合成、逐镜演绎、AI 配音、字幕、标题卡、混音、编码、质检全自动。

**自带火山方舟 Agent Plan API 支持**：导演、生图、配音共用一把 `ARK_API_KEY`，全部走 Agent Plan 套餐 —— **生图不额外计费**。

画风在哪里改（详见下文 [Changing the look](#changing-the-look)）：

| 想怎么改 | 改哪里 |
| --- | --- |
| 换默认画风 / 改画风中文名 / 加、藏一个画风 | `casts/styles.json` —— 画风的专门配置文件，只改这一个 |
| 用某套画风 | 项目 JSON 的 `cast` 字段写画风 key（如 `"clay"`），或直接写 cast 文件路径 |
| 改某套画风的整体美术方向 | 对应 `casts/<画风>.json` 顶部的 `"style"` 字段（如 `casts/bikini_bottom.json` 第 4 行） |
| 换全片背景板 | 同文件 `"background"` → `"prompt"` |
| 全新画风 | 复制 `casts/_template.json`，按 `_hint_*` 注释填写，再 `python scripts/build.py --check` 校验 |

### 内置画风（7 套）

| 画风 key | 中文名 | 适用 | 规模 |
|---|---|---|---|
| `bikini_bottom` | 比奇堡 | 卡通经济学科普（**默认画风**，自带素材库） | 5 角色 26 道具 60 素材 |
| `watercolor_anime` | 水彩动漫 | 手绘水彩动画电影质感，暖阳海边小镇 | 3 角色 14 道具 |
| `clay` | 黏土定格 | 橡皮泥定格动画质感，雪夜森林小屋 | 3 角色 14 道具 |
| `neon_cyberpunk` | 赛博霓虹 | 霓虹雨夜都市，赛博朋克动漫 | 3 角色 14 道具 |
| `retro_editorial` | 复古欧式插画 | 雨夜街角咖啡馆，中古编辑插画质感 | 3 角色 14 道具 |
| `retro_pulp` | 复古公路海报 | 70 年代丝网印刷海报，沙漠日落公路 | 3 角色 15 道具 |
| `flat_geo` | 极简几何商务 | 扁平几何色块，剪影人物，科技商务风 | 3 角色 13 道具 |

除比奇堡自带素材库外，其余画风首次使用先生成素材库（约 20 秒/张，一次生成、永久复用）：

```bash
python scripts/build_library.py clay --plates
```

### 画风总配置 casts/styles.json

哪些画风可用、默认用哪个、每个画风叫什么，都由这一个文件管理：

```json
{
  "default": "bikini_bottom",
  "styles": {
    "bikini_bottom": { "label": "比奇堡", "note": "海绵宝宝 2D 手绘卡通" },
    "clay":          { "label": "黏土定格", "note": "橡皮泥定格动画质感" }
  }
}
```

- `default` —— 项目没写 `cast` 字段时用的画风
- `styles` —— 每个画风一条：`label` / `note` 只影响展示；`file` 可选（默认 `casts/<key>.json`）；`"hidden": true` 让画风不再出现在列表里，但项目里写它的 key 仍可用
- **注册不是必须的** —— 丢进 `casts/` 的 cast 文件会被自动发现，注册只是为了加中文名和说明
- 改完跑 `python scripts/build.py --check` 校验；`python scripts/new_project.py --list-styles` 查看全部画风

### 画风相关命令

```bash
python scripts/new_project.py --list-styles                     # 列出全部画风
python scripts/new_project.py --name demo --title "标题" \
    --script examples/sunk_cost.txt --cast clay                  # 用指定画风建项目
python scripts/build_library.py clay --plates                   # 一次性生成某画风的全部素材
python scripts/build.py --check                                 # 校验画风配置与全部 cast 文件
```

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

**An editable Jianying (剪映) project, plus a finished MP4 as the preview.**

The MP4 is what the pipeline thinks the video should be. The draft is the same
timeline with everything still separable — background, each character, each
prop, each label, the narration, the subtitles — so the shot where the caption
crowds a character is a drag rather than another full run.

| | |
|---|---|
| Draft | `out/<name>/jianying/<name>/`, opens in Jianying with every layer intact |
| Resolution | 1920×1080 landscape or 1080×1920 portrait |
| Frame rate | 30 fps |
| Video | H.264, CRF 20 by default |
| Audio | AAC 192 kbps — narration over a music bed |
| Structure | title card → shots → closing card |
| Subtitles | burned into the MP4; a real editable subtitle track in the draft; `.srt` either way |

Six videos were produced during development: two art styles, both orientations,
10 seconds to 2 minutes 44.

### The Jianying draft

Tracks come out named and in order: `背景` (the plate and the two cards),
`图层1…图层N` (one per simultaneous element, stacked in the renderer's own depth
order), `配音` (narration, one clip per shot), `字幕` (subtitles, imported so
they carry Jianying's native styling).

```bash
python scripts/draft.py out/<name> --install
```

`--install` writes into Jianying's own drafts folder so the project simply
appears in the app. Without it the draft lands under `out/<name>/jianying/` and
can be moved there by hand. Media is referenced by absolute path, so move the
draft folder and its `materials/` together, or re-export.

One thing to know before editing: **each element is a full-canvas transparent
frame, not a cropped sprite.** Dragging and scaling behave normally — the
artwork sits at the centre of its own frame — but the selection handles sit at
the canvas edge rather than around the character. That is deliberate.
Jianying's `scale` is undocumented, and "fit the material to the canvas" and
"fill the canvas with it" disagree for any material that is not canvas-shaped;
a canvas-shaped material makes the two identical, so placement is exact without
having to guess which one Jianying means. `scripts/check_draft.py` asserts that
property rather than trusting it.

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
pip install pillow numpy scipy pyJianYingDraft
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
- **`selftest.py`** — offline checks that spend nothing. They exercise the style
  registry, script splitting, the duration model, matting, layout repair,
  rendering, mixing, draft export and verification, building a real MP4 from
  synthetic assets. Run it after any
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
file. **No module needs touching.** Which casts exist, what each is called and
which one is the default live in one more file: **`casts/styles.json`, the
art-style registry.** That registry is the file to edit when you want to switch
or manage styles; a cast file is where you edit a style's actual art direction.

Where exactly the art direction lives:

| What you want to change | Where |
|---|---|
| Switch the default style, rename one, add or hide one | `casts/styles.json` |
| Use a style | the project JSON's `cast` field: the style key (`"clay"`) or the path to its cast file |
| Change a style's overall art direction | the `"style"` field at the top of that `casts/<style>.json` (e.g. `casts/bikini_bottom.json` line 4) |
| Change the background plate every shot sits on | `"background"` → `"prompt"` in the same file |
| Create a brand-new style | copy `casts/_template.json` to `casts/<key>.json`, fill it in following the `_hint_*` comments, validate with `python scripts/build.py --check`, then generate the library with `python scripts/build_library.py <key> --plates` |

### The style registry

`casts/styles.json` is small on purpose:

```json
{
  "default": "bikini_bottom",
  "styles": {
    "bikini_bottom": { "label": "比奇堡", "note": "..." },
    "clay":          { "label": "黏土定格", "note": "..." }
  }
}
```

- `default` — the style used when a project does not name one
- `styles` — one entry per style: `label` and `note` are display-only, `file`
  is optional (it defaults to `casts/<key>.json`), `hidden: true` keeps a style
  out of the listings while projects can still name it
- **Registering a cast is optional.** A cast file dropped into `casts/` is
  discovered automatically; the registry entry only adds its Chinese label and
  note. `new_project.py --list-styles` prints the whole table.

Seven casts ship:

| Cast | Label | For | Size |
|---|---|---|---|
| `bikini_bottom` | 比奇堡 | cartoon economics explainers (the default) | 5 characters, 26 props, 60 sprites |
| `watercolor_anime` | 水彩动漫 | hand-painted watercolour anime film look, seaside town | 3 characters, 14 props |
| `clay` | 黏土定格 | plasticine stop-motion feel, snowy storybook forest | 3 characters, 14 props |
| `neon_cyberpunk` | 赛博霓虹 | neon-lit rain-soaked cyberpunk night city | 3 characters, 14 props |
| `retro_editorial` | 复古欧式插画 | mid-century editorial illustration, rainy café corner | 3 characters, 14 props |
| `retro_pulp` | 复古公路海报 | 1970s screen-print road-trip poster, desert highway | 3 characters, 15 props |
| `flat_geo` | 极简几何商务 | flat geometric corporate look, silhouette figures | 3 characters, 13 props |

All of them follow the same shape as `bikini_bottom`: a completely different
visual world is one JSON file and one command, with no code changed.
Its sprite library is generated once (see below) and reused by every video.

### Starting a new style

```bash
cp casts/_template.json casts/my_style.json
# fill it in — the _hint_* keys explain every field and its traps
python scripts/build.py --check                       # validates the registry and every cast
python scripts/build_library.py my_style --plates     # optionally register it in casts/styles.json first
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
| List every available style | `python scripts/new_project.py --list-styles` |
| Create a project and see its cost | `python scripts/new_project.py --name … --title … --script … --cast <style key> --orientation …` |
| Contact sheet of the shots | `python scripts/build.py <project> --preview` |
| Build (checks the result automatically) | `python scripts/build.py <project>` |
| Check a finished video on its own | `python scripts/verify.py <project> --verbose` |
| Re-run after editing the plan | `python scripts/build.py <project> --from storyboard` |
| Generate a cast's whole library | `python scripts/build_library.py <style key or cast path> --plates` |
| Apply new validation rules to an old plan | `python scripts/migrate_plan.py out/<name>/plan.json <style key or cast path>` |
| Cut out a single image by hand | `python scripts/matting.py in.jpg out.png` |
| Export the editable Jianying project | `python scripts/draft.py out/<name> --install` |
| Check an exported draft | `python scripts/check_draft.py out/<name>` |

### Stages

`plan → assets → voice → storyboard → render → audio → mux → draft`

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

`check_draft.py` covers the deliverable separately, and runs inline the moment
a draft is written. It reads `draft_content.json` back with no knowledge of how
it was produced, rebuilds every shot from that file's materials and transforms
alone, and compares against the renderer's own plate — **mean difference must
stay under 2/255**, and in practice comes out at 0.00. It also asserts the
property the export depends on (every video material shares the canvas aspect
ratio, so `scale` cannot be ambiguous), that no segment sets a scale, that every
referenced file exists, and that each narration clip starts on its own shot.

It earns its place. It has already caught a stale draft left behind by
re-matted sprites, and a one-pixel slip in a portrait build caused by deriving
the transform from element centres, where an odd-width sprite rounds against
the frame's own floor-divided paste.

A build refuses to finish if the draft disagrees with the render, and reports it
as a fault in the pipeline rather than in your input — because it is.

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
| `draft.py` | Exports the editable Jianying project - the deliverable |
| `check_draft.py` | Rebuilds the draft's frames from its own JSON and compares them to the render |
| `verify.py` | The twelve checks |
| `selftest.py` | Offline checks (style registry, matting, layout, draft export); needs no credentials |
| `preview.py` | Contact sheet |
| `new_project.py` | Settings in, project file out, with a cost and length estimate |
| `build_library.py` | Generate a cast's whole sprite catalogue in one go |
| `styles.py` | The art-style registry: which styles exist, which is default |
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

That equation assumes the edge really is a blend of subject and key, and a JPEG
edge is not: compression ringing paints colours there that no alpha value
explains, and it survives. Measured across 85 sprites, edge pixels still
carried **6.7% key colour on average and 16% at worst** — the visible purple
fringe. Three further passes take that to **0.59%**:

1. **Choke.** The matte is pulled in about a pixel before anything else, which
   discards the outermost contaminated ring. Cartoon art has a thick black
   outline just inside it, so this costs 0.5% of the ink and nothing that reads.
2. **Borrow.** Partial pixels take their colour from the nearest solid one.
   Unmixing amplifies noise as alpha falls — dividing by 0.05 makes an artefact
   twenty times worse — and the interior colour a pixel away is what the edge
   should have been.
3. **Rim suppression.** What is left is compared against the artwork a few
   pixels further in, and only the *excess* key tint is removed.

That last step is fussier than it sounds, and the reason is worth stating,
because the obvious version is wrong. "Red and blue above green" describes
magenta spill — and equally describes SpongeBob's tie, Sandy's flower and every
brown, pink and purple in the cast. A first attempt suppressed it everywhere,
scored a perfect 0.0% contamination, and turned all of them grey. What
separates spill from paint is not colour and not alpha, but position and
context: spill sits within a few pixels of the cut, and it is more key-tinted
than the artwork just inside it. Beside a black outline the excess is the whole
halo; inside Patrick, whose own pink reads as spill by colour alone, the
reference is just as pink and the excess is nothing.

`selftest.py` checks both directions — that the key comes off, *and* that a red
circle on magenta stays red. A contamination score on its own is a metric the
code can satisfy by destroying the picture.

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
- **The draft has never been opened in Jianying by this code.** Jianying is
  Windows/macOS desktop software and was not installed on the machine this was
  built on, so `check_draft.py` verifies the draft against the renderer rather
  than against the app. Every number it writes is either documented
  (`transform`, microsecond timings) or made moot by construction (`scale`, via
  canvas-shaped materials), but the first person to open one should still expect
  to confirm that.
- **Element layers select as full-canvas boxes**, not tight around the artwork.
  See [The Jianying draft](#the-jianying-draft).
- **Labels and balloons are pictures in the draft, not text objects.** They can
  be moved, scaled and deleted, but not retyped — the styling is drawn by PIL
  and Jianying has no equivalent. Subtitles *are* real text.

---

## Repository layout

```
SKILL.md                  operator playbook (Chinese) - rules, workflow, failure table
README.md                 this file
.env.example              credentials template
casts/
  styles.json             the art-style registry - default style, labels, notes
  _template.json          annotated template for a new art style
  bikini_bottom.json      cartoon cast: 5 characters, 26 props (the default)
  watercolor_anime.json   watercolour anime cast: seaside town
  clay.json               claymation cast: snowy storybook forest
  neon_cyberpunk.json     cyberpunk cast: neon night city
  retro_editorial.json    editorial cast: rainy café corner
  retro_pulp.json         pulp-poster cast: desert highway
  flat_geo.json           flat-vector cast: geometric corporate plaza
projects/*.json           one file per video
examples/*.txt            narration scripts
scripts/                  see "How it works"
references/
  reference-findings.md   the frame-by-frame measurements the design rests on
  storyboard-schema.md    every field in plan.json and storyboard.json
  api-notes.md            verified endpoint behaviour, and the errors that mislead
assets/                   default music bed and sound effects
out/                      generated; not tracked
  <name>/jianying/<name>/ the editable Jianying project - the deliverable
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
those assets public**. The six other casts raise no such question, and the
pipeline itself is style-agnostic — a cast file is all that ties it to any look.
