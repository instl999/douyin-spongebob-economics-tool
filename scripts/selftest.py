"""Prove the installation works, without touching the network.

    python scripts/selftest.py

Every stage that does not need an API is exercised on synthetic assets: the
script splitter, the duration model, matting, the layout checks, the renderer,
the audio mix and the verifier. It builds a real four-second MP4 in a temp
directory and runs the full check suite against it.

This is the thing to run after installing, after editing anything, or when a
build fails and it is not obvious whether the pipeline or the API is at fault.
It spends nothing, so it can be run freely.
"""
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: F401  UTF-8 stdout; see console.py

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

PASS, FAIL = "[ok]  ", "[FAIL]"


class Suite:
    def __init__(self):
        self.failures = 0

    def check(self, name, condition, detail=""):
        ok = bool(condition)
        if not ok:
            self.failures += 1
        print(f"  {PASS if ok else FAIL} {name}{('  ' + detail) if detail else ''}")
        return ok


def synthetic_assets(directory):
    """A plate and three sprites, one with an enclosed hole to matte."""
    W, H = 1920, 1080
    plate = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(plate)
    for y in range(H):
        k = y / H
        draw.line([(0, y), (W, y)], fill=(int(70 + 60 * k), int(200 - 20 * k),
                                          int(205 - 25 * k)))
    draw.rectangle([0, int(H * 0.70), W, H], fill=(120, 205, 70))
    plate.save(directory / "background.png")

    for name, colour, size in (("a.png", (230, 60, 60), (420, 520)),
                               ("b.png", (250, 220, 60), (380, 470)),
                               ("prop_c.png", (150, 150, 160), (300, 260))):
        w, h = size
        sprite = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(sprite)
        d.ellipse([4, 4, w - 4, h - 4], fill=colour + (255,),
                  outline=(20, 20, 20, 255), width=8)
        d.ellipse([w * 0.3, h * 0.25, w * 0.45, h * 0.4], fill=(255, 255, 255, 255))
        sprite.save(directory / name)


