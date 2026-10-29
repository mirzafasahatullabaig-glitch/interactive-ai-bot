"""Fullscreen touch kiosk UI for the bot (800x480 HDMI resistive panel).

pygame draws straight to the framebuffer -- no desktop, X, Wayland or browser
needed. Chat transcript + one big Talk button (/voice) + a typed-input fallback
+ a Cam button. The LLM reply and transcription run on a worker thread so the
touchscreen never freezes while the Pi thinks.

Run on the Pi:   python ui_kiosk.py
Run on a PC (windowed, for testing):   UI_WINDOWED=1 python ui_kiosk.py

Layout is fixed to 800x480 and tap targets are large, because a resistive
panel is single-touch and imprecise.
"""
import os
import queue
import threading

import pygame

import sysstat
from bot import Bot
from main import load_cfg

W, H = 800, 480
# premium dark theme w/ accent glow (aesthetic borrowed from the dashboard brief)
BG = (14, 16, 22)
PANEL = (22, 26, 36)
ACCENT = (90, 170, 255)
ACCENT_HOT = (255, 90, 120)      # listening
ACCENT_WARM = (250, 190, 60)     # thinking
TXT = (230, 234, 240)
TXT_DIM = (140, 150, 165)
USER = (120, 200, 160)

# Buttons: (label, id, rect). Big targets for resistive touch.
TALK = pygame.Rect(20, 400, 360, 64)
CAM = pygame.Rect(400, 400, 180, 64)
TYPE = pygame.Rect(600, 400, 180, 64)


