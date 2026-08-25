const DEFAULT_PALETTE = ["#f7e6d4", "#e64955", "#d6a343", "#73a9dc", "#79bd72"];
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_FILE_SIZE = 4 * 1024 * 1024;
const ARTBOARD_WIDTH = 900;
const ARTBOARD_HEIGHT = 1200;

const qs = (selector, parent = document) => parent.querySelector(selector);
const qsa = (selector, parent = document) => [...parent.querySelectorAll(selector)];

const fileInput = qs("#photo-input");
const dropzone = qs("#dropzone");
const uploadEmpty = qs("#upload-empty");
const uploadPreview = qs("#upload-preview");
const sourceImage = qs("#source-image");
const storyImage = qs("#story-image");
const sourcePlaceholder = qs(".source-placeholder");
const removePhotoButton = qs("#remove-photo");
const fileName = qs("#file-name");
const photoError = qs("#photo-error");
const generateButton = qs("#generate-button");
const generateLabel = qs("#generate-label");
const actionStatus = qs("#action-status");
const results = qs("#results");
const resultStatusTitle = qs("#result-status-title");
const resultStatusCopy = qs("#result-status-copy");
const form = qs("#studio-form");
const titleInput = qs("#card-title");
const messageInput = qs("#card-message");
const modeStatus = qs("#mode-status");
const modelBadge = qs("#model-badge");
const storyCount = qs("#story-count");
const swatches = qs("#swatches");
const toast = qs("#toast");

let currentUrl = null;
let currentFile = null;
let currentPalette = [...DEFAULT_PALETTE];
let customPaintColors = [];
let generatedCards = [];
let modelsReady = false;
let isGenerating = false;
let toastTimer = null;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function rgbToHex([red, green, blue]) {
  return `#${[red, green, blue].map((channel) => clamp(Math.round(channel), 0, 255).toString(16).padStart(2, "0")).join("")}`;
}

function hexToRgb(hex) {
  const clean = hex.replace("#", "");
  return [0, 2, 4].map((index) => Number.parseInt(clean.slice(index, index + 2), 16));
}

