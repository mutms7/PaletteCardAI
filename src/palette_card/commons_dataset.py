"""Reproducible, license-aware Wikimedia Commons starter-data acquisition.

This module deliberately performs no network work at import time. The CLI
wrapper calls :func:`run_acquisition`; tests can exercise parsing, filtering,
splitting, and image validation with an injected client or local bytes.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import random
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from .config import CLASS_NAMES, Paths

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
DEFAULT_CATEGORIES: dict[str, str] = {
    "flower": "Close-up photographs of flowers",
    "heart": "Heart symbols",
    "ring": "Wedding rings",
    "cake": "Birthday cakes",
    "balloon": "Toy balloons",
}
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
MANIFEST_FIELDS = [
    "class_name", "split", "local_path", "source_category", "source_title",
    "page_url", "file_url", "creator", "credit", "license", "license_url",
    "license_raw", "license_url_raw", "usage_terms", "attribution_required",
    "width", "height", "sha256", "average_hash", "difference_hash",
    "downloaded_at",
]


def validate_user_agent(user_agent: str | None) -> str:
    """Require a descriptive application name and a reachable contact."""

    value = str(user_agent or "").strip()
    if not re.search(r"PaletteCard(?:\s*AI)?", value, re.IGNORECASE):
        raise ValueError("--user-agent must identify PaletteCard (for example PaletteCardAI/0.1 ...)")
    if not (re.search(r"https://\S+", value, re.IGNORECASE) or re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value) or re.search(r"\bUser:[^\s;)]+", value, re.IGNORECASE)):
        raise ValueError("--user-agent must include a contact https URL, email address, or User: account")
    return value


def _safe_url(value: str) -> str:
    value = html.unescape(value.strip())
    if value.startswith("//"):
        value = "https:" + value
    return value if value.startswith(("https://", "http://")) else ""


def _anchor_urls(value: str) -> list[str]:
    urls: list[str] = []
    for href in re.findall(r"<a\b[^>]*\bhref\s*=\s*['\"]([^'\"]+)['\"]", value, flags=re.IGNORECASE):
        safe = _safe_url(href)
        if safe:
            urls.append(safe)
    return urls


def sanitize_metadata(value: Any) -> str:
    """Turn Commons extmetadata into safe, readable plain text."""

    if isinstance(value, Mapping):
        value = value.get("value", "")
    raw = str(value or "")
    anchors = _anchor_urls(raw)
    text = html.unescape(raw)
    text = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for url in anchors:
        if url not in text:
            text += f" ({url})"
    return text


def _metadata_value(metadata: Mapping[str, Any], key: str) -> str:
    return sanitize_metadata(metadata.get(key, ""))


@dataclass(frozen=True)
class LicenseInfo:
    name: str
    url: str
    raw_name: str
    raw_url: str
    usage_terms: str
    attribution_required: bool


def _metadata_url(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key, "")
    if isinstance(value, Mapping):
        value = value.get("value", "")
    urls = _anchor_urls(str(value))
    if urls:
        return urls[0]
    return _safe_url(sanitize_metadata(value).split(" ", 1)[0])


def _canonical_license(name: str, url: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "creativecommons.org":
        return None
    path = parsed.path.rstrip("/").lower()
    version = r"\d+\.\d+"
    if name == "CC0" and re.fullmatch(rf"/publicdomain/zero/{version}", path):
        return "CC0", f"https://creativecommons.org{path}/"
    if name == "CC BY" and re.fullmatch(rf"/licenses/by/{version}", path):
        return "CC BY", f"https://creativecommons.org{path}/"
    if name == "CC BY-SA" and re.fullmatch(rf"/licenses/by-sa/{version}", path):
        return "CC BY-SA", f"https://creativecommons.org{path}/"
    return None


def parse_license(metadata: Mapping[str, Any], creator: str | None = None) -> LicenseInfo | None:
    """Accept only reviewed PD/CC0/CC BY/CC BY-SA metadata combinations."""

    raw_name = _metadata_value(metadata, "LicenseShortName")
    raw_url = _metadata_value(metadata, "LicenseUrl")
    text = raw_name.lower().replace("–", "-").replace("—", "-")
    blocked = ("noncommercial", "non-commercial", "no derivatives", "no-derivatives")
    if any(token in text for token in blocked) or re.search(r"\b(?:nd|nc)\b", text.replace("-", " ")):
        return None
    if "public domain" in text or text in {"pd", "p.d."}:
        return LicenseInfo("Public domain", raw_url, raw_name, raw_url, "Public-domain claim; verify status before redistribution.", False)
    if re.search(r"\bcc0\b|cc zero|creative commons zero", text):
        canonical = _canonical_license("CC0", _metadata_url(metadata, "LicenseUrl"))
        return LicenseInfo("CC0", canonical[1], raw_name, raw_url, "CC0; no attribution required by the license, but preserve provenance.", False) if canonical else None
    # Commons uses names such as "CC BY 4.0" and "CC BY-SA 3.0".
    if re.search(r"creative commons attribution(?:[- ](?:share[- ]?alike))?|\bcc\s*by(?:\s*[- ]?sa)?\b", text):
        name = "CC BY-SA" if "share" in text or "by-sa" in text or "by sa" in text else "CC BY"
        if not creator or not creator.strip():
            return None
        canonical = _canonical_license(name, _metadata_url(metadata, "LicenseUrl"))
        return LicenseInfo(name, canonical[1], raw_name, raw_url, f"{name}; attribution required; preserve creator and license link.", True) if canonical else None
    return None


def _page_url(title: str) -> str:
    return "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe=":()/")


@dataclass(frozen=True)
class ImageCandidate:
    class_name: str
    source_category: str
    source_title: str
    page_url: str
    file_url: str
    license: LicenseInfo
    creator: str = ""
    credit: str = ""
    width: int = 0
    height: int = 0
    mime: str = ""


def _iter_pages(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    pages = payload.get("query", {}).get("pages", [])
    if isinstance(pages, Mapping):
        pages = pages.values()
    return pages or []


def parse_category_members(payload: Mapping[str, Any]) -> list[str]:
    """Extract file titles from either API response format."""

    members = payload.get("query", {}).get("categorymembers", [])
    return [str(item.get("title", "")) for item in members if item.get("title", "").startswith("File:")]


def parse_imageinfo(payload: Mapping[str, Any], class_name: str, category: str) -> ImageCandidate | None:
    """Parse one imageinfo response, applying MIME and license filters."""

    pages = list(_iter_pages(payload))
    if not pages:
        return None
    page = pages[0]
    infos = page.get("imageinfo", [])
    if isinstance(infos, Mapping):
        infos = [infos]
    if not infos:
        return None
    info = infos[0]
    mime = str(info.get("mime", "")).lower().split(";", 1)[0]
    if mime not in ALLOWED_MIME:
        return None
    extmetadata = info.get("extmetadata", {})
    creator = _metadata_value(extmetadata, "Artist") or _metadata_value(extmetadata, "Credit")
    license_info = parse_license(extmetadata, creator=creator)
    if license_info is None:
        return None
    title = str(page.get("title", ""))
    file_url = str(info.get("thumburl") or info.get("url") or "")
    if not file_url.startswith(("https://", "http://")):
        return None
    return ImageCandidate(
        class_name=class_name,
        source_category=category,
        source_title=title,
        page_url=_page_url(title),
        file_url=file_url,
        license=license_info,
        creator=creator,
        credit=_metadata_value(extmetadata, "Credit"),
        width=int(info.get("thumbwidth") or info.get("width") or 0),
        height=int(info.get("thumbheight") or info.get("height") or 0),
        mime=mime,
    )


class CommonsClient:
    """Polite sequential Action API and image downloader."""

    def __init__(self, endpoint: str = COMMONS_API, user_agent: str | None = None, timeout: float = 20.0, max_retries: int = 3, backoff: float = 1.0, max_bytes: int = 15_000_000, pacing: float = 0.25, opener: Callable[..., Any] | None = None, sleeper: Callable[[float], None] = time.sleep, media_timeout: float = 10.0, media_max_retries: int = 1, media_backoff: float = 0.5):
        self.endpoint = endpoint
        self.user_agent = validate_user_agent(user_agent)
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff = max(0.0, backoff)
        self.max_bytes = max_bytes
        self.pacing = max(0.0, pacing)
        self.opener = opener or urllib.request.urlopen
        self.sleeper = sleeper
        self.media_timeout = max(0.1, media_timeout)
        self.media_max_retries = max(0, media_max_retries)
        self.media_backoff = max(0.0, media_backoff)
        self._last_success: float | None = None

    def _read(self, url: str, *, timeout: float | None = None, max_retries: int | None = None, backoff: float | None = None) -> bytes:
        request_timeout = self.timeout if timeout is None else max(0.1, timeout)
        retry_limit = self.max_retries if max_retries is None else max(0, max_retries)
        retry_backoff = self.backoff if backoff is None else max(0.0, backoff)
        last_error: Exception | None = None
        for attempt in range(retry_limit + 1):
            try:
                if self._last_success is not None:
                    wait = self.pacing - (time.monotonic() - self._last_success)
                    if wait > 0:
                        self.sleeper(wait)
                request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json,image/*,*/*;q=0.8"})
                with self.opener(request, timeout=request_timeout) as response:
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.max_bytes:
                        raise ValueError(f"response exceeds max_bytes={self.max_bytes}")
                    data = response.read(self.max_bytes + 1)
                    if len(data) > self.max_bytes:
                        raise ValueError(f"response exceeds max_bytes={self.max_bytes}")
                    self._last_success = time.monotonic()
                    return data
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in (429, 503) or attempt >= retry_limit:
                    break
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = retry_backoff * (2**attempt)
                self.sleeper(min(30.0, max(0.0, delay)))
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                if attempt >= retry_limit:
                    break
                self.sleeper(min(30.0, retry_backoff * (2**attempt)))
        raise RuntimeError(f"Commons request failed after retries: {last_error}") from last_error

    def json(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        query = dict(params)
        query.setdefault("format", "json")
        query.setdefault("formatversion", "2")
        query["maxlag"] = "5"
        url = self.endpoint + "?" + urllib.parse.urlencode(query)
        try:
            return json.loads(self._read(url).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Commons API returned invalid JSON") from exc

    def download(self, url: str) -> bytes:
        """Fetch media with a shorter, independent retry budget than API calls."""

        return self._read(url, timeout=self.media_timeout, max_retries=self.media_max_retries, backoff=self.media_backoff)


def category_candidates(client: CommonsClient, class_name: str, category: str, max_candidates: int = 180) -> list[ImageCandidate]:
    """Query category members with pagination and imageinfo metadata."""

    titles: list[str] = []
    continuation: dict[str, Any] = {}
    while len(titles) < max_candidates:
        payload = client.json({"action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}", "cmtype": "file", "cmlimit": "500", **continuation})
        titles.extend(parse_category_members(payload))
        cont = payload.get("continue")
        if not cont:
            break
        continuation = {key: value for key, value in cont.items() if key != "continue"}
    result: list[ImageCandidate] = []
    selected = titles[:max_candidates]
    for start in range(0, len(selected), 50):
        batch = selected[start:start + 50]
        payload = client.json({"action": "query", "prop": "imageinfo", "titles": "|".join(batch), "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "1024"})
        result.extend(parse_imageinfo_batch(payload, class_name, category))
    return result


def parse_imageinfo_batch(payload: Mapping[str, Any], class_name: str, category: str) -> list[ImageCandidate]:
    """Parse all pages from one <=50-title imageinfo response."""

    result: list[ImageCandidate] = []
    for page in _iter_pages(payload):
        candidate = parse_imageinfo({"query": {"pages": [page]}}, class_name, category)
        if candidate is not None:
            result.append(candidate)
    return result


def average_hash(data: bytes, hash_size: int = 8) -> str:
    """Return a color-aware average hash for near-duplicate rejection.

    Using all RGB channels avoids treating every uniformly colored image as
    the same hash, while still catching resized/cropped copies.
    """

    with Image.open(io.BytesIO(data)) as image:
        resized = image.convert("RGB").resize((hash_size, hash_size), Image.Resampling.BILINEAR)
        channels = resized.split()
        bits: list[str] = []
        channel_means: list[float] = []
        for channel in channels:
            values = list(channel.get_flattened_data()) if hasattr(channel, "get_flattened_data") else list(channel.getdata())
            mean = sum(values) / len(values)
            channel_means.append(mean)
            bits.extend("1" if value >= mean else "0" for value in values)
        # Include a unary coarse color profile so solid red, blue, and dark
        # images do not collapse to the same shape-only hash. Small lighting
        # changes alter few bits; genuinely different colors alter many.
        for mean in channel_means:
            bits.extend("1" if mean >= threshold else "0" for threshold in range(256))
    return "".join(bits)


def difference_hash(data: bytes, hash_size: int = 8) -> str:
    """Return a gradient-based perceptual hash to supplement average hash."""

    with Image.open(io.BytesIO(data)) as image:
        gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.BILINEAR)
        values = list(gray.get_flattened_data()) if hasattr(gray, "get_flattened_data") else list(gray.getdata())
    return "".join("1" if values[row * (hash_size + 1) + column] > values[row * (hash_size + 1) + column + 1] else "0" for row in range(hash_size) for column in range(hash_size))


def hamming_distance(first: str, second: str) -> int:
    return sum(a != b for a, b in zip(first, second)) + abs(len(first) - len(second))


def _color_profile_distance(first: str, second: str, hash_size: int = 8) -> int:
    """Compare the unary RGB profile embedded at the end of average hashes."""

    profile_length = 3 * 256
    if len(first) < profile_length or len(second) < profile_length:
        return profile_length
    return hamming_distance(first[-profile_length:], second[-profile_length:])


def is_perceptual_duplicate(
    average_hash_value: str,
    difference_hash_value: str,
    seen_hashes: Sequence[tuple[str, str]],
    *,
    threshold: int = 12,
    color_threshold: int = 20,
) -> bool:
    """Detect resized/cropped/burst-like images without rejecting colors blindly.

    Either shape metric can establish a near match, but a coarse RGB profile
    guard prevents monochrome images with unrelated colors from collapsing
    into one group. Exact byte duplicates are handled separately by SHA-256.
    """

    for previous_average, previous_difference in seen_hashes:
        same_color = _color_profile_distance(average_hash_value, previous_average) <= color_threshold
        if same_color and (
            hamming_distance(average_hash_value, previous_average) <= threshold
            or hamming_distance(difference_hash_value, previous_difference) <= threshold
        ):
            return True
    return False


def validate_image_bytes(data: bytes, max_bytes: int = 15_000_000, min_width: int = 64, min_height: int = 64, max_pixels: int = 16_000_000) -> tuple[str, int, int, str]:
    """Reject oversized, corrupt, unsupported, or tiny raster images."""

    if len(data) > max_bytes:
        raise ValueError("image exceeds max_bytes")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            image.verify()
        with Image.open(io.BytesIO(data)) as checked:
            image_format = str(checked.format or "").upper()
            width, height = checked.size
            if image_format not in ALLOWED_FORMATS:
                raise ValueError(f"unsupported image format {image_format or 'unknown'}")
            if width < min_width or height < min_height:
                raise ValueError("image dimensions are too small")
            if width * height > max_pixels:
                raise ValueError("image dimensions exceed max_pixels")
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(f"invalid image: {exc}") from exc
    return image_format, width, height, ALLOWED_FORMATS[image_format]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_counts(total: int, ratios: tuple[float, float, float] = (0.70, 0.15, 0.15)) -> dict[str, int]:
    if total < 1 or abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError("total must be at least 1 and split ratios must sum to 1")
    if total == 1:
        return {"train": 1, "val": 0, "test": 0}
    if total == 2:
        return {"train": 1, "val": 0, "test": 1}
    names = ("train", "val", "test")
    counts = {name: max(1, int(total * ratio)) for name, ratio in zip(names, ratios)}
    while sum(counts.values()) > total:
        candidate = max((name for name in names if counts[name] > 1), key=lambda name: counts[name])
        counts[candidate] -= 1
    while sum(counts.values()) < total:
        candidate = max(names, key=lambda name: ratios[names.index(name)] - counts[name] / total)
        counts[candidate] += 1
    return counts


def assign_splits(items: Sequence[Any], seed: int, ratios: tuple[float, float, float] = (0.70, 0.15, 0.15)) -> list[tuple[Any, str]]:
    counts = split_counts(len(items), ratios)
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    output: list[tuple[Any, str]] = []
    cursor = 0
    for split in ("train", "val", "test"):
        for item in shuffled[cursor:cursor + counts[split]]:
            output.append((item, split))
        cursor += counts[split]
    return output


def _safe_slug(text: str, fallback: str = "image") -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", sanitize_metadata(text)).strip("-").lower()
    return (text[:72] or fallback).strip("-")


def safe_local_path(root: Path, relative: str | Path) -> Path:
    """Resolve a manifest path and reject traversal/out-of-root paths."""

    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"unsafe local path: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative_path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"unsafe local path: {relative}") from exc
    return resolved


@dataclass
class AttributionRecord:
    class_name: str
    split: str
    local_path: str
    source_category: str
    source_title: str
    page_url: str
    file_url: str
    creator: str
    credit: str
    license: str
    license_url: str
    license_raw: str
    license_url_raw: str
    usage_terms: str
    attribution_required: bool
    width: int
    height: int
    sha256: str
    average_hash: str
    difference_hash: str
    downloaded_at: str


def _manifest_rows(records: Sequence[AttributionRecord]) -> list[dict[str, Any]]:
    return [asdict(record) for record in records]


def _csv_manifest_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _json_manifest_bytes(rows: Sequence[Mapping[str, Any]], digest: str) -> bytes:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": digest,
        "manifest_id": f"sha256:{digest}",
        "records": list(rows),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def write_manifests(records: Sequence[AttributionRecord], data_root: Path) -> None:
    """Write the authoritative CSV first and derive JSON from its exact bytes."""

    data_root.mkdir(parents=True, exist_ok=True)
    rows = _manifest_rows(records)
    csv_bytes = _csv_manifest_bytes(rows)
    digest = sha256_bytes(csv_bytes)
    csv_target = data_root / "attribution.csv"
    json_target = data_root / "attribution.json"
    _atomic_write_bytes(csv_target, csv_bytes)
    _atomic_write_bytes(json_target, _json_manifest_bytes(rows, digest))


def manifest_sha256(data_root: Path) -> str:
    path = data_root / "attribution.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing attribution manifest: {path}")
    return sha256_bytes(path.read_bytes())


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".palettecard-{path.name}-", suffix=".part", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.write_bytes(data)
        with temporary.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_existing_manifest(data_root: Path, records: Sequence[AttributionRecord], *, max_bytes: int = 15_000_000, min_width: int = 64, min_height: int = 64) -> None:
    """Fail closed on missing/mismatched manifest files or orphan rasters."""

    resolved_root = data_root.resolve()
    expected_paths: set[str] = set()
    for record in records:
        if record.class_name not in CLASS_NAMES or record.split not in {"train", "val", "test"}:
            raise ValueError(f"Invalid manifest class/split: {record.class_name}/{record.split}")
        path = safe_local_path(data_root, record.local_path)
        expected_paths.add(path.relative_to(data_root.resolve()).as_posix())
        if not path.exists():
            raise ValueError(f"Manifest file is missing: {record.local_path}")
        data = path.read_bytes()
        if sha256_bytes(data) != record.sha256:
            raise ValueError(f"Manifest SHA-256 mismatch: {record.local_path}")
        try:
            validate_image_bytes(data, max_bytes=max_bytes, min_width=min_width, min_height=min_height)
        except ValueError as exc:
            raise ValueError(f"Manifest image is invalid: {record.local_path}") from exc
    raster_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    for path in resolved_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in raster_extensions:
            relative = path.relative_to(resolved_root).as_posix()
            if relative not in expected_paths:
                raise ValueError(f"Orphan raster is not in attribution manifest: {relative}")


def read_manifest(data_root: Path) -> list[AttributionRecord]:
    path = data_root / "attribution.csv"
    if not path.exists():
        return []
    records: list[AttributionRecord] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                safe_local_path(data_root, row["local_path"])
                records.append(AttributionRecord(**{
                    key: (
                        int(row[key]) if key in {"width", "height"}
                        else row[key].strip().lower() == "true" if key == "attribution_required"
                        else row[key]
                    )
                    for key in MANIFEST_FIELDS
                }))
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Invalid attribution manifest row in {path}") from exc
    # CSV is authoritative. Repair a missing/stale derived JSON sidecar when
    # reading for resume or validation rather than trusting its contents.
    reconcile_manifest_json(data_root, records)
    return records


def reconcile_manifest_json(data_root: Path, records: Sequence[AttributionRecord]) -> str:
    """Rebuild JSON when it does not match the authoritative CSV digest/rows."""

    csv_path = data_root / "attribution.csv"
    csv_bytes = csv_path.read_bytes()
    digest = sha256_bytes(csv_bytes)
    rows = _manifest_rows(records)
    json_path = data_root / "attribution.json"
    matches = False
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        matches = (
            payload.get("manifest_sha256") == digest
            and payload.get("manifest_id") == f"sha256:{digest}"
            and payload.get("records") == rows
        )
    except (OSError, json.JSONDecodeError):
        matches = False
    if not matches:
        _atomic_write_bytes(json_path, _json_manifest_bytes(rows, digest))
    return digest


REVIEW_MARKER = "REVIEW_REQUIRED"
REVIEW_RECEIPT = "REVIEW_RECEIPT.json"


def mark_review_required(data_root: Path) -> str:
    """Write a visible marker after Commons acquisition, without deleting data."""

    digest = manifest_sha256(data_root)
    _atomic_write_bytes(
        data_root / REVIEW_MARKER,
        ("Human review required before training.\nManifest SHA-256: " + digest + "\n").encode("utf-8"),
    )
    return digest


def validate_review_approval(data_root: Path) -> None:
    """Require a receipt for a Commons manifest and ensure it is still current."""

    manifest = data_root / "attribution.csv"
    if not manifest.exists():
        return
    # Validate the files before looking at the receipt. A valid receipt only
    # attests to the CSV bytes; it cannot make a changed image or orphan safe.
    records = read_manifest(data_root)
    validate_existing_manifest(data_root, records)
    receipt_path = data_root / REVIEW_RECEIPT
    if not receipt_path.exists():
        raise ValueError(
            "Commons attribution manifest requires human review before training; "
            "inspect contact sheets and run `python scripts/approve_dataset.py`."
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid dataset review receipt: {receipt_path}") from exc
    if receipt.get("manifest_sha256") != manifest_sha256(data_root):
        raise ValueError("Dataset review receipt is stale; the attribution manifest changed. Review and approve again.")
    if not receipt.get("approved_at") or not receipt.get("reviewer") or receipt.get("human_review") is not True:
        raise ValueError("Dataset review receipt is incomplete; create a new explicit approval.")


def approve_dataset(data_root: Path, reviewer: str, note: str = "") -> Path:
    """Create a non-destructive human-review receipt bound to the manifest hash."""

    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("reviewer must not be empty")
    records = read_manifest(data_root)
    validate_existing_manifest(data_root, records)
    digest = manifest_sha256(data_root)
    payload = {
        "human_review": True,
        "reviewer": reviewer,
        "note": note.strip(),
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": digest,
    }
    target = data_root / REVIEW_RECEIPT
    _atomic_write_bytes(target, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    return target


def _font(size: int = 16):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def generate_contact_sheets(records: Sequence[AttributionRecord], data_root: Path, audit_root: Path, per_class: int = 60) -> None:
    """Create locally reviewable contact sheets labeled with provenance."""

    audit_root.mkdir(parents=True, exist_ok=True)
    font = _font(14)
    for class_name in CLASS_NAMES:
        class_records = [record for record in records if record.class_name == class_name][:per_class]
        if not class_records:
            continue
        cell_width, cell_height, columns = 280, 230, 4
        rows = (len(class_records) + columns - 1) // columns
        sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), "white")
        draw = ImageDraw.Draw(sheet)
        for index, record in enumerate(class_records):
            x, y = (index % columns) * cell_width, (index // columns) * cell_height
            path = safe_local_path(data_root, record.local_path)
            try:
                with Image.open(path) as image:
                    thumb = image.convert("RGB")
                    thumb.thumbnail((cell_width - 12, 165), Image.Resampling.LANCZOS)
                    sheet.paste(thumb, (x + (cell_width - thumb.width) // 2, y + 4))
            except (OSError, ValueError):
                continue
            label = f"{Path(record.local_path).name}\n{record.split} | {record.source_title[:32]}"
            draw.multiline_text((x + 6, y + 174), label, fill="black", font=font, spacing=2)
        sheet.save(audit_root / f"{class_name}.jpg", quality=90)


def _target_is_scaffold(root: Path) -> bool:
    if not root.exists():
        return True
    for path in root.rglob("*"):
        if path.is_file() and path.name not in {".gitkeep", "README.md"}:
            return False
    return True


def ensure_target_safe(data_root: Path, resume: bool = False) -> None:
    if data_root.exists() and not resume and not _target_is_scaffold(data_root):
        raise FileExistsError(f"Target {data_root} contains files. Use --resume to add to it; automatic deletion is never offered.")
    for split in ("train", "val", "test"):
        for class_name in CLASS_NAMES:
            (data_root / split / class_name).mkdir(parents=True, exist_ok=True)


def cleanup_tool_temp_files(data_root: Path) -> int:
    """Remove only known PaletteCard staging files inside the exact target."""

    removed = 0
    if not data_root.exists():
        return removed
    for path in data_root.rglob(".palettecard-*"):
        if path.is_file() and path.name.startswith(".palettecard-") and path.suffix == ".part":
            path.unlink()
            removed += 1
    return removed


def _split_schedule(per_class: int, seed: int, class_index: int, current_counts: Mapping[str, int] | None = None) -> list[str]:
    """Return deterministic remaining split slots, honoring existing counts."""

    desired = split_counts(per_class)
    current = {split: int((current_counts or {}).get(split, 0)) for split in ("train", "val", "test")}
    if any(current[split] < 0 or current[split] > desired[split] for split in current):
        raise ValueError(f"Existing split counts exceed target for class index {class_index}: {current} vs {desired}")
    slots = [split for split, count in desired.items() for _ in range(count)]
    random.Random(seed + class_index).shuffle(slots)
    for split, count in current.items():
        for _ in range(count):
            try:
                slots.remove(split)
            except ValueError as exc:
                raise ValueError(f"Existing split counts cannot fit target for {split}: {current} vs {desired}") from exc
    return slots


def _progress_line(class_name: str, count: int, target: int, stats: Mapping[str, int], *, enabled: bool) -> None:
    if enabled and (count == target or count % 5 == 0):
        print(
            f"{class_name} {count}/{target} "
            f"(downloaded={stats['downloaded']}, rejected={stats['rejected']}, duplicates={stats['duplicates']})",
            flush=True,
        )


def _record_from_candidate(candidate: ImageCandidate, split: str, local_path: str, data: bytes, image_format: str, width: int, height: int) -> AttributionRecord:
    return AttributionRecord(
        candidate.class_name, split, local_path, candidate.source_category, candidate.source_title,
        candidate.page_url, candidate.file_url, candidate.creator, candidate.credit,
        candidate.license.name, candidate.license.url, candidate.license.raw_name,
        candidate.license.raw_url, candidate.license.usage_terms,
        candidate.license.attribution_required, width, height, sha256_bytes(data),
        average_hash(data), difference_hash(data), datetime.now(timezone.utc).isoformat(),
    )


def run_acquisition(*, data_root: str | Path = Paths().data, audit_root: str | Path = Paths().artifacts / "dataset_audit", categories: Mapping[str, str] = DEFAULT_CATEGORIES, per_class: int = 60, seed: int = 42, resume: bool = False, dry_run: bool = False, list_candidates: bool = False, client: CommonsClient | None = None, user_agent: str | None = None, pace_seconds: float = 0.25, min_width: int = 64, min_height: int = 64, max_bytes: int = 15_000_000, max_candidates: int | None = None, progress: bool = True, quiet: bool = False) -> list[AttributionRecord]:
    """Acquire a balanced starter set with one-image durable commits."""

    if set(categories) != set(CLASS_NAMES):
        raise ValueError(f"categories must contain exactly {list(CLASS_NAMES)}")
    if per_class < 1:
        raise ValueError("per_class must be at least 1")
    if pace_seconds < 0:
        raise ValueError("pace_seconds must be non-negative")
    data_root = Path(data_root).expanduser().resolve()
    audit_root = Path(audit_root).expanduser().resolve()
    client = client or CommonsClient(user_agent=user_agent, max_bytes=max_bytes, pacing=pace_seconds)
    progress_enabled = progress and not quiet

    if not (dry_run or list_candidates):
        # Remove only known interrupted staging files before safety checks.
        cleanup_tool_temp_files(data_root)
        ensure_target_safe(data_root, resume=resume)
    existing = read_manifest(data_root) if (resume and not (dry_run or list_candidates)) else []
    if resume and not (dry_run or list_candidates):
        validate_existing_manifest(data_root, existing, max_bytes=max_bytes, min_width=min_width, min_height=min_height)
    records = list(existing)
    seen_sha = {record.sha256 for record in records}
    seen_hash_pairs = [(record.average_hash, record.difference_hash) for record in records if record.average_hash and record.difference_hash]

    def commit_one(candidate: ImageCandidate, split: str, data: bytes, image_format: str, width: int, height: int) -> None:
        index = len([record for record in records if record.class_name == candidate.class_name]) + 1
        filename = f"{index:04d}-{_safe_slug(candidate.source_title, candidate.class_name)}{ALLOWED_FORMATS[image_format]}"
        relative = Path(split) / candidate.class_name / filename
        destination = safe_local_path(data_root, relative)
        while destination.exists():
            index += 1
            filename = f"{index:04d}-{_safe_slug(candidate.source_title, candidate.class_name)}{ALLOWED_FORMATS[image_format]}"
            relative = Path(split) / candidate.class_name / filename
            destination = safe_local_path(data_root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        record = _record_from_candidate(candidate, split, relative.as_posix(), data, image_format, width, height)
        appended = False
        try:
            _atomic_write_bytes(destination, data)
            records.append(record)
            appended = True
            write_manifests(records, data_root)
            mark_review_required(data_root)
        except KeyboardInterrupt:
            # If CSV was not replaced, remove only this tool-created image;
            # otherwise retain the complete durable record for resume.
            durable = False
            try:
                durable = any(item.local_path == record.local_path for item in read_manifest(data_root))
            except (OSError, ValueError):
                durable = False
            if not durable:
                if appended and records and records[-1].local_path == record.local_path:
                    records.pop()
                destination.unlink(missing_ok=True)
                write_manifests(records, data_root)
                mark_review_required(data_root)
            raise

    try:
        for class_index, class_name in enumerate(CLASS_NAMES):
            existing_count = sum(record.class_name == class_name for record in records)
            if existing_count > per_class:
                raise ValueError(f"Resume manifest already has {existing_count} {class_name} images; requested per-class target is {per_class}.")
            needed = per_class - existing_count
            if needed <= 0:
                _progress_line(class_name, existing_count, per_class, {"downloaded": 0, "rejected": 0, "duplicates": 0}, enabled=progress_enabled)
                continue
            candidates = category_candidates(client, class_name, categories[class_name], max_candidates or per_class * 3)
            if dry_run or list_candidates:
                print(f"{class_name}: {len(candidates)} accepted metadata candidates from {categories[class_name]}")
                for candidate in candidates:
                    print(f"  {candidate.source_title} | {candidate.license.name} | {candidate.file_url}")
                continue
            random.Random(seed + class_index).shuffle(candidates)
            current_counts = {split: sum(record.class_name == class_name and record.split == split for record in records) for split in ("train", "val", "test")}
            split_slots = _split_schedule(per_class, seed, class_index, current_counts)
            stats = {"downloaded": 0, "rejected": 0, "duplicates": 0}
            accepted_count = existing_count
            for candidate in candidates:
                if accepted_count >= per_class:
                    break
                try:
                    data = client.download(candidate.file_url)
                    stats["downloaded"] += 1
                    image_format, width, height, _ = validate_image_bytes(data, max_bytes=max_bytes, min_width=min_width, min_height=min_height)
                    digest = sha256_bytes(data)
                    ahash = average_hash(data)
                    dhash = difference_hash(data)
                except (OSError, RuntimeError, ValueError):
                    stats["rejected"] += 1
                    continue
                if digest in seen_sha or is_perceptual_duplicate(ahash, dhash, seen_hash_pairs):
                    stats["duplicates"] += 1
                    continue
                if not split_slots:
                    raise RuntimeError(f"No remaining split slot for {class_name}; existing split counts are inconsistent")
                split = split_slots.pop(0)
                commit_one(candidate, split, data, image_format, width, height)
                seen_sha.add(digest)
                seen_hash_pairs.append((ahash, dhash))
                accepted_count += 1
                _progress_line(class_name, accepted_count, per_class, stats, enabled=progress_enabled)
            if accepted_count < per_class:
                raise RuntimeError(
                    f"Only found {accepted_count} valid unique images for {class_name}; need {per_class} "
                    f"(downloaded={stats['downloaded']}, rejected={stats['rejected']}, duplicates={stats['duplicates']})."
                )
        if dry_run or list_candidates:
            return []
        write_manifests(records, data_root)
        mark_review_required(data_root)
    except KeyboardInterrupt:
        cleanup_tool_temp_files(data_root)
        try:
            write_manifests(records, data_root)
            mark_review_required(data_root)
        finally:
            raise
    generate_contact_sheets(records, data_root, audit_root)
    return records
