const DEFAULT_PALETTE = ["#f7f3ed", "#8d1738", "#c79a50", "#ead8d2", "#3e3034"];
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_FILE_SIZE = 4 * 1024 * 1024;

const fileInput = document.querySelector("#photo-input");
const dropzone = document.querySelector("#dropzone");
const uploadEmpty = document.querySelector("#upload-empty");
const uploadPreview = document.querySelector("#upload-preview");
const sourceImage = document.querySelector("#source-image");
const storyImage = document.querySelector("#story-image");
const sourcePlaceholder = document.querySelector(".source-placeholder");
const removePhoto = document.querySelector("#remove-photo");
const fileName = document.querySelector("#file-name");
const photoError = document.querySelector("#photo-error");
const generateButton = document.querySelector("#generate-button");
const generateLabel = document.querySelector("#generate-label");
const actionStatus = document.querySelector("#action-status");
const results = document.querySelector("#results");
const resultStatusTitle = document.querySelector("#result-status-title");
const resultStatusCopy = document.querySelector("#result-status-copy");
const form = document.querySelector("#studio-form");
const titleInput = document.querySelector("#card-title");
const messageInput = document.querySelector("#card-message");
const swatches = [...document.querySelectorAll("#swatches span")];
const paletteBars = [...document.querySelectorAll(".palette-bar span")];
const miniSwatches = [...document.querySelectorAll(".mini-swatches span")];
const cardImages = [...document.querySelectorAll(".card-source-image")];
const modeStatus = document.querySelector("#mode-status");
const modelBadge = document.querySelector("#model-badge");
const cardPreviews = [...document.querySelectorAll(".card-preview")];
const downloadLinks = [...document.querySelectorAll(".download-card")];

let currentUrl = null;
let currentFile = null;
let currentPalette = [...DEFAULT_PALETTE];
let generationTimer = null;
let isGenerating = false;
let modelsReady = false;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function rgbToHex([red, green, blue]) {
  return `#${[red, green, blue].map((channel) => clamp(Math.round(channel), 0, 255).toString(16).padStart(2, "0")).join("")}`;
}

function hexToRgb(hex) {
  return [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16));
}

function mix(hex, target, amount) {
  const source = hexToRgb(hex);
  const destination = hexToRgb(target);
  return rgbToHex(source.map((channel, index) => channel + (destination[index] - channel) * amount));
}

