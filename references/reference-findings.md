# What the reference videos actually do

Measured from three finished videos (`什么是地瓜经济`, `什么是效率工资`,
`什么是同群效应`), 1920×1080, 30 fps, 191–211 s each. Numbers here are what the
pixels say, not what the format looks like it is doing — several of them
contradict the obvious guess, which is why they are written down.

Everything in `render.py` follows from this page.

## The background is one static image

A per-pixel median across 70 frames sampled every 3 s reconstructs a clean
plate, and every frame matches it wherever no sprite covers it:

| Measurement | Result |
|---|---|
| Mean deviation from the median plate, character-free corner | **0.7–1.4 / 255** |
| Fraction of each frame matching the plate exactly | 65–80 % |
| Same background across all three videos | yes, identical |

0.7–1.4 grey levels is H.264 noise. There is **no parallax, no drifting bubble
layer, no camera move, no zoom**. The plate never moves at all.

This is the single most important finding, because the obvious guess — that a
"living" cartoon background must drift a little — is wrong, and implementing
that drift costs render time *and* makes the result look less like the target.

## A shot holds completely still, then dissolves

Consecutive-frame differences across an 8 s window, sampled at 10 fps:

```
t=91.0 … 91.5   0.021 0.011 0.005 0.005 0.003 0.001    <- frozen
t=92.5 … 93.1   17.0  8.8  15.1  23.0  24.6  18.9  9.7 <- the change
t=93.2 … 94.8   0.19  0.31  0.29  0.06  0.03  …  0.002 <- frozen again
```

So: hold dead still for several seconds, change over ~0.6 s, hold again.
Characters do not breathe. Props do not bob. Nothing sways.

The small 0.3–1.4 bumps inside a held stretch are the **subtitle changing while
the picture stays put** — one composition can carry two or three captions.

## The change is a group dissolve

Tracking foreground opacity through one transition:

| Time from start | What is on screen |
|---|---|
| 0.00 – 0.23 s | shot A, fully opaque |
| 0.23 – 0.43 s | A fading down |
| ~0.43 s | crossover, mostly bare background |
| 0.43 – 0.90 s | B fading up |
| 0.90 s on | shot B, fully opaque |

Total ≈ **0.65 s**, midpoint ≈ 0.43 s. Every element of the shot — characters,
props, labels, and the caption — fades **as one group**. No element slides in,
pops, scales or arrives on its own schedule. The background is untouched
throughout and is simply revealed at the crossover.

`render.py` defaults to a 0.5 s dissolve, which sits inside this range and
feels slightly tighter.

## Typography

Measured off full-resolution frames:

| Property | Value |
|---|---|
| Caption vertical centre | y ≈ 975 of 1080 = **90.3 %** |
| Caption size | ~62 px at 1920 wide = **0.032 × width** |
| Caption style | white fill, black stroke ~5 px, heavy weight, centred |
| Advance for 16 CJK characters | ~1050 px |
| Keyword labels | noticeably **larger** than the caption, dark fill, white outline, placed beside the thing they name |
| Gold highlight | used on the **closing card**, not in the running captions |

## Where things sit

| Element | Frame y |
|---|---|
| Horizon (grass line) in the plate | ~0.70 |
| Character feet | 0.77 – 0.82 |
| Caption centre | 0.903 |

Characters stand a little **below** the horizon and well **above** the caption.
This is why `layout.py` ends the stage at 0.86 of frame height rather than at
the frame edge: "y = 0.97" then means feet on the ground, clear of the caption,
in either orientation.

Character height ≈ 420 px of 1080 ≈ **0.39 of frame height**.

## Cards

- **Opening**: black frame, red brush-calligraphy title with a grey offset copy
  behind it, animating in.
- **Closing**: black frame, white brush calligraphy, two lines, one keyword in
  gold with a glow.

No calligraphic CJK face ships with Windows (only YaHei and SimSun), so
`render.py` approximates the opening card with a heavy weight plus the grey
offset. The lettering is the one place the local render is visibly not the
reference.

## Foreground elements

Each is a separate cut-out with a transparent background, composited over the
plate. Observed in the references:

- Characters, on model, in a handful of repeated poses
- Props: buildings, boats, crates, ovens, sinks, tables, plate stacks, charts
- Flat panels floating at eye level, used as boards and maps
- Speech balloons with wrapped text
- Big keyword labels

Elements **interleave in z-order** — a table passes in front of a character
whose legs are behind it — so draw order within a shot matters and the list is
back to front.

Notably **absent**: contact shadows. Sprites are composited flat, with no
ellipse or soft shadow beneath them. Adding one is an easy instinct and it is
not what the references do.
