"""SQLite-backed usage, cooldown and observation-budget tracking.

One table `calls` records every model call (success or failure). Everything else
(per-model counts, cooldowns, hourly observation budget) is derived from it with
queries, so there is a single source of truth and nothing to keep in sync.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "bot.sqlite3"


class UsageStore:
    def __init__(self, db_path=DB_PATH):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the observation scheduler runs in a timer
        # thread; all writes are short and serialized by SQLite's own lock.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS calls (
                   ts        REAL NOT NULL,   -- epoch seconds
                   kind      TEXT NOT NULL,   -- 'text' | 'vision'
                   model     TEXT NOT NULL,
                   source    TEXT NOT NULL,   -- 'cloud' | 'local'
                   ok        INTEGER NOT NULL,-- 1 success, 0 failure
                   error     TEXT,            -- error class on failure
                   automatic INTEGER NOT NULL DEFAULT 0,  -- 1 = auto observation
                   tokens    INTEGER DEFAULT 0
               )"""
        )
        self.conn.commit()

    def record(self, kind, model, source, ok, error=None, automatic=False, tokens=0):
        self.conn.execute(
            "INSERT INTO calls (ts, kind, model, source, ok, error, automatic, tokens)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), kind, model, source, int(ok), error, int(automatic), tokens),
        )
        self.conn.commit()

    def in_cooldown(self, model, cooldown_sec):
        """True if `model`'s most recent call was a failure within cooldown_sec."""
        row = self.conn.execute(
            "SELECT ok, ts FROM calls WHERE model=? ORDER BY ts DESC LIMIT 1",
            (model,),
        ).fetchone()
        if not row:
            return False
        ok, ts = row
        return not ok and (time.time() - ts) < cooldown_sec

    def observations_last_hour(self):
        """Count of SUCCESSFUL automatic vision calls in the last 3600s."""
        cutoff = time.time() - 3600
        return self.conn.execute(
            "SELECT COUNT(*) FROM calls"
            " WHERE automatic=1 AND kind='vision' AND ok=1 AND ts>=?",
            (cutoff,),
        ).fetchone()[0]

    def summary(self):
        """Aggregate stats for the /status command."""
        rows = self.conn.execute(
            "SELECT source, kind, ok, COUNT(*), COALESCE(SUM(tokens),0)"
            " FROM calls GROUP BY source, kind, ok"
        ).fetchall()
        return {
            "rows": rows,                       # (source, kind, ok, count, tokens)
            "observations_last_hour": self.observations_last_hour(),
            "total": self.conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0],
        }

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    # assert-based self-check: cooldown + budget logic, on a temp DB.
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
    s = UsageStore(p)
    assert not s.in_cooldown("m", 120), "no history -> not in cooldown"
    s.record("text", "m", "cloud", ok=False, error="RateLimit")
    assert s.in_cooldown("m", 120), "recent failure -> in cooldown"
    s.record("text", "m", "cloud", ok=True)
    assert not s.in_cooldown("m", 120), "later success -> cooldown cleared"
    assert s.observations_last_hour() == 0, "no auto vision yet"
    s.record("vision", "v", "cloud", ok=True, automatic=True)
    assert s.observations_last_hour() == 1, "one auto vision counted"
    s.record("vision", "v", "cloud", ok=False, automatic=True)
    assert s.observations_last_hour() == 1, "failed auto vision not counted"
    print("usage_store self-check OK")
