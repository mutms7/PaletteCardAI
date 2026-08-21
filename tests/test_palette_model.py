import numpy as np

from palette_card.palette import Color, contrast_ratio
from palette_card.palette_model import (
    build_palette_model,
    derive_learned_design_palette,
    encode_source_palette,
    generate_palette_dataset,
    palette_target,
    save_palette_dataset,
)


def test_palette_features_and_targets_have_stable_shapes():
    colors = [Color((220, 30, 40), 0.7), Color((30, 70, 220), 0.3)]
    assert encode_source_palette(colors).shape == (20,)
    assert palette_target(colors).shape == (6,)


def test_synthetic_dataset_is_reproducible_and_split(tmp_path):
    first = generate_palette_dataset(100, seed=7)
    second = generate_palette_dataset(100, seed=7)
    assert np.array_equal(first[0], second[0])
    path = save_palette_dataset(tmp_path / "palettes.npz", *first, seed=7)
    arrays = np.load(path)
    assert arrays["train_x"].shape == (80, 20)
    assert arrays["val_x"].shape == (10, 20)
    assert arrays["test_x"].shape == (10, 20)


def test_untrained_palette_model_is_still_guarded():
    colors = [Color((230, 25, 40), 0.5), Color((20, 65, 225), 0.5)]
    design = derive_learned_design_palette(colors, build_palette_model())
    for fill, text in (
        (design.background, design.on_background), (design.surface, design.on_surface),
        (design.primary, design.on_primary), (design.secondary, design.on_secondary),
        (design.accent, design.on_accent),
    ):
        assert contrast_ratio(fill, text) >= 4.5
    assert design.quality.contrast_pass
