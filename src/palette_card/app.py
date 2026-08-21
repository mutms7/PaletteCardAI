"""Gradio application for PaletteCard AI.

The app intentionally works without a custom checkpoint. In Demo Mode the
object dropdown is the source of the label; Auto never invents a confidence
score. After training, restart the app and Auto will use the validated model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .card import render_card_set, save_cards
from .config import CLASS_NAMES, Paths
from .model import load_checkpoint, predict, select_device
from .palette import derive_palette_roles, extract_palette
from .runtime import cleanup_generated_cards, validate_upload_image


@dataclass
class Predictor:
    model: object
    device: str
    image_size: int

    def predict(self, image: Image.Image):
        return predict(self.model, image, self.device, self.image_size)


@dataclass
class PalettePredictor:
    model: object

    def predict(self, colors):
        from .palette_model import derive_learned_design_palette

        return derive_learned_design_palette(colors, self.model)


def load_predictor(checkpoint_path: str | Path | None = None) -> tuple[Predictor | None, str]:
    """Load a checkpoint if present, returning a user-facing mode message."""

    checkpoint = Path(checkpoint_path or Paths().default_checkpoint)
    if not checkpoint.exists():
        return None, "Demo Mode: no custom checkpoint found. Select an object below; Auto has no model confidence yet."
    try:
        model, metadata = load_checkpoint(checkpoint, map_location="cpu")
        device = select_device("auto")
        return Predictor(model, device, int(metadata.get("image_size", 224))), f"Model Mode: loaded {checkpoint.name}; Auto predictions are enabled."
    except Exception as exc:
        return None, f"Demo Mode: checkpoint could not be loaded ({exc}). Select an object manually or retrain it."


def load_palette_predictor(checkpoint_path: str | Path | None = None) -> tuple[PalettePredictor | None, str]:
    checkpoint = Path(checkpoint_path or Paths().palette_checkpoint)
    if not checkpoint.exists():
        return None, "Palette mode: explainable color-theory engine (learned palette checkpoint not found)."
    try:
        from .palette_model import load_palette_checkpoint

        model, _ = load_palette_checkpoint(checkpoint)
        return PalettePredictor(model), f"Palette mode: learned {checkpoint.name} roles with deterministic accessibility guardrails."
    except Exception as exc:
        return None, f"Palette mode: explainable fallback ({exc})."


def resolve_output_dir(output_dir: str | Path | None = None) -> Path:
    """Resolve/create the only directory the Gradio server may serve."""

    destination = Path(output_dir or Paths().cards).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def analyze_image(image: Image.Image, object_choice: str = "Auto", title: str = "A little color for you", message: str = "Made from the colors in your photo.", predictor: Predictor | None = None, mode_message: str | None = None, output_dir: str | Path | None = None, palette_predictor: PalettePredictor | None = None, palette_mode_message: str | None = None, max_pixels: int = 16_000_000, retention_hours: int | None = None):
    """Run recognition (or explicit manual selection), palette, and card generation."""

    image = validate_upload_image(image, max_pixels=max_pixels)
    colors = extract_palette(image, n_colors=5)
    roles = palette_predictor.predict(colors) if palette_predictor is not None else derive_palette_roles(colors)
    if object_choice and object_choice != "Auto":
        label = object_choice
        recognition = f"Object: {label.title()} (user selected; no model confidence claimed)"
    elif predictor is not None:
        label, confidence, _ = predictor.predict(image)
        warning = "  Low-confidence warning: try a clearer centered photo or choose the label manually." if confidence < 0.60 else ""
        recognition = f"Object: {label.title()} — {confidence:.1%} confidence.{warning}"
    else:
        label = "unclassified object"
        recognition = "Object: not classified (Demo Mode; no confidence claimed). Choose a label manually for a named card."
    cards = render_card_set(image, colors, title, message, label, design_palette=roles)
    destination = Path(output_dir or Paths().cards)
    if retention_hours is not None:
        cleanup_generated_cards(destination, retention_hours)
    stem = f"palette-card-{uuid4().hex[:10]}"
    paths = save_cards(cards, destination, stem=stem)
    source_text = "  ".join(f"{color.hex} ({color.proportion:.0%})" for color in roles.source)
    design_roles = roles.as_dict()
    quality = roles.quality
    quality_text = ", ".join(
        f"{key.replace('_', ' ')}={value}"
        for key, value in quality.breakdown.items()
    )
    # Keep source observations and design decisions visibly separate. This
    # makes it clear that the classifier (when trained) recognizes objects,
    # while this current color intelligence is deterministic design science.
    status = "\n".join(filter(None, [
        mode_message,
        palette_mode_message,
        recognition,
        f"Palette: {source_text}  [Source colors (observed)]",
        f"Design roles (derived): {design_roles}",
        f"Design rationale: {roles.rationale}",
        f"Harmony: {roles.harmony} · heuristic design quality: {quality.score:.0f}/100 ({quality_text})",
    ]))
    gallery = [(card, f"Variation {index}") for index, card in enumerate(cards, 1)]
    return gallery, [str(path) for path in paths], status


def build_app(checkpoint_path: str | Path | None = None, output_dir: str | Path | None = None, palette_checkpoint_path: str | Path | None = None, *, max_pixels: int = 16_000_000, retention_hours: int | None = None, concurrency: int = 2, queue_size: int = 32):
    """Build the Gradio Blocks UI without launching it."""

    try:
        import gradio as gr
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("Gradio is required for the web app. Install dependencies from pyproject.toml.") from exc
    predictor, mode_message = load_predictor(checkpoint_path)
    palette_predictor, palette_mode_message = load_palette_predictor(palette_checkpoint_path)
    resolved_output_dir = resolve_output_dir(output_dir)

    def callback(image, object_choice, title, message):
        try:
            return analyze_image(image, object_choice, title, message, predictor, mode_message, resolved_output_dir, palette_predictor, palette_mode_message, max_pixels, retention_hours)
        except Exception as exc:
            # Keep UI failures readable instead of exposing a Python traceback.
            return [], [], f"Could not create cards: {exc}"

    with gr.Blocks(title="PaletteCard AI") as demo:
        gr.Markdown("# PaletteCard AI\nUpload one centered-object photo and turn its colors into three downloadable designs.")
        gr.Markdown(mode_message + "\n\n" + palette_mode_message)
        with gr.Row():
            with gr.Column(scale=1):
                image = gr.Image(type="pil", label="Object photo")
                object_choice = gr.Dropdown(["Auto", *CLASS_NAMES], value="Auto", label="Object label", info="Demo Mode uses your selection. Auto uses the trained model when a checkpoint exists.")
                title = gr.Textbox(value="A little color for you", label="Card title")
                message = gr.Textbox(value="Made from the colors in your photo.", label="Card message", lines=3)
                generate = gr.Button("Generate 3 cards", variant="primary")
            with gr.Column(scale=2):
                gallery = gr.Gallery(label="Design variations", columns=3, height="auto")
                downloads = gr.File(label="Download PNG files", file_count="multiple")
                status = gr.Markdown("Upload an image to begin.")
        generate.click(callback, inputs=[image, object_choice, title, message], outputs=[gallery, downloads, status])
    demo.queue(max_size=queue_size, default_concurrency_limit=concurrency)
    # Keep these attributes inspectable for tests and use the exact same path
    # when main() launches the server with Gradio's allowed_paths guard.
    demo._palette_card_output_dir = resolved_output_dir
    demo._palette_card_allowed_paths = [str(resolved_output_dir)]
    demo._palette_card_object_model_ready = predictor is not None
    demo._palette_card_palette_model_ready = palette_predictor is not None
    return demo


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Launch the PaletteCard AI Gradio app")
    parser.add_argument("--checkpoint", type=Path, default=Paths().default_checkpoint)
    parser.add_argument("--palette-checkpoint", type=Path, default=Paths().palette_checkpoint)
    parser.add_argument("--output-dir", type=Path, default=Paths().cards)
    parser.add_argument("--share", action="store_true", help="Ask Gradio for a public share link; review privacy before using")
    args = parser.parse_args()
    output_dir = resolve_output_dir(args.output_dir)
    demo = build_app(args.checkpoint, output_dir, args.palette_checkpoint)
    demo.launch(share=args.share, allowed_paths=[str(output_dir)])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
