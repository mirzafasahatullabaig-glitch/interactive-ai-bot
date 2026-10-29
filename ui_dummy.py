"""Dummy kiosk for touch/display testing -- NO bot, models, camera or .env.

Same 800x480 layout and buttons as ui_kiosk.py, but every tap just cycles the
theme colour so you can confirm the touchscreen works end-to-end before the
real backend is wired up. Run under cage exactly like the real UI:

    sudo chvt 3
    cage -- .venv/bin/python ui_dummy.py        # on the Pi
    UI_WINDOWED=1 python ui_dummy.py             # windowed, on a PC
"""
import os

import pygame

W, H = 800, 480
TXT = (230, 234, 240)
TXT_DIM = (140, 150, 165)
# tap cycles through these (bg, panel, accent) themes
THEMES = [
    ((14, 16, 22), (22, 26, 36), (90, 170, 255)),    # blue
    ((22, 14, 20), (36, 22, 30), (255, 90, 120)),    # red
    ((14, 20, 16), (22, 34, 26), (90, 220, 140)),    # green
    ((20, 18, 12), (34, 30, 22), (250, 190, 60)),    # amber
    ((18, 14, 22), (30, 22, 36), (180, 120, 255)),   # violet
]

TALK = pygame.Rect(20, 400, 360, 64)
CAM = pygame.Rect(400, 400, 180, 64)
TYPE = pygame.Rect(600, 400, 180, 64)


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
    theme = 0
    taps = 0

    def button(rect, label, accent):
        pygame.draw.rect(screen, THEMES[theme][1], rect, border_radius=12)
        pygame.draw.rect(screen, accent, rect, 2, border_radius=12)
        t = font_b.render(label, True, accent)
        screen.blit(t, t.get_rect(center=rect.center))

    clock = pygame.time.Clock()
    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                running = False
            elif e.type == pygame.MOUSEBUTTONDOWN:   # a tap (touch or mouse)
                theme = (theme + 1) % len(THEMES)    # change colours
                taps += 1

        bg, panel, accent = THEMES[theme]
        screen.fill(bg)
        pygame.draw.rect(screen, panel, pygame.Rect(20, 20, 760, 344),
                         border_radius=14)
        screen.blit(font.render("DUMMY MODE - tap anywhere to change colour",
                                True, TXT), (40, 60))
        screen.blit(font.render(f"taps: {taps}", True, TXT_DIM), (40, 100))
        button(TALK, "TALK", accent)
        button(CAM, "Cam", accent)
        button(TYPE, "Type", accent)
        pygame.display.flip()
        clock.tick(20)
    pygame.quit()


if __name__ == "__main__":
    main()
