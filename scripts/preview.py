"""Contact sheet of every shot, for checking composition before rendering.

Cheaper to look at one sheet than to watch a three-minute render and then find
that two characters overlap in shot 9.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw

import render as render_mod
import textkit
from layout import Layout


def contact_sheet(storyboard, workdir, out_path, columns=4, thumb_width=440):
    cfg = storyboard.get("video", {})
    lay = Layout(cfg.get("orientation"), cfg.get("width"), cfg.get("height"))
    renderer = render_mod.Renderer(storyboard, workdir, lay=lay)

    panels = []
    if storyboard.get("title_card"):
        panels.append(("title",
                       render_mod.compose_card(storyboard["title_card"], lay,
                                               renderer.assets)))
    for scene in storyboard.get("scenes", []):
        frame = render_mod.compose_plate(scene, renderer.background,
                                         renderer.assets, lay,
                                         renderer.panel_color)
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
