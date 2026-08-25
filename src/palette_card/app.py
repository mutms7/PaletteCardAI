"""Gradio application for PaletteCard AI.

The app intentionally works without a custom checkpoint. In Demo Mode the
object dropdown is the source of the label; Auto never invents a confidence
score. After training, restart the app and Auto will use the validated model.
"""

from __future__ import annotations

import ast
import base64
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .card import render_card_set, save_cards
from .config import CLASS_NAMES, Paths
from .model import load_checkpoint, predict, select_device
from .palette import contrast_ratio, derive_palette_roles, extract_palette
from .runtime import cleanup_generated_cards, validate_upload_image
from .design import oklch_to_srgb, srgb_to_oklch


_ASSET_DIR = Path(__file__).with_name("assets")
_DEFAULT_TITLE = "A little color for you"
_DEFAULT_MESSAGE = "Made from the colors in your photo."
_EMPTY_STATE_COPY = "Upload a photo to begin."
_PRIVACY_COPY = "Runs on this computer by default. Generated PNGs stay in the configured local output folder until you remove them."
_DEFAULT_STUDIO_THEME = {
    "canvas": "#fbf7eb",
    "surface": "#fffdf7",
    "surface-muted": "#f3eddf",
    "ink": "#292531",
    "muted": "#625b67",
    "rule": "#3a3440",
    "accent": "#e64955",
    "accent-soft": "#ffe4a8",
    "accent-contrast": "#ffffff",
    "secondary": "#377fca",
    "decorative": "#83ce78",
    "photo-accent": "#e64955",
    "photo-tint": "#ffe4a8",
}
_STUDIO_THEME_KEYS = tuple(_DEFAULT_STUDIO_THEME)


def _load_studio_css() -> str:
    """Load the studio stylesheet and embed its display font for offline use."""

    try:
        css = (_ASSET_DIR / "studio.css").read_text(encoding="utf-8")
        font = base64.b64encode((_ASSET_DIR / "Schoolbell-Regular.ttf").read_bytes()).decode("ascii")
        return css.replace("__SCHOOLBELL_FONT_DATA__", font)
    except OSError:  # pragma: no cover - source checkout always includes the asset
        return ""


def _load_studio_js() -> str:
    """Load the tiny progressive-enhancement theme animator."""

    try:
        return (_ASSET_DIR / "studio.js").read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - source checkout always includes the asset
        return ""


def _role_value(roles: object, name: str) -> object:
    if isinstance(roles, dict):
        return roles[name]
    return getattr(roles, name)


def _role_or(roles: object, name: str, fallback: object) -> object:
    """Read a semantic role while tolerating compact status-test mappings."""

    try:
        return _role_value(roles, name)
    except (AttributeError, KeyError):
        return fallback


