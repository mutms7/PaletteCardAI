(function () {
  "use strict";

  // Keep this bridge dependency-free: Gradio updates the hidden HTML payload
  // after generation, and the observer applies the same semantic tokens to
  // the document root so the whole studio responds to the source photo.
  var DEFAULT_THEME = {
    canvas: "#f7f4ef",
    surface: "#fffdf9",
    "surface-muted": "#f3eee7",
    ink: "#171719",
    muted: "#6f6b66",
    rule: "#ded8cf",
    accent: "#8d1738",
    "accent-soft": "#ead8d2",
    "accent-contrast": "#ffffff",
    secondary: "#344992",
    decorative: "#ead8d2",
    "photo-accent": "#8d1738",
    "photo-tint": "#ead8d2"
  };
  var THEME_KEYS = Object.keys(DEFAULT_THEME);
  var HEX_COLOR = /^#[0-9a-f]{6}$/i;
  var TRANSITION_CLASS = "pc-theme-transition";
  var lastPayload = null;

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function readPayload() {
    var node = document.querySelector("[data-palette-card-theme]");
    if (!node) return null;
    try {
      var value = JSON.parse(node.getAttribute("data-palette-card-theme") || "{}");
      if (!value || typeof value !== "object" || Array.isArray(value)) return {};
      return value;
    } catch (_error) {
      return {};
    }
  }

  function validTheme(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    return THEME_KEYS.every(function (key) {
      return typeof value[key] === "string" && HEX_COLOR.test(value[key]);
    });
  }

  function setTransitionClass(active) {
    var body = document.body;
    if (!body) return;
    body.classList.remove(TRANSITION_CLASS);
    if (!active || reducedMotion()) return;
    // Force a reflow so repeated generations retrigger the short color wash.
    void body.offsetWidth;
    body.classList.add(TRANSITION_CLASS);
    window.setTimeout(function () {
      body.classList.remove(TRANSITION_CLASS);
    }, 760);
  }

  function applyTheme(value) {
    var theme = validTheme(value) ? value : DEFAULT_THEME;
    var root = document.documentElement;
    THEME_KEYS.forEach(function (key) {
      root.style.setProperty("--" + key, theme[key]);
    });
    setTransitionClass(theme !== DEFAULT_THEME && !reducedMotion());
  }

  function syncTheme() {
    var payload = readPayload();
    if (payload === null) return;
    var serialized = JSON.stringify(payload);
    if (serialized === lastPayload) return;
    lastPayload = serialized;
    // An empty payload is the reset signal emitted when the image is removed
    // or the user presses Start over. Invalid payloads also fail closed.
    applyTheme(payload);
  }

  function start() {
    syncTheme();
    if (!document.body || typeof MutationObserver === "undefined") return;
    var observer = new MutationObserver(syncTheme);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-palette-card-theme"] });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
