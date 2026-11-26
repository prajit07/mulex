# Bundled models

All inference runs locally on ONNX Runtime (CPU). No model calls an external service.

| File | Role in V-CIP onboarding | Source | Licence | Size |
| --- | --- | --- | --- | --- |
| `retinaface/retinaface_mnet025_int8.onnx` | Face detection and five-point landmarks (document photo and every live frame) | [amd/retinaface](https://huggingface.co/amd/retinaface): RetinaFace, MobileNet-0.25 backbone, trained on WIDER FACE, 640×608 input, INT8 | Apache-2.0 | 1.8 MB |
| `arcface/w600k_mbf.onnx` | Face identity: 512-D ArcFace embedding, cosine similarity between the document and live faces | [InsightFace](https://github.com/deepinsight/insightface) `buffalo_sc` pack: ArcFace loss, MobileFaceNet backbone, WebFace600K | Non-commercial research use only (InsightFace model licence) | 13.6 MB |
| `minifasnet/minifasnet.onnx` | Passive liveness (real vs print, screen or replay), 128×128, logits `[real, spoof]` | MiniFASNet v2, trained by the team on CelebA-Spoof (97.8–98.2% reported on the team's held-out split) | Apache-2.0 (Silent-Face-Anti-Spoofing derivative) | 0.6 MB |
| `deepfake/communityforensics_vit_int8.onnx` | Deepfake / generated-face screening, logits `[real, fake]` | [CommunityForensics-DeepfakeDet-ViT](https://huggingface.co/onnx-community/CommunityForensics-DeepfakeDet-ViT-ONNX), trained on 2.7M images from 4,803 generators ([paper](https://arxiv.org/abs/2411.04125)), INT8 | MIT | 37 MB |
| `deepfake/deepfake_freq_model.onnx` | The team's Tiny CNN + ViT frequency-domain detector (graph only) | Team model | Team | 22 KB |
| bundled in the `rapidocr` wheel | Document text detection and recognition (PP-OCRv6 small) | [RapidOCR](https://github.com/RapidAI/RapidOCR) | Apache-2.0 | 31 MB |

## Notes for evaluators

- **Team deepfake model.** `deepfake_freq_model.onnx` stores its weights in an external `deepfake_freq_model.onnx.data` file that is not in this repository. Place that file beside the graph and restart: MULEX then uses the team model automatically, otherwise it uses the public CommunityForensics ViT. `GET /api/health` reports which one is active.
- **Measured behaviour, not published benchmarks.** On a small local check with public-domain portraits, ArcFace scored 0.56–0.61 for the same person and 0.06–0.16 for different people, and MiniFASNet scored genuine portraits 0.87–1.0 live. The public deepfake detector separated StyleGAN faces (0.66–0.77) from genuine photographs (0.35–0.69) only weakly. The Trust Engine therefore uses it as a 20% signal: a score of at least 0.60 sends a session to manual review, and only a score of at least 0.85 rejects. None of these models has been validated on V-CIP production data.
- **Licence.** The InsightFace ArcFace weights are licensed for non-commercial research only. A production deployment would swap in a commercially licensed face-recognition model of the same 112×112 ArcFace interface.
