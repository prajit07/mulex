"""Face pipeline for V-CIP onboarding: detection, alignment, identity, liveness, deepfake.

All inference runs locally on ONNX Runtime (CPU). Model files live in
``onnx_models/``; see ``onnx_models/MODELS.md`` for sources and licences.
"""

import io
from functools import lru_cache
from itertools import product
from math import ceil
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps, UnidentifiedImageError

MODELS = Path(__file__).resolve().parents[2] / "onnx_models"
MAX_PIXELS = 8_000_000

# Standard ArcFace 112x112 five-point template (eyes, nose, mouth corners).
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


class ImageError(ValueError):
    """The upload is not a readable JPEG/PNG of acceptable size."""


def decode_image(content):
    """Decode a JPEG/PNG upload into a BGR array, honouring EXIF orientation."""
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ImageError("Use a JPEG or PNG image")
            if image.width * image.height > MAX_PIXELS:
                raise ImageError("Use an image of at most 8 megapixels")
            rgb = np.array(ImageOps.exif_transpose(image).convert("RGB"))
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ImageError("Use a readable JPEG or PNG image") from exc
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def session(path):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    return ort.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )


def softmax(values):
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    weights = np.exp(values - values.max())
    return weights / weights.sum()


class RetinaFace:
    """RetinaFace (MobileNet-0.25) with the biubug6 prior layout, NHWC BGR input."""

    MEAN = np.array([104.0, 117.0, 123.0], dtype=np.float32)
    MIN_SIZES = ((16, 32), (64, 128), (256, 512))
    STEPS = (8, 16, 32)

    def __init__(self, path):
        self.session = session(path)
        item = self.session.get_inputs()[0]
        self.input_name = item.name
        self.height, self.width = int(item.shape[1]), int(item.shape[2])
        self.priors = self._priors()
        self.lock = Lock()

    def _priors(self):
        anchors = []
        for step, sizes in zip(self.STEPS, self.MIN_SIZES):
            rows, cols = ceil(self.height / step), ceil(self.width / step)
            for i, j in product(range(rows), range(cols)):
                for size in sizes:
                    anchors.append(
                        [
                            (j + 0.5) * step / self.width,
                            (i + 0.5) * step / self.height,
                            size / self.width,
                            size / self.height,
                        ]
                    )
        return np.array(anchors, dtype=np.float32)

    def detect(self, bgr, threshold=0.6):
        """Return faces as dicts with box, score and five landmarks, best first."""
        h, w = bgr.shape[:2]
        scale = min(self.width / w, self.height / h)
        resized = cv2.resize(bgr, (round(w * scale), round(h * scale)))
        canvas = np.zeros((self.height, self.width, 3), dtype=np.float32)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        tensor = (canvas - self.MEAN)[None, ...]
        with self.lock:
            loc, conf, landm = self.session.run(None, {self.input_name: tensor})
        loc, conf, landm = loc[0], conf[0], landm[0]
        scores = conf[:, 1]
        if not (np.isclose(conf.sum(axis=1), 1, atol=1e-3)).all():
            scores = np.apply_along_axis(lambda r: softmax(r)[1], 1, conf)
        keep = scores >= threshold
        if not keep.any():
            return []
        p, loc, landm, scores = self.priors[keep], loc[keep], landm[keep], scores[keep]
        centres = p[:, :2] + loc[:, :2] * 0.1 * p[:, 2:]
        sizes = p[:, 2:] * np.exp(loc[:, 2:] * 0.2)
        boxes = np.hstack([centres - sizes / 2, centres + sizes / 2])
        boxes *= [self.width, self.height, self.width, self.height]
        points = np.hstack(
            [p[:, :2] + landm[:, k : k + 2] * 0.1 * p[:, 2:] for k in range(0, 10, 2)]
        ) * np.tile([self.width, self.height], 5)
        boxes /= scale
        points /= scale
        order = cv2.dnn.NMSBoxes(
            [[b[0], b[1], b[2] - b[0], b[3] - b[1]] for b in boxes.tolist()],
            scores.tolist(),
            threshold,
            0.4,
        )
        faces = []
        for i in np.array(order).reshape(-1):
            x1, y1, x2, y2 = boxes[i]
            faces.append(
                dict(
                    box=[
                        float(max(0, x1)),
                        float(max(0, y1)),
                        float(min(w, x2)),
                        float(min(h, y2)),
                    ],
                    score=float(scores[i]),
                    landmarks=points[i].reshape(5, 2),
                )
            )
        faces.sort(key=lambda f: -f["score"])
        return faces


def align(bgr, landmarks, size=112):
    """Similarity-transform the face onto the ArcFace template."""
    matrix, _ = cv2.estimateAffinePartial2D(
        np.asarray(landmarks, dtype=np.float32), ARCFACE_TEMPLATE * (size / 112)
    )
    if matrix is None:
        raise ValueError("Could not align face")
    return cv2.warpAffine(bgr, matrix, (size, size), borderValue=0)


def context_crop(bgr, box, scale=1.5):
    """Square crop around the face with surrounding context, padded at edges."""
    x1, y1, x2, y2 = box
    side = max(x2 - x1, y2 - y1) * scale
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    left, top = int(round(cx - side / 2)), int(round(cy - side / 2))
    right, bottom = left + int(round(side)), top + int(round(side))
    h, w = bgr.shape[:2]
    pad = [max(0, -top), max(0, bottom - h), max(0, -left), max(0, right - w)]
    padded = cv2.copyMakeBorder(bgr, *pad, cv2.BORDER_REFLECT_101)
    return padded[top + pad[0] : bottom + pad[0], left + pad[2] : right + pad[2]]


