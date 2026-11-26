"""Append-only investigation records scoped to a dataset and account."""

from fastapi import HTTPException

from .storage import now


def history(db, key, account):
    rows = db.execute(
        """SELECT event_id, revision, analyst, status, note, created
           FROM case_events WHERE dataset=? AND account=?
           ORDER BY revision DESC""",
        (key, account),
    )
    return [dict(row) for row in rows]


def statuses(db, key):
    rows = db.execute(
        """SELECT e.account, e.status FROM case_events e
           JOIN (SELECT account, max(revision) AS revision FROM case_events
                 WHERE dataset=? GROUP BY account) latest
             ON latest.account=e.account AND latest.revision=e.revision
           WHERE e.dataset=?""",
        (key, key),
    )
    return {r["account"]: r["status"] for r in rows}


def append_event(db, key, account, update):
    existing = db.execute(
        """SELECT revision, analyst, status, note FROM case_events
           WHERE dataset=? AND account=? AND event_id=?""",
        (key, account, update.event_id),
    ).fetchone()
    if existing:
        if tuple(existing) == (
            update.expected_revision + 1,
            update.analyst,
            update.status,
            update.note,
        ):
            return {"revision": existing["revision"]}
        raise HTTPException(409, "Case event ID belongs to another update")
    latest = db.execute(
        "SELECT coalesce(max(revision),0) FROM case_events WHERE dataset=? AND account=?",
        (key, account),
    ).fetchone()[0]
    if update.expected_revision != latest:
        raise HTTPException(
            409, "The case has changed. Reopen its file before saving your review."
        )
    db.execute(
        """INSERT INTO case_events(dataset,account,revision,event_id,analyst,status,note,created)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            key,
            account,
            latest + 1,
            update.event_id,
            update.analyst,
            update.status,
            update.note,
            now(),
        ),
    )
    return {"revision": latest + 1}
