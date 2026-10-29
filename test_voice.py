"""Self-check for silence endpointing. No mic, no model. Run: python test_voice.py

Feeds synthetic per-block (rms, samples) sequences to _trim_endpoint and checks
it cuts at the end of speech, waits out the hang, and handles the edge cases.
"""
import numpy as np

from voice_input import _trim_endpoint

LOUD, QUIET = 0.5, 0.0
THRESH = 0.01
HANG = 3  # blocks of quiet that end recording


def blk(rms, n=10):
    return (rms, np.full(n, rms, dtype="float32"))


def main():
    # speech then silence: stops HANG blocks after the last loud block, so it
    # keeps the speech + exactly HANG trailing quiet blocks, drops the rest.
    seq = [blk(LOUD)] * 4 + [blk(QUIET)] * 6
    out = _trim_endpoint(seq, THRESH, HANG)
    assert len(out) == (4 + HANG) * 10, len(out)

    # leading silence before speech is NOT counted as the hang (started=False),
    # so a slow start doesn't cut off the beginning.
    seq = [blk(QUIET)] * 5 + [blk(LOUD)] * 2 + [blk(QUIET)] * HANG
    out = _trim_endpoint(seq, THRESH, HANG)
    assert len(out) == (5 + 2 + HANG) * 10, len(out)

    # all silence (never started): returns everything, no crash, stays a string
    seq = [blk(QUIET)] * 4
    out = _trim_endpoint(seq, THRESH, HANG)
    assert len(out) == 4 * 10 and out.dtype == np.float32

    # a brief quiet gap shorter than HANG does not end mid-sentence
    seq = [blk(LOUD)] * 2 + [blk(QUIET)] * 2 + [blk(LOUD)] * 2 + [blk(QUIET)] * HANG
    out = _trim_endpoint(seq, THRESH, HANG)
    assert len(out) == (2 + 2 + 2 + HANG) * 10, len(out)

    # empty input is safe
    assert len(_trim_endpoint([], THRESH, HANG)) == 0

    print("voice tests OK")


if __name__ == "__main__":
    main()
