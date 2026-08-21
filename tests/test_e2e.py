from pathlib import Path

from PIL import Image

from palette_card.app import analyze_image, build_app, resolve_output_dir


def test_demo_mode_local_pipeline(tmp_path: Path):
    image = Image.new("RGBA", (48, 40), (240, 180, 40, 255))
    gallery, files, status = analyze_image(image, "cake", "Test card", "A local end-to-end test.", output_dir=tmp_path)
    assert len(gallery) == 3
    assert len(files) == 3
    assert all(Path(path).exists() for path in files)
    assert "user selected" in status
    assert "Palette:" in status


def test_gradio_output_dir_is_resolved_and_restricted(tmp_path: Path):
    output_dir = resolve_output_dir(tmp_path / "cards")
    assert output_dir == (tmp_path / "cards").resolve()
    demo = build_app(output_dir=output_dir)
    assert demo._palette_card_allowed_paths == [str(output_dir)]
    assert demo._palette_card_output_dir == output_dir
