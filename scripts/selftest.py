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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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

        # Budget: past the cap, requests fall back to the old snapping.
        many = [ask(f"bob_act{n}.png", f"doing thing number {n}", n)
                for n in range(1, plan_mod.NEW_POSE_BUDGET + 4)]
        capped = plan_mod.validate({"shots": many},
                                   ["句。"] * len(many), reloaded)
        suite.check("new poses are capped per video",
                    len(capped["new_poses"]) == plan_mod.NEW_POSE_BUDGET,
                    f"{len(capped['new_poses'])} of {len(many)} requested, "
                    f"cap {plan_mod.NEW_POSE_BUDGET}")

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

        report = verify_mod.Report()
        duration = verify_mod.check_container(report, final, storyboard)
        verify_mod.check_background(report, final, duration)
        verify_mod.check_layout(report, storyboard, work, lay)
        bad = report.failures()
        suite.check("the verifier passes its own render", not bad,
                    "; ".join(n for _, n, _ in bad))

    print()
    if suite.failures:
        print(f"{suite.failures} check(s) failed - the installation is not healthy")
    else:
        print("all checks passed - the pipeline works offline; "
              "run build.py --check for the API side")
    return 1 if suite.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
