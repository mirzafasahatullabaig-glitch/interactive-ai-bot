# Interactive AI Bot v0.1

A terminal AI bot (Windows): chats via cloud models through the **OmniRoute**
gateway, falls back to **local Ollama** when cloud fails, and can watch the
webcam and describe scene changes.

## Files
| File | Purpose |
|---|---|
| `main.py` | Terminal REPL + command dispatch |
| `bot.py` | Controller: text/vision routing, fallback, camera, scheduler |
| `usage_store.py` | SQLite: call log, cooldowns, hourly observation budget |
| `ui_kiosk.py` | Fullscreen 800x480 touch kiosk UI (pygame, framebuffer) |
| `sysstat.py` | CPU/RAM/temp readout for the kiosk (stdlib, Pi `/proc`+`vcgencmd`) |
| `voice_input.py` | `/voice` speech-to-text (faster-whisper, local/offline) |
| `kb_store.py` | File-based RAG retriever (keyword overlap, stdlib only) |
| `kb/*.jsonl` | Knowledge-base chunks injected into chat context (see `KB_PATH`) |
| `test_routing.py` | Mocked tests for fallback logic (no network, no paid calls) |
| `test_kb.py` | Self-check for the RAG retriever |
| `.env.example` | Config template — copy to `.env`, never commit `.env` |

## Setup (PowerShell)
```powershell
cd F:\interactive-ai-bot
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then edit .env
python usage_store.py         # self-check -> "usage_store self-check OK"
python test_routing.py        # -> "routing tests OK"
python main.py
```

You still need to install + run OmniRoute (http://localhost:20128) and connect
at least one free provider, and have Ollama running with the models named in
`.env`. See the build milestones for the exact order.

## Commands
`<text>` chat · `/cam` describe webcam now · `/observe on|off` auto-watch ·
`/status` usage stats · `/voice` mic → transcribe → chat · `/quit`
Run with `--bt` to drive it from a phone over Bluetooth (see below).

## Phone over Bluetooth (replaces the touchscreen)
Run `python main.py --bt` to take input from a phone instead of the keyboard:
the phone is the keyboard+screen over an RFCOMM/SPP serial link, so any
"Bluetooth serial terminal" app works (no custom app). Stdlib sockets only.

One-time Pi setup (sudo):
```
sudo rfkill unblock bluetooth
sudo hciconfig hci0 up && sudo hciconfig hci0 piscan
# SPP discovery needs bluetoothd in compat mode, else sdptool is a silent no-op:
sudo sed -i 's|bluetoothd|bluetoothd --compat|' /lib/systemd/system/bluetooth.service
sudo systemctl daemon-reload && sudo systemctl restart bluetooth
sudo sdptool add --channel=1 SP && sudo chmod 666 /var/run/sdp
```
Pair the phone once with `bluetoothctl` (agent on; confirm passkey; `trust <mac>`),
then connect from the app to the advertised Serial Port on channel 1.

## What needs internet
Cloud text + all cloud vision (through OmniRoute). Camera capture, scene-change
detection, Ollama text, and local vision all run offline.
