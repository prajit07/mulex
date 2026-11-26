"""Identity-document reading: RapidOCR (PP-OCR, ONNX Runtime) plus field parsing.

Only masked identifiers leave this module for storage or display; the full
number is used transiently for validation and keyed hashing.
"""

import re
from functools import lru_cache
from threading import Lock

AADHAAR = re.compile(r"(?<!\d)(\d{4})\s?(\d{4})\s?(\d{4})(?!\d)")
PAN = re.compile(r"\b([A-Z]{5})([0-9]{4})([A-Z])\b")
DATE = re.compile(r"\b(\d{2})[/\-.](\d{2})[/\-.](\d{4})\b")
YEAR = re.compile(r"\b(?:year of birth|yob)\D*(\d{4})\b", re.IGNORECASE)
NOISE = re.compile(
    r"government|india|income tax|department|aadhaar|unique|identification|"
    r"authority|card|dob|birth|male|female|address|signature|permanent|account|"
    r"number|father|specimen|test|vid\b|enrol",
    re.IGNORECASE,
)

# Verhoeff tables (UIDAI uses the Verhoeff checksum for Aadhaar numbers).
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(digits):
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _D[check][_P[i % 8][int(ch)]]
    return check == 0


def mask(doc_type, number):
    if not number:
        return None
    if doc_type == "AADHAAR":
        return "XXXX XXXX " + number[-4:]
    return number[:2] + "XXX" + "XXXX" + number[-1]


@lru_cache(maxsize=1)
def engine():
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "error"})


_ocr_lock = Lock()


def read_lines(bgr):
    """Return [(text, confidence)] in reading order."""
    with _ocr_lock:
        result = engine()(bgr)
    texts = list(getattr(result, "txts", None) or ())
    scores = list(getattr(result, "scores", None) or ())
    boxes = getattr(result, "boxes", None)
    lines = list(zip(texts, scores))
    if boxes is not None and len(boxes) == len(lines):
        order = sorted(
            range(len(lines)),
            key=lambda i: (
                round(float(min(p[1] for p in boxes[i])) / 12),
                float(min(p[0] for p in boxes[i])),
            ),
        )
        lines = [lines[i] for i in order]
    return [(str(t).strip(), float(s)) for t, s in lines if str(t).strip()]


def _name(lines, anchor_index):
    """Best name candidate: alphabetic line above the anchor, else the first such line."""

    def candidate(text):
        letters = re.sub(r"[^A-Za-z .]", "", text).strip()
        words = letters.split()
        return (
            letters
            if 2 <= len(words) <= 5
            and len(letters) >= 4
            and not NOISE.search(text)
            and letters.upper() != letters.lower()
            else None
        )

    if anchor_index is not None:
        for i in range(anchor_index - 1, max(-1, anchor_index - 4), -1):
            found = candidate(lines[i][0])
            if found:
                return found.title(), lines[i][1]
    for text, score in lines:
        found = candidate(text)
        if found:
            return found.title(), score
    return None, 0.0


def parse(lines, doc_type):
    """Extract structured fields with per-field confidence and validation."""
    fields, confidence, issues = {}, {}, []
    dob_index = None
    for i, (text, score) in enumerate(lines):
        if DATE.search(text) or YEAR.search(text):
            dob_index = i
            m = DATE.search(text)
            fields["dob"] = "/".join(m.groups()) if m else YEAR.search(text).group(1)
            confidence["dob"] = score
            break
    for text, score in lines:
        upper = text.upper()
        if re.search(r"\bFEMALE\b", upper):
            fields["gender"], confidence["gender"] = "F", score
            break
        if re.search(r"\bMALE\b", upper):
            fields["gender"], confidence["gender"] = "M", score
            break
    number = None
    if doc_type == "AADHAAR":
        for text, score in lines:
            m = AADHAAR.search(text)
            if m:
                number, confidence["id_number"] = "".join(m.groups()), score
                break
        if number and not verhoeff_valid(number):
            issues.append(
                "Aadhaar number fails the Verhoeff checksum: possible misread or tampering"
            )
    else:
        for text, score in lines:
            m = PAN.search(re.sub(r"\s", "", text.upper()))
            if m:
                number, confidence["id_number"] = "".join(m.groups()), score
                break
        if number:
            if number[3] != "P":
                issues.append("PAN is not issued to an individual (4th character)")
    if number is None:
        issues.append("Identity number not readable")
    name, name_score = _name(lines, dob_index)
    if name:
        fields["name"], confidence["name"] = name, name_score
    else:
        issues.append("Name not readable")
    if "dob" not in fields:
        issues.append("Date of birth not readable")
    required = ("id_number", "name", "dob")
    present = [k for k in required if k in confidence]
    score = sum(confidence[k] for k in present) / len(required) if present else 0.0
    if any("checksum" in i or "individual" in i for i in issues):
        score *= 0.5
    return dict(
        fields=fields,
        number=number,
        masked=mask(doc_type, number),
        confidence={k: round(v, 3) for k, v in confidence.items()},
        score=round(score, 3),
        issues=issues,
    )