def main():
    print("cartoon-econ-video self-test (offline)\n")
    suite = Suite()

    # --- imports -----------------------------------------------------------
    try:
        import assets as assets_mod
        import audio as audio_mod
        import checks as checks_mod
        import matting
        import plan as plan_mod
        import render as render_mod
        import textkit
        import timing
        import verify as verify_mod
        from layout import Layout
        suite.check("every module imports", True)
    except Exception as exc:
        suite.check("every module imports", False, str(exc))
        return 1

    # --- casts -------------------------------------------------------------
    import styles as styles_mod
    reg_issues = styles_mod.problems()
    suite.check("style registry casts/styles.json", not reg_issues,
                reg_issues[0] if reg_issues else "")
    suite.check("registry has a usable default",
                styles_mod.default_key() is not None,
                f"default: {styles_mod.default_key()}")
    for key, entry in sorted(styles_mod.available().items()):
        issues = assets_mod.Cast.load(entry["file"],
                                      root=ROOT / "casts").problems()
        suite.check(f"cast {key}", not issues,
                    issues[0] if issues else "")
        if entry["registered"]:
            suite.check(f"cast {key} file exists", entry["file"].exists(),
                        str(entry["file"]))

    # --- the config actually drives the code ------------------------------
    # A settings file that everything ignores looks exactly like one that
    # works. These assert the wiring rather than the file: every look value
    # the modules use has to be the one styles.py resolved, so re-hardcoding
    # any of them fails here instead of silently making the file decorative.
    look = styles_mod.look()
    wired = {
        "render.FRAMING": (render_mod.FRAMING, look["framing"]),
        "render.CAPTION_FADE": (render_mod.CAPTION_FADE,
                                look["timing"]["caption_fade"]),
        "render.ELEMENT_FADE": (render_mod.ELEMENT_FADE,
                                look["timing"]["element_fade"]),
        "checks.MIN_GAP": (checks_mod.MIN_GAP, look["safe_zones"]["min_gap"]),
        "checks.SIDE_MARGIN": (checks_mod.SIDE_MARGIN,
                               look["safe_zones"]["side_margin"]),
        "checks.TOP_MARGIN": (checks_mod.TOP_MARGIN,
                              look["safe_zones"]["top_margin"]),
        "checks.EDGE_TOLERANCE": (checks_mod.EDGE_TOLERANCE,
                                  look["safe_zones"]["edge_tolerance"]),
        "matting.CHOKE": (matting.CHOKE, look["matting"]["choke"]),
        "matting.RIM_PIXELS": (matting.RIM_PIXELS, look["matting"]["rim_pixels"]),
        "plan.LABEL_TONES": (set(plan_mod.LABEL_TONES), set(look["label_tones"])),
    }
    adrift = [name for name, (got, want) in wired.items() if got != want]
    suite.check("every look setting comes from casts/styles.json", not adrift,
                f"{len(wired)} checked" if not adrift else ", ".join(adrift))

    # And an override has to reach the geometry, not just the dict.
    real_registry = styles_mod.registry
    try:
        styles_mod.registry = lambda: {
            "styles": {"_probe": {"look": {"frame": {"landscape": {
                "subtitle_size": 0.08, "subtitle_y": 0.5}}}}}}
        probe = Layout("landscape", style="_probe")
        plain = Layout("landscape")
        suite.check("a style's look override reaches the frame geometry",
                    probe.subtitle_font_px() > plain.subtitle_font_px()
                    and probe.subtitle_center_y != plain.subtitle_center_y,
                    f"caption {plain.subtitle_font_px()}px@{plain.subtitle_center_y} "
                    f"-> {probe.subtitle_font_px()}px@{probe.subtitle_center_y}")
        suite.check("an override leaves its siblings alone",
                    Layout("landscape", style="_probe").cfg["stage"]
                    == plain.cfg["stage"])
    finally:
        styles_mod.registry = real_registry

    # --- the director may ask for a pose the cast does not have -----------
    # The catalogue is postures, not actions, so a sentence like "he is handed
    # his pay" has nothing that performs it and used to settle for whoever
    # looked closest. A request is accepted only if it names a real character,
    # a well-formed new pose, and one figure.
    with tempfile.TemporaryDirectory() as tmp:
        casts = Path(tmp)
        cast_data = {
            "name": "probe", "style": "test style",
            "background": {"prompt": "a plate"},
            "characters": {
                "alice": {"look": "a woman", "role": "the worker",
                          "relative_height": 1.0, "poses": {"stand": "standing"}},
                "bob": {"look": "a man", "role": "the boss",
                        "relative_height": 1.0, "poses": {"stand": "standing"}}},
            "props": {"desk": "a desk"},
        }
        (casts / "probe.json").write_text(json.dumps(cast_data), encoding="utf-8")
        probe = assets_mod.Cast.load(casts / "probe.json", root=casts)

        def ask(asset, pose_text, shot=1):
            return {"id": shot, "framing": "medium", "elements": [
                {"asset": asset, "new_pose": pose_text,
                 "x": 0.5, "y": 0.97, "h": 0.46}]}

        result = plan_mod.validate(
            {"shots": [ask("alice_take_pay.png",
                           "both hands out taking a pay envelope, beaming"),
                       ask("alice_nope.png", "", 2),
                       ask("carol_wave.png", "waving", 3),
                       ask("alice_Take Pay.png", "taking pay", 4),
                       ask("alice_hand_over.png",
                           "handing an envelope to bob", 5)]},
            ["一。", "二。", "三。", "四。", "五。"], probe)
        added = {r["asset"] for r in result["new_poses"]}
        suite.check("a described action becomes a new pose",
                    added == {"alice_take_pay.png"},
                    f"accepted {sorted(added)}")
        plan_mod.commit_poses(probe, result)
        suite.check("the pose is recorded for later videos",
                    (probe.dir / "learned_poses.json").exists()
                    and "take_pay" in probe.data["characters"]["alice"]["poses"])
        reloaded = assets_mod.Cast.load(casts / "probe.json", root=casts)
        suite.check("and is in the catalogue next time",
                    "alice_take_pay.png" in reloaded.catalogue())

        # A requested pose that never reaches the disk must not take the
        # character out of the shot with it. The first real run of this asked
        # for two poses, hit a quota wall, and shot 2 rendered as an empty
        # plate - worse than the generic casting the request improved on.
        import build as build_mod

        class _FakeProject:
            pass

        stub = _FakeProject()
        stub.out = casts / "out"
        stub.out.mkdir(exist_ok=True)
        for name in ("alice_stand.png", "bob_stand.png", "prop_desk.png"):
            (stub.out / name).write_bytes(b"")
        starved = {"scenes": [{"id": 1, "elements": [
            {"asset": "alice_take_pay.png", "x": 0.3, "y": 0.97, "h": 0.46},
            {"asset": "bob_hand_over.png", "x": 0.7, "y": 0.97, "h": 0.46},
            {"asset": "prop_desk.png", "x": 0.5, "y": 0.97, "h": 0.3},
        ]}]}
        build_mod.log = lambda *a, **k: None
        build_mod.reconcile_sprites(stub, starved, reloaded)
        stood_in = [e["asset"] for e in starved["scenes"][0]["elements"]]
        suite.check("a pose that failed to generate stands in, not vanishes",
                    stood_in == ["alice_stand.png", "bob_stand.png",
                                 "prop_desk.png"],
                    f"{stood_in}")

        # ...and a stand-in must not put one character on screen twice.
        clash = {"scenes": [{"id": 1, "elements": [
            {"asset": "alice_stand.png", "x": 0.3, "y": 0.97, "h": 0.46},
            {"asset": "alice_take_pay.png", "x": 0.7, "y": 0.97, "h": 0.46},
        ]}]}
        build_mod.reconcile_sprites(stub, clash, reloaded)
        suite.check("a stand-in never doubles a character",
                    [e["asset"] for e in clash["scenes"][0]["elements"]]
                    == ["alice_stand.png"])

        # One pose must not carry a whole video. Measured on a real 32-shot
        # build, krabs_stand appeared seven times - 22% of shots the same
        # picture - and nothing noticed.
        lib = assets_mod.Library(reloaded, log=lambda *a, **k: None)
        suite.check("the anchor is the character's first pose",
                    reloaded.anchor_pose("alice") == "stand"
                    and lib._is_anchor("alice_stand.png")
                    and not lib._is_anchor("alice_take_pay.png")
                    and not lib._is_anchor("prop_desk.png"))
        # Anchoring is a generation technique, not part of what a sprite is, so
        # turning it on must not invalidate a library built without it.
        fp = lib._fingerprint(reloaded.catalogue()["alice_stand.png"], "1920x1920")
        suite.check("anchoring does not change the cache key",
                    fp == lib._fingerprint(
                        reloaded.catalogue()["alice_stand.png"], "1920x1920"))

        for n in range(2, 8):
            reloaded.data["characters"]["alice"]["poses"][f"p{n}"] = f"posture {n}"
        crowded = [{"id": i, "narration": "句。", "framing": "medium",
                    "elements": [{"asset": "alice_stand.png", "x": 0.5,
                                  "y": 0.97, "h": 0.46, "rel": 1.0}]}
                   for i in range(1, 17)]
        notes = []
        plan_mod._vary_poses(crowded, reloaded, notes)
        used = [e["asset"] for s in crowded for e in s["elements"]]
        top = max(used.count(a) for a in set(used))
        # The target is one-in-eight, but a cast can simply not have enough
        # poses to reach it: 16 shots over 7 usable poses cannot put fewer than
        # 3 on the most-used one. Hold it to whichever bound is achievable, so
        # the check stays honest if the cast grows or shrinks.
        usable = [p for p in reloaded.data["characters"]["alice"]["poses"]
                  if not reloaded.is_learned(f"alice_{p}.png")]
        floor = max(-(-len(crowded) // len(usable)), 2, -(-len(crowded) // 8))
        suite.check("no pose carries the whole video",
                    top <= floor and len(set(used)) >= 5,
                    f"16 identical shots became {len(set(used))} distinct poses, "
                    f"most-used {top} (best possible {floor})")
        suite.check("variety never reaches for a learned pose",
                    not any(reloaded.is_learned(a) for a in used),
                    "learned poses mean one specific action")

        # Two figures in one sprite, for a beat that is an exchange. A shot
        # cannot hold the pair and one of its members separately - that would
        # put a character on screen twice.
        duo = plan_mod.validate({"shots": [
            {"id": 1, "framing": "medium", "elements": [
                {"asset": "duo_alice_bob_handover.png",
                 "new_interaction": "alice hands bob an envelope, he takes it",
                 "x": 0.5, "y": 0.97, "h": 0.5},
                {"asset": "alice_stand.png", "x": 0.2, "y": 0.97, "h": 0.46}]},
            {"id": 2, "framing": "medium", "elements": [
                {"asset": "duo_alice_alice_x.png",
                 "new_interaction": "alice and alice", "x": 0.5}]},
            {"id": 3, "framing": "medium", "elements": [
                {"asset": "duo_alice_carol_x.png",
                 "new_interaction": "with someone not in the cast", "x": 0.5}]},
        ]}, ["一。", "二。", "三。"], reloaded)
        made = {d["asset"] for d in duo["new_interactions"]}
        shot1 = [e["asset"] for e in duo["scenes"][0]["elements"] if "asset" in e]
        suite.check("an exchange can be drawn as one two-figure sprite",
                    made == {"duo_alice_bob_handover.png"},
                    f"accepted {sorted(made)}")
        suite.check("a two-figure sprite claims both its characters",
                    shot1 == ["duo_alice_bob_handover.png"],
                    f"shot 1 kept {shot1}")
        plan_mod.commit_poses(reloaded, duo)
        again = assets_mod.Cast.load(casts / "probe.json", root=casts)
        suite.check("and it joins the catalogue for later videos",
                    "duo_alice_bob_handover.png" in again.catalogue()
                    and again.duo_members("duo_alice_bob_handover.png")
                    == ["alice", "bob"])

        # Budget: past the cap, requests fall back to the old snapping.
        many = [ask(f"bob_act{n}.png", f"doing thing number {n}", n)
                for n in range(1, plan_mod.NEW_POSE_BUDGET + 4)]
        capped = plan_mod.validate({"shots": many},
                                   ["句。"] * len(many), reloaded)
        suite.check("new poses are capped per video",
                    len(capped["new_poses"]) == plan_mod.NEW_POSE_BUDGET,
                    f"{len(capped['new_poses'])} of {len(many)} requested, "
                    f"cap {plan_mod.NEW_POSE_BUDGET}")

    # --- composition: the frame was 90% empty -----------------------------
    # Measured across seven finished videos: foreground filled 8-10% of the
    # frame, the top 24-37% was dead, and the three features that would fix it
    # were used 4, 6 and 0 times in 87 shots.
    house = assets_mod.Cast.load(ROOT / "casts" / "bikini_bottom.json",
                                 root=ROOT / "casts")
    board = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "medium", "elements": [
            {"asset": "sponge_work.png", "x": 0.3, "h": 0.46},
            {"asset": "prop_whiteboard.png", "x": 0.7, "h": 0.22,
             "anchor": "center", "y": 0.4}]}]},
        ["一句。"], house)
    heights = {e["asset"]: e["h"] for e in board["scenes"][0]["elements"]
               if e.get("asset")}
    suite.check("a board too small to read is raised",
                heights.get("prop_whiteboard.png", 0) >= plan_mod.MIN_BOARD_HEIGHT,
                f"0.22 -> {heights.get('prop_whiteboard.png')}")

    hide = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "medium", "elements": [
            {"asset": "sponge_work.png", "x": 0.5, "h": 0.46},
            {"asset": "prop_kitchen_counter.png", "x": 0.5, "h": 0.44}]}]},
        ["一句。"], house)
    counter = next(e for e in hide["scenes"][0]["elements"]
                   if e.get("asset") == "prop_kitchen_counter.png")
    suite.check("furniture cannot swallow whoever stands behind it",
                counter["h"] <= 0.46 * plan_mod.FURNITURE_SHARE + 1e-6,
                f"0.44 -> {counter['h']} against a 0.46 character")

    thin_wide = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "wide", "elements": [
            {"asset": "sponge_work.png", "x": 0.5, "h": 0.46}]}]},
        ["一句。"], house)
    suite.check("a wide shot with nothing in it is just a small shot",
                thin_wide["scenes"][0]["framing"] != "wide",
                f"wide -> {thin_wide['scenes'][0]['framing']}")

    borrowed = plan_mod.validate(
        {"shots": [
            {"id": 1, "framing": "medium", "elements": [
                {"asset": "sponge_work.png", "x": 0.3, "h": 0.46}]},
            {"id": 2, "framing": "medium", "elements": [
                {"type": "panel", "x": 0.5, "w": 0.8, "ph": 0.6},
                {"asset": "prop_krusty_krab.png", "x": 0.5, "h": 0.5}]}]},
        ["一句。", "二句。"], house)
    order = [e.get("asset") or e.get("type")
             for e in borrowed["scenes"][1]["elements"]]
    suite.check("an element added after sorting still lands in depth order",
                order[0] == "panel" and "sponge_work.png" in order[1:],
                f"{order} - a character inserted at index 0 renders behind "
                f"the translucent wall")

    # --- a parameter accepted and then dropped ----------------------------
    # `panel_color` was threaded from the cast into compose_plate and never
    # passed on to the function that uses it. Nothing failed; panels just kept
    # the old colour, and the same silent-edit mistake had already happened
    # twice. A parameter a function accepts and never mentions again is almost
    # always a half-finished edit.
    import ast
    dropped = []
    for module in ("render.py", "checks.py", "plan.py", "assets.py",
                   "textkit.py", "build.py", "verify.py", "styles.py"):
        tree = ast.parse((Path(__file__).parent / module).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # Dunder signatures are fixed by the language - __exit__ must take
            # exc and tb whether or not it looks at them - so they cannot be
            # evidence of a half-finished edit.
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            declared = {a.arg for a in node.args.args} | {
                a.arg for a in node.args.kwonlyargs}
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            for arg in sorted(declared - used - {"self", "cls"}):
                dropped.append(f"{module}:{node.name}() takes {arg!r} unused")
    suite.check("no parameter is accepted and then ignored", not dropped,
                dropped[0] if dropped else "")

    # --- pure logic --------------------------------------------------------
    beats = plan_mod.split_script("一二三四五六七八九十。" * 6, shot_seconds=5.0)
    joined = "".join(beats)
    suite.check("splitting preserves the script",
                joined == "一二三四五六七八九十。" * 6,
                f"{len(beats)} beats, {len(joined)} chars")

    predicted = timing.clip_seconds("一二三四五六七八九十", 1.0)
    suite.check("duration model is sane", 1.5 < predicted < 3.0,
                f"10 chars -> {predicted:.2f}s")
    fit = timing.fit_to_target("一二三四五六七八九十。" * 20, 9999)
    suite.check("unreachable targets are reported", not fit["ok"])

    lay = Layout("landscape")
    suite.check("caption sits where the reference has it",
                abs(lay.subtitle_center_y / lay.height - 0.903) < 0.001,
                f"y={lay.subtitle_center_y}")
    suite.check("wrapping handles newlines",
                textkit.wrap("上\n下", 60, 900) == ["上", "下"])

    # --- matting -----------------------------------------------------------
    probe = Image.new("RGB", (200, 200), (255, 0, 255))
    ImageDraw.Draw(probe).ellipse([50, 50, 150, 150], fill=(20, 120, 220))
    cut, mode = assets_mod.matting.auto_cutout(probe)
    alpha = np.asarray(cut)[:, :, 3]
    suite.check("chroma matting keeps the subject and drops the key",
                mode == "chroma" and 0.4 < (alpha > 128).mean() < 0.95,
                f"{mode}, {(alpha > 128).mean():.2f} opaque")

    # Removing the key must not take the artwork's own warm colours with it.
    # A spill suppression that ran over every pixel scored perfectly on "no
    # magenta left" and turned SpongeBob's red tie olive, so the contamination
    # measure is useless unless it is paired with this one.
    warm = Image.new("RGB", (200, 200), (255, 0, 255))
    ImageDraw.Draw(warm).ellipse([40, 40, 160, 160], fill=(205, 35, 45))
    kept = np.asarray(assets_mod.matting.auto_cutout(warm)[0])[90:110, 90:110]
    r, g, b = (kept[..., i].mean() for i in range(3))
    suite.check("matting leaves the artwork's own warm colours alone",
                r > 175 and g < 75, f"the red circle stayed {r:.0f},{g:.0f},{b:.0f}")

    # --- labels have to survive the plate they land on --------------------
    # A white outline is the reference look and it is only an outline where
    # the plate is dark. Generated settings are not reliably dark - the first
    # one back was a cream wall - so the choice is measured, and this pins both
    # directions: nothing here should quietly go back to always-white.
    options = [tuple(look["caption"]["fill"]), tuple(look["caption"]["stroke_fill"])]
    swatch = {"type": "label", "text": "标签", "x": 0.3, "y": 0.21, "anchor": "middle"}

    class _NoAssets:
        def sized(self, *a):
            raise AssertionError("a label must not need a sprite")

        def original(self, *a):
            raise AssertionError("a label must not need a sprite")

    def _flat(colour):
        return Image.new("RGB", lay.size, colour)

    def _busy(wall, fixture):
        """A wall with something bolted across it - the case that needs an
        outline at all. The generated kitchen was cream with a steel hood."""
        plate = Image.new("RGB", lay.size, wall)
        ImageDraw.Draw(plate).rectangle(
            [0, int(lay.height * 0.16), lay.width, int(lay.height * 0.26)],
            fill=fixture)
        return plate

    def _pick(plate, tone):
        el = dict(swatch, tone=tone)
        tag = render_mod.build_element_image(el, _NoAssets(), lay)
        return render_mod.outline_against(plate, el, tag, lay, options)

    white, black = options[0], options[1]
    # A flat plate is what every shipped style has, and there the fill carries
    # the label on its own. Whatever the style asked for is left alone - this
    # is the half that keeps the seven styles looking the way they did.
    kept = [_pick(_flat(c), "money") for c in ((250, 247, 224), (18, 52, 96),
                                               (76, 224, 183))]
    suite.check("a flat plate keeps the outline the style asked for",
                all(c == white for c in kept), f"cream, deep water, aqua -> {kept}")

    # A busy one does not: no fill reads against both a cream wall and the
    # steel bolted across it, so here the outline is load-bearing and measured.
    rescued = _pick(_busy((250, 247, 224), (74, 80, 86)), "money")
    suite.check("a label on a busy plate gets an outline that reads",
                rescued == black, f"cream wall + steel hood -> {rescued}")

    # And the measurement weighs the fill as well as the ground. On the ground
    # alone this picks black behind the near-black neutral tone, where the
    # outline disappears into the letters instead of behind them.
    not_lost = _pick(_busy((30, 32, 38), (140, 145, 150)), "neutral")
    suite.check("an outline never vanishes into the letters it outlines",
                not_lost == white, f"dark plate, near-black label -> {not_lost}")

    # The renderer and the draft exporter both draw labels and both used to
    # hardcode white. One measurement carried in the storyboard is what stops
    # them drifting, the way three copies of the placement arithmetic once did.
    suite.check("the draft outlines a label the way the render did",
                "el.get(\"outline\"" in (ROOT / "scripts" / "draft.py").read_text(
                    encoding="utf-8-sig"),
                "draft.py reads the outline the storyboard carries")

    # --- a generated setting is the plate, not a prop ---------------------
    # As a panel it was cover-cropped into a 2.7:1 box, which cut the top and
    # the floor off a 16:9 room and left the blank middle. Nothing should put
    # it back into an element.
    build_src = (ROOT / "scripts" / "build.py").read_text(encoding="utf-8-sig")
    suite.check("a generated setting is used as the background plate",
                '"background": _plate(' in build_src
                and "_give_a_place" not in build_src,
                "setting.png reaches the renderer as the plate")

    # --- the cost line has to be true -------------------------------------
    # USAGE was declared and reset and never incremented by anything, so every
    # run ended with "nothing was generated, everything came from cache" - a
    # build that had just spent thirteen image generations said exactly what a
    # fully cached one said. Faked at the socket so this costs nothing.
    import ark as ark_mod
    import config as config_mod

    class _Response:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    real_open, real_key = urllib.request.urlopen, config_mod.ARK_API_KEY
    real_download = ark_mod._download
    try:
        config_mod.ARK_API_KEY = "offline-selftest"
        ark_mod._download = lambda url, out, retries=3: out
        urllib.request.urlopen = lambda *a, **k: _Response(
            {"choices": [{"message": {"content": "ok"}}],
             "data": [{"url": "http://example.invalid/x.png"}]})
        ark_mod.reset_usage()
        ark_mod.chat([{"role": "user", "content": "hi"}])
        ark_mod.generate_image("a wall", Path(tempfile.gettempdir()) / "x.png")
        ark_mod.chat([{"role": "user", "content": [
            {"type": "text", "text": "what"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]}])
        counted = dict(ark_mod.USAGE)
    finally:
        urllib.request.urlopen, config_mod.ARK_API_KEY = real_open, real_key
        ark_mod._download = real_download
        ark_mod.reset_usage()
    suite.check("a run reports what it actually spent",
                counted["text_calls"] == 1 and counted["images"] == 1
                and counted["vision_calls"] == 1,
                f"{counted['text_calls']} director, {counted['images']} image, "
                f"{counted['vision_calls']} vision")

    # --- re-validating a plan must not quietly lose fields ----------------
    # migrate_plan.py rebuilt the model's answer from an allowlist of three
    # keys, so every field the validator learned to read after it was written
    # was dropped on the floor: each shot's `beat`, which the sound design
    # picks its cues from, and the video's `setting`, which is its backdrop. A
    # migrated video came out somewhere else with duller sound and said
    # nothing. This walks the validator's output back through it.
    import migrate_plan as migrate_mod
    with tempfile.TemporaryDirectory() as tmp:
        seed = plan_mod.validate(
            {"title": "标题", "setting": "a back kitchen",
             "shots": [{"id": 1, "framing": "medium",
                        "beat": {"subject": "alice", "action": "is paid",
                                 "emotion": "delighted"},
                        "elements": [{"asset": "sponge_work.png",
                                      "x": 0.5, "h": 0.46}]}]},
            ["一句。"], house)
        path = Path(tmp) / "plan.json"
        path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        again = migrate_mod.migrate(path, ROOT / "casts" / "bikini_bottom.json")
        kept = (again.get("setting") == seed["setting"]
                and again["scenes"][0].get("beat") == seed["scenes"][0].get("beat")
                and again.get("title") == seed["title"])
        suite.check("re-validating a plan keeps everything the validator reads",
                    kept, f"setting {again.get('setting')!r}, "
                          f"beat {'kept' if again['scenes'][0].get('beat') else 'LOST'}")

    # --- two figures should not be the smallest thing in the video --------
    paired = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "medium", "elements": [
            {"asset": "duo_krabs_sponge_handover.png", "x": 0.5, "h": 0.50}]}]},
        ["一句。"], house)
    duo = paired["scenes"][0]["elements"][0]
    close = look["framing"]["close"]
    suite.check("an interaction is not left smaller than the singles around it",
                duo["h"] >= plan_mod.MIN_DUO_HEIGHT,
                f"0.50 at medium -> {duo['h']} against a solo's "
                f"0.48x{close} = {0.48 * close:.2f}")

    # --- panels reach the frame edges -------------------------------------
    # A director writing "w": 0.30 gets a card floating in the middle of the
    # frame; a wall is supposed to reach the edges. The floor is applied in
    # validate(), so this asks validate() rather than reading the constant.
    walled = plan_mod.validate(
        {"shots": [{"id": 1, "framing": "medium", "elements": [
            {"type": "panel", "x": 0.2, "y": 0.9, "w": 0.30, "ph": 0.20}]}]},
        ["一句。"], house)
    panel = next(el for el in walled["scenes"][0]["elements"]
                 if el.get("type") == "panel")
    suite.check("a panel is widened to sit near the frame edges",
                panel["w"] >= 0.9 and panel["ph"] >= 0.4 and panel["x"] == 0.5,
                f"0.30x0.20 -> {panel['w']:.2f}x{panel['ph']:.2f} at x={panel['x']}")

    # --- a real render, end to end ----------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        synthetic_assets(work)
        storyboard = {
            "video": {"orientation": "landscape", "width": 1920, "height": 1080,
                      "fps": 30, "background": "background.png", "dissolve": 0.4,
                      "crf": 26, "preset": "ultrafast"},
            "title_card": {"text": "自检", "duration": 1.0, "style": "title"},
            "scenes": [
                {"id": 1, "duration": 1.6, "framing": "medium",
                 "subtitle": "第一镜的字幕。",
                 "elements": [{"asset": "a.png", "x": 0.3, "y": 0.97, "h": 0.45},
                              {"asset": "b.png", "x": 0.7, "y": 0.97, "h": 0.45}]},
                {"id": 2, "duration": 1.6, "framing": "close",
                 "subtitle": "第二镜的字幕。",
                 "elements": [{"asset": "prop_c.png", "x": 0.5, "y": 0.97, "h": 0.4},
                              {"type": "label", "text": "标签", "x": 0.5, "y": 0.35}]},
            ],
            "ending_card": {"text": "结束", "duration": 1.0, "highlight": "束"},
        }
        sprites = render_mod.Assets(work)
        findings = checks_mod.inspect(storyboard, sprites, lay, repair=True)
        residual = checks_mod.inspect(storyboard, sprites, lay, repair=False)
        suite.check("layout repair converges", not residual,
                    f"{len(findings)} repaired, {len(residual)} left")

        # A head above the top edge was reported and never repaired: the
        # message went into the log and the video shipped with a decapitated
        # character. Everything else in this pass fixes what it finds.
        tall = {"video": storyboard["video"], "scenes": [{
            "id": 1, "duration": 1.0, "framing": "close", "subtitle": "x",
            "elements": [{"asset": "a.png", "x": 0.5, "y": 0.97, "h": 0.95,
                          "anchor": "bottom", "rel": 1.0}]}]}
        checks_mod.inspect(tall, sprites, lay, repair=True)
        suite.check("a head above the frame is scaled back in, not just reported",
                    not checks_mod.inspect(tall, sprites, lay, repair=False),
                    f"h 0.95 -> {tall['scenes'][0]['elements'][0]['h']}")

        video = work / "selftest.mp4"
        renderer = render_mod.Renderer(storyboard, work)
        total, frames = renderer.render(
            video, [s["duration"] for s in storyboard["scenes"]])
        suite.check("renders frames", video.exists() and frames > 100,
                    f"{frames} frames / {total:.1f}s")
        suite.check("held frames are reused, not recomputed",
                    len(renderer._frame_cache) < frames / 4,
                    f"{len(renderer._frame_cache)} unique of {frames}")

        track = audio_mod.mix(
            audio_mod.build_narration([(None, total)], work / "n.wav"),
            work / "a.wav", total)
        final = work / "final.mp4"
        audio_mod.mux(video, track, final)
        suite.check("muxes audio", final.exists())

        # --- the opening: a spoken title over a stinger --------------------
        # The title card used to hold a silent slot, so the video opened on two
        # and a half seconds of nothing. Three things have to line up now, and
        # each is checked by measuring the audio rather than by trusting the
        # call: the card is long enough for its own line, the voice waits for
        # the stinger, and the stinger is actually there.
        import build as build_mod
        import config  # bound later in main(); needed here first

        lead = audio_mod.TITLE_SFX_LEAD
        suite.check("opening: a long title lengthens its card",
                    build_mod.title_slot(2.6, 1.0, 0.35) == 2.6
                    and abs(build_mod.title_slot(2.6, 3.5, 0.35)
                            - (lead + 3.5 + 0.35)) < 1e-6,
                    f"{build_mod.title_slot(2.6, 3.5, 0.35):.2f}s for a 3.5s title")
        # ...but not without limit. The director brief asks for ten characters
        # or fewer and that is a request; a thirty-character title reads for
        # five seconds and would hold the card that long before shot 1.
        suite.check("opening: a title too long to read loses its voice",
                    not build_mod.title_voice_fits(5.3, 0.35)
                    and build_mod.title_voice_fits(2.1, 0.35)
                    and build_mod.title_slot(2.6, 30.0, 0.35)
                    <= build_mod.MAX_TITLE_SLOT,
                    f"cap {build_mod.MAX_TITLE_SLOT}s")

        spoken = work / "title_voice.wav"
        subprocess.run(
            [config.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
             "-i", "sine=f=440:d=1.2", "-ar", "44100", "-ac", "2",
             str(spoken)], check=True)
        slot = build_mod.title_slot(2.6, 1.2, 0.35)
        opening_narration = audio_mod.build_narration(
            [(spoken, slot, lead), (None, 1.0)], work / "opening.wav")

        def rms_db(path, start, length):
            raw = work / "seg.raw"
            subprocess.run(
                [config.FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}",
                 "-i", str(path), "-t", f"{length:.3f}", "-ac", "1",
                 "-ar", "8000", "-f", "s16le", str(raw)], check=True)
            data = np.fromfile(raw, dtype="<i2").astype(float) / 32768
            if not len(data):
                return -120.0
            return 20 * np.log10(max(float(np.sqrt((data ** 2).mean())), 1e-6))

        quiet = rms_db(opening_narration, 0.0, lead - 0.05)
        voiced = rms_db(opening_narration, lead + 0.1, 0.6)
        suite.check("opening: the title's voice waits for the stinger",
                    quiet < -60 < voiced,
                    f"{quiet:.0f} dB before the lead, {voiced:.0f} dB after")

        import sfx as sfx_mod
        cue_path = sfx_mod.library().get(build_mod.OPENING_SFX)
        suite.check("opening: the stinger is in the cue library",
                    cue_path is not None, build_mod.OPENING_SFX)
        # gen_sfx.py states its own invariant - everything here is generated,
        # so there is no licensing question and the library rebuilds from one
        # command. A downloaded cue was committed here by mistake and broke
        # that; this is the check that would have caught it.
        import gen_sfx
        # Every cue is generated except the opening one, which is supplied.
        # gen_sfx says so in its own docstring, and the exception is the point:
        # a synthesised stand-in for the sound these videos open on is worse
        # than none, so nothing may quietly substitute for it.
        shipped = sorted(set(sfx_mod.library()) - set(gen_sfx.GENERATORS))
        suite.check("opening: the cue is supplied, everything else is generated",
                    shipped == [build_mod.OPENING_SFX],
                    f"shipped: {shipped or 'nothing'}")
        suite.check("opening: nothing can synthesise the cue behind our backs",
                    build_mod.OPENING_SFX not in gen_sfx.GENERATORS)
        # A leftover copy in the other format shadows the shipped one silently:
        # same name, same sound, no way to tell from the build which is
        # playing. This actually happened to the installed skill directory.
        duplicates = sfx_mod.duplicate_cues()
        suite.check("opening: no cue is shadowed by a copy in another format",
                    not duplicates,
                    ", ".join(sorted(duplicates)) or "26 cues, no collisions")
        if cue_path:
            cue = [(0.0, build_mod.OPENING_SFX, cue_path, 0.7)]
            on = audio_mod.mix(opening_narration, work / "cue_on.wav",
                               slot + 1.0, bgm=None, cues=cue)
            off = audio_mod.mix(opening_narration, work / "cue_off.wav",
                                slot + 1.0, bgm=None)
            suite.check("opening: the stinger lands on the first frame",
                        rms_db(off, 0.0, 0.35) < -60 < rms_db(on, 0.0, 0.35),
                        f"{rms_db(off, 0.0, 0.35):.0f} dB without, "
                        f"{rms_db(on, 0.0, 0.35):.0f} dB with")
            # A cue's own gain overrides cue_volume. Without it the stinger is
            # mixed at the library level, which is set for a coin drop under
            # narration rather than for the accent that opens the video.
            loud = rms_db(on, 0.0, 0.35)
            soft = rms_db(audio_mod.mix(
                opening_narration, work / "cue_soft.wav", slot + 1.0,
                bgm=None, cues=[(0.0, "x", cue_path)], cue_volume=0.34),
                0.0, 0.35)
            suite.check("opening: a cue can carry its own gain",
                        loud > soft + 3,
                        f"{loud:.0f} dB at 0.7 against {soft:.0f} dB at 0.34")

        # The draft is written blind - Jianying is not installed on most
        # machines that build one - so the only thing that can be checked is
        # that its own numbers rebuild the frames the renderer drew.
        import check_draft
        import draft as draft_mod
        (work / "storyboard.json").write_text(
            json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")
        draft_dir, _, layers = draft_mod.DraftBuilder(
            work, name="selftest").build(work / "jianying")
        diffs = check_draft.compare(work, draft_dir) or []
        worst = max((d for _, d in diffs), default=99.0)
        suite.check("the Jianying draft rebuilds the same frames",
                    diffs and worst <= check_draft.MAX_MEAN_DIFF,
                    f"{len(diffs)} shots over {layers} layers, "
                    f"worst {worst:.2f}/255")

        # Sound cues are derived from the storyboard, and the two outputs must
        # derive them from the same list. Planned before the cards were
        # attached, every cue landed a title-card early and the ending sting
        # was never placed - silent, plausible, and invisible in every check.
        import sfx as sfx_mod
        durations = [s["duration"] for s in storyboard["scenes"]]
        cues = sfx_mod.plan(storyboard, durations, cast=None)
        title = float(storyboard["title_card"]["duration"])
        suite.check("sound cues fall inside the video, after the title card",
                    cues and all(title <= w <= total for w, _, _ in cues),
                    f"{len(cues)} cues from {min(w for w,_,_ in cues):.1f}s "
                    f"to {max(w for w,_,_ in cues):.1f}s of {total:.1f}s")
        punchline = set(styles_mod.look()["sound"]["cues"].get("punchline") or [])
        ending_at = [w for w, n, _ in cues if n in punchline]
        suite.check("the ending card gets its sting",
                    ending_at and ending_at[-1] > total - 5.0,
                    f"at {ending_at[-1]:.1f}s" if ending_at else "none")

        # Variety is the point, not decoration. A fixed table gave one real
        # video 53% the same effect - seven whooshes of thirteen cues - which
        # is what a viewer hears as "these don't fit" before they notice
        # anything about the sounds themselves.
        long_board = dict(storyboard, scenes=[
            dict(storyboard["scenes"][i % 2], id=i + 1,
                 beat={"emotion": e}, duration=2.0)
            for i, e in enumerate(["happy", "confused", "disappointed", "smug",
                                   "delighted", "worried", "shocked", "glum"])])
        many = sfx_mod.plan(long_board, [2.0] * 8, cast=None)
        names = [n for _, n, _ in many]
        commonest = max(names.count(n) for n in set(names)) if names else 99
        suite.check("cues do not repeat while the palette has anything left",
                    len(set(names)) >= max(4, len(names) * 0.6)
                    and commonest <= max(2, len(names) // 4),
                    f"{len(set(names))} distinct of {len(names)}, "
                    f"most-repeated {commonest}")

        # And the reaction has to come from the beat, not from the props.
        happy = sfx_mod.plan(
            dict(storyboard, scenes=[dict(storyboard["scenes"][0],
                                          beat={"emotion": "delighted"})]),
            [3.0], cast=None)
        glum = sfx_mod.plan(
            dict(storyboard, scenes=[dict(storyboard["scenes"][0],
                                          beat={"emotion": "dismayed"})]),
            [3.0], cast=None)
        suite.check("a delighted beat and a dismayed one sound different",
                    [n for _, n, _ in happy] != [n for _, n, _ in glum],
                    f"{[n for _, n, _ in happy]} vs {[n for _, n, _ in glum]}")

        # The build records the plan in the storyboard so the mix and the draft
        # cannot each derive their own; the draft reads only that.
        storyboard["sound_cues"] = [[round(w, 3), n] for w, n, _ in cues]
        (work / "storyboard.json").write_text(
            json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")

        draft_dir, _, layers = draft_mod.DraftBuilder(
            work, name="selftest").build(work / "jianying")
        content = json.loads((draft_dir / "draft_content.json").read_text(
            encoding="utf-8-sig"))
        by_kind = {}
        for track in content["tracks"]:
            name = str(track.get("name", ""))
            key = ("text" if name.startswith("文字") else
                   "sfx" if name.startswith("音效") else name)
            by_kind[key] = by_kind.get(key, 0) + len(track["segments"])
        # A camera move belongs only to the framings the config names, and it
        # has to be a move: scaling every layer where it stands makes the shot
        # pull apart instead of pushing in, so the offsets are keyframed too.
        motion = styles_mod.look()["motion"]
        moving = {}
        for track in content["tracks"]:
            if track["type"] != "video":
                continue
            for sg in track["segments"]:
                props = {kf.get("property_type")
                         for kf in sg.get("common_keyframes") or []}
                if props:
                    moving.setdefault(sg["target_timerange"]["start"], set())
                    moving[sg["target_timerange"]["start"]] |= props
        want_moving = sum(1 for sc in storyboard["scenes"]
                          if sc.get("framing") in motion["framings"])
        suite.check("only the configured framings get a camera move",
                    len(moving) == want_moving,
                    f"{len(moving)} moving shot(s), "
                    f"{want_moving} {'/'.join(motion['framings'])} in the board")
        suite.check("a move keyframes position as well as scale",
                    all({"KFTypeScaleX", "KFTypePositionX"} <= props
                        for props in moving.values()),
                    "scaling in place is not a camera move")

        wanted_labels = sum(1 for sc in storyboard["scenes"]
                            for el in sc["elements"]
                            if el.get("type") in ("label", "bubble"))
        suite.check("labels export as editable text, not pictures",
                    by_kind.get("text", 0) == wanted_labels,
                    f"{by_kind.get('text', 0)} text segments for "
                    f"{wanted_labels} label(s)")
        # Two labels in one shot both span it, and two cues can land closer
        # together than an effect is long. Jianying allows one segment per
        # track at a time, so both need parallel lanes rather than a drop.
        suite.check("overlapping text and cues get parallel lanes",
                    by_kind.get("sfx", 0) == len(cues),
                    f"{by_kind.get('sfx', 0)} cue segments")

        report = verify_mod.Report()
        duration = verify_mod.check_container(report, final, storyboard)
        verify_mod.check_background(report, final, duration)
        verify_mod.check_layout(report, storyboard, work, lay)
        bad = report.failures()
        suite.check("the verifier passes its own render", not bad,
                    "; ".join(n for _, n, _ in bad))

    # The footage track's checks live in their own module. Two sessions work on
    # this repo at once; a single thousand-line test file is where their work
    # is guaranteed to collide.
    import selftest_footage
    selftest_footage.run(suite, lay)

    print()
    if suite.failures:
        print(f"{suite.failures} check(s) failed - the installation is not healthy")
    else:
        print("all checks passed - the pipeline works offline; "
              "run build.py --check for the API side")
    return 1 if suite.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