function rgbToHsl([red, green, blue]) {
  const [r, g, b] = [red, green, blue].map((value) => value / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  let hue = 0;
  let saturation = 0;
  const lightness = (max + min) / 2;
  if (max !== min) {
    const delta = max - min;
    saturation = lightness > .5 ? delta / (2 - max - min) : delta / (max + min);
    if (max === r) hue = ((g - b) / delta + (g < b ? 6 : 0)) / 6;
    if (max === g) hue = ((b - r) / delta + 2) / 6;
    if (max === b) hue = ((r - g) / delta + 4) / 6;
  }
  return [hue * 360, saturation * 100, lightness * 100];
}

function hslToHex([hue, saturation, lightness]) {
  const h = ((hue % 360) + 360) % 360 / 360;
  const s = clamp(saturation, 0, 100) / 100;
  const l = clamp(lightness, 0, 100) / 100;
  if (s === 0) return rgbToHex([l * 255, l * 255, l * 255]);
  const q = l < .5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (offset) => {
    let t = h + offset;
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return rgbToHex([channel(1 / 3) * 255, channel(0) * 255, channel(-1 / 3) * 255]);
}

function mix(hex, target, amount) {
  const source = hexToRgb(hex);
  const destination = hexToRgb(target);
  return rgbToHex(source.map((channel, index) => channel + (destination[index] - channel) * amount));
}

function colorDistance(first, second) {
  return Math.sqrt(first.reduce((total, channel, index) => total + (channel - second[index]) ** 2, 0));
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3200);
}

function updateActionState(state, message) {
  actionStatus.dataset.state = state;
  actionStatus.textContent = message;
}

function setRoute(route, scroll = true) {
  const allowed = new Set(["create", "how", "editor", "privacy"]);
  const next = allowed.has(route) ? route : "create";
  qsa("[data-view]").forEach((view) => { view.hidden = view.dataset.view !== next; });
  qsa(".main-nav [data-route]").forEach((link) => {
    const current = link.dataset.route === next;
    link.classList.toggle("is-current", current);
    if (current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.title = `${next === "create" ? "Make a color story" : next === "how" ? "How the AI works" : next === "editor" ? "Paint your card" : "Privacy"} | PaletteCard AI`;
  if (scroll) window.scrollTo({ top: 0, behavior: "instant" });
}

function routeFromHash() {
  const hash = window.location.hash.replace("#", "");
  if (hash === "studio" || hash === "results") {
    setRoute("create", false);
    requestAnimationFrame(() => qs(`#${hash}`)?.scrollIntoView());
    return;
  }
  setRoute(hash || "create");
}

window.addEventListener("hashchange", routeFromHash);
routeFromHash();

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

function renderSwatches() {
  swatches.replaceChildren(...currentPalette.slice(0, 5).map((color) => {
    const swatch = document.createElement("span");
    swatch.style.setProperty("--swatch", color);
    swatch.title = color.toUpperCase();
    return swatch;
  }));
  storyCount.textContent = currentFile ? "5 / 5" : "0 / 5";
  buildPaintWells();
}

function updateColorCopy(roles = {}) {
  qs("#canvas-role").textContent = roles.background || "Warm white";
  qs("#accent-role").textContent = roles.accent || (currentFile ? currentPalette[1].toUpperCase() : "Waiting for a photo");
  qs("#approach-role").textContent = roles.rationale || (currentFile ? "Pulled from your photo" : "Soft and simple");
}

function validateFile(file) {
  if (!file) return "Choose a photo first.";
  if (!ALLOWED_TYPES.has(file.type)) return "Choose a JPG, PNG, or WebP image.";
  if (file.size > MAX_FILE_SIZE) return "Choose an image smaller than 4 MB.";
  return "";
}

function clearPhoto() {
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = null;
  currentFile = null;
  fileInput.value = "";
  sourceImage.removeAttribute("src");
  storyImage.removeAttribute("src");
  uploadEmpty.hidden = false;
  uploadPreview.hidden = true;
  storyImage.hidden = true;
  sourcePlaceholder.hidden = false;
  generateButton.disabled = true;
  currentPalette = [...DEFAULT_PALETTE];
  renderSwatches();
  updateColorCopy();
  updateActionState("idle", "Add a photo, then I can start.");
  photoError.textContent = "";
}

function setPhoto(file) {
  const error = validateFile(file);
  if (error) {
    photoError.textContent = error;
    return;
  }
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentFile = file;
  currentUrl = URL.createObjectURL(file);
  sourceImage.src = currentUrl;
  storyImage.src = currentUrl;
  sourceImage.onload = () => {
    try {
      currentPalette = samplePalette(sourceImage);
      renderSwatches();
      updateColorCopy();
    } catch {
      currentPalette = [...DEFAULT_PALETTE];
      renderSwatches();
    }
  };
  fileName.textContent = file.name;
  uploadEmpty.hidden = true;
  uploadPreview.hidden = false;
  sourcePlaceholder.hidden = true;
  storyImage.hidden = false;
  photoError.textContent = "";
  generateButton.disabled = !modelsReady;
  updateActionState(modelsReady ? "ready" : "loading", modelsReady ? "Your photo is ready. Make the covers when you like." : "Your photo is ready. I’m still waiting for the models.");
}

fileInput.addEventListener("change", () => setPhoto(fileInput.files[0]));
removePhotoButton.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); clearPhoto(); });
["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.add("is-dragging"); }));
["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.remove("is-dragging"); }));
dropzone.addEventListener("drop", (event) => setPhoto(event.dataTransfer.files[0]));

async function checkModels() {
  try {
    const response = await fetch("/readyz", { cache: "no-store" });
    const status = await response.json();
    modelsReady = response.ok && status.object_model?.loaded && status.palette_model?.loaded;
  } catch {
    modelsReady = false;
  }
  modelBadge.classList.toggle("is-ready", modelsReady);
  modelBadge.classList.toggle("is-error", !modelsReady);
  qs("span", modelBadge).textContent = modelsReady ? "AI ready" : "AI is resting";
  modeStatus.textContent = modelsReady ? "Both small models are ready." : "The models haven’t loaded yet. Try again in a moment.";
  generateButton.disabled = !currentFile || !modelsReady || isGenerating;
  if (currentFile && modelsReady) updateActionState("ready", "Your photo is ready. Make the covers when you like.");
}

function errorMessage(payload, response) {
  if (typeof payload?.detail === "string") return payload.detail;
  return response.status === 503 ? "The models are still waking up. Try again in a moment." : "I couldn’t make the covers this time. Try the photo again.";
}

function showGeneratedCards(payload) {
  generatedCards = payload.cards || [];
  qsa(".card-preview").forEach((preview, index) => {
    preview.replaceChildren();
    const image = document.createElement("img");
    image.className = "generated-card-image";
    image.src = generatedCards[index].data_url;
    image.alt = `Generated ${index + 1} card cover`;
    preview.append(image);
  });
  qsa(".download-card").forEach((link, index) => {
    link.hidden = false;
    link.href = generatedCards[index].data_url;
    link.download = generatedCards[index].filename;
  });
  if (payload.source_colors?.length === 5) {
    currentPalette = payload.source_colors;
    renderSwatches();
  }
  updateColorCopy(payload.design_roles || {});
  const recognition = (payload.recognition || "Object found").replace("Object: ", "").replace(/[.]+$/, "");
  resultStatusTitle.textContent = "Your three covers are ready.";
  resultStatusCopy.textContent = `${recognition}. I found five colors and kept your photo in the middle.`;
  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentFile || !modelsReady || isGenerating) return;
  isGenerating = true;
  generateButton.disabled = true;
  generateButton.setAttribute("aria-busy", "true");
  generateLabel.textContent = "The helpers are working...";
  updateActionState("loading", "First I’ll look at the object. Then I’ll sort its colors.");
  const data = new FormData();
  data.append("file", currentFile, currentFile.name);
  data.append("object_choice", qs('input[name="object"]:checked').value);
  data.append("title", titleInput.value.trim());
  data.append("message", messageInput.value.trim());
  try {
    const response = await fetch("/api/generate", { method: "POST", body: data });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(payload, response));
    showGeneratedCards(payload);
    updateActionState("ready", "Done. Pick a cover below, or download all three.");
  } catch (error) {
    updateActionState("error", error.message);
  } finally {
    isGenerating = false;
    generateButton.removeAttribute("aria-busy");
    generateButton.disabled = !currentFile || !modelsReady;
    generateLabel.textContent = "Make my 3 covers";
  }
});

