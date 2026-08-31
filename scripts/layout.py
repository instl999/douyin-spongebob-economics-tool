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

ORIENTATIONS = {
    "landscape": {
        "width": 1920, "height": 1080,
        "stage": (0.0, 0.0, 1.0, 0.86),   # bottom edge = the ground line
        "subtitle_size": 0.0323,        # of frame width  -> 62px at 1920
        "subtitle_y": 0.903,            # centre line, of frame height
        "subtitle_max_width": 0.86,
        "label_size": 0.0344,           # -> 66px, reads larger than the caption
        "image_size": "2560x1440",
    },
    "portrait": {
        "width": 1080, "height": 1920,
        "stage": (0.0, 0.14, 1.0, 0.80),  # bottom edge = the ground line
        "subtitle_size": 0.050,         # -> 54px at 1080, readable on a phone
        "subtitle_y": 0.845,
        "subtitle_max_width": 0.90,
        "label_size": 0.052,
        "image_size": "1440x2560",
    },
}


class Layout:
    """Turns relative storyboard coordinates into pixels for one frame size."""

    def __init__(self, orientation="landscape", width=None, height=None,
                 overrides=None):
        if orientation not in ORIENTATIONS:
            raise ValueError(f"orientation must be one of {sorted(ORIENTATIONS)}")
        cfg = dict(ORIENTATIONS[orientation])
        cfg.update(overrides or {})
        self.orientation = orientation
        self.width = int(width or cfg["width"])
        self.height = int(height or cfg["height"])
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
