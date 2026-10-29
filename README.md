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
| `test_routing.py` | Mocked tests for fallback logic (no network, no paid calls) |
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
`/status` usage stats · `/quit`

## What needs internet
Cloud text + all cloud vision (through OmniRoute). Camera capture, scene-change
detection, Ollama text, and local vision all run offline.