qs("#start-over").addEventListener("click", () => {
  results.hidden = true;
  generatedCards = [];
  clearPhoto();
  qs("#studio").scrollIntoView({ behavior: "smooth" });
});

renderSwatches();
checkModels();

const artboard = qs("#artboard");
const displayContext = artboard.getContext("2d");
const layersList = qs("#layers-list");
const brushSize = qs("#brush-size");
const brushOpacity = qs("#brush-opacity");
const sizeOutput = qs("#size-output");
const opacityOutput = qs("#opacity-output");
const colorDot = qs("#brush-color-dot");
const colorLabel = qs("#brush-color-label");
const toolbox = qs(".toolbox");
const textControls = qs("#text-controls");
const textContent = qs("#text-content");
const textFont = qs("#text-font");
const textSize = qs("#text-size");
const textWidth = qs("#text-width");
const textRotation = qs("#text-rotation");
const textColor = qs("#text-color");
const textX = qs("#text-x");
const textY = qs("#text-y");
const TEXT_FONTS = ["Schoolbell", "Fredoka", "Comic Neue", "Patrick Hand"];
let layers = [];
let selectedLayerId = null;
let layerCounter = 0;
let currentTool = "brush";
let brushColor = "#e64955";
let drawing = false;
let draggingText = false;
let textDragOffset = null;
let textEditBefore = null;
let previousPoint = null;
let undoStack = [];
let redoStack = [];

function makeLayer(name, locked = false, type = locked ? "base" : "paint") {
  const canvas = document.createElement("canvas");
  canvas.width = ARTBOARD_WIDTH;
  canvas.height = ARTBOARD_HEIGHT;
  return { id: `layer-${++layerCounter}`, name, canvas, visible: true, locked, type };
}

function makeTextLayer(name = "Words", x = ARTBOARD_WIDTH / 2, y = 260) {
  return {
    ...makeLayer(name, false, "text"),
    text: "Your words here",
    fontFamily: "Schoolbell",
    fontSize: 72,
    color: "#292531",
    align: "center",
    width: 560,
    rotation: 0,
    x,
    y,
    bounds: { width: 560, height: 90 },
  };
}

function selectedLayer() {
  return layers.find((layer) => layer.id === selectedLayerId);
}

function blankBase() {
  const base = makeLayer("Paper and photo", true);
  const context = base.canvas.getContext("2d");
  context.fillStyle = "#fffdf6";
  context.fillRect(0, 0, ARTBOARD_WIDTH, ARTBOARD_HEIGHT);
  context.strokeStyle = "#292531";
  context.lineWidth = 4;
  context.setLineDash([14, 12]);
  context.strokeRect(44, 44, ARTBOARD_WIDTH - 88, ARTBOARD_HEIGHT - 88);
  context.setLineDash([]);
  context.fillStyle = "#665e68";
  context.font = "42px Schoolbell";
  context.textAlign = "center";
  context.fillText("A blank cover for your idea", ARTBOARD_WIDTH / 2, ARTBOARD_HEIGHT / 2);
  return base;
}