def _role_rgb(value: object) -> tuple[int, int, int]:
    """Accept a DesignPalette role or its status-text hex representation."""

    if isinstance(value, str):
        match = re.fullmatch(r"#([0-9a-fA-F]{6})", value.strip())
        if not match:
            raise ValueError(f"Invalid role color: {value!r}")
        token = match.group(1)
        return tuple(int(token[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    rgb = getattr(value, "rgb", value)
    if len(rgb) != 3:
        raise ValueError("Role colors must have exactly three channels")
    return tuple(max(0, min(255, int(round(float(channel))))) for channel in rgb)  # type: ignore[return-value]


def _hex_rgb(value: object) -> str:
    return "#%02X%02X%02X" % _role_rgb(value)


def _derive_studio_tint(
    value: object,
    *,
    lightness_shift: float,
    chroma_scale: float,
    chroma_cap: float,
    lightness_floor: float = 0.06,
    lightness_ceiling: float = 0.97,
) -> str:
    """Make a restrained UI tint from an already-derived semantic role.

    The UI never invents a hue.  It moves the existing role in OKLCH and lets
    the shared design conversion helper gamut-map the result to sRGB.
    """

    lightness, chroma, hue = srgb_to_oklch(_role_rgb(value))
    # UI tints are always lifted toward a light floor.  A dark source accent
    # must not turn ``accent-soft`` into another dark surface just because its
    # source lightness happened to be above/below an arbitrary midpoint.
    target_lightness = max(
        lightness_floor,
        min(lightness_ceiling, lightness + lightness_shift),
    )
    target_chroma = min(max(0.0, chroma * chroma_scale), chroma_cap)
    return _hex_rgb(oklch_to_srgb((target_lightness, target_chroma, hue)))


def _derive_studio_role(
    value: object,
    *,
    lightness_shift: float = 0.0,
    chroma_scale: float = 1.0,
    chroma_cap: float = 0.025,
) -> str:
    """Round-trip a semantic role through OKLCH before exposing it to CSS.

    Canvas and surface are intentionally inherited from the design engine,
    rather than being hard-coded UI colors.  The explicit OKLCH round-trip
    keeps this presentation mapping on the same perceptual conversion path as
    the design roles and gives the tint/rule helpers one consistent contract.
    """

    return _derive_studio_tint(
        value,
        lightness_shift=lightness_shift,
        chroma_scale=chroma_scale,
        chroma_cap=chroma_cap,
        lightness_floor=0.06,
        lightness_ceiling=0.99,
    )


def _derive_studio_muted(value: object, surface: object) -> str:
    """Derive a subdued text token and keep it readable on the surface."""

    lightness, chroma, hue = srgb_to_oklch(_role_rgb(value))
    # Most on-surface roles are dark because the design engine targets a light
    # editorial canvas.  Keep muted text in a restrained mid-dark range even
    # if a future learned role supplies a light on-surface value.
    target_lightness = min(0.58, max(0.30, lightness + 0.18))
    target_chroma = min(max(0.0, chroma * 0.12), 0.035)
    surface_rgb = _role_rgb(surface)
    candidates = [
        target_lightness,
        *sorted(
            (0.08 + index * 0.03 for index in range(30)),
            key=lambda candidate: abs(candidate - target_lightness),
        ),
    ]
    for candidate_lightness in candidates:
        candidate = oklch_to_srgb((candidate_lightness, target_chroma, hue))
        if contrast_ratio(candidate, surface_rgb) >= 4.5:
            return _hex_rgb(candidate)
    # This is only reachable for an unusually dark surface with a constrained
    # gamut.  Preserve the readable semantic contract with a neutral fallback.
    return _hex_rgb((24, 24, 28) if contrast_ratio((24, 24, 28), surface_rgb) >= 4.5 else (240, 240, 244))


def derive_studio_theme(roles: object) -> dict[str, str]:
    """Map a derived DesignPalette to the CSS tokens used by the studio.

    ``roles`` may be a :class:`DesignPalette` or the safe hex mapping emitted
    in the status text.  Keeping this mapping here preserves analyze_image's
    established three-value contract while making the browser theme explicit.
    """

    background = _role_value(roles, "background")
    surface = _role_value(roles, "surface")
    accent = _role_value(roles, "accent")
    secondary = _role_value(roles, "secondary")
    on_surface = _role_or(roles, "on_surface", _role_or(roles, "text", "#171719"))
    on_accent = _role_or(roles, "on_accent", "#FFFFFF")

    # Keep the large surfaces neutral and photo-led, while still routing every
    # token through the shared perceptual helpers.  Accents/tints are derived
    # from semantic roles; no UI hue is introduced here.
    canvas = _derive_studio_role(background, chroma_scale=1.0, chroma_cap=0.025)
    surface_hex = _derive_studio_role(surface, chroma_scale=1.0, chroma_cap=0.025)
    accent_soft = _derive_studio_tint(
        accent,
        lightness_shift=0.30,
        chroma_scale=0.34,
        chroma_cap=0.085,
        lightness_floor=0.82,
    )
    rule = _derive_studio_tint(
        accent,
        lightness_shift=0.43,
        chroma_scale=0.16,
        chroma_cap=0.045,
        lightness_floor=0.78,
    )
    muted = _derive_studio_muted(on_surface, surface)
    surface_muted = _derive_studio_tint(
        accent,
        lightness_shift=0.22,
        chroma_scale=0.30,
        chroma_cap=0.06,
        lightness_floor=0.88,
    )
    decorative = _derive_studio_tint(
        secondary,
        lightness_shift=0.25,
        chroma_scale=0.28,
        chroma_cap=0.075,
        lightness_floor=0.82,
    )
    accent_hex = _hex_rgb(accent)
    soft_hex = accent_soft
    return {
        "canvas": canvas,
        "surface": surface_hex,
        "surface-muted": surface_muted,
        "ink": _hex_rgb(on_surface),
        "muted": muted,
        "rule": rule,
        "accent": accent_hex,
        "accent-soft": soft_hex,
        "accent-contrast": _hex_rgb(on_accent),
        "secondary": _hex_rgb(secondary),
        "decorative": decorative,
        "photo-accent": accent_hex,
        "photo-tint": soft_hex,
    }


def _roles_from_status(status: str) -> dict[str, object] | None:
    roles_line = next((line for line in status.splitlines() if line.startswith("Design roles (derived):")), "")
    if not roles_line:
        return None
    try:
        roles = ast.literal_eval(roles_line.split(":", 1)[1].strip())
    except (SyntaxError, ValueError):
        return None
    return roles if isinstance(roles, dict) else None


def _studio_theme_from_status(status: str) -> dict[str, str] | None:
    roles = _roles_from_status(status)
    if not roles:
        return None
    try:
        return derive_studio_theme(roles)
    except (KeyError, TypeError, ValueError):
        return None


def _theme_payload_html(theme: dict[str, str] | None = None) -> str:
    """Return a hidden DOM payload consumed by the local studio.js observer."""

    # The payload is an internal DOM bridge, but still validate it before it
    # reaches an attribute.  A malformed/partial theme resets to the known
    # defaults in studio.js instead of leaking arbitrary CSS values.
    candidate = theme or {}
    if set(candidate) != set(_STUDIO_THEME_KEYS) or any(
        not isinstance(candidate.get(key), str)
        or not re.fullmatch(r"#[0-9a-fA-F]{6}", candidate[key])
        for key in _STUDIO_THEME_KEYS
    ):
        candidate = {}
    payload = json.dumps(candidate, separators=(",", ":"))
    return (
        f'<div class="pc-theme-payload" hidden data-palette-card-theme="{html.escape(payload, quote=True)}" '
        'aria-hidden="true"></div>'
    )


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
            theme = _studio_theme_from_status(status)
            return (
                gallery,
                files,
                _color_story_html(status),
                status_html(status),
                gr.update(visible=True),
                gr.update(visible=True),
                _theme_payload_html(theme) if theme else gr.update(),
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
                gr.update(),
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
            _theme_payload_html(),
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
            gr.update(value="Draw my 3 cards →", interactive=False),
            gr.update(visible=True),
            _theme_payload_html(),
        )

    def mark_loading():
        return gr.update(value="Mixing your colors…", interactive=False)

    def mark_ready():
        return gr.update(value="Draw my 3 cards →", interactive=True)

    with gr.Blocks(title="PaletteCard AI") as demo:
        gr.HTML(
            "<header class='pc-header'><a class='pc-wordmark' href='#top' aria-label='PaletteCard AI home'>"
            "<span class='pc-logo-scribble' aria-hidden='true'>✦</span> PaletteCard <em>AI</em></a>"
            "<nav class='pc-nav' aria-label='Main navigation'><a href='#pc-create'>Create</a>"
            "<a href='#pc-how'>How it works</a><a href='#pc-model'>The models</a></nav>"
            "<div class='pc-header-meta'><span class='pc-local-badge'>● Local &amp; private</span></div></header>"
        )
        gr.HTML(
            "<main id='top' class='pc-main'><section class='pc-hero' aria-labelledby='pc-hero-title'>"
            "<div class='pc-hero-copy-wrap'><p class='pc-eyebrow'>A small color studio — made with real AI</p>"
            "<h1 id='pc-hero-title'>Your photo has a <span>color story.</span><br>Let's draw it out!</h1>"
            "<p class='pc-hero-copy'>Upload one favorite thing. PaletteCard recognizes it, learns its colors, and turns them into three downloadable greeting cards.</p>"
            "<a class='pc-jump-link' href='#pc-create'>Make a card <span aria-hidden='true'>↓</span></a></div>"
            "<div class='pc-hero-art' aria-hidden='true'><span class='pc-sun'>☀</span><span class='pc-flower'>✿</span>"
            "<div class='pc-paper-card'><span>one photo</span><b>→</b><span>three cards!</span></div>"
            "<span class='pc-pencil'>✎</span></div>"
            "</section></main>"
        )
        gr.HTML(
            "<section id='pc-how' class='pc-trail' aria-label='How PaletteCard works'>"
            "<div><span class='pc-trail-dot pc-dot-coral'>1</span><strong>Pick a photo</strong><small>Flower, heart, ring, cake, or balloon</small></div>"
            "<i aria-hidden='true'></i><div><span class='pc-trail-dot pc-dot-blue'>2</span><strong>AI finds its story</strong><small>Object + five observed colors</small></div>"
            "<i aria-hidden='true'></i><div><span class='pc-trail-dot pc-dot-green'>3</span><strong>Keep your cards</strong><small>Three full-size PNG designs</small></div></section>"
        )
        with gr.Row(elem_classes=["pc-mode-row"]):
            gr.HTML(f"<div id='pc-model' class='pc-mode-badge' role='status'><span class='pc-model-icon' aria-hidden='true'>✦</span><div><strong>{html.escape(_mode_badge(predictor))}</strong><span>{html.escape(mode_message)}</span></div></div>")
            gr.HTML(f"<div class='pc-palette-mode'><span class='pc-model-icon' aria-hidden='true'>●</span><div><strong>Color model ready</strong><span>{html.escape(palette_mode_message)}</span></div></div>")

        with gr.Row(elem_id="pc-create", elem_classes=["pc-studio-grid"]):
            with gr.Column(scale=5, elem_classes=["pc-input-rail"]):
                gr.Markdown("### 1. Choose your picture", elem_classes=["pc-section-kicker"])
                image = gr.Image(
                    type="pil",
                    sources=["upload", "webcam", "clipboard"],
                    label="Drop a photo of one centered object",
                    height=360,
                    elem_id="pc-image",
                    elem_classes=["pc-image-input"],
                )
                gr.Markdown("Flower, heart, ring, cake, or balloon · JPG, PNG, or WebP", elem_classes=["pc-upload-hint"])
                gr.Markdown("Tip: a bright, centered subject with a simple background works best.", elem_classes=["pc-source-note"])
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
                generate = gr.Button("Draw my 3 cards →", variant="primary", size="lg", interactive=False, elem_id="pc-generate", elem_classes=["pc-cta"])
                gr.Markdown("Processing stays on this computer. No network request is needed to make the cards.", elem_classes=["pc-local-note"])

            with gr.Column(scale=4, elem_classes=["pc-source-rail"]):
                gr.HTML(
                    "<div class='pc-source-rail-heading'><p class='pc-section-kicker'>2. Watch the color magic</p>"
                    "<h2>Your palette will pop up here!</h2>"
                    "<p>The AI looks for the object, observes five colors, then gives each one a useful job without sacrificing readability.</p></div>"
                )
                empty_state = gr.Markdown(_EMPTY_STATE_COPY, elem_id="pc-empty-state", elem_classes=["pc-empty-state"])
                gr.HTML(
                    "<div class='pc-empty-example'><span aria-hidden='true'>↖</span> Add a photo and this blank page becomes your color story.</div>",
                    elem_classes=["pc-empty-example"],
                )
                gr.HTML(
                    "<div class='pc-source-contract'><span class='pc-contract-number'>✿</span><div><strong>Colors we spot</strong><p>Five colors from the photo and how much each one appears.</p></div></div>"
                    "<div class='pc-source-contract'><span class='pc-contract-number'>✎</span><div><strong>Colors we design with</strong><p>Calm backgrounds, joyful accents, and easy-to-read words.</p></div></div>",
                    elem_classes=["pc-contract-list"],
                )

        with gr.Column(visible=False, elem_id="pc-results", elem_classes=["pc-results"] ) as result_panel:
            with gr.Row(elem_classes=["pc-results-heading"]):
                gr.Markdown("## 3. Ta-da! Your cards are ready")
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
            theme_payload = gr.HTML(
                _theme_payload_html(),
                elem_id="pc-theme-payload",
                elem_classes=["pc-theme-payload"],
            )

        gr.HTML(
            f"<footer id='pc-privacy' class='pc-footer'><div><strong>PaletteCard AI</strong><span>Made with pixels, pencils, and two tiny neural networks.</span></div>"
            f"<p><strong>Private by default.</strong> {html.escape(_PRIVACY_COPY)} Using <code>--share</code> creates a public link.</p></footer>"
        )

        image.change(
            image_state,
            inputs=image,
            outputs=[generate, empty_state, result_panel, start_over, gallery, downloads, story_html, status, theme_payload],
            show_progress="hidden",
        )
        # The transient loading label is part of the same event chain as the
        # unchanged three-value analysis contract.
        generate.click(mark_loading, outputs=generate, show_progress="hidden").then(
            callback,
            inputs=[image, object_choice, title, message],
            outputs=[gallery, downloads, story_html, status, result_panel, start_over, theme_payload],
            show_progress="minimal",
        ).then(mark_ready, outputs=generate, show_progress="hidden")
        start_over.click(
            reset_form,
            outputs=[image, object_choice, title, message, gallery, downloads, story_html, status, result_panel, start_over, generate, empty_state, theme_payload],
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
    demo._palette_card_js = _load_studio_js()
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
    demo.launch(share=args.share, allowed_paths=[str(output_dir)], css=_load_studio_css(), js=_load_studio_js())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
