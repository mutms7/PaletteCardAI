from pathlib import Path

from palette_card.app import _color_story_html, _mode_badge, build_app


def _components_by_elem_id(demo):
    return {
        component["props"]["elem_id"]: component["props"]
        for component in demo.config["components"]
        if component.get("props", {}).get("elem_id")
    }


def test_studio_copy_and_states_are_present(tmp_path: Path):
    demo = build_app(
        checkpoint_path=tmp_path / "missing-object.pt",
        palette_checkpoint_path=tmp_path / "missing-palette.pt",
        output_dir=tmp_path / "cards",
    )
    components = _components_by_elem_id(demo)
    rendered_copy = "\n".join(str(component.get("props", {})) for component in demo.config["components"])

    assert _mode_badge(None) == "Demo Mode · choose the object yourself."
    assert "A small color studio" in rendered_copy
    assert "Make a color story from one photo." in rendered_copy
    assert "Drop a photo of one centered object" in rendered_copy
    assert "Flower, heart, ring, cake, or balloon · JPG, PNG, or WebP" in rendered_copy
    assert "How should we identify it?" in rendered_copy
    assert "Three directions, ready to download" in rendered_copy
    assert "Download all 3 PNGs" in rendered_copy
    assert "Color story" in rendered_copy
    assert "Runs on this computer by default." in rendered_copy

    assert components["pc-generate"]["interactive"] is False
    assert components["pc-empty-state"]["visible"] is True
    assert components["pc-results"]["visible"] is False
    assert "@media (prefers-reduced-motion: reduce)" in demo._palette_card_css
    assert "@media (max-width: 520px)" in demo._palette_card_css


def test_color_story_separates_observed_and_derived_roles():
    status = "\n".join(
        (
            "Palette: #aa1122 (60%)  #1133aa (40%)  [Source colors (observed)]",
            "Design roles (derived): {'background': '#f9f7f2', 'surface': '#fffdfa', 'primary': '#861b36', 'secondary': '#344992', 'accent': '#861b36'}",
        )
    )
    story = _color_story_html(status)

    assert "Observed in the photo" in story
    assert "Translated into design roles" in story
    assert story.count('<span class="pc-swatch"') == 7
    assert "#aa1122" in story
    assert "#344992" in story
