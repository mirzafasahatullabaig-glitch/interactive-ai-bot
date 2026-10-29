"""Self-check for the RAG retriever. No network. Run: python test_kb.py

Builds a tiny KB in a temp file and checks that relevant queries retrieve the
right chunk, that an unrelated query retrieves nothing, and that scoring ranks
the best match first.
"""
import json
import os
import tempfile

from kb_store import KB

ROWS = [
    {"id": "a", "topic": "placements", "source": "x", "text": "Highest package was 51 LPA from ServiceNow."},
    {"id": "b", "topic": "contact", "source": "y", "text": "Phone +91-9866308271, email info@gcet.edu.in."},
    {"id": "c", "topic": "programs", "source": "z", "text": "B.Tech Computer Science and Mechanical Engineering."},
]


def make_kb():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in ROWS:
            f.write(json.dumps(r) + "\n")
    return path


def main():
    path = make_kb()
    try:
        kb = KB(path)
        assert len(kb.chunks) == 3

        # relevant query finds the right chunk first
        hits = kb.search("what is the highest placement package?")
        assert hits and hits[0]["id"] == "a", hits

        # contact query
        hits = kb.search("email address and phone number")
        assert hits and hits[0]["id"] == "b", hits

        # unrelated query retrieves nothing (min_score gate)
        assert kb.search("what is the weather on mars") == []

        # empty / stopword-only query is safe
        assert kb.search("the a of") == []

        # context formatting cites source
        ctx = KB.as_context(kb.search("package"))
        assert "source: x" in ctx, ctx
    finally:
        os.remove(path)
    print("kb tests OK")


if __name__ == "__main__":
    main()
