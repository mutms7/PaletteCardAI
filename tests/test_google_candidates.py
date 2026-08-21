from __future__ import annotations

import io
import json
from pathlib import Path
import urllib.error
import email.utils

import pytest
from PIL import Image, ImageDraw

from palette_card.commons_dataset import (
    AttributionRecord,
    average_hash,
    difference_hash,
    mark_review_required,
    read_manifest,
    sha256_bytes,
    write_manifests,
)
from palette_card.google_candidates import (
    DEFAULT_USER_AGENT,
    GoogleCommonsClient,
    RateLimitPause,
    commons_file_title,
    mark_google_review_required,
    load_candidate_manifest,
    promote_google_dataset,
    read_google_manifest,
    run_google_import,
    validate_google_staging,
)
import palette_card.google_candidates as google_module


def _image_bytes(color: tuple[int, int, int], marker: int = 0) -> bytes:
    image = Image.new("RGB", (100, 80), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((marker % 40, 10, 70, 60), outline=(255 - color[0], 255 - color[1], 255 - color[2]), width=3)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _candidate_payload(title: str, file_url: str) -> dict:
    return {
        "query": {
            "pages": [
                {
                    "title": title,
                    "imageinfo": [
                        {
                            "thumburl": file_url,
                            "mime": "image/png",
                            "thumbwidth": 100,
                            "thumbheight": 80,
                            "extmetadata": {
                                "LicenseShortName": "CC BY 4.0",
                                "LicenseUrl": "https://creativecommons.org/licenses/by/4.0/",
                                "Artist": {"value": "Example Artist"},
                                "Credit": {"value": "Example archive"},
                            },
                        }
                    ],
                }
            ]
        }
    }


def _write_candidates(path: Path, *rows: dict) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_commons_file_title_requires_https_file_page():
    assert commons_file_title("https://commons.wikimedia.org/wiki/File:Red_Flower.jpg") == "File:Red Flower.jpg"
    with pytest.raises(ValueError, match="HTTPS Wikimedia Commons File"):
        commons_file_title("https://www.google.com/search?q=flower")
    with pytest.raises(ValueError, match="File:<title>"):
        commons_file_title("https://commons.wikimedia.org/wiki/Category:Flowers")


def test_candidate_loader_rejects_navigation_rows(tmp_path: Path):
    path = tmp_path / "candidates.jsonl"
    _write_candidates(
        path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.jpg"},
        {"class_name": "flower", "prompt": "single", "rank": 2, "source_page": "https://www.google.com/search?q=flower", "title": "All"},
        {"class_name": "not-a-class", "prompt": "single", "rank": 3, "source_page": "https://commons.wikimedia.org/wiki/File:B.jpg"},
    )
    candidates, rejected = load_candidate_manifest(path)
    assert len(candidates) == 1
    assert len(rejected) == 2
    assert {item.reason for item in rejected} == {"source_page must be an HTTPS Wikimedia Commons File page", "unknown class_name"}


def test_google_client_aborts_429_and_preserves_exact_retry_after():
    calls = []
    waits = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "120"}, None)

    client = GoogleCommonsClient(
        user_agent=DEFAULT_USER_AGENT,
        max_retries=4,
        pacing=0,
        opener=opener,
        sleeper=waits.append,
    )
    with pytest.raises(RateLimitPause) as caught:
        client.json({"action": "query"})
    assert caught.value.retry_after_seconds == 120
    assert caught.value.retry_after_raw == "120"
    assert len(calls) == 1
    assert waits == []


def test_google_client_does_not_cap_retry_after_for_transient_503():
    waits = []
    calls = []

    class Response:
        headers = {"Content-Length": "2"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return b"{}"

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {"Retry-After": "120"}, None)
        return Response()

    client = GoogleCommonsClient(
        user_agent=DEFAULT_USER_AGENT,
        max_retries=1,
        pacing=0,
        opener=opener,
        sleeper=waits.append,
    )
    assert client.json({"action": "query"}) == {}
    assert waits == [120]
    assert len(calls) == 2


