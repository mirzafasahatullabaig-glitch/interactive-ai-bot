"""Interactive AI Bot v0.1 controller.

Routes text and vision requests through OmniRoute (cloud) with an Ollama local
fallback, tracks usage/cooldowns/budget via UsageStore, and owns the webcam +
local scene-change detection for automatic observation.

Both OmniRoute and Ollama are OpenAI-compatible, so one SDK (openai) talks to
both -- only the base_url / api_key / model differ.
"""
import base64
import os
import threading
import time

import cv2
from openai import OpenAI

from kb_store import KB
from usage_store import UsageStore

# Error classes that mean "temporary, try a backup / cool this model down".
# Anything else (e.g. auth 401, bad request) is permanent -> don't retry it.
TEMPORARY_ERRORS = ("RateLimit", "APITimeout", "InternalServer", "APIConnection")


def _classify_error(exc):
    name = type(exc).__name__
    for tag in TEMPORARY_ERRORS:
        if tag in name:
            return tag
    return name  # permanent / unknown


class Bot:
    def __init__(self, cfg, store=None):
        self.cfg = cfg
        self.store = store or UsageStore()
        # Short timeout + no SDK retries: a dead free route should fail fast so
        # we fall through to the next model / local, not stall ~14s per try.
        ct = float(cfg.get("CLOUD_TIMEOUT_SEC", 8))
        self._cloud = OpenAI(base_url=cfg["OMNIROUTE_BASE_URL"],
                             api_key=cfg["OMNIROUTE_API_KEY"],
                             timeout=ct, max_retries=0)
        self._local = OpenAI(base_url=cfg["OLLAMA_BASE_URL"],
                             api_key=cfg["OLLAMA_API_KEY"],
                             timeout=float(cfg.get("LOCAL_TIMEOUT_SEC", 60)),
                             max_retries=0)
        self.history = []            # chat memory for this session
        self.observations = []       # recent scene descriptions (context)
        # Optional RAG knowledge base; absent/empty path -> disabled, no change.
        kb_path = cfg.get("KB_PATH", "")
        try:
            self._kb = KB(kb_path) if kb_path else None
        except OSError:
            self._kb = None          # missing file shouldn't crash the bot
        self._voice = None           # lazy: built on first /voice (see listen)
        self._cam = None
        self._warmup = 0
        self._cam_lock = threading.Lock()   # serialize camera access
        self._prev_gray = None
        self._observe = False
        self._timer = None
        self._warm_local()

    # ----- text route ----------------------------------------------------
    def chat(self, user_text):
        """Stream the reply: yields text chunks. Falls back cloud->local
        before the first chunk, so no half-printed wrong-provider output."""
        self.history.append({"role": "user", "content": user_text})
        sys_prompt = "You are a friendly, concise assistant."
        if self._kb:
            hits = self._kb.search(user_text)
            if hits:
                sys_prompt += ("\nUse these facts if relevant; cite the source:\n"
                               + KB.as_context(hits))
        if self.observations:
            sys_prompt += " Recent things you have seen: " + \
                "; ".join(self.observations[-3:])
        # shortcut: cap history to last 8 turns so prompt stays short as chat
        # grows; raise HISTORY_TURNS if you need longer memory.
        turns = int(self.cfg.get("HISTORY_TURNS", 8))
        messages = [{"role": "system", "content": sys_prompt},
                    *self.history[-turns:]]

        stream = self._open_text_stream(messages)
        if stream is None:
            yield "(All text providers failed, including local Ollama.)"
            return
        parts = []
        for piece in stream:
            parts.append(piece)
            yield piece
        self.history.append({"role": "assistant", "content": "".join(parts)})

    def _open_text_stream(self, messages):
        """Return a generator of text chunks from the first working route, or
        None. Pulls the first chunk here so a failed route falls through
        BEFORE the caller prints anything."""
        for model in self._split(self.cfg["TEXT_MODELS"]):
            if self.store.in_cooldown(model, int(self.cfg["COOLDOWN_SEC"])):
                continue
            gen, stop_cloud = self._stream_one(self._cloud, model, "cloud", messages)
            if gen is not None:
                return gen
            if stop_cloud:
                break  # permanent/connection error -> siblings on same gateway fail too
        model = self.cfg.get("OLLAMA_TEXT_MODEL")
        if model:
            return self._stream_one(self._local, model, "local", messages)[0]
        return None

    def _stream_one(self, client, model, source, messages):
        """Start a streaming completion. Returns (generator, stop_cloud):
        generator of chunks if the first chunk arrives, else None; stop_cloud
        True if the failure means no sibling cloud model will work either."""
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, stream=True)
            first = None
            for chunk in resp:
                d = chunk.choices[0].delta.content if chunk.choices else None
                if d:
                    first = d
                    break
            if first is None:          # empty stream -> treat as failure
                self.store.record("text", model, source, ok=False, error="EmptyStream")
                return None, False
        except Exception as e:  # noqa: BLE001
            err = _classify_error(e)
            self.store.record("text", model, source, ok=False, error=err)
            stop = err not in TEMPORARY_ERRORS or err == "APIConnection"
            return None, stop

        def gen():
            yield first
            try:
                for chunk in resp:
                    d = chunk.choices[0].delta.content if chunk.choices else None
                    if d:
                        yield d
            finally:
                self.store.record("text", model, source, ok=True)
        return gen(), False

    # ----- vision route --------------------------------------------------
    def analyze_image(self, jpg_bytes, prompt="Describe what you see.", automatic=False):
        """Return a description, or None if no vision route succeeded."""
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpg_bytes).decode()
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        messages = [{"role": "user", "content": content}]

        desc = self._try_vision_cloud(messages, automatic)
        if desc is None:
            desc = self._try_vision_local(messages, automatic)
        return desc  # None => caller must say "image analysis unavailable"

    def _try_vision_cloud(self, messages, automatic):
        for model in self._split(self.cfg["VISION_MODELS"]):
            if self.store.in_cooldown(model, int(self.cfg["COOLDOWN_SEC"])):
                continue
            try:
                r = self._cloud.chat.completions.create(model=model, messages=messages)
                tok = getattr(getattr(r, "usage", None), "total_tokens", 0) or 0
                self.store.record("vision", model, "cloud", ok=True,
                                 automatic=automatic, tokens=tok)
                return r.choices[0].message.content
            except Exception as e:  # noqa: BLE001
                err = _classify_error(e)
                self.store.record("vision", model, "cloud", ok=False,
                                 error=err, automatic=automatic)
                if err not in TEMPORARY_ERRORS or err == "APIConnection":
                    break  # permanent OR gateway unreachable -> siblings will fail too
        return None

    def _try_vision_local(self, messages, automatic):
        model = self.cfg.get("OLLAMA_VISION_MODEL")
        if not model:
            return None
        try:
            r = self._local.chat.completions.create(model=model, messages=messages)
            self.store.record("vision", model, "local", ok=True, automatic=automatic)
            return r.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            self.store.record("vision", model, "local", ok=False,
                             error=_classify_error(e), automatic=automatic)
            return None

    # ----- camera --------------------------------------------------------
    # OpenCV capture backend by CAMERA_BACKEND; same tree runs on Windows + Linux.
    _BACKENDS = {"auto": 0, "dshow": cv2.CAP_DSHOW, "v4l2": cv2.CAP_V4L2}

    def _open_cam(self):
        if self._cam is None:
            # dshow avoids slow MSMF init on Windows; v4l2 for Linux/Pi USB cams;
            # auto lets OpenCV pick. Any backend returns black frames until the
            # sensor's auto-exposure settles, so discard the first few.
            backend = self._BACKENDS.get(self.cfg.get("CAMERA_BACKEND", "auto"), 0)
            self._cam = cv2.VideoCapture(int(self.cfg["CAMERA_INDEX"]), backend)
            self._cam.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.cfg["FRAME_WIDTH"]))
            self._cam.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.cfg["FRAME_HEIGHT"]))
            self._warmup = 5  # discard first frames until auto-exposure settles
        return self._cam

    def capture_jpeg(self):
        """Grab one frame as JPEG bytes, or None on failure. Thread-safe."""
        with self._cam_lock:
            cam = self._open_cam()
            for _ in range(getattr(self, "_warmup", 0)):
                cam.read()
            self._warmup = 0
            ok, frame = cam.read()
            if not ok or frame is None:
                return None, None
            ok, buf = cv2.imencode(".jpg", frame)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            return (buf.tobytes() if ok else None), gray

    def scene_changed(self, gray):
        """Mean per-pixel grayscale diff vs previous frame > threshold."""
        if gray is None:
            return False
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            return True  # first frame always "changed"
        diff = cv2.absdiff(self._prev_gray, gray).mean()
        self._prev_gray = gray
        return diff > float(self.cfg["SCENE_CHANGE_THRESHOLD"])

    def manual_cam(self):
        """/cam : capture now and describe (priority, counts as non-automatic)."""
        jpg, _ = self.capture_jpeg()
        if jpg is None:
            return "(Camera unavailable.)"
        desc = self.analyze_image(jpg, automatic=False)
        return desc or "(Image analysis unavailable: all vision routes failed.)"

    # ----- voice input ---------------------------------------------------
    def listen(self):
        """/voice : record one utterance from the mic and return its transcript
        ('' if silence). Builds the Voice engine (and loads the Whisper model)
        on first use so startup and non-voice runs pay nothing."""
        if self._voice is None:
            from voice_input import Voice
            self._voice = Voice(
                model_size=self.cfg.get("VOICE_MODEL", "base"),
                seconds=float(self.cfg.get("VOICE_SECONDS", 5)),
                silence_rms=float(self.cfg.get("VOICE_SILENCE_RMS", 0.01)),
                hang_seconds=float(self.cfg.get("VOICE_HANG_SEC", 0.6)))
        return self._voice.listen()

    # ----- observation scheduler ----------------------------------------
    def start_observing(self):
        self._observe = True
        self._schedule()

    def stop_observing(self):
        self._observe = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _schedule(self):
        if not self._observe:
            return
        interval = float(self.cfg["OBSERVE_INTERVAL_SEC"])
        self._timer = threading.Timer(interval, self._observe_tick)
        self._timer.daemon = True
        self._timer.start()

    def _observe_tick(self):
        try:
            jpg, gray = self.capture_jpeg()
            budget = int(self.cfg["OBSERVE_HOURLY_BUDGET"])
            if jpg is not None and self.scene_changed(gray) \
                    and self.store.observations_last_hour() < budget:
                desc = self.analyze_image(jpg, "Briefly describe the scene.",
                                         automatic=True)
                if desc:
                    self.observations.append(desc)
                    print(f"\n[observation] {desc}\n> ", end="", flush=True)
        finally:
            self._schedule()  # always reschedule, even if this tick failed

    # ----- misc ----------------------------------------------------------
    def _warm_local(self):
        """Fire a 1-token Ollama request in the background so the model is
        loaded before the first real chat (removes cold-start latency)."""
        model = self.cfg.get("OLLAMA_TEXT_MODEL")
        if not model or str(self.cfg.get("WARM_LOCAL", "1")) == "0":
            return

        def ping():
            try:
                self._local.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1)
            except Exception:  # noqa: BLE001 - warm-up is best-effort
                pass
        threading.Thread(target=ping, daemon=True).start()

    @staticmethod
    def _split(csv):
        return [m.strip() for m in csv.split(",") if m.strip()]

    def status(self):
        s = self.store.summary()
        lines = [f"Total calls: {s['total']}",
                 f"Auto observations last hour: {s['observations_last_hour']}"
                 f"/{self.cfg['OBSERVE_HOURLY_BUDGET']}",
                 f"Observing: {'on' if self._observe else 'off'}"]
        for source, kind, ok, count, tokens in s["rows"]:
            lines.append(f"  {source}/{kind} {'ok' if ok else 'fail'}: "
                        f"{count} calls, {tokens} tokens")
        return "\n".join(lines)

    def shutdown(self):
        self.stop_observing()
        with self._cam_lock:
            if self._cam is not None:
                self._cam.release()
                self._cam = None
        self.store.close()
