"""Learned palette-role selection with deterministic design guardrails.

The neural network is a compact student of the explainable design engine. It
learns primary and secondary role colors in Oklab from five observed source
colors. Accessibility, gamut mapping, neutral surfaces, and final quality
checks remain deterministic.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence

import numpy as np

from .design import (
    DesignPalette,
    DesignQuality,
    _contrast,
    _rendered_accent_metrics,
    _safe_pair,
    _separation,
    _source_relationship,
    derive_design_palette,
    oklab_to_srgb,
    oklch_to_srgb,
    srgb_to_oklab,
    srgb_to_oklch,
)
from .palette import Color

PALETTE_CHECKPOINT_VERSION = 1
PALETTE_MODEL_NAME = "palette_mlp_v1"
PALETTE_INPUT_DIM = 20
PALETTE_OUTPUT_DIM = 6


def build_palette_model():
    import torch.nn as nn

    return nn.Sequential(
        nn.Linear(PALETTE_INPUT_DIM, 128),
        nn.SiLU(),
        nn.Dropout(0.05),
        nn.Linear(128, 128),
        nn.SiLU(),
        nn.Linear(128, PALETTE_OUTPUT_DIM),
    )


def encode_source_palette(colors: Sequence[Color]) -> np.ndarray:
    """Encode up to five observed colors as Oklab plus pixel proportion."""

    features = np.zeros((5, 4), dtype=np.float32)
    for index, color in enumerate(tuple(colors)[:5]):
        features[index, :3] = np.asarray(srgb_to_oklab(color), dtype=np.float32)
        features[index, 3] = float(np.clip(color.proportion, 0.0, 1.0))
    return features.reshape(-1)


def palette_target(colors: Sequence[Color]) -> np.ndarray:
    """Return teacher primary/secondary roles in a continuous color space."""

    design = derive_design_palette(colors)
    return np.asarray(
        (*srgb_to_oklab(design.primary), *srgb_to_oklab(design.secondary)),
        dtype=np.float32,
    )


def synthetic_source_palette(rng: np.random.Generator) -> list[Color]:
    """Create a varied, image-like source palette for teacher distillation."""

    count = int(rng.integers(1, 6))
    family = str(rng.choice(("mono", "analogous", "complementary", "multicolor")))
    base_hue = float(rng.uniform(0.0, 360.0))
    if family == "mono":
        hues = base_hue + rng.normal(0.0, 8.0, count)
    elif family == "analogous":
        hues = base_hue + rng.normal(0.0, 28.0, count)
    elif family == "complementary":
        hues = np.asarray([
            base_hue + (180.0 if index % 2 else 0.0) + rng.normal(0.0, 12.0)
            for index in range(count)
        ])
    else:
        hues = rng.uniform(0.0, 360.0, count)
    weights = rng.dirichlet(np.full(count, 1.35))
    colors: list[Color] = []
    for index in range(count):
        lightness = float(rng.uniform(0.20, 0.92))
        chroma = float(rng.uniform(0.0, 0.018) if rng.random() < 0.22 else rng.uniform(0.035, 0.30))
        rgb = oklch_to_srgb((lightness, chroma, float(hues[index] % 360.0)))
        colors.append(Color(rgb, float(weights[index])))
    return sorted(colors, key=lambda color: color.proportion, reverse=True)


def generate_palette_dataset(count: int, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    if count < 100:
        raise ValueError("count must be at least 100")
    rng = np.random.default_rng(seed)
    inputs: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    while len(inputs) < count:
        colors = synthetic_source_palette(rng)
        design = derive_design_palette(colors)
        if not design.quality.contrast_pass or design.quality.score < 80.0:
            continue
        inputs.append(encode_source_palette(colors))
        targets.append(palette_target(colors))
    return np.stack(inputs), np.stack(targets)


def save_palette_dataset(path: str | Path, inputs: np.ndarray, targets: np.ndarray, seed: int = 42) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if inputs.shape != (len(inputs), PALETTE_INPUT_DIM) or targets.shape != (len(inputs), PALETTE_OUTPUT_DIM):
        raise ValueError("unexpected palette dataset shape")
    rng = np.random.default_rng(seed)
    indices = rng.permutation(len(inputs))
    train_end = int(len(indices) * 0.80)
    val_end = int(len(indices) * 0.90)
    np.savez_compressed(
        destination,
        train_x=inputs[indices[:train_end]], train_y=targets[indices[:train_end]],
        val_x=inputs[indices[train_end:val_end]], val_y=targets[indices[train_end:val_end]],
        test_x=inputs[indices[val_end:]], test_y=targets[indices[val_end:]],
        seed=np.asarray(seed),
    )
    return destination


def train_palette_model(
    dataset_path: str | Path,
    checkpoint_path: str | Path,
    metrics_path: str | Path,
    *,
    epochs: int = 60,
    batch_size: int = 256,
    learning_rate: float = 1e-3,
    seed: int = 42,
) -> dict:
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    torch.manual_seed(seed)
    np.random.seed(seed)
    arrays = np.load(dataset_path)
    tensors = {
        key: torch.as_tensor(arrays[key], dtype=torch.float32)
        for key in ("train_x", "train_y", "val_x", "val_y", "test_x", "test_y")
    }
    model = build_palette_model()
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    criterion = torch.nn.SmoothL1Loss(beta=0.02)
    loader = DataLoader(
        TensorDataset(tensors["train_x"], tensors["train_y"]),
        batch_size=batch_size, shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    best_loss = math.inf
    best_epoch = 0
    history = []
    checkpoint = Path(checkpoint_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, epochs + 1):
        model.train()
        total = 0.0
        for inputs, targets in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(inputs), targets)
            loss.backward()
            optimizer.step()
            total += float(loss.item()) * len(inputs)
        model.eval()
        with torch.no_grad():
            val_loss = float(criterion(model(tensors["val_x"]), tensors["val_y"]).item())
        history.append({"epoch": epoch, "train_loss": total / len(tensors["train_x"]), "val_loss": val_loss})
        if val_loss < best_loss:
            best_loss, best_epoch = val_loss, epoch
            torch.save({
                "format_version": PALETTE_CHECKPOINT_VERSION,
                "model_name": PALETTE_MODEL_NAME,
                "input_dim": PALETTE_INPUT_DIM,
                "output_dim": PALETTE_OUTPUT_DIM,
                "state_dict": model.state_dict(),
                "epoch": epoch,
                "val_loss": val_loss,
            }, checkpoint)
    model, metadata = load_palette_checkpoint(checkpoint)
    model.eval()
    with torch.no_grad():
        predicted = model(tensors["test_x"]).numpy()
    errors = np.linalg.norm(
        predicted.reshape(-1, 2, 3) - tensors["test_y"].numpy().reshape(-1, 2, 3),
        axis=2,
    ) * 100.0
    report = {
        "model": PALETTE_MODEL_NAME,
        "counts": {key: int(len(tensors[f"{key}_x"])) for key in ("train", "val", "test")},
        "best_epoch": best_epoch,
        "best_val_loss": best_loss,
        "test_mean_delta_e_ok": float(errors.mean()),
        "test_p90_delta_e_ok": float(np.percentile(errors, 90)),
        "history": history,
        "checkpoint_metadata": {key: value for key, value in metadata.items() if key != "state_dict"},
    }
    metrics = Path(metrics_path)
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def load_palette_checkpoint(path: str | Path):
    import torch

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Palette checkpoint not found: {source}")
    payload = torch.load(source, map_location="cpu", weights_only=True)
    if payload.get("format_version") != PALETTE_CHECKPOINT_VERSION or payload.get("model_name") != PALETTE_MODEL_NAME:
        raise ValueError("unsupported palette checkpoint")
    if payload.get("input_dim") != PALETTE_INPUT_DIM or payload.get("output_dim") != PALETTE_OUTPUT_DIM:
        raise ValueError("palette checkpoint dimensions do not match")
    model = build_palette_model()
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, dict(payload)


def _guarded_role(predicted_lab: Sequence[float], fallback: Color, source: Sequence[Color], max_chroma: float) -> Color:
    if len(predicted_lab) != 3 or not np.isfinite(predicted_lab).all():
        return fallback
    rgb = oklab_to_srgb((
        float(np.clip(predicted_lab[0], 0.36, 0.74)),
        float(np.clip(predicted_lab[1], -0.35, 0.35)),
        float(np.clip(predicted_lab[2], -0.35, 0.35)),
    ))
    lightness, chroma, hue = srgb_to_oklch(rgb)
    rgb = oklch_to_srgb((lightness, min(chroma, max_chroma), hue))
    candidate = Color(rgb)
    chromatic = [srgb_to_oklch(color) for color in source if srgb_to_oklch(color)[1] >= 0.022]
    if chromatic:
        distances = [min(abs(hue - item[2]), 360.0 - abs(hue - item[2])) for item in chromatic]
        if min(distances) > 55.0:
            return fallback
    return candidate


def derive_learned_design_palette(colors: Sequence[Color], model) -> DesignPalette:
    """Predict accent roles, then enforce the expert engine's safety rules."""

    import torch

    source = tuple(colors)
    base = derive_design_palette(source)
    with torch.no_grad():
        output = model(torch.as_tensor(encode_source_palette(source)).unsqueeze(0)).numpy()[0]
    primary = _guarded_role(output[:3], base.primary, source, 0.18)
    secondary = _guarded_role(output[3:], base.secondary, source, 0.14)
    primary, on_primary = _safe_pair(primary)
    secondary, on_secondary = _safe_pair(secondary)
    accent, on_accent = primary, on_primary
    primary_lch, secondary_lch = srgb_to_oklch(primary), srgb_to_oklch(secondary)
    tints = tuple(Color(oklch_to_srgb(spec)) for spec in (
        (0.88, min(primary_lch[1] * 0.28, 0.06), primary_lch[2]),
        (0.90, min(secondary_lch[1] * 0.24, 0.05), secondary_lch[2]),
        (0.95, min(primary_lch[1] * 0.18, 0.04), primary_lch[2]),
        (0.96, min(secondary_lch[1] * 0.16, 0.035), secondary_lch[2]),
    ))
    ratios = [
        _contrast(base.background, base.on_background), _contrast(base.surface, base.on_surface),
        _contrast(primary, on_primary), _contrast(secondary, on_secondary), _contrast(accent, on_accent),
    ]
    strong_count, strong_area = _rendered_accent_metrics((primary, secondary, accent))
    relationship = _source_relationship(source, (primary, secondary))
    separation = _separation((primary, secondary))
    quality = DesignQuality(
        score=100.0 * (0.35 * float(min(ratios) >= 4.5) + 0.25 + 0.20 * 0.95 + 0.10 * separation + 0.10 * relationship),
        contrast_pass=min(ratios) >= 4.5,
        contrast_ratio_min=min(ratios),
        large_surface_neutrality=base.quality.large_surface_neutrality,
        accent_restraint=0.95,
        perceptual_separation=separation,
        source_relationship=relationship,
        strong_accent_count=strong_count,
        strong_accent_area=strong_area,
    )
    return DesignPalette(
        source, base.background, base.surface, primary, secondary, accent,
        base.on_background, base.on_surface, on_primary, on_secondary, on_accent,
        tints, base.harmony,
        "Learned palette student selected the accent roles; OKLCH gamut, source-hue, neutral-surface, and WCAG guardrails were then enforced.",
        quality, base.dark,
    )


__all__ = [
    "build_palette_model", "derive_learned_design_palette", "encode_source_palette",
    "generate_palette_dataset", "load_palette_checkpoint", "palette_target",
    "save_palette_dataset", "synthetic_source_palette", "train_palette_model",
]
