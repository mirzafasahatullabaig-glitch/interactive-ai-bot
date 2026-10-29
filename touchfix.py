"""Orientation fix for touch taps, applied in-app on screen-pixel coords.

pygame already gives tap coords in screen pixels (0..W-1, 0..H-1). If the panel
is mounted/wired rotated or mirrored vs. the display, remap here instead of
rebooting to change the ads7846 overlay. Eight orientations cover every
rotate/flip combination; pick one with TOUCH_ORIENT (0..7) or cycle live.

    0 none      1 flipX     2 flipY     3 rot180 (flipX+flipY)
    4 swap      5 swap+flipX   6 swap+flipY   7 rot90/rot270 combos

Most real cases are 0, 1, 2, 3 (no swap) or 4..7 (swap). Cycle in the dummy
with the 'n' key until a tap lands under your finger, read the number off the
screen, then set TOUCH_ORIENT=<n> for the real kiosk.
"""
import os

ORIENTS = 8


def transform(pos, w, h, orient):
    # work in 0..1 so swap is exact at the edges regardless of aspect ratio
    nx = pos[0] / (w - 1)
    ny = pos[1] / (h - 1)
    if orient & 4:            # swap axes
        nx, ny = ny, nx
    if orient & 1:            # flip X
        nx = 1.0 - nx
    if orient & 2:            # flip Y
        ny = 1.0 - ny
    x = max(0, min(w - 1, round(nx * (w - 1))))
    y = max(0, min(h - 1, round(ny * (h - 1))))
    return x, y


def default_orient():
    try:
        return int(os.environ.get("TOUCH_ORIENT", "0")) % ORIENTS
    except ValueError:
        return 0


if __name__ == "__main__":
    W, H = 800, 480
    # orient 0 is identity
    assert transform((10, 20), W, H, 0) == (10, 20)
    # flipX maps left edge to right edge
    assert transform((0, 0), W, H, 1) == (W - 1, 0)
    # flipY maps top to bottom
    assert transform((0, 0), W, H, 2) == (0, H - 1)
    # rot180 maps top-left to bottom-right
    assert transform((0, 0), W, H, 3) == (W - 1, H - 1)
    # swap: max-y input becomes max-x output, min-x input becomes min-y output
    assert transform((0, 0), W, H, 4) == (0, 0)
    assert transform((0, H - 1), W, H, 4) == (W - 1, 0)   # y max -> x max
    assert transform((W - 1, 0), W, H, 4) == (0, H - 1)   # x max -> y max
    print("touchfix self-check OK")
