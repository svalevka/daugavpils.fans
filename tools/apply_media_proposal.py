#!/usr/bin/env python3
"""
Apply one approved media proposal (see review_app/) to the target band or
release: copy the staged media file into the archive, compute its sha256
checksum (and for video, duration and bitrate via ffprobe), update
band.yaml or release.yaml, upload the file to archive.org, sync metadata,
and update the consolidated metadata backup bundle.

Runs inside the GitHub Action (.github/workflows/apply-media-proposal.yml)
triggered when an approver clicks "Upload" in review_app, or on schedule.

Usage:
    python tools/apply_media_proposal.py \\
        --proposal-file proposal.json \\
        --media-file /path/to/media.bin \\
        [--bands-dir PATH] [--dry-run] [--skip-upload]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import unidecode
from pydantic import ValidationError

from archive_org import band_item_id, release_item_id
from models import ImageObject, MusicAlbum, MusicGroup, PropertyValue, VideoObject
from publish_to_archive_org import (
    band_metadata,
    publish_item,
    publish_metadata_bundle,
    release_metadata,
)
from validate import dump_yaml, ffprobe_av_info, load_yaml, sha256_of

REPO_ROOT = Path(__file__).resolve().parent.parent

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

MIME_EXTENSIONS: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}


class MediaApplyError(Exception):
    """A media proposal could not be applied. Message is user-safe."""


def sanitize_filename_stem(original_filename: str, fallback: str) -> str:
    stem = Path(original_filename).stem
    transliterated = unidecode.unidecode(stem).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return cleaned if len(cleaned) >= 2 else fallback


def resolve_media_extension(content_type: str, original_filename: str) -> str:
    if content_type in MIME_EXTENSIONS:
        return MIME_EXTENSIONS[content_type]
    ext = Path(original_filename).suffix.lower()
    return ext if ext else ".bin"


def _resolve_slug_dir(bands_dir: Path, slug: str, what: str) -> Path:
    if not SLUG_RE.match(slug):
        raise MediaApplyError(f"{what} {slug!r} is not a valid slug")
    resolved_bands_dir = bands_dir.resolve()
    candidate = (bands_dir / slug).resolve()
    if candidate.parent != resolved_bands_dir and candidate != resolved_bands_dir / slug:
        raise MediaApplyError(f"{what} {slug!r} does not resolve inside {bands_dir}")
    if not candidate.is_dir():
        raise MediaApplyError(f"{what} {slug!r} does not exist")
    return candidate


def load_band(bands_dir: Path, band_slug: str) -> tuple[Path, Path, MusicGroup]:
    band_dir = _resolve_slug_dir(bands_dir, band_slug, "band")
    band_yaml = band_dir / "band.yaml"
    if not band_yaml.exists():
        raise MediaApplyError(f"{band_dir} has no band.yaml")
    try:
        band = MusicGroup.model_validate(load_yaml(band_yaml))
    except ValidationError as e:
        raise MediaApplyError(f"{band_yaml}: schema validation failed:\n{e}") from e
    return band_dir, band_yaml, band


def load_release(band_dir: Path, release_slug: str) -> tuple[Path, Path, MusicAlbum]:
    if not SLUG_RE.match(release_slug):
        raise MediaApplyError(f"release slug {release_slug!r} is not a valid slug")
    release_dir = (band_dir / release_slug).resolve()
    if release_dir.parent != band_dir.resolve():
        raise MediaApplyError(f"release slug {release_slug!r} does not resolve inside {band_dir}")
    release_yaml = release_dir / "release.yaml"
    if not release_yaml.exists():
        raise MediaApplyError(f"{release_dir} has no release.yaml")
    try:
        release = MusicAlbum.model_validate(load_yaml(release_yaml))
    except ValidationError as e:
        raise MediaApplyError(f"{release_yaml}: schema validation failed:\n{e}") from e
    return release_dir, release_yaml, release


def determine_target_path(
    band_dir: Path,
    release_dir: Path | None,
    band_slug: str,
    proposal_id: int,
    original_filename: str,
    content_type: str,
    media_type: str,
    source_media_path: Path,
) -> tuple[Path, str]:
    """Determine (target_file_path, content_url) for placing the media file."""
    ext = resolve_media_extension(content_type, original_filename)
    fallback = f"{media_type}-{proposal_id}"
    stem = sanitize_filename_stem(original_filename, fallback)

    source_sha256 = sha256_of(source_media_path)

    if release_dir is not None:
        target_dir = release_dir
        if media_type == "video" and not stem.startswith("video-"):
            stem = f"video-{stem}"
        candidate_name = f"{stem}{ext}"
        candidate_path = target_dir / candidate_name
        if candidate_path.exists() and sha256_of(candidate_path) != source_sha256:
            candidate_name = f"{stem}-{proposal_id}{ext}"
            candidate_path = target_dir / candidate_name
        content_url = candidate_name
    else:
        target_dir = band_dir / "media"
        target_dir.mkdir(parents=True, exist_ok=True)
        if not stem.startswith(f"{band_slug}-"):
            stem = f"{band_slug}-{stem}"
        candidate_name = f"{stem}{ext}"
        candidate_path = target_dir / candidate_name
        if candidate_path.exists() and sha256_of(candidate_path) != source_sha256:
            candidate_name = f"{stem}-{proposal_id}{ext}"
            candidate_path = target_dir / candidate_name
        content_url = f"media/{candidate_name}"

    return candidate_path, content_url


def apply_media_proposal(
    proposal: dict[str, Any],
    media_file_path: Path,
    bands_dir: Path,
    *,
    dry_run: bool = False,
    skip_upload: bool = False,
) -> tuple[Path, Path, str]:
    """Applies the media proposal:
    1. Validates slugs and models.
    2. Determines filename and copies file.
    3. Computes sha256 (and ffprobe for video).
    4. Updates band.yaml / release.yaml.
    5. Uploads to archive.org (unless dry_run or skip_upload).
    Returns (target_file_path, target_yaml_path, content_url).
    """
    proposal_id = int(proposal.get("id", 0))
    band_slug = proposal["band_slug"]
    release_slug = proposal.get("release_slug") or None
    media_type = proposal["media_type"]
    original_filename = proposal.get("original_filename", "media")
    content_type = proposal.get("content_type", "")
    caption = proposal.get("caption") or None

    if media_type not in ("image", "video"):
        raise MediaApplyError(f"unsupported media_type {media_type!r}")

    if not media_file_path.exists():
        raise MediaApplyError(f"media file {media_file_path} not found")

    band_dir, band_yaml, band = load_band(bands_dir, band_slug)
    release_dir = release_yaml = release = None
    if release_slug:
        release_dir, release_yaml, release = load_release(band_dir, release_slug)

    target_file, content_url = determine_target_path(
        band_dir,
        release_dir,
        band_slug,
        proposal_id,
        original_filename,
        content_type,
        media_type,
        media_file_path,
    )

    if not dry_run:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(media_file_path, target_file)

    actual_sha256 = sha256_of(target_file if not dry_run else media_file_path)

    duration = None
    bitrate = None
    if media_type == "video":
        duration, bitrate = ffprobe_av_info(target_file if not dry_run else media_file_path)

    identifier = [PropertyValue(propertyID="sha256", value=actual_sha256)]

    if release is not None:
        target_yaml = release_yaml
        if media_type == "image":
            existing = next((img for img in release.image if img.contentUrl == content_url), None)
            if existing is not None:
                existing.identifier = identifier
                if caption:
                    existing.caption = caption
            else:
                release.image.append(
                    ImageObject(
                        contentUrl=content_url,
                        encodingFormat=content_type,
                        identifier=identifier,
                        caption=caption,
                        depicts=[],
                    )
                )
        else:
            name = caption or Path(original_filename).stem
            existing_v = next((vid for vid in release.video if vid.contentUrl == content_url), None)
            if existing_v is not None:
                existing_v.identifier = identifier
                existing_v.name = name
                existing_v.duration = duration
                existing_v.bitrate = bitrate
            else:
                release.video.append(
                    VideoObject(
                        contentUrl=content_url,
                        encodingFormat=content_type,
                        identifier=identifier,
                        name=name,
                        duration=duration,
                        bitrate=bitrate,
                    )
                )

        target_model = release
        item_id = release_item_id(band_slug, release_slug)
        metadata = release_metadata(release, band)
    else:
        target_yaml = band_yaml
        if media_type == "image":
            existing = next((img for img in band.image if img.contentUrl == content_url), None)
            if existing is not None:
                existing.identifier = identifier
                if caption:
                    existing.caption = caption
            else:
                band.image.append(
                    ImageObject(
                        contentUrl=content_url,
                        encodingFormat=content_type,
                        identifier=identifier,
                        caption=caption,
                        depicts=[],
                    )
                )
        else:
            name = caption or Path(original_filename).stem
            existing_v = next((vid for vid in band.video if vid.contentUrl == content_url), None)
            if existing_v is not None:
                existing_v.identifier = identifier
                existing_v.name = name
                existing_v.duration = duration
                existing_v.bitrate = bitrate
            else:
                band.video.append(
                    VideoObject(
                        contentUrl=content_url,
                        encodingFormat=content_type,
                        identifier=identifier,
                        name=name,
                        duration=duration,
                        bitrate=bitrate,
                    )
                )

        target_model = band
        item_id = band_item_id(band_slug)
        metadata = band_metadata(band)

    if not dry_run:
        dump_yaml(target_yaml, target_model.model_dump(by_alias=True, exclude_none=True))

    if not dry_run and not skip_upload:
        files = {content_url: target_file}
        ok = publish_item(item_id, files, metadata, dry_run=False, yaml_path=target_yaml)
        if not ok:
            raise MediaApplyError(f"failed to publish item {item_id} to archive.org")
        publish_metadata_bundle(bands_dir, dry_run=False)

    return target_file, target_yaml, content_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proposal-file", type=Path, required=True, help="path to proposal metadata as JSON"
    )
    parser.add_argument(
        "--media-file", type=Path, required=True, help="path to downloaded media binary file"
    )
    parser.add_argument(
        "--bands-dir",
        type=Path,
        default=REPO_ROOT / "bands",
        help="bands directory (default: repo bands/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="simulate without writing files or uploading"
    )
    parser.add_argument(
        "--skip-upload", action="store_true", help="write files locally but skip archive.org upload"
    )
    args = parser.parse_args()

    proposal = json.loads(args.proposal_file.read_text())

    try:
        target_file, target_yaml, content_url = apply_media_proposal(
            proposal,
            args.media_file,
            args.bands_dir,
            dry_run=args.dry_run,
            skip_upload=args.skip_upload,
        )
    except MediaApplyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"Applied media proposal #{proposal.get('id', 0)}:")
    print(f"  file: {target_file}")
    print(f"  contentUrl: {content_url}")
    print(f"  yaml: {target_yaml}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
