"""Headless self-check for the kiosk UI's worker<->UI event flow. No pygame,
no display -- tests the queue/pump logic that is the only non-trivial part
(a background worker streaming into the transcript without touching the UI
thread's widgets). Run: python test_ui.py
"""
import queue
import threading
import time
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

    def status(self):
        return "all ok"

    def capture_frame_rgb(self):
        return None            # no cam in the stub -> grab loop takes else-branch

    def release_cam(self):
        self.released = True

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
    k.cam_on = False
    k._frame = None
    k._frame_lock = threading.Lock()
    k._cam_stop = None
    k.bt = None
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

    # live-cam overlay: open starts the grab thread, close stops it + releases
    k5, _ = make_kiosk()
    k5.open_cam()
    assert k5.cam_on and k5._cam_stop is not None
    time.sleep(0.2)                       # let the grab loop run (cam returns None)
    assert k5._frame is None              # no-signal path set it to None, no crash
    k5.close_cam()
    assert not k5.cam_on and k5._cam_stop.is_set()
    assert getattr(k5.bot, "released", False), "close_cam must release the camera"
    k5.open_cam()                         # idempotent-ish: reopen after close works
    assert k5.cam_on
    k5.close_cam()

    # Bluetooth: a chat turn mirrors the reply back to the phone channel
    k6, _ = make_kiosk()

    class FakeBT:
        def __init__(self):
            self.sent = []
        def write(self, s):
            self.sent.append(s)
    k6.bt = FakeBT()
    k6._run_chat("hi there")
    k6.pump()
    assert k6.bt.sent == ["hello world \n"], k6.bt.sent   # reply echoed to phone
    assert k6.lines[0] == ("you", "hi there")             # and shown on screen

    # stream_bot event adds a standalone bot notice (BT status lines)
    k7, _ = make_kiosk()
    k7.events.put(("stream_bot", "Phone connected."))
    k7.pump()
    assert k7.lines[-1] == ("bot", "Phone connected."), k7.lines

    # _bt_dispatch routes slash-commands to real actions, not the LLM
    class RecBT:
        def __init__(self):
            self.sent = []
        def write(self, s):
            self.sent.append(s)

    k8, _ = make_kiosk()
    k8.bt = RecBT()
    k8._bt_dispatch("/status")                      # -> bot.status(), not chat
    assert k8.bt.sent == ["all ok\n"], k8.bt.sent

    k9, _ = make_kiosk()
    k9.bt = RecBT()
    k9._bt_dispatch("/cam")                         # -> manual_cam(), not chat
    assert "a desk\n" in k9.bt.sent, k9.bt.sent

    k10, _ = make_kiosk()
    k10.bt = RecBT()
    k10._bt_dispatch("/voice")                      # -> listen() then chat the result
    assert any("listening" in s for s in k10.bt.sent), k10.bt.sent
    assert any("voice question" in s for s in k10.bt.sent), k10.bt.sent
    k10.pump()
    assert k10.lines[0] == ("you", "voice question")  # heard text went to chat

    k11, _ = make_kiosk()
    k11.bt = RecBT()
    k11._bt_dispatch("/bogus")                      # unknown slash-cmd, not chat
    assert any("unknown command" in s for s in k11.bt.sent), k11.bt.sent

    k12, _ = make_kiosk()
    k12.bt = RecBT()
    k12._bt_dispatch("hello there")                 # plain text -> chat
    k12.pump()
    assert k12.lines[0] == ("you", "hello there")
    assert "hello world \n" in k12.bt.sent          # reply mirrored to phone

    print("ui tests OK")


if __name__ == "__main__":
    main()
