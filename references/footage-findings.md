# What the live-action reference videos actually do

Measured from three finished videos (`我们每天穿的衣服全是塑料做的`,
`一马赫到底有多快`, `互联网的前身`), 1280×720 30 fps as downloaded, 269–410 s.
The 720p is a download artifact; the production masters are certainly 1080p.

These are a **different genre** from the ones in `reference-findings.md`. Those
are illustrated and composited from a static plate. These are cut from real
footage.

## They are not generated. They are retrieved.

Frames sampled at 12/45/90/150/220 s across all three:

| Shot | What it is |
|---|---|
| Fibre bundle, macro, green background | licensed macro cinematography |
| Yarn plant, worker walking the aisle | documentary factory footage |
| 1950s Los Angeles street, driving POV | colourised archival |
| Teletype machine, punched tape | archival |
| Rotary telephone on paper texture | archival, graded |
| Mach shockwave, three-panel composite | purpose-built motion graphics |

Only the last one is *made*. Everything else is *found*. An image generator
cannot produce this video no matter how good the prompts are, because the
material is photographic and moving. Retrieval is not an optimisation here, it
is the production method.

## Pacing

| | Internet | Mach | Clothes |
|---|---|---|---|
| Duration | 309 s | 269 s | 410 s |
| Hard cuts (scene > 0.3) | 59 | 24 | 67 |
| Mean shot length | **5.2 s** | 11.2 s | **6.1 s** |

5–6 s per shot is the same beat length the illustrated pipeline already uses,
so `split_script` needs no change for footage. The Mach video's 11 s average is
an artifact of its long composited sequences, which register as one shot.

## The audio is doing more work than the picture

| | Internet | Mach | Clothes |
|---|---|---|---|
| Integrated loudness | −18.7 LUFS | −16.4 LUFS | −18.5 LUFS |
| Loudness range (LRA) | 3.5 LU | 7.5 LU | 3.1 LU |
| Silences > 0.4 s at −50 dB | 0 | 0 | 1 |

**Zero silence across five to seven minutes.** A music bed runs continuously
under narration compressed to a 3 LU range. Much of what reads as "expensive"
is this: the track never breathes and is normalised to platform spec.
Implemented as `audio.normalize` / `TARGET_LUFS = -17.0`; verified moving a
test mix from −43.7 to −17.0 LUFS exactly.

## Two registers, so two providers

| Query | Commons (keyless) |
|---|---|
| `rotary telephone 1950s` | 2 usable, incl. 1920×1080 CC BY-SA |
| `textile factory machine` | 3 usable, all ≤ 640×480 |
| `heavy rain umbrella` | **1 usable — a McKinley funeral cortege** |

Commons is excellent for archival and close to useless for modern B-roll.
Pexels is the reverse. Hence `provider="pexels+commons"`.

Commons material is often 640×480 and is **not** filtered out — good archival
footage is low-resolution by nature. Flagged `low_res` instead.

## Why keyword search alone is not enough

