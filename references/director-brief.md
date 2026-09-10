# Shots

The narration is already split. Do not change it, merge it, or add to it.
Return exactly {count} shots, with these ids.

{beats}

# Who is in this cast

Every picture in this video is drawn for it, from your description. Nothing is
picked off a shelf and nothing carries over from another video, so there is no
list of existing drawings to choose from - there is only this cast, and what
you say each one is doing.

{catalogue}

# Casting

{casting}

# For each shot

## First read the sentence, then cast it

Before choosing anything, work out what the sentence actually depicts, and put
it in the shot as "beat":

{{"subject": "who it happens to",
  "action": "what they physically do - a verb someone could perform",
  "object": "the thing involved, or null",
  "emotion": "how the subject feels about it, or null",
  "relation": "who does it to whom, or null"}}

Then choose {lo_elements}-{hi_elements} elements that **perform that action**.

The test is whether someone watching with the sound off would describe the
picture using the same verb as the sentence. A character standing next to the
thing the sentence mentions does not pass: "拿到工资" is not a person and a bag
of money in the same frame, it is a person **being handed** money and pleased
about it. Cast the *action*, then let the object and the feeling follow from it.

- **action** decides the pose. Pick the pose whose description contains that
  verb, not merely the character the sentence is about
- **emotion** decides which of the near-matching poses to use. The same beat
  ends differently if the subject is pleased or dismayed, and that difference
  is most of what the shot is for
- **object** goes in the frame, positioned so it is being acted on: held,
  handed over, pointed at, worked at - not parked beside someone
- **relation** also decides *facing*. A sprite points whichever way it was
  drawn, and often that is away from whoever it is addressing. Add
  `"flip": true` to mirror a character horizontally so the thing they are
  holding out, or the way they are turned, faces the other person. Put the
  giver and the receiver next to each other, not at opposite sides of the frame
- **relation** decides who else is on screen. "A pays B" needs both, facing
  each other. A one-sided action needs one
- Change who is on screen when the subject changes. One lone character with
  nothing happening is a wasted shot

## Describe every drawing

An element is a description of a picture, not the name of one. Say who is in it
and what the picture shows:

{{"who": "sponge",
  "shows": "both hands out taking a pay envelope, beaming, delighted",
  "x": 0.62, "y": 0.97, "h": 0.46}}

- **who** is one character name from the cast above. Leave it out entirely and
  you get an object instead - a prop, drawn on its own
- **shows** is what the drawing depicts: what the hands, arms, posture and face
  are doing, and what is being held. Do not describe costume, colour or art
  style - those come from the cast and are already settled. One figure per
  drawing unless you are asking for two on purpose (below)

"蟹老板把工资信封递过来，他双手接过" is not two people standing near money. It
is one figure holding an envelope out and another taking it with both hands,
and because you describe both of those, that is what gets drawn.

**Describe each shot afresh.** Two shots that you describe in the same words
become the same picture - which is sometimes right, and is what made the old
version of this tool put an identical shot of the boss in seven of thirty-two
frames. If the sentence has moved on, say what has changed.

### Objects

Leave `who` out and describe the thing:

{{"shows": "a fat stack of gold coins", "x": 0.72, "y": 0.97, "h": 0.30}}

Add `"role"` when the object is not simply standing on the ground:

- `"board"` - a whiteboard, chart, diagram or poster. Hangs at eye level, and
  emphasis text placed on it snaps to its middle
- `"furniture"` - a counter, desk or table. Drawn *over* the legs of whoever is
  at it, which is most of what makes a shot look like a place rather than
  cut-outs on a lawn. Any shot set at a workplace wants one
- `"hanging"` - a clock, a sign, anything on a wall

Left out, it is guessed from your description, and the guess is only as good as
the words: say "a whiteboard showing a rising line" and it hangs; say "a chart"
and it may not.

### When the interaction *is* the sentence

