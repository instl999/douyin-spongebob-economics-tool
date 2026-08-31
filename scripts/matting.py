"""Turn a generated image into a clean transparent sprite.

Sprites are generated on a saturated chroma background rather than white, and
that choice does most of the work here. With a white background an enclosed
region - the gap between a raised arm and the body - is indistinguishable from
a white the character actually has, like an eye or a glove, so the only safe
rule is "remove white the border can reach", which leaves those gaps filled in.
A saturated key is unambiguous: the gap is key-coloured, the eye is not, so
enclosed regions come out correctly without any connectivity guesswork.

The white path is kept for images that came back on white anyway, where the
border-connected rule is the best available.

Fractional edge pixels are colour-decontaminated in both paths. A
half-transparent pixel generated over the key still *holds* the key colour;
composited over the plate it reads as a fringe. Solving
observed = a*F + (1-a)*key for F removes it.

That equation assumes the edge is a blend of subject and key, and a JPEG edge
is not - compression ringing puts colours there that no alpha explains, and
they survive. Measured over 85 sprites, edge pixels still carried 6.7% key
colour on average and 16% at worst: the visible purple rim. Three passes take
that to 0.6% - choke the matte inward a pixel, give partial pixels the colour
of the nearest solid one, then remove the key tint that remains in the rim.
The third is narrower than it sounds and _suppress_spill explains why.

Sprites are cropped to their own bounding box so that a sprite's height means
the character's height, and scale numbers stay comparable between assets.
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

# Distance-from-white below LO is certainly background, above HI certainly not.
LO = 8.0
HI = 44.0
# How far to pull the matte inward, as a fraction of the alpha ramp.
CHOKE = 0.22
# How far in from the cut edge spill can still be. Wide enough for a JPEG halo,
# narrow enough that no character is a few pixels thick.
RIM_PIXELS = 6.0


def cutout(img, lo=LO, hi=HI, pad=8, decontaminate=True, autocrop=True):
    """RGB(A) PIL image -> RGBA PIL image with the outer white removed."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)

    # Distance from white: dark OR saturated pixels both score high.
    dist = 255.0 - rgb.min(axis=2)

    # Everything that could be background, then keep only what the border reaches.
    candidate = dist < hi
    labels, count = ndimage.label(candidate)
    outside = np.zeros_like(candidate)
    if count:
        border = np.concatenate([
            labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])
        touching = np.unique(border[border > 0])
        if touching.size:
            outside = np.isin(labels, touching)

    # Soft ramp only where the border actually reaches.
    bg_strength = np.clip((hi - dist) / max(hi - lo, 1e-6), 0.0, 1.0)
    alpha = np.ones(dist.shape, dtype=np.float32)
    alpha[outside] = 1.0 - bg_strength[outside]
    alpha = np.clip(alpha, 0.0, 1.0)

    if decontaminate:
        # observed = a*F + (1-a)*white  ->  F = (observed - (1-a)*255) / a
        a = alpha[..., None]
        safe = np.maximum(a, 1e-3)
        rgb = np.where(a > 0.004, (rgb - (1.0 - a) * 255.0) / safe, rgb)
        rgb = np.clip(rgb, 0.0, 255.0)

    out = np.dstack([rgb, alpha * 255.0]).astype(np.uint8)
    result = Image.fromarray(out, mode="RGBA")
    if autocrop:
        result = crop_to_content(result, pad=pad)
    return result


def crop_to_content(img, pad=8, threshold=8):
    a = np.asarray(img)[:, :, 3]
    ys, xs = np.where(a > threshold)
    if not len(ys):
        return img
    y0, y1 = max(0, ys.min() - pad), min(img.height, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(img.width, xs.max() + 1 + pad)
    return img.crop((x0, y0, x1, y1))


def background_is_white(img, tolerance=60):
    """Fraction of border pixels that are near-white - a sanity check.

    A generated image that came back with a scene background instead of a plain
    one scores low here, and matting it would punch a hole in the subject.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    border = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    return float((255.0 - border.min(axis=1) < tolerance).mean())


def sample_key(img, trim=2):
    """Median colour of the border ring - the background the model produced."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    ring = np.concatenate([rgb[trim, :], rgb[-1 - trim, :],
                           rgb[:, trim], rgb[:, -1 - trim]])
    return np.median(ring, axis=0)


