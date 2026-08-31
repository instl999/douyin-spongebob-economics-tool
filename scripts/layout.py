"""Frame geometry, shared by the renderer and the preview.

A storyboard is written once and rendered either landscape or portrait, so all
positions in it are relative, never pixels.

Positions are relative to a *stage* rather than to the frame, and the stage's
bottom edge is the line characters stand on - not the bottom of the picture.
That is what "y = 0.97" should mean: feet on the ground, clear of the caption.
Measured on the references, feet land around 80% of frame height while the
caption sits at 90%; a stage that ended at the frame edge would put every
character on top of its own subtitle.

Sizing is relative to the stage too, which is what keeps a character the same
visual size in both orientations - sizing to the frame would make every sprite
balloon when the frame got taller.
"""

import styles as styles_mod

# The canvas an orientation means. This is what the word denotes, not a
# preference, so it stays here; everything that *is* a preference - stage,
# caption size and position, label size - lives in casts/styles.json under
# look.frame, and can be overridden per style.
CANVAS = {"landscape": (1920, 1080), "portrait": (1080, 1920)}


class Layout:
    """Turns relative storyboard coordinates into pixels for one frame size."""

    def __init__(self, orientation="landscape", width=None, height=None,
                 overrides=None, style=None):
        if orientation not in CANVAS:
            raise ValueError(f"orientation must be one of {sorted(CANVAS)}")
        cfg = styles_mod.frame(orientation, style)
        cfg.update(overrides or {})
        default_w, default_h = CANVAS[orientation]
        self.orientation = orientation
        self.width = int(width or cfg.get("width") or default_w)
        self.height = int(height or cfg.get("height") or default_h)
        self.cfg = cfg

        x0, y0, x1, y1 = cfg["stage"]
        self.stage_x = x0 * self.width
        self.stage_y = y0 * self.height
        self.stage_w = (x1 - x0) * self.width
        self.stage_h = (y1 - y0) * self.height

    # --- stage-relative -> pixels -----------------------------------------
    def point(self, x, y):
        return (int(round(self.stage_x + x * self.stage_w)),
                int(round(self.stage_y + y * self.stage_h)))

    def sprite_height(self, h):
        """h is a fraction of stage height."""
        return max(1, int(round(h * self.stage_h)))

    # --- frame-relative ----------------------------------------------------
    def subtitle_font_px(self, scale=1.0):
        return max(12, int(round(self.cfg["subtitle_size"] * self.width * scale)))

    def label_font_px(self, scale=1.0):
        return max(10, int(round(self.cfg["label_size"] * self.width * scale)))

    @property
    def subtitle_center_y(self):
        return int(round(self.cfg["subtitle_y"] * self.height))

    @property
    def subtitle_max_px(self):
        return int(self.cfg["subtitle_max_width"] * self.width)

    @property
    def image_size(self):
        return self.cfg["image_size"]

    @property
    def size(self):
        return (self.width, self.height)

    def describe(self):
        return (f"{self.orientation} {self.width}x{self.height}  "
                f"stage {int(self.stage_w)}x{int(self.stage_h)} at "
                f"({int(self.stage_x)},{int(self.stage_y)})")


def from_video(video):
    """The Layout a storyboard's `video` block describes.

    The renderer, the draft exporter and the draft checker each used to build
    their own Layout from the same three fields. They agreed only because the
    geometry was a constant; once it became configurable, one shared reading of
    the storyboard is what keeps the exported draft matching the render.
    """
    orientation = video.get("orientation", "landscape")
    look = styles_mod.look(carried=video.get("look"))
    frame = (look.get("frame") or {}).get(orientation) or {}
    return Layout(orientation, video.get("width"), video.get("height"),
                  overrides=frame)
