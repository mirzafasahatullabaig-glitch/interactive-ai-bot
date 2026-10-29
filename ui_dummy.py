"""Dummy kiosk for touch/display testing -- NO bot, models, camera or .env.

A diagnostic, not a demo. It answers two questions:
  1. Does the touch land WHERE you press?  -> a dot is drawn at the exact tap
     point, and the raw x,y is shown. If the dot is under your finger, the
     position (and calibration) is right.
  2. Which button did it hit?  -> only the button you actually touched lights
     up and its name is shown. If you press TALK and "Cam" lights, the axes
     need swap/invert in the ads7846 overlay.

Run under cage exactly like the real UI:
    sudo chvt 3
    cage -- .venv/bin/python ui_dummy.py        # on the Pi
    UI_WINDOWED=1 python ui_dummy.py             # windowed, on a PC
Press Esc to quit.
"""
import os

import pygame

from touchcal import Calib

W, H = 800, 480
BG = (14, 16, 22)
PANEL = (22, 26, 36)
ACCENT = (90, 170, 255)
HIT = (255, 90, 120)       # the button you actually touched
DOT = (90, 220, 140)       # marker at the raw touch point
TXT = (230, 234, 240)
TXT_DIM = (140, 150, 165)

BUTTONS = [
    ("TALK", pygame.Rect(20, 400, 360, 64)),
    ("Cam", pygame.Rect(400, 400, 180, 64)),
    ("Type", pygame.Rect(600, 400, 180, 64)),
]


def _open_display():
    if os.environ.get("UI_WINDOWED"):
        pygame.display.init()
        return pygame.display.set_mode((W, H))
    for drv in ["kmsdrm", "x11", "wayland"]:
        try:
            os.environ["SDL_VIDEODRIVER"] = drv
            pygame.display.quit()
            pygame.display.init()
            return pygame.display.set_mode((W, H), pygame.FULLSCREEN)
        except pygame.error:
            pass
    raise SystemExit("No usable SDL video driver (try UI_WINDOWED=1 on a PC).")


def main():
    pygame.init()
    pygame.mouse.set_visible(False)
    screen = _open_display()
    font = pygame.font.SysFont("dejavusans", 20)
    font_b = pygame.font.SysFont("dejavusans", 26, bold=True)

    last_pos = None       # (x, y) of the most recent tap (after calibration)
    last_hit = None       # label of the button hit, or None
    taps = 0

    # Calibration: tap the 4 on-screen targets to correct residual distortion.
    # pygame already gives screen pixels; this fixes a linear offset/scale/flip
    # left over from the overlay. Press 'c' to start, Esc to quit.
    TARGETS = [(40, 40), (W - 40, 40), (40, H - 40), (W - 40, H - 40)]
    calibrating = False
    cal_idx = 0
    cal_raw = []          # where pygame reported each target tap
    calib = None          # Calib once solved

    clock = pygame.time.Clock()
    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                running = False
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_c:
                calibrating, cal_idx, cal_raw, calib = True, 0, [], None
            elif e.type == pygame.MOUSEBUTTONDOWN:   # a tap (touch or mouse)
                if calibrating:
                    cal_raw.append(e.pos)            # reported tap for target
                    cal_idx += 1
                    if cal_idx >= 4:                 # all 4 corners collected
                        tl, tr, bl, br = cal_raw
                        calib = Calib.from_taps(tl, tr, bl, br, W, H)
                        calibrating = False
                    continue
                pos = calib.apply(*e.pos) if calib else e.pos
                last_pos = pos
                last_hit = next((lbl for lbl, r in BUTTONS
                                 if r.collidepoint(pos)), None)
                taps += 1

        screen.fill(BG)

        if calibrating:   # show just the current target to tap
            tx, ty = TARGETS[cal_idx]
            pygame.draw.circle(screen, HIT, (tx, ty), 16, 3)
            pygame.draw.line(screen, HIT, (tx - 22, ty), (tx + 22, ty), 2)
            pygame.draw.line(screen, HIT, (tx, ty - 22), (tx, ty + 22), 2)
            msg = f"CALIBRATE: tap the ring  ({cal_idx + 1}/4)"
            screen.blit(font.render(msg, True, TXT), (W // 2 - 140, H // 2))
            pygame.display.flip()
            clock.tick(20)
            continue

        pygame.draw.rect(screen, PANEL, pygame.Rect(20, 20, 760, 344),
                         border_radius=14)
        screen.blit(font.render("TOUCH TEST - a dot marks where you touched",
                                True, TXT), (40, 60))
        cal_state = "ON" if calib else "OFF (press 'c' to calibrate)"
        screen.blit(font.render(f"calibration: {cal_state}", True, TXT_DIM),
                    (40, 190))
        pos_txt = f"last tap: {last_pos}" if last_pos else "last tap: (none yet)"
        screen.blit(font.render(pos_txt, True, TXT_DIM), (40, 100))
        screen.blit(font.render(f"hit button: {last_hit or '-'}", True, TXT_DIM),
                    (40, 130))
        screen.blit(font.render(f"taps: {taps}", True, TXT_DIM), (40, 160))

        # buttons -- only the one you touched is highlighted
        for lbl, r in BUTTONS:
            colour = HIT if lbl == last_hit else ACCENT
            pygame.draw.rect(screen, PANEL, r, border_radius=12)
            pygame.draw.rect(screen, colour, r, 3, border_radius=12)
            t = font_b.render(lbl, True, colour)
            screen.blit(t, t.get_rect(center=r.center))

        # dot at the exact touch point -- should sit under your finger
        if last_pos:
            pygame.draw.circle(screen, DOT, last_pos, 10)
            pygame.draw.circle(screen, BG, last_pos, 4)

        pygame.display.flip()
        clock.tick(20)
    pygame.quit()


if __name__ == "__main__":
    main()
