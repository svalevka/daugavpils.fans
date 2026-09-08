#!/usr/bin/env python3
"""
Download every band/release's media (audio/image/video) from archive.org
into a clone of this repo, filling in the files bands/**/*.yaml describes
but that .gitignore keeps out of git. This is the inverse of
publish_to_archive_org.py: instead of uploading local media to archive.org,
it downloads media that's already there, using each band/release's item id
(archive_org.py) and each file's recorded sha256 to verify what lands on
disk.

Metadata itself is not fetched from anywhere else - bands/**/*.yaml is
already on disk from cloning this repo, and is the sole source of truth
for which bands/releases/files exist. This script never talks to GitHub
or any other metadata source, only archive.org, for exactly one job:
restoring the gitignored media around metadata you already have.

Every file downloaded is a public, unauthenticated HTTPS GET (see
archive_org.archive_org_url()) - no archive.org account, API key, or the
`internetarchive` package (upload-only concerns) are needed.

Safe to re-run: a file already on disk whose sha256 already matches the
recorded checksum is left alone rather than re-downloaded, so an
interrupted or partially-failed run can just be run again.

Usage:
    python tools/download_archive.py                  # download everything
    python tools/download_archive.py --bands-dir PATH  # a different tree
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

from archive_org import archive_org_url, band_item_id, release_item_id
from models import AudioObject, ImageObject, MusicAlbum, MusicGroup, VideoObject

REPO_ROOT = Path(__file__).resolve().parent.parent

MediaObject = AudioObject | ImageObject | VideoObject


@dataclass
class DownloadFailure:
    path: Path
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def recorded_sha256(media_item: MediaObject) -> str | None:
    for prop in media_item.identifier:
        if prop.propertyID == "sha256":
            return prop.value
    return None


def load_band(band_dir: Path) -> MusicGroup:
    raw = yaml.safe_load((band_dir / "band.yaml").read_text())
    return MusicGroup.model_validate(raw)


def load_release(release_dir: Path) -> MusicAlbum:
    raw = yaml.safe_load((release_dir / "release.yaml").read_text())
    return MusicAlbum.model_validate(raw)


def media_items_for_band(band: MusicGroup) -> list[MediaObject]:
    return [*band.image, *band.video]


def media_items_for_release(release: MusicAlbum) -> list[MediaObject]:
    return [t.audio for t in release.track] + [*release.image, *release.video]


def fetch_one(item_id: str, base_dir: Path, media_item: MediaObject, source_yaml: Path) -> DownloadFailure | None:
    """Download one media file into base_dir/media_item.contentUrl, unless
    it's already there with a matching checksum. Verifies against the
    recorded sha256 (when present) after downloading; a mismatch is
    reported as a failure rather than silently left on disk."""
    dest = base_dir / media_item.contentUrl
    expected = recorded_sha256(media_item)

    if dest.exists() and expected and sha256_of(dest) == expected:
        print(f"  already present, verified: {dest}")
        return None

    url = archive_org_url(item_id, media_item.contentUrl)
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading: {url} -> {dest}")
    try:
        urllib.request.urlretrieve(url, dest)
    except urllib.error.HTTPError as e:
        return DownloadFailure(dest, f"{e} fetching {url} (referenced by {source_yaml})")
    except urllib.error.URLError as e:
        return DownloadFailure(dest, f"{e.reason} fetching {url} (referenced by {source_yaml})")

    if expected is None:
        print(f"    (no sha256 recorded for {media_item.contentUrl}, could not verify)")
        return None

    actual = sha256_of(dest)
    if actual != expected:
        return DownloadFailure(
            dest,
            f"checksum mismatch after download (expected {expected}, got {actual}); "
            f"referenced by {source_yaml}",
        )
    return None


def download_band(band_dir: Path, band: MusicGroup) -> list[DownloadFailure]:
    band_yaml = band_dir / "band.yaml"
    item_id = band_item_id(band.slug)
    failures = []
    for media_item in media_items_for_band(band):
        failure = fetch_one(item_id, band_dir, media_item, band_yaml)
        if failure:
            failures.append(failure)
    return failures


def download_release(release_dir: Path, band_slug: str, release: MusicAlbum) -> list[DownloadFailure]:
    release_yaml = release_dir / "release.yaml"
    item_id = release_item_id(band_slug, release.slug)
    failures = []
    for media_item in media_items_for_release(release):
        failure = fetch_one(item_id, release_dir, media_item, release_yaml)
        if failure:
            failures.append(failure)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--bands-dir",
        type=Path,
        default=REPO_ROOT / "bands",
        help="directory to download into (default: this repo's bands/)",
    )
    args = parser.parse_args()
    bands_dir: Path = args.bands_dir

    all_failures: list[DownloadFailure] = []
    band_dirs = sorted(p for p in bands_dir.iterdir() if p.is_dir() and (p / "band.yaml").exists())

    for band_dir in band_dirs:
        band = load_band(band_dir)
        print(f"{band.slug}:")
        all_failures.extend(download_band(band_dir, band))

        release_dirs = sorted(p for p in band_dir.iterdir() if p.is_dir() and (p / "release.yaml").exists())
        for release_dir in release_dirs:
            release = load_release(release_dir)
            print(f"{band.slug}/{release.slug}:")
            all_failures.extend(download_release(release_dir, band.slug, release))

    print()
    if all_failures:
        print(f"{len(all_failures)} file(s) failed:\n")
        for failure in all_failures:
            print(f"- {failure}")
        print("\nRe-run this script to retry - already-downloaded, verified files are skipped.")
        return 1

    print("OK: every referenced media file is present on disk and checksum-verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
