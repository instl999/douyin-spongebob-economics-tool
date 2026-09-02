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
        self.sprites = self.dir / "sprites"

    @classmethod
    def load(cls, path, root=None):
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        cast = cls(data, root or path.parent)
        cast._merge_learned()
        return cast

    # --- poses the director asked for -------------------------------------

    @property
    def learned_path(self):
        return self.dir / "learned_poses.json"

    def _merge_learned(self):
        """Fold in poses earlier videos asked for, so they can be reused.

        Kept in a sidecar rather than written back into the cast file. The cast
        file is hand-authored and the one thing a user is expected to edit;
        a program that rewrites it while they have it open is a good way to
        lose their work, and a generated pose mixed in with their own gives
        them no way to tell which is which.
        """
        if not self.learned_path.exists():
            return
        try:
            learned = json.loads(self.learned_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return
        characters = self.data.setdefault("characters", {})
        for char_name, poses in (learned.get("poses") or {}).items():
            char = characters.get(char_name)
            if not char:
                continue          # the cast was edited; the pose has no owner
            # Hand-written poses win: a user who redefines a name means it.
            for pose, description in poses.items():
                char.setdefault("poses", {}).setdefault(pose, description)

    def is_learned(self, filename):
        """Whether this pose was requested for one beat rather than authored.

        A learned pose means one specific thing - "holding out a pay envelope"
        - so it belongs to the shot that asked for it and nowhere else. The
        hand-written poses in a cast file are postures and moods, which are
        interchangeable in a way that an action is not.
        """
        stem = filename[:-4] if filename.endswith(".png") else filename
        character, _, pose = stem.partition("_")
        if not self.learned_path.exists():
            return False
        try:
            learned = json.loads(self.learned_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return False
        return pose in ((learned.get("poses") or {}).get(character) or {})

    def learn_pose(self, character, pose, description):
        """Record a new pose for this cast. Returns its sprite filename."""
        char = (self.data.get("characters") or {}).get(character)
        if not char:
            raise ValueError(f"{character!r} is not a character in this cast")
        char.setdefault("poses", {})[pose] = description
        self.learned_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stored = json.loads(self.learned_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            stored = {}
        stored.setdefault("_README", [
            "Poses the director asked for while making a video, because no pose "
            "in the cast file expressed what the narration described.",
            "Generated once, then reused by every later video.",
            "Safe to delete: anything still wanted is simply asked for again.",
            "Editing casts/<style>.json wins over anything in here.",
        ])
        stored.setdefault("poses", {}).setdefault(character, {})[pose] = description
        self.learned_path.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        return f"{character}_{pose}.png"

    # --- prompt construction ---------------------------------------------
    def sprite_prompt(self, description, rules=SPRITE_RULES):
        return ", ".join(p for p in
                         [self.style, description, rules, CHROMA_PROMPT] if p)

    def prop_prompt(self, description):
        return self.sprite_prompt(description, PROP_RULES)

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

    def writable(self, filename):
        """Whether this prop is a surface meant to be written on.

        The distinction the layout needs is not "does text overlap something"
        but "does text overlap the *wrong* thing". A label centred on a blank
        whiteboard is the whole point of the whiteboard; the same label across
        a character's face is the defect.
        """
        stem = filename[:-4] if filename.endswith(".png") else filename
        if not stem.startswith("prop_"):
            return False
        return stem[len("prop_"):] in set(self.data.get("writable") or [])

    def in_front(self, filename):
        """Whether this prop is drawn over the characters rather than behind.

        A table the character stands behind has to overlap their legs, or the
        shot reads as two cut-outs placed side by side rather than as a scene.
        """
        stem = filename[:-4] if filename.endswith(".png") else filename
        if not stem.startswith("prop_"):
            return False
        return stem[len("prop_"):] in set(self.data.get("foreground") or [])

    def hangs(self, filename):
        """Whether this sprite belongs in the air rather than on the ground."""
        stem = filename[:-4] if filename.endswith(".png") else filename
        if not stem.startswith("prop_"):
            return False           # characters always stand
        return stem[len("prop_"):] in set(self.data.get("hanging") or [])

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

    def anchor_file(self, character):
        """The raw generated image to condition on, if it has been made."""
        pose = self.anchor_pose(character)
        if not pose:
            return None
        raw = self.dir / "raw" / f"{character}_{pose}.jpg"
        return raw if raw.exists() else None

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

    def brief(self):
        """What each sprite depicts, for the director to choose between.

        `catalogue` returns the full generation prompt - style, costume, chroma
        rules - which is what an image model needs and the wrong thing entirely
        to choose from. The director was given bare filenames instead and so
        picked poses by guessing at their names: asked for someone being handed
        a pay packet it chose `krabs_stand` and `sponge_happy`, because nothing
        told it that `krabs_greedy` is claws clasped and gleaming while
        `krabs_point` is a raised claw mid-explanation.

        Returns {"characters": {name: (role, {file: what the body is doing})},
                 "props": {file: what the object is}}.
        """
        characters = {}
        for name, char in (self.data.get("characters") or {}).items():
            poses = {f"{name}_{pose}.png": desc
                     for pose, desc in (char.get("poses") or {}).items()}
            characters[name] = (char.get("role", ""), poses)
        props = {f"prop_{name}.png": desc
                 for name, desc in (self.data.get("props") or {}).items()}
        return {"characters": characters, "props": props}

    def catalogue(self):
        """Every asset this cast can produce: {filename: prompt}."""
        out = {}
        for name, char in (self.data.get("characters") or {}).items():
            look = char.get("look", "")
            for pose, pose_desc in (char.get("poses") or {}).items():
                out[f"{name}_{pose}.png"] = self.sprite_prompt(
                    f"{look}, {pose_desc}")
        for name, desc in (self.data.get("props") or {}).items():
            out[f"prop_{name}.png"] = self.prop_prompt(desc)
        return out


class Library:
    """Builds and caches the sprites for one cast."""

    def __init__(self, cast, log=print):
        self.cast = cast
        self._anchor_cache = {}
        self.log = log
        self.sprites = cast.sprites
        self.sprites.mkdir(parents=True, exist_ok=True)
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

    def is_current(self, filename, prompt, size):
        entry = self.manifest.get(filename)
        return (entry and entry.get("fingerprint") == self._fingerprint(prompt, size)
                and (self.sprites / filename).exists())

    def build_sprite(self, filename, prompt, size, force=False, lock=None,
                     anchor=None):
        """Generate + matte one sprite unless the cache already has it.

        `lock` guards the shared manifest when several of these run at once.
        The manifest is written by the caller afterwards rather than on every
        sprite, so a parallel build does not have threads racing to rewrite the
        same file sixty times.

        `anchor` is a reference image of this character in another pose. It is
        a generation technique, not part of what the sprite is meant to be, so
        the cache still keys on the plain prompt: turning anchoring on does not
        invalidate a library that was built without it.
        """
        if not force and self.is_current(filename, prompt, size):
            return self.sprites / filename, False
        raw_path = self.raw / (Path(filename).stem + ".jpg")
        if anchor:
            ark.generate_image(self.cast.anchored_prompt(anchor["description"]),
                               raw_path, size=size,
                               reference_images=[anchor["uri"]])
        else:
            ark.generate_image(prompt, raw_path, size=size)
        info = matting.process_file(raw_path, self.sprites / filename)
        entry = {
            "fingerprint": self._fingerprint(prompt, size),
            "prompt": prompt, "size": size, "key": info["mode"],
            "anchored": bool(anchor),
            "coverage": round(info["coverage"], 4),
            "pixels": list(info["size"]), "built": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if lock is not None:
            with lock:
                self.manifest[filename] = entry
        else:
            self.manifest[filename] = entry
            self._save_manifest()
        return self.sprites / filename, True

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

    def build_all(self, size, only=None, force=False, workers=4):
        """Build the whole catalogue (or just `only`). Returns a small report.

        Generation is the slow part of a new cast - 28 images took 12 minutes
        serially, all of it waiting on the service - so requests go out in
        parallel. Four is deliberately modest: the point is to overlap the
        waiting, not to hammer a rate limit into failing half the library.
        """
        from concurrent.futures import ThreadPoolExecutor
        import threading

        catalogue = self.cast.catalogue()
        if only:
            wanted = set(only)
            catalogue = {k: v for k, v in catalogue.items() if k in wanted}
            for name in sorted(wanted - set(catalogue)):
                self.log(f"  ! {name} is not in the cast - skipping")

        todo = sorted(catalogue.items())
        total = len(todo)
        built, cached, failed = [], [], []
        lock = threading.Lock()
        counter = {"n": 0}

        def one(item):
            filename, prompt = item
            try:
                _, made = self.build_sprite(filename, prompt, size, force=force,
                                            lock=lock,
                                            anchor=self._anchor_for(filename))
            except Exception as exc:            # keep going; report at the end
                with lock:
                    counter["n"] += 1
                    failed.append((filename, str(exc)))
                    self.log(f"  [{counter['n']}/{total}] {filename}  FAILED: {exc}")
                return
            with lock:
                counter["n"] += 1
                (built if made else cached).append(filename)
                entry = self.manifest.get(filename, {})
                flag = ""
                if made and not (0.02 < entry.get("coverage", 1.0) < 0.95):
                    flag = "  <-- odd cutout coverage, check this one"
                self.log(f"  [{counter['n']}/{total}] {filename}  "
                         f"{'generated' if made else 'cached'}{flag}")

        # Anchors first, and alone. Every other pose of a character is drawn
        # against its anchor, so the anchor has to exist before the rest go out
        # - and if they all went out together, whichever won the race would be
        # a different character from the others.
        anchors = [t for t in todo if self._is_anchor(t[0])]
        rest = [t for t in todo if not self._is_anchor(t[0])]

        def run(batch):
            if not batch:
                return
            if workers > 1 and len(batch) > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    list(pool.map(one, batch))
            else:
                for item in batch:
                    one(item)

        run(anchors)
        run(rest)
        self._save_manifest()
        return {"built": built, "cached": cached, "failed": failed}

    def _is_anchor(self, filename):
        """Whether this sprite is the one the character's others are drawn from."""
        stem = filename[:-4] if filename.endswith(".png") else filename
        if stem.startswith("prop_"):
            return False           # a prop has no other poses to stay in step with
        character, _, pose = stem.partition("_")
        return pose and pose == self.cast.anchor_pose(character)

    def _anchor_for(self, filename):
        """The reference image for this sprite, or None to generate it plainly.

        Returns None for props, for the anchor itself, and whenever the anchor
        has not been drawn yet - a missing anchor makes a sprite slightly less
        consistent, which is the old behaviour and much better than refusing to
        draw it.
        """
        import base64
        stem = filename[:-4] if filename.endswith(".png") else filename
        if stem.startswith("prop_") or self._is_anchor(filename):
            return None
        character, _, pose = stem.partition("_")
        source = self.cast.anchor_file(character)
        if not source:
            return None
        description = ((self.cast.data.get("characters") or {})
                       .get(character, {}).get("poses", {}).get(pose))
        if not description:
            return None
        cached = self._anchor_cache.get(character)
        if cached is None:
            cached = ("data:image/jpeg;base64,"
                      + base64.b64encode(source.read_bytes()).decode())
            self._anchor_cache[character] = cached
        return {"uri": cached, "description": description}


    def available(self):
        return sorted(p.name for p in self.sprites.glob("*.png"))


def link_into(project_dir, cast, names=None):
    """Copy the sprites a project uses into its own directory.

    Copying rather than referencing keeps a finished project self-contained, so
    it still renders after the cast library is edited or moved.
    """
    import shutil
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in sorted(cast.sprites.glob("*.png")):
        if names and src.name not in names:
            continue
        dst = project_dir / src.name
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)
        copied.append(src.name)
    return copied