def test_retry_after_http_date_is_parsed_without_cap():
    now = 1_700_000_000.0
    header = email.utils.format_datetime(__import__("datetime").datetime.fromtimestamp(now + 120, tz=__import__("datetime").timezone.utc), usegmt=True)
    assert google_module._parse_retry_after(header, now=lambda: now) == 120


class FakeGoogleClient:
    def __init__(self, image_map: dict[str, bytes], rate_limit: bool = False):
        self.image_map = image_map
        self.rate_limit = rate_limit
        self.download_calls: list[str] = []

    def json(self, params):
        title = params["titles"]
        file_url = f"https://upload.wikimedia.org/{title.removeprefix('File:')}"
        return _candidate_payload(title, file_url)

    def download(self, url):
        self.download_calls.append(url)
        if self.rate_limit:
            raise RateLimitPause(url, 60, "60")
        return self.image_map[url.rsplit("/", 1)[-1]]


def test_import_is_prompt_organized_globally_deduplicated_and_review_gated(tmp_path: Path):
    input_path = tmp_path / "candidates.jsonl"
    rows = [
        {"class_name": "flower", "prompt": "single flower", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:Flower-A.png"},
        {"class_name": "flower", "prompt": "bouquet", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:Flower-B.png"},
        {"class_name": "heart", "prompt": "heart symbol", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:Heart-A.png"},
    ]
    _write_candidates(input_path, *rows)
    duplicate = _image_bytes((180, 40, 60), marker=2)
    image_map = {
        "Flower-A.png": duplicate,
        "Flower-B.png": duplicate,
        "Heart-A.png": _image_bytes((40, 80, 220), marker=26),
    }
    output = tmp_path / "imported"
    records = run_google_import(
        input_path=input_path,
        output_root=output,
        per_prompt=1,
        pace_seconds=0,
        client=FakeGoogleClient(image_map),
        progress=False,
    )
    assert len(records) == 2
    assert {record.class_name for record in records} == {"flower", "heart"}
    assert all(Path(record.local_path).parts[0] in {"flower", "heart"} for record in records)
    assert all(len(Path(record.local_path).parts) == 3 for record in records)
    assert len({record.sha256 for record in records}) == len(records)
    assert (output / "google_attribution.csv").exists()
    assert (output / "google_attribution.json").exists()
    assert (output / "REVIEW_REQUIRED").exists()
    assert not (output / "REVIEW_RECEIPT.json").exists()
    assert len(read_google_manifest(output)) == 2


def test_import_rate_limit_stops_before_next_candidate(tmp_path: Path):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.png"},
        {"class_name": "flower", "prompt": "single", "rank": 2, "source_page": "https://commons.wikimedia.org/wiki/File:B.png"},
    )
    client = FakeGoogleClient({}, rate_limit=True)
    with pytest.raises(RateLimitPause):
        run_google_import(input_path=input_path, output_root=tmp_path / "imported", per_prompt=2, pace_seconds=0, client=client, progress=False)
    assert len(client.download_calls) == 1
    assert not (tmp_path / "imported" / "google_attribution.csv").exists()


def test_resume_after_rate_limit_indexes_accepted_source_before_api_and_media(tmp_path: Path):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.png"},
        {"class_name": "flower", "prompt": "single", "rank": 2, "source_page": "https://commons.wikimedia.org/wiki/File:B.png"},
    )

    class FirstRunClient(FakeGoogleClient):
        def __init__(self, limit_b=True):
            super().__init__({"A.png": _image_bytes((180, 40, 60), marker=2), "B.png": _image_bytes((40, 80, 220), marker=26)})
            self.json_titles = []
            self.limit_b = limit_b

        def json(self, params):
            self.json_titles.append(params["titles"])
            return super().json(params)

        def download(self, url):
            self.download_calls.append(url)
            if self.limit_b and url.endswith("B.png"):
                raise RateLimitPause(url, 30, "30")
            return self.image_map[url.rsplit("/", 1)[-1]]

    first = FirstRunClient()
    output = tmp_path / "imported"
    with pytest.raises(RateLimitPause):
        run_google_import(input_path=input_path, output_root=output, per_prompt=2, pace_seconds=0, client=first, progress=False)
    assert first.json_titles == ["File:A.png", "File:B.png"]
    assert len(read_google_manifest(output)) == 1

    second = FirstRunClient(limit_b=False)
    records = run_google_import(input_path=input_path, output_root=output, per_prompt=2, resume=True, pace_seconds=0, client=second, progress=False)
    assert len(records) == 2
    assert second.json_titles == ["File:B.png"]
    assert second.download_calls == ["https://upload.wikimedia.org/B.png"]


def test_dry_run_never_downloads(tmp_path: Path):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.png"},
    )

    class NoDownload(FakeGoogleClient):
        def download(self, url):
            raise AssertionError("dry run must not download media")

    records = run_google_import(input_path=input_path, output_root=tmp_path / "imported", dry_run=True, client=NoDownload({}), progress=False)
    assert records == []
    assert not (tmp_path / "imported").exists()


