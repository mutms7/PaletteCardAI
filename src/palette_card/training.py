"""Training and dataset preparation commands for PaletteCard AI."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import numpy as np

from .config import CLASS_NAMES, IMAGE_SIZE, IMAGENET_MEAN, IMAGENET_STD, Paths, TrainConfig
from .commons_dataset import validate_review_approval
from .model import _safe_torch_load, build_model, save_checkpoint, select_device, validate_checkpoint_payload, validate_image_size


def seed_everything(seed: int) -> None:
    """Seed Python, NumPy, and Torch for repeatable experiments."""

    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    except ImportError:
        pass


def validate_data_layout(data_dir: str | Path, minimum_train_images: int = 2) -> dict[str, dict[str, int]]:
    """Validate train/val/test folders and return image counts by class."""

    root = Path(data_dir)
    if not root.exists():
        raise FileNotFoundError(f"Dataset directory does not exist: {root}. Run `python scripts/prepare_dataset.py` and add licensed images.")
    validate_review_approval(root)
    report: dict[str, dict[str, int]] = {}
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    for split in ("train", "val", "test"):
        split_dir = root / split
        if not split_dir.exists():
            raise ValueError(f"Missing split directory {split_dir}. Expected train, val, and test.")
        actual = {p.name for p in split_dir.iterdir() if p.is_dir()}
        expected = set(CLASS_NAMES)
        missing, extra = expected - actual, actual - expected
        if missing or extra:
            raise ValueError(f"{split_dir} must contain exactly {list(CLASS_NAMES)}; missing={sorted(missing)}, extra={sorted(extra)}")
        report[split] = {}
        for name in CLASS_NAMES:
            count = sum(1 for p in (split_dir / name).iterdir() if p.is_file() and p.suffix.lower() in extensions)
            minimum = minimum_train_images if split == "train" else 1
            if count < minimum:
                raise ValueError(f"Not enough {name} images in {split_dir / name}: found {count}, need at least {minimum}.")
            report[split][name] = count
    return report


def _datasets(data_dir: Path, image_size: int):
    try:
        from torchvision import datasets, transforms
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("torchvision is required for training. Install dependencies from pyproject.toml.") from exc

    class OrderedImageFolder(datasets.ImageFolder):
        """ImageFolder with the project's declared class order, not alphabetic order."""

        def __init__(self, root, transform=None):
            super().__init__(root, transform=transform)
            if set(self.classes) != set(CLASS_NAMES):
                raise ValueError(f"{root} classes must be exactly {list(CLASS_NAMES)}, got {self.classes}")
            original_names = list(self.classes)
            old_to_new = {old: CLASS_NAMES.index(name) for old, name in enumerate(original_names)}
            self.class_to_idx = {name: index for index, name in enumerate(CLASS_NAMES)}
            self.samples = [(path, old_to_new[target]) for path, target in self.samples]
            self.imgs = self.samples
            self.targets = [target for _, target in self.samples]
            self.classes = list(CLASS_NAMES)

    train_transform = transforms.Compose([
        transforms.RandomResizedCrop(image_size, scale=(0.62, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomRotation(15),
        transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
        transforms.ToTensor(),
        transforms.RandomErasing(p=0.12, scale=(0.02, 0.10), ratio=(0.5, 2.0)),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    eval_transform = transforms.Compose([
        transforms.Resize((image_size, image_size)),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    return (
        OrderedImageFolder(data_dir / "train", transform=train_transform),
        OrderedImageFolder(data_dir / "val", transform=eval_transform),
        OrderedImageFolder(data_dir / "test", transform=eval_transform),
    )


def _run_epoch(model, loader, criterion, optimizer, device: str, train: bool, mixup_alpha: float = 0.0):
    import torch

    model.train(train)
    total_loss = 0.0
    correct = 0.0
    total = 0
    for inputs, labels in loader:
        inputs, labels = inputs.to(device), labels.to(device)
        mixed_labels = None
        mix = 1.0
        if train and mixup_alpha > 0.0 and inputs.size(0) > 1:
            mix = float(np.random.beta(mixup_alpha, mixup_alpha))
            permutation = torch.randperm(inputs.size(0), device=inputs.device)
            mixed_labels = labels[permutation]
            inputs = mix * inputs + (1.0 - mix) * inputs[permutation]
        if train:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(train):
            logits = model(inputs)
            loss = criterion(logits, labels)
            if mixed_labels is not None:
                loss = mix * loss + (1.0 - mix) * criterion(logits, mixed_labels)
            if train:
                loss.backward()
                optimizer.step()
        total_loss += float(loss.item()) * inputs.size(0)
        predictions = logits.argmax(1)
        correct += mix * float((predictions == labels).sum().item())
        if mixed_labels is not None:
            correct += (1.0 - mix) * float((predictions == mixed_labels).sum().item())
        total += inputs.size(0)
    return {"loss": total_loss / max(total, 1), "accuracy": correct / max(total, 1), "count": total}


def _evaluate(model, loader, device: str):
    import torch

    model.eval()
    matrix = np.zeros((len(CLASS_NAMES), len(CLASS_NAMES)), dtype=int)
    correct = total = 0
    with torch.no_grad():
        for inputs, labels in loader:
            logits = model(inputs.to(device))
            predictions = logits.argmax(1).cpu().numpy()
            actual = labels.numpy()
            for truth, predicted in zip(actual, predictions):
                matrix[int(truth), int(predicted)] += 1
            correct += int((predictions == actual).sum())
            total += len(actual)
    return {"accuracy": correct / max(total, 1), "count": total, "confusion_matrix": matrix.tolist()}


def _write_plots(history: list[dict[str, Any]], confusion: list[list[int]], directory: Path) -> None:
    """Write optional PNG diagnostics without making matplotlib a hard dependency."""

    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    directory.mkdir(parents=True, exist_ok=True)
    epochs = [row["epoch"] for row in history]
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].plot(epochs, [row["train"]["loss"] for row in history], label="train loss")
    axes[0].plot(epochs, [row["val"]["loss"] for row in history], label="val loss")
    axes[0].set_xlabel("epoch")
    axes[0].legend()
    axes[0].set_title("Loss")
    axes[1].plot(epochs, [row["train"]["accuracy"] for row in history], label="train accuracy")
    axes[1].plot(epochs, [row["val"]["accuracy"] for row in history], label="val accuracy")
    axes[1].set_xlabel("epoch")
    axes[1].legend()
    axes[1].set_title("Accuracy")
    figure.tight_layout()
    figure.savefig(directory / "training_curves.png", dpi=140)
    plt.close(figure)
    figure, axis = plt.subplots(figsize=(5, 4))
    axis.imshow(confusion, cmap="Blues")
    axis.set_xticks(range(len(CLASS_NAMES)), CLASS_NAMES, rotation=45, ha="right")
    axis.set_yticks(range(len(CLASS_NAMES)), CLASS_NAMES)
    axis.set_xlabel("predicted")
    axis.set_ylabel("actual")
    axis.set_title("Test confusion matrix")
    for row in range(len(CLASS_NAMES)):
        for column in range(len(CLASS_NAMES)):
            axis.text(column, row, confusion[row][column], ha="center", va="center")
    figure.tight_layout()
    figure.savefig(directory / "confusion_matrix.png", dpi=140)
    plt.close(figure)


def train(config: TrainConfig) -> dict[str, Any]:
    """Run transfer learning and write checkpoint plus metric artifacts."""

    import torch
    from torch import nn
    from torch.utils.data import DataLoader

    if config.epochs < 1:
        raise ValueError("epochs must be at least 1")
    if config.batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if not 0.0 <= config.label_smoothing < 1.0:
        raise ValueError("label_smoothing must be in [0, 1)")
    if config.mixup_alpha < 0.0:
        raise ValueError("mixup_alpha must be non-negative")
    validate_image_size(config.image_size)
    counts = validate_data_layout(config.data_dir)
    seed_everything(config.seed)
    device = select_device(config.device)
    train_set, val_set, test_set = _datasets(Path(config.data_dir), config.image_size)
    generator = torch.Generator().manual_seed(config.seed)
    train_loader = DataLoader(train_set, batch_size=config.batch_size, shuffle=True, num_workers=config.num_workers, generator=generator)
    val_loader = DataLoader(val_set, batch_size=config.batch_size, shuffle=False, num_workers=config.num_workers)
    test_loader = DataLoader(test_set, batch_size=config.batch_size, shuffle=False, num_workers=config.num_workers)
    model = build_model(len(CLASS_NAMES), pretrained=config.pretrained, allow_weight_download=config.allow_weight_download).to(device)
    criterion = nn.CrossEntropyLoss(label_smoothing=config.label_smoothing)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(config.epochs, 1))
    history: list[dict[str, Any]] = []
    best_accuracy = -1.0
    best_epoch = 0
    for epoch in range(1, config.epochs + 1):
        train_metrics = _run_epoch(model, train_loader, criterion, optimizer, device, train=True, mixup_alpha=config.mixup_alpha)
        val_metrics = _run_epoch(model, val_loader, criterion, optimizer, device, train=False)
        scheduler.step()
        row = {"epoch": epoch, "train": train_metrics, "val": val_metrics, "learning_rate": optimizer.param_groups[0]["lr"]}
        history.append(row)
        if val_metrics["accuracy"] > best_accuracy:
            best_accuracy, best_epoch = val_metrics["accuracy"], epoch
            save_checkpoint(config.output_checkpoint, model, image_size=config.image_size, epoch=epoch, metrics={"val_accuracy": best_accuracy})
    # Evaluate the best checkpoint, not the final epoch.
    best_payload = validate_checkpoint_payload(_safe_torch_load(torch, config.output_checkpoint, device), expected_image_size=config.image_size)
    model.load_state_dict(best_payload["state_dict"])
    test_metrics = _evaluate(model, test_loader, device)
    config.metrics_dir.mkdir(parents=True, exist_ok=True)
    report = {"classes": list(CLASS_NAMES), "counts": counts, "device": device, "best_epoch": best_epoch, "best_val_accuracy": best_accuracy, "test": test_metrics, "history": history}
    (config.metrics_dir / "history.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (config.metrics_dir / "confusion_matrix.json").write_text(json.dumps({"classes": list(CLASS_NAMES), "matrix": test_metrics["confusion_matrix"]}, indent=2), encoding="utf-8")
    _write_plots(history, test_metrics["confusion_matrix"], config.metrics_dir)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train PaletteCard's five-class MobileNet classifier")
    parser.add_argument("--data-dir", type=Path, default=Paths().data)
    parser.add_argument("--checkpoint", type=Path, default=Paths().default_checkpoint)
    parser.add_argument("--metrics-dir", type=Path, default=Paths().metrics)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--label-smoothing", type=float, default=0.05)
    parser.add_argument("--mixup-alpha", type=float, default=0.20)
    parser.add_argument("--num-workers", type=int, default=0, help="Use 0 on Windows if multiprocessing is unfamiliar")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--pretrained", action=argparse.BooleanOptionalAction, default=True, help="Start with ImageNet features (recommended)")
    parser.add_argument("--allow-weight-download", action="store_true", help="Permit torchvision to download ImageNet weights")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = TrainConfig(data_dir=args.data_dir, output_checkpoint=args.checkpoint, metrics_dir=args.metrics_dir, epochs=args.epochs, batch_size=args.batch_size, learning_rate=args.learning_rate, weight_decay=args.weight_decay, label_smoothing=args.label_smoothing, mixup_alpha=args.mixup_alpha, num_workers=args.num_workers, seed=args.seed, pretrained=args.pretrained, allow_weight_download=args.allow_weight_download, device=args.device)
    try:
        report = train(config)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Training failed: {exc}")
        return 2
    print(f"Training complete. Best validation accuracy: {report['best_val_accuracy']:.1%}; test accuracy: {report['test']['accuracy']:.1%}")
    print(f"Checkpoint: {config.output_checkpoint}")
    print(f"Metrics: {config.metrics_dir / 'history.json'}")
    return 0


def prepare_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create PaletteCard dataset folders; does not download images")
    parser.add_argument("--data-dir", type=Path, default=Paths().data)
    args = parser.parse_args(argv)
    root = args.data_dir
    for split in ("train", "val", "test"):
        for name in CLASS_NAMES:
            (root / split / name).mkdir(parents=True, exist_ok=True)
            (root / split / name / ".gitkeep").touch(exist_ok=True)
    print(f"Created train/val/test folders for: {', '.join(CLASS_NAMES)} under {root}")
    print("Add only licensed images; see data/README.md for split guidance.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