def cutout_chroma(img, key, lo=40.0, hi=130.0, pad=8, decontaminate=True,
                  autocrop=True, choke=CHOKE):
    """Remove everything close to `key` in colour, wherever it appears."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    key = np.asarray(key, dtype=np.float32)

    # Weight chroma over luma: a dark green outline must survive a green key.
    diff = rgb - key
    luma = diff.mean(axis=2, keepdims=True)
    chroma = diff - luma
    dist = np.sqrt((chroma ** 2).sum(axis=2) * 2.0 + (luma[..., 0] ** 2) * 0.35)

    alpha = np.clip((dist - lo) / max(hi - lo, 1e-6), 0.0, 1.0)

    # Deliberately no hole filling here. An enclosed key-coloured region - the
    # gap between a raised arm and the body - is exactly what this path exists
    # to remove, and filling holes would put every one of them back.

    # Choke the matte inward first. Unmixing assumes the edge really is
    # a*F + (1-a)*key, and a JPEG edge is not - ringing puts colours there that
    # no alpha explains, so they survive. Measured over 60 sprites, edge pixels
    # still carried 5.7% key colour on average and 43% at worst. Pulling the
    # boundary in by about a pixel discards that ring, and cartoon art has a
    # thick black outline just inside it, so nothing that reads is lost.
    if choke > 0:
        alpha = np.clip((alpha - choke) / max(1.0 - choke, 1e-6), 0.0, 1.0)

    if decontaminate:
        # 1. Give partial pixels the colour of the nearest solid one. Unmixing
        #    amplifies noise as alpha falls - dividing by 0.05 makes a JPEG
        #    artefact twenty times worse - and the interior colour a pixel away
        #    is what the edge should have been anyway.
        solid = alpha >= 0.90
        if solid.any() and (~solid).any():
            _, (iy, ix) = ndimage.distance_transform_edt(~solid, return_indices=True)
            borrowed = rgb[iy, ix]
            blend = np.clip((0.90 - alpha) / 0.90, 0.0, 1.0)[..., None]
            rgb = rgb * (1.0 - blend) + borrowed * blend

        # 2. Unmix what remains, which is now genuine partial cover.
        a = alpha[..., None]
        rgb = np.where(a > 0.004,
                       (rgb - (1.0 - a) * key[None, None, :]) / np.maximum(a, 1e-3),
                       rgb)
        rgb = np.clip(rgb, 0.0, 255.0)

        # 3. Suppress whatever key hue still shows in the rim.
        rgb = _suppress_spill(rgb, key, alpha)

    out = np.dstack([rgb, alpha * 255.0]).astype(np.uint8)
    result = Image.fromarray(out, mode="RGBA")
    if autocrop:
        result = crop_to_content(result, pad=pad)
    return result


def _suppress_spill(rgb, key, alpha, ring=RIM_PIXELS):
    """Take the key's tint out of the rim without touching the artwork.

    Two earlier attempts failed in opposite directions, and the shape of this
    function is the record of both.

    Suppressing everywhere zeroed the measurement and wrecked the pictures:
    "red and blue above green" describes SpongeBob's tie and Sandy's flower
    exactly as well as it describes magenta spill, so both went grey.

    Suppressing only where alpha is partial then left the fringe visibly
    purple, because the worst of it is *opaque* - JPEG ringing paints a solid
    halo just outside the black outline, and no alpha value marks it.

    What separates spill from paint is neither colour nor alpha but position
    and context: spill is within a few pixels of the cut edge, and it is more
    key-tinted than the artwork immediately inside it. So the tint is measured
    against the nearest pixel deep enough inside to be certainly paint, and
    only the excess over that is removed, fading out `ring` pixels in. Beside a
    black outline the excess is the whole halo; inside Patrick, whose own pink
    reads as spill by colour alone, the reference is just as pink and the
    excess is nothing.
    """
    key = np.asarray(key, dtype=np.float32)
    if float(key.max() - key.min()) < 40.0:      # a grey key tints nothing
        return rgb
    weak = int(np.argmin(key))
    strong = [c for c in range(3)
              if c != weak and key[c] >= key.max() * 0.5]

    tint = np.minimum.reduce([rgb[:, :, c] for c in strong]) - rgb[:, :, weak]

    depth = ndimage.distance_transform_edt(alpha >= 0.90)
    inside = depth >= ring
    if not inside.any():                          # too thin to have an inside
        return rgb
    _, (iy, ix) = ndimage.distance_transform_edt(~inside, return_indices=True)
    excess = np.maximum(tint - tint[iy, ix], 0.0)
    excess *= np.clip((ring - depth) / ring, 0.0, 1.0)

    out = rgb.copy()
    for channel in strong:
        out[:, :, channel] -= excess
    return np.clip(out, 0.0, 255.0)



def auto_cutout(img, pad=8):
    """Pick the right path from what the background actually is."""
    key = sample_key(img)
    if (255.0 - key.min()) < 40.0:          # near-white background
        return cutout(img, pad=pad), "white"
    return cutout_chroma(img, key, pad=pad), "chroma"


def process_file(src, dst, pad=8):
    img = Image.open(src)
    result, mode = auto_cutout(img, pad=pad)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    result.save(dst, "PNG")
    coverage = float((np.asarray(result)[:, :, 3] > 8).mean())
    return {"path": Path(dst), "size": result.size, "mode": mode,
            "coverage": coverage}


def main():
    ap = argparse.ArgumentParser(description="white-background cutout")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--lo", type=float, default=LO)
    ap.add_argument("--hi", type=float, default=HI)
    ap.add_argument("--pad", type=int, default=8)
    ap.add_argument("--no-crop", action="store_true")
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    pairs = ([(f, dst / (f.stem + ".png")) for f in sorted(src.iterdir())
              if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
             if src.is_dir() else [(src, dst)])
    for s, d in pairs:
        info = process_file(s, d, pad=args.pad)
        flag = "" if 0.02 < info["coverage"] < 0.95 else "  <-- check this one"
        print(f"  {s.name} -> {d.name}  {info['size'][0]}x{info['size'][1]}"
              f"  key={info['mode']} coverage={info['coverage']:.2f}{flag}")


if __name__ == "__main__":
    main()