class ArcFace:
    """ArcFace embeddings (InsightFace w600k MobileFaceNet), 512-D, L2-normalised."""

    def __init__(self, path):
        self.session = session(path)
        self.input_name = self.session.get_inputs()[0].name
        self.lock = Lock()

    def embed(self, aligned_bgr):
        rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        tensor = ((rgb - 127.5) / 127.5).transpose(2, 0, 1)[None, ...]
        with self.lock:
            vector = self.session.run(None, {self.input_name: tensor})[0].reshape(-1)
        norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm == 0:
            raise ValueError("Invalid face embedding")
        return (vector / norm).astype(np.float32)


class Classifier:
    """Binary NCHW RGB face classifier returning the probability of one class."""

    def __init__(self, path, size, positive, mean=0.0, std=1.0, name=""):
        self.session = session(path)
        self.input_name = self.session.get_inputs()[0].name
        self.size, self.positive, self.name = size, positive, name
        self.mean, self.std = mean, std
        self.lock = Lock()

    def probability(self, crop_bgr):
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        grow = self.size > rgb.shape[0]
        rgb = cv2.resize(
            rgb,
            (self.size, self.size),
            interpolation=cv2.INTER_LANCZOS4 if grow else cv2.INTER_AREA,
        )
        tensor = ((rgb.astype(np.float32) / 255.0 - self.mean) / self.std).transpose(
            2, 0, 1
        )[None, ...]
        with self.lock:
            logits = self.session.run(None, {self.input_name: tensor})[0]
        return float(softmax(logits)[self.positive])


@lru_cache(maxsize=1)
def detector():
    return RetinaFace(MODELS / "retinaface/retinaface_mnet025_int8.onnx")


@lru_cache(maxsize=1)
def embedder():
    return ArcFace(MODELS / "arcface/w600k_mbf.onnx")


@lru_cache(maxsize=1)
def liveness_model():
    # MiniFASNet v2 (128x128, 1.5x face crop): logits are [real, spoof].
    return Classifier(
        MODELS / "minifasnet/minifasnet.onnx", 128, 0, name="MiniFASNet v2"
    )


class CommunityForensicsViT:
    """Generated-image detector trained on 4,803 generators (MIT licence).

    Preprocessing follows its model card: shorter side to 440 (bicubic),
    centre crop 384, CLIP mean and standard deviation. Logits are [real, fake].
    """

    name = "CommunityForensics ViT (public, MIT)"
    MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

    def __init__(self, path):
        self.session = session(path)
        self.input_name = self.session.get_inputs()[0].name
        self.lock = Lock()

    def probability(self, crop_bgr):
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = 440 / min(h, w)
        rgb = cv2.resize(
            rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_CUBIC
        )
        top, left = (rgb.shape[0] - 384) // 2, (rgb.shape[1] - 384) // 2
        rgb = rgb[top : top + 384, left : left + 384].astype(np.float32) / 255.0
        tensor = ((rgb - self.MEAN) / self.STD).transpose(2, 0, 1)[None, ...]
        with self.lock:
            logits = self.session.run(None, {self.input_name: tensor})[0]
        return float(softmax(logits)[1])


@lru_cache(maxsize=1)
def deepfake_model():
    """The team's Tiny CNN + ViT when its weights are supplied, else a public detector."""
    folder = MODELS / "deepfake"
    if (folder / "deepfake_freq_model.onnx.data").is_file():
        return Classifier(
            folder / "deepfake_freq_model.onnx", 224, 1, name="Tiny CNN + ViT (team)"
        )
    return CommunityForensicsViT(folder / "communityforensics_vit_int8.onnx")


def quality(bgr, box):
    """Capture-quality measurements and the user prompts they trigger."""
    h, w = bgr.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    face = cv2.cvtColor(bgr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    face_px = min(x2 - x1, y2 - y1)
    sharpness = float(cv2.Laplacian(face, cv2.CV_64F).var()) if face.size else 0.0
    brightness = float(face.mean()) if face.size else 0.0
    offset = max(abs((x1 + x2) / 2 / w - 0.5), abs((y1 + y2) / 2 / h - 0.5))
    prompts = []
    if face_px < 80:
        prompts.append("Move closer: the face is too small for reliable checks")
    if sharpness < 40:
        prompts.append("Hold still: the image is blurred")
    if brightness < 60:
        prompts.append("Improve lighting: the face is too dark")
    elif brightness > 210:
        prompts.append("Reduce glare: the face is over-exposed")
    if offset > 0.25:
        prompts.append("Centre your face in the frame")
    return dict(
        face_pixels=face_px,
        sharpness=round(sharpness, 1),
        brightness=round(brightness, 1),
        centre_offset=round(offset, 3),
        prompts=prompts,
    )


def model_status():
    status = {}
    for key, loader in (
        ("retinaface", detector),
        ("arcface", embedder),
        ("liveness", liveness_model),
        ("deepfake", deepfake_model),
    ):
        try:
            model = loader()
            status[key] = getattr(model, "name", "") or "ready"
        except Exception:
            status[key] = "unavailable"
    return status
