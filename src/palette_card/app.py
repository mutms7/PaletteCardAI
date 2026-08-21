"""Gradio application for PaletteCard AI.

The app intentionally works without a custom checkpoint. In Demo Mode the
object dropdown is the source of the label; Auto never invents a confidence
score. After training, restart the app and Auto will use the validated model.
"""

from __future__ import annotations

import ast
import html
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .card import render_card_set, save_cards
from .config import CLASS_NAMES, Paths
from .model import load_checkpoint, predict, select_device
from .palette import derive_palette_roles, extract_palette
from .runtime import cleanup_generated_cards, validate_upload_image


_ASSET_DIR = Path(__file__).with_name("assets")
_DEFAULT_TITLE = "A little color for you"
_DEFAULT_MESSAGE = "Made from the colors in your photo."
_EMPTY_STATE_COPY = "Upload a photo to begin."
_PRIVACY_COPY = "Runs on this computer by default. Generated PNGs stay in the configured local output folder until you remove them."


def _load_studio_css() -> str:
    """Load the local editorial stylesheet without making the UI network-bound."""

    try:
        return (_ASSET_DIR / "studio.css").read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - source checkout always includes the asset
        return ""


def _mode_badge(predictor: Predictor | None) -> str:
    """Return the short, honest recognition-mode copy used by the UI."""

    if predictor is None:
        return "Demo Mode · choose the object yourself."
    return "Model Mode · Auto is ready."


def _color_story_html(status: str) -> str:
    """Render inspectable source/derived swatches from the public status text.

    ``analyze_image`` intentionally keeps its established three-value return
    contract.  The UI can still show a visual color story by deriving this
    small presentation-only fragment from the existing status lines.
    """

    source_line = next((line for line in status.splitlines() if line.startswith("Palette:")), "")
    source_swatches = re.findall(r"(#[0-9a-fA-F]{6})\s+\(([^)]+)\)", source_line)
    roles_line = next((line for line in status.splitlines() if line.startswith("Design roles (derived):")), "")
    roles: dict[str, object] = {}
    try:
        roles = ast.literal_eval(roles_line.split(":", 1)[1].strip()) if roles_line else {}
    except (SyntaxError, ValueError):
        roles = {}

    def swatch(color: str, label: str, detail: str = "") -> str:
        safe_color = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#ded8cf"
        return (
            f'<span class="pc-swatch" style="--swatch:{safe_color}" '
            f'role="img" aria-label="{html.escape(label)} {html.escape(detail)}" '
            f'title="{html.escape(label)} {html.escape(detail)}"></span>'
        )

    observed = "".join(swatch(color, "Observed source color", proportion) for color, proportion in source_swatches)
    role_keys = ("background", "surface", "primary", "secondary", "accent")
    derived = "".join(
        swatch(str(roles[key]), f"Derived {key} role")
        for key in role_keys
        if key in roles
    )
    if not observed and not derived:
        return "<p class='pc-story-empty'>Your color story will appear here after generation.</p>"
    return (
        "<div class='pc-story-grid'>"
        "<div><p class='pc-story-label'>Observed in the photo</p>"
        f"<div class='pc-swatch-row'>{observed}</div></div>"
        "<div><p class='pc-story-label'>Translated into design roles</p>"
        f"<div class='pc-swatch-row'>{derived}</div></div>"
        "</div>"
    )


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