function relativeLuminance(hex) {
  const channels = hexToRgb(hex).map((channel) => channel / 255).map((channel) => (
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return (0.2126 * channels[0]) + (0.7152 * channels[1]) + (0.0722 * channels[2]);
}

function saturation([red, green, blue]) {
  const highest = Math.max(red, green, blue);
  const lowest = Math.min(red, green, blue);
  return highest === 0 ? 0 : (highest - lowest) / highest;
}

function colorDistance(first, second) {
  return Math.sqrt(first.reduce((total, channel, index) => total + (channel - second[index]) ** 2, 0));
}

function prefersReducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function pulseTheme() {
  if (prefersReducedMotion()) return;
  document.body.classList.remove("theme-transition");
  // Force a reflow so repeated palette generations retrigger the wash.
  void document.body.offsetWidth;
  document.body.classList.add("theme-transition");
  window.clearTimeout(pulseTheme.timeout);
  pulseTheme.timeout = window.setTimeout(() => document.body.classList.remove("theme-transition"), 760);
}

function samplePalette(image) {
  const canvas = document.createElement("canvas");
  canvas.width = 72;
  canvas.height = 72;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const bins = new Map();

  for (let index = 0; index < pixels.length; index += 16) {
    if (pixels[index + 3] < 180) continue;
    const color = [pixels[index], pixels[index + 1], pixels[index + 2]];
    const key = color.map((channel) => channel >> 5).join("-");
    const entry = bins.get(key) || { count: 0, sums: [0, 0, 0] };
    entry.count += 1;
    color.forEach((channel, channelIndex) => { entry.sums[channelIndex] += channel; });
    bins.set(key, entry);
  }

  const candidates = [...bins.values()]
    .map((entry) => ({ count: entry.count, rgb: entry.sums.map((total) => total / entry.count) }))
    .sort((first, second) => second.count - first.count);

  const chosen = [];
  for (const candidate of candidates) {
    if (chosen.every((color) => colorDistance(color, candidate.rgb) > 58)) chosen.push(candidate.rgb);
    if (chosen.length === 5) break;
  }
  while (chosen.length < 5) chosen.push(hexToRgb(DEFAULT_PALETTE[chosen.length]));
  return chosen.map(rgbToHex);
}

function updateState(state, message) {
  actionStatus.dataset.state = state;
  actionStatus.textContent = message;
}

function updateTheme(palette, animate = false) {
  const colors = palette.map(hexToRgb);
  const accentIndex = colors.reduce((bestIndex, color, index) => (
    saturation(color) > saturation(colors[bestIndex]) ? index : bestIndex
  ), 0);
  const sourceAccent = palette[accentIndex];
  const accent = relativeLuminance(sourceAccent) > 0.44 ? mix(sourceAccent, "#171719", 0.38) : sourceAccent;
  const secondary = palette.find((color) => color !== sourceAccent && colorDistance(hexToRgb(color), hexToRgb(sourceAccent)) > 75) || palette[1];
  const deep = rgbToHex(colors.reduce((darkest, color) => (
    color.reduce((sum, channel) => sum + channel, 0) < darkest.reduce((sum, channel) => sum + channel, 0) ? color : darkest
  )));
  const tint = mix(sourceAccent, "#ffffff", 0.78);
  const canvas = mix("#f4f0e9", tint, 0.24);
  const surface = mix("#fffdf9", tint, 0.13);
  const surfaceMuted = mix("#f3eee7", tint, 0.34);
  const rule = mix("#d9d2c8", sourceAccent, 0.16);
  const accentContrast = relativeLuminance(accent) < 0.43 ? "#ffffff" : "#171719";
  const root = document.documentElement;

  [["--canvas", canvas], ["--surface", surface], ["--surface-muted", surfaceMuted], ["--rule", rule],
    ["--accent", accent], ["--accent-soft", tint], ["--accent-contrast", accentContrast],
    ["--secondary", secondary], ["--deep", deep], ["--photo-accent", sourceAccent], ["--photo-tint", tint]]
    .forEach(([name, value]) => root.style.setProperty(name, value));

  document.querySelector("#accent-role").textContent = sourceAccent.toUpperCase();
  document.querySelector("#canvas-role").textContent = `${canvas.toUpperCase()} tint`;
  document.querySelector("#approach-role").textContent = "Source-led contrast and hierarchy";
  if (animate) pulseTheme();
}

function updatePalette(palette, animate = false) {
  currentPalette = [...palette];
  swatches.forEach((swatch, index) => {
    swatch.style.setProperty("--swatch", palette[index]);
    swatch.title = palette[index].toUpperCase();
    swatch.setAttribute("aria-label", `Observed color ${index + 1}: ${palette[index].toUpperCase()}`);
  });
  [...paletteBars, ...miniSwatches].forEach((shape, index) => {
    shape.style.background = palette[index % palette.length];
  });
  document.querySelector("#story-count").textContent = "5 / 5";
  updateTheme(palette, animate);
}

function selectedObject() {
  return document.querySelector('input[name="object"]:checked')?.value || "flower";
}

function updateCardCopy() {
  const title = titleInput.value.trim() || "A little color for you";
  const message = messageInput.value.trim() || "Made from the colors in your photo.";
  const objectName = selectedObject() === "auto" ? "OBJECT" : selectedObject().toUpperCase();
  document.querySelectorAll(".card-title-output").forEach((node) => { node.textContent = title; });
  document.querySelectorAll(".card-message-output").forEach((node) => { node.textContent = message; });
  document.querySelectorAll(".card-object").forEach((node) => { node.textContent = objectName; });
}

function resetPhoto({ errorMessage = "" } = {}) {
  window.clearTimeout(generationTimer);
  generationTimer = null;
  isGenerating = false;
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = null;
  currentFile = null;
  fileInput.value = "";
  sourceImage.removeAttribute("src");
  storyImage.removeAttribute("src");
  cardImages.forEach((image) => image.removeAttribute("src"));
  cardPreviews.forEach((preview) => {
    preview.classList.remove("has-generated");
    preview.querySelector(".generated-card-image")?.remove();
  });
  downloadLinks.forEach((link) => {
    link.hidden = true;
    link.removeAttribute("href");
    link.removeAttribute("download");
  });
  uploadEmpty.hidden = false;
  uploadPreview.hidden = true;
  storyImage.hidden = true;
  sourcePlaceholder.hidden = false;
  generateButton.disabled = true;
  generateButton.setAttribute("aria-busy", "false");
  generateLabel.textContent = "Draw my 3 cards";
  results.hidden = true;
  photoError.textContent = errorMessage;
  updateState(errorMessage ? "invalid" : "empty", errorMessage || "Add a photo to begin. AI processing starts only when you make the cards.");
  updatePalette(DEFAULT_PALETTE, false);
  document.querySelector("#story-count").textContent = "0 / 5";
  document.querySelector("#accent-role").textContent = "Waiting for photo";
  document.querySelector("#canvas-role").textContent = "Warm neutral";
  document.querySelector("#approach-role").textContent = "Quiet surfaces, restrained color";
  resultStatusTitle.textContent = "Your three cards are ready.";
  resultStatusCopy.textContent = "5 colors observed · 3 directions composed.";
}

function loadPhoto(file) {
  photoError.textContent = "";
  if (!file || !ALLOWED_TYPES.has(file.type)) {
    if (!currentUrl) updateState("invalid", "Choose a JPG, PNG, or WebP image.");
    photoError.textContent = "Choose a JPG, PNG, or WebP image.";
    return;
  }
  if (file.size > MAX_FILE_SIZE) {
    if (!currentUrl) updateState("invalid", "Choose an image smaller than 4 MB.");
    photoError.textContent = "Choose an image smaller than 4 MB.";
    return;
  }
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentFile = file;
  currentUrl = URL.createObjectURL(file);
  const probe = new Image();
  probe.onload = () => {
    sourceImage.src = currentUrl;
    sourceImage.alt = `Selected source photo: ${file.name}`;
    storyImage.src = currentUrl;
    cardImages.forEach((image) => { image.src = currentUrl; });
    fileName.textContent = file.name;
    uploadEmpty.hidden = true;
    uploadPreview.hidden = false;
    sourcePlaceholder.hidden = true;
    storyImage.hidden = false;
    generateButton.disabled = !modelsReady;
    updateState(modelsReady ? "ready" : "loading", modelsReady ? "Ready to make a color story with both AI models." : "Waiting for the AI models to finish starting…");
    updatePalette(samplePalette(probe), true);
    updateCardCopy();
  };
  probe.onerror = () => resetPhoto({ errorMessage: "We couldn't read that image. Try another file." });
  probe.src = currentUrl;
}

fileInput.addEventListener("change", () => loadPhoto(fileInput.files[0]));
removePhoto.addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  resetPhoto();
});
titleInput.addEventListener("input", updateCardCopy);
messageInput.addEventListener("input", updateCardCopy);
document.querySelectorAll('input[name="object"]').forEach((input) => input.addEventListener("change", () => {
  updateCardCopy();
  modeStatus.innerHTML = input.value === "auto"
    ? "<strong>Model Mode</strong> · the classifier will identify the object."
    : "<strong>Manual override</strong> · your selection replaces the classifier label.";
}));