function wrapText(context, text, maxWidth) {
  const paragraphs = String(text || " ").split("\n");
  const lines = [];
  paragraphs.forEach((paragraph) => {
    const words = paragraph.split(/\s+/).filter(Boolean);
    if (!words.length) {
      lines.push("");
      return;
    }
    let line = "";
    words.forEach((word) => {
      const candidate = line ? `${line} ${word}` : word;
      if (line && context.measureText(candidate).width > maxWidth) {
        lines.push(line);
        line = word;
      } else {
        line = candidate;
      }
    });
    lines.push(line);
  });
  return lines.slice(0, 8);
}

function textLayout(context, layer) {
  context.font = `${layer.fontSize}px "${layer.fontFamily}"`;
  const lines = wrapText(context, layer.text, layer.width);
  const lineHeight = layer.fontSize * 1.08;
  return { lines, lineHeight, width: layer.width, height: Math.max(lineHeight, lines.length * lineHeight) };
}

function drawTextLayer(context, layer) {
  context.save();
  context.translate(layer.x, layer.y);
  context.rotate(layer.rotation * Math.PI / 180);
  context.fillStyle = layer.color;
  context.textBaseline = "top";
  context.textAlign = layer.align;
  const layout = textLayout(context, layer);
  const textXPosition = layer.align === "left" ? -layout.width / 2 : layer.align === "right" ? layout.width / 2 : 0;
  layout.lines.forEach((line, index) => context.fillText(line, textXPosition, -layout.height / 2 + index * layout.lineHeight));
  layer.bounds = { width: layout.width, height: layout.height };
  context.restore();
}

function drawTextSelection(context, layer) {
  const bounds = layer.bounds || { width: layer.width, height: layer.fontSize * 1.08 };
  const padding = 14;
  context.save();
  context.translate(layer.x, layer.y);
  context.rotate(layer.rotation * Math.PI / 180);
  context.strokeStyle = "#e64955";
  context.fillStyle = "#ffd84d";
  context.lineWidth = 3;
  context.setLineDash([10, 8]);
  context.strokeRect(-bounds.width / 2 - padding, -bounds.height / 2 - padding, bounds.width + padding * 2, bounds.height + padding * 2);
  context.setLineDash([]);
  [[-1, -1], [1, -1], [-1, 1], [1, 1]].forEach(([horizontal, vertical]) => {
    context.beginPath();
    context.arc(horizontal * (bounds.width / 2 + padding), vertical * (bounds.height / 2 + padding), 8, 0, Math.PI * 2);
    context.fill();
    context.stroke();
  });
  context.restore();
}

function textLayerAt(point) {
  return [...layers].reverse().find((layer) => {
    if (layer.type !== "text" || !layer.visible) return false;
    const angle = -layer.rotation * Math.PI / 180;
    const dx = point.x - layer.x;
    const dy = point.y - layer.y;
    const localX = dx * Math.cos(angle) - dy * Math.sin(angle);
    const localY = dx * Math.sin(angle) + dy * Math.cos(angle);
    const bounds = layer.bounds || { width: layer.width, height: layer.fontSize * 1.08 };
    return Math.abs(localX) <= bounds.width / 2 + 22 && Math.abs(localY) <= bounds.height / 2 + 22;
  });
}

function resetEditor(base = blankBase()) {
  layers = [base, makeLayer("Paint 1")];
  selectedLayerId = layers[1].id;
  undoStack = [];
  redoStack = [];
  renderLayers();
  renderComposite();
  updateHistoryButtons();
}

function renderComposite(showSelection = true) {
  if (!layers.length) return;
  displayContext.clearRect(0, 0, ARTBOARD_WIDTH, ARTBOARD_HEIGHT);
  layers.forEach((layer) => {
    if (!layer.visible) return;
    if (layer.type === "text") drawTextLayer(displayContext, layer);
    else displayContext.drawImage(layer.canvas, 0, 0);
  });
  const layer = selectedLayer();
  if (showSelection && currentTool === "text" && layer?.type === "text" && layer.visible) drawTextSelection(displayContext, layer);
}