Two separate figures can never actually touch: the envelope is inside one
drawing and the hands that take it are inside another. For a sentence whose
whole point is that one does something *to* the other, ask for both in one
drawing by naming two:

{{"who": ["krabs", "sponge"],
  "shows": "Krabs holds out a pay envelope and SpongeBob takes it with both hands, beaming, the envelope passing between them",
  "x": 0.5, "y": 0.97, "h": 0.60}}

- name them left to right, and do not also place either of them separately in
  that shot - the drawing already contains both
- **shows** has to say what passes between them, who gives and who receives.
  One figure holding a thing and another touching it reads either way round
- give it `h` 0.58-0.66. Two figures share the height one character gets, and
  this shot is nearly always "medium" while the reactions either side are
  "close", so at the same `h` the pair ends up the smallest thing in the video
  - backwards, for the shot that carries the action. Below 0.58 is raised for you
- you have {duo_budget} of these. Two figures is twice the anatomy and half the
  attention per figure, so spend them on the beats that are genuinely an
  exchange and describe everything else one figure at a time

### How many

You have **{pose_budget} drawings** for the whole video, and every one of them
is drawn from scratch. Two elements described in the same words count once.
Past the cap, elements are dropped.

{orientation_note}

## Framing

Give every shot a "framing" of "wide", "medium" or "close". It scales the whole
shot, and it is how you vary the picture without moving anything.

- "close" for one character making a point, or a reaction
- "medium" for two characters, or a character with the prop they are using
- "wide" for three characters, or a big prop like a building

**Vary it.** A run of identically framed shots reads as a slideshow. Never use
the same value more than twice in a row, and aim for roughly a fifth of the
video to be "close" - a video that never gets near a face reads flat.

## Setting a shot somewhere else

Every video already sits somewhere: the `setting` you name at the end of your
answer is generated once and used as the backdrop behind *every* shot, so a
script that never names a place still gets a room rather than the cast's
default meadow. That is the floor, not the ceiling.

When one sentence names a *different* place from the video's own - an office in
a script set in a shop, a warehouse, a dock, a bank hall - put a wall behind
everyone for that shot:

{{"type": "panel", "x": 0.5, "y": 0.99, "w": 0.94, "ph": 0.5}}

x,y is the bottom centre in stage coordinates; w and ph are fractions of the
frame. **A wall reaches the frame edges**: w below 0.94 and ph below 0.45 are
raised to those, because a narrow slab reads as a card floating on the backdrop
rather than as a room. Add the furniture that belongs there on top of it - a
desk, a counter, a meeting table - and the place is built.

**Look through the shot list for every sentence that names a location and give
those shots a panel.** The generated backdrop covers the video's own place; a
panel is how a single shot goes somewhere else.

This instruction has been in the brief from the beginning and across seven
finished videos a panel was used **four times in eighty-seven shots**. The
result is that every shot of every video is the same empty meadow: measured,
the mean colour of one 32-shot video varied by three levels out of 255 from
start to finish. If the script names two or three places, two or three shots
need a panel. Do not put one on every shot - shots about an idea rather than a
place do not need one - but a video whose script mentions an office and never
shows one has failed at this.

**Furniture in front is the other half of it.** A counter, a desk, a table
placed at nearly the same x as a character is drawn *over their legs*, so they
read as standing behind it rather than beside it. That overlap is most of what
makes a shot look like a scene instead of cut-outs on a lawn, and it was used
in **six of eighty-seven shots**. Any shot set in a workplace wants one.

## Coordinates

x and y are 0-1 across the stage. y is where the *bottom* of a sprite sits.

- Characters on the ground: y 0.96-1.0, h 0.42-0.52.
  y = 1.0 is the ground line, so 0.97 means "standing on it"
- Props are usually smaller than the people using them: h 0.20-0.35 for a
  hand-held or table-top object, 0.35-0.50 for furniture, 0.50-0.65 only for a
  building. A stack of banknotes as tall as a person reads as a mistake
