"""HTTP API for deepfake-resilient V-CIP onboarding (the prevention layer)."""

import json
import secrets
import time
from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import Field

from ..schema import Identifier, Label, Record
from ..storage import connect, dataset_text, now
from . import camera, documents, service, trust, vision

router = APIRouter(prefix="/api/datasets/{key}/onboarding", tags=["onboarding"])

REASON_CODES = {
    "APPROVE": ("DOCUMENTS_VERIFIED", "IN_PERSON_CONFIRMED", "REVIEW_CLEARED"),
    "ESCALATE": ("SENIOR_REVIEW", "FRAUD_TEAM", "DOCUMENT_RECHECK"),
    "REJECT": (
        "FACE_MISMATCH",
        "LIVENESS_FAILED",
        "DEEPFAKE_SUSPECTED",
        "DOCUMENT_INVALID",
        "IDENTITY_REUSE",
        "MULE_NETWORK_LINK",
        "VIRTUAL_CAMERA",
    ),
}


class SessionStart(Record):
    applicant: Label
    doc_type: Literal["AADHAAR", "PAN"]
    device_id: Identifier
    cameras: list[Annotated[str, Field(max_length=200)]] = Field(max_length=20)
    selected_camera: Annotated[str, Field(max_length=200)] | None = None
    automated: bool = False
    capture_mode: Literal["camera", "upload"] = "camera"
    simulated: bool = False


class OfficerDecision(Record):
    action: Literal["APPROVE", "ESCALATE", "REJECT"]
    reason_code: Annotated[str, Field(pattern=r"^[A-Z_]{3,40}$")]
    officer: Label
    note: Annotated[str, Field(min_length=4, max_length=500)]


async def read_image(upload):
    content = await upload.read(3_000_001)
    if len(content) > 3_000_000:
        raise HTTPException(413, "Image exceeds 3 MB")
    try:
        return vision.decode_image(content)
    except vision.ImageError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/status")
def status(key: str):
    with connect() as db:
        dataset_text(db, key)
    return dict(
        models=vision.model_status(),
        weights=trust.WEIGHTS,
        bands=dict(verified=90, review=70),
        reason_codes=REASON_CODES,
    )


@router.get("")
def list_sessions(key: str):
    with connect() as db:
        dataset_text(db, key)
        return service.summary(db, key) | dict(
            sessions_list=[
                service.public(r)
                for r in db.execute(
                    "SELECT * FROM onboarding_sessions WHERE dataset=? ORDER BY created DESC LIMIT 200",
                    (key,),
                )
            ]
        )


@router.get("/audit/verify")
def verify(key: str):
    with connect() as db:
        dataset_text(db, key)
        return service.verify_chain(db)


@router.get("/{session_id}")
def get_session(key: str, session_id: str):
    with connect() as db:
        return service.public(service.load(db, key, session_id), db)


