"""The sprite library: generate once, matte, cache, reuse.

Character consistency across fifty-odd shots is the hard problem in this
format, and the way out is not to fight a prompt into behaving. Each pose is
generated exactly once, cut out, and stored under the cast; every video that
uses that cast composites the same PNG. A character therefore cannot drift,
because nothing regenerates it.

Sprites are generated on a saturated chroma background rather than white. See
matting.py for why that choice does most of the cutout work.

The cache key is the full prompt, so editing a pose description regenerates
only that pose and leaves the rest of the library alone.
"""
import hashlib
import json
import time
from pathlib import Path

import ark
import config
import matting

# Magenta is a safer key than green here: the palette is full of greens - grass,
# eye stalks, Patrick's shorts - and none of it is near pure magenta.
CHROMA_PROMPT = ("solid pure magenta background, RGB 255 0 255, flat uniform "
                 "chroma key backdrop, no gradient, no texture, no shadow")

SPRITE_RULES = ("full body, complete figure, centred, nothing cropped, "
                "no drop shadow, no ground, no scenery, no text, no watermark")

# Props need saying twice. Asked for "a stack of plates" in this style, the
# model helpfully adds the character who would be holding them, and a prop
# sprite with a character baked in puts that character on screen twice.
PROP_RULES = ("a single inanimate object on its own, no characters, no people, "
              "no creatures, no hands, no arms, nothing else in the picture, "
              "centred, complete, no drop shadow, no ground, no scenery, "
              "no text, no watermark")

# Two figures in one image, for a beat where one acts on the other. Everything
# else here is one character per sprite, which is what makes the library
# reusable - but it also means a handover can never actually connect, because
# the envelope lives in one PNG and the hands that take it live in another. No
# arrangement of two rectangles fixes that. For the beats where the interaction
# *is* the sentence, the two figures have to be drawn together.
# Two figures is roughly twice the anatomy to get right and about half the
# attention per figure, and it shows: the first handover came back with Mr.
# Krabs holding the envelope with a claw that was not attached to him, plus a
# spare one at his side. Limb count and attachment therefore have to be said
# out loud, the way PROP_RULES has to say "no characters" twice.
#
# Direction has to be said too. One figure holding an object and another
# touching it is symmetric, and reads either way round: the giver needs a
# visibly extended arm and the receiver visibly open, reaching hands.
DUO_RULES = ("both characters fully visible and complete, standing close "
             "together and physically interacting, turned toward each other, "
             "the giver's arm clearly extended and the receiver's hands open "
             "and reaching so it is obvious which way the action goes, "
             "each character anatomically correct with the normal number of "
             "limbs, every arm and hand plainly attached to the body it "
             "belongs to, no duplicated limbs, no detached or floating hands, "
             "nothing cropped, no drop shadow, no ground, no scenery, "
             "no text, no watermark")

DUO_PREFIX = "duo_"

# How many times to draw an interaction before choosing. Off by default, and
# the reason is worth recording rather than repeating.
#
# Drawing twice and keeping the better one only helps if the selector is better
# than a coin flip, and this one could not be shown to be. Calibrated on a pair
# where the answer was clear - one had a claw detached from its owner, the
# other did not - it chose the detached one, both times, in both orders. It is
# consistent and it is not right, which is the same failure the absolute judge
# in critique.py had: this model is a poor judge of its own pictures.
#
# So the second draw is available and not default. If an interaction comes out
# wrong, delete its PNG and rebuild: one call, and a person deciding, which is
# the only reliable judge here.
DUO_CANDIDATES = 1

COMPARE_PROMPT = """这 {count} 张图画的是同一个内容：

{description}

选出最好的一张。判断顺序：
1. 解剖是否正确 —— 有没有多出来的手/爪子、没连在身上的肢体、畸形的四肢
2. 动作方向是否清楚 —— 谁在给、谁在接，一眼能不能看出来
3. 两个角色是否都完整、都在互动

只回一个数字（1 到 {count}），不要任何其他内容。"""

# Sprites are generated square and cropped to their own bounds, so the video's
# orientation is irrelevant to them. Keeping the size fixed means one library
# serves landscape and portrait projects instead of two. 1920x1920 is exactly
# the plan's 3,686,400 pixel minimum.
SPRITE_SIZE = "1920x1920"

