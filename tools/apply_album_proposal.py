#!/usr/bin/env python3
"""
Apply one approved album proposal (see review_app/) to the target band:
create the release directory under bands/<band-slug>/<release-slug>/, copy
staged audio files and optional cover image into the archive, compute sha256
checksums and ffprobe duration/bitrate, create release.yaml, upload the
release item to archive.org, sync metadata, and update the consolidated
metadata backup bundle.

Runs inside the GitHub Action (.github/workflows/apply-album-proposal.yml)
triggered when an approver clicks "Upload & Publish" in review_app, or on schedule.

Usage:
    python tools/apply_album_proposal.py \
        --proposal-file proposal.json \
        --tracks-dir /path/to/tracks/ \
        [--cover-file /path/to/cover.jpg] \
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

from archive_org import release_item_id
from models import AudioObject, ImageObject, MusicAlbum, MusicGroup, MusicRecording, PropertyValue
from publish_to_archive_org import (
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
    "audio/mpeg": ".mp3",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
}


class AlbumApplyError(Exception):
    """An album proposal could not be applied. Message is user-safe."""


def sanitize_filename_stem(title: str, fallback: str = "track") -> str:
    transliterated = unidecode.unidecode(title).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return cleaned if len(cleaned) >= 1 else fallback


def resolve_media_extension(content_type: str, original_filename: str) -> str:
    if content_type in MIME_EXTENSIONS:
        return MIME_EXTENSIONS[content_type]
    ext = Path(original_filename).suffix.lower()
    return ext if ext else ".mp3"


def load_band(bands_dir: Path, band_slug: str) -> tuple[Path, Path, MusicGroup]:
    if not SLUG_RE.match(band_slug):
        raise AlbumApplyError(f"Band slug {band_slug!r} is invalid")
    band_dir = (bands_dir / band_slug).resolve()
    band_yaml = band_dir / "band.yaml"
    if not band_yaml.exists():
        raise AlbumApplyError(f"{band_dir} has no band.yaml")
    try:
        band = MusicGroup.model_validate(load_yaml(band_yaml))
    except ValidationError as e:
        raise AlbumApplyError(f"{band_yaml}: schema validation failed:\n{e}") from e
    return band_dir, band_yaml, band


def apply_album_proposal(
    proposal: dict[str, Any],
    tracks_dir: Path,
    cover_file: Path | None,
    bands_dir: Path,
    dry_run: bool = False,
    skip_upload: bool = False,
) -> Path:
    band_slug = proposal["band_slug"]
    release_slug = proposal["release_slug"]
    band_dir, band_yaml, band = load_band(bands_dir, band_slug)

    release_dir = band_dir / release_slug
    if release_dir.exists() and (release_dir / "release.yaml").exists():
        raise AlbumApplyError(f"Release directory {release_dir} already exists")

    if not dry_run:
        release_dir.mkdir(parents=True, exist_ok=True)

    images: list[ImageObject] = []
    files_to_upload: dict[str, Path] = {}

    # Handle cover image if present
    if cover_file and cover_file.exists():
        cover_ext = cover_file.suffix.lower() or ".jpg"
        if cover_ext in (".jpeg", ".jpg"):
            dest_cover_name = "cover.jpg"
        elif cover_ext in (".png", ".webp"):
            dest_cover_name = f"cover{cover_ext}"
        else:
            dest_cover_name = "cover.jpg"

        dest_cover_path = release_dir / dest_cover_name
        if not dry_run:
            shutil.copy2(cover_file, dest_cover_path)
            cover_sha = sha256_of(dest_cover_path)
        else:
            dest_cover_path = cover_file
            cover_sha = "dryrun-sha256"

        mime_type = "image/jpeg"
        if dest_cover_name.endswith(".png"):
            mime_type = "image/png"
        elif dest_cover_name.endswith(".webp"):
            mime_type = "image/webp"

        images.append(
            ImageObject(
                contentUrl=dest_cover_name,
                encodingFormat=mime_type,
                identifier=[PropertyValue(propertyID="sha256", value=cover_sha)],
            )
        )
        files_to_upload[dest_cover_name] = dest_cover_path

    # Handle tracks
    tracks_meta = proposal.get("tracks", [])
    if not tracks_meta:
        raise AlbumApplyError("Proposal has no tracks")

    recordings: list[MusicRecording] = []
    for track_info in tracks_meta:
        pos = track_info["position"]
        track_name = track_info["name"]
        content_type = track_info.get("content_type", "audio/mpeg")
        orig_filename = track_info.get("original_filename", f"track_{pos}.mp3")

        # Locate staged track file
        # Check by position e.g. track_1.bin, or stored_filename, or pos.bin
        candidate_files = [
            tracks_dir / f"track_{pos}.bin",
            tracks_dir / f"track_{pos}.mp3",
            tracks_dir / f"{pos}.mp3",
            tracks_dir / track_info.get("stored_filename", ""),
            tracks_dir / orig_filename,
        ]
        src_track_path = next((f for f in candidate_files if f and f.exists() and f.is_file()), None)
        if src_track_path is None:
            # Fallback: scan tracks_dir for matching pos
            found = list(tracks_dir.glob(f"*{pos}*"))
            src_track_path = found[0] if found else None

        if src_track_path is None or not src_track_path.exists():
            raise AlbumApplyError(f"Audio file for track {pos} ({track_name!r}) not found in {tracks_dir}")

        ext = resolve_media_extension(content_type, orig_filename)
        stem = sanitize_filename_stem(track_name, fallback=f"track-{pos}")
        dest_filename = f"{pos:02d}-{stem}{ext}"
        dest_track_path = release_dir / dest_filename

        if not dry_run:
            shutil.copy2(src_track_path, dest_track_path)
            duration_iso, bitrate_str = ffprobe_av_info(dest_track_path)
            track_sha = sha256_of(dest_track_path)
        else:
            dest_track_path = src_track_path
            duration_iso = track_info.get("duration", "PT3M00S")
            bitrate_str = track_info.get("bitrate", "320 kbps")
            track_sha = track_info.get("sha256", "dryrun-sha256")

        files_to_upload[dest_filename] = dest_track_path

        recordings.append(
            MusicRecording(
                position=pos,
                name=track_name,
                audio=AudioObject(
                    contentUrl=dest_filename,
                    encodingFormat=content_type,
                    bitrate=bitrate_str,
                    duration=duration_iso,
                    identifier=[PropertyValue(propertyID="sha256", value=track_sha)],
                ),
            )
        )

    # Build MusicAlbum model
    album_data = {
        "@type": "MusicAlbum",
        "name": proposal["name"],
        "slug": release_slug,
        "datePublished": str(proposal["date_published"]),
        "byArtist": band_slug,
        "genre": proposal.get("genre") or [],
        "license": proposal.get("license") or "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        "track": [r.model_dump(by_alias=True, exclude_none=True) for r in recordings],
    }
    if proposal.get("description"):
        album_data["description"] = proposal["description"]
    if proposal.get("description_en"):
        album_data["description_en"] = proposal["description_en"]
    if images:
        album_data["image"] = [img.model_dump(by_alias=True, exclude_none=True) for img in images]

    try:
        album = MusicAlbum.model_validate(album_data)
    except ValidationError as exc:
        raise AlbumApplyError(f"Generated album metadata failed validation: {exc}") from exc

    release_yaml_path = release_dir / "release.yaml"
    if not dry_run:
        dump_yaml(release_yaml_path, album.model_dump(by_alias=True, exclude_none=True))

    # Archive.org upload
    if not skip_upload:
        item_id = release_item_id(band_slug, release_slug)
        item_meta = release_metadata(album, band)
        print(f"Publishing release {item_id} to archive.org ({len(files_to_upload)} files)...")
        ok = publish_item(
            item_id=item_id,
            files=files_to_upload,
            metadata=item_meta,
            dry_run=dry_run,
            yaml_path=release_yaml_path,
        )
        if not ok:
            raise AlbumApplyError(f"archive.org upload failed for {item_id}")

        # Update consolidated metadata bundle
        print("Updating consolidated metadata backup bundle on archive.org...")
        bundle_ok = publish_metadata_bundle(bands_dir, dry_run=dry_run)
        if not bundle_ok:
            raise AlbumApplyError("archive.org metadata bundle sync failed")

    print(f"Successfully applied album {release_slug} under {band_slug}.")
    return release_yaml_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proposal-file", type=Path, required=True, help="Path to proposal JSON file")
    parser.add_argument("--tracks-dir", type=Path, required=True, help="Path to directory containing track audio files")
    parser.add_argument("--cover-file", type=Path, default=None, help="Path to cover art image file (optional)")
    parser.add_argument("--bands-dir", type=Path, default=REPO_ROOT / "bands", help="Path to bands directory")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing files or uploading")
    parser.add_argument("--skip-upload", action="store_true", help="Write local files but skip archive.org upload")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not args.proposal_file.exists():
        print(f"Error: proposal file not found: {args.proposal_file}", file=sys.stderr)
        sys.exit(1)
    if not args.tracks_dir.exists() or not args.tracks_dir.is_dir():
        print(f"Error: tracks directory not found: {args.tracks_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        proposal = json.loads(args.proposal_file.read_text())
        apply_album_proposal(
            proposal=proposal,
            tracks_dir=args.tracks_dir,
            cover_file=args.cover_file,
            bands_dir=args.bands_dir,
            dry_run=args.dry_run,
            skip_upload=args.skip_upload,
        )
    except AlbumApplyError as exc:
        print(f"Error applying album proposal: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
