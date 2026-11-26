import csv
import io
from contextlib import asynccontextmanager
from hashlib import sha256

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import cases
from .analysis import analyze
from .limits import RequestSizeLimit
from .onboarding import service as onboarding
from .onboarding.routes import router as onboarding_router
from .onboarding.vision import model_status
from .reports import incident_report, report
from .schema import CaseUpdate, Complaint, Dataset, Transfer
from .storage import ROOT, connect, dataset_text, now, put_dataset, setup


@asynccontextmanager
async def lifespan(app):
    setup()
    with connect(write=True) as db:
        onboarding.setup_tables(db)
    yield


app = FastAPI(title="MULEX investigation workbench", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestSizeLimit)
app.include_router(onboarding_router)
app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")


@app.middleware("http")
async def protect_local_writes(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
        if origin != f"{request.url.scheme}://{request.url.netloc}":
            return Response("Use the workbench origin for writes", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; media-src 'self' blob:; img-src 'self' blob: data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'"
        )
    return response


app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False
)


@app.get("/api/health")
def health():
    with connect() as db:
        db.execute("SELECT id FROM datasets LIMIT 1").fetchone()
    return {"status": "ready", "version": app.version, "models": model_status()}


@app.get("/")
def home():
    return FileResponse(ROOT / "web/index.html")


@app.get("/api/datasets")
def list_datasets():
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT id,name,created FROM datasets ORDER BY created DESC"
            )
        ]


@app.get("/api/example")
def example():
    return FileResponse(ROOT / "examples/shadowflow.json", filename="shadowflow.json")


@app.post("/api/datasets", status_code=201)
def import_data(data: Dataset):
    with connect(True) as db:
        return {"id": put_dataset(db, data)}


@app.post("/api/datasets/csv", status_code=201)
def import_csv(file: UploadFile = File(...)):
    raw = file.file.read(4_000_001)
    if len(raw) > 4_000_000:
        raise HTTPException(413, "CSV exceeds 4 MB")
    try:
        reader = csv.DictReader(
            io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True
        )
        fieldnames = [f.strip() for f in (reader.fieldnames or ())]
        reader.fieldnames = fieldnames
        aliases = {
            "id": ("id", "txn_id", "transaction_id"),
            "sender": ("sender", "sender_id", "from_account", "from"),
            "receiver": ("receiver", "receiver_id", "to_account", "to"),
            "amount": ("amount_paise", "amount"),
            "timestamp": ("timestamp",),
        }
        col = {}
        for key_name, candidates in aliases.items():
            matches = [c for c in candidates if c in fieldnames]
            if not matches:
                raise ValueError(
                    "Expected columns id/txn_id, sender/sender_id, receiver/receiver_id, amount_paise/amount, timestamp"
                )
            chosen = matches[0]
            if fieldnames.count(chosen) != 1:
                raise ValueError(f"Duplicate column {chosen}")
            col[key_name] = chosen
        use_paise = col["amount"] == "amount_paise"
        rows, ids = [], set()
        for row in reader:
            amt_raw = row[col["amount"]]
            clean = {
                "id": row[col["id"]],
                "sender": row[col["sender"]],
                "receiver": row[col["receiver"]],
                "amount_paise": (
                    int(amt_raw) if use_paise else round(float(amt_raw) * 100)
                ),
                "timestamp": row[col["timestamp"]],
            }
            t = Transfer.model_validate(clean)
            rows.append(t)
            ids.update([t.sender, t.receiver])
        data = Dataset(
            name=(file.filename or "Imported ledger")[:120],
            accounts=[{"id": k, "name": k} for k in sorted(ids)],
            transactions=rows,
        )
    except (csv.Error, UnicodeError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"Invalid CSV: {str(exc)[:400]}") from exc
    return import_data(data)


def account_exists(analysis, account):
    if account not in analysis.scores:
        raise HTTPException(404, f"Unknown account {account}")