function renderLayers() {
  layersList.replaceChildren();
  [...layers].reverse().forEach((layer) => {
    const row = document.createElement("div");
    row.className = `layer-row${layer.id === selectedLayerId ? " is-selected" : ""}${layer.visible ? "" : " is-hidden"}`;
    row.dataset.layerId = layer.id;
    const visibility = document.createElement("button");
    visibility.type = "button";
    visibility.className = "visibility-button";
    visibility.setAttribute("aria-label", `${layer.visible ? "Hide" : "Show"} ${layer.name}`);
    visibility.textContent = layer.visible ? "◉" : "○";
    visibility.addEventListener("click", () => {
      layer.visible = !layer.visible;
      renderLayers();
      renderComposite();
    });
    const select = document.createElement("button");
    select.type = "button";
    select.className = "layer-select";
    select.textContent = `${layer.type === "text" ? "T " : layer.locked ? "▣ " : "✎ "}${layer.name}`;
    select.title = layer.name;
    select.addEventListener("click", () => {
      selectedLayerId = layer.id;
      if (layer.type === "text") currentTool = "text";
      renderLayers();
      updateToolButtons();
      renderComposite();
    });
    select.addEventListener("dblclick", () => renameSelectedLayer());
    row.append(visibility, select);
    layersList.append(row);
  });
  const layer = selectedLayer();
  const index = layers.findIndex((item) => item.id === selectedLayerId);
  qs("#move-layer-up").disabled = !layer || index >= layers.length - 1 || layer.locked;
  qs("#move-layer-down").disabled = !layer || index <= 1 || layer.locked;
  qs("#delete-layer").disabled = !layer || layer.locked;
  qs("#duplicate-layer").disabled = !layer;
  qs("#rename-layer").disabled = !layer;
  updateTextControls();
}

function addPaintLayer(name = `Paint ${layers.filter((layer) => layer.type === "paint").length + 1}`) {
  const layer = makeLayer(name);
  layers.push(layer);
  selectedLayerId = layer.id;
  currentTool = "brush";
  renderLayers();
  updateToolButtons();
  renderComposite();
}

function addTextLayer(x = ARTBOARD_WIDTH / 2, y = 260) {
  const count = layers.filter((layer) => layer.type === "text").length + 1;
  const layer = makeTextLayer(`Words ${count}`, x, y);
  layers.push(layer);
  selectedLayerId = layer.id;
  currentTool = "text";
  document.fonts.load(`${layer.fontSize}px "${layer.fontFamily}"`).finally(() => renderComposite());
  renderLayers();
  updateToolButtons();
  renderComposite();
  return layer;
}

function updateTextControls() {
  const layer = selectedLayer();
  const isText = layer?.type === "text";
  textControls.hidden = !isText;
  toolbox.classList.toggle("has-text", isText);
  if (!isText) return;
  textContent.value = layer.text;
  textFont.value = layer.fontFamily;
  textSize.value = String(layer.fontSize);
  textWidth.value = String(layer.width);
  textRotation.value = String(layer.rotation);
  textColor.value = layer.color;
  textX.value = String(Math.round(layer.x));
  textY.value = String(Math.round(layer.y));
  qs("#text-size-output").textContent = String(layer.fontSize);
  qs("#text-width-output").textContent = String(layer.width);
  qs("#text-rotation-output").textContent = `${layer.rotation}°`;
  qsa("[data-align]", textControls).forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.align === layer.align)));
}

function renameSelectedLayer() {
  const layer = selectedLayer();
  if (!layer) return;
  const name = window.prompt("What should I call this layer?", layer.name)?.trim();
  if (name) {
    layer.name = name.slice(0, 32);
    renderLayers();
  }
}

qs("#add-layer").addEventListener("click", () => addPaintLayer());
qs("#rename-layer").addEventListener("click", renameSelectedLayer);
qs("#delete-layer").addEventListener("click", () => {
  const index = layers.findIndex((layer) => layer.id === selectedLayerId);
  if (index <= 0) return;
  layers.splice(index, 1);
  selectedLayerId = layers[Math.max(1, index - 1)]?.id || layers[0].id;
  renderLayers();
  renderComposite();
});
qs("#duplicate-layer").addEventListener("click", () => {
  const source = selectedLayer();
  if (!source) return;
  let copy;
  if (source.type === "text") {
    copy = makeTextLayer(`${source.name} copy`, clamp(source.x + 26, 60, ARTBOARD_WIDTH - 60), clamp(source.y + 26, 60, ARTBOARD_HEIGHT - 60));
    Object.assign(copy, {
      text: source.text,
      fontFamily: source.fontFamily,
      fontSize: source.fontSize,
      color: source.color,
      align: source.align,
      width: source.width,
      rotation: source.rotation,
    });
  } else {
    copy = makeLayer(`${source.name} copy`);
    copy.canvas.getContext("2d").drawImage(source.canvas, 0, 0);
  }
  layers.splice(layers.indexOf(source) + 1, 0, copy);
  selectedLayerId = copy.id;
  currentTool = copy.type === "text" ? "text" : "brush";
  renderLayers();
  updateToolButtons();
  renderComposite();
});
qs("#move-layer-up").addEventListener("click", () => {
  const index = layers.findIndex((layer) => layer.id === selectedLayerId);
  if (index > 0 && index < layers.length - 1) [layers[index], layers[index + 1]] = [layers[index + 1], layers[index]];
  renderLayers(); renderComposite();
});
qs("#move-layer-down").addEventListener("click", () => {
  const index = layers.findIndex((layer) => layer.id === selectedLayerId);
  if (index > 1) [layers[index], layers[index - 1]] = [layers[index - 1], layers[index]];
  renderLayers(); renderComposite();
});