["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.add("is-dragging");
}));
["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.remove("is-dragging");
}));
dropzone.addEventListener("drop", (event) => loadPhoto(event.dataTransfer.files[0]));

function applyGeneratedCards(payload) {
  updatePalette(payload.source_colors?.length === 5 ? payload.source_colors : currentPalette, true);
  const roles = payload.design_roles || {};
  document.querySelector("#accent-role").textContent = roles.accent || currentPalette[0];
  document.querySelector("#canvas-role").textContent = roles.background || "AI-selected neutral";
  document.querySelector("#approach-role").textContent = roles.harmony || "Learned roles with accessibility guardrails";
  payload.cards.forEach((card, index) => {
    const preview = cardPreviews[index];
    preview.querySelector(".generated-card-image")?.remove();
    const image = document.createElement("img");
    image.className = "generated-card-image";
    image.src = card.data_url;
    image.alt = `AI-generated PaletteCard direction ${index + 1}`;
    preview.append(image);
    preview.classList.add("has-generated");
    const link = downloadLinks[index];
    link.href = card.data_url;
    link.download = card.filename;
    link.hidden = false;
  });
  results.hidden = false;
  resultStatusTitle.textContent = "Your three AI-generated cards are ready.";
  resultStatusCopy.textContent = `${payload.recognition} · 5 colors observed · 3 directions composed.`;
  updateState("success", "Your cards are ready. Choose any direction to download it.");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentFile || isGenerating) return;
  isGenerating = true;
  generateButton.disabled = true;
  generateButton.setAttribute("aria-busy", "true");
  generateLabel.textContent = "Mixing your colors…";
  updateState("loading", "The classifier and palette model are mixing your colors… A cold start can take up to a minute.");
  try {
    const body = new FormData();
    body.append("file", currentFile, currentFile.name);
    body.append("object_choice", selectedObject());
    body.append("title", titleInput.value.trim());
    body.append("message", messageInput.value.trim());
    const response = await fetch("/api/generate", { method: "POST", body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "The AI service could not generate the cards.");
    applyGeneratedCards(payload);
  } catch (error) {
    results.hidden = true;
    updateState("error", error.message || "We couldn’t create the cards. Try again with a smaller image.");
  } finally {
    generateLabel.textContent = "Draw my 3 cards";
    generateButton.disabled = false;
    generateButton.setAttribute("aria-busy", "false");
    isGenerating = false;
    if (!results.hidden) {
      results.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
    }
  }
});

document.querySelector("#start-over").addEventListener("click", () => {
  resetPhoto();
  document.querySelector("#studio").scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
});

async function checkModelReadiness() {
  try {
    const response = await fetch("/readyz", { headers: { Accept: "application/json" } });
    const readiness = await response.json();
    if (response.ok && readiness.object_model?.loaded && readiness.palette_model?.loaded) {
      modelsReady = true;
      modelBadge.innerHTML = '<span aria-hidden="true"></span> AI ready';
      modeStatus.innerHTML = "<strong>Model Mode</strong> · Auto classification and learned palette roles are ready.";
      generateButton.disabled = !currentFile;
      return;
    }
    throw new Error("models unavailable");
  } catch (_error) {
    modelsReady = false;
    modelBadge.innerHTML = '<span aria-hidden="true"></span> AI unavailable';
    modeStatus.innerHTML = "<strong>AI unavailable</strong> · try again shortly.";
    generateButton.disabled = true;
  }
}

updateCardCopy();
resetPhoto();
checkModelReadiness();