SEVERITY = {"Critical": 0, "High": 1, "Review": 2}


def build_alerts(analysis, rows, entry):
    """Risk-ranked feed combining detection findings and onboarding prevention."""
    alerts = []
    for row in rows:
        if row["level"] in ("Critical", "High"):
            top = max(row["reasons"], key=lambda r: r["points"])
            alerts.append(
                dict(
                    severity=row["level"],
                    kind="Account risk",
                    account=row["id"],
                    title=f"{row['id']} scored {row['score']}/100"
                    + (f" · {row['role']}" if row["role"] else ""),
                    detail=f"{top['indicator']}: {top['evidence']}",
                )
            )
    for incident in analysis.incidents:
        if len(incident["reported_accounts"]) >= 2:
            alerts.append(
                dict(
                    severity="High",
                    kind="Correlated complaints",
                    account=incident["reported_accounts"][0],
                    title=f"{incident['id']}: {len(incident['complaints'])} complaints "
                    f"across {len(incident['reported_accounts'])} reported accounts",
                    detail="Linked through " + ", ".join(incident["accounts"]),
                )
            )
    for ring in analysis.cycles:
        alerts.append(
            dict(
                severity="Review",
                kind="Circular flow",
                account=ring["members"][0],
                title="Funds returned to origin in " + f"{ring['hours']} h",
                detail=" → ".join(ring["members"] + ring["members"][:1]),
            )
        )
    for s in entry["recent"]:
        if s["decision"] == "REJECTED" or s["network_flags"]:
            alerts.append(
                dict(
                    severity="High" if s["network_flags"] else "Review",
                    kind="Onboarding blocked"
                    if s["decision"] == "REJECTED"
                    else "Onboarding review",
                    session=s["id"],
                    title=f"{s['id']}: {s['applicant']} · {s['decision'] or s['stage']}",
                    detail=(s["network_flags"] or s["reasons"] or ["—"])[0],
                )
            )
    alerts.sort(key=lambda a: SEVERITY[a["severity"]])
    return alerts


@app.get("/api/datasets/{key}")
def overview(key: str):
    with connect() as db:
        a = analyze(dataset_text(db, key))
        rows = sorted(a.scores.values(), key=lambda s: (-s["score"], s["id"]))
        status = cases.statuses(db, key)
        entry = onboarding.summary(db, key)
        return dict(
            name=a.data.name,
            accounts=[
                {**row, "case_status": status.get(row["id"], "New")} for row in rows
            ],
            clusters=a.clusters,
            networks=a.cluster_summary(),
            incidents=a.incidents,
            cycles=a.cycles,
            alerts=build_alerts(a, rows, entry),
            onboarding=entry,
            count_transactions=len(a.data.transactions),
            count_complaints=len(a.data.complaints),
            open_cases=sum(
                s in ("Investigating", "Escalated") for s in status.values()
            ),
            peer_model=a.peer_model,
        )


def onboarding_record(db, key, account):
    row = db.execute(
        "SELECT * FROM onboarding_sessions WHERE dataset=? AND account_id=?",
        (key, account),
    ).fetchone()
    return onboarding.public(row, db) if row else None


@app.get("/api/datasets/{key}/accounts/{account}")
def account_detail(key: str, account: str):
    with connect() as db:
        a = analyze(dataset_text(db, key))
        account_exists(a, account)
        return dict(
            **a.detail(account),
            case_history=cases.history(db, key, account),
            onboarding=onboarding_record(db, key, account),
        )