function updateToolButtons() {
  qsa(".tool-button").forEach((button) => {
    const active = button.dataset.tool === currentTool;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  artboard.style.cursor = currentTool === "text" ? "move" : currentTool === "eraser" ? "cell" : "crosshair";
  updateTextControls();
}
qsa(".tool-button").forEach((button) => button.addEventListener("click", () => {
  const nextTool = button.dataset.tool;
  if (nextTool === "text" && selectedLayer()?.type !== "text") {
    addTextLayer();
    return;
  }
  currentTool = nextTool;
  updateToolButtons();
  renderComposite();
}));
qs("#add-text").addEventListener("click", () => addTextLayer(ARTBOARD_WIDTH / 2, 320));
brushSize.addEventListener("input", () => { sizeOutput.textContent = brushSize.value; });
brushOpacity.addEventListener("input", () => { opacityOutput.textContent = `${brushOpacity.value}%`; });

function setBrushColor(color) {
  brushColor = color.toUpperCase();
  colorDot.style.setProperty("--brush-color", brushColor);
  colorLabel.textContent = brushColor;
  qs("#custom-color").value = brushColor;
}

function mixedPaint(base, x, y) {
  const [hue, saturation, lightness] = rgbToHsl(hexToRgb(base));
  const dx = x - .5;
  const dy = y - .5;
  const radius = Math.min(1, Math.sqrt(dx * dx + dy * dy) * 2);
  const angle = Math.atan2(dy, dx);
  return hslToHex([
    hue + Math.sin(angle) * 10 * radius,
    saturation + Math.cos(angle) * 18 * radius,
    lightness - dy * 32 + Math.sin(angle * 2) * 4 * radius,
  ]);
}

function buildPaintWells() {
  if (!qs("#paint-wells")) return;
  const colors = [...currentPalette.slice(0, 5), ...customPaintColors, "#fffdf6", "#19181d"].slice(-9);
  qs("#paint-wells").replaceChildren(...colors.map((color, index) => {
    const well = document.createElement("button");
    well.type = "button";
    well.className = "paint-well";
    well.style.setProperty("--paint", color);
    well.setAttribute("aria-label", `${index < 5 ? "Photo color" : color === "#fffdf6" ? "White paint" : color === "#19181d" ? "Black paint" : "Custom paint"} ${color}`);
    const mixAtPointer = (event) => {
      const bounds = well.getBoundingClientRect();
      const x = clamp((event.clientX - bounds.left) / bounds.width, 0, 1);
      const y = clamp((event.clientY - bounds.top) / bounds.height, 0, 1);
      const mixed = mixedPaint(color, x, y);
      well.style.setProperty("--mix-x", `${x * 100}%`);
      well.style.setProperty("--mix-y", `${y * 100}%`);
      well.style.setProperty("--mixed", mixed);
      setBrushColor(mixed);
    };
    well.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      well.setPointerCapture(event.pointerId);
      well.classList.add("is-mixing");
      mixAtPointer(event);
    });
    well.addEventListener("pointermove", (event) => { if (well.hasPointerCapture(event.pointerId)) mixAtPointer(event); });
    const stop = (event) => { if (well.hasPointerCapture(event.pointerId)) well.releasePointerCapture(event.pointerId); well.classList.remove("is-mixing"); };
    well.addEventListener("pointerup", stop);
    well.addEventListener("pointercancel", stop);
    return well;
  }));
}

qs("#water-well").addEventListener("click", () => {
  setBrushColor(mix(brushColor, "#ffffff", .22));
  brushOpacity.value = String(Math.max(20, Number(brushOpacity.value) - 12));
  opacityOutput.textContent = `${brushOpacity.value}%`;
  showToast("A little water made the paint lighter and softer.");
});
qs("#custom-color").addEventListener("input", (event) => setBrushColor(event.target.value));
qs("#custom-color").addEventListener("change", (event) => {
  const color = event.target.value.toUpperCase();
  if (!customPaintColors.includes(color)) customPaintColors.push(color);
  customPaintColors = customPaintColors.slice(-2);
  buildPaintWells();
  setBrushColor(color);
});

