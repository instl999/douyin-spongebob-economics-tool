# plan.json and storyboard.json

Two files, written into the project's output directory.

`plan.json` is the director's output and the one **you edit by hand**. It holds
composition only — who is on screen, doing what, where — and no timing.

`storyboard.json` is generated from `plan.json` plus the measured narration
lengths, and is what the renderer consumes. Editing it directly works but is
overwritten on the next `--from storyboard`.

---

## plan.json

```json
{
  "title": "什么是效率工资",
  "setting": "the back kitchen of a busy burger restaurant",
  "mood": ["疑问", "讲解"],
  "sections": [{ "from": 1, "mood": ["疑问"] }, { "from": 7, "mood": ["转机"] }],
  "ending": { "text": "买到的是\n愿意做好的心气", "highlight": "心气" },
  "scenes": [
    {
      "id": 1,
      "narration": "蟹老板最近很烦恼。蟹堡王的后厨总是慢半拍。",
      "framing": "medium",
      "beat": { "subject": "krabs", "action": "frets over a slow kitchen",
                "object": "order tickets", "emotion": "worried", "relation": null },
      "elements": [
        { "who": ["krabs"], "shows": "wringing his claws beside a spike of unfilled order tickets",
          "asset": "krabs_wringing_his_claws_beside_a_3f9a1c.png",
          "x": 0.30, "y": 0.97, "h": 0.46, "rel": 1.0, "flip": false },
        { "type": "label", "text": "慢半拍", "tone": "bad", "for": "krabs",
          "x": 0.62, "y": 0.40, "anchor": "center" }
      ]
    }
  ],
  "drawings": [
    { "asset": "krabs_wringing_his_claws_beside_a_3f9a1c.png", "kind": "figure",
      "who": ["krabs"], "shows": "wringing his claws beside a spike of unfilled order tickets" }
  ],
  "problems": []
}
```

Every picture is drawn for this video from its `shows`. `drawings` lists each
one once - two elements described in the same words share a drawing - and an
element's `asset` is the drawing's filename, written by the validator from
`who` and `shows` with a hash of the description in it. **Edit `shows`, not
`asset`**: on the next run the plan stage sees the new description, names a
new file, and only that picture is drawn again. Editing a drawing's `shows` in
the `drawings` list moves every shot that uses it.

`setting` is the room the whole script sits in, drawn as the plate when the
project allows it (`fallback_setting`). `mood` and `sections` choose the music:
the moods of the whole script, and where its feeling turns - each section
starts on a shot and gets its own bed. `beat` is the director's reading of the
sentence; the sound cues are chosen from it, and with `voice.emotion` on, the
narration's tone too.

`problems` is a report from validation, not an input. It records every element
that was repaired, every prop put back on the ground, every framing run broken
up, and anything past the drawing budget. Worth reading after a fresh plan.

**`narration` is never written by the model.** The script is split by
`plan.split_script` and the model only chooses elements, so the text here is
always the user's own. Editing it here changes what is spoken and captioned;
editing the source script and re-planning is usually what you want.

### Coordinates

`x` and `y` are 0–1 **within the stage**, not the frame. The stage's bottom
edge is the ground line — 0.86 of frame height in landscape, 0.735 in
portrait, where the caption and the phone app's controls sit below it — so
`y: 0.97` puts feet on the ground and clear of the caption in either
orientation. See `layout.py` and `look.frame` in `casts/styles.json`.

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
| `who` | sprite | the characters in the drawing, by cast name; empty for a prop. Two names draw them together in one picture |
| `shows` | sprite | what the drawing shows - the description it is generated from |
| `asset` | sprite | the drawing's filename, derived from `who` and `shows`. Do not set by hand |
| `flip` | sprite | mirror the drawing, so a figure faces whoever it addresses |
| `z` | all | explicit depth, when the default bands are not enough; lower is further back |
| `type` | all | `sprite` (default), `label`, `bubble`, `panel` |
| `text` | label, bubble | the words |
| `tone` | label | `good`, `bad`, `money` or `neutral`; the colour is `look.label_tones` |
| `for` | label | the character the label is about; it is placed just above that character's head |
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
element will not look like them. The storyboard sets it for a label whose words
the narration says, to when they are said; set it by hand for anything else.

---

## storyboard.json