def test_import_interruption_rolls_back_image_and_manifest(tmp_path: Path, monkeypatch):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.png"},
    )
    output = tmp_path / "imported"
    client = FakeGoogleClient({"A.png": _image_bytes((180, 40, 60), marker=2)})
    original = google_module.write_google_manifests
    calls = {"count": 0}

    def interrupt_after_manifest(records, root):
        calls["count"] += 1
        digest = original(records, root)
        if calls["count"] == 1:
            raise KeyboardInterrupt
        return digest

    monkeypatch.setattr(google_module, "write_google_manifests", interrupt_after_manifest)
    with pytest.raises(KeyboardInterrupt):
        run_google_import(input_path=input_path, output_root=output, per_prompt=1, pace_seconds=0, client=client, progress=False)
    assert read_google_manifest(output) == []
    assert not list(output.rglob("*.png"))
    assert (output / "REVIEW_REQUIRED").exists()


def test_google_staging_validation_reconciles_sidecar_and_rejects_tampering(tmp_path: Path):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.png"},
    )
    output = tmp_path / "imported"
    run_google_import(
        input_path=input_path,
        output_root=output,
        per_prompt=1,
        pace_seconds=0,
        client=FakeGoogleClient({"A.png": _image_bytes((180, 40, 60), marker=2)}),
        progress=False,
    )
    sidecar = output / "google_attribution.json"
    sidecar.write_text(json.dumps({"manifest_sha256": "tampered", "records": []}), encoding="utf-8")
    assert len(validate_google_staging(output)) == 1
    csv_path = output / "google_attribution.csv"
    csv_path.write_text(csv_path.read_text(encoding="utf-8").replace("CC BY 4.0", "All rights reserved"), encoding="utf-8")
    mark_google_review_required(output, google_module.sha256_bytes(csv_path.read_bytes()))
    with pytest.raises(ValueError, match="license"):
        validate_google_staging(output)


def test_resume_without_google_manifest_rejects_orphan_raster(tmp_path: Path):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.png"},
    )
    output = tmp_path / "imported"
    (output / "flower" / "single").mkdir(parents=True)
    (output / "flower" / "single" / "orphan.png").write_bytes(_image_bytes((180, 40, 60), marker=2))
    with pytest.raises(ValueError, match="orphan raster"):
        run_google_import(input_path=input_path, output_root=output, resume=True, client=FakeGoogleClient({}), progress=False)


def test_promotion_requires_confirmation_then_writes_standard_review_gated_layout(tmp_path: Path):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.png"},
        {"class_name": "heart", "prompt": "symbol", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:B.png"},
    )
    source = tmp_path / "imported"
    run_google_import(
        input_path=input_path,
        output_root=source,
        per_prompt=1,
        pace_seconds=0,
        client=FakeGoogleClient({"A.png": _image_bytes((180, 40, 60), marker=2), "B.png": _image_bytes((40, 80, 220), marker=26)}),
        progress=False,
    )
    target = tmp_path / "data"
    with pytest.raises(ValueError, match="requires.*confirm_reviewed"):
        promote_google_dataset(source_root=source, data_root=target, audit_root=tmp_path / "audit", reviewer="Reviewer")
    assert not (target / "attribution.csv").exists()
    promoted = promote_google_dataset(
        source_root=source,
        data_root=target,
        audit_root=tmp_path / "audit",
        confirm_reviewed=True,
        reviewer="Reviewer",
    )
    assert len(promoted) == 2
    assert all((target / split / class_name).exists() for split in ("train", "val", "test") for class_name in ("flower", "heart", "ring", "cake", "balloon"))
    assert (target / "attribution.csv").exists()
    assert (target / "REVIEW_REQUIRED").exists()
    assert not (target / "REVIEW_RECEIPT.json").exists()
    assert (tmp_path / "audit" / "staging" / "flower.jpg").exists()
    assert (tmp_path / "audit" / "promoted" / "flower.jpg").exists()


