from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.analysis import INDICATOR_MAXIMUM, Analysis
from app.main import app
from app.schema import Dataset


def ledger(transactions=(), complaints=()):
    return dict(
        name="Independent fixture",
        accounts=[dict(id=a, name=a) for a in "ABCD"],
        transactions=list(transactions),
        complaints=list(complaints),
    )


def at(minutes):
    return datetime(2026, 11, 1, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def txn(key, sender, receiver, amount, timestamp):
    return dict(
        id=key,
        sender=sender,
        receiver=receiver,
        amount_paise=amount,
        timestamp=timestamp.isoformat(),
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MULEX_DB", str(tmp_path / "state.db"))
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def workspace(client, data):
    res = client.post("/api/datasets", json=data)
    assert res.status_code == 201, res.text
    return "/api/datasets/" + res.json()["id"]


def test_demo_graph_score_and_pdf(client):
    key = "/api/datasets/" + client.get("/api/datasets").json()[0]["id"]
    d = client.get(key + "/accounts/ACC-2847").json()
    assert (
        d["rapid"] == 30400000 and d["incoming"] == 32000000 and len(d["filings"]) == 6
    )
    assert sum(x["points"] for x in d["reasons"]) == pytest.approx(d["score"])
    assert d["score"] >= 75 and "ACC-7721" in d["cluster"]
    assert any(t["receiver"] == "ACC-9904" and t["hop"] == 3 for t in d["trail"])
    assert client.get(key + "/accounts/ACC-2847/report").content.startswith(b"%PDF-")
    assert (
        client.get("/").status_code == 200
        and client.get("/assets/desk.js").status_code == 200
    )


def test_chronology_no_reuse_and_isolation():
    t = datetime.now(UTC)
    a = Analysis(
        Dataset.model_validate(
            ledger(
                [
                    txn("one", "A", "B", 100, t),
                    txn("old", "B", "C", 200, t - timedelta(minutes=1)),
                    txn("two", "B", "C", 60, t + timedelta(minutes=1)),
                    txn("three", "B", "D", 90, t + timedelta(minutes=2)),
                    txn("late", "C", "D", 30, t + timedelta(minutes=20)),
                ]
            )
        )
    )
    assert a.features["B"]["rapid"] == 100
    assert "old" not in [r["id"] for r in a.detail("A")["trail"]]
    assert not a.peer_model


def test_atomic_import_and_csv(client):
    before = client.get("/api/datasets").json()
    invalid = ledger(
        complaints=[
            dict(
                id="c",
                category="Incident",
                account="unknown",
                timestamp=datetime.now(UTC).isoformat(),
            )
        ]
    )
    assert client.post("/api/datasets", json=invalid).status_code == 422
    assert client.get("/api/datasets").json() == before
    body = b"id,sender,receiver,amount_paise,timestamp\nT1,A,B,12000,2026-11-01T00:00:00Z\n"
    url = "/api/datasets/csv"
    res = client.post(url, files={"file": ("rows.csv", body)})
    assert res.status_code == 201, res.text
    assert client.post(url, files={"file": ("rows.csv", body)}).json() == res.json()
    assert (
        client.post(
            url, files={"file": ("rows.csv", body.replace(b"12000", b"-10"))}
        ).status_code
        == 422
    )
    assert (
        client.get("/api/datasets/" + res.json()["id"]).json()["count_transactions"]
        == 1
    )


def test_import_drops_extra_parameters_in_json_and_csv(client):
    ts = datetime.now(UTC).isoformat()
    payload = {
        "name": "Extra fields ledger",
        "source_system": "CBS-EXPORT",
        "accounts": [
            {"id": "A", "name": "Alice", "branch": "PATNA-01", "kyc_tier": "FULL"},
            {"id": "B", "name": "Bob", "device": "DEV-9", "risk_hint": 99},
        ],
        "transactions": [
            {
                "id": "TX1",
                "sender": "A",
                "receiver": "B",
                "amount_paise": 50000,
                "timestamp": ts,
                "channel": "IMPS",
                "remarks": "loan",
            }
        ],
        "complaints": [
            {
                "id": "CMP1",
                "account": "B",
                "category": "Fraud",
                "timestamp": ts,
                "transfer": "TX1",
                "reporter_phone": "+910000000000",
            }
        ],
    }
    res = client.post("/api/datasets", json=payload)
    assert res.status_code == 201, res.text
    key = res.json()["id"]
    overview = client.get(f"/api/datasets/{key}").json()
    assert overview["count_transactions"] == 1
    assert overview["count_complaints"] == 1
    evidence = client.get(f"/api/datasets/{key}/accounts/B/evidence.json").json()
    assert evidence["account"]["score"] == 10
    assert "reporter_phone" not in evidence["account"]["filings"][0]

    # JSON without a name field defaults cleanly to "Imported ledger".
    nameless = {k: v for k, v in payload.items() if k != "name"}
    nameless_res = client.post("/api/datasets", json=nameless)
    assert nameless_res.status_code == 201, nameless_res.text
    assert (
        client.get("/api/datasets/" + nameless_res.json()["id"]).json()["name"]
        == "Imported ledger"
    )

    # Pakistan/massive_dataset style JSON and CSV (account_id, device_id, txn_id, sender_id, receiver_id, amount, naive timestamp, fraud_reports).
    massive_json = {
        "accounts": [
            {
                "account_id": "acc_00000001",
                "institution": "MCB",
                "account_type": "personal",
                "opened_day": -251,
                "device_id": "dev_00018290",
                "sim_age_days": 1523,
                "cnic": "42101-2719583-9",
            },
            {
                "account_id": "acc_00000002",
                "institution": "HBL",
                "account_type": "personal",
                "opened_day": -254,
                "device_id": "dev_00018290",
            },
        ],
        "transactions": [
            {
                "txn_id": "txn_00000001",
                "sender_id": "acc_00000001",
                "receiver_id": "acc_00000002",
                "amount": 66065.96,
                "timestamp": "2026-05-01 00:00:02",
                "channel": "1ibft",
                "sender_institution": "MCB",
                "receiver_institution": "HBL",
                "is_confirmed_fraud": True,
                "reported_day": 20,
            }
        ],
        "fraud_reports": [
            {
                "report_id": "rep_000001",
                "txn_id": "txn_00000001",
                "reported_by_institution": "HBL",
                "report_day": 20,
                "outcome": "confirmed_mule",
            }
        ],
    }
    m_res = client.post("/api/datasets", json=massive_json)
    assert m_res.status_code == 201, m_res.text
    m_ov = client.get("/api/datasets/" + m_res.json()["id"]).json()
    assert m_ov["count_transactions"] == 1 and m_ov["count_complaints"] == 1
    m_acc = client.get(f"/api/datasets/{m_res.json()['id']}/accounts/acc_00000002").json()
    assert m_acc["incoming"] == 6606596 and m_acc["filings"][0]["id"] == "rep_000001"

    massive_csv = (
        b"txn_id,sender_id,receiver_id,amount,timestamp,channel,sender_institution\n"
        b"txn_1,acc_1,acc_2,1250.50,2026-05-01 00:00:02,raast,HBL\n"
    )
    mc_res = client.post("/api/datasets/csv", files={"file": ("txns.csv", massive_csv)})
    assert mc_res.status_code == 201, mc_res.text
    mc_acc = client.get(f"/api/datasets/{mc_res.json()['id']}/accounts/acc_2").json()
    assert mc_acc["incoming"] == 125050

    # Required validation still rejects bad values even when extra fields exist.
    bad_payload = {**payload, "transactions": [{**payload["transactions"][0], "amount_paise": -5}]}
    assert client.post("/api/datasets", json=bad_payload).status_code == 422

    # CSV with extra columns and reordered headers drops extra columns and validates.
    csv_body = (
        b"channel,timestamp,id,sender,receiver,amount_paise,city\n"
        b"UPI,2026-11-01T00:00:00Z,T9,X,Y,25000,Patna\n"
    )
    csv_res = client.post("/api/datasets/csv", files={"file": ("extra.csv", csv_body)})
    assert csv_res.status_code == 201, csv_res.text
    csv_ov = client.get("/api/datasets/" + csv_res.json()["id"]).json()
    assert csv_ov["count_transactions"] == 1
    assert {a["id"] for a in csv_ov["accounts"]} == {"X", "Y"}


def test_deviation_uses_own_preceding_history():
    at = datetime.now(UTC)
    rows = [txn(f"n{i}", "A", "B", 1000, at + timedelta(days=i)) for i in range(5)]
    rows.append(txn("surge", "A", "B", 20000, at + timedelta(days=5)))
    scores = Analysis(Dataset.model_validate(ledger(rows))).scores["A"]
    assert scores["latest_amount_ratio"] == 20
    assert any(
        r["indicator"] == "Departure from account history" for r in scores["reasons"]
    )


def test_local_writes_require_matching_browser_origin(client):
    result = client.post(
        "/api/datasets", json=ledger(), headers={"Origin": "https://other.example"}
    )
    assert result.status_code == 403


def test_new_complaint_rescores_accounts_and_links_transaction(client):
    ts = datetime.now(UTC)
    key = workspace(client, ledger([txn("ticket", "A", "B", 12000, ts)]))
    filing = {
        "id": "case1",
        "account": "B",
        "timestamp": ts.isoformat(),
        "category": "Disputed payment",
        "transfer": "ticket",
    }
    assert (
        client.post(key + "/complaints", json={**filing, "account": "C"}).status_code
        == 422
    )
    assert client.post(key + "/complaints", json=filing).status_code == 200
    assert client.post(key + "/complaints", json=filing).status_code == 200
    assert client.get(key + "/accounts/B").json()["score"] == 10
    assert client.get(key + "/accounts/A").json()["score"] == 3
    assert client.get(key).json()["count_complaints"] == 1


def test_case_revision_and_evidence_snapshot(client):
    key = workspace(client, ledger())
    route = key + "/accounts/A/case"
    review = dict(
        event_id="review-1",
        expected_revision=0,
        analyst="Analyst 1",
        status="Investigating",
        note="Reviewing the origin of credits.",
    )
    assert client.post(route, json=review).json() == {"revision": 1}
    assert client.post(route, json=review).json() == {"revision": 1}
    assert (
        client.post(route, json={**review, "event_id": "review-2"}).status_code == 409
    )
    assert client.post(
        route,
        json={
            **review,
            "event_id": "review-2",
            "expected_revision": 1,
            "status": "Closed",
        },
    ).json() == {"revision": 2}
    evidence = client.get(key + "/accounts/A/evidence.json").json()
    assert [e["status"] for e in evidence["case_history"]] == [
        "Closed",
        "Investigating",
    ]
    assert len(evidence["ledger_sha256"]) == 64
    assert client.get(key + "/accounts/B/evidence.json").json()["case_history"] == []
    assert (
        next(a for a in client.get(key).json()["accounts"] if a["id"] == "A")[
            "case_status"
        ]
        == "Closed"
    )


def test_same_time_is_not_a_chronological_match():
    t = datetime.now(UTC)
    independent = ledger([txn("a", "A", "B", 7000, t), txn("z", "B", "C", 7000, t)])
    assert Analysis(Dataset.model_validate(independent)).features["B"]["rapid"] == 0


def test_local_readiness_and_request_limits(client):
    result = client.get("/api/health")
    models = result.json()["models"]
    assert result.json()["status"] == "ready"
    assert "unavailable" not in models.values()
    assert client.get("/", headers={"Host": "unknown.example"}).status_code == 400
    assert client.post("/api/datasets", content=b" " * 8_000_001).status_code == 413
    assert client.get("/api/datasets").headers["cache-control"] == "no-store"


def test_layering_chain_role_and_source_trace():
    a = Analysis(
        Dataset.model_validate(
            ledger(
                [
                    txn("in1", "A", "B", 10000, at(0)),
                    txn("in2", "D", "B", 10000, at(1)),
                    txn("hop1", "B", "C", 19000, at(5)),
                    txn("hop2", "C", "D", 18000, at(10)),
                ]
            )
        )
    )
    reasons = {r["indicator"]: r for r in a.scores["B"]["reasons"]}
    assert "Layering chain" in reasons and "C" in reasons["Layering chain"]["evidence"]
    assert a.scores["C"]["role"] == "Layering intermediary"
    source, truncated = a.sources("C")
    assert not truncated
    assert [(h["id"], h["hop"]) for h in source] == [
        ("hop1", 1),
        ("in2", 2),
        ("in1", 2),
    ]
    # Credits after the onward hop are not sources of it.
    assert "hop2" not in {h["id"] for h in a.sources("C")[0]}


def test_circular_flow_requires_time_order():
    ordered = Analysis(
        Dataset.model_validate(
            ledger(
                [
                    txn("r1", "A", "B", 5000, at(0)),
                    txn("r2", "B", "C", 4900, at(5)),
                    txn("r3", "C", "A", 4800, at(9)),
                ]
            )
        )
    )
    assert ordered.cycles[0]["transfers"] == ["r1", "r2", "r3"]
    assert any(
        r["indicator"] == "Circular fund flow" for r in ordered.scores["B"]["reasons"]
    )
    rotated = Analysis(
        Dataset.model_validate(
            ledger(
                [
                    txn("r1", "A", "B", 5000, at(10)),
                    txn("r2", "B", "C", 4900, at(15)),
                    txn("r3", "C", "A", 4800, at(0)),
                ]
            )
        )
    )
    # The ring completes only when traversal starts at C; every rotation is tried.
    assert rotated.cycles[0]["transfers"] == ["r3", "r1", "r2"]
    reversed_order = Analysis(
        Dataset.model_validate(
            ledger(
                [
                    txn("r1", "A", "B", 5000, at(10)),
                    txn("r2", "B", "C", 4900, at(5)),
                    txn("r3", "C", "A", 4800, at(0)),
                ]
            )
        )
    )
    assert reversed_order.cycles == []
    slow = Analysis(
        Dataset.model_validate(
            ledger(
                [
                    txn("r1", "A", "B", 5000, at(0)),
                    txn("r2", "B", "C", 4900, at(60 * 24 * 2)),
                    txn("r3", "C", "A", 4800, at(60 * 24 * 4)),
                ]
            )
        )
    )
    assert slow.cycles == []


def test_scores_never_exceed_one_hundred():
    burst = [txn(f"v{i}", "A", "B", 1000, at(i)) for i in range(12)]
    burst += [txn(f"o{i}", "B", "C", 1000, at(i + 0.5)) for i in range(12)]
    filings = [
        dict(id=f"c{i}", account="B", category="Fraud", timestamp=at(30).isoformat())
        for i in range(4)
    ]
    a = Analysis(Dataset.model_validate(ledger(burst, filings)))
    assert all(0 <= s["score"] <= 100 for s in a.scores.values())
    assert sum(INDICATOR_MAXIMUM.values()) == 100
    for s in a.scores.values():
        assert all(r["points"] <= r["maximum"] for r in s["reasons"])


def test_overview_correlates_incidents_networks_and_alerts(client):
    key = "/api/datasets/" + client.get("/api/datasets").json()[0]["id"]
    o = client.get(key).json()
    incident = o["incidents"][0]
    assert incident["reported_accounts"] == ["ACC-2847", "ACC-5530"]
    assert len(incident["complaints"]) == 8
    assert {"account": "ACC-1029", "payers": ["ACC-5530", "ACC-7721"]} in incident[
        "common_beneficiaries"
    ]
    assert [n["members"] for n in o["networks"]] == [
        ["ACC-2847", "ACC-7721"],
        ["ACC-4410", "ACC-5530"],
    ]
    assert o["networks"][0]["devices"] == ["DEVICE-X17"]
    assert set(o["cycles"][0]["members"]) == {
        "ACC-1029",
        "ACC-9904",
        "ACC-4410",
        "ACC-5530",
    }
    kinds = {a["kind"] for a in o["alerts"]}
    assert {"Account risk", "Correlated complaints", "Circular flow"} <= kinds
    assert o["alerts"][0]["severity"] == "Critical"
    d = client.get(key + "/accounts/ACC-5530").json()
    assert d["incident"]["id"] == incident["id"] and d["role"] == "Collector"
    assert {h["sender"] for h in d["source_trail"] if h["hop"] == 1} >= {
        "ACC-VIC-7",
        "ACC-VIC-8",
        "ACC-4410",
    }
    evidence = client.get(key + "/accounts/ACC-5530/evidence.json").json()
    assert evidence["account"]["incident"]["complaints"] == incident["complaints"]
    referral = client.get(key + f"/incidents/{incident['id']}/evidence.json").json()
    assert {a["id"] for a in referral["accounts"]} == set(incident["accounts"])
    assert len(referral["complaints"]) == 8 and len(referral["ledger_sha256"]) == 64
    assert all(
        t["sender"] in incident["accounts"] or t["receiver"] in incident["accounts"]
        for t in referral["transfers"]
    )
    pdf = client.get(key + f"/incidents/{incident['id']}/report")
    assert pdf.content.startswith(b"%PDF-")
    assert client.get(key + "/incidents/INC-999/report").status_code == 404
