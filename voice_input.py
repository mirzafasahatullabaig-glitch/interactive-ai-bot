"""Local speech-to-text for the /voice command. Records from the default mic
(sounddevice), stopping as soon as you stop talking, and transcribes it offline
with faster-whisper.

Stopping on silence (instead of always waiting the full window) cuts the dead
wait every turn and -- on a small box like a Pi 4 -- means less audio to run
through the CPU. Model loads lazily on first use so startup and non-voice runs
pay nothing.
"""
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000  # what Whisper expects
CAPTURE_RATE = 48000 # many USB mics refuse 16k; capture native, decimate to 16k (48000/16000=3)
_BLOCK = 0.1         # seconds per captured block (silence resolution)


def _trim_endpoint(blocks, silence_rms, hang_blocks):
    """Given per-block (rms, samples), return audio up to the end of speech:
    once speech has started, cut after `hang_blocks` consecutive quiet blocks.
    Pure function so the endpointing can be tested without a mic."""
    out, started, quiet = [], False, 0
    for rms, samples in blocks:
        out.append(samples)
        if rms >= silence_rms:
            started, quiet = True, 0
        elif started:
            quiet += 1
            if quiet >= hang_blocks:
                break
    return np.concatenate(out) if out else np.zeros(0, dtype="float32")


class Voice:
    def __init__(self, model_size="base", seconds=5.0, device=None,
                 silence_rms=0.01, hang_seconds=0.6):
        self.model_size = model_size
        self.seconds = float(seconds)          # hard ceiling on one utterance
        self.device = device                   # None = system default mic
        self.silence_rms = float(silence_rms)  # below this a block counts as quiet
        self.hang_seconds = float(hang_seconds)  # quiet run that ends recording
        self._model = None                     # lazy

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            # int8 on CPU: small footprint, Pi-viable. Downloads model once.
            self._model = WhisperModel(self.model_size, device="cpu",
                                       compute_type="int8")
        return self._model

    def record(self):
        """Capture mono 16k audio, stopping ~hang_seconds after speech ends or
        at `seconds`, whichever comes first. Returns a float32 numpy array."""
        block_frames = int(_BLOCK * CAPTURE_RATE)
        max_blocks = max(1, int(self.seconds / _BLOCK))
        hang_blocks = max(1, int(self.hang_seconds / _BLOCK))
        blocks = []
        with sd.InputStream(samplerate=CAPTURE_RATE, channels=1, dtype="float32",
                            blocksize=block_frames, device=self.device) as stream:
            for _ in range(max_blocks):
                data, _overflow = stream.read(block_frames)
                samples = data.reshape(-1)
                blocks.append((float(np.sqrt(np.mean(samples ** 2))), samples))
        audio = _trim_endpoint(blocks, self.silence_rms, hang_blocks)
        return audio[:: CAPTURE_RATE // SAMPLE_RATE]  # decimate to 16k for Whisper

    def transcribe(self, audio):
        """Return the recognized text (stripped), or '' if nothing was said."""
        segments, _ = self._load().transcribe(audio, language="en")
        return " ".join(s.text.strip() for s in segments).strip()

    def listen(self):
        """Record one utterance and return its transcript ('' if silence)."""
        return self.transcribe(self.record())
