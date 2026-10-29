# Milestone 12 — Raspberry Pi 4 Port Notes

Plan only, no code. This maps what carries over unchanged from the Windows
build (`v0.2.0`) and what has to change to run on a Raspberry Pi 4. Read it
before deployment; it is the pre-flight checklist, not a rewrite.

Target: Raspberry Pi 4 (4 GB+), 64-bit Raspberry Pi OS (Bookworm), Python 3.11.

---

## 1. What stays identical

These need no change — the design already isolated them from the OS.

- **Routing / fallback logic** (`bot.py`): OmniRoute cloud → Ollama local,
  cooldowns, fast-fail, streaming, history cap. Pure Python + the `openai` SDK.
- **`usage_store.py`**: SQLite is on the Pi stock. Call log, cooldown, hourly
  budget all port as-is.
- **`main.py` REPL and commands**: `/cam`, `/observe`, `/status`, `/quit`.
- **`test_routing.py`**: mocked, no network, no camera — runs anywhere.
- **The `.env` contract**: every knob stays; only *values* change (below).
- **OpenAI-compatible assumption**: both OmniRoute and Ollama still expose
  `/v1`, so one SDK talks to both. Unchanged.

---

## 2. What changes

### 2.1 Camera backend — the one real code change

`bot._open_cam()` hard-codes the Windows DirectShow backend:

```python
self._cam = cv2.VideoCapture(int(self.cfg["CAMERA_INDEX"]), cv2.CAP_DSHOW)
```

`cv2.CAP_DSHOW` is Windows-only. On the Pi it must be **`cv2.CAP_V4L2`** (or
drop the backend arg and let OpenCV auto-pick). This is the only place that
assumes an OS.

- **USB webcam**: `CAP_V4L2`, device is usually `/dev/video0` → `CAMERA_INDEX=0`.
- **Pi Camera Module (CSI/ribbon)**: on Bookworm the legacy stack is gone. Use
  the libcamera path — either a GStreamer pipeline string into `VideoCapture`,
  or capture with `picamera2` and hand the frame array to the existing
  `cv2.imencode`/`cvtColor` code. The rest of `capture_jpeg` / `scene_changed`
  is backend-agnostic and stays.
- **DSHOW warm-up frames**: the `self._warmup = 5` black-frame discard exists
  because DSHOW returns black until auto-exposure settles. V4L2/libcamera also
  need a short warm-up, so keep the mechanism — just stop calling it a "DSHOW"
  quirk in the comment. 2–3 frames is usually enough on V4L2.

Recommended shape: pick the backend from config (e.g. a `CAMERA_BACKEND`
env of `auto|v4l2|dshow`) so the same tree runs on both machines without an
edit. Default `auto`.

### 2.1b Voice input (/voice) on the Pi

- `VOICE_MODEL`: use **`tiny`** on the Pi, not `base`. Smaller download (~75MB),
  less RAM, faster CPU transcribe — accuracy drop is acceptable for short
  commands.
- Keep the Whisper model **lazy** (loaded on first `/voice`, as it is now).
  Do NOT warm it at startup: on 4 GB, holding Whisper resident alongside the
  3B Ollama model + OpenCV is what tips the box into swap. Pay the RAM only
  when voice is actually used.
- Recording already stops on silence, so there's no fixed wait and less audio
  to transcribe — that's the main latency win and it helps the Pi most.
- `faster-whisper` + `sounddevice` need `libsndfile`/PortAudio on the Pi:
  `sudo apt install libportaudio2 libsndfile1`.

### 2.2 Local model size — `.env` only

The Pi 4 has no usable GPU for LLMs; everything runs CPU-bound on Ollama.

- `OLLAMA_TEXT_MODEL`: the Windows box runs `llama3.1:8b` / (you tested)
  `qwen2.5:0.5b-instruct`. On a Pi 4, **`qwen2.5:3b`** is the sweet spot —
  far better quality than 0.5B, and still runnable (slowly) on 4 GB. Expect
  several seconds per reply. `llama3.1:8b` will swap/thrash on 4 GB — avoid.