```json
{
  "video": {
    "orientation": "landscape", "width": 1920, "height": 1080, "fps": 30,
    "background": "background.png", "dissolve": 0.5,
    "crf": 20, "preset": "veryfast", "speed": 1.5,
    "panel_color": [176, 196, 205], "look": { "…": "the look, on this clock" }
  },
  "title_card":  { "text": "什么是效率工资", "duration": 1.73, "style": "title",
                   "size": 0.095, "max_width": 0.84,
                   "voice": "out/x/voice/title.mp3", "lead": 0.3 },
  "title_bar": { "text": "什么是效率工资" },
  "scenes": [
    {
      "id": 1, "duration": 2.93,
      "subtitle": "蟹老板最近很烦恼。蟹堡王的后厨总是慢半拍。",
      "captions": [
        { "text": "蟹老板最近很烦恼。", "start": 0.0,  "end": 1.21 },
        { "text": "蟹堡王的后厨总是慢半拍。", "start": 1.21, "end": 2.93 }
      ],
      "beat": { "…": "as in plan.json" },
      "elements": [ … as in plan.json … ]
    },
    { "id": 2, "transition": "cut", "…": "…" }
  ],
  "ending_card": { "text": "…", "highlight": "心气", "duration": 2.67,
                   "size": 0.07, "max_width": 0.84 },
  "sound_cues": [[0.0, "opening_dong", 1.0], [4.673, "cash"], [6.589, "chime"]],
  "music": { "volume": 0.16, "duck": true,
             "beds": [{ "path": "assets/bgm/疑问….mp3", "start": 0.0, "end": 16.8,
                        "fade_in": 1.2, "fade_out": 2.0 }] }
}
```

`title_bar` appears only where the layout asks for one (portrait, by default).
`sound_cues` and `music` are decided once, here, and read by both the mix and
the draft - a cue is `[seconds, name]` with an optional gain, and each bed runs
from `start` to `end`, crossfading with its neighbour when there are sections.

### video

| Field | Default | Meaning |
|---|---|---|
| `orientation` | `landscape` | `landscape` 1920×1080 or `portrait` 1080×1920 |
| `fps` | 30 | |
| `background` | `background.png` | the one plate; cover-cropped if it is not the frame's shape |
| `dissolve` | 0.5 | seconds of cross-dissolve between shots and cards |
| `crf` / `preset` | 20 / `veryfast` | x264 settings |
| `speed` | 1.5 | what the build ran at. **Recorded, not applied**: every duration in this file is already on that clock, so nothing downstream divides by it again. See the Speed section of CLAUDE.md |
| `panel_color`, `look` | from the style | carried so the renderer, the draft and its checker use one set of numbers |

### scenes

`duration` is the shot's slot: the measured narration length plus `tail_pad`
from the project file, both already at the video's `speed` - the narration
because it was spoken at that rate, the tail because `build.py` divided it. The
last shot has no tail. `captions` are the narration a phrase at a time, one
line each, changing at the pauses measured in the clip (by length where it has
none). If `captions` is absent the renderer derives them from `subtitle`.

`transition` is `"cut"` on a shot that shares a character with the one before
it: dissolved, the character would show twice, at two sizes. Absent, the shot
dissolves in.

### cards

| Field | Meaning |
|---|---|
| `text` | may contain `\n`; explicit breaks are honoured and each line wraps on its own |
| `style` | `title` for red-with-grey-offset, anything else for plain white |
| `highlight` | one phrase drawn in gold |
| `size` | type size as a fraction of frame width; per orientation in `look.frame` |
| `max_width` | the widest a line may run, as a fraction of frame width |
| `face` | `brush` (default, the bundled Ma Shan Zheng) or `sans`; a card with a character the brush face lacks is set in sans |
| `image` | optional pre-made PNG to show instead of drawn text |
| `duration` | seconds, at the video's `speed` |
| `voice` | title card only: the clip that reads it aloud, or **`null` when it is not read at all**. Always written, so a silent title is stated rather than implied. See below |
| `lead` | title card only, and only alongside `voice`: how far into the card the voice starts, so the draft lays the clip where the mix did |

`voice` is a decision, not a lookup. A title too long to read before shot 1 has
to start keeps its type on the card and loses its voice — `build.py` applies
that rule, and this field is the only record of the outcome. Both the draft
exporter and `check_draft` read it rather than going back to
`voice/index.json`, where the clip is still sitting: doing that spoke a title
the rendered MP4 was silent for, truncated into a card sized for no speech.