# Matches the reference title cards: red brush lettering, grey offset copy,
# black ground. The model is reliable about this look and unreliable about the
# characters, which is why build_title_card verifies them.
TITLE_PROMPT = (
    "中文书法艺术字『{text}』，"
    "狂草毛笔字，鲜红色字体"
    "带灰白色立体投影，笔画"
    "有飞白和枯笔质感，纯黑"
    "色背景，横向排列一行，"
    "字体清晰完整准确")

# The fallback backdrop for shots the script gives no place to. Without one, a
# video alternates between built rooms and bare meadow, which reads worse than
# either on its own. It is a wall seen from in front - not a landscape - so it
# has to be flat, frontal and empty in the middle where the characters stand.
SETTING_RULES = ("a flat frontal view of an interior wall or backdrop, "
                 "seen straight on, no perspective vanishing point, "
                 "no characters, no people, no creatures, no text, "
                 "no watermark, nothing in the foreground, "
                 "the middle and lower area plain and uncluttered so figures "
                 "can stand in front of it, detail only near the top and edges")

BACKGROUND_RULES = ("wide establishing background plate, no characters, "
                    "no people, no text, no watermark, nothing in the "
                    "foreground, empty stage with clear space in the lower half")


class Cast:
    """A style, a background, a set of characters with poses, and props."""

    def __init__(self, data, root):
        self.data = data
        self.root = Path(root)
        self.name = data.get("name", "cast")
        self.style = data.get("style", "")
        self.dir = self.root / self.name

    @classmethod
    def load(cls, path, root=None):
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        cast = cls(data, root or path.parent)
        return cast




    # --- prompt construction ---------------------------------------------
    def sprite_prompt(self, description, rules=SPRITE_RULES):
        return ", ".join(p for p in
                         [self.style, description, rules, CHROMA_PROMPT] if p)

    def prop_prompt(self, description):
        return self.sprite_prompt(description, PROP_RULES)

    def duo_prompt(self, members, description):
        """One image of two named characters doing something to each other."""
        characters = self.data.get("characters") or {}
        looks = [f"{name} ({(characters.get(name) or {}).get('look', '')})"
                 for name in members]
        return ", ".join(p for p in [
            self.style, " and ".join(looks), description,
            DUO_RULES, CHROMA_PROMPT] if p)

    def duo_members(self, filename):
        """The two characters in an interaction sprite, or [] if it is not one."""
        stem = filename[:-4] if filename.endswith(".png") else filename
        if not stem.startswith(DUO_PREFIX):
            return []
        rest = stem[len(DUO_PREFIX):].split("_")
        characters = self.data.get("characters") or {}
        # duo_<a>_<b>_<action>: the action can contain underscores, the names
        # cannot, so the members are however many leading parts are characters.
        members = []
        for part in rest:
            if part in characters and len(members) < 2:
                members.append(part)
            else:
                break
        return members if len(members) == 2 else []



    def relative_height(self, filename):
        """How tall this sprite is relative to the cast's baseline character.

        The director picks one `h` per shot and has no way to keep the cast in
        proportion across shots, so Squidward would be Patrick's height in one
        shot and twice it in the next. Multiplying by a fixed per-character
        figure makes the relationship a property of the cast instead.
        """
        stem = filename[:-4] if filename.endswith(".png") else filename
        if stem.startswith("prop_"):
            return 1.0
        char = (self.data.get("characters") or {}).get(stem.split("_", 1)[0])
        return float((char or {}).get("relative_height", 1.0))

    @property
    def panel_color(self):
        """Slab colour for this style's `panel` elements.

        A default that reads on a teal undersea plate disappears on a cream
        office wall, so the colour belongs to the cast rather than the renderer.
        """
        value = self.data.get("panel_color") or (176, 196, 205)
        return tuple(int(v) for v in value)[:3]




    def setting_prompt(self, description):
        """A themed backdrop for one video, in this cast's art style."""
        return ", ".join(p for p in [self.style, description, SETTING_RULES] if p)

    def background_prompt(self):
        bg = self.data.get("background", {})
        return ", ".join(p for p in
                         [self.style, bg.get("prompt", ""), BACKGROUND_RULES] if p)

    def problems(self):
        """Everything wrong with this cast file, in plain language.

        A cast is the one file a user is expected to edit, so the failure mode
        to design for is a hand-edit that looks fine and quietly does nothing -
        a prop listed under `hanging` whose name does not match any prop, for
        instance, floats exactly as before and gives no hint why.
        """
        issues = []
        # A template copied but not filled in validates as structurally fine
        # and then generates 60 images of "REPLACE ME", so the placeholder is
        # itself an error.
        blob = json.dumps(self.data, ensure_ascii=False)
        if "REPLACE ME" in blob:
            issues.append("still contains REPLACE ME placeholders - fill the "
                          "template in before building")
        if not self.data.get("style"):
            issues.append("no `style` - every generated image will be styleless")
        if not (self.data.get("background") or {}).get("prompt"):
            issues.append("no `background.prompt` - there is no plate to composite on")

        characters = self.data.get("characters") or {}
        if not characters:
            issues.append("no `characters`")
        for name, char in characters.items():
            if not char.get("look"):
                issues.append(f"character {name!r} has no `look`")
            if not (char.get("poses") or {}):
                issues.append(f"character {name!r} has no poses")
            height = char.get("relative_height", 1.0)
            if not isinstance(height, (int, float)) or not 0.3 <= height <= 3.0:
                issues.append(
                    f"character {name!r} has relative_height {height!r}; "
                    "expected a number between 0.3 and 3.0")
            if "_" in name:
                issues.append(
                    f"character name {name!r} contains an underscore, which is "
                    "the separator for poses - sprite names would be ambiguous")

        props = set(self.data.get("props") or {})
        for key in ("hanging", "foreground", "writable"):
            listed = self.data.get(key) or []
            if not isinstance(listed, list):
                issues.append(f"`{key}` must be a list of prop names")
                continue
            for entry in listed:
                if entry not in props:
                    issues.append(
                        f"`{key}` lists {entry!r}, which is not a prop in this "
                        "cast - it will have no effect")
        overlap = set(self.data.get("hanging") or []) & set(self.data.get("foreground") or [])
        if overlap:
            issues.append(
                f"{sorted(overlap)} are in both `hanging` and `foreground`; "
                "`hanging` wins, so the `foreground` entry does nothing")
        return issues

    def anchor_pose(self, character):
        """The pose that defines what this character looks like.

        Every sprite is generated from a text prompt, independently, so the
        same character drifts between poses - the description says "a stout
        boss in a brown waistcoat" and the model settles a slightly different
        face, build and palette each time. One pose is generated first and then
        used as a reference image for the rest, which pins the design. The cast
        may name it; otherwise the first pose listed is it, since cast files
        put the neutral standing pose first by convention.
        """
        char = (self.data.get("characters") or {}).get(character) or {}
        poses = char.get("poses") or {}
        named = char.get("anchor")
        if named in poses:
            return named
        return next(iter(poses), None)

    def anchor_path(self, character):
        """Where this character's reference image lives. Tracked, not scratch.

        It used to be read out of `raw/`, which is the pre-matting scratch dir
        and is gitignored - so a fresh clone had no anchors at all and silently
        generated everything unanchored, at 82% palette match instead of 98%.
        That was survivable only while the sprite library itself was committed
        and nothing needed generating. Now that every image in a video is drawn
        fresh, this one file per character is the only thing holding a
        character together, so it has a home of its own and is committed.
        """
        return self.dir / "anchors" / f"{character}.jpg"

    def anchor_file(self, character):
        """The image to condition on, if it has been made."""
        settled = self.anchor_path(character)
        if settled.exists():
            return settled
        # Libraries built before anchors had their own directory keep theirs
        # among the scratch images. Read it there rather than redrawing a
        # character that already has a settled design.
        pose = self.anchor_pose(character)
        if pose:
            raw = self.dir / "raw" / f"{character}_{pose}.jpg"
            if raw.exists():
                return raw
        return None

    def anchored_prompt(self, description):
        """Prompt for a pose generated against the character's anchor.

        The reference pins identity hard - palette match to the anchor goes
        from 82% to 98% - but it also pulls the pose back toward the reference,
        and a "pointing" sprite came back with the arm barely raised. So the
        prompt has to say plainly which half is being copied and which half is
        being replaced, and lead with the change.
        """
        return ", ".join(p for p in [
            f"the same character as the reference image, now {description}",
            "keep the face, colours, costume, proportions and art style of the "
            "reference exactly as they are; change only the pose and expression",
            SPRITE_RULES, CHROMA_PROMPT] if p)

    def anchored_duo_prompt(self, members, description):
        """Two characters, drawn against both their anchors, doing something."""
        characters = self.data.get("characters") or {}
        looks = " and ".join(
            f"{name} ({(characters.get(name) or {}).get('look', '')})"
            for name in members)
        return ", ".join(p for p in [
            f"the same two characters as the reference images ({looks}), "
            f"now {description}",
            "keep each character's face, colours, costume, proportions and art "
            "style exactly as the references show them",
            DUO_RULES, CHROMA_PROMPT] if p)




