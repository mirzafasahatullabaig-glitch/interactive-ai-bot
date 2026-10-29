"""Tiny file-based RAG retriever. Loads a .jsonl knowledge base (one chunk per
line: {id, topic, source, text}) and returns the chunks whose words best
overlap the query. Keyword overlap, not embeddings -- stdlib only, no new
dependency, good enough for a small hand-written KB.

shortcut: bag-of-words Jaccard-ish scoring, no stemming/synonyms. Swap in
Ollama embeddings (nomic-embed-text) if the KB grows past a few hundred chunks
or recall gets poor.
"""
import json
import re


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", s.lower()))


# Words too common to help ranking (would match every chunk).
_STOP = _tokens("the a an of and or to in is are for with what who how does do "
                "tell me about it its their college this that at on by")


class KB:
    def __init__(self, path):
        self.chunks = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    c = json.loads(line)
                    c["_tok"] = _tokens(c["text"]) | _tokens(c.get("topic", ""))
                    self.chunks.append(c)

    def search(self, query, k=3, min_score=1):
        """Return up to k chunks (dicts) scored by query-word overlap, best
        first. Chunks scoring below min_score are dropped so an unrelated
        question injects nothing."""
        q = _tokens(query) - _STOP
        if not q:
            return []
        scored = [(len(q & c["_tok"]), c) for c in self.chunks]
        scored = [(s, c) for s, c in scored if s >= min_score]
        scored.sort(key=lambda sc: sc[0], reverse=True)
        return [c for _, c in scored[:k]]

    @staticmethod
    def as_context(chunks):
        """Join chunks into a system-prompt snippet with source attribution."""
        return "\n".join(f"- {c['text']} (source: {c['source']})" for c in chunks)
