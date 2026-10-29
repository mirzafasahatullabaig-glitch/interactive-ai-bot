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

    last_pos = None       # (x, y) of the most recent tap
    last_hit = None       # label of the button hit, or None
    taps = 0

    clock = pygame.time.Clock()
    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                running = False
            elif e.type == pygame.MOUSEBUTTONDOWN:   # a tap (touch or mouse)
                last_pos = e.pos
                last_hit = next((lbl for lbl, r in BUTTONS
                                 if r.collidepoint(e.pos)), None)
                taps += 1

        screen.fill(BG)
        pygame.draw.rect(screen, PANEL, pygame.Rect(20, 20, 760, 344),
                         border_radius=14)
        screen.blit(font.render("TOUCH TEST - a dot marks where you touched",
                                True, TXT), (40, 60))
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
