import io
import json
import hashlib
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

from palette_card.commons_dataset import (
    CLASS_NAMES,
    DEFAULT_CATEGORIES,
    CommonsClient,
    cleanup_tool_temp_files,
    ImageCandidate,
    LicenseInfo,
    assign_splits,
    average_hash,
    difference_hash,
    is_perceptual_duplicate,
    parse_category_members,
    parse_imageinfo,
    parse_license,
    manifest_sha256,
    read_manifest,
    approve_dataset,
    validate_existing_manifest,
    validate_review_approval,
    run_acquisition,
    safe_local_path,
    split_counts,
    validate_user_agent,
    validate_image_bytes,
)


def _image_bytes(color: tuple[int, int, int], diagonal: bool = False, pattern: int = 0) -> bytes:
    image = Image.new("RGB", (96, 80), color)
    if diagonal:
        for tile_y in range(10):
            for tile_x in range(12):
                if (tile_x + tile_y + pattern) % 3 == 0:
                    for y in range(tile_y * 8, tile_y * 8 + 8):
                        for x in range(tile_x * 8, tile_x * 8 + 8):
                            image.putpixel((x, y), (255 - color[0], 255 - color[1], 255 - color[2]))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_license_allowlist_and_html_sanitization():
    accepted = parse_license({"LicenseShortName": {"value": "<b>CC BY-SA 4.0</b>"}, "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"}}, creator="Example artist")
    assert accepted is not None and accepted.name == "CC BY-SA" and accepted.attribution_required
    assert parse_license({"LicenseShortName": "Public domain"}).name == "Public domain"
    assert parse_license({"LicenseShortName": "CC0 1.0", "LicenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/"}).name == "CC0"
    assert parse_license({"LicenseShortName": "CC BY 4.0", "LicenseUrl": "https://creativecommons.org/licenses/by/4.0/"}) is None
    assert parse_license({"LicenseShortName": "CC BY 4.0", "LicenseUrl": "https://creativecommons.org/licenses/by/4.0/"}, creator="Artist") is not None
    assert parse_license({"LicenseShortName": "CC BY-NC 4.0"}) is None
    assert parse_license({"LicenseShortName": "CC BY-ND 4.0"}) is None
    assert parse_license({"LicenseShortName": "All rights reserved"}) is None


def test_api_parsers_prefer_thumbnail_and_reject_unsupported_mime():
    members = {"query": {"categorymembers": [{"title": "File:A.png"}, {"title": "Category:Ignore"}]}}
    assert parse_category_members(members) == ["File:A.png"]
    accepted = parse_imageinfo({"query": {"pages": [{"title": "File:A.png", "imageinfo": [{"thumburl": "https://upload.wikimedia.org/a.png", "mime": "image/png", "thumbwidth": 100, "thumbheight": 80, "extmetadata": {"LicenseShortName": "CC BY 4.0", "LicenseUrl": "https://creativecommons.org/licenses/by/4.0/", "Artist": {"value": "Ada <a href='//commons.wikimedia.org/wiki/User:Ada'>Ada</a>"}}}]}]}}, "flower", "Flowers")
    assert accepted is not None and accepted.file_url.endswith("a.png")
    assert "https://commons.wikimedia.org/wiki/User:Ada" in accepted.creator
    rejected = parse_imageinfo({"query": {"pages": [{"title": "File:A.svg", "imageinfo": [{"url": "https://upload.wikimedia.org/a.svg", "mime": "image/svg+xml", "extmetadata": {"LicenseShortName": "CC BY 4.0"}}]}]}}, "flower", "Flowers")
    assert rejected is None


def test_split_counts_are_balanced_and_deterministic():
    assert split_counts(60) == {"train": 42, "val": 9, "test": 9}
    assert split_counts(3) == {"train": 1, "val": 1, "test": 1}
    assert split_counts(1) == {"train": 1, "val": 0, "test": 0}
    assert split_counts(2) == {"train": 1, "val": 0, "test": 1}
    first = assign_splits(list(range(20)), seed=7)
    second = assign_splits(list(range(20)), seed=7)
    assert first == second
    assert {split for _, split in first} == {"train", "val", "test"}


