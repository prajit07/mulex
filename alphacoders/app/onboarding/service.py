"""V-CIP onboarding sessions: storage, mule-network checks and signed audit trail."""

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from ..analysis import analyze
from ..schema import Account, Dataset
from ..storage import ROOT, dataset_text, now

FACE_REUSE = 0.60
REVIEW_RISK = 55


def setup_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS onboarding_sessions (
        id TEXT PRIMARY KEY, dataset TEXT NOT NULL REFERENCES datasets(id),
        created TEXT NOT NULL, updated TEXT NOT NULL, stage TEXT NOT NULL,
        applicant TEXT NOT NULL, doc_type TEXT NOT NULL, device_id TEXT NOT NULL,
        id_hash TEXT, id_masked TEXT, camera TEXT NOT NULL, document TEXT,
        capture TEXT, decision TEXT, officer TEXT, account_id TEXT)""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS onboarding_dataset ON onboarding_sessions(dataset, created)"
    )
    db.execute("""CREATE TABLE IF NOT EXISTS onboarding_faces (
        session TEXT NOT NULL REFERENCES onboarding_sessions(id),
        kind TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(session, kind))""")
    db.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
        event TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL,
        prev_hash TEXT NOT NULL, hash TEXT NOT NULL, signature TEXT NOT NULL)""")


def _key():
    db_path = Path(os.environ.get("MULEX_DB", ROOT / "data/workbench.db"))
    path = db_path.with_name(db_path.stem + ".audit.key")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


def keyed_hash(value):
    return hmac.new(_key(), value.encode(), hashlib.sha256).hexdigest()


def audit(db, session, event, payload):
    """Append a hash-chained, HMAC-signed audit record."""
    prev = db.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = prev["hash"] if prev else "0" * 64
    created = now()
    body = json.dumps(
        dict(session=session, event=event, payload=payload, created=created),
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256((prev_hash + body).encode()).hexdigest()
    signature = hmac.new(_key(), digest.encode(), hashlib.sha256).hexdigest()
    db.execute(
        "INSERT INTO audit_log(session,event,payload,created,prev_hash,hash,signature) VALUES (?,?,?,?,?,?,?)",
        (
            session,
            event,
            json.dumps(payload, sort_keys=True),
            created,
            prev_hash,
            digest,
            signature,
        ),
    )


def verify_chain(db):
    prev_hash, count = "0" * 64, 0
    for row in db.execute("SELECT * FROM audit_log ORDER BY seq"):
        body = json.dumps(
            dict(
                session=row["session"],
                event=row["event"],
                payload=json.loads(row["payload"]),
                created=row["created"],
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256((prev_hash + body).encode()).hexdigest()
        expected = hmac.new(_key(), digest.encode(), hashlib.sha256).hexdigest()
        if (
            row["prev_hash"] != prev_hash
            or row["hash"] != digest
            or not hmac.compare_digest(row["signature"], expected)
        ):
            return dict(valid=False, records=count, broken_at=row["seq"])
        prev_hash, count = digest, count + 1
    return dict(valid=True, records=count, broken_at=None)


def load(db, key, session_id):
    row = db.execute(
        "SELECT * FROM onboarding_sessions WHERE id=? AND dataset=?", (session_id, key)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Onboarding session not found")
    return row


def public(row, db=None):
    out = dict(row)
    for field in ("camera", "document", "capture", "decision", "officer"):
        out[field] = json.loads(out[field]) if out[field] else None
    out.pop("id_hash", None)
    if db is not None:
        out["audit"] = [
            dict(
                seq=r["seq"],
                event=r["event"],
                created=r["created"],
                hash=r["hash"][:16],
                payload=json.loads(r["payload"]),
            )
            for r in db.execute(
                "SELECT * FROM audit_log WHERE session=? ORDER BY seq", (row["id"],)
            )
        ]
    return out


def update(db, session_id, **fields):
    fields["updated"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    values = [
        json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()
    ]
    db.execute(
        f"UPDATE onboarding_sessions SET {cols} WHERE id=?", (*values, session_id)
    )


def store_face(db, session_id, kind, vector):
    db.execute(
        "INSERT OR REPLACE INTO onboarding_faces(session,kind,vector) VALUES (?,?,?)",
        (session_id, kind, np.asarray(vector, dtype=np.float32).tobytes()),
    )


def face(db, session_id, kind):
    row = db.execute(
        "SELECT vector FROM onboarding_faces WHERE session=? AND kind=?",
        (session_id, kind),
    ).fetchone()
    return np.frombuffer(row["vector"], dtype=np.float32) if row else None


def network_flags(db, key, row, live_vector):
    """Mule-entry intelligence linking onboarding to the detection graph."""
    flags = []
    if row["id_hash"]:
        for other in db.execute(
            "SELECT id, stage FROM onboarding_sessions WHERE dataset=? AND id_hash=? AND id<>?",
            (key, row["id_hash"], row["id"]),
        ):
            flags.append(
                f"Identity document already used in onboarding session {other['id']} ({other['stage']})"
            )
    if live_vector is not None:
        for other in db.execute(
            """SELECT s.id, s.id_hash, f.vector FROM onboarding_faces f
               JOIN onboarding_sessions s ON s.id=f.session
               WHERE s.dataset=? AND f.kind='live' AND s.id<>?""",
            (key, row["id"]),
        ):
            if other["id_hash"] == row["id_hash"]:
                continue
            similarity = float(
                np.frombuffer(other["vector"], dtype=np.float32) @ live_vector
            )
            if similarity >= FACE_REUSE:
                flags.append(
                    f"Same face already enrolled under another identity in session {other['id']} (similarity {similarity:.2f})"
                )
    analysis = analyze(dataset_text(db, key))
    linked = sorted(
        (a.id for a in analysis.data.accounts if a.device == row["device_id"]),
        key=lambda acc: -analysis.scores[acc]["score"],
    )
    risky = [a for a in linked if analysis.scores[a]["score"] >= REVIEW_RISK]
    if risky:
        top = analysis.scores[risky[0]]
        flags.append(
            f"Device {row['device_id']} is linked to {top['level'].lower()}-risk account {top['id']} (score {top['score']})"
        )
    prior = db.execute(
        """SELECT COUNT(DISTINCT id_hash) FROM onboarding_sessions
           WHERE dataset=? AND device_id=? AND id<>? AND id_hash IS NOT NULL AND id_hash<>?""",
        (key, row["device_id"], row["id"], row["id_hash"] or ""),
    ).fetchone()[0]
    if prior >= 1:
        flags.append(
            f"Device {row['device_id']} was used to onboard {prior} other identit{'y' if prior == 1 else 'ies'}"
        )
    return flags, linked


def open_account(db, key, row):
    """Create the customer record in the dataset (the CBS write in production)."""
    data = Dataset.model_validate_json(dataset_text(db, key))
    account_id = "ACC-ONB-" + row["id"][-6:].upper()
    if any(a.id == account_id for a in data.accounts):
        return account_id
    if len(data.accounts) >= 3000:
        raise HTTPException(409, "Dataset has reached 3,000 accounts")
    account = Account(id=account_id, name=row["applicant"], device=row["device_id"])
    updated = data.model_copy(update={"accounts": [*data.accounts, account]})
    db.execute(
        "UPDATE datasets SET payload=? WHERE id=?", (updated.model_dump_json(), key)
    )
    return account_id


def summary(db, key):
    rows = [
        public(r)
        for r in db.execute(
            "SELECT * FROM onboarding_sessions WHERE dataset=? ORDER BY created DESC",
            (key,),
        )
    ]
    decided = [r for r in rows if r["decision"]]
    count = lambda pred: sum(1 for r in rows if pred(r))  # noqa: E731
    return dict(
        sessions=len(rows),
        blocked=count(lambda r: (r["decision"] or {}).get("decision") == "REJECTED"),
        review=count(lambda r: r["stage"] == "MANUAL_REVIEW"),
        verified=count(lambda r: (r["decision"] or {}).get("decision") == "VERIFIED"),
        opened=count(lambda r: bool(r["account_id"])),
        network_hits=count(lambda r: bool((r["decision"] or {}).get("network_flags"))),
        recent=[
            dict(
                id=r["id"],
                applicant=r["applicant"],
                stage=r["stage"],
                decision=(r["decision"] or {}).get("decision"),
                trust=(r["decision"] or {}).get("trust"),
                reasons=(r["decision"] or {}).get("reasons", [])[:3],
                network_flags=(r["decision"] or {}).get("network_flags", []),
                created=r["created"],
                account_id=r["account_id"],
            )
            for r in rows[:8]
        ],
        decided=len(decided),
    )
