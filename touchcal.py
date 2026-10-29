"""Linear touch calibration: map raw ADS7846 coords -> screen pixels.

NOTE: on this project the ads7846 kernel overlay (xmin/xmax/ymin/ymax, swapxy)
already does this mapping, and cage normalises to pixels -- so in the REAL
kiosk you should not need this at all. It lives here for the dummy test tool,
where calibrating in-app (no reboot) is handy. Don't stack this on top of a
correct overlay, or you double-correct.

A Calib is four numbers per axis plus two flags. Build one two ways:
  - Calib.from_range(...) if you already know the raw min/max, or
  - Calib.from_taps(corners, W, H) to solve it from four corner taps.
"""
from dataclasses import dataclass


@dataclass
class Calib:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    w: int               # screen width  (px)
    h: int               # screen height (px)
    swap_xy: bool = False
    invert_x: bool = False
    invert_y: bool = False

    def apply(self, x_raw, y_raw):
        """Raw touch -> (x_screen, y_screen) pixel ints, clamped to screen."""
        if self.swap_xy:
            x_raw, y_raw = y_raw, x_raw
        x = _map(x_raw, self.x_min, self.x_max, self.w, self.invert_x)
        y = _map(y_raw, self.y_min, self.y_max, self.h, self.invert_y)
        return x, y

    @classmethod
    def from_range(cls, x_min, x_max, y_min, y_max, w, h, **flags):
        return cls(x_min, x_max, y_min, y_max, w, h, **flags)

    @classmethod
    def from_taps(cls, tl, tr, bl, br, w, h):
        """Solve calibration from four corner taps, each a raw (x, y).
        tl=top-left, tr=top-right, bl=bottom-left, br=bottom-right, as seen
        on the mounted screen. Detects swap/invert automatically."""
        xs = [p[0] for p in (tl, tr, bl, br)]
        ys = [p[1] for p in (tl, tr, bl, br)]
        # Axis swap: if left->right moves the RAW y-axis more than the raw
        # x-axis, the panel's axes are swapped relative to the screen.
        dx_lr = abs(tr[0] - tl[0]) + abs(br[0] - bl[0])   # raw-x change L->R
        dy_lr = abs(tr[1] - tl[1]) + abs(br[1] - bl[1])   # raw-y change L->R
        swap = dy_lr > dx_lr
        a = [(_sw(p, swap)) for p in (tl, tr, bl, br)]    # post-swap coords
        axl = min(a[0][0], a[2][0]); axr = max(a[1][0], a[3][0])
        ayt = min(a[0][1], a[1][1]); ayb = max(a[2][1], a[3][1])
        # invert if left edge reads a HIGHER raw value than the right edge
        inv_x = (a[0][0] + a[2][0]) > (a[1][0] + a[3][0])
        inv_y = (a[0][1] + a[1][1]) > (a[2][1] + a[3][1])
        return cls(axl, axr, ayt, ayb, w, h, swap, inv_x, inv_y)


def _sw(p, swap):
    return (p[1], p[0]) if swap else (p[0], p[1])


def _map(v, lo, hi, size, invert):
    if hi == lo:
        return 0
    t = (v - lo) / (hi - lo)          # 0..1
    if invert:
        t = 1.0 - t
    return max(0, min(size - 1, int(t * size)))


# ---- self-check (run: python touchcal.py) --------------------------------
if __name__ == "__main__":
    W, H = 800, 480
    # normal panel, no swap/invert: raw 200..3700 x, 150..3900 y
    c = Calib.from_range(200, 3700, 150, 3900, W, H)
    assert c.apply(200, 150) == (0, 0)
    assert c.apply(3700, 3900) == (W - 1, H - 1)
    assert abs(c.apply(1950, 2025)[0] - W // 2) <= 1    # midpoint ~ centre
    # clamp beyond range
    assert c.apply(0, 0) == (0, 0)
    assert c.apply(9999, 9999) == (W - 1, H - 1)
    # inverted X: left edge reads high
    ci = Calib.from_range(200, 3700, 150, 3900, W, H, invert_x=True)
    assert ci.apply(200, 150) == (W - 1, 0)
    # solve from four clean corners (no swap, no invert)
    s = Calib.from_taps((200, 150), (3700, 150), (200, 3900), (3700, 3900), W, H)
    assert not s.swap_xy and not s.invert_x and not s.invert_y
    assert s.apply(200, 150) == (0, 0)
    # solve from SWAPPED panel: moving L->R changes raw-y, not raw-x
    sw = Calib.from_taps((150, 200), (150, 3700), (3900, 200), (3900, 3700), W, H)
    assert sw.swap_xy
    assert sw.apply(150, 200) == (0, 0)
    print("touchcal self-check OK")
