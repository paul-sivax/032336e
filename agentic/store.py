"""Durable local demo store. One orchestrator process, enforced by a file lock.

Transactions use BEGIN IMMEDIATE, including idempotency and budget updates.
Do not deploy this SQLite profile across hosts; see docs/agentic-demo.md.
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from hashlib import sha256
from uuid import uuid4


def uid():
    return uuid4().hex


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.tx() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS records_owner ON records(kind,owner);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    operation_id TEXT NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL,
                    created_at REAL NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def tx(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def get(self, kind, key, owner=None, con=None):
        if con is None:
            with self.tx() as c:
                return self.get(kind, key, owner, c)
        r = con.execute(
            "SELECT owner,data FROM records WHERE kind=? AND id=?", (kind, key)
        ).fetchone()
        return (
            json.loads(r["data"])
            if r and (owner is None or r["owner"] == owner)
            else None
        )

    def put(self, kind, key, owner, data, con=None):
        if con is None:
            with self.tx() as c:
                return self.put(kind, key, owner, data, c)
        con.execute(
            "INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
            (kind, key, owner, canonical(data)),
        )

    def list(self, kind, owner=None, con=None):
        if con is None:
            with self.tx() as c:
                return self.list(kind, owner, c)
        sql, args = "SELECT data FROM records WHERE kind=?", [kind]
        if owner is not None:
            sql += " AND owner=?"
            args.append(owner)
        return [json.loads(r[0]) for r in con.execute(sql, args).fetchall()]

    def event(self, owner, operation_id, event_type, data, con=None):
        if con is None:
            with self.tx() as c:
                return self.event(owner, operation_id, event_type, data, c)
        con.execute(
            "INSERT INTO events(owner,operation_id,type,data,created_at) VALUES(?,?,?,?,?)",
            (owner, operation_id, event_type, canonical(data), time.time()),
        )

    def events(self, owner, after=0):
        with self.tx() as c:
            return [
                {**dict(r), "data": json.loads(r["data"])}
                for r in c.execute(
                    "SELECT * FROM events WHERE owner=? AND id>? ORDER BY id LIMIT 200",
                    (owner, after),
                )
            ]