def test_promotion_interruption_rolls_back_standard_file(tmp_path: Path, monkeypatch):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:A.png"},
    )
    source = tmp_path / "imported"
    run_google_import(
        input_path=input_path,
        output_root=source,
        per_prompt=1,
        pace_seconds=0,
        client=FakeGoogleClient({"A.png": _image_bytes((180, 40, 60), marker=2)}),
        progress=False,
    )
    target = tmp_path / "data"
    original = google_module.write_manifests
    calls = {"count": 0}

    def interrupt_after_standard_manifest(records, root):
        calls["count"] += 1
        original(records, root)
        if calls["count"] == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(google_module, "write_manifests", interrupt_after_standard_manifest)
    with pytest.raises(KeyboardInterrupt):
        promote_google_dataset(
            source_root=source,
            data_root=target,
            audit_root=tmp_path / "audit",
            confirm_reviewed=True,
            reviewer="Reviewer",
        )
    assert len(read_google_manifest(source)) == 1
    assert (target / "attribution.csv").exists()
    assert not list(target.rglob("*.png"))


def test_promotion_resume_skips_recompressed_duplicate_in_existing_split(tmp_path: Path):
    input_path = tmp_path / "candidates.jsonl"
    _write_candidates(
        input_path,
        {"class_name": "flower", "prompt": "single", "rank": 1, "source_page": "https://commons.wikimedia.org/wiki/File:Staging.png"},
    )
    base = _image_bytes((180, 40, 60), marker=2)
    source = tmp_path / "imported"
    run_google_import(
        input_path=input_path,
        output_root=source,
        per_prompt=1,
        pace_seconds=0,
        client=FakeGoogleClient({"Staging.png": base}),
        progress=False,
    )
    with Image.open(io.BytesIO(base)) as image:
        equivalent = io.BytesIO()
        image.convert("RGB").resize((120, 96), Image.Resampling.BICUBIC).save(equivalent, format="JPEG", quality=88)
    equivalent_bytes = equivalent.getvalue()
    assert google_module.is_perceptual_duplicate(
        average_hash(base),
        difference_hash(base),
        [(average_hash(equivalent_bytes), difference_hash(equivalent_bytes))],
    )

    target = tmp_path / "data"
    existing_path = target / "test" / "flower" / "0001-existing.jpg"
    existing_path.parent.mkdir(parents=True)
    existing_path.write_bytes(equivalent_bytes)
    existing = AttributionRecord(
        "flower",
        "test",
        "test/flower/0001-existing.jpg",
        "existing source",
        "File:Existing.jpg",
        "https://commons.wikimedia.org/wiki/File:Existing.jpg",
        "https://upload.wikimedia.org/existing.jpg",
        "Example Artist",
        "Example archive",
        "CC BY",
        "https://creativecommons.org/licenses/by/4.0/",
        "CC BY 4.0",
        "https://creativecommons.org/licenses/by/4.0/",
        "CC BY; attribution required; preserve creator and license link.",
        True,
        120,
        96,
        sha256_bytes(equivalent_bytes),
        average_hash(equivalent_bytes),
        difference_hash(equivalent_bytes),
        "2026-01-01T00:00:00+00:00",
    )
    write_manifests([existing], target)
    mark_review_required(target)
    promoted = promote_google_dataset(
        source_root=source,
        data_root=target,
        audit_root=tmp_path / "audit",
        resume=True,
        confirm_reviewed=True,
        reviewer="Reviewer",
    )
    assert len(promoted) == 1
    assert len(read_manifest(target)) == 1
    assert [record.split for record in promoted] == ["test"]
    assert not list((target / "train" / "flower").glob("*"))
