"""Headless self-check for the kiosk UI's worker<->UI event flow. No pygame,
no display -- tests the queue/pump logic that is the only non-trivial part
(a background worker streaming into the transcript without touching the UI
thread's widgets). Run: python test_ui.py
"""
import queue
import types


# Re-implement nothing: import the class but stub out pygame + Bot so the
# event logic is exercised in isolation.
class FakeBot:
    def __init__(self, reply="hello world"):
        self.reply = reply

    def chat(self, text):
        for w in self.reply.split():
            yield w + " "

    def listen(self):
        return "voice question"

    def manual_cam(self):
        return "a desk"

    def shutdown(self):
        pass


def make_kiosk():
    # Build a Kiosk-like object without running pygame: borrow the real methods.
    import importlib.util
    spec = importlib.util.spec_from_file_location("ui_kiosk", "ui_kiosk.py")
    # Inject a dummy pygame so import doesn't fail.
    import sys
    sys.modules.setdefault("pygame", types.ModuleType("pygame"))
    sys.modules["pygame"].Rect = lambda *a: None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ui_kiosk"] = mod
    spec.loader.exec_module(mod)

    k = mod.Kiosk.__new__(mod.Kiosk)      # skip __init__ (no pygame/Bot)
    k.bot = FakeBot()
    k.lines = []
    k.status = "ready"
    k.events = queue.Queue()
    k.busy = False
    return k, mod


def drain(k):
    # run worker fully (synchronously) then pump into UI state
    k.pump()


def main():
    k, mod = make_kiosk()

    # a chat turn: worker emits user + stream*, pump builds transcript
    k._run_chat("hi there")
    k.pump()
    assert k.lines[0] == ("you", "hi there"), k.lines
    assert k.lines[1][0] == "bot" and "hello world" in k.lines[1][1], k.lines
    assert k.status == "ready" and k.busy is False

    # voice turn with empty transcript resets cleanly (no dangling busy)
    k2, _ = make_kiosk()
    k2.bot.listen = lambda: ""            # silence
    k2.busy = True
    k2._run_voice()
    k2.pump()
    assert k2.busy is False and k2.status == "ready", (k2.busy, k2.status)
    assert k2.lines == [], k2.lines       # nothing added on silence

    # cam turn streams the description into a bot line
    k3, _ = make_kiosk()
    k3._run_cam()
    k3.pump()
    assert k3.lines[0] == ("you", "[camera]")
    assert k3.lines[1] == ("bot", "a desk"), k3.lines

    # transcript is capped
    k4, _ = make_kiosk()
    for i in range(60):
        k4.add("bot", str(i))
    assert len(k4.lines) == 40 and k4.lines[-1] == ("bot", "59")

    print("ui tests OK")


if __name__ == "__main__":
    main()
