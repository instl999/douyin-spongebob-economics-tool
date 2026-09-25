"""Contact sheet of every shot, for checking composition before rendering.

Cheaper to look at one sheet than to watch a three-minute render and then find
that two characters overlap in shot 9. `build.py --preview` makes one straight
after the plan, before anything is paid for, from `stand_ins`: the drawings
that exist, and a labelled box the right shape for each one that does not.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: F401  UTF-8 stdout; see console.py

from PIL import Image, ImageDraw

import render as render_mod
import textkit
from layout import Layout


# The shape a drawing not made yet is given, width over height. Sprites are
# sized by height, so the aspect is what decides how much of the frame the
# real one will take: one character stands tall, two side by side are wider
# than they are tall, and a prop is anything - square is the least wrong.
STAND_IN_ASPECT = {1: 0.55, 2: 1.15}
STAND_IN_PROP = 1.0
STAND_IN_HEIGHT = 900


def stand_in(spec, height=STAND_IN_HEIGHT):
    """A labelled box standing in for a drawing that has not been made.

    Named for who is in it and filled with what it will show, so a sheet of
    stand-ins still says which drawing is which.
    """
    who = list(spec.get("who") or [])
    aspect = (STAND_IN_ASPECT.get(len(who), STAND_IN_ASPECT[2]) if who
              else STAND_IN_PROP)
    W, H = max(40, int(height * aspect)), int(height)
    image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    edge = max(4, H // 150)
    pen.rounded_rectangle([edge, edge, W - edge - 1, H - edge - 1],
                          radius=int(min(W, H) * 0.08),
                          fill=(250, 250, 246, 190),
                          outline=(64, 64, 64, 235), width=edge)
    name = " + ".join(who) if who else "prop"
    name_px, name_lines = _fit(name, int(H * 0.09), W * 0.84, 2)
    y = int(H * 0.07)
    for line in name_lines:
        pen.text((W // 2, y), line, font=textkit.font(name_px, True),
                 fill=(30, 30, 30, 255), anchor="ma")
        y += textkit.line_height(name_px, True)
    body = (spec.get("shows") or "").strip()
    if body:
        room = H - y - int(H * 0.08)
        size, lines = _fit(body, int(H * 0.055), W * 0.84,
                           max(1, room // max(1, int(H * 0.055 * 1.3))))
        lh = textkit.line_height(size, False)
        top = y + int(H * 0.04)
        for n, line in enumerate(lines):
            if top + (n + 1) * lh > H - int(H * 0.05):
                break
            pen.text((W // 2, top + n * lh), line,
                     font=textkit.font(size, False),
                     fill=(70, 70, 70, 255), anchor="ma")
    return image


def _fit(text, size, width, max_lines):
    """(size, lines) for text wrapped into `width`, shrunk to `max_lines`."""
    lines = textkit.wrap(text, size, width)
    while len(lines) > max_lines and size > 12:
        size = max(12, int(size * 0.9))
        lines = textkit.wrap(text, size, width)
    return size, lines


def _plate_stand_in(size, colour, label):
    """A plain graded ground for a backdrop not generated yet."""
    W, H = size
    top = tuple(min(255, int(c * 1.12) + 12) for c in colour)
    bottom = tuple(int(c * 0.78) for c in colour)
    image = Image.new("RGB", (W, H))
    pen = ImageDraw.Draw(image)
    for y in range(H):
        k = y / max(1, H - 1)
        pen.line([(0, y), (W, y)], fill=tuple(
            int(a + (b - a) * k) for a, b in zip(top, bottom)))
    px = max(16, W // 48)
    pen.text((px, px), label, font=textkit.font(px, False),
             fill=tuple(max(0, c - 70) for c in bottom))
    return image


def _place(source, target):
    """Put `source` at `target` - a hard link where the disk allows one."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def stand_ins(project, plan, folder):
    """Fill `folder` with every picture the plan needs. Returns (made, stood in).

    What has already been drawn is used as it is. Nothing is generated: a
    drawing, the backdrop and the script's own setting that do not exist yet
    get a stand-in each, labelled with what they will be.
    """
    folder = Path(folder)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    made = standing = 0
    for spec in plan.get("drawings") or []:
        source = project.out / spec["asset"]
        if source.exists():
            _place(source, folder / spec["asset"])
            made += 1
        else:
            stand_in(spec).save(folder / spec["asset"], "PNG")
            standing += 1
    lay = project.layout
    colour = tuple(project.cast.panel_color)
    setting = (plan.get("setting") or "").strip()
    grounds = [("background.png", f"{project.get('cast')} backdrop, not drawn yet")]
    if setting and project.get("fallback_setting", True):
        grounds.append(("setting.png", f"setting, not drawn yet: {setting}"))
    for name, label in grounds:
        source = project.out / name
        if source.exists():
            _place(source, folder / name)
        else:
            _plate_stand_in(lay.size, colour, label).save(folder / name, "PNG")
    card = project.out / "title_card.png"
    if card.exists():
        _place(card, folder / card.name)
        plan["_title_card_image"] = card.name
    return made, standing


