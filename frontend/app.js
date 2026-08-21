const DEFAULT_PALETTE = ["#f7f3ed", "#8d1738", "#c79a50", "#ead8d2", "#3e3034"];

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
const actionStatus = document.querySelector("#action-status");
const results = document.querySelector("#results");
const form = document.querySelector("#studio-form");
const titleInput = document.querySelector("#card-title");
const messageInput = document.querySelector("#card-message");
const swatches = [...document.querySelectorAll("#swatches span")];
const paletteBars = [...document.querySelectorAll(".palette-bar span")];
const cardImages = [...document.querySelectorAll(".card-source-image")];

let currentUrl = null;
let currentPalette = [...DEFAULT_PALETTE];

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

function saturation([red, green, blue]) {
  const highest = Math.max(red, green, blue);
  const lowest = Math.min(red, green, blue);
  return highest === 0 ? 0 : (highest - lowest) / highest;
}

function colorDistance(first, second) {
  return Math.sqrt(first.reduce((total, channel, index) => total + (channel - second[index]) ** 2, 0));
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
    .map((entry) => ({
      count: entry.count,
      rgb: entry.sums.map((total) => total / entry.count),
    }))
    .sort((first, second) => second.count - first.count);

  const chosen = [];
  for (const candidate of candidates) {
    if (chosen.every((color) => colorDistance(color, candidate.rgb) > 58)) chosen.push(candidate.rgb);
    if (chosen.length === 5) break;
  }
  while (chosen.length < 5) chosen.push(hexToRgb(DEFAULT_PALETTE[chosen.length]));
  return chosen.map(rgbToHex);
}

function updateTheme(palette) {
  const colors = palette.map(hexToRgb);
  const accentIndex = colors.reduce((bestIndex, color, index) => saturation(color) > saturation(colors[bestIndex]) ? index : bestIndex, 0);
  const accent = palette[accentIndex];
  const secondary = palette.find((color) => color !== accent && colorDistance(hexToRgb(color), hexToRgb(accent)) > 75) || palette[1];
  const deep = rgbToHex(colors.reduce((darkest, color) => color.reduce((sum, channel) => sum + channel, 0) < darkest.reduce((sum, channel) => sum + channel, 0) ? color : darkest));
  const tint = mix(accent, "#ffffff", 0.78);
  document.documentElement.style.setProperty("--accent", accent);
  document.documentElement.style.setProperty("--accent-soft", tint);
  document.documentElement.style.setProperty("--secondary", secondary);
  document.documentElement.style.setProperty("--deep", deep);
  document.querySelector("#accent-role").textContent = accent.toUpperCase();
  document.querySelector("#canvas-role").textContent = `${tint.toUpperCase()} tint`;
}

function updatePalette(palette) {
  currentPalette = palette;
  swatches.forEach((swatch, index) => {
    swatch.style.setProperty("--swatch", palette[index]);
    swatch.title = palette[index].toUpperCase();
  });
  paletteBars.forEach((bar, index) => { bar.style.background = palette[index]; });
  document.querySelector("#story-count").textContent = "5 / 5";
  updateTheme(palette);
}

function selectedObject() {
  return document.querySelector('input[name="object"]:checked').value;
}

function updateCardCopy() {
  const title = titleInput.value.trim() || "A little color for you";
  const message = messageInput.value.trim() || "Made from the colors in your photo.";
  const objectName = selectedObject().toUpperCase();
  document.querySelectorAll(".card-title-output").forEach((node) => { node.textContent = title; });
  document.querySelectorAll(".card-message-output").forEach((node) => { node.textContent = message; });
  document.querySelectorAll(".card-object").forEach((node) => { node.textContent = objectName; });
}

function resetPhoto() {
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = null;
  fileInput.value = "";
  sourceImage.removeAttribute("src");
  storyImage.removeAttribute("src");
  cardImages.forEach((image) => image.removeAttribute("src"));
  uploadEmpty.hidden = false;
  uploadPreview.hidden = true;
  storyImage.hidden = true;
  sourcePlaceholder.hidden = false;
  generateButton.disabled = true;
  results.hidden = true;
  photoError.textContent = "";
  actionStatus.textContent = "Add a photo to begin. No AI or server is connected.";
  updatePalette(DEFAULT_PALETTE);
  document.querySelector("#story-count").textContent = "0 / 5";
  document.querySelector("#accent-role").textContent = "Waiting for photo";
  document.querySelector("#canvas-role").textContent = "Warm neutral";
}

function loadPhoto(file) {
  photoError.textContent = "";
  if (!file || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    photoError.textContent = "Choose a JPG, PNG, or WebP image.";
    return;
  }
  if (file.size > 12 * 1024 * 1024) {
    photoError.textContent = "Choose an image smaller than 12 MB for this preview.";
    return;
  }
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = URL.createObjectURL(file);
  const probe = new Image();
  probe.onload = () => {
    sourceImage.src = currentUrl;
    storyImage.src = currentUrl;
    cardImages.forEach((image) => { image.src = currentUrl; });
    fileName.textContent = file.name;
    uploadEmpty.hidden = true;
    uploadPreview.hidden = false;
    sourcePlaceholder.hidden = true;
    storyImage.hidden = false;
    generateButton.disabled = false;
    actionStatus.textContent = "Ready to preview. Processing stays in this tab.";
    updatePalette(samplePalette(probe));
    updateCardCopy();
  };
  probe.onerror = () => {
    photoError.textContent = "We could not read that image. Try another file.";
    resetPhoto();
  };
  probe.src = currentUrl;
}

fileInput.addEventListener("change", () => loadPhoto(fileInput.files[0]));
removePhoto.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); resetPhoto(); });
titleInput.addEventListener("input", updateCardCopy);
messageInput.addEventListener("input", updateCardCopy);
document.querySelectorAll('input[name="object"]').forEach((input) => input.addEventListener("change", updateCardCopy));

["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.add("is-dragging");
}));
["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.remove("is-dragging");
}));
dropzone.addEventListener("drop", (event) => loadPhoto(event.dataTransfer.files[0]));

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!currentUrl) return;
  updateCardCopy();
  results.hidden = false;
  actionStatus.textContent = "Frontend preview ready — no AI or server was called.";
  results.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
});

document.querySelector("#start-over").addEventListener("click", () => {
  resetPhoto();
  document.querySelector("#studio").scrollIntoView({ behavior: "smooth", block: "start" });
});

updateCardCopy();
