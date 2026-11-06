"""Interactive AI Bot v0.1 -- terminal entry point.

Commands:
  <text>        chat (cloud -> local Ollama fallback)
  /cam          capture webcam now and describe it
  /voice        record from the mic, transcribe locally, and chat it
  /observe on   start automatic scene-change observation
  /observe off  stop it
  /status       show usage, cooldowns, observation budget
  /quit         exit
"""
import os

from dotenv import dotenv_values

from bot import Bot

DEFAULTS = {
    "OMNIROUTE_BASE_URL": "http://localhost:20128/v1",
    "OMNIROUTE_API_KEY": "",
    "TEXT_MODELS": "",
    "VISION_MODELS": "",
    "OLLAMA_BASE_URL": "http://localhost:11434/v1",
    "OLLAMA_API_KEY": "ollama",
    "OLLAMA_TEXT_MODEL": "",
    "OLLAMA_VISION_MODEL": "",
    "CAMERA_INDEX": "0", "CAPTURE_FPS": "5", "CAMERA_BACKEND": "dshow",
    "FRAME_WIDTH": "640", "FRAME_HEIGHT": "480",
    "SCENE_CHANGE_THRESHOLD": "12", "OBSERVE_INTERVAL_SEC": "10",
    "OBSERVE_HOURLY_BUDGET": "20", "COOLDOWN_SEC": "120",
    "KB_PATH": "kb/geethanjali.jsonl",
    "VOICE_MODEL": "base", "VOICE_SECONDS": "5",
    "VOICE_SILENCE_RMS": "0.01", "VOICE_HANG_SEC": "0.6",
}


def load_cfg():
    # .env overrides real environment overrides defaults.
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in os.environ.items() if k in DEFAULTS})
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    cfg.update({k: v for k, v in dotenv_values(env_path).items() if v is not None})
    return cfg


def stream_reply(bot, text, emit):
    for piece in bot.chat(text):   # stream: emit as it generates
        emit(piece)
    emit("\n")


def main(bt=False):
    cfg = load_cfg()
    bot = Bot(cfg)
    chan = None
    if bt:
        from bluetooth_input import BluetoothChannel
        chan = BluetoothChannel()
        print("Waiting for a phone over Bluetooth (SPP channel 1)...")
        print("Connected:", chan.wait_for_phone())
        read_line = chan.readline
        emit = chan.write
    else:
        read_line = lambda: input("> ").strip()
        emit = lambda s: print(s, end="", flush=True)

    emit("Interactive AI Bot v0.1. Type /quit to exit, /status for stats.\n")
    try:
        while True:
            try:
                line = read_line().strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                if bt:  # SPP sends \n per line; empty read means the phone closed
                    break
                continue
            if line == "/quit":
                break
            elif line == "/cam":
                emit(bot.manual_cam() + "\n")
            elif line == "/observe on":
                bot.start_observing()
                emit("Observing started.\n")
            elif line == "/observe off":
                bot.stop_observing()
                emit("Observing stopped.\n")
            elif line == "/status":
                emit(bot.status() + "\n")
            elif line == "/voice":
                emit("Listening...\n")
                said = bot.listen()           # record + transcribe
                if not said:
                    emit("(Heard nothing.)\n")
                    continue
                emit(f"(heard) {said}\n")
                stream_reply(bot, said, emit)
            elif line.startswith("/"):
                emit(f"Unknown command: {line}\n")
            else:
                stream_reply(bot, line, emit)
    finally:
        bot.shutdown()
        if chan is not None:
            chan.close()
        print("Bye.")


if __name__ == "__main__":
    import sys
    main(bt="--bt" in sys.argv)