def test_safe_paths_and_image_validation(tmp_path: Path):
    assert safe_local_path(tmp_path, "train/flower/photo.png").parent == tmp_path / "train" / "flower"
    with pytest.raises(ValueError, match="unsafe"):
        safe_local_path(tmp_path, "../outside.png")
    valid = _image_bytes((80, 120, 160), diagonal=True)
    assert validate_image_bytes(valid)[0] == "PNG"
    with pytest.raises(ValueError, match="invalid image"):
        validate_image_bytes(b"not an image")
    with pytest.raises(ValueError, match="max_bytes"):
        validate_image_bytes(valid, max_bytes=10)


def test_perceptual_duplicate_grouping_handles_resize_crop_and_burst():
    base = Image.open(io.BytesIO(_image_bytes((90, 120, 160), diagonal=True, pattern=2))).convert("RGB")
    variants = []
    resized = base.resize((128, 106), Image.Resampling.BICUBIC)
    cropped = base.crop((4, 3, 92, 77)).resize((96, 80), Image.Resampling.BICUBIC)
    burst = Image.new("RGB", base.size)
    for y in range(base.height):
        for x in range(base.width):
            r, g, b = base.getpixel((x, y))
            burst.putpixel((x, y), (min(255, r + 4), min(255, g + 4), min(255, b + 4)))
    for image in (resized, cropped, burst):
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        variants.append(stream.getvalue())
    first_pair = (average_hash(_image_bytes((90, 120, 160), diagonal=True, pattern=2)), difference_hash(_image_bytes((90, 120, 160), diagonal=True, pattern=2)))
    seen = [first_pair]
    assert all(is_perceptual_duplicate(average_hash(data), difference_hash(data), seen) for data in variants)
    different = _image_bytes((220, 35, 45), diagonal=True, pattern=14)
    assert not is_perceptual_duplicate(average_hash(different), difference_hash(different), seen)


class FakeCommonsClient:
    def __init__(self, image_map: dict[str, bytes]):
        self.image_map = image_map
        self.candidates: dict[str, str] = {}

    def json(self, params):
        if params.get("list") == "categorymembers":
            class_name = next(name for name, category in DEFAULT_CATEGORIES.items() if params["cmtitle"] == f"Category:{category}")
            titles = [f"File:{class_name}-{index}.png" for index in range(4)]
            return {"query": {"categorymembers": [{"title": title} for title in titles]}}
        pages = []
        for title in params["titles"].split("|"):
            pages.append({"title": title, "imageinfo": [{"thumburl": f"https://example.invalid/{title}", "mime": "image/png", "thumbwidth": 96, "thumbheight": 80, "extmetadata": {"LicenseShortName": "CC BY-SA 4.0", "LicenseUrl": "https://creativecommons.org/licenses/by-sa/4.0/", "Artist": {"value": "<b>Example artist</b>"}}}]})
        return {"query": {"pages": pages}}

    def download(self, url):
        filename = url.rsplit("/", 1)[-1]
        filename = filename.removeprefix("File:")
        return self.image_map[filename]