- **Boards, charts, calendars and maps are the exception: h 0.40-0.55.** They
  are usually what the shot is *about*, and they are the only thing that ever
  occupies the upper half of the frame. Measured, they were coming out at 0.27
  against a character's 0.45, which reads as a sticker on a wall of empty sky.
  A chart the audience is meant to read has to be readable
- One character: x 0.5. Two: x 0.30 and x 0.70. Three: 0.22, 0.5, 0.78
- A prop a character uses goes beside them, e.g. character x 0.34, prop x 0.64
- Furniture a character works AT - a counter, a desk, a sink, a table - goes at
  almost the same x as that character (within 0.06), not beside them. It is
  drawn over their legs, so they read as standing behind it. Use this to build
  a place: a counter plus an oven plus a character is a kitchen
- Boards, charts and maps hang at eye level: "anchor": "center", y 0.34-0.46
- Never leave a solid object floating in open water. Anything not hanging on a
  wall stands on the ground like everyone else
- **Put a label on most shots.** Two or three words on screen is what makes a
  point land and what a viewer scrolling without sound reads first. Measured
  across finished videos this ran from 1 label in 16 shots to 29 in 32; the
  sparse end is the mistake. Aim for two thirds of shots to carry one, and
  never invent a fact - a label names something the sentence already says
- Labels: "type": "label" with "text", "anchor": "center", just above or below
  the thing they name, y 0.20-0.55. Two or three words - a figure, a name, a
  before/after. Never a whole sentence. Add "tone": "good" when it names an
  improvement, "bad" for a loss or a problem, "money" for a figure or a price,
  and leave it off otherwise - it colours the label green, red or amber
- Speech bubbles: "type": "bubble" with "text", "anchor": "center", y 0.18-0.34,
  "tail": "left" or "right" leaning back toward the speaker. Under 15
  characters, used sparingly, for a character's own line
- **"appear": seconds from the shot start, for anything that should arrive
  rather than already be there.** This is the only way a shot develops instead
  of being a still held for five or six seconds, and across seven videos it was
  used **zero times**. A label lands better a beat after the sentence starts
  saying it; a chart lands better after the character has turned to it. Use it
  on most shots that run over four seconds and have more than one thing in
  them. (Emphasis text gets a default arrival if you leave it out, but you know
  the sentence and the code does not.)
- Draw order is worked out for you: walls behind, then hanging boards, then
  characters, then furniture over their legs. Only set "z" (a number, lower is
  further back) when you need something the bands cannot express - a character
  standing between two pieces of furniture

Never put the same character on screen twice in one shot. Do not overlap two
characters.

# Output

{{"title": "<= 10 characters, the question the video answers",
  "setting": "one place this whole script could sit in, 10-20 words, in the cast's world - a back kitchen, a small office, a shop counter, a bank hall. Used as the backdrop for every shot whose sentence names nowhere in particular, so pick the one that suits the subject rather than the first sentence",
  "ending": {{"text": "the closing line, may contain \\n", "highlight": "<= 4 characters from it"}},
  "shots": [
    {{"id": 1, "framing": "medium",
      "beat": {{"subject": "sponge", "action": "is handed his pay packet",
                "object": "pay envelope", "emotion": "delighted",
                "relation": "krabs hands it to sponge"}},
      "elements": [
        {{"who": ["krabs", "sponge"],
          "shows": "Krabs holds out a pay envelope and SpongeBob takes it with both hands, beaming, the envelope passing between them",
          "x": 0.50, "y": 0.97, "h": 0.60}},
        {{"shows": "a long shop counter", "role": "furniture",
          "x": 0.50, "y": 0.97, "h": 0.34}},
        {{"type": "label", "text": "发工资", "tone": "money",
          "x": 0.22, "y": 0.24, "anchor": "center"}}
      ]}}
  ]}}