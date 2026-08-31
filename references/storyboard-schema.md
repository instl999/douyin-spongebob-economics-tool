# plan.json and storyboard.json

Two files, written into the project's output directory.

`plan.json` is the director's output and the one **you edit by hand**. It holds
composition only — who is on screen, where — and no timing.

`storyboard.json` is generated from `plan.json` plus the measured narration
lengths, and is what the renderer consumes. Editing it directly works but is
overwritten on the next `--from storyboard`.

---

## plan.json

```json
{
  "title": "什么是效率工资",
  "ending": { "text": "买到的是\n愿意做好的心气", "highlight": "心气" },
  "scenes": [
    {
      "id": 1,
      "narration": "蟹老板最近很烦恼。蟹堡王的后厨总是慢半拍。",
      "framing": "medium",
      "elements": [
        { "asset": "prop_krusty_krab.png", "x": 0.62, "y": 0.99, "h": 0.55 },
        { "asset": "krabs_worried.png",    "x": 0.28, "y": 0.97, "h": 0.45 },
        { "type": "label", "text": "慢半拍", "x": 0.62, "y": 0.40, "anchor": "center" }
      ]
    }
  ],
  "problems": ["shot 7: 'krabs_happy.png' -> 'krabs_stand.png'"]
}
```

`problems` is a report from validation, not an input. It records every sprite
name that was corrected, every prop put back on the ground, and every framing
run broken up. Worth reading after a fresh plan.

**`narration` is never written by the model.** The script is split by
`plan.split_script` and the model only chooses elements, so the text here is
always the user's own. Editing it here changes what is spoken and captioned;
editing the source script and re-planning is usually what you want.

### Coordinates

`x` and `y` are 0–1 **within the stage**, not the frame. The stage's bottom
edge is the ground line — 0.86 of frame height in landscape, 0.80 in portrait —
so `y: 0.97` puts feet on the ground and clear of the caption in either
orientation. See `layout.py`.

`h` is the sprite's height as a fraction of stage height. `0.45` is a normal
adult character; the measured reference is ≈ 0.39 of *frame* height.

Elements are drawn **back to front** in list order.

### Framing

`framing` is `"wide"`, `"medium"` or `"close"` and scales every sprite in the
shot by 0.88, 1.0 or 1.30. It is the only camera control there is, and it
exists because a run of identically framed shots reads as a slideshow — the
references vary the subject size by about 1.4x. The validator breaks any run of
three identical framings.

### Draw order and depth

Elements composite in list order, back to front, and the validator sorts them
into three bands before writing the plan:

0. `panel` slabs — walls, floors, docks — behind absolutely everything
1. props the cast lists under `hanging` — boards, charts, maps — behind everyone
2. characters and everything else
3. props the cast lists under `foreground` — tables, sinks, counters — drawn
   over the legs of whoever stands at them

Within a band the director's own order is kept. Reordering by hand works, but
re-planning will sort it again.

### Panels

A `panel` is a flat slab drawn behind everyone: a wall, a floor, a quay. It is
how the references set a shot somewhere else without changing the background,
which they never do — a pale blue-grey rectangle behind Mr. Krabs reads as a
dock, a large grey one as the outside of a building. Nothing is generated for
it; it is a rounded rectangle drawn with PIL.

```json
{ "type": "panel", "x": 0.64, "y": 0.99, "w": 0.52, "ph": 0.38,
  "color": [176, 196, 205], "alpha": 242 }
```

`x`,`y` is the bottom centre in stage coordinates; `w` and `ph` are fractions
of the frame. Roughly one shot in four, not every shot.

### Element types

| Field | Applies to | Meaning |
|---|---|---|
| `asset` | sprite | filename from the cast catalogue; anything else is corrected to another pose of the same character, or dropped |
| `type` | all | `sprite` (default), `label`, `bubble`, `panel` |
| `text` | label, bubble | the words |
| `x`, `y` | all | stage coordinates, 0–1 |
| `h` | sprite | height as a fraction of stage height |
| `rel` | sprite | written by the validator from the cast's `relative_height`; keeps Squidward taller than Patrick whatever `h` says. Do not set by hand |
| `anchor` | all | `bottom` (default), `center`, `top`, `top_left` |
| `tail` | bubble | `left` or `right` — which side the pointer leans toward |
| `size` | label, bubble | multiplier on the default type size |
| `color`, `outline` | label | RGB triples |
| `max_width` | label, bubble | wrap width as a fraction of frame width |
| `w`, `ph` | panel | width and height as fractions of the frame |
| `color`, `alpha`, `radius` | panel | slab colour, opacity 0–255, corner rounding |
| `appear` | all | seconds after the shot starts; fades in over 0.28 s |

`appear` is the only per-element timing there is, and it is deliberate: the
references fade a whole shot as one group, so a storyboard that staggers every
element will not look like them. Use it for the one label that lands on a beat.

---

## storyboard.json

```json
{
  "video": {
    "orientation": "landscape", "width": 1920, "height": 1080, "fps": 30,
    "background": "background.png", "dissolve": 0.5,
    "crf": 20, "preset": "veryfast"
  },
  "title_card":  { "text": "什么是效率工资", "duration": 2.6, "style": "title", "size": 0.082 },
  "scenes": [
    {
      "id": 1, "duration": 4.39,
      "subtitle": "蟹老板最近很烦恼。蟹堡王的后厨总是慢半拍。",
      "captions": [
        { "text": "蟹老板最近很烦恼。", "start": 0.0,  "end": 1.65 },
        { "text": "蟹堡王的后厨总是慢半拍。", "start": 1.65, "end": 4.39 }
      ],
      "elements": [ … as in plan.json … ]
    }
  ],
  "ending_card": { "text": "…", "highlight": "心气", "duration": 4.0, "size": 0.062 }
}
```

### video

| Field | Default | Meaning |
|---|---|---|
| `orientation` | `landscape` | `landscape` 1920×1080 or `portrait` 1080×1920 |
| `fps` | 30 | |
| `background` | `background.png` | the one plate; cover-cropped if it is not the frame's shape |
| `dissolve` | 0.5 | seconds of cross-dissolve between shots and cards |
| `crf` / `preset` | 20 / `veryfast` | x264 settings |

### scenes

`duration` is the shot's slot: the measured narration length plus `tail_pad`
from the project file. `captions` are timed inside it by character count, so a
long shot shows two or three shorter captions in turn rather than one wall of
text. If `captions` is absent the renderer derives them from `subtitle`.

### cards

| Field | Meaning |
|---|---|
| `text` | may contain `\n`; explicit breaks are honoured and each line wraps on its own |
| `style` | `title` for red-with-grey-offset, anything else for plain white |
| `highlight` | one phrase drawn in gold |
| `size` | type size as a fraction of frame width |
| `image` | optional pre-made PNG to show instead of drawn text |
| `duration` | seconds |
