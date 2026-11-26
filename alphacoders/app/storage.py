import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException

from .schema import Dataset

ROOT = Path(__file__).resolve().parent.parent


def now():
    return datetime.now(UTC).isoformat()


@contextmanager
def connect(write=False, initialize=False):
    path = Path(os.environ.get("MULEX_DB", ROOT / "data/workbench.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        if initialize:
            db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db
        db.commit()
    except sqlite3.OperationalError as exc:
        if getattr(exc, "sqlite_errorname", "") in {"SQLITE_BUSY", "SQLITE_LOCKED"}:
            raise HTTPException(
                503, "Database busy. Retry this request.", headers={"Retry-After": "1"}
            ) from exc
        raise
    finally:
        db.close()


def put_dataset(db, data):
    payload = data.model_dump_json()
    key = sha256(payload.encode()).hexdigest()[:24]
    db.execute(
        "INSERT OR IGNORE INTO datasets VALUES (?, ?, ?, ?)",
        (key, data.name, payload, now()),
    )
    return key


def setup():
    with connect(write=True, initialize=True) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > 3:
            raise RuntimeError("This database requires a newer MULEX release")
        db.execute(
            "CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, name TEXT, payload TEXT, created TEXT)"
        )
        db.execute("""CREATE TABLE IF NOT EXISTS case_events (
            dataset TEXT NOT NULL REFERENCES datasets(id), account TEXT NOT NULL,
            revision INTEGER NOT NULL, event_id TEXT NOT NULL, analyst TEXT NOT NULL,
            status TEXT NOT NULL, note TEXT NOT NULL, created TEXT NOT NULL,
            PRIMARY KEY(dataset,account,revision), UNIQUE(dataset,account,event_id))""")
        if version < 3:
            db.execute("PRAGMA user_version=3")
        demo = Dataset.model_validate_json(
            (ROOT / "examples" / "shadowflow.json").read_bytes()
        )
        put_dataset(db, demo)


def dataset_text(db, key):
    row = db.execute("SELECT payload FROM datasets WHERE id=?", (key,)).fetchone()
    if row is None:
        raise HTTPException(404, "Dataset not found")
    return row["payload"]
