"""Mocked tests for the fallback policy. No network, no paid calls.

Run: python test_routing.py  ->  "routing tests OK"
"""
import os
import tempfile

from bot import Bot
from usage_store import UsageStore


class FakeErr(Exception):
    """Named to be recognized as temporary by _classify_error."""


class RateLimitError(FakeErr):
    pass


class AuthenticationError(FakeErr):
    pass


def make_bot():
    cfg = {
        "OMNIROUTE_BASE_URL": "http://x/v1", "OMNIROUTE_API_KEY": "k",
        "OLLAMA_BASE_URL": "http://x/v1", "OLLAMA_API_KEY": "k",
        "TEXT_MODELS": "cloud-a, cloud-b", "VISION_MODELS": "vcloud",
        "OLLAMA_TEXT_MODEL": "local-text", "OLLAMA_VISION_MODEL": "local-vis",
        "COOLDOWN_SEC": "120", "OBSERVE_HOURLY_BUDGET": "20",
        "WARM_LOCAL": "0",   # no real Ollama ping during tests
    }
    db = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
    return Bot(cfg, store=UsageStore(db))


def fake_completions(script):
    """script: dict model -> callable returning reply text or raising.
    Supports stream=True (text route) and plain (vision route)."""
    def _chunk(txt):
        delta = type("D", (), {"content": txt})
        return type("Ck", (), {"choices": [type("Ch", (), {"delta": delta})]})
    class C:
        def create(self, model, messages, stream=False, **kw):
            r = script[model]()            # raises here if the route "fails"
            if stream:
                return iter([_chunk(r)])    # single-chunk stream
            class Resp:
                choices = [type("M", (), {"message": type("X", (), {"content": r})})]
                usage = None
            return Resp()
    return type("Client", (), {"chat": type("Ch", (), {"completions": C()})})()


def say(gen):
    """Join a streamed chat() generator into the full reply string."""
    return "".join(gen)


def ok(text):
    return lambda: text


def fail(exc):
    def _():
        raise exc
    return _


# 1. First cloud model fails (rate limit) -> backup cloud succeeds.
b = make_bot()
b._cloud = fake_completions({"cloud-a": fail(RateLimitError()), "cloud-b": ok("B")})
assert say(b.chat("hi")) == "B", "should fall through to backup cloud model"

# 2. All cloud fail -> local Ollama text used.
b = make_bot()
b._cloud = fake_completions({"cloud-a": fail(RateLimitError()),
                            "cloud-b": fail(RateLimitError())})
b._local = fake_completions({"local-text": ok("LOCAL")})
assert say(b.chat("hi")) == "LOCAL", "should fall back to local Ollama"

# 3. Auth error on first cloud model is permanent -> skip backups, go local.
b = make_bot()
b._cloud = fake_completions({"cloud-a": fail(AuthenticationError()),
                            "cloud-b": ok("SHOULD-NOT-REACH")})
b._local = fake_completions({"local-text": ok("LOCAL")})
assert say(b.chat("hi")) == "LOCAL", "auth error -> stop trying cloud, use local"

# 4. Cooldown: a model that just failed is skipped on the next call.
b = make_bot()
b._cloud = fake_completions({"cloud-a": fail(RateLimitError()), "cloud-b": ok("B")})
say(b.chat("one"))                  # consume stream: cloud-a fails, cloud-b ok
assert b.store.in_cooldown("cloud-a", 120), "failed model should be cooling down"

# 5. Vision: cloud fails -> local vision fallback used.
b = make_bot()
b._cloud = fake_completions({"vcloud": fail(RateLimitError())})
b._local = fake_completions({"local-vis": ok("I see a cat")})
assert b.analyze_image(b_bytes := b"jpg") == "I see a cat", "local vision fallback"

# 6. Vision: no local vision configured -> None (caller says "unavailable").
b = make_bot()
b.cfg["OLLAMA_VISION_MODEL"] = ""
b._cloud = fake_completions({"vcloud": fail(RateLimitError())})
assert b.analyze_image(b"jpg") is None, "no vision route -> None, never fabricate"

print("routing tests OK")