def test_mocked_acquisition_is_balanced_duplicate_safe_and_auditable(tmp_path: Path):
    image_map: dict[str, bytes] = {}
    class_colors = [(20, 30, 40), (220, 35, 45), (35, 210, 70), (45, 65, 225), (230, 180, 25)]
    for class_index, class_name in enumerate(CLASS_NAMES):
        for index in range(4):
            # One exact duplicate is deliberately shared with balloon's first
            # candidate; it must be skipped without affecting other classes.
            if class_name == "balloon" and index == 0:
                data = image_map["flower-0.png"]
            else:
                base = class_colors[class_index]
                data = _image_bytes(tuple(min(255, channel + index * 3) for channel in base), diagonal=True, pattern=class_index * 4 + index)
            image_map[f"{class_name}-{index}.png"] = data
    records = run_acquisition(data_root=tmp_path / "data", audit_root=tmp_path / "audit", per_class=3, seed=11, client=FakeCommonsClient(image_map))
    assert len(records) == 15
    assert {record.class_name for record in records} == set(CLASS_NAMES)
    assert len({record.sha256 for record in records}) == len(records)
    for class_name in CLASS_NAMES:
        counts = {split: sum(record.class_name == class_name and record.split == split for record in records) for split in ("train", "val", "test")}
        assert counts == {"train": 1, "val": 1, "test": 1}
        assert (tmp_path / "audit" / f"{class_name}.jpg").exists()
    assert (tmp_path / "data" / "attribution.csv").exists()
    assert (tmp_path / "data" / "attribution.json").exists()
    assert (tmp_path / "data" / "REVIEW_REQUIRED").exists()
    assert records[0].usage_terms and records[0].attribution_required
    manifest = tmp_path / "data" / "attribution.csv"
    sidecar = tmp_path / "data" / "attribution.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["manifest_sha256"] == manifest_sha256(tmp_path / "data")
    assert payload["manifest_id"] == f"sha256:{payload['manifest_sha256']}"
    payload["records"] = []
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    read_manifest(tmp_path / "data")
    repaired = json.loads(sidecar.read_text(encoding="utf-8"))
    assert len(repaired["records"]) == len(records)
    assert repaired["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_streamed_interruption_is_resumable_and_keeps_no_orphan(tmp_path: Path, capsys):
    image_map = {
        f"{class_name}-{index}.png": _image_bytes((30 + ci * 40, 70 + ci * 25, 120 + index * 8), diagonal=True, pattern=ci * 4 + index)
        for ci, class_name in enumerate(CLASS_NAMES) for index in range(4)
    }

    class InterruptingClient(FakeCommonsClient):
        def __init__(self, image_map):
            super().__init__(image_map)
            self.download_calls = 0

        def download(self, url):
            self.download_calls += 1
            if self.download_calls == 5:
                raise KeyboardInterrupt
            return super().download(url)

    data_root = tmp_path / "data"
    with pytest.raises(KeyboardInterrupt):
        run_acquisition(data_root=data_root, audit_root=tmp_path / "audit", per_class=3, client=InterruptingClient(image_map), progress=True)
    partial = read_manifest(data_root)
    assert len(partial) == 4
    validate_existing_manifest(data_root, partial)
    assert (data_root / "REVIEW_REQUIRED").exists()
    assert len([path for path in data_root.rglob("*") if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]) == 4
    progress_output = capsys.readouterr().out
    assert "flower 3/3" in progress_output and "downloaded=" in progress_output

    completed = run_acquisition(data_root=data_root, audit_root=tmp_path / "audit", per_class=3, resume=True, client=FakeCommonsClient(image_map), progress=False)
    assert len(completed) == 15
    validate_existing_manifest(data_root, completed)
    for class_name in CLASS_NAMES:
        assert {split: sum(record.class_name == class_name and record.split == split for record in completed) for split in ("train", "val", "test")} == {"train": 1, "val": 1, "test": 1}


def test_media_download_has_independent_short_retry_budget():
    timeouts = []

    def opener(request, timeout):
        timeouts.append(timeout)
        raise TimeoutError("media timeout")

    client = CommonsClient(user_agent="PaletteCardAI/0.1 (https://example.org/contact)", media_timeout=0.2, media_max_retries=0, opener=opener, sleeper=lambda _: None)
    with pytest.raises(RuntimeError, match="after retries"):
        client.download("https://example.org/image.png")
    assert timeouts == [0.2]


def test_cleanup_removes_only_known_staging_suffix(tmp_path: Path):
    known = tmp_path / ".palettecard-image.png-123.part"
    user_file = tmp_path / ".user-image.part"
    known.write_bytes(b"partial")
    user_file.write_bytes(b"keep")
    assert cleanup_tool_temp_files(tmp_path) == 1
    assert not known.exists() and user_file.exists()


def test_user_agent_requires_palettecard_and_contact():
    assert validate_user_agent("PaletteCardAI/0.1 (https://example.org/contact)")
    assert validate_user_agent("PaletteCard/0.1 (me@example.org)")
    with pytest.raises(ValueError, match="identify PaletteCard"):
        validate_user_agent("Example/1.0 (https://example.org)")
    with pytest.raises(ValueError, match="contact"):
        validate_user_agent("PaletteCardAI/0.1")


def test_resume_validation_and_explicit_review_receipt(tmp_path: Path):
    image_map = {f"{class_name}-{index}.png": _image_bytes((40 + ci * 35, 80 + ci * 20, 120 + index * 10), diagonal=True, pattern=ci * 4 + index)
                 for ci, class_name in enumerate(CLASS_NAMES) for index in range(4)}
    data_root = tmp_path / "data"
    records = run_acquisition(data_root=data_root, audit_root=tmp_path / "audit", per_class=3, client=FakeCommonsClient(image_map))
    validate_existing_manifest(data_root, records)
    with pytest.raises(ValueError, match="requires human review"):
        validate_review_approval(data_root)
    receipt = approve_dataset(data_root, "Reviewer", "checked contact sheets")
    assert receipt.exists()
    validate_review_approval(data_root)
    from palette_card.training import validate_data_layout
    validate_data_layout(data_root, minimum_train_images=1)
    first_path = data_root / records[0].local_path
    original_image = first_path.read_bytes()
    first_path.write_bytes(_image_bytes((7, 8, 9), diagonal=True, pattern=99))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_review_approval(data_root)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_data_layout(data_root)
    first_path.write_bytes(original_image)
    orphan = data_root / "train" / "flower" / "orphan.png"
    orphan.write_bytes(_image_bytes((11, 22, 33)))
    with pytest.raises(ValueError, match="Orphan raster"):
        validate_review_approval(data_root)
    with pytest.raises(ValueError, match="Orphan raster"):
        validate_data_layout(data_root)
    orphan.unlink()
    manifest = data_root / "attribution.csv"
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="stale"):
        validate_review_approval(data_root)
    first_path.unlink()
    with pytest.raises(ValueError, match="missing"):
        validate_existing_manifest(data_root, records)