- `OLLAMA_VISION_MODEL`: `qwen3-vl:2b` is already small and Pi-viable, but
  vision inference on CPU will be slow (tens of seconds). Acceptable for
  `/cam` on demand; see 2.4 for `/observe`.
- `LOCAL_TIMEOUT_SEC`: bump from `60` — CPU generation is slower. Suggest
  `120`+ so a legitimately-working-but-slow local reply isn't cut off.
- `WARM_LOCAL=1`: keep it. Cold-start matters *more* on the Pi, so preloading
  the model in the background at startup is a bigger win here.

### 2.3 Gateway / network paths

- `OMNIROUTE_BASE_URL=http://localhost:20128/v1` assumes OmniRoute runs on the
  **same machine**. Two options on the Pi:
  1. Run OmniRoute on the Pi too → `localhost` unchanged.
  2. Point the Pi at OmniRoute on another box →
     `http://<gateway-host>:20128/v1`. **Use a placeholder in `.env.example`,
     put the real LAN IP only in the gitignored `.env`** (per your no-LAN-IPs-
     in-git rule).
- `OLLAMA_BASE_URL`: same choice. Ollama on the Pi = `localhost:11434`;
  Ollama on a beefier LAN box = that host's IP (and then local inference is
  no longer "offline", so weigh that against the offline-fallback goal).
- `CLOUD_TIMEOUT_SEC=8`: fine, maybe `10` if the Pi's Wi-Fi is flaky.

### 2.4 Observation scheduler / budget

- `OBSERVE_INTERVAL_SEC=10` is fine for scene-change *detection* (that's cheap
  grayscale diffing), but a changed scene triggers a vision call. On CPU-local
  vision each call is slow, so a busy scene could queue work faster than it
  clears. Raise `OBSERVE_INTERVAL_SEC` (e.g. `30`) and/or lower
  `OBSERVE_HOURLY_BUDGET` for the Pi.
- If cloud vision is wired up (OpenRouter inside OmniRoute), `/observe` leans
  cloud and the CPU cost disappears — that makes the OpenRouter-vision item
  more worthwhile on the Pi than on the desktop.
- `CAPTURE_FPS`, `FRAME_WIDTH/HEIGHT`: 640×480 is a sane Pi default; don't
  raise resolution — it inflates both JPEG encode time and vision token cost.

---

## 3. Environment / install differences

- **OpenCV**: `pip install opencv-python` pulls heavy wheels and can be painful
  on ARM. Prefer the OS package **`sudo apt install python3-opencv`** and use a
  venv with `--system-site-packages`, or `opencv-python-headless` if no GUI is
  needed (this bot is headless — it never calls `imshow`, so headless is ideal
  and smaller).
- **Ollama**: install via the official ARM64 Linux script. Confirm the chosen
  models exist with `ollama list` before first run.
- **Startup / recovery**: Windows runs it from a terminal. On the Pi, run it as
  a **systemd service** (or `systemd --user`) with `Restart=on-failure` so it
  survives crashes and reboots. Note the REPL reads stdin — for an unattended
  service you'd want a non-interactive mode (observe-only), or keep it attached
  to a console/`tmux`. Flag this as a small follow-up, not part of this port.
- **Memory**: 4 GB is tight with a 3B model loaded + OpenCV + camera buffers.
  Add zram or a swapfile as a safety net; prefer the 8 GB Pi 4 if available.
- **Thermals**: sustained CPU LLM inference will throttle a bare Pi. Use a
  heatsink/fan for anything beyond occasional `/cam`.

---

## 4. One-line summary

Only **one line of code** must change (the `CAP_DSHOW` camera backend);
everything else is `.env` values (3B local model, longer timeouts, slower
observe cadence, gateway host) plus OS-level setup (apt OpenCV, Ollama ARM,
systemd autostart, swap/thermals). The architecture ports cleanly.
