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
import time

import pygame

import sysstat
import touchfix
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
BUBBLE_ME = (38, 66, 104)        # user bubble: accent-tinted, right-aligned
BUBBLE_BOT = (34, 40, 52)        # bot bubble: raised panel, left-aligned

# Buttons: (label, id, rect). Big targets for resistive touch.
TALK = pygame.Rect(20, 400, 360, 64)
CAM = pygame.Rect(400, 400, 180, 64)
TYPE = pygame.Rect(600, 400, 180, 64)

# Live-cam overlay: a centered feed with Describe / Close under it.
CAM_VIEW = pygame.Rect(160, 40, 480, 360)   # 4:3 feed, matches 640x480 source
CAM_DESC = pygame.Rect(160, 412, 300, 56)
CAM_CLOSE = pygame.Rect(480, 412, 160, 56)


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
        self.cam_on = False          # live-preview overlay open?
        self._frame = None           # latest camera frame as a pygame Surface
        self._frame_lock = threading.Lock()
        self._cam_stop = None        # Event to stop the grab thread
        self.bt = None               # optional Bluetooth phone channel
        self.add("bot", "Tap Talk and ask me something.")

    def start_bluetooth(self):
        """Start the Bluetooth server in the background. Binding happens inside
        the thread with indefinite retry, because an RFCOMM channel freed by a
        just-killed kiosk can stay bound in the kernel for up to a minute
        (SO_REUSEADDR does NOT work for AF_BLUETOOTH). The UI starts right away
        regardless; BT attaches as soon as the channel frees."""
        threading.Thread(target=self._bt_loop, daemon=True).start()

    def _bind_bt(self):
        """Bind the RFCOMM server, retrying until the channel is free. Returns
        True once bound. Logs each failed attempt so a stuck channel is visible."""
        from bluetooth_input import BluetoothChannel
        delay = 2
        while True:
            try:
                self.bt = BluetoothChannel()
                self._bt_log("bound RFCOMM channel 1")
                return True
            except OSError as e:          # [98] Address already in use -> wait
                self.bt = None
                self._bt_log(f"bind failed ({e}); retry in {delay}s")
                time.sleep(delay)
                delay = min(delay + 2, 10)
            except Exception as e:  # noqa: BLE001 - no radio / not Linux -> give up
                self.bt = None
                self.events.put(("stream_bot", f"(Bluetooth off: {e})"))
                self._bt_log(f"bluetooth off: {e}")
                return False

    def _bt_loop(self):
        """Bind, then accept a phone, serve its lines, and when it disconnects
        go back to waiting so a reconnect works without restarting the kiosk."""
        if not self._bind_bt():
            return
        while True:                      # outer: one iteration per phone session
            self.events.put(("stream_bot", "Waiting for a phone over Bluetooth..."))
            self._bt_log("waiting for phone")
            self.bt.wait_for_phone()
            self.events.put(("stream_bot", "Phone connected."))
            self._bt_log("phone connected")
            while True:                  # inner: one iteration per message
                line = self.bt.readline().strip()
                self._bt_log(f"readline -> {line!r}")
                if not line:             # empty read == phone disconnected
                    self.events.put(("stream_bot", "Phone disconnected."))
                    self._bt_log("phone disconnected; re-accepting")
                    self.bt.reset_conn()  # drop dead conn, allow a fresh accept
                    break                # back to wait_for_phone
                while self.busy:         # one turn at a time; wait for UI to free
                    time.sleep(0.05)
                self.busy = True         # serialize with the touch UI
                try:
                    self._bt_dispatch(line)
                finally:
                    self.busy = False    # always free the UI, even on error
                self._bt_log("reply sent to phone")

    def _bt_dispatch(self, line):
        """Handle a line from the phone. Slash-commands do their real job (the
        app's mic/cam/status buttons send these); anything else is chat. The
        phone's channel is text-only, so /voice records from the PI's mic and
        /cam uses the Pi camera -- the phone just triggers them. Runs with
        self.busy already True; _run_chat here is called directly (not via
        start()) so it streams on this BT thread, and does NOT emit ("done")
        -- the caller clears busy."""
        if line == "/voice":
            # record from the Pi mic (someone speaks near the kiosk), transcribe,
            # then chat it. Tell the phone we're listening so it isn't left hanging.
            self.bt.write("(listening at the kiosk...)\n")
            self.events.put(("status", "listening"))
            try:
                said = self.bot.listen()
            except Exception as e:  # noqa: BLE001
                self.bt.write(f"(voice error: {e})\n")
                return
            if not said:
                self.bt.write("(heard nothing)\n")
                self.events.put(("status", "ready"))
                return
            self.bt.write(f"(heard) {said}\n")
            self._run_chat(said)
        elif line == "/cam":
            self.events.put(("user", "[camera]"))
            self.events.put(("status", "thinking"))
            desc = self.bot.manual_cam()
            self.events.put(("stream", desc))
            self.events.put(("status", "ready"))
            self.bt.write(desc + "\n")
        elif line == "/status":
            self.bt.write(self.bot.status() + "\n")
        elif line.startswith("/"):
            self.bt.write(f"(unknown command: {line})\n")
        else:
            self._run_chat(line)

    @staticmethod
    def _bt_log(msg):
        try:
            with open("/tmp/kiosk_bt.log", "a") as f:
                f.write(f"{time.time():.1f} {msg}\n")
        except OSError:
            pass

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
        if self.bt is not None:      # mirror the reply back to the phone
            self.bt.write("".join(parts) + "\n")
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

    # ----- live camera overlay --------------------------------------------
    def _grab_loop(self, stop):
        """Background: pull RGB frames into a Surface for the UI to blit.
        Runs ~15fps; never touches pygame display, only builds a Surface."""
        while not stop.is_set():
            rgb = self.bot.capture_frame_rgb()
            if rgb is not None:
                # numpy (h,w,3) -> pygame wants (w,h,3); swapaxes, don't copy twice
                surf = pygame.surfarray.make_surface(rgb.swapaxes(0, 1))
                with self._frame_lock:
                    self._frame = surf
            else:
                with self._frame_lock:
                    self._frame = None   # cam gone -> show "no signal"
            time.sleep(1 / 15)

    def open_cam(self):
        if self.cam_on:
            return
        if self.bot.camera_disabled():   # dead GSPCA cam hangs on open; don't try
            self.add("bot", "No camera: needs a UVC webcam. This one is unsupported.")
            return
        self.cam_on = True
        self._cam_stop = threading.Event()
        threading.Thread(target=self._grab_loop, args=(self._cam_stop,),
                         daemon=True).start()

    def close_cam(self):
        if not self.cam_on:
            return
        self.cam_on = False
        if self._cam_stop:
            self._cam_stop.set()
        with self._frame_lock:
            self._frame = None
        self.bot.release_cam()   # don't hold the sensor open when not previewing

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
                elif kind == "stream_bot":     # standalone bot notice (BT status)
                    self.add("bot", val)
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

    def _button(self, rect, label, color, enabled=True, filled=False):
        if filled and enabled:           # solid accent fill, dark label
            pygame.draw.rect(self.screen, color, rect, border_radius=16)
            label_col = BG
        else:                            # outlined on the panel
            pygame.draw.rect(self.screen, PANEL, rect, border_radius=16)
            pygame.draw.rect(self.screen, color if enabled else TXT_DIM, rect, 2,
                             border_radius=16)
            label_col = color if enabled else TXT_DIM
        t = self.font_b.render(label, True, label_col)
        self.screen.blit(t, t.get_rect(center=rect.center))

    def draw_cam(self):
        """Live preview overlay: scale the latest frame into CAM_VIEW."""
        self.screen.fill(BG)
        with self._frame_lock:
            frame = self._frame
        pygame.draw.rect(self.screen, PANEL, CAM_VIEW, border_radius=12)
        if frame is not None:
            self.screen.blit(pygame.transform.smoothscale(frame, CAM_VIEW.size),
                             CAM_VIEW.topleft)
        else:
            msg = self.font.render("No camera signal", True, TXT_DIM)
            self.screen.blit(msg, msg.get_rect(center=CAM_VIEW.center))
        pygame.draw.rect(self.screen, ACCENT, CAM_VIEW, 2, border_radius=12)
        # if a describe is running, the status line shows it
        busy = self.busy and self.status == "thinking"
        self._button(CAM_DESC, "Describing..." if busy else "Describe",
                     ACCENT_WARM if busy else ACCENT, not busy)
        self._button(CAM_CLOSE, "Close", ACCENT_HOT)
        pygame.display.flip()

    def draw(self):
        if self.cam_on:
            self.draw_cam()
            return
        self.screen.fill(BG)
        # transcript panel (shortened to leave room for the stats bar)
        panel = pygame.Rect(20, 20, 760, 344)
        pygame.draw.rect(self.screen, PANEL, panel, border_radius=14)
        # chat bubbles, newest at the bottom, clipped to the panel bottom-up.
        self.screen.set_clip(panel)
        maxw = int(panel.w * 0.72)       # a bubble is at most ~3/4 the panel
        pad, lh, gap = 10, 24, 8         # text padding, line height, bubble gap
        y = panel.bottom - 12
        for who, text in reversed(self.lines):
            mine = who == "you"
            wrapped = self._wrap(text or "...", self.font, maxw - 2 * pad)
            bw = max(self.font.size(ln)[0] for ln in wrapped) + 2 * pad
            bh = len(wrapped) * lh + 2 * pad - 4
            y -= bh
            bx = panel.right - 14 - bw if mine else panel.x + 14
            bubble = pygame.Rect(bx, y, bw, bh)
            fill = BUBBLE_ME if mine else BUBBLE_BOT
            pygame.draw.rect(self.screen, fill, bubble, border_radius=12)
            ty = y + pad - 2
            for ln in wrapped:
                self.screen.blit(self.font.render(ln, True, TXT), (bx + pad, ty))
                ty += lh
            y -= gap
            if y < panel.top + 6:
                break
        self.screen.set_clip(None)
        # status pill (dot + label) in the panel's top-right corner
        dotc = {"ready": ACCENT, "listening": ACCENT_HOT,
                "thinking": ACCENT_WARM}[self.status]
        label = self.font_s.render(self.status, True, dotc)
        pill = pygame.Rect(0, 0, label.get_width() + 34, 26)
        pill.topright = (panel.right - 12, panel.top + 10)
        pygame.draw.rect(self.screen, BG, pill, border_radius=13)
        pygame.draw.circle(self.screen, dotc, (pill.x + 14, pill.centery), 5)
        self.screen.blit(label, (pill.x + 24, pill.centery - label.get_height() // 2))
        # system stats bar (CPU / RAM / temp)
        stat_txt = sysstat.fmt(self.stats)
        self.screen.blit(self.font_s.render(stat_txt, True, TXT_DIM), (24, 374))
        # buttons: TALK is the primary action (filled), Cam/Type are outlined
        talk_c = ACCENT_HOT if self.status == "listening" else ACCENT
        self._button(TALK, "TALK" if not self.busy else "...", talk_c,
                     not self.busy, filled=True)
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
                elif e.type == pygame.MOUSEBUTTONDOWN:
                    p = touchfix.transform(e.pos, W, H, touchfix.default_orient())
                    if self.cam_on:
                        if CAM_CLOSE.collidepoint(p):
                            self.close_cam()
                        elif CAM_DESC.collidepoint(p) and not self.busy:
                            self.start(self._run_cam)   # describe current scene
                    elif self.busy:
                        pass   # ignore taps while a reply is in flight
                    elif TALK.collidepoint(p):
                        self.start(self._run_voice)
                    elif CAM.collidepoint(p):
                        self.open_cam()                 # live preview overlay
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
        self.close_cam()     # stop the grab thread before releasing the bot
        if self.bt is not None:
            self.bt.close()
        self.bot.shutdown()
        pygame.quit()


def main(bt=False):
    pygame.init()
    pygame.mouse.set_visible(False)   # kiosk: taps only, no cursor
    k = Kiosk()
    if bt:
        k.start_bluetooth()
        # systemctl stop/restart sends SIGTERM; without a handler Python exits
        # without running cleanup, orphaning the RFCOMM socket on channel 1 and
        # blocking the next kiosk's bind. Close BT hard, then exit.
        import signal

        def _term(_sig, _frm):
            if k.bt is not None:
                k.bt.close()
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, _term)
    k.run()


if __name__ == "__main__":
    import sys
    main(bt="--bt" in sys.argv)