function pointerPoint(event) {
  const bounds = artboard.getBoundingClientRect();
  return {
    x: (event.clientX - bounds.left) * ARTBOARD_WIDTH / bounds.width,
    y: (event.clientY - bounds.top) * ARTBOARD_HEIGHT / bounds.height,
  };
}

function textState(layer) {
  return {
    text: layer.text,
    fontFamily: layer.fontFamily,
    fontSize: layer.fontSize,
    color: layer.color,
    align: layer.align,
    width: layer.width,
    rotation: layer.rotation,
    x: layer.x,
    y: layer.y,
  };
}

function snapshot(layer) {
  if (layer.type === "text") return { kind: "text", layerId: layer.id, state: textState(layer) };
  return { kind: "canvas", layerId: layer.id, dataUrl: layer.canvas.toDataURL("image/png") };
}

function recordHistory(item) {
  if (!item) return;
  undoStack.push(item);
  undoStack = undoStack.slice(-24);
  redoStack = [];
  updateHistoryButtons();
}

function drawSegment(from, to) {
  const layer = selectedLayer();
  if (!layer || layer.locked || layer.type !== "paint") return;
  const context = layer.canvas.getContext("2d");
  context.save();
  context.lineCap = "round";
  context.lineJoin = "round";
  context.lineWidth = Number(brushSize.value);
  context.globalAlpha = Number(brushOpacity.value) / 100;
  context.globalCompositeOperation = currentTool === "eraser" ? "destination-out" : "source-over";
  context.strokeStyle = brushColor;
  context.beginPath();
  context.moveTo(from.x, from.y);
  context.lineTo(to.x, to.y);
  context.stroke();
  context.restore();
  renderComposite();
}

artboard.addEventListener("pointerdown", (event) => {
  const point = pointerPoint(event);
  if (currentTool === "text") {
    event.preventDefault();
    let layer = textLayerAt(point);
    if (!layer) layer = selectedLayer()?.type === "text" ? selectedLayer() : addTextLayer(point.x, point.y);
    selectedLayerId = layer.id;
    recordHistory(snapshot(layer));
    draggingText = true;
    textDragOffset = { x: point.x - layer.x, y: point.y - layer.y };
    artboard.setPointerCapture(event.pointerId);
    renderLayers();
    updateToolButtons();
    renderComposite();
    return;
  }
  const layer = selectedLayer();
  if (!layer || layer.locked || layer.type !== "paint") {
    showToast("Pick or add a paint layer before drawing.");
    return;
  }
  event.preventDefault();
  artboard.setPointerCapture(event.pointerId);
  recordHistory(snapshot(layer));
  drawing = true;
  previousPoint = point;
  drawSegment(previousPoint, { x: previousPoint.x + .1, y: previousPoint.y + .1 });
});
artboard.addEventListener("pointermove", (event) => {
  if (draggingText) {
    event.preventDefault();
    const layer = selectedLayer();
    if (!layer || layer.type !== "text") return;
    const point = pointerPoint(event);
    layer.x = clamp(point.x - textDragOffset.x, 0, ARTBOARD_WIDTH);
    layer.y = clamp(point.y - textDragOffset.y, 0, ARTBOARD_HEIGHT);
    updateTextControls();
    renderComposite();
    return;
  }
  if (!drawing) return;
  event.preventDefault();
  const next = pointerPoint(event);
  drawSegment(previousPoint, next);
  previousPoint = next;
});
function endDrawing(event) {
  if (!drawing && !draggingText) return;
  drawing = false;
  draggingText = false;
  textDragOffset = null;
  previousPoint = null;
  if (artboard.hasPointerCapture(event.pointerId)) artboard.releasePointerCapture(event.pointerId);
}
artboard.addEventListener("pointerup", endDrawing);
artboard.addEventListener("pointercancel", endDrawing);

function restoreSnapshot(item) {
  return new Promise((resolve) => {
    const layer = layers.find((candidate) => candidate.id === item.layerId);
    if (!layer) { resolve(); return; }
    if (item.kind === "text") {
      Object.assign(layer, item.state);
      selectedLayerId = layer.id;
      currentTool = "text";
      renderLayers();
      updateToolButtons();
      renderComposite();
      resolve();
      return;
    }
    const image = new Image();
    image.onload = () => {
      const context = layer.canvas.getContext("2d");
      context.clearRect(0, 0, ARTBOARD_WIDTH, ARTBOARD_HEIGHT);
      context.drawImage(image, 0, 0);
      renderLayers();
      renderComposite();
      resolve();
    };
    image.src = item.dataUrl;
  });
}