class Kiosk:
    def __init__(self):
        self.screen = self._open_display()
        pygame.display.set_caption("Assistant")
        self.font = pygame.font.SysFont("dejavusans", 20)
        self.font_s = pygame.font.SysFont("dejavusans", 15)
        self.font_b = pygame.font.SysFont("dejavusans", 26, bold=True)
        self.bot = Bot(load_cfg())
        self.lines = []            # (speaker, text) transcript
        self.status = "ready"      # ready | listening | thinking
        self.events = queue.Queue()  # worker -> UI (thread-safe)
        self.busy = False
        self.stats = sysstat.snapshot()
        self._stats_at = 0           # last poll time (poll ~1Hz, not per frame)
        self.add("bot", "Tap Talk and ask me something.")

    @staticmethod
    def _open_display():
        """Open an 800x480 surface, trying video backends in order. A console
        Pi (no desktop) has no X/Wayland, so kmsdrm (DRM/KMS) is the one that
        works; UI_WINDOWED or an explicit SDL_VIDEODRIVER wins if set."""
        if os.environ.get("UI_WINDOWED"):
            pygame.display.init()
            return pygame.display.set_mode((W, H))
        # explicit override first, then the Pi console driver, then desktop
        env = os.environ.get("SDL_VIDEODRIVER")
        order = ([env] if env else []) + ["kmsdrm", "x11", "wayland"]
        last = None
        for drv in order:
            try:
                os.environ["SDL_VIDEODRIVER"] = drv
                pygame.display.quit()
                pygame.display.init()
                return pygame.display.set_mode((W, H), pygame.FULLSCREEN)
            except pygame.error as e:  # driver not available -> try next
                last = f"{drv}: {e}"
        raise SystemExit(f"No usable SDL video driver. Last error: {last}\n"
                         "On a console Pi install one, e.g. kmsdrm support, "
                         "or run under a desktop. Try UI_WINDOWED=1 on a PC.")

    def add(self, who, text):
        self.lines.append((who, text))
        self.lines = self.lines[-40:]   # cap transcript memory

    # ----- worker thread: never touch pygame from here, only the queue -----
    def _run_chat(self, text):
        self.events.put(("user", text))
        self.events.put(("status", "thinking"))
        parts = []
        try:
            for piece in self.bot.chat(text):
                parts.append(piece)
                self.events.put(("stream", "".join(parts)))
        except Exception as e:  # noqa: BLE001 - surface failures on screen
            self.events.put(("stream", f"(error: {e})"))
        self.events.put(("done", None))

    def _run_voice(self):
        self.events.put(("status", "listening"))
        try:
            said = self.bot.listen()
        except Exception as e:  # noqa: BLE001
            said = ""
            self.events.put(("stream", f"(voice error: {e})"))
            self.events.put(("done", None))
            return
        if not said:
            self.events.put(("status", "ready"))
            self.events.put(("busy", False))
            return
        self._run_chat(said)

    def _run_cam(self):
        self.events.put(("status", "thinking"))
        self.events.put(("user", "[camera]"))
        desc = self.bot.manual_cam()
        self.events.put(("stream", desc))
        self.events.put(("done", None))

    def start(self, target, *args):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=target, args=args, daemon=True).start()

    # ----- drain worker events into UI state (called on UI thread) ---------
    def pump(self):
        try:
            while True:
                kind, val = self.events.get_nowait()
                if kind == "user":
                    self.add("you", val)
                    self.add("bot", "")        # placeholder to stream into
                elif kind == "stream":
                    if self.lines and self.lines[-1][0] == "bot":
                        self.lines[-1] = ("bot", val)
                elif kind == "status":
                    self.status = val
                elif kind == "busy":
                    self.busy = val
                elif kind == "done":
                    self.status = "ready"
                    self.busy = False
        except queue.Empty:
            pass

    # ----- drawing ---------------------------------------------------------
    def _wrap(self, text, font, maxw):
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.size(t)[0] <= maxw:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or [""]

    def _button(self, rect, label, color, enabled=True):
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=12)
        pygame.draw.rect(self.screen, color if enabled else TXT_DIM, rect, 2,
                         border_radius=12)
        t = self.font_b.render(label, True, color if enabled else TXT_DIM)
        self.screen.blit(t, t.get_rect(center=rect.center))

    def draw(self):
        self.screen.fill(BG)
        # transcript panel (shortened to leave room for the stats bar)
        panel = pygame.Rect(20, 20, 760, 344)
        pygame.draw.rect(self.screen, PANEL, panel, border_radius=14)
        # render newest-last, clipped to the panel from the bottom up
        y = panel.bottom - 14
        for who, text in reversed(self.lines):
            color = USER if who == "you" else (TXT if who == "bot" else TXT_DIM)
            prefix = "You: " if who == "you" else ("" if who == "bot" else "")
            wrapped = self._wrap(prefix + (text or "..."), self.font, panel.w - 28)
            for ln in reversed(wrapped):
                y -= 26
                if y < panel.top + 10:
                    break
                self.screen.blit(self.font.render(ln, True, color), (panel.x + 14, y))
            if y < panel.top + 10:
                break
        # status dot + label
        dotc = {"ready": ACCENT, "listening": ACCENT_HOT,
                "thinking": ACCENT_WARM}[self.status]
        pygame.draw.circle(self.screen, dotc, (panel.right - 20, panel.top + 18), 7)
        self.screen.blit(self.font_s.render(self.status, True, dotc),
                         (panel.right - 110, panel.top + 10))
        # system stats bar (CPU / RAM / temp)
        stat_txt = sysstat.fmt(self.stats)
        self.screen.blit(self.font_s.render(stat_txt, True, TXT_DIM), (24, 372))
        # buttons
        talk_c = ACCENT_HOT if self.status == "listening" else ACCENT
        self._button(TALK, "TALK" if not self.busy else "...", talk_c, not self.busy)
        self._button(CAM, "Cam", ACCENT, not self.busy)
        self._button(TYPE, "Type", ACCENT, not self.busy)
        pygame.display.flip()

    # ----- typed-input fallback (uses the keyboard that's attached) --------
    def type_prompt(self):
        buf = ""
        while True:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    return None
                if e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_RETURN:
                        return buf.strip()
                    if e.key == pygame.K_ESCAPE:
                        return ""
                    if e.key == pygame.K_BACKSPACE:
                        buf = buf[:-1]
                    elif e.unicode and e.unicode.isprintable():
                        buf += e.unicode
            self.screen.fill(BG)
            box = pygame.Rect(20, 200, 760, 80)
            pygame.draw.rect(self.screen, PANEL, box, border_radius=12)
            pygame.draw.rect(self.screen, ACCENT, box, 2, border_radius=12)
            self.screen.blit(self.font.render(buf + "|", True, TXT),
                             (box.x + 14, box.y + 28))
            self.screen.blit(self.font_s.render("Type, Enter to send, Esc to cancel",
                             True, TXT_DIM), (box.x + 14, box.y - 24))
            pygame.display.flip()

    def run(self):
        clock = pygame.time.Clock()
        running = True
        while running:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                    running = False
                elif e.type == pygame.MOUSEBUTTONDOWN and not self.busy:
                    p = e.pos
                    if TALK.collidepoint(p):
                        self.start(self._run_voice)
                    elif CAM.collidepoint(p):
                        self.start(self._run_cam)
                    elif TYPE.collidepoint(p):
                        txt = self.type_prompt()
                        if txt:
                            self.start(self._run_chat, txt)
            self.pump()
            now = pygame.time.get_ticks()
            if now - self._stats_at > 1000:   # refresh stats ~1Hz
                self.stats = sysstat.snapshot()
                self._stats_at = now
            self.draw()
            clock.tick(20)   # 20fps is plenty for a chat UI; easy on the Pi
        self.bot.shutdown()
        pygame.quit()


def main():
    pygame.init()
    pygame.mouse.set_visible(False)   # kiosk: taps only, no cursor
    Kiosk().run()


if __name__ == "__main__":
    main()
