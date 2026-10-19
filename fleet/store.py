"""telemetry.db: latest machine state, per-source health, account snapshots, samples.

Written only by the poller. The web app opens it read-only.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fleet import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    configured INTEGER NOT NULL,
    last_attempt TEXT,
    last_success TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

-- One row per machine per source. machine_key = "<source>:<native id>".
CREATE TABLE IF NOT EXISTS machine_state (
    machine_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT,                 -- asic | gpu_rig | rental_server
    location TEXT,             -- home | metered-mining | circlehash | clore | ...
    algorithm TEXT,
    running INTEGER,           -- 1 running, 0 not running, NULL unknown
    status TEXT,               -- source's own status word
    hashrate_hs REAL,
    watts REAL,
    watts_quality TEXT,        -- measured | reported | estimated | NULL
    temp_max REAL,
    extra TEXT,                -- JSON, source-specific detail
    observed_at TEXT,          -- when the source says it last saw the machine
    fetched_at TEXT NOT NULL   -- when we last got this row
);

CREATE TABLE IF NOT EXISTS account_state (
    account_key TEXT PRIMARY KEY,   -- "<source>:<id>"
    source TEXT NOT NULL,
    data TEXT NOT NULL,             -- JSON
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS samples (
    machine_key TEXT NOT NULL,
    ts TEXT NOT NULL,
    hashrate_hs REAL,
    watts REAL,
    running INTEGER,
    PRIMARY KEY (machine_key, ts)
) WITHOUT ROWID;
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect(readonly: bool = False):
    path = settings.TELEMETRY_DB
    if readonly:
        if not path.exists():
            yield None
            return
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        if not readonly:
            conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def record_source(source: str, configured: bool, ok: bool | None, error: str | None = None):
    """ok=None means the source wasn't attempted (unconfigured)."""
    now = utcnow()
    with connect() as conn:
        row = conn.execute("SELECT consecutive_failures FROM source_status WHERE source=?",
                           (source,)).fetchone()
        failures = row["consecutive_failures"] if row else 0
        if ok is True:
            failures = 0
        elif ok is False:
            failures += 1
        conn.execute(
            """INSERT INTO source_status (source, configured, last_attempt, last_success,
                                          last_error, consecutive_failures)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                 configured=excluded.configured,
                 last_attempt=COALESCE(excluded.last_attempt, source_status.last_attempt),
                 last_success=COALESCE(excluded.last_success, source_status.last_success),
                 last_error=excluded.last_error,
                 consecutive_failures=excluded.consecutive_failures""",
            (source, int(configured), now if ok is not None else None,
             now if ok else None, (error or "")[:500] or None, failures),
        )


def upsert_machines(machines: list[dict]):
    """Write the latest state for each machine and append a sample."""
    now = utcnow()
    with connect() as conn:
        for m in machines:
            conn.execute(
                """INSERT INTO machine_state (machine_key, source, name, kind, location, algorithm,
                       running, status, hashrate_hs, watts, watts_quality, temp_max, extra,
                       observed_at, fetched_at)
                   VALUES (:machine_key, :source, :name, :kind, :location, :algorithm,
                       :running, :status, :hashrate_hs, :watts, :watts_quality, :temp_max, :extra,
                       :observed_at, :fetched_at)
                   ON CONFLICT(machine_key) DO UPDATE SET
                       name=excluded.name, kind=excluded.kind, location=excluded.location,
                       algorithm=excluded.algorithm, running=excluded.running,
                       status=excluded.status, hashrate_hs=excluded.hashrate_hs,
                       watts=excluded.watts, watts_quality=excluded.watts_quality,
                       temp_max=excluded.temp_max, extra=excluded.extra,
                       observed_at=excluded.observed_at, fetched_at=excluded.fetched_at""",
                {
                    "kind": None, "location": None, "algorithm": None, "running": None,
                    "status": None, "hashrate_hs": None, "watts": None, "watts_quality": None,
                    "temp_max": None, "observed_at": None,
                    **m,
                    "running": None if m.get("running") is None else int(bool(m["running"])),
                    "extra": json.dumps(m.get("extra") or {}),
                    "fetched_at": now,
                },
            )
            conn.execute(
                "INSERT OR REPLACE INTO samples (machine_key, ts, hashrate_hs, watts, running) "
                "VALUES (?, ?, ?, ?, ?)",
                (m["machine_key"], now, m.get("hashrate_hs"), m.get("watts"),
                 None if m.get("running") is None else int(bool(m["running"]))),
            )


def upsert_account(account_key: str, source: str, data: dict):
    with connect() as conn:
        conn.execute(
            """INSERT INTO account_state (account_key, source, data, fetched_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(account_key) DO UPDATE SET data=excluded.data, fetched_at=excluded.fetched_at""",
            (account_key, source, json.dumps(data), utcnow()),
        )


def purge_samples():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.SAMPLE_RETENTION_DAYS)).isoformat()
    with connect() as conn:
        conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))


def read_all() -> dict:
    """Snapshot of everything, for the web app. Empty if the poller hasn't run yet."""
    with connect(readonly=True) as conn:
        if conn is None:
            return {"sources": [], "machines": [], "accounts": []}
        try:
            sources = [dict(r) for r in conn.execute("SELECT * FROM source_status ORDER BY source")]
            machines = [dict(r) for r in conn.execute("SELECT * FROM machine_state ORDER BY location, name")]
            accounts = [dict(r) for r in conn.execute("SELECT * FROM account_state ORDER BY account_key")]
        except sqlite3.OperationalError:
            return {"sources": [], "machines": [], "accounts": []}
    for m in machines:
        m["extra"] = json.loads(m.get("extra") or "{}")
        # SQLite returns 1/0; the view distinguishes True/False/None (unknown)
        m["running"] = None if m["running"] is None else bool(m["running"])
    for a in accounts:
        a["data"] = json.loads(a["data"])
    return {"sources": sources, "machines": machines, "accounts": accounts}
