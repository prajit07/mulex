"""Onboarding (V-CIP) prevention layer: Trust Engine, gates, documents and API flow."""

import io
import sqlite3

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.onboarding import camera, documents, trust, vision

VALID_AADHAAR = "491837265126"  # synthetic number with a valid Verhoeff digit


def png(color, size=(64, 64)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------- Trust Engine
def test_trust_formula_and_bands():
    assert sum(trust.WEIGHTS.values()) == pytest.approx(1.0)
    best = trust.decide(
        dict(identity_similarity=0.7, liveness=1.0, deepfake=0.0, ocr=1.0)
    )
    assert best["trust"] == 100 and best["decision"] == "VERIFIED"
    mid = trust.decide(
        dict(identity_similarity=0.5, liveness=0.9, deepfake=0.2, ocr=0.8)
    )
    expected = 100 * (0.45 * 0.75 + 0.30 * 0.9 + 0.20 * 0.8 + 0.05 * 0.8)
    assert mid["trust"] == pytest.approx(expected, abs=0.01)
    assert mid["decision"] == "MANUAL_REVIEW"
    assert trust.band(89.99) == "MANUAL_REVIEW" and trust.band(69.99) == "REJECTED"


@pytest.mark.parametrize(
    "signals, decision, fragment",
    [
        (dict(liveness=0.3), "REJECTED", "Presentation attack"),
        (dict(deepfake=0.9), "REJECTED", "Synthetic face suspected"),
        (dict(deepfake=0.7), "MANUAL_REVIEW", "Possible synthetic face"),
        (dict(identity_similarity=0.35), "MANUAL_REVIEW", "Weak face match"),
        (dict(identity_similarity=0.2), "REJECTED", "does not match"),
        (dict(liveness=None), "MANUAL_REVIEW", "unavailable"),
    ],
)
def test_overrides_only_make_decisions_stricter(signals, decision, fragment):
    base = dict(identity_similarity=0.7, liveness=1.0, deepfake=0.0, ocr=1.0)
    result = trust.decide(base | signals)
    assert result["decision"] == decision
    assert any(fragment in r for r in result["reasons"])


def test_camera_gate_and_network_flags():
    blocked = trust.decide({}, camera_passed=False)
    assert blocked["decision"] == "REJECTED" and blocked["trust"] is None
    clean = dict(identity_similarity=0.7, liveness=1.0, deepfake=0.0, ocr=1.0)
    flagged = trust.decide(clean, network_flags=["Device linked to ACC-1"])
    assert (
        flagged["decision"] == "MANUAL_REVIEW"
        and "Device linked to ACC-1" in flagged["reasons"]
    )
    assert (
        trust.decide(clean, quality_prompts=["Hold still"])["decision"]
        == "MANUAL_REVIEW"
    )


# --------------------------------------------------------------- Camera gate
@pytest.mark.parametrize(
    "labels, automated, mode, passed",
    [
        (["Integrated Webcam (0bda:5634)"], False, "camera", True),
        (["Integrated Webcam", "OBS Virtual Camera"], False, "camera", False),
        (["Dummy video device (0x0000)"], False, "camera", False),
        (["ManyCam Virtual Webcam"], False, "camera", False),
        (["Integrated Webcam"], True, "camera", False),
        ([], False, "camera", False),
        ([], False, "upload", True),
        (["OBS Virtual Camera"], False, "upload", False),
    ],
)
def test_camera_integrity(labels, automated, mode, passed):
    ok, reasons, summary = camera.evaluate(
        labels, labels[0] if labels else None, automated, mode
    )
    assert ok is passed and bool(reasons) is not passed and summary["mode"] == mode


# ----------------------------------------------------------------- Documents
def test_aadhaar_parse_checksum_and_masking():
    lines = [
        ("SPECIMEN IDENTITY CARD", 0.99),
        ("Rahul Kumar", 0.98),
        ("DOB: 15/08/1990", 0.97),
        ("MALE", 0.99),
        ("4918 3726 5126", 0.96),
    ]
    parsed = documents.parse(lines, "AADHAAR")
    assert parsed["fields"] == dict(name="Rahul Kumar", dob="15/08/1990", gender="M")
    assert parsed["masked"] == "XXXX XXXX 5126" and not parsed["issues"]
    assert 0.9 < parsed["score"] <= 1
    tampered = documents.parse([*lines[:4], ("4918 3726 5127", 0.96)], "AADHAAR")
    assert any("Verhoeff" in i for i in tampered["issues"])
    assert tampered["score"] < parsed["score"] / 1.5


def test_pan_parse_and_missing_fields():
    pan = documents.parse(
        [("Priya Sharma", 0.97), ("14/02/1992", 0.95), ("ABCPE1234F", 0.98)], "PAN"
    )
    assert (
        pan["number"] == "ABCPE1234F"
        and pan["masked"] == "ABXXXXXXXF"
        and not pan["issues"]
    )
    company = documents.parse(
        [("Acme Traders Ltd", 0.9), ("01/01/2001", 0.9), ("ABCCE1234F", 0.9)], "PAN"
    )
    assert any("individual" in i for i in company["issues"])
    empty = documents.parse([("GOVERNMENT OF INDIA", 0.9)], "AADHAAR")
    assert {
        "Identity number not readable",
        "Name not readable",
        "Date of birth not readable",
    } <= set(empty["issues"])


# ------------------------------------------------- API flow with stand-in models
class FakeDetector:
    """Faces are encoded by colour: green pixels mean 'two people in frame', black 'no face'."""

    def detect(self, bgr, threshold=0.6):
        b, g, r = (int(x) for x in bgr[0, 0])
        if (b, g, r) == (0, 0, 0):
            return []
        face = dict(box=[8.0, 8.0, 56.0, 56.0], score=0.99, landmarks=np.zeros((5, 2)))
        return [face, face] if g == 255 else [face]


class FakeEmbedder:
    def embed(self, bgr):
        vector = bgr[0, 0].astype(np.float32) + 1
        return vector / np.linalg.norm(vector)


class FakeClassifier:
    def __init__(self, value, name):
        self.value, self.name = value, name

    def probability(self, crop):
        return self.value


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setenv("MULEX_DB", str(tmp_path / "state.db"))
    scores = dict(liveness=0.99, deepfake=0.05)
    monkeypatch.setattr(vision, "detector", lambda: FakeDetector())
    monkeypatch.setattr(vision, "embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(vision, "align", lambda bgr, landmarks: bgr)
    monkeypatch.setattr(vision, "context_crop", lambda bgr, box, scale=1.5: bgr)
    monkeypatch.setattr(vision, "quality", lambda bgr, box: dict(prompts=[]))
    monkeypatch.setattr(
        vision,
        "liveness_model",
        lambda: FakeClassifier(scores["liveness"], "fake-live"),
    )
    monkeypatch.setattr(
        vision, "deepfake_model", lambda: FakeClassifier(scores["deepfake"], "fake-df")
    )
    number = {"value": VALID_AADHAAR}
    monkeypatch.setattr(
        documents,
        "read_lines",
        lambda bgr: [
            ("Rahul Kumar", 0.99),
            ("DOB 15/08/1990", 0.99),
            (" ".join(number["value"][i : i + 4] for i in (0, 4, 8)), 0.99),
        ],
    )
    with TestClient(app, base_url="http://localhost") as client:
        key = client.get("/api/datasets").json()[0]["id"]
        yield client, f"/api/datasets/{key}", scores, number, tmp_path / "state.db"


def run(
    client,
    base,
    live=(200, 40, 40),
    doc=(200, 40, 40),
    cameras=("Integrated Webcam",),
    device="DEV-A",
    mode="camera",
    applicant="Rahul Kumar",
):
    s = client.post(
        base + "/onboarding",
        json=dict(
            applicant=applicant,
            doc_type="AADHAAR",
            device_id=device,
            cameras=list(cameras),
            selected_camera=cameras[0] if cameras else None,
            capture_mode=mode,
        ),
    ).json()
    if s["stage"] == "REJECTED":
        return s
    sid = s["id"]
    assert client.post(
        f"{base}/onboarding/{sid}/document", files={"image": ("d.png", png(doc))}
    ).json()["accepted"]
    assert client.post(
        f"{base}/onboarding/{sid}/capture", files={"frame": ("f.png", png(live))}
    ).json()["accepted"]
    return client.post(f"{base}/onboarding/{sid}/submit").json()


def test_verified_session_opens_monitored_account(flow):
    client, base, *_ = flow
    s = run(client, base)
    assert s["decision"]["decision"] == "VERIFIED" and s["decision"]["trust"] >= 90
    officer = dict(
        action="APPROVE",
        reason_code="DOCUMENTS_VERIFIED",
        officer="Officer 1",
        note="Documents match the applicant.",
    )
    bad = client.post(
        f"{base}/onboarding/{s['id']}/decision",
        json=officer | {"reason_code": "FACE_MISMATCH"},
    )
    assert bad.status_code == 422
    done = client.post(f"{base}/onboarding/{s['id']}/decision", json=officer).json()
    account = done["account_id"]
    assert done["stage"] == "APPROVED" and account.startswith("ACC-ONB-")
    detail = client.get(f"{base}/accounts/{account}").json()
    assert detail["onboarding"]["id"] == s["id"] and detail["score"] < 35
    assert (
        client.get(f"{base}/accounts/{account}/evidence.json").json()["onboarding"][
            "officer"
        ]["action"]
        == "APPROVE"
    )
    again = client.post(f"{base}/onboarding/{s['id']}/decision", json=officer)
    assert again.status_code == 409
    assert client.get(f"{base}/accounts/{account}/report").content.startswith(b"%PDF-")


def test_virtual_camera_blocks_before_document(flow):
    client, base, *_ = flow
    s = run(client, base, cameras=("Integrated Webcam", "OBS Virtual Camera"))
    assert s["stage"] == "REJECTED" and any(
        "OBS Virtual Camera" in r for r in s["decision"]["reasons"]
    )
    late = client.post(
        f"{base}/onboarding/{s['id']}/document",
        files={"image": ("d.png", png((1, 2, 3)))},
    )
    assert late.status_code == 409


def test_imposter_rejected_and_cannot_be_approved(flow):
    client, base, *_ = flow
    s = run(client, base, live=(0, 40, 200))
    assert s["decision"]["decision"] == "REJECTED"
    officer = dict(
        action="APPROVE",
        reason_code="REVIEW_CLEARED",
        officer="O",
        note="Trying to override",
    )
    assert (
        client.post(f"{base}/onboarding/{s['id']}/decision", json=officer).status_code
        == 409
    )
    rejected = client.post(
        f"{base}/onboarding/{s['id']}/decision",
        json=officer | dict(action="REJECT", reason_code="FACE_MISMATCH"),
    ).json()
    assert rejected["stage"] == "REJECTED_BY_OFFICER" and rejected["account_id"] is None


def test_spoof_and_deepfake_signals(flow):
    client, base, scores, *_ = flow
    scores["liveness"] = 0.2
    assert run(client, base)["decision"]["decision"] == "REJECTED"
    scores.update(liveness=0.99, deepfake=0.92)
    assert run(client, base, device="DEV-B")["decision"]["decision"] == "REJECTED"


def test_mule_network_intelligence(flow):
    client, base, scores, number, _ = flow
    first = run(client, base)
    assert first["decision"]["decision"] == "VERIFIED"
    reused_id = run(client, base, device="DEV-C")
    assert any("already used" in f for f in reused_id["decision"]["network_flags"])
    number["value"] = "234123412346"
    assert documents.verhoeff_valid(number["value"])
    same_face = run(client, base, device="DEV-D", applicant="Another Name")
    assert any(
        "Same face already enrolled" in f
        for f in same_face["decision"]["network_flags"]
    )
    number["value"] = "999911112222"
    mule_device = run(
        client, base, live=(10, 220, 30), doc=(10, 220, 30), device="DEVICE-X17"
    )
    flags = mule_device["decision"]["network_flags"]
    assert (
        any("ACC-2847" in f for f in flags)
        and mule_device["decision"]["decision"] == "MANUAL_REVIEW"
    )
    assert "ACC-2847" in mule_device["decision"]["linked_accounts"]
    overview = client.get(base).json()
    assert overview["onboarding"]["network_hits"] >= 3
    assert any(a["kind"].startswith("Onboarding") for a in overview["alerts"])


def test_capture_edge_cases_and_upload_cap(flow):
    client, base, *_ = flow
    s = client.post(
        base + "/onboarding",
        json=dict(
            applicant="Edge",
            doc_type="AADHAAR",
            device_id="DEV-E",
            cameras=["Webcam"],
            selected_camera="Webcam",
        ),
    ).json()
    sid = s["id"]
    assert (
        client.post(
            f"{base}/onboarding/{sid}/capture",
            files={"frame": ("f.png", png((5, 5, 5)))},
        ).status_code
        == 409
    )
    missing = client.post(
        f"{base}/onboarding/{sid}/document", files={"image": ("d.png", png((0, 0, 0)))}
    ).json()
    assert not missing["accepted"] and "missing or cropped" in missing["reason"]
    client.post(
        f"{base}/onboarding/{sid}/document",
        files={"image": ("d.png", png((200, 40, 40)))},
    )
    none = client.post(
        f"{base}/onboarding/{sid}/capture", files={"frame": ("f.png", png((0, 0, 0)))}
    ).json()
    assert not none["accepted"] and "not detected" in none["reason"]
    two = client.post(
        f"{base}/onboarding/{sid}/capture",
        files={"frame": ("f.png", png((10, 255, 10)))},
    ).json()
    assert not two["accepted"] and "paused" in two["reason"]
    junk = client.post(
        f"{base}/onboarding/{sid}/capture", files={"frame": ("f.txt", b"not an image")}
    )
    assert junk.status_code == 422
    uploaded = run(client, base, cameras=(), mode="upload", device="DEV-U")
    assert uploaded["decision"]["decision"] == "MANUAL_REVIEW"
    assert any("uploaded from a file" in r for r in uploaded["decision"]["reasons"])


def test_audit_chain_detects_tampering(flow):
    client, base, _, _, db_path = flow
    run(client, base)
    assert client.get(base + "/onboarding/audit/verify").json()["valid"]
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE audit_log SET payload=replace(payload,'VERIFIED','REJECTED') WHERE event='ENGINE_DECISION'"
        )
    verdict = client.get(base + "/onboarding/audit/verify").json()
    assert not verdict["valid"] and verdict["broken_at"] is not None


# ------------------------------------------------------------- Real models
def test_bundled_models_run_on_cpu():
    assert "unavailable" not in vision.model_status().values()
    blank = np.full((480, 640, 3), 230, dtype=np.uint8)
    assert vision.detector().detect(blank) == []
    vector = vision.embedder().embed(
        np.random.default_rng(0).integers(0, 255, (112, 112, 3), dtype=np.uint8)
    )
    assert vector.shape == (512,) and abs(float(np.linalg.norm(vector)) - 1) < 1e-4
    crop = np.random.default_rng(1).integers(0, 255, (160, 160, 3), dtype=np.uint8)
    for model in (vision.liveness_model(), vision.deepfake_model()):
        assert 0.0 <= model.probability(crop) <= 1.0
    card = np.full((200, 900, 3), 255, dtype=np.uint8)
    cv2.putText(
        card, "4918 3726 5126", (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (0, 0, 0), 5
    )
    text = " ".join(t for t, _ in documents.read_lines(card))
    assert "4918" in text and "5126" in text


@pytest.mark.parametrize(
    "case, expected",
    [
        ("good", []),
        ("dark", ["Improve lighting"]),
        ("blurred", ["Hold still"]),
        ("small", ["Move closer"]),
        ("off_centre", ["Centre your face"]),
    ],
)
def test_capture_quality_prompts(case, expected):
    rng = np.random.default_rng(3)
    frame = rng.integers(90, 200, (480, 640, 3), dtype=np.uint8)  # textured, mid-grey
    box = [220, 140, 420, 380]
    if case == "dark":
        frame = (frame * 0.2).astype(np.uint8)
    if case == "blurred":
        frame = cv2.GaussianBlur(frame, (0, 0), 12)
    if case == "small":
        box = [300, 220, 360, 280]
    if case == "off_centre":
        box = [10, 10, 210, 250]
    prompts = vision.quality(frame, box)["prompts"]
    assert all(any(e in p for p in prompts) for e in expected)
    if not expected:
        assert prompts == []