def contact_sheet(storyboard, workdir, out_path, columns=4, thumb_width=440):
    cfg = storyboard.get("video", {})
    lay = Layout(cfg.get("orientation"), cfg.get("width"), cfg.get("height"))
    renderer = render_mod.Renderer(storyboard, workdir, lay=lay)

    panels = []
    if storyboard.get("title_card"):
        panels.append(("title",
                       render_mod.compose_card(storyboard["title_card"], lay,
                                               renderer.assets)))
    zones = lay.cfg.get("ui_zones") or []
    for scene in storyboard.get("scenes", []):
        frame = render_mod.compose_plate(scene, renderer.background,
                                         renderer.assets, lay,
                                         renderer.panel_color,
                                         chrome=renderer.chrome)
        image = Image.fromarray(frame)
        caption = (scene.get("captions") or [{}])[0].get("text") or scene.get("subtitle", "")
        if caption:
            layer, _ = textkit.render_caption(
                lay.size, caption, size=lay.subtitle_font_px(),
                center_y=lay.subtitle_center_y, max_width=lay.subtitle_max_px)
            if layer is not None:
                image = image.convert("RGBA")
                image.alpha_composite(layer)
                image = image.convert("RGB")
        # Where the phone app draws over the video, roughly: nothing that
        # matters should be under these.
        if zones:
            outline = ImageDraw.Draw(image, "RGBA")
            for x0, y0, x1, y1 in zones:
                outline.rectangle([x0 * lay.width, y0 * lay.height,
                                   x1 * lay.width - 1, y1 * lay.height - 1],
                                  fill=(255, 40, 40, 38),
                                  outline=(255, 40, 40, 220),
                                  width=max(2, lay.width // 240))
        panels.append((f"shot {scene.get('id', '?')}  "
                       f"{scene.get('duration', 0):.1f}s  "
                       f"{scene.get('framing', 'medium')}", image))
    if storyboard.get("ending_card"):
        panels.append(("ending",
                       render_mod.compose_card(storyboard["ending_card"], lay,
                                               renderer.assets)))

    thumb_height = int(thumb_width * lay.height / lay.width)
    label_height = 26
    rows = (len(panels) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb_width,
                              rows * (thumb_height + label_height)), (24, 24, 26))
    draw = ImageDraw.Draw(sheet)
    font = textkit.font(16, bold=False)

    for index, (label, panel) in enumerate(panels):
        row, column = divmod(index, columns)
        x = column * thumb_width
        y = row * (thumb_height + label_height)
        if not isinstance(panel, Image.Image):
            panel = Image.fromarray(panel)
        sheet.paste(panel.convert("RGB").resize((thumb_width, thumb_height),
                                                Image.LANCZOS), (x, y))
        draw.text((x + 8, y + thumb_height + 4), label, fill=(230, 230, 120), font=font)

    out_path = Path(out_path)
    sheet.save(out_path, "JPEG", quality=88)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard")
    ap.add_argument("workdir")
    ap.add_argument("out", nargs="?", default="preview.jpg")
    ap.add_argument("--columns", type=int, default=4)
    args = ap.parse_args()
    sb = json.loads(Path(args.storyboard).read_text(encoding="utf-8-sig"))
    print(contact_sheet(sb, args.workdir, args.out, columns=args.columns))


if __name__ == "__main__":
    main()
