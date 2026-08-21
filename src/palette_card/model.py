"""Safe MobileNetV3 transfer-learning helpers.

Torch is imported lazily so palette and card features remain testable without
the optional ML stack installed. A checkpoint records class order; loading a
checkpoint with a different order is rejected instead of silently producing
wrong labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import CLASS_NAMES, IMAGE_SIZE, IMAGENET_MEAN, IMAGENET_STD

MODEL_NAME = "mobilenet_v3_small"
CHECKPOINT_VERSION = 1


def validate_image_size(image_size: int) -> int:
    """Validate the square input size stored in configs and checkpoints."""

    if isinstance(image_size, bool) or not isinstance(image_size, int) or not 32 <= image_size <= 1024:
        raise ValueError("image_size must be an integer between 32 and 1024 pixels")
    return image_size


def _torch():
    try:
        import torch
        import torch.nn as nn
        from torchvision import models, transforms
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("PyTorch and torchvision are required for training or checkpoint inference. Install the project dependencies first.") from exc
    return torch, nn, models, transforms


def select_device(requested: str = "auto") -> str:
    torch, _, _, _ = _torch()
    requested = requested.lower()
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but no CUDA device is available. Use --device cpu or auto.")
    if requested not in {"cpu", "cuda"}:
        raise ValueError("device must be auto, cpu, or cuda")
    return requested


def build_model(num_classes: int = len(CLASS_NAMES), pretrained: bool = False, allow_weight_download: bool = False):
    """Create MobileNetV3 Small and replace its classifier.

    ``allow_weight_download`` is explicit because pretrained weights are a
    multi-hundred-MB network download. If disabled, the model starts from
    random weights even when ``pretrained=True``.
    """

    torch, nn, models, _ = _torch()
    weights = None
    if pretrained:
        if not allow_weight_download:
            raise RuntimeError("Pretrained weights requested but downloads are disabled. Re-run with --allow-weight-download (requires internet), or use --no-pretrained.")
        weights = models.MobileNet_V3_Small_Weights.DEFAULT
    model = models.mobilenet_v3_small(weights=weights)
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, num_classes)
    return model


def inference_transform(image_size: int = IMAGE_SIZE):
    validate_image_size(image_size)
    _, _, _, transforms = _torch()
    return transforms.Compose([transforms.Resize((image_size, image_size)), transforms.ToTensor(), transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD)])


def _safe_torch_load(torch, source: str | Path, map_location: str = "cpu"):
    """Load only tensor-safe checkpoint data; never fall back to pickle loading."""

    try:
        return torch.load(source, map_location=map_location, weights_only=True)
    except TypeError as exc:
        raise RuntimeError("This checkpoint loader requires torch.load(..., weights_only=True). Upgrade PyTorch to a version that supports safe weights-only loading; refusing an unsafe fallback.") from exc


def validate_checkpoint_payload(payload: Mapping[str, Any], expected_image_size: int | None = None) -> dict[str, Any]:
    """Validate all metadata before a model is constructed or state is loaded."""

    if not isinstance(payload, Mapping):
        raise ValueError("Checkpoint must contain a metadata dictionary")
    if payload.get("format_version") != CHECKPOINT_VERSION:
        raise ValueError(f"Unsupported checkpoint format_version {payload.get('format_version')!r}; expected {CHECKPOINT_VERSION}")
    if payload.get("model_name") != MODEL_NAME:
        raise ValueError(f"Unsupported model_name {payload.get('model_name')!r}; expected {MODEL_NAME!r}")
    if list(payload.get("class_names", [])) != list(CLASS_NAMES):
        raise ValueError(f"Checkpoint class order must be exactly {list(CLASS_NAMES)}")
    image_size = validate_image_size(payload.get("image_size"))
    if expected_image_size is not None and image_size != validate_image_size(expected_image_size):
        raise ValueError(f"Checkpoint image_size {image_size} does not match configured image_size {expected_image_size}")
    if not isinstance(payload.get("state_dict"), Mapping):
        raise ValueError("Checkpoint is missing a tensor state_dict")
    return dict(payload)


def save_checkpoint(path: str | Path, model, class_names: Sequence[str] = CLASS_NAMES, model_name: str = MODEL_NAME, image_size: int = IMAGE_SIZE, epoch: int | None = None, metrics: Mapping[str, Any] | None = None) -> Path:
    validate_image_size(image_size)
    if model_name != MODEL_NAME:
        raise ValueError(f"model_name must be {MODEL_NAME!r}")
    torch, _, _, _ = _torch()
    expected = list(CLASS_NAMES)
    if list(class_names) != expected:
        raise ValueError(f"class_names must exactly match {expected}")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": CHECKPOINT_VERSION,
        "model_name": model_name,
        "class_names": list(class_names),
        "image_size": image_size,
        "state_dict": model.state_dict(),
        "epoch": epoch,
        "metrics": dict(metrics or {}),
    }
    torch.save(payload, destination)
    return destination


def load_checkpoint(path: str | Path, map_location: str = "cpu", allow_weight_download: bool = False):
    """Load and validate a PaletteCard checkpoint."""

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Checkpoint not found: {source}. Train one with `python -m palette_card.training` first.")
    torch, _, _, _ = _torch()
    payload = validate_checkpoint_payload(_safe_torch_load(torch, source, map_location))
    model = build_model(len(CLASS_NAMES), pretrained=False, allow_weight_download=allow_weight_download)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, dict(payload)


def predict(model, image, device: str = "cpu", image_size: int = IMAGE_SIZE) -> tuple[str, float, dict[str, float]]:
    """Predict a class and return label, confidence, and all probabilities."""

    torch, _, _, _ = _torch()
    transformed = inference_transform(image_size)(image.convert("RGB")).unsqueeze(0).to(device)
    model = model.to(device)
    model.eval()
    with torch.no_grad():
        probabilities = torch.softmax(model(transformed), dim=1)[0]
    index = int(torch.argmax(probabilities).item())
    scores = {name: float(probabilities[i].item()) for i, name in enumerate(CLASS_NAMES)}
    return CLASS_NAMES[index], scores[CLASS_NAMES[index]], scores