The McKinley result is the argument for stage 3. Worse in live testing: the
word "ticket" retrieved a **traffic-citation bodycam video** for a beat about a
concert ticket stub. Vision caught it (score 1, "wrong subject, no ticket stub
here"). That ambiguity is invisible to keyword search.

Stock libraries are also full of shots matching *any* explanatory beat — a
person pointing at a whiteboard, a rising arrow, a handshake. `RANK_QUESTION`
scores those 0 explicitly.

## Pexels needs a key, whatever it appears to do

A first unauthenticated call returned HTTP 200 with real results; the next
returned 401. Do not build on the 200.

## The brief poisons the ranker, not just the search

First live run, on a SpongeBob-flavoured script, the director wrote fictional
characters into the shot briefs:

    needs: "SpongeBob peers out a window at heavy falling rain"
    queries: ["person looking out rain window", ...]

The queries recovered on their own. The **brief did not** — and the brief is
what stage 3 ranks against, so vision would have rejected every correct
real-world clip that search correctly found. A stage-1 prompt fault surfacing
as a stage-3 failure, with nothing in between reporting a problem.

Fixed with an explicit recast rule plus a worked example. After the fix:
"Close-up of a hand holding a printed paper concert ticket", and for
"economists call this a sunk cost", "a person frowning while staring at a used
concert ticket stub" — rather than the pre-fix answer, a textbook page with the
term printed on it.

## A high score says WHAT, never WHERE

The most expensive failure found, and it reported success at every step.

The switchboard operator that scored 5 lives inside `Arnhem kan weer
automatisch telefoneren`, an **89-second** newsreel. Ranking scored its
thumbnail; `prepare` then cut five seconds from the head. The result was a
bombed-out street with a truck. Coverage said 40%, the render completed, the
subtitle was correctly placed, and the picture was simply wrong.

`footage_render.locate` samples frames across any source over `LONG_SOURCE`
(25 s), scores each on the same rubric, and cuts around the best.

| | before locate | after |
|---|---|---|
| Cut point in an 89 s newsreel | 1.0 s | 34.6 s |
| What is on screen | bombed-out street | switchboard operators |
| Anything reporting a problem | no | — |

## Commons recall is literal, and largely not in English

Beat 3 wanted a rotary dial and returned **zero** candidates from
`turning rotary phone dial`, `vintage rotary telephone`, `old dial telephone` —
while the 1920×1080 `Telefon W48 Deutsche Bundespost 1950er Jahre` sits in the
library and *was* found by the exact phrase `rotary telephone 1950s`. Commons
matches words in titles and descriptions, and much of its best footage is
titled in German or Dutch. Both clips chosen in the archival run were 384×288
Dutch newsreel.

Treat Commons as a bonus, not a supply. **A Pexels key is what makes this track
production-viable**; without one, coverage on a modern script was 0/4.

## Structure: no cards at either end

The drawn track opens on a calligraphy title card and closes on a 金句 card.
The live-action references do neither.

- **Opening**: straight onto footage. The internet reference's first shot is a
  vintage television showing static, with the hook line set mid-frame in
  brackets — 「你一定听过这个声音」 — its English translation beneath, and the
  ordinary bottom subtitle running underneath at the same time.
- **Closing**: straight out of footage. The last shot is a talking-head
  interview, bilingual subtitle still running, no card at all.

## Bilingual subtitles are not optional

Every sampled frame of all three carries two lines: Chinese in white with a
black stroke, and a smaller English translation directly beneath at roughly
55% of its size. One of the most visible markers of the format. The
translation costs no extra model call — the query stage already reads every
beat, so it returns `en` alongside `needs` and the queries.

## The registers are mixed inside one video

The internet reference cuts archival telephony (teletype, 1950s street)
against a modern talking-head interview shot on a contemporary camera. It is
not an archival video; it is a video that *uses* archival material. This is the
direct argument for `pexels+commons` over either alone.

## The references cut. They do not dissolve.

The drawn track's measured rule is whole-shot cross-dissolves, ~0.5 s. That
rule was carried across to footage without re-measuring, and it is wrong here.

The internet reference has **59 hard cuts in 309 s** at a 5.2 s mean shot -
essentially every boundary is a cut. Cross-fading every transition made the
montage read as a slideshow, and it also hid the boundaries from scene
detection, so the first build measured "0 cuts, mean shot 23.8 s" on a
five-shot video. `DISSOLVE` now defaults to 0 and `assemble` concatenates.

## Two captions were on screen at once

The first build had every subtitle visibly doubled. A caption ended at
`start + seconds - dissolve*0.6` while the next began at
`start + seconds - dissolve`, leaving 0.2 s of overlap on every transition.

The selftest did not catch it because it asserted the wrong thing - that each
caption ends inside its own shot, which was true throughout. The invariant that
matters is between neighbours, and is now tested as such.

## loudnorm quietly compresses, and alimiter quietly boosts

Two ffmpeg defaults, both silent, both cost a measurable amount of quality.

**`loudnorm` single-pass runs in dynamic mode** - it does not just move the
level, it compresses toward the LRA target, and raising that target does not
release it:

| filter | result on a mix whose natural LRA is 3.6 |
|---|---|
| `loudnorm=I=-17:LRA=3.5` | I −17.7, **LRA 2.1** |
| `loudnorm=I=-17:LRA=7` | I −17.9, **LRA 2.7** |
| `loudnorm=I=-17:LRA=11` | I −17.9, **LRA 2.7** |

`linear=true` with measured values is supposed to fix this, and is a *request*,
not a guarantee: loudnorm drops back to dynamic when the gain would push the
true peak past TP, with no warning. Two mixes 0.2 LU apart went 3.6 → 3.6 and
3.4 → 2.1 through the identical call. `normalize` therefore computes the gain
itself and applies it with `volume`, which cannot change LRA at all.

**`alimiter` defaults to `level=true`**, which auto-levels the output up to the
ceiling and silently undoes any gain staging in front of it. A track aimed at
−17 LUFS came out at −15.7 until `level=false` was set.

Final, all three bed configurations in band on both measures:

| | I | LRA |
|---|---|---|
| bed at 0.10 | −17.2 | 3.4 |
| bed at 0.06 | −16.9 | 3.7 |
| no bed | −16.9 | 3.8 |

Ducking the bed under speech (`sidechaincompress`) was measured and moved LRA
by at most 0.1 across bed levels from 0.025 to 0.10. The compressed range was
loudnorm's doing, not the bed's. Kept as an option, default off.

## Five shots of the same object is a failure

The first build returned three near-identical shots - a black rotary telephone
on a wooden surface - for three different beats. Every one matched its beat;
the sequence still did not work, because the references never repeat a
composition. `QUERY_SYSTEM` now requires the framing to be cycled deliberately
(wide establishing, medium with a person, close-up on hands, unusual angle) and
to be stated inside `needs` so it reaches the picture.

## Three ffmpeg and text defects that only exist in the output file

None of these raise an error. Each was found by building the video and looking
at it, and each is now pinned by a test.

**`concat` refuses mismatched pixel aspect ratios.** A 384x288 archival upscale
came out SAR 1571:1440 while generated fill was 20384:20385, at identical pixel
dimensions. `xfade` joined them without complaint, so this only surfaced when
transitions became hard cuts. Every shot now ends with `setsar=1`.

**The caption wrapper broke English words in half.** `textkit.wrap` is
character-based - correct for CJK, where every character stands alone - and its
"pull one character down" and orphan-control rules cheerfully turned
"distant places." into "distant plac" / "es." The drawn track never saw it
because it only renders Chinese. Both rules now stop at a Latin word boundary.

**The English line sat on top of the Chinese**, then fell off the frame.
Offsetting the English by a fixed multiple of the Chinese size works only while
the Chinese is one line; on a two-line beat the second line landed straight on
the English, and the English itself was clipped at row 1080.

Measuring the Chinese bbox and anchoring to its bottom does not fix it either:
`render_caption` clamps its bbox to the frame, so a block that already
overflows reports a bottom of exactly the frame height and the overflow reads
as far smaller than it is. `_stacked_caption` therefore computes both block
heights the way `render_caption` lays them out - `line_height*n + 0.20*size*(n-1)`
plus stroke padding - places the English under the Chinese, and shifts the whole
pair up only as far as it must. Both cases now end at row 1045 against a floor
of 1053, and a one-line beat does not drift just because its neighbour needed
two.

## Loudness range has a ceiling the pipeline cannot raise

A mix cannot have more dynamic range than its source. This TTS delivers
narration at LRA 3.0-3.8 depending on the take, and the references sit at
3.1-3.5, so on a flat take the finished mix simply cannot reach the band:

| | LRA |
|---|---|
| narration as synthesised | 3.0 |
| + flat bed at 0.10 | 2.6 |
| + bed at 0.05, ducked | 2.9 |
| references | 3.1-3.5 |

Ducking is worth ~0.3 LU here and a quieter bed another ~0.3, which is the
difference between clearly flat and effectively at the ceiling. An earlier
measurement said ducking did nothing; that was taken through single-pass
loudnorm, whose own compression swamped it. **Re-measure audio changes
downstream of normalisation, not upstream of it.**

`verify` therefore floors at 2.8 rather than 3.1 and prints the narration's own
range beside the result, so a flat take is not mistaken for a broken mix.

## Motion graphics, and the rule they are built around

The Mach reference is the precedent: the one shot across all three references
that was *made* rather than found is its three-panel shockwave composite, built
because no footage of a shockwave exists. Economics is mostly that case - a
savings rate, a flow of money from households through a bank to firms, one
quantity outgrowing another. None of it has a photograph, and a generated still
of "a rising line" is a picture of a line rather than the line rising.

These are drawn with PIL, frame by frame, not generated. The reason is not
taste: the frames carry numbers, and an image model asked for "a bar chart
showing 45%" returns something chart-shaped saying something else.

**The rule: a number may appear on screen only if it appears in the narration.**
`validate_graphic` extracts every figure the beat actually states - Arabic and
Chinese, including 百分之七点二 and 三十 - and refuses any graphic whose values are
not among them. Enforced in code, not requested in the prompt, because a prompt
is a request and this needs to be a guarantee. A chart is read as data whatever
the voiceover says, so a helpfully-supplied "45%" for a beat that never
mentioned 45 is a fabricated statistic published in an authoritative format.

Two details that took a measurement to get right:

- **"百分之" is a unit marker, not a number.** Its 百 parsed as 100, so every beat
  mentioning a percentage silently grounded an invented value of 100.
- **An ungrounded comparison is downgraded, not discarded.** The bars still show
  which is larger - which is what "prices rose faster than wages" actually says
  - with `show_values` cleared so no figure is printed.

Layout is constrained to `SAFE_TOP`..`SAFE_BOTTOM` (0.10-0.78 of frame height).
The bottom fifth belongs to the subtitles, and the first pass put bar labels at
0.85, drawing them underneath the narration.

Graphic beats skip retrieval entirely - there is no footage of a savings rate,
and searching for it spends vision calls on candidates that can only lose - and
they are excluded from the coverage ratio, since they were never a gap.

## What twenty beats found that seven could not

Every build up to this point was five to seven beats. A 20-beat script (95 s)
broke three things immediately, none of which a short video can reach.

**Generated diagrams are worthless, and there were four of them.** Asked for
"a diagram of the four parts of GDP" an image model returned a whiteboard with
four empty boxes; asked for a labelled comparison it returned two blank
squares; asked for a flowchart, a person drawing meaningless circles. Image
models cannot render diagrams or legible text, and a long script has far more
structural beats than a short one.

Two changes. A `breakdown` kind for parts-of-a-whole, which is the shape those
beats actually wanted. And a hard prompt rule: footage and generated stills are
never asked for a diagram, chart, graph, whiteboard, flowchart, labelled boxes
or written words. If a beat needs a diagram it gets a graphic; if no kind fits,
a real-world scene that stands for the idea, never a picture of the idea
written down.

`breakdown` also handles the ungrounded case the honest way: given four parts
and no stated proportions it draws four **equal** labelled segments. The parts
are what the narration said; the split is not.

**Graphic language was inconsistent inside a single video** - English titles
sitting above Chinese bar labels, in the same build, from the same call. The
prompt rule was already there and the model followed it for one script and not
the next. Asking again is not a fix. `repair_graphic_language` detects
wrong-language labels and translates them in one batched call, leaving
acronyms (GDP, CPI), years and percentages alone. Same lesson as the numbers:
where it matters, check rather than ask.

**Retrieval was 0/14.** Commons is archival and this was modern economics, so
essentially everything fell through to generation. A Pexels key is the whole
difference here.

## Judge the mix, not the voice

The loudness-range check failed a build at LRA 2.5 whose narration was also
2.5 - the mix had lost nothing at all. A mix cannot have more dynamic range
than its source, and this TTS delivers anywhere from 2.5 to 3.8 depending on
the take, so testing the finished file against the references' 3.1 floor fails
builds for something no stage in the pipeline can change.

The check now tests that the mix preserves the narration's own range, which is
what the pipeline controls and which still catches the real defect - a
normalisation stage flattening the track, exactly how LRA 3.6 once became 2.1.
The reference band is printed alongside as context rather than as a verdict.

The same reasoning applies to the scorecard: `compare_to_reference` reports the
absolute band, because that is the honest comparison to the references, and the
build's own `verify` tests what the build can be held responsible for.

## The same bug, twice, in the function that was not refactored

`hook_png` had the caption-stacking bug independently and kept it after the
captions were fixed, because it drew its own two lines with its own fixed
offset. On a long beat the hook wrapped to two lines and the English
translation printed straight across the second one. Both now go through
`_stacked_caption`, which takes a centre and a scale.

The hook also gets a length rule: refused when it needs more than one line at
hook size. The reference hook is nine characters - 「你一定听过这个声音」 - and a
two-line hook is not a hook, it is the subtitle repeated in a larger font over
the top of the shot. The bottom subtitle carries the words either way.

## Banning diagram photos improved *retrieval*

Unintended and worth knowing. Forbidding the director from asking footage or
generation for diagrams, charts and whiteboards pushed it toward real-world
scenes for those beats - a news studio, a factory line, a rebuild after a storm
- and those are findable. Retrieval on the same 20-beat script went from
**0/14 to 4/14** with no change to the retrieval code at all. Asking for
something that exists is most of the search problem.

## Recover the unit, do not ask for it again

The director supplies `unit` inconsistently: "%" on a counter and a bar chart,
omitted on the comparison, all in one build - so two bars read "53" and "68"
under a voiceover saying 百分之五十三. `numbers_with_units` records how each
figure was spoken (`45%`, `百分之四十五`, `两个百分点`) and the unit is inferred
when every value on screen was a percentage. A plain count stays a plain count:
"大概三十年前" does not become 30%.

That is the third time the same shape of fix has been the right one - numbers,
label language, and now units. Where the output has to be correct rather than
merely plausible, check it in code; the prompt is a request.

## Looking closer: the first read of these videos was wrong

The original analysis sampled five frames per video and concluded "licensed
footage, retrieved not generated." A 25-frame contact sheet across each full
runtime says something different, and the difference is most of why the output
still did not feel professional.

**The Mach video is barely footage at all. It is a designed composite.**

- One persistent backdrop - dark blue-grey gradient with a faint cross-hatch
  texture - underneath nearly every shot.
- Subjects **cut out** and placed on it: the MiG-25, a portrait of Mach, an
  airliner, a man thinking. Many sit on rounded-corner cards with soft shadows.
- A **large typographic layer** that is not the subtitle: 「比值」 in big serif
  red, 「音障」 in white, "1887年", "1马赫=340米/s". Design text, sized like a
  title, composed into the frame.
- **Annotation graphics over the picture**: dashed outlines drawn around the
  two scientists, a red arrow labelling 后掠翼, magnifier circles over a
  molecular diagram, floating ？？？ marks.
- The bilingual subtitle is small, at the very bottom, and clearly secondary.

Architecturally that is the *drawn* track - fixed plate plus composited
cutouts - built from photographic elements instead of cartoon sprites.

**The clothes video mixes registers.** Full-frame factory footage, yes, but
also: objects composited on plain dark grounds (a bottle beside its pellets;
cotton, wool and silk in a row; body armour and a firefighter's jacket), text-led
frames with the copy set large on the left and the object on the right, a
document panel with a red-circled 0.02 in a highlighted table, a real donut
chart with leader lines, and 「关键工艺参数」 labelled directly onto a loom shot.

### What that means for this pipeline

Full-frame footage under a bottom subtitle - what this track builds - is the
*least* designed thing these videos do. The gap is not grading or pacing. It is
that the references compose: background, subject, type and annotation as
separate layers.

## The grade gap, measured

| | mean luma | saturation | edge/centre luma |
|---|---|---|---|
| mach | 92 | 0.31 | **0.50** |
| clothes | 113 | 0.18 | 0.87 |
| internet | 86 | 0.27 | 0.89 |
| mine (before) | 87 | 0.31 | **1.18** |

Luma and saturation were already right. The vignette was not: the `clean` grade
had none at all, so edges came out *brighter* than centre while every reference
darkens them. `vignette=PI/6` measures 0.70 on a flat grey, inside the
reference span. A scrim was added on top - `colorlevels=romax=X` is a straight
multiply, so romax=0.84 is exactly a 16% black layer - heavier on generated
stills, which are flat, than on retrieved footage, which carries its own
exposure.

## Chart colour was wrong in a way eyeballing cannot catch

Run through the dataviz skill's validator, the original dark palette failed
three of five checks:

- **normal-vision deltaE 14.6, below the floor of 15** - the two bars were hard to
  tell apart for full-colour readers, never mind colour-blind ones
- muted slot chroma 0.024 - grey pretending to be a colour
- accent lightness 0.751, outside the dark band of 0.48-0.67

`#4288CC` + `#BE8532` on `#161A20` passes all five (normal deltaE 24.9, CVD 22.2).
The vintage pair `#B03A2E` + `#2E6E78` passes separation (22.3 / 11.8) and
deliberately fails the chroma floor, because a period paper chart is meant to
read as ink and every bar carries a direct label - the secondary encoding that
permits it.

Also applied from that skill: a 2 px gap between adjacent fills (breakdown
segments were flush, an explicit anti-pattern), rounded ends on the free end of
each bar only, a recessive 2 px axis, and a `dim` text token so captions and
axis labels stop wearing a series colour.

## The luma gap was emptiness, not darkness

Chasing whole-video mean luma toward the references' 86-113 was about to be a
mistake. Measured per shot in a finished econ build:

| shot kind | luma | fraction of frame that is content |
|---|---|---|
| generated / footage | 79-121 | 40-81% |
| **graphic** | **35-41** | **2-10%** |
| mach reference | 110 | 60% |

The photographic shots were already inside the reference band. The graphic
shots dragged the average down because they are nearly *empty* - a chart on a
ground, using a twentieth of the pixels, where the reference fills more than
half the frame with subject, type and annotation together.

Brightening the ground would have moved the number and made the video worse.
The fixes are the ones that fill a frame: a gradient rather than a flat fill,
the reference's faint cross-hatch texture on the backdrop, and larger marks
(bar width 0.13 -> 0.17 of frame, flow nodes 0.165 -> 0.19, breakdown height
0.16 -> 0.20).

**A whole-video average is a weak proxy.** It is worth measuring per shot and
per kind before treating any aggregate as a target.

## Two tests that broke honestly

Both were mine, and both had assumed a flat background.

- The subtitle-band check found "ink" as any pixel unlike the frame's modal
  value. A gradient differs from its own modal value everywhere, so the moment
  the flat fill was replaced the whole frame read as content. Fixed by
  differencing against the bare backdrop.
- That fix then broke again when the texture arrived, because `frame` crops a
  drifting window out of a padded canvas: the output and a freshly generated
  backdrop share neither texture phase nor gradient. Fixed by checking layout
  at `drift=0`, where the two align exactly, and checking the drift separately
  as arithmetic - `layout_floor + DRIFT <= SAFE_BOTTOM`.

A test that silently assumes something about the thing it measures fails when
that thing improves, which looks exactly like a regression.

## The compositor

Built because every measurable property was inside the reference bands and the
output still read flat. The missing thing was not a number, it was a layer.

`composite.py` assembles, back to front: the shared textured backdrop, an
optional rounded card with a soft shadow, a cut-out subject, a large headline
with an English gloss under it, and an annotation on a leader line. The subject
is generated on a plain white ground and matted with the *drawn* track's
`matting.auto_cutout` - which already chooses between the white and chroma
paths and suppresses the rim spill that would otherwise outline every subject
against a dark backdrop. No second matting implementation.

Four layouts, because the references use four: subject left with type right,
the mirror, subject centred under the type, and subject on a card with type
beside it.

### The fixed-offset bug, for the third time

The sub-line was placed at `headline_y + size * 1.15`, which assumes the
headline is one line. It is not, and "regenerated fibre" printed straight
across the second line of 再生化学纤维. This is the same mistake as the bilingual
caption and the opening hook - three separate places where one piece of text
was positioned from another's *font size* rather than from its measured block.

`_draw_text` now returns the bottom of what it drew, and callers stack off
that. If a fourth text layer is ever added, it stacks the same way.

### Column widths have to know what is beside them

The card layout's headline ran underneath the card, because the text column was
0.46 of frame width starting at 0.08 while the panel starts at 0.52. Columns
are per-layout now. Obvious in hindsight and invisible until rendered.

## The compositor was built, and the director never used it

First build after `composite.py` landed: zero composites across seven beats.
The instructions were in the prompt - verified by printing it - and the model
simply skipped an optional field, exactly as it had skipped the number
grounding, the label language and the `unit` field before it.

The fix was structural: a **required `shot`** on every beat, declared before
the specs, with the three choices presented as the first decision rather than
buried after a list of prohibitions. Composites appeared immediately.

That created one more bug worth recording. The field was first called `kind`,
which collides with the *graphic spec's own* `kind`, and the model duly
answered `kind: "counter"` - conflating the beat-level decision with the chart
type. Renamed to `shot`, and any chart name arriving in it is now read as a
declaration of "graphic". The validator **honours** the declared choice rather
than inferring one from whichever spec appeared, because inferring is exactly
what made composite skippable.

## A rejected graphic poisons the footage fallback

The subtler failure in the same build. Beat 5 declared a `comparison` whose
values came back as empty strings. Validation refused it - correctly - the beat
fell through to footage, and its `needs` still read "a side-by-side comparison
of two upward lines showing different slopes". Retrieval searched for that and
generation rendered it: a picture of a chart, which is the empty-whiteboard
failure arriving through the fallback instead of through the director's first
choice.

`repair_diagram_needs` detects shot descriptions carrying chart vocabulary on
beats that ended up as footage, and rewrites them as real-world scenes in one
batched call. The lesson generalises: **when a validator rejects something, the
fields written to support it are now stale, and something downstream will use
them anyway.**

## The pattern, four times over

| asked for | what happened | what worked |
|---|---|---|
| do not invent numbers | invented 33% | validate against the narration |
| labels in the narration's language | English titles over Chinese bars | detect and repair |
| supply the `%` unit | omitted on one chart in three | infer from how it was spoken |
| consider a composite | never chosen, not once | require an explicit `shot` |

For anything that must be correct rather than merely plausible, a prompt is a
request. Check the output, or make the choice structural.

## Every cut landed on an empty frame

Sampling frame density every half-second across a finished build, rather than
at five spread-out moments, showed something the spot checks could not:

```
   4.5s  20.5%  ############
   5.0s   0.6%                <- cut
   5.5s   5.9%  ###
   6.0s  13.9%  ########
   6.5s  20.3%  ############
```

The same shape at every one of the seven cuts: 0.6%, 5.6%, 6.8%, 8.5%,
climbing back over roughly a second. Every layer - subject, headline,
sub-line - ramped up from zero alpha, so each hard cut landed on bare
backdrop. Seven near-blank flashes in thirty-five seconds.

The references do the opposite. They cut **to** a finished composition and
animate an annotation over it: the red arrow labelling 后掠翼 draws itself on,
the dashed outline appears, but the frame under them is already made. So:

- The subject and headline no longer fade. They **settle** - pure translation,
  full opacity from the first frame.
- The annotation still draws itself on. That one is the moving part.
- Charts keep growing their bars, because that is the animation and the point.
  What they gained is a **scaffold** - gridlines at round values, labelled -
  that is present before any data arrives.

A composite is now 14% covered on the frame the cut lands on, against 1.3%.

## The density measure rewards texture, not quality

Worth writing down because it nearly sent the whole redesign the wrong way.
Frame density here means "pixels differing from a heavily blurred copy of the
frame". By that measure:

| | density |
|---|---|
| reference videos | 52-73% |
| a bar chart that reads well | 29% |
| a line chart that reads better | **7%** |

The line chart is the better frame. It scores a fifth as much because the
measure counts local detail, and photographic footage is full of local detail
while clean graphic design deliberately is not. Chasing the references' number
on made shots means adding noise.

The measure is kept, as `emptiest_frame_pct`, with a **floor** instead of a
band: below about 4% a frame is not sparse, it is blank. That is the failure
it actually found.

## Scene detection cannot count this track's cuts

`compare_to_reference` reported "0 cuts, mean shot 35.3s" on a seven-shot
video and flagged the mean as out of band. Every made shot shares one backdrop
and one palette by design, so a cut between a chart and a composite scores far
below any usable threshold:

| threshold | cuts found |
|---|---|
| 0.3 (the setting) | 0 |
| 0.2 | 1 |
| 0.05 | 15 |

There is no good threshold; the signal is not there. The builder already
writes `shots.json`, which knows exactly, so the report reads that when it is
beside the video and says which source it used. A measurement tool that
reports a confident wrong number is worse than one that declines to answer.

## drawbox does not re-evaluate its size per frame

The progress rule was first drawn with
`drawbox=w=iw*t/D:h=3:color=...:t=fill`. ffmpeg exits 0, writes a valid file,
and draws the rule at **full width on every frame**. Two separate causes, both
silent:

- A comma inside a filter option is a filtergraph *chain* separator, so
  `min(1,t/9)` split the graph mid-option.
- With the comma gone, `w=iw*t/4` still filled completely at every timestamp.
  Reduced to a 4-second black test clip, drawbox's size expressions are simply
  not re-evaluated per frame in this build (ffmpeg 9.0.1).

`overlay`'s `x` **is** evaluated per frame, exactly: an accent strip slid in
from the left measured 12% / 88% at t=0.5 / 3.5 of a 4 s clip. The rule is now
an overlay, and the selftest measures the filled fraction at three moments
rather than checking an exit code.

## The frame furniture belongs to the video, not the shot

A persistent eyebrow, progress rule and baseline were first drawn inside
`motion.frame` and `composite.frame`. That only reaches the kinds those two
modules render - so the moment a build contains a retrieved photographic shot,
the rule blinks out for five seconds and comes back. Furniture that comes and
goes is worse than none.

It is now composited once over the assembled video, which also puts it above
the per-shot vignette, where chrome belongs.

The eyebrow's topic is left **blank** unless `--topic` is passed. Nothing in
the script reliably names the video's subject in the narration's own language,
and a persistent wrong label on every frame is worse than no label. The rule
and baseline need no such guess and are always drawn.

## The fixed-offset bug, for the fourth time

Same shape as the bilingual caption, the opening hook, and the composite
sub-line. This time the annotation was clamped below the *headline's position*
so it would not collide - and still collided, because the English sub-line
hangs below the headline and the clamp did not know about it. Fixed by
clamping against `_draw_text`'s **returned bottom**, which is the whole reason
that function returns it.

The same annotation had a second bug the tests found: it was offset from the
subject *towards the rest of the frame*, and the rest of the frame is exactly
where the headline column is. It landed on the type in all four layouts, both
directions. It now sits inside the subject's own box, which by construction
never overlaps the text column.

## Non-UTF-8 consoles killed builds partway through

Windows encodes stdout with the console code page - cp1252 under Git Bash,
cp936 under a Chinese cmd.exe. Every script here prints Chinese, so the
`print` itself raised `UnicodeEncodeError`. Not at startup, where a missing
dependency would: at the first Chinese character, which is partway through a
build, after the narration and the images have been paid for. The traceback
names `charmap.py`, so it reads as a Python bug rather than a terminal
setting.

`scripts/console.py` reconfigures both streams to UTF-8 on import, and every
entry point imports it.

## Verified / not verified

Verified live, all three stages: query generation, search, and vision ranking.

Verified end to end: `prepare` → `locate` → `assemble` → captions, producing a
9.5 s 1920×1080 montage (2 × 5 s, one 0.5 s dissolve; ffprobe: 9.500000) with
subtitles at 62 px on scanline 975, the same geometry the drawn track uses.

Coverage measured: **0/4 on a modern script, 2/5 on an archival one**, both
Commons-only. This is the provider, not the funnel.

Not verified: Pexels end to end (no API key — search 401s); any sequence longer
than two shots; and the full `footage_build` orchestrator, which reached the
picture stage on its first run and was interrupted before completing.
