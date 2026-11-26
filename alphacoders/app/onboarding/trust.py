"""Trust Engine: deterministic, auditable fusion of the V-CIP signals.

    trust = 100 * (0.45 * identity + 0.30 * liveness
                   + 0.20 * (1 - deepfake) + 0.05 * ocr)

Bands: trust >= 90 VERIFIED, 70-89.99 MANUAL_REVIEW, < 70 REJECTED.
Camera integrity is a hard gate, not a weight. Overrides below can only make
a decision stricter; every override is recorded as a reason.
"""

WEIGHTS = {"identity": 0.45, "liveness": 0.30, "deepfake": 0.20, "ocr": 0.05}
VERIFIED, REVIEW, REJECTED = "VERIFIED", "MANUAL_REVIEW", "REJECTED"
RANK = {VERIFIED: 0, REVIEW: 1, REJECTED: 2}

# ArcFace cosine similarity: the report's operating threshold is ~0.40.
MATCH_THRESHOLD = 0.40
CLEAR_MISMATCH = 0.30
LIVE_THRESHOLD = 0.50
DEEPFAKE_REVIEW = 0.60
DEEPFAKE_REJECT = 0.85


def identity_score(similarity):
    """Map cosine similarity to 0-1: 0.20 -> 0, 0.40 -> 0.5, >= 0.60 -> 1."""
    return min(1.0, max(0.0, (similarity - 0.20) / 0.40))


def band(trust):
    return VERIFIED if trust >= 90 else REVIEW if trust >= 70 else REJECTED


def decide(signals, camera_passed=True, network_flags=(), quality_prompts=()):
    """Fuse signals into a decision.

    ``signals`` maps identity_similarity, liveness, deepfake and ocr to floats,
    or None when that model or input was unavailable.
    """
    if not camera_passed:
        return dict(
            decision=REJECTED,
            trust=None,
            components={},
            reasons=["Camera integrity gate failed before the session opened"],
        )
    reasons, caps = [], []

    def cap(level, reason):
        caps.append(level)
        reasons.append(reason)

    similarity = signals.get("identity_similarity")
    values = {
        "identity": None if similarity is None else identity_score(similarity),
        "liveness": signals.get("liveness"),
        "deepfake": None
        if signals.get("deepfake") is None
        else 1.0 - signals["deepfake"],
        "ocr": signals.get("ocr"),
    }
    components = {
        k: None if v is None else round(100 * WEIGHTS[k] * v, 2)
        for k, v in values.items()
    }
    trust = round(sum(c for c in components.values() if c is not None), 2)
    for key, label in (
        ("identity", "Face match (ArcFace)"),
        ("liveness", "Liveness (MiniFASNet)"),
        ("deepfake", "Deepfake screening"),
        ("ocr", "Document OCR"),
    ):
        if values[key] is None:
            cap(REVIEW, f"{label} unavailable: cannot auto-verify")
    live = signals.get("liveness")
    if live is not None and live < LIVE_THRESHOLD:
        cap(REJECTED, f"Presentation attack suspected: liveness {live:.2f} < 0.50")
    fake = signals.get("deepfake")
    if fake is not None and fake >= DEEPFAKE_REJECT:
        cap(REJECTED, f"Synthetic face suspected: deepfake {fake:.2f} >= 0.85")
    elif fake is not None and fake >= DEEPFAKE_REVIEW:
        cap(REVIEW, f"Possible synthetic face: deepfake {fake:.2f} >= 0.60")
    if similarity is not None and similarity < CLEAR_MISMATCH:
        cap(REJECTED, f"Face does not match the document: similarity {similarity:.2f}")
    elif similarity is not None and similarity < MATCH_THRESHOLD:
        cap(REVIEW, f"Weak face match: similarity {similarity:.2f} < 0.40")
    for prompt in quality_prompts:
        cap(REVIEW, f"Reduced-confidence capture: {prompt}")
    for flag in network_flags:
        cap(REVIEW, flag)
    decision = band(trust)
    if decision != VERIFIED or not caps:
        reasons.insert(0, f"Trust {trust:.1f} falls in the {decision} band")
    for level in caps:
        if RANK[level] > RANK[decision]:
            decision = level
    return dict(decision=decision, trust=trust, components=components, reasons=reasons)
