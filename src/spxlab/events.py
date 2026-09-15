"""Append-only evidence log; derived reports can always be regenerated."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            body TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL)""")
        row = self.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        self.previous = row[0] if row else "0" * 64

    def append(self, kind, payload, *, run_id, generation=0, mono=None, utc=None):
        body = {"schema_version": 1, "event_type": kind, "payload": payload,
                "run_id": run_id, "generation": generation,
                "recorded_at": utc or utc_now(),
                "monotonic_ns": time.monotonic_ns() if mono is None else mono}
        encoded = canonical(body)
        digest = hashlib.sha256((self.previous + encoded).encode()).hexdigest()
        cur = self.db.execute("INSERT INTO events(body,previous_hash,hash) VALUES(?,?,?)",
                              (encoded, self.previous, digest))
        self.previous = digest
        return {"seq": cur.lastrowid, **body, "hash": digest}

    def close(self):
        self.db.close()


def read_events(path):
    db = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    previous = "0" * 64
    last_seq = 0
    try:
        for seq, body, prev, digest in db.execute("SELECT seq,body,previous_hash,hash FROM events ORDER BY seq"):
            if seq != last_seq+1 or prev != previous:
                raise ValueError("Evidence log sequence/hash chain mismatch")
            if hashlib.sha256((prev+body).encode()).hexdigest() != digest:
                raise ValueError("Evidence log content hash mismatch")
            yield {"seq": seq, **json.loads(body), "hash": digest}
            previous, last_seq = digest, seq
    finally:
        db.close()
