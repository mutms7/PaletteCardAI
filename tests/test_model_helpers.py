import pytest

from palette_card.config import CLASS_NAMES
from palette_card.model import MODEL_NAME


def test_declared_class_order_is_stable():
    assert CLASS_NAMES == ("flower", "heart", "ring", "cake", "balloon")
    assert MODEL_NAME == "mobilenet_v3_small"


def test_missing_checkpoint_is_actionable():
    from palette_card.model import load_checkpoint

    with pytest.raises(FileNotFoundError, match="Checkpoint not found"):
        load_checkpoint("this-file-does-not-exist.pt")


def test_checkpoint_round_trip_records_and_validates_image_size(tmp_path):
    torch = pytest.importorskip("torch")
    from palette_card.model import build_model, load_checkpoint, save_checkpoint

    checkpoint = tmp_path / "round-trip.pt"
    model = build_model(pretrained=False)
    save_checkpoint(checkpoint, model, image_size=128)
    loaded, metadata = load_checkpoint(checkpoint)
    assert metadata["format_version"] == 1
    assert metadata["image_size"] == 128
    assert list(metadata["class_names"]) == list(CLASS_NAMES)
    assert loaded.classifier[-1].out_features == len(CLASS_NAMES)
    payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    assert payload["format_version"] == 1


def test_checkpoint_rejects_unknown_format_before_model_build(tmp_path):
    torch = pytest.importorskip("torch")
    from palette_card.model import load_checkpoint

    checkpoint = tmp_path / "bad-format.pt"
    torch.save({"format_version": 999, "model_name": MODEL_NAME}, checkpoint)
    with pytest.raises(ValueError, match="format_version"):
        load_checkpoint(checkpoint)


def test_checkpoint_loader_refuses_unsafe_torch_fallback(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    from palette_card import model as model_module

    checkpoint = tmp_path / "not-readable.pt"
    checkpoint.write_bytes(b"placeholder")

    def no_weights_only(*args, **kwargs):
        raise TypeError("weights_only is unsupported")

    monkeypatch.setattr(torch, "load", no_weights_only)
    with pytest.raises(RuntimeError, match="weights_only"):
        model_module.load_checkpoint(checkpoint)


def test_image_size_validation_is_bounded():
    from palette_card.model import validate_image_size

    with pytest.raises(ValueError, match="between 32 and 1024"):
        validate_image_size(16)
