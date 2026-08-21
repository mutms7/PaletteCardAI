"""Import Wikimedia Commons File pages collected from Google Images.

The Google Images manifest is deliberately treated as an untrusted candidate
list.  This module resolves each Commons File page through the Action API,
checks the returned license and raster metadata, and writes a separate,
review-gated import tree.  It never approves or trains a dataset.

Acquisition is sequential.  A Wikimedia rate limit is a pause condition, not
a bad image: :class:`RateLimitPause` is raised immediately and the durable
manifest remains resumable without skipping the candidate that was limited.
"""

from __future__ import annotations

import csv
import email.utils
import hashlib
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from PIL import Image

from .commons_dataset import (
    ALLOWED_FORMATS,
    CLASS_NAMES,
    CommonsClient,
    ImageCandidate,
    average_hash,
    assign_splits,
    AttributionRecord,
    generate_contact_sheets,
    difference_hash,
    is_perceptual_duplicate,
    mark_review_required,
    parse_imageinfo,
    parse_license,
    read_manifest,
    safe_local_path,
    sha256_bytes,
    validate_existing_manifest,
    validate_image_bytes,
    write_manifests,
    ensure_target_safe,
)
from .config import Paths


DEFAULT_USER_AGENT = "PaletteCardAI/0.1 (commons; User:Mutms7)"
COMMONS_HOSTS = {"commons.wikimedia.org", "www.commons.wikimedia.org"}
GOOGLE_MANIFEST_FIELDS = [
    "class_name",
    "prompt",
    "rank",
    "source_page",
    "source_title",
    "local_path",
    "file_url",
    "creator",
    "credit",
    "license",
    "license_url",
    "license_raw",
    "license_url_raw",
    "usage_terms",
    "attribution_required",
    "width",
    "height",
    "sha256",
    "average_hash",
    "difference_hash",
    "downloaded_at",
]
GOOGLE_REVIEW_MARKER = "REVIEW_REQUIRED"
GOOGLE_MANIFEST_CSV = "google_attribution.csv"
GOOGLE_MANIFEST_JSON = "google_attribution.json"
GOOGLE_AUDIT_DIR = "google_dataset_audit"


@dataclass(frozen=True)
class GoogleCandidate:
    """One candidate row from the Google Images collection manifest."""

    class_name: str
    prompt: str
    rank: int
    source_page: str
    source_title: str = ""


@dataclass(frozen=True)
class CandidateRejection:
    line_number: int
    reason: str
    source_page: str = ""
    prompt: str = ""


@dataclass(frozen=True)
class GoogleAttributionRecord:
    class_name: str
    prompt: str
    rank: int
    source_page: str
    source_title: str
    local_path: str
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


class RateLimitPause(RuntimeError):
    """Signal that acquisition must stop at a server-specified retry time."""

    def __init__(self, url: str, retry_after_seconds: float | None, retry_after_raw: str = ""):
        self.url = url
        self.retry_after_seconds = retry_after_seconds
        self.retry_after = retry_after_seconds
        self.retry_after_raw = retry_after_raw
        detail = "unknown retry delay" if retry_after_seconds is None else f"retry after {retry_after_seconds:g} seconds"
        super().__init__(f"Wikimedia rate-limited this request; pause acquisition ({detail})")


def _safe_slug(value: str, fallback: str = "item") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value[:100] or fallback


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".palettecard-{path.name}-", suffix=".part", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(data)
        with temporary.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def commons_file_title(url: str) -> str:
    """Return the decoded ``File:`` title only for a canonical Commons page."""

    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme != "https" or parsed.netloc.lower() not in COMMONS_HOSTS:
        raise ValueError("source_page must be an HTTPS Wikimedia Commons File page")
    path = urllib.parse.unquote(parsed.path)
    if not path.startswith("/wiki/File:") or len(path) <= len("/wiki/File:"):
        raise ValueError("source_page must point to /wiki/File:<title>")
    title = path[len("/wiki/"):].replace("_", " ")
    if not title.startswith("File:"):
        raise ValueError("source_page must point to a File title")
    return title