def test_resume_validation_rejects_orphan_raster(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "train" / "flower").mkdir(parents=True)
    (data_root / "train" / "flower" / "orphan.png").write_bytes(_image_bytes((1, 2, 3)))
    with pytest.raises(ValueError, match="Orphan raster"):
        validate_existing_manifest(data_root, [])


def test_manifest_validation_accepts_relative_data_root(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_root = Path("data")
    (data_root / "train" / "flower").mkdir(parents=True)
    (data_root / "train" / "flower" / "orphan.png").write_bytes(_image_bytes((1, 2, 3)))
    with pytest.raises(ValueError, match="Orphan raster"):
        validate_existing_manifest(data_root, [])


def test_category_imageinfo_is_batched_in_groups_of_fifty():
    class BatchSpy:
        def __init__(self):
            self.title_batches = []

        def json(self, params):
            if params.get("list") == "categorymembers":
                return {"query": {"categorymembers": [{"title": f"File:X-{i}.png"} for i in range(101)]}}
            titles = params["titles"].split("|")
            self.title_batches.append(titles)
            return {"query": {"pages": [{"title": title, "imageinfo": [{"thumburl": f"https://example.invalid/{title}", "mime": "image/png", "thumbwidth": 96, "thumbheight": 80, "extmetadata": {"LicenseShortName": "CC0 1.0", "LicenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/"}}]} for title in titles]}}

    from palette_card.commons_dataset import category_candidates
    spy = BatchSpy()
    candidates = category_candidates(spy, "flower", "Flowers", max_candidates=101)
    assert len(candidates) == 101
    assert [len(batch) for batch in spy.title_batches] == [50, 50, 1]


def test_commons_client_retries_retry_after_without_network():
    class Response:
        headers = {"Content-Length": "2"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return b"{}"

    calls = []
    waits = []
    def opener(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "0"}, None)
        return Response()

    import urllib.error
    client = CommonsClient(user_agent="PaletteCardAI/0.1 (https://example.org/contact)", max_retries=1, pacing=0, opener=opener, sleeper=waits.append)
    assert client.json({"action": "query"}) == {}
    assert len(calls) == 2 and waits == [0.0]


def test_training_refuses_unapproved_commons_manifest(tmp_path: Path):
    from palette_card.training import validate_data_layout

    for split in ("train", "val", "test"):
        for class_name in CLASS_NAMES:
            folder = tmp_path / split / class_name
            folder.mkdir(parents=True)
    (tmp_path / "attribution.csv").write_text("class_name\n", encoding="utf-8")
    with pytest.raises(ValueError, match="human review"):
        validate_data_layout(tmp_path, minimum_train_images=0)