def analyze_image(image: Image.Image, object_choice: str = "Auto", title: str = _DEFAULT_TITLE, message: str = _DEFAULT_MESSAGE, predictor: Predictor | None = None, mode_message: str | None = None, output_dir: str | Path | None = None, palette_predictor: PalettePredictor | None = None, palette_mode_message: str | None = None, max_pixels: int = 16_000_000, retention_hours: int | None = None):
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
        "Your three cards are ready.",
        "5 colors observed · 3 directions composed.",
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

    def status_html(status: str) -> str:
        """Keep generation status readable and announce it to assistive tech."""

        escaped = html.escape(status)
        if "\nDETAILS: " in escaped:
            summary, detail = escaped.split("\nDETAILS: ", 1)
            body = f"{summary.replace(chr(10), '<br>')}<details><summary>Details</summary><span class='pc-status-detail'>{detail}</span></details>"
        else:
            body = escaped.replace("\n", "<br>")
        return f"<div class='pc-status-copy' aria-live='polite'>{body}</div>"

    def callback(image, object_choice, title, message):
        try:
            gallery, files, status = analyze_image(image, object_choice, title, message, predictor, mode_message, resolved_output_dir, palette_predictor, palette_mode_message, max_pixels, retention_hours)
            return (
                gallery,
                files,
                _color_story_html(status),
                status_html(status),
                gr.update(visible=True),
                gr.update(visible=True),
            )
        except Exception as exc:
            # Keep UI failures readable instead of exposing a Python traceback.
            status = "We couldn’t create the cards. Check that an image is uploaded, then try again.\nDETAILS: " + str(exc)
            return (
                [],
                [],
                _color_story_html(status),
                status_html(status),
                gr.update(visible=True),
                gr.update(visible=True),
            )

    def image_state(image):
        has_image = image is not None
        return (
            gr.update(interactive=has_image),
            gr.update(visible=not has_image),
            gr.update(visible=False),
            gr.update(visible=False),
            [],
            [],
            _color_story_html(""),
            "",
        )

    def reset_form():
        return (
            gr.update(value=None),
            gr.update(value="Auto"),
            gr.update(value=_DEFAULT_TITLE),
            gr.update(value=_DEFAULT_MESSAGE),
            [],
            [],
            _color_story_html(""),
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(value="Generate 3 cards", interactive=False),
            gr.update(visible=True),
        )

    def mark_loading():
        return gr.update(value="Finding the color story…", interactive=False)

    def mark_ready():
        return gr.update(value="Generate 3 cards", interactive=True)

    with gr.Blocks(title="PaletteCard AI") as demo:
        gr.HTML(
            "<header class='pc-header'><a class='pc-wordmark' href='#top' aria-label='PaletteCard AI home'>PaletteCard <span>AI</span></a>"
            "<div class='pc-header-meta'><span class='pc-local-badge'>Local by default</span>"
            "<a href='#pc-privacy'>Privacy</a></div></header>"
        )
        gr.HTML(
            "<main id='top' class='pc-main'><section class='pc-hero' aria-labelledby='pc-hero-title'>"
            "<p class='pc-eyebrow'>A small color studio</p>"
            "<h1 id='pc-hero-title'>Make a color story from one photo.</h1>"
            "<p class='pc-hero-copy'>One centered object becomes a considered color story and three greeting-card directions.</p>"
            "</section></main>"
        )
        with gr.Row(elem_classes=["pc-mode-row"]):
            gr.HTML(f"<div class='pc-mode-badge' role='status'><strong>{html.escape(_mode_badge(predictor))}</strong><span>{html.escape(mode_message)}</span></div>")
            gr.HTML(f"<div class='pc-palette-mode'><span>{html.escape(palette_mode_message)}</span></div>")

        with gr.Row(elem_classes=["pc-studio-grid"]):
            with gr.Column(scale=5, elem_classes=["pc-input-rail"]):
                gr.Markdown("### Start with the source", elem_classes=["pc-section-kicker"])
                image = gr.Image(
                    type="pil",
                    sources=["upload", "webcam", "clipboard"],
                    label="Drop a photo of one centered object",
                    height=360,
                    elem_id="pc-image",
                    elem_classes=["pc-image-input"],
                )
                gr.Markdown("Flower, heart, ring, cake, or balloon · JPG, PNG, or WebP", elem_classes=["pc-upload-hint"])
                gr.Markdown("Your photo is the source of the story. The interface stays quiet so the colors can lead.", elem_classes=["pc-source-note"])
                object_choice = gr.Dropdown(
                    ["Auto", *CLASS_NAMES],
                    value="Auto",
                    label="How should we identify it?",
                    info="Demo Mode uses your choice. Model Mode can make an Auto prediction.",
                    elem_id="pc-object-choice",
                )
                with gr.Row(elem_classes=["pc-copy-row"]):
                    title = gr.Textbox(value=_DEFAULT_TITLE, label="Card title", elem_id="pc-title")
                    message = gr.Textbox(value=_DEFAULT_MESSAGE, label="Card message", lines=3, elem_id="pc-message")
                generate = gr.Button("Generate 3 cards", variant="primary", size="lg", interactive=False, elem_id="pc-generate", elem_classes=["pc-cta"])
                gr.Markdown("Processing stays on this computer. No network request is needed to make the cards.", elem_classes=["pc-local-note"])

            with gr.Column(scale=4, elem_classes=["pc-source-rail"]):
                gr.HTML(
                    "<div class='pc-source-rail-heading'><p class='pc-section-kicker'>The color lab</p>"
                    "<h2>Photo in. Color story out.</h2>"
                    "<p>Start with one clear subject. We observe five colors, then turn them into readable roles for three distinct layouts.</p></div>"
                )
                empty_state = gr.Markdown(_EMPTY_STATE_COPY, elem_id="pc-empty-state", elem_classes=["pc-empty-state"])
                gr.HTML(
                    "<div class='pc-empty-example'><span aria-hidden='true'>↗</span> Drop a photo of one centered object above to see the studio come alive.</div>",
                    elem_classes=["pc-empty-example"],
                )
                gr.HTML(
                    "<div class='pc-source-contract'><span class='pc-contract-number'>01</span><div><strong>Observed colors</strong><p>Five colors from the photo, with their relative presence.</p></div></div>"
                    "<div class='pc-source-contract'><span class='pc-contract-number'>02</span><div><strong>Derived roles</strong><p>Neutral surfaces, restrained accents, and safe text pairings.</p></div></div>",
                    elem_classes=["pc-contract-list"],
                )

        with gr.Column(visible=False, elem_id="pc-results", elem_classes=["pc-results"] ) as result_panel:
            with gr.Row(elem_classes=["pc-results-heading"]):
                gr.Markdown("## Three directions, ready to download")
                start_over = gr.Button("Start over", variant="secondary", size="sm", visible=False, elem_id="pc-start-over")
            gallery = gr.Gallery(
                label="Three directions, ready to download",
                columns=3,
                height="auto",
                object_fit="cover",
                allow_preview=True,
                buttons=["download", "download_all", "fullscreen"],
                elem_id="pc-gallery",
                elem_classes=["pc-gallery"],
            )
            downloads = gr.File(label="Download all 3 PNGs", file_count="multiple", elem_id="pc-downloads", elem_classes=["pc-downloads"])
            with gr.Accordion("Color story", open=False, elem_id="pc-color-story", elem_classes=["pc-color-story"]):
                gr.Markdown("Observed in the photo, then translated into design roles.", elem_classes=["pc-story-helper"])
                story_html = gr.HTML(_color_story_html(""), elem_id="pc-story-swatches")
                status = gr.HTML("", elem_id="pc-status", elem_classes=["pc-status"])

        gr.HTML(
            f"<footer id='pc-privacy' class='pc-footer'><p><strong>Local by default.</strong> {html.escape(_PRIVACY_COPY)}</p>"
            "<p>Using <code>--share</code> creates a public link. Do not use it for sensitive images.</p></footer>"
        )

        image.change(
            image_state,
            inputs=image,
            outputs=[generate, empty_state, result_panel, start_over, gallery, downloads, story_html, status],
            show_progress="hidden",
        )
        # The transient loading label is part of the same event chain as the
        # unchanged three-value analysis contract.
        generate.click(mark_loading, outputs=generate, show_progress="hidden").then(
            callback,
            inputs=[image, object_choice, title, message],
            outputs=[gallery, downloads, story_html, status, result_panel, start_over],
            show_progress="minimal",
        ).then(mark_ready, outputs=generate, show_progress="hidden")
        start_over.click(
            reset_form,
            outputs=[image, object_choice, title, message, gallery, downloads, story_html, status, result_panel, start_over, generate, empty_state],
            show_progress="hidden",
        )
    demo.queue(max_size=queue_size, default_concurrency_limit=concurrency)
    # Keep these attributes inspectable for tests and use the exact same path
    # when main() launches the server with Gradio's allowed_paths guard.
    demo._palette_card_output_dir = resolved_output_dir
    demo._palette_card_allowed_paths = [str(resolved_output_dir)]
    demo._palette_card_object_model_ready = predictor is not None
    demo._palette_card_palette_model_ready = palette_predictor is not None
    demo._palette_card_css = _load_studio_css()
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
    demo.launch(share=args.share, allowed_paths=[str(output_dir)], css=_load_studio_css())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
