"""Camera integrity: a hard gate evaluated before a V-CIP session opens.

The browser enumerates media devices (labels are only exposed after camera
permission is granted) and reports them here. This is a first-line control:
it stops commodity virtual-camera injection (OBS, v4l2loopback, ManyCam ...)
but a fully attacker-controlled browser can misreport devices, which is why
liveness and deepfake checks still run on every frame.
"""

import re

VIRTUAL_CAMERA_PATTERNS = (
    r"\bobs\b",
    r"obs virtual",
    r"virtual",
    r"v4l2loopback",
    r"dummy video",
    r"manycam",
    r"xsplit",
    r"snap camera",
    r"splitcam",
    r"e2esoft|vcam",
    r"camtwist",
    r"mmhmm",
    r"nvidia broadcast",
    r"droidcam",
    r"\bcamo\b",
    r"iriun",
    r"epoccam",
    r"youcam",
    r"unity video capture",
    r"screen capture",
)
_PATTERN = re.compile("|".join(VIRTUAL_CAMERA_PATTERNS), re.IGNORECASE)


def is_virtual(label):
    return bool(_PATTERN.search(label or ""))


def evaluate(devices, selected_label, automated, mode="camera"):
    """Return (passed, reasons, summary) for the reported capture environment.

    In ``upload`` mode (evaluation without a webcam) no camera is opened, so
    labels are not required; the Trust Engine caps those sessions at review.
    """
    labels = [d.strip() for d in devices if d and d.strip()]
    reasons = []
    if automated:
        reasons.append("Automated browser detected (navigator.webdriver)")
    if mode == "camera" and not labels:
        reasons.append("No camera labels reported: grant camera permission and retry")
    virtual = sorted({label for label in labels if is_virtual(label)})
    if virtual:
        reasons.append(
            "Virtual camera interface present: "
            + ", ".join(virtual)
            + ". Disable it and use the device's physical camera."
        )
    if selected_label and is_virtual(selected_label) and selected_label not in virtual:
        reasons.append(f"Selected camera is a virtual interface: {selected_label}")
    summary = dict(
        cameras=len(labels),
        virtual=virtual,
        selected=selected_label or None,
        automated=bool(automated),
        mode=mode,
    )
    return (not reasons, reasons, summary)