function updateHistoryButtons() {
  qs("#undo-action").disabled = undoStack.length === 0;
  qs("#redo-action").disabled = redoStack.length === 0;
}
qs("#undo-action").addEventListener("click", async () => {
  const item = undoStack.pop();
  if (!item) return;
  const layer = layers.find((candidate) => candidate.id === item.layerId);
  if (layer) redoStack.push(snapshot(layer));
  await restoreSnapshot(item);
  updateHistoryButtons();
});

function beginTextEdit() {
  const layer = selectedLayer();
  if (layer?.type === "text" && !textEditBefore) textEditBefore = snapshot(layer);
}

function commitTextEdit() {
  if (!textEditBefore) return;
  const layer = selectedLayer();
  if (layer?.type === "text" && JSON.stringify(textEditBefore.state) !== JSON.stringify(textState(layer))) recordHistory(textEditBefore);
  textEditBefore = null;
}

function bindTextControl(element, property, parse = (value) => value) {
  element.addEventListener("focus", beginTextEdit);
  element.addEventListener("pointerdown", beginTextEdit);
  element.addEventListener("input", () => {
    const layer = selectedLayer();
    if (layer?.type !== "text") return;
    layer[property] = parse(element.value);
    updateTextControls();
    renderComposite();
  });
  element.addEventListener("change", commitTextEdit);
  element.addEventListener("blur", commitTextEdit);
}

bindTextControl(textContent, "text");
bindTextControl(textSize, "fontSize", Number);
bindTextControl(textWidth, "width", Number);
bindTextControl(textRotation, "rotation", Number);
bindTextControl(textColor, "color");
bindTextControl(textX, "x", (value) => clamp(Number(value), 0, ARTBOARD_WIDTH));
bindTextControl(textY, "y", (value) => clamp(Number(value), 0, ARTBOARD_HEIGHT));
bindTextControl(textFont, "fontFamily");
textFont.addEventListener("change", () => {
  const layer = selectedLayer();
  if (layer?.type === "text") document.fonts.load(`${layer.fontSize}px "${layer.fontFamily}"`).finally(() => renderComposite());
});
qsa("[data-align]", textControls).forEach((button) => button.addEventListener("click", () => {
  const layer = selectedLayer();
  if (layer?.type !== "text" || layer.align === button.dataset.align) return;
  recordHistory(snapshot(layer));
  layer.align = button.dataset.align;
  updateTextControls();
  renderComposite();
}));
qs("#redo-action").addEventListener("click", async () => {
  const item = redoStack.pop();
  if (!item) return;
  const layer = layers.find((candidate) => candidate.id === item.layerId);
  if (layer) undoStack.push(snapshot(layer));
  await restoreSnapshot(item);
  updateHistoryButtons();
});

async function loadCardIntoEditor(dataUrl, label) {
  const base = makeLayer(label, true);
  const image = new Image();
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = reject;
    image.src = dataUrl;
  });
  base.canvas.getContext("2d").drawImage(image, 0, 0, ARTBOARD_WIDTH, ARTBOARD_HEIGHT);
  resetEditor(base);
  window.location.hash = "editor";
  showToast("Your cover is on the bottom layer. Time to paint.");
}

qsa(".edit-card").forEach((button) => button.addEventListener("click", () => {
  const index = Number(button.dataset.card);
  const card = generatedCards[index];
  if (!card) {
    showToast("Make the three covers first, then choose one to paint.");
    return;
  }
  loadCardIntoEditor(card.data_url, `Cover ${index + 1}`).catch(() => showToast("I couldn’t open that cover in the editor."));
}));

qs("#new-blank").addEventListener("click", () => {
  resetEditor();
  showToast("Fresh paper. Go make a mess.");
});
qs("#export-art").addEventListener("click", () => {
  renderComposite(false);
  artboard.toBlob((blob) => {
    renderComposite(true);
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "my-palette-card.png";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("Your flattened PNG is ready.");
  }, "image/png");
});

resetEditor();
setBrushColor(brushColor);
updateToolButtons();
Promise.all(TEXT_FONTS.map((font) => document.fonts.load(`48px "${font}"`))).finally(() => renderComposite());

window.addEventListener("beforeunload", () => {
  if (currentUrl) URL.revokeObjectURL(currentUrl);
});