def canonical_commons_page_url(url: str) -> str:
    """Normalize a Commons File URL and reject query/fragment ambiguity."""

    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "commons.wikimedia.org":
        raise ValueError("source_page must use the canonical Commons HTTPS host")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("source_page must not contain query, parameter, or fragment data")
    title = commons_file_title(url)
    encoded = urllib.parse.quote(title.replace(" ", "_"), safe=":()!,.-_~")
    return "https://commons.wikimedia.org/wiki/" + encoded


def source_page_key(url: str) -> tuple[str, str]:
    """Return canonical page and title keys for resume de-duplication."""

    canonical = canonical_commons_page_url(url)
    return canonical, commons_file_title(canonical).casefold()


def _canonical_file_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"upload.wikimedia.org", "commons.wikimedia.org"}:
        raise ValueError("file_url must be an HTTPS Wikimedia raster URL")
    if not parsed.path or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("file_url must not contain query, parameter, or fragment data")
    return url


def load_candidate_manifest(path: str | Path) -> tuple[list[GoogleCandidate], list[CandidateRejection]]:
    """Load valid Commons File rows and report rejected Google/navigation rows."""

    candidates: list[GoogleCandidate] = []
    rejected: list[CandidateRejection] = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, Mapping):
                    raise ValueError("row is not an object")
                class_name = str(row.get("class_name", "")).strip().lower()
                prompt = str(row.get("prompt", "")).strip()
                source_page = str(row.get("source_page", "")).strip()
                if class_name not in CLASS_NAMES:
                    raise ValueError("unknown class_name")
                if not prompt:
                    raise ValueError("missing prompt")
                commons_file_title(source_page)
                rank = int(row.get("rank", 0))
                if rank < 1:
                    raise ValueError("rank must be positive")
                candidates.append(GoogleCandidate(class_name, prompt, rank, source_page, str(row.get("title", "")).strip()))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raw: dict[str, Any] = {}
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, Mapping):
                        raw = dict(parsed)
                except (json.JSONDecodeError, TypeError):
                    pass
                rejected.append(CandidateRejection(line_number, str(exc), str(raw.get("source_page", "")), str(raw.get("prompt", ""))))
    return candidates, rejected


