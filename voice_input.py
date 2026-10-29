"""Local speech-to-text for the /voice command. Records a fixed window from the
default mic (sounddevice) and transcribes it offline with faster-whisper.

Model loads lazily on first use so startup and non-voice runs pay nothing.
shortcut: fixed-duration record (no silence detection); raise VOICE_SECONDS or
switch to push-to-talk if utterances get cut off.
"""
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000  # what Whisper expects


class Voice:
    def __init__(self, model_size="base", seconds=5.0, device=None):
        self.model_size = model_size
        self.seconds = float(seconds)
        self.device = device        # None = system default mic
        self._model = None          # lazy

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            # int8 on CPU: small footprint, Pi-viable. Downloads model once.
            self._model = WhisperModel(self.model_size, device="cpu",
                                       compute_type="int8")
        return self._model

    def record(self):
        """Capture `seconds` of mono 16k audio as a float32 numpy array."""
        frames = int(self.seconds * SAMPLE_RATE)
        audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                       dtype="float32", device=self.device)
        sd.wait()
        return audio.reshape(-1)

    def transcribe(self, audio):
        """Return the recognized text (stripped), or '' if nothing was said."""
        segments, _ = self._load().transcribe(audio, language="en")
        return " ".join(s.text.strip() for s in segments).strip()

    def listen(self):
        """Record one utterance and return its transcript ('' if silence)."""
        return self.transcribe(self.record())
