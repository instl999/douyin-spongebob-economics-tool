# Shots

The narration is already split. Do not change it, merge it, or add to it.
Return exactly {count} shots, with these ids.

{beats}

# Sprites you may use

Exact filenames; nothing else exists unless you ask for it (see below). The
text after each name is what that sprite shows - choose on that, not on the
name.

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

## When no pose performs the action - ask for one

The catalogue is mostly postures: standing, thinking, pleased, worried. Most
scripts describe things nobody in it is doing. **This is the normal case, not
an edge case, and asking is the normal response to it.**

Run this test on every shot, before you cast it:

> Read the pose descriptions for the character in `subject`. Does any of them
> describe a body performing `action`? Not the right mood - the right *action*.

If none does, ask for the pose:

{{"asset": "sponge_take_pay.png",
  "new_pose": "both hands out taking a pay envelope, beaming, delighted",
  "x": 0.62, "y": 0.97, "h": 0.46}}

"蟹老板把工资信封递过来，他双手接过" fails the test twice. No krabs pose
describes handing something over, and no sponge pose describes taking something
with both hands. `krabs_stand` plus `sponge_happy` plus a pile of coins is two
people standing near money - it is the failure this whole section exists to
prevent. Ask for `krabs_hand_over` and `sponge_take_pay` and the shot performs
the sentence.

- the filename must be `<character>_<pose>.png` for a character in the cast,
  and `<pose>` must be new, lowercase, no spaces
- `new_pose` describes the **body**: what the hands, arms, posture and face are
  doing. Do not describe clothing, colour or art style - those come from the
  cast. Do not name other characters; one figure only
- Do **not** ask when the difference is only mood and a pose already performs
  the action. `sponge_sad` covers any dejected standing
- You have {pose_budget} requests for the whole video. A script about people
  doing things should use most of them. Spend them on the shots where the
  action *is* the point, and settle on the shots that are only commentary
- Anything you ask for is drawn once and then belongs to this cast, so prefer
  a pose that other scripts would also use ("take_pay") over one welded to this
  sentence ("take_pay_from_krabs_on_friday")

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

The background never changes. When the narration names a *place* - an office, a
meeting, a shop, a warehouse, a dock, a kitchen - put a flat slab behind
everyone and the shot reads as being there:

{{"type": "panel", "x": 0.5, "y": 0.99, "w": 0.55, "ph": 0.34}}

x,y is the bottom centre in stage coordinates; w and ph are fractions of the
frame. Add the furniture that belongs there on top of it - a desk, a counter, a
meeting table - and the place is built.

**Look through the shot list for every sentence that names a location and give
those shots a panel.** It is the only way this format can leave the default
setting, so a video whose script mentions an office and never shows one has
missed something. Do not put one on every shot; shots that are about an idea
rather than a place do not need one.

## Coordinates

x and y are 0-1 across the stage. y is where the *bottom* of a sprite sits.

- Characters on the ground: y 0.96-1.0, h 0.42-0.52.
  y = 1.0 is the ground line, so 0.97 means "standing on it"
- Props are usually smaller than the people using them: h 0.20-0.35 for a
  hand-held or table-top object, 0.35-0.50 for furniture, 0.50-0.65 only for a
  building. A stack of banknotes as tall as a person reads as a mistake
- One character: x 0.5. Two: x 0.30 and x 0.70. Three: 0.22, 0.5, 0.78
- A prop a character uses goes beside them, e.g. character x 0.34, prop x 0.64
- Furniture a character works AT - a counter, a desk, a sink, a table - goes at
  almost the same x as that character (within 0.06), not beside them. It is
  drawn over their legs, so they read as standing behind it. Use this to build
  a place: a counter plus an oven plus a character is a kitchen
- Boards, charts and maps hang at eye level: "anchor": "center", y 0.34-0.46
- Never leave a solid object floating in open water. Anything not hanging on a
  wall stands on the ground like everyone else
- Labels: "type": "label" with "text", "anchor": "center", just above or below
  the thing they name, y 0.20-0.55. Two or three words - a figure, a name, a
  before/after. Never a whole sentence. Add "tone": "good" when it names an
  improvement, "bad" for a loss or a problem, "money" for a figure or a price,
  and leave it off otherwise - it colours the label green, red or amber
- Speech bubbles: "type": "bubble" with "text", "anchor": "center", y 0.18-0.34,
  "tail": "left" or "right" leaning back toward the speaker. Under 15
  characters, used sparingly, for a character's own line
- Anything arriving part-way through a shot: "appear": seconds from shot start
- Draw order is worked out for you: walls behind, then hanging boards, then
  characters, then furniture over their legs. Only set "z" (a number, lower is
  further back) when you need something the bands cannot express - a character
  standing between two pieces of furniture

Never put the same character on screen twice in one shot. Do not overlap two
characters.

# Output

{{"title": "<= 10 characters, the question the video answers",
  "ending": {{"text": "the closing line, may contain \\n", "highlight": "<= 4 characters from it"}},
  "shots": [
    {{"id": 1, "framing": "medium",
      "beat": {{"subject": "sponge", "action": "is handed his pay packet",
                "object": "pay envelope", "emotion": "delighted",
                "relation": "krabs hands it to sponge"}},
      "elements": [
        {{"asset": "krabs_point.png", "x": 0.30, "y": 0.97, "h": 0.46}},
        {{"asset": "sponge_take_pay.png",
          "new_pose": "both hands out taking a pay envelope, beaming, delighted",
          "x": 0.66, "y": 0.97, "h": 0.46}},
        {{"type": "label", "text": "发工资", "tone": "money",
          "x": 0.48, "y": 0.40, "anchor": "center"}}
      ]}}
  ]}}