def _parse_retry_after(value: str | None, now: Callable[[], float] = time.time) -> float | None:
    """Parse Retry-After seconds or HTTP-date without imposing a cap."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, parsed.timestamp() - now())


class GoogleCommonsClient(CommonsClient):
    """Commons client with fail-closed 429 behavior for Google imports."""

    def _read(
        self,
        url: str,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff: float | None = None,
    ) -> bytes:
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
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": self.user_agent, "Accept": "application/json,image/*,*/*;q=0.8"},
                )
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
                if exc.code == 429:
                    raw = exc.headers.get("Retry-After", "") if exc.headers else ""
                    raise RateLimitPause(url, _parse_retry_after(raw), raw) from exc
                last_error = exc
                if exc.code not in (502, 503, 504) or attempt >= retry_limit:
                    break
                raw = exc.headers.get("Retry-After", "") if exc.headers else ""
                delay = _parse_retry_after(raw)
                if delay is None:
                    delay = retry_backoff * (2**attempt)
                self.sleeper(delay)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                if attempt >= retry_limit:
                    break
                self.sleeper(retry_backoff * (2**attempt))
        raise RuntimeError(f"Commons request failed after retries: {last_error}") from last_error


def _manifest_rows(records: Sequence[GoogleAttributionRecord]) -> list[dict[str, Any]]:
    return [asdict(record) for record in records]


def _manifest_csv_bytes(records: Sequence[GoogleAttributionRecord]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=GOOGLE_MANIFEST_FIELDS)
    writer.writeheader()
    writer.writerows(_manifest_rows(records))
    return stream.getvalue().encode("utf-8")


def _manifest_digest(records: Sequence[GoogleAttributionRecord]) -> tuple[bytes, str]:
    data = _manifest_csv_bytes(records)
    return data, sha256_bytes(data)


def write_google_manifests(records: Sequence[GoogleAttributionRecord], output_root: Path) -> str:
    """Atomically write authoritative CSV and hash-bound JSON sidecar."""

    csv_bytes, digest = _manifest_digest(records)
    _atomic_write_bytes(output_root / GOOGLE_MANIFEST_CSV, csv_bytes)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": digest,
        "manifest_id": f"sha256:{digest}",
        "records": _manifest_rows(records),
    }
    _atomic_write_bytes(
        output_root / GOOGLE_MANIFEST_JSON,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return digest


def _read_google_csv(output_root: Path) -> list[GoogleAttributionRecord]:
    path = output_root / GOOGLE_MANIFEST_CSV
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != GOOGLE_MANIFEST_FIELDS:
            raise ValueError(
                f"Google attribution manifest fields are invalid; expected {GOOGLE_MANIFEST_FIELDS}, got {reader.fieldnames}"
            )
        records: list[GoogleAttributionRecord] = []
        for line_number, row in enumerate(reader, 2):
            try:
                if any(row.get(key) is None for key in GOOGLE_MANIFEST_FIELDS):
                    raise ValueError("missing manifest field")
                values = {
                    key: (
                        int(row[key]) if key in {"rank", "width", "height"}
                        else row[key].strip().lower() == "true" if key == "attribution_required"
                        else row[key].strip()
                    )
                    for key in GOOGLE_MANIFEST_FIELDS
                }
                records.append(GoogleAttributionRecord(**values))
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid Google attribution row {line_number}") from exc
    return records


def _reconcile_google_sidecar(output_root: Path, records: Sequence[GoogleAttributionRecord]) -> str:
    """Treat CSV as authoritative and repair an interrupted/tampered sidecar."""

    csv_path = output_root / GOOGLE_MANIFEST_CSV
    digest = sha256_bytes(csv_path.read_bytes())
    expected_rows = _manifest_rows(records)
    sidecar = output_root / GOOGLE_MANIFEST_JSON
    matches = False
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        matches = (
            payload.get("manifest_sha256") == digest
            and payload.get("manifest_id") == f"sha256:{digest}"
            and payload.get("records") == expected_rows
        )
    except (OSError, json.JSONDecodeError):
        matches = False
    if not matches:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": digest,
            "manifest_id": f"sha256:{digest}",
            "records": expected_rows,
        }
        _atomic_write_bytes(sidecar, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    return digest


def read_google_manifest(output_root: Path, *, reconcile_sidecar: bool = True) -> list[GoogleAttributionRecord]:
    """Read the authoritative CSV and optionally repair its derived JSON sidecar."""

    records = _read_google_csv(output_root)
    if (output_root / GOOGLE_MANIFEST_CSV).exists() and reconcile_sidecar:
        _reconcile_google_sidecar(output_root, records)
    return records


def _validate_google_record(
    output_root: Path,
    record: GoogleAttributionRecord,
    *,
    max_bytes: int,
    min_width: int,
    min_height: int,
) -> tuple[str, str]:
    if record.class_name not in CLASS_NAMES:
        raise ValueError(f"invalid Google manifest class: {record.class_name}")
    if not record.prompt.strip() or record.rank < 1:
        raise ValueError("Google manifest prompt/rank is invalid")
    canonical_page = canonical_commons_page_url(record.source_page)
    title = commons_file_title(canonical_page)
    if record.source_title != title:
        raise ValueError(f"Google manifest source title does not match source page: {record.source_title}")
    _canonical_file_url(record.file_url)
    if not record.creator.strip() and record.license in {"CC BY", "CC BY-SA"}:
        raise ValueError("attribution-required Google record has no creator")
    if record.license in {"CC BY", "CC BY-SA"} and not record.license_url.strip():
        raise ValueError("attribution-required Google record has no canonical license URL")
    license_info = parse_license(
        {"LicenseShortName": record.license_raw, "LicenseUrl": record.license_url_raw},
        creator=record.creator,
    )
    if license_info is None or license_info.name != record.license or license_info.url != record.license_url:
        raise ValueError(f"Google manifest license is not in the allowed canonical allowlist: {record.source_title}")
    if bool(record.attribution_required) != bool(license_info.attribution_required):
        raise ValueError(f"Google manifest attribution flag is inconsistent: {record.source_title}")
    if not record.usage_terms.strip():
        raise ValueError(f"Google manifest usage terms are missing: {record.source_title}")
    local = safe_local_path(output_root, record.local_path)
    relative = local.relative_to(output_root.resolve())
    parts = relative.parts
    expected_prompt_slug = _safe_slug(record.prompt, "prompt")
    if len(parts) != 3 or parts[0] != record.class_name or parts[1] != expected_prompt_slug:
        raise ValueError(f"Google manifest local path is not organized by class/prompt: {record.local_path}")
    if not local.exists():
        raise ValueError(f"Google manifest file is missing: {record.local_path}")
    data = local.read_bytes()
    image_format, width, height, _ = validate_image_bytes(data, max_bytes=max_bytes, min_width=min_width, min_height=min_height)
    if sha256_bytes(data) != record.sha256:
        raise ValueError(f"Google manifest SHA-256 mismatch: {record.local_path}")
    if record.width != width or record.height != height:
        raise ValueError(f"Google manifest dimensions mismatch: {record.local_path}")
    if not re.fullmatch(r"[0-9a-f]{64}", record.sha256):
        raise ValueError(f"Google manifest SHA-256 is invalid: {record.local_path}")
    if not record.average_hash or not record.difference_hash:
        raise ValueError(f"Google manifest perceptual hashes are missing: {record.local_path}")
    return canonical_page, title.casefold()


def validate_google_staging(
    output_root: str | Path,
    *,
    max_bytes: int = 15_000_000,
    min_width: int = 64,
    min_height: int = 64,
) -> list[GoogleAttributionRecord]:
    """Fail closed on staging tampering before resume or promotion."""

    output = Path(output_root).expanduser().resolve()
    manifest = output / GOOGLE_MANIFEST_CSV
    if not manifest.exists():
        return []
    records = read_google_manifest(output, reconcile_sidecar=True)
    digest = sha256_bytes(manifest.read_bytes())
    marker = output / GOOGLE_REVIEW_MARKER
    if not marker.exists() or f"Manifest SHA-256: {digest}" not in marker.read_text(encoding="utf-8"):
        raise ValueError("Google staging review marker is missing or stale")
    source_pages: set[str] = set()
    source_titles: set[str] = set()
    seen_sha: set[str] = set()
    seen_pairs: list[tuple[str, str]] = []
    expected_paths: set[str] = set()
    for record in records:
        page_key, title_key = _validate_google_record(
            output, record, max_bytes=max_bytes, min_width=min_width, min_height=min_height
        )
        if page_key in source_pages or title_key in source_titles:
            raise ValueError(f"Duplicate canonical Google source page/title: {record.source_title}")
        source_pages.add(page_key)
        source_titles.add(title_key)
        if record.sha256 in seen_sha or is_perceptual_duplicate(record.average_hash, record.difference_hash, seen_pairs):
            raise ValueError(f"Duplicate Google staging image: {record.local_path}")
        seen_sha.add(record.sha256)
        seen_pairs.append((record.average_hash, record.difference_hash))
        expected_paths.add(record.local_path)
    for local in output.rglob("*"):
        if local.is_file() and local.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            if local.relative_to(output).as_posix() not in expected_paths:
                raise ValueError(f"Orphan raster is not in Google manifest: {local}")
    return records


def _validate_existing(
    output_root: Path,
    records: Sequence[GoogleAttributionRecord],
    max_bytes: int,
    min_width: int,
    min_height: int,
) -> None:
    """Validate every existing staging record before resume."""

    if not (output_root / GOOGLE_MANIFEST_CSV).exists():
        orphan = [
            path
            for path in output_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]
        if orphan:
            raise ValueError("Google staging resume requires a manifest; orphan raster files were found")
        return
    validated = validate_google_staging(
        output_root, max_bytes=max_bytes, min_width=min_width, min_height=min_height
    )
    if len(validated) != len(records):
        raise ValueError("Google staging manifest changed while validating resume")


def mark_google_review_required(output_root: Path, digest: str) -> None:
    _atomic_write_bytes(
        output_root / GOOGLE_REVIEW_MARKER,
        ("Human review required before training.\nManifest SHA-256: " + digest + "\n").encode("utf-8"),
    )


def _record_from_candidate(
    candidate: GoogleCandidate,
    resolved: ImageCandidate,
    data: bytes,
    local_path: str,
    width: int,
    height: int,
) -> GoogleAttributionRecord:
    canonical_title = commons_file_title(candidate.source_page)
    return GoogleAttributionRecord(
        candidate.class_name,
        candidate.prompt,
        candidate.rank,
        candidate.source_page,
        canonical_title,
        local_path,
        resolved.file_url,
        resolved.creator,
        resolved.credit,
        resolved.license.name,
        resolved.license.url,
        resolved.license.raw_name,
        resolved.license.raw_url,
        resolved.license.usage_terms,
        resolved.license.attribution_required,
        width,
        height,
        sha256_bytes(data),
        average_hash(data),
        difference_hash(data),
        datetime.now(timezone.utc).isoformat(),
    )


def _commit_google_record(
    *,
    records: list[GoogleAttributionRecord],
    record: GoogleAttributionRecord,
    data: bytes,
    output_root: Path,
) -> None:
    """Commit one image and its manifests, rolling back on interruption."""

    destination = safe_local_path(output_root, record.local_path)
    if destination.exists():
        raise FileExistsError(f"Google staging destination already exists: {record.local_path}")
    previous = list(records)
    try:
        _atomic_write_bytes(destination, data)
        records.append(record)
        digest = write_google_manifests(records, output_root)
        mark_google_review_required(output_root, digest)
    except BaseException:
        records[:] = previous
        destination.unlink(missing_ok=True)
        # Restore the last complete manifest.  Atomic writers ensure a
        # Ctrl-C during CSV/JSON replacement cannot leave a new image orphaned.
        restore_digest = write_google_manifests(records, output_root)
        mark_google_review_required(output_root, restore_digest)
        raise


def _standard_record_from_google(
    record: GoogleAttributionRecord,
    *,
    split: str,
    local_path: str,
) -> AttributionRecord:
    """Map reviewed staging provenance into the existing training manifest."""

    return AttributionRecord(
        record.class_name,
        split,
        local_path,
        record.prompt,
        record.source_title,
        record.source_page,
        record.file_url,
        record.creator,
        record.credit,
        record.license,
        record.license_url,
        record.license_raw,
        record.license_url_raw,
        record.usage_terms,
        record.attribution_required,
        record.width,
        record.height,
        record.sha256,
        record.average_hash,
        record.difference_hash,
        record.downloaded_at,
    )


def _commit_standard_record(
    *,
    records: list[AttributionRecord],
    record: AttributionRecord,
    data: bytes,
    data_root: Path,
) -> None:
    """Promote one reviewed file with the same rollback semantics as staging."""

    destination = safe_local_path(data_root, record.local_path)
    if destination.exists():
        raise FileExistsError(f"Promotion destination already exists: {record.local_path}")
    previous = list(records)
    try:
        _atomic_write_bytes(destination, data)
        records.append(record)
        write_manifests(records, data_root)
        mark_review_required(data_root)
    except BaseException:
        records[:] = previous
        destination.unlink(missing_ok=True)
        write_manifests(records, data_root)
        mark_review_required(data_root)
        raise


def _staging_as_standard_records(records: Sequence[GoogleAttributionRecord]) -> list[AttributionRecord]:
    """Adapt staging paths for the existing contact-sheet renderer."""

    return [_standard_record_from_google(record, split=record.prompt, local_path=record.local_path) for record in records]


def generate_google_contact_sheets(
    source_root: str | Path,
    records: Sequence[GoogleAttributionRecord],
    audit_root: str | Path,
) -> None:
    """Create contact sheets before promotion so review is a visible step."""

    if not records:
        raise ValueError("Cannot create Google contact sheets for an empty staging manifest")
    generate_contact_sheets(
        _staging_as_standard_records(records),
        Path(source_root).expanduser().resolve(),
        Path(audit_root).expanduser().resolve(),
        per_class=max(sum(record.class_name == class_name for record in records) for class_name in CLASS_NAMES),
    )


def promote_google_dataset(
    *,
    source_root: str | Path,
    data_root: str | Path = Paths().data,
    audit_root: str | Path = Paths().artifacts / GOOGLE_AUDIT_DIR,
    seed: int = 42,
    resume: bool = False,
    confirm_reviewed: bool = False,
    reviewer: str = "",
    max_bytes: int = 15_000_000,
    min_width: int = 64,
    min_height: int = 64,
) -> list[AttributionRecord]:
    """Explicitly promote reviewed Google staging files into train layout.

    The confirmation and reviewer are intentionally required at the library
    boundary, not just exposed as a CLI convention.  Promotion always leaves
    the standard dataset in ``REVIEW_REQUIRED`` state; approval remains a
    separate explicit ``approve_dataset`` action.
    """

    source = Path(source_root).expanduser().resolve()
    target = Path(data_root).expanduser().resolve()
    audit = Path(audit_root).expanduser().resolve()
    records = validate_google_staging(
        source, max_bytes=max_bytes, min_width=min_width, min_height=min_height
    )
    if not records:
        raise ValueError("Google staging manifest contains no accepted records")
    generate_google_contact_sheets(source, records, audit / "staging")
    if not confirm_reviewed or not reviewer.strip():
        raise ValueError(
            "Promotion requires explicit confirm_reviewed=True after contact sheets are reviewed, "
            "plus a non-empty reviewer"
        )
    ensure_target_safe(target, resume=resume)
    existing = read_manifest(target) if (target / "attribution.csv").exists() else []
    if existing:
        validate_existing_manifest(target, existing, max_bytes=max_bytes, min_width=min_width, min_height=min_height)
    promoted = list(existing)
    existing_pages: set[str] = set()
    existing_titles: set[str] = set()
    existing_sha = {record.sha256 for record in existing}
    existing_pairs = [
        (record.average_hash, record.difference_hash)
        for record in existing
        if record.average_hash and record.difference_hash
    ]
    for record in existing:
        try:
            page_key, title_key = source_page_key(record.page_url)
        except ValueError:
            continue
        existing_pages.add(page_key)
        existing_titles.add(title_key)
    existing_counts = {class_name: sum(record.class_name == class_name for record in existing) for class_name in CLASS_NAMES}
    ordered_all: list[tuple[GoogleAttributionRecord, str]] = []
    for class_index, class_name in enumerate(CLASS_NAMES):
        class_records = sorted(
            (record for record in records if record.class_name == class_name),
            key=lambda record: (record.prompt, record.rank, source_page_key(record.source_page)[0]),
        )
        if class_records:
            ordered_all.extend(assign_splits(class_records, seed=seed + class_index))
    for source_record, split in ordered_all:
        page_key, title_key = source_page_key(source_record.source_page)
        if page_key in existing_pages or title_key in existing_titles:
            continue
        # The target may already contain a recompressed/resized copy under a
        # different source page.  Skip it before choosing a new split so a
        # near-duplicate cannot leak across train/val/test on --resume.
        if source_record.sha256 in existing_sha or is_perceptual_duplicate(
            source_record.average_hash,
            source_record.difference_hash,
            existing_pairs,
        ):
            existing_pages.add(page_key)
            existing_titles.add(title_key)
            continue
        number = existing_counts[source_record.class_name] + 1
        suffix = Path(source_record.local_path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError(f"Promotion source has unsupported raster suffix: {source_record.local_path}")
        local = Path(split) / source_record.class_name / f"{number:04d}-{_safe_slug(source_record.source_title.removeprefix('File:'), 'image')}{suffix}"
        source_path = safe_local_path(source, source_record.local_path)
        data = source_path.read_bytes()
        if sha256_bytes(data) != source_record.sha256:
            raise ValueError(f"Promotion source changed during copy: {source_record.source_title}")
        standard_record = _standard_record_from_google(source_record, split=split, local_path=local.as_posix())
        _commit_standard_record(records=promoted, record=standard_record, data=data, data_root=target)
        existing_pages.add(page_key)
        existing_titles.add(title_key)
        existing_sha.add(source_record.sha256)
        existing_pairs.append((source_record.average_hash, source_record.difference_hash))
        existing_counts[source_record.class_name] += 1
    final_digest = mark_review_required(target)
    generate_contact_sheets(
        promoted,
        target,
        audit / "promoted",
        per_class=max(sum(record.class_name == class_name for record in promoted) for class_name in CLASS_NAMES),
    )
    # Keep the digest in the marker; do not create REVIEW_RECEIPT or approve.
    if not final_digest:
        raise RuntimeError("Promotion did not produce a review marker")
    return promoted


def _resolve_candidate(client: GoogleCommonsClient, candidate: GoogleCandidate, *, thumb_width: int = 1600) -> ImageCandidate | None:
    title = commons_file_title(candidate.source_page)
    payload = client.json(
        {
            "action": "query",
            "prop": "imageinfo",
            "titles": title,
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": str(max(256, thumb_width)),
        }
    )
    return parse_imageinfo(payload, candidate.class_name, candidate.prompt)


def run_google_import(
    *,
    input_path: str | Path = Paths().root / "data" / "google_candidates" / "search_candidates.jsonl",
    output_root: str | Path = Paths().root / "data" / "google_candidates" / "imported",
    per_prompt: int = 15,
    max_candidates: int | None = None,
    max_downloads: int | None = None,
    resume: bool = False,
    dry_run: bool = False,
    pace_seconds: float = 1.0,
    max_bytes: int = 15_000_000,
    min_width: int = 64,
    min_height: int = 64,
    thumb_width: int = 1600,
    client: GoogleCommonsClient | None = None,
    progress: bool = True,
) -> list[GoogleAttributionRecord]:
    """Resolve and import candidates, stopping durably on a rate limit."""

    if per_prompt < 1:
        raise ValueError("per_prompt must be at least 1")
    if max_candidates is not None and max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    if max_downloads is not None and max_downloads < 1:
        raise ValueError("max_downloads must be positive")
    if pace_seconds < 0:
        raise ValueError("pace_seconds must be non-negative")
    candidates, rejected_rows = load_candidate_manifest(input_path)
    if max_candidates is not None:
        candidates = candidates[:max_candidates]
    output = Path(output_root).expanduser().resolve()
    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
        existing = read_google_manifest(output) if resume else []
        if not resume and any(path.is_file() and path.name not in {".gitkeep", "README.md"} for path in output.rglob("*")):
            raise FileExistsError(f"Output {output} contains files; use --resume to add without deleting anything")
        if resume:
            _validate_existing(output, existing, max_bytes, min_width, min_height)
    else:
        existing = []
    records = list(existing)
    seen_sha = {record.sha256 for record in records}
    seen_pairs = [(record.average_hash, record.difference_hash) for record in records]
    accepted_source_pages: set[str] = set()
    accepted_source_titles: set[str] = set()
    for record in records:
        page_key, title_key = source_page_key(record.source_page)
        accepted_source_pages.add(page_key)
        accepted_source_titles.add(title_key)
    client = client or GoogleCommonsClient(
        user_agent=DEFAULT_USER_AGENT,
        max_bytes=max_bytes,
        pacing=pace_seconds,
        media_timeout=20.0,
        media_max_retries=1,
    )
    prompt_counts: dict[tuple[str, str], int] = {}
    for record in records:
        prompt_counts[(record.class_name, record.prompt)] = prompt_counts.get((record.class_name, record.prompt), 0) + 1
    stats = {"accepted": len(records), "rejected": len(rejected_rows), "duplicates": 0, "rate_limited": 0, "downloaded": 0}
    if progress and rejected_rows:
        print(f"candidate manifest: valid={len(candidates)} rejected={len(rejected_rows)} (mostly Google navigation rows)", flush=True)
    download_attempts = 0
    for candidate in candidates:
        key = (candidate.class_name, candidate.prompt)
        if prompt_counts.get(key, 0) >= per_prompt:
            continue
        try:
            candidate_page_key, candidate_title_key = source_page_key(candidate.source_page)
        except ValueError as exc:
            stats["rejected"] += 1
            if progress:
                print(f"rejected class={candidate.class_name} prompt={candidate.prompt!r} rank={candidate.rank} reason={exc}", flush=True)
            continue
        # Resume indexing happens before Action API metadata resolution and
        # before media access, so accepted candidates are never replayed.
        if candidate_page_key in accepted_source_pages or candidate_title_key in accepted_source_titles:
            if progress:
                print(f"skipped accepted source class={candidate.class_name} prompt={candidate.prompt!r} rank={candidate.rank}", flush=True)
            continue
        if max_downloads is not None and download_attempts >= max_downloads:
            break
        try:
            resolved = _resolve_candidate(client, candidate, thumb_width=thumb_width)
            if resolved is None:
                stats["rejected"] += 1
                if progress:
                    print(f"rejected class={candidate.class_name} prompt={candidate.prompt!r} rank={candidate.rank} reason=license-or-raster", flush=True)
                continue
            if dry_run:
                prompt_counts[key] = prompt_counts.get(key, 0) + 1
                if progress:
                    print(f"candidate class={candidate.class_name} prompt={candidate.prompt!r} rank={candidate.rank} license={resolved.license.name}", flush=True)
                continue
            download_attempts += 1
            data = client.download(resolved.file_url)
            stats["downloaded"] += 1
            image_format, width, height, _ = validate_image_bytes(data, max_bytes=max_bytes, min_width=min_width, min_height=min_height)
            digest = sha256_bytes(data)
            ahash = average_hash(data)
            dhash = difference_hash(data)
            if digest in seen_sha or is_perceptual_duplicate(ahash, dhash, seen_pairs):
                stats["duplicates"] += 1
                if progress:
                    print(f"duplicate class={candidate.class_name} prompt={candidate.prompt!r} rank={candidate.rank}", flush=True)
                continue
            prompt_slug = _safe_slug(candidate.prompt, "prompt")
            class_slug = _safe_slug(candidate.class_name, "class")
            title_slug = _safe_slug(resolved.source_title.removeprefix("File:"), "image")
            index = prompt_counts.get(key, 0) + 1
            local = Path(class_slug) / prompt_slug / f"{index:04d}-{title_slug}{ALLOWED_FORMATS[image_format]}"
            record = _record_from_candidate(candidate, resolved, data, local.as_posix(), width, height)
            _commit_google_record(records=records, record=record, data=data, output_root=output)
            seen_sha.add(digest)
            seen_pairs.append((ahash, dhash))
            accepted_source_pages.add(candidate_page_key)
            accepted_source_titles.add(candidate_title_key)
            prompt_counts[key] = index
            stats["accepted"] += 1
            if progress:
                print(
                    f"accepted class={candidate.class_name} prompt={candidate.prompt!r} "
                    f"{index}/{per_prompt} accepted={stats['accepted']} rejected={stats['rejected']} "
                    f"duplicates={stats['duplicates']} rate-limited={stats['rate_limited']}",
                    flush=True,
                )
        except RateLimitPause:
            stats["rate_limited"] += 1
            if progress:
                print(
                    f"rate-limited class={candidate.class_name} prompt={candidate.prompt!r} "
                    f"accepted={stats['accepted']} rejected={stats['rejected']} duplicates={stats['duplicates']} "
                    "acquisition paused; resume later without skipping this candidate",
                    flush=True,
                )
            if not dry_run and records:
                digest = write_google_manifests(records, output)
                mark_google_review_required(output, digest)
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            stats["rejected"] += 1
            if progress:
                print(f"rejected class={candidate.class_name} prompt={candidate.prompt!r} rank={candidate.rank} reason={exc}", flush=True)
    if not dry_run:
        digest = write_google_manifests(records, output)
        mark_google_review_required(output, digest)
    if progress:
        print(
            f"summary accepted={stats['accepted']} rejected={stats['rejected']} "
            f"duplicates={stats['duplicates']} rate-limited={stats['rate_limited']} "
            f"downloaded={stats['downloaded']}",
            flush=True,
        )
    return records


__all__ = [
    "CandidateRejection",
    "DEFAULT_USER_AGENT",
    "GOOGLE_MANIFEST_CSV",
    "GOOGLE_MANIFEST_FIELDS",
    "GoogleAttributionRecord",
    "GoogleCandidate",
    "GoogleCommonsClient",
    "RateLimitPause",
    "canonical_commons_page_url",
    "commons_file_title",
    "generate_google_contact_sheets",
    "load_candidate_manifest",
    "mark_google_review_required",
    "promote_google_dataset",
    "read_google_manifest",
    "run_google_import",
    "validate_google_staging",
    "write_google_manifests",
]