@router.post("", status_code=201)
def start(key: str, body: SessionStart):
    passed, reasons, summary = camera.evaluate(
        body.cameras, body.selected_camera, body.automated, body.capture_mode
    )
    summary["simulated"] = body.simulated
    session_id = "VCIP-" + secrets.token_hex(4).upper()
    stage = "AWAITING_DOCUMENT" if passed else "REJECTED"
    decision = None
    if not passed:
        decision = trust.decide({}, camera_passed=False)
        decision["reasons"] += reasons
        decision |= dict(camera_reasons=reasons, network_flags=[], decided_at=now())
    with connect(True) as db:
        dataset_text(db, key)
        db.execute(
            """INSERT INTO onboarding_sessions(id,dataset,created,updated,stage,applicant,doc_type,device_id,camera,decision)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                key,
                now(),
                now(),
                stage,
                body.applicant,
                body.doc_type,
                body.device_id,
                json.dumps(dict(passed=passed, reasons=reasons, **summary)),
                json.dumps(decision) if decision else None,
            ),
        )
        service.audit(
            db,
            session_id,
            "SESSION_STARTED",
            dict(camera_passed=passed, reasons=reasons, **summary),
        )
        if not passed:
            service.audit(
                db,
                session_id,
                "ENGINE_DECISION",
                dict(decision="REJECTED", reasons=decision["reasons"]),
            )
        return service.public(service.load(db, key, session_id), db)


@router.post("/{session_id}/document")
async def document(key: str, session_id: str, image: UploadFile = File(...)):
    with connect() as db:
        row = service.load(db, key, session_id)
    if row["stage"] not in ("AWAITING_DOCUMENT", "AWAITING_CAPTURE"):
        raise HTTPException(409, "The document step is closed for this session")
    bgr = await read_image(image)
    started = time.perf_counter()
    try:
        lines = documents.read_lines(bgr)
        parsed = documents.parse(lines, row["doc_type"])
    except Exception:
        parsed = dict(
            fields={},
            number=None,
            masked=None,
            confidence={},
            score=None,
            issues=["OCR engine unavailable"],
        )
    faces = vision.detector().detect(bgr, threshold=0.5)
    elapsed = round((time.perf_counter() - started) * 1000)
    if not faces:
        return dict(
            accepted=False,
            reason="Upload rejected: the document photo is missing or cropped. Capture the full card.",
            ocr=dict(
                fields=parsed["fields"],
                masked=parsed["masked"],
                issues=parsed["issues"],
            ),
        )
    vector = vision.embedder().embed(vision.align(bgr, faces[0]["landmarks"]))
    dob = parsed["fields"].get("dob")
    record = dict(
        fields={k: v for k, v in parsed["fields"].items() if k != "dob"}
        | (dict(birth_year=dob[-4:]) if dob else {}),
        id_masked=parsed["masked"],
        confidence=parsed["confidence"],
        ocr_score=parsed["score"],
        issues=parsed["issues"],
        face_score=round(faces[0]["score"], 3),
        latency_ms=elapsed,
    )
    with connect(True) as db:
        service.load(db, key, session_id)
        service.store_face(db, session_id, "document", vector)
        service.update(
            db,
            session_id,
            stage="AWAITING_CAPTURE",
            document=record,
            id_hash=service.keyed_hash(parsed["number"]) if parsed["number"] else None,
            id_masked=parsed["masked"],
        )
        service.audit(
            db,
            session_id,
            "DOCUMENT_READ",
            dict(
                id_masked=parsed["masked"],
                ocr_score=parsed["score"],
                issues=parsed["issues"],
            ),
        )
    return dict(accepted=True, document=record)


@router.post("/{session_id}/capture")
async def capture(key: str, session_id: str, frame: UploadFile = File(...)):
    with connect() as db:
        row = service.load(db, key, session_id)
        reference = service.face(db, session_id, "document")
    if row["stage"] != "AWAITING_CAPTURE" or reference is None:
        raise HTTPException(409, "Upload the identity document before the live capture")
    bgr = await read_image(frame)
    started = time.perf_counter()
    faces = vision.detector().detect(bgr)
    if not faces:
        return dict(
            accepted=False,
            reason="Face not detected. Look at the camera with your face fully visible.",
        )
    if len(faces) > 1:
        return dict(
            accepted=False,
            reason=f"Session paused: {len(faces)} faces in frame. Only the applicant may be visible.",
        )
    best = faces[0]
    quality = vision.quality(bgr, best["box"])
    vector = vision.embedder().embed(vision.align(bgr, best["landmarks"]))
    result = dict(
        identity_similarity=round(float(vector @ reference), 4),
        quality=quality,
        face_score=round(best["score"], 3),
    )
    for name, loader, crop in (
        ("liveness", vision.liveness_model, 1.5),
        ("deepfake", vision.deepfake_model, 1.5),
    ):
        try:
            model = loader()
            result[name] = round(
                model.probability(vision.context_crop(bgr, best["box"], crop)), 4
            )
            result[name + "_model"] = model.name
        except Exception:
            result[name] = None
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    with connect(True) as db:
        service.load(db, key, session_id)
        service.store_face(db, session_id, "live", vector)
        service.update(db, session_id, capture=result)
        service.audit(
            db,
            session_id,
            "LIVE_CAPTURE",
            {k: v for k, v in result.items() if k != "quality"}
            | dict(prompts=quality["prompts"]),
        )
    return dict(accepted=True, capture=result)


@router.post("/{session_id}/submit")
def submit(key: str, session_id: str):
    with connect(True) as db:
        row = service.load(db, key, session_id)
        if row["decision"]:
            return service.public(row, db)
        cap = service.public(row)["capture"]
        doc = service.public(row)["document"]
        if not cap or not doc:
            raise HTTPException(409, "Complete the document and live capture first")
        flags, linked = service.network_flags(
            db, key, row, service.face(db, session_id, "live")
        )
        signals = dict(
            identity_similarity=cap["identity_similarity"],
            liveness=cap.get("liveness"),
            deepfake=cap.get("deepfake"),
            ocr=doc.get("ocr_score"),
        )
        issues = [i for i in doc.get("issues", []) if "unavailable" not in i]
        prompts = list(cap["quality"]["prompts"])
        if service.public(row)["camera"].get("mode") == "upload":
            prompts.append(
                "frame uploaded from a file, so liveness of the session is unproven"
            )
        outcome = trust.decide(
            signals,
            network_flags=flags + [f"Document check: {i}" for i in issues],
            quality_prompts=prompts,
        )
        outcome |= dict(
            signals=signals,
            network_flags=flags,
            linked_accounts=linked,
            decided_at=now(),
        )
        service.update(db, session_id, decision=outcome, stage=outcome["decision"])
        service.audit(
            db,
            session_id,
            "ENGINE_DECISION",
            dict(
                decision=outcome["decision"],
                trust=outcome["trust"],
                reasons=outcome["reasons"],
            ),
        )
        return service.public(service.load(db, key, session_id), db)


@router.post("/{session_id}/decision")
def officer_decision(key: str, session_id: str, body: OfficerDecision):
    if body.reason_code not in REASON_CODES[body.action]:
        raise HTTPException(
            422, f"Reason code must be one of {', '.join(REASON_CODES[body.action])}"
        )
    with connect(True) as db:
        row = service.load(db, key, session_id)
        engine = service.public(row)["decision"]
        if not engine:
            raise HTTPException(
                409, "The Trust Engine has not decided this session yet"
            )
        if row["officer"] and row["stage"] in ("APPROVED", "REJECTED_BY_OFFICER"):
            raise HTTPException(
                409, "This session already has a final officer decision"
            )
        if body.action == "APPROVE" and engine["decision"] == "REJECTED":
            raise HTTPException(
                409, "A rejected session cannot be approved. Start a new session."
            )
        account_id = None
        if body.action == "APPROVE":
            account_id = service.open_account(db, key, row)
        stage = dict(
            APPROVE="APPROVED", ESCALATE="ESCALATED", REJECT="REJECTED_BY_OFFICER"
        )[body.action]
        record = body.model_dump() | dict(decided_at=now())
        service.update(
            db, session_id, officer=record, stage=stage, account_id=account_id
        )
        service.audit(
            db, session_id, "OFFICER_DECISION", record | dict(account_id=account_id)
        )
        return service.public(service.load(db, key, session_id), db)