class Library:
    """Draws what one video needs, and the references a cast keeps."""

    def __init__(self, cast, log=print):
        self.cast = cast
        self._anchor_cache = {}
        self.log = log
        self.raw = cast.dir / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.manifest_path = cast.dir / "manifest.json"
        self.manifest = (json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
                         if self.manifest_path.exists() else {})

    def _save_manifest(self):
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _fingerprint(prompt, size):
        return hashlib.sha256(f"{size}\n{prompt}".encode("utf-8")).hexdigest()[:16]




    def build_background(self, out_path, size, force=False):
        """The one plate the whole video sits on. Not matted.

        Cached under the cast, not the project, then copied in. All three
        reference videos share a single identical background, and a series only
        reads as a series if every episode sits on the same plate - a
        per-project regeneration would drift the coral and the horizon from one
        video to the next.
        """
        import shutil
        prompt = self.cast.background_prompt()
        fingerprint = self._fingerprint(prompt, size)
        cached = self.cast.dir / f"background_{size}.png"
        key = f"background::{size}"
        out_path = Path(out_path)

        fresh = (cached.exists()
                 and self.manifest.get(key, {}).get("fingerprint") == fingerprint)
        if force or not fresh:
            ark.generate_image(prompt, cached, size=size)
            self.manifest[key] = {"fingerprint": fingerprint, "prompt": prompt,
                                  "size": size,
                                  "built": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._save_manifest()
        made = force or not fresh
        # build_library asks for the plate at its own cached path, so source and
        # destination are the same file - copying it onto itself raises
        # PermissionError on Windows rather than being a harmless no-op.
        same = out_path.resolve() == cached.resolve()
        if not same and (not out_path.exists() or made
                         or out_path.stat().st_mtime < cached.stat().st_mtime):
            shutil.copy2(cached, out_path)
        return out_path, made

    def build_setting(self, description, out_path, size, force=False):
        """Generate the video's fallback backdrop. Returns (path, made).

        The *file* belongs to one video - it is derived from that script's
        subject, unlike the plate, which is what makes a series look like a
        series and must never drift. The bookkeeping still lives in the cast's
        manifest, so the key carries the description: on a single "setting"
        key, two scripts sharing a style would each find the other's
        fingerprint and regenerate a backdrop the other had already paid for,
        every time the two were built in turn.
        """
        prompt = self.cast.setting_prompt(description)
        fingerprint = self._fingerprint(prompt, size)
        key = f"setting::{fingerprint}"
        out_path = Path(out_path)
        if (not force and out_path.exists()
                and self.manifest.get(key, {}).get("fingerprint") == fingerprint):
            return out_path, False
        ark.generate_image(prompt, out_path, size=size)
        self.manifest[key] = {"fingerprint": fingerprint, "prompt": prompt,
                              "size": size,
                              "built": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._save_manifest()
        return out_path, True

    def build_anchor(self, character, size, force=False):
        """Draw the one reference image that defines a character. (path, made).

        This is the only drawing that outlives a video. Everything else is made
        for one script and thrown away, which is the point - a shared library
        is why every video used to open on the same picture. But identity has
        to come from somewhere: generated from the description alone, the same
        character drifts between shots of a single video, measured at 82%
        palette match against 98% when every drawing is conditioned on one
        reference. So: one file per character, committed, and nothing else.
        """
        char = (self.cast.data.get("characters") or {}).get(character) or {}
        look = (char.get("look") or "").strip()
        if not look:
            raise ValueError(f"{character} has no `look` to draw from")
        out_path = self.cast.anchor_path(character)
        if out_path.exists() and not force:
            return out_path, False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        prompt = self.cast.sprite_prompt(
            f"{look}, standing squarely facing the viewer, arms relaxed at the "
            "sides, neutral friendly expression, the definitive reference "
            "drawing of this character")
        ark.generate_image(prompt, out_path, size=size)
        self._anchor_cache.pop(character, None)
        return out_path, True

    def build_drawing(self, spec, out_dir, size, force=False):
        """Draw one described element for one video. Returns (path, made).

        The filename already carries a hash of what was asked for, so the file
        being there *is* the cache - re-running a stage redraws nothing, and
        two shots that described the same picture share it. Nothing is written
        to the cast, so nothing reaches the next video.
        """
        out_dir = Path(out_dir)
        out_path = out_dir / spec["asset"]
        if out_path.exists() and not force:
            return out_path, False
        who, shows = list(spec.get("who") or []), spec["shows"]
        uris = [uri for uri in (self._anchor_uri(name) for name in who) if uri]
        if len(who) == 2:
            prompt = (self.cast.anchored_duo_prompt(who, shows) if uris
                      else self.cast.duo_prompt(who, shows))
        elif who:
            prompt = (self.cast.anchored_prompt(shows) if uris
                      else self.cast.sprite_prompt(
                          f"{(self.cast.data['characters'][who[0]]).get('look','')}, {shows}"))
        else:
            prompt, uris = self.cast.prop_prompt(shows), []
        raw_path = out_dir / "raw" / (out_path.stem + ".jpg")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ark.generate_image(prompt, raw_path, size=size,
                           reference_images=uris or None)
        matting.process_file(raw_path, out_path)
        return out_path, True

    def build_title_card(self, text, out_path, size, force=False, attempts=2):
        """Generate brush-calligraphy lettering, and check that it reads right.

        Image models are good at this style and unreliable about the exact
        characters, so the result is read back with the vision model and only
        kept when it matches. Two misses and the caller falls back to drawn
        text, which is never wrong and never as good.

        Returns (path_or_None, note).
        """
        prompt = TITLE_PROMPT.format(text=text)
        key = f"title::{text}::{size}"
        fingerprint = self._fingerprint(prompt, size)
        out_path = Path(out_path)
        if (not force and out_path.exists()
                and self.manifest.get(key, {}).get("fingerprint") == fingerprint):
            return out_path, "cached"

        wanted = "".join(ch for ch in text if not ch.isspace())
        for attempt in range(1, attempts + 1):
            try:
                ark.generate_image(prompt, out_path, size=size)
            except Exception as exc:
                return None, f"generation failed: {exc}"
            try:
                seen = ark.read_image_text(
                    out_path,
                    "这张图里的中文字是什么？只输出那几个字，不要标点，不要任何其他内容。")
            except Exception as exc:
                return None, f"could not verify the lettering: {exc}"
            got = "".join(ch for ch in seen if not ch.isspace())
            if got == wanted:
                self.manifest[key] = {"fingerprint": fingerprint, "prompt": prompt,
                                      "size": size, "verified": got,
                                      "built": time.strftime("%Y-%m-%d %H:%M:%S")}
                self._save_manifest()
                return out_path, f"verified on attempt {attempt}"
            self.log(f"  title lettering read back as {got!r}, wanted {wanted!r}"
                     f" - attempt {attempt}/{attempts}")
        out_path.unlink(missing_ok=True)
        return None, "lettering never matched; drawing it instead"




    def _anchor_uri(self, character):
        """The character's anchor image as a data URI, read once."""
        import base64
        if character not in self._anchor_cache:
            source = self.cast.anchor_file(character)
            self._anchor_cache[character] = (
                "data:image/jpeg;base64,"
                + base64.b64encode(source.read_bytes()).decode()
                if source else None)
        return self._anchor_cache[character]