@app.post("/api/datasets/{key}/complaints")
def record_complaint(key: str, complaint: Complaint):
    with connect(True) as db:
        a = analyze(dataset_text(db, key))
        account_exists(a, complaint.account)
        existing = next((c for c in a.data.complaints if c.id == complaint.id), None)
        if existing:
            if existing == complaint:
                return existing
            raise HTTPException(409, "Complaint ID already exists")
        if len(a.data.complaints) >= 5000:
            raise HTTPException(409, "Dataset has reached 5,000 complaints")
        if complaint.transfer and not any(
            t.id == complaint.transfer for t in a.events[complaint.account]
        ):
            raise HTTPException(
                422, "The referenced transfer must involve this account"
            )
        updated = a.data.model_copy(
            update={"complaints": [*a.data.complaints, complaint]}
        )
        db.execute(
            "UPDATE datasets SET payload=? WHERE id=?", (updated.model_dump_json(), key)
        )
        return complaint


@app.post("/api/datasets/{key}/accounts/{account}/case")
def record_case(key: str, account: str, update: CaseUpdate):
    with connect(True) as db:
        account_exists(analyze(dataset_text(db, key)), account)
        return cases.append_event(db, key, account, update)


def evidence_snapshot(key, account):
    with connect() as db:
        payload = dataset_text(db, key)
        analysis = analyze(payload)
        account_exists(analysis, account)
        return {
            "dataset": analysis.data.name,
            "dataset_id": key,
            "ledger_sha256": sha256(payload.encode("utf-8")).hexdigest(),
            "generated_at": now(),
            "account": analysis.detail(account),
            "case_history": cases.history(db, key, account),
            "onboarding": onboarding_record(db, key, account),
        }


@app.get("/api/datasets/{key}/accounts/{account}/evidence.json")
def export_evidence(key: str, account: str):
    return JSONResponse(
        evidence_snapshot(key, account),
        headers={"Content-Disposition": f'attachment; filename="MULEX-{account}.json"'},
    )


@app.get("/api/datasets/{key}/accounts/{account}/report")
def export(key: str, account: str):
    pdf = report(evidence_snapshot(key, account))
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="MULEX-{account}.pdf"'},
    )


def incident_snapshot(key, incident_id):
    """Law-enforcement referral pack for one correlated incident."""
    with connect() as db:
        payload = dataset_text(db, key)
        a = analyze(payload)
        incident = next((i for i in a.incidents if i["id"] == incident_id), None)
        if not incident:
            raise HTTPException(404, f"Unknown incident {incident_id}")
        members = set(incident["accounts"])
        complaints = [
            c.model_dump(mode="json")
            for c in a.data.complaints
            if c.id in incident["complaints"]
        ]
        transfers = [
            t.model_dump(mode="json")
            for t in sorted(a.data.transactions, key=lambda t: (t.timestamp, t.id))
            if t.sender in members or t.receiver in members
        ]
        status = cases.statuses(db, key)
        return {
            "dataset": a.data.name,
            "dataset_id": key,
            "ledger_sha256": sha256(payload.encode("utf-8")).hexdigest(),
            "generated_at": now(),
            "incident": incident,
            "accounts": [
                {
                    **{
                        k: a.scores[m][k]
                        for k in (
                            "id",
                            "name",
                            "score",
                            "level",
                            "role",
                            "incoming",
                            "outgoing",
                            "rapid",
                            "reasons",
                        )
                    },
                    "case_status": status.get(m, "New"),
                }
                for m in sorted(members, key=lambda m: -a.scores[m]["score"])
            ],
            "complaints": complaints,
            "transfers": transfers,
            "cycles": [c for c in a.cycles if members.intersection(c["members"])],
        }


@app.get("/api/datasets/{key}/incidents/{incident_id}/evidence.json")
def export_incident_evidence(key: str, incident_id: str):
    return JSONResponse(
        incident_snapshot(key, incident_id),
        headers={
            "Content-Disposition": f'attachment; filename="MULEX-{incident_id}.json"'
        },
    )


@app.get("/api/datasets/{key}/incidents/{incident_id}/report")
def export_incident(key: str, incident_id: str):
    pdf = incident_report(incident_snapshot(key, incident_id))
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="MULEX-{incident_id}.pdf"'
        },
    )
