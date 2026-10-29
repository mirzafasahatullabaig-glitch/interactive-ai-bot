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
    "CAMERA_INDEX": "0", "CAPTURE_FPS": "5",
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
    cfg.update({k: v for k, v in dotenv_values(".env").items() if v is not None})
    return cfg


def stream_reply(bot, text):
    for piece in bot.chat(text):   # stream: print as it generates
        print(piece, end="", flush=True)
    print()


def main():
    cfg = load_cfg()
    bot = Bot(cfg)
    print("Interactive AI Bot v0.1. Type /quit to exit, /status for stats.")
    try:
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line == "/quit":
                break
            elif line == "/cam":
                print(bot.manual_cam())
            elif line == "/observe on":
                bot.start_observing()
                print("Observing started.")
            elif line == "/observe off":
                bot.stop_observing()
                print("Observing stopped.")
            elif line == "/status":
                print(bot.status())
            elif line == "/voice":
                print("Listening...", flush=True)
                said = bot.listen()           # record + transcribe
                if not said:
                    print("(Heard nothing.)")
                    continue
                print(f"(heard) {said}")
                stream_reply(bot, said)
            elif line.startswith("/"):
                print(f"Unknown command: {line}")
            else:
                stream_reply(bot, line)
    finally:
        bot.shutdown()
        print("Bye.")


if __name__ == "__main__":
    main()
