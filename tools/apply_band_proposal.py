#!/usr/bin/env python3
"""
Apply one approved band proposal (see review_app/) to the archive:
create the band directory under bands/<band-slug>/, copy optional staged
band photo into media/<band-slug>-photo.<ext>, create band.yaml,
optionally create the first release directory under
bands/<band-slug>/<release-slug>/ with audio tracks, cover image, and
release.yaml, upload media to archive.org, sync metadata, and update the
consolidated metadata backup bundle.

Runs inside the GitHub Action (.github/workflows/apply-band-proposal.yml)
triggered when an approver clicks "Upload & Publish" in review_app, or on schedule.

Usage:
    python tools/apply_band_proposal.py \
        --proposal-file proposal.json \
        [--photo-file /path/to/band-photo.jpg] \
        [--cover-file /path/to/cover.jpg] \
        [--tracks-dir /path/to/tracks/] \
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
from models import AudioObject, ImageObject, MusicAlbum, MusicGroup, MusicRecording, PropertyValue
from publish_to_archive_org import (
    band_metadata,
    publish_item,
    publish_metadata_bundle,
    release_metadata,
)
from exif_cleanup import strip_sensitive_exif
from validate import dump_yaml, ffprobe_av_info, sha256_of

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


class BandApplyError(Exception):
    """A band proposal could not be applied. Message is user-safe."""


def sanitize_filename_stem(title: str, fallback: str = "track") -> str:
    transliterated = unidecode.unidecode(title).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return cleaned if len(cleaned) >= 1 else fallback


def resolve_media_extension(content_type: str, original_filename: str, default: str = ".mp3") -> str:
    if content_type in MIME_EXTENSIONS:
        return MIME_EXTENSIONS[content_type]
    ext = Path(original_filename).suffix.lower()
    return ext if ext else default


def apply_band_proposal(
    proposal: dict[str, Any],
    photo_file: Path | None,
    cover_file: Path | None,
    tracks_dir: Path | None,
    bands_dir: Path,
    dry_run: bool = False,
    skip_upload: bool = False,
) -> Path:
    band_slug = proposal.get("band_slug", "").strip()
    if not band_slug or not SLUG_RE.match(band_slug):
        raise BandApplyError(f"Band slug {band_slug!r} is invalid")

    band_dir = (bands_dir / band_slug).resolve()
    if band_dir.exists() and (band_dir / "band.yaml").exists():
        raise BandApplyError(f"Band directory {band_dir} already exists")

    if not dry_run:
        band_dir.mkdir(parents=True, exist_ok=True)

    band_images: list[ImageObject] = []
    band_files_to_upload: dict[str, Path] = {}

    # Handle band photo if present
    if photo_file and photo_file.exists():
        photo_ext = photo_file.suffix.lower() or ".jpg"
        if photo_ext in (".jpeg", ".jpg"):
            photo_ext = ".jpg"
            photo_mime = "image/jpeg"
        elif photo_ext == ".png":
            photo_mime = "image/png"
        elif photo_ext == ".webp":
            photo_mime = "image/webp"
        elif photo_ext == ".gif":
            photo_mime = "image/gif"
        else:
            photo_ext = ".jpg"
            photo_mime = "image/jpeg"

        media_dir = band_dir / "media"
        if not dry_run:
            media_dir.mkdir(parents=True, exist_ok=True)

        dest_photo_name = f"{band_slug}-photo{photo_ext}"
        dest_photo_rel = f"media/{dest_photo_name}"
        dest_photo_path = media_dir / dest_photo_name

        if not dry_run:
            shutil.copy2(photo_file, dest_photo_path)
            strip_sensitive_exif(dest_photo_path)
            photo_sha = sha256_of(dest_photo_path)
        else:
            dest_photo_path = photo_file
            photo_sha = "dryrun-sha256"

        band_images.append(
            ImageObject(
                contentUrl=dest_photo_rel,
                encodingFormat=photo_mime,
                identifier=[PropertyValue(propertyID="sha256", value=photo_sha)],
            )
        )
        band_files_to_upload[dest_photo_rel] = dest_photo_path

    # Build MusicGroup
    band_data: dict[str, Any] = {
        "@type": "MusicGroup",
        "name": proposal["name"],
        "slug": band_slug,
        "genre": proposal.get("genre") or [],
        "location": proposal.get("location") or "Daugavpils, Latvia",
    }
    if proposal.get("founding_date"):
        band_data["foundingDate"] = str(proposal["founding_date"])
    if proposal.get("dissolution_date"):
        band_data["dissolutionDate"] = str(proposal["dissolution_date"])
    if proposal.get("description"):
        band_data["description"] = proposal["description"]
    if proposal.get("description_en"):
        band_data["description_en"] = proposal["description_en"]
    if band_images:
        band_data["image"] = [img.model_dump(by_alias=True, exclude_none=True) for img in band_images]

    try:
        band = MusicGroup.model_validate(band_data)
    except ValidationError as exc:
        raise BandApplyError(f"Generated band metadata failed validation: {exc}") from exc

    band_yaml_path = band_dir / "band.yaml"
    if not dry_run:
        dump_yaml(band_yaml_path, band.model_dump(by_alias=True, exclude_none=True))

    # Handle optional first release
    album: MusicAlbum | None = None
    release_yaml_path: Path | None = None
    release_files_to_upload: dict[str, Path] = {}

    has_release = bool(proposal.get("has_release"))
    if has_release:
        release_slug = proposal.get("release_slug", "").strip()
        if not release_slug or not SLUG_RE.match(release_slug):
            raise BandApplyError(f"Release slug {release_slug!r} is invalid")

        release_dir = band_dir / release_slug
        if release_dir.exists() and (release_dir / "release.yaml").exists():
            raise BandApplyError(f"Release directory {release_dir} already exists")

        if not dry_run:
            release_dir.mkdir(parents=True, exist_ok=True)

        release_images: list[ImageObject] = []
        if cover_file and cover_file.exists():
            cover_ext = cover_file.suffix.lower() or ".jpg"
            if cover_ext in (".jpeg", ".jpg"):
                dest_cover_name = "cover.jpg"
                mime_type = "image/jpeg"
            elif cover_ext in (".png", ".webp"):
                dest_cover_name = f"cover{cover_ext}"
                mime_type = f"image/{cover_ext.lstrip('.')}"
            else:
                dest_cover_name = "cover.jpg"
                mime_type = "image/jpeg"

            dest_cover_path = release_dir / dest_cover_name
            if not dry_run:
                shutil.copy2(cover_file, dest_cover_path)
                strip_sensitive_exif(dest_cover_path)
                cover_sha = sha256_of(dest_cover_path)
            else:
                dest_cover_path = cover_file
                cover_sha = "dryrun-sha256"

            release_images.append(
                ImageObject(
                    contentUrl=dest_cover_name,
                    encodingFormat=mime_type,
                    identifier=[PropertyValue(propertyID="sha256", value=cover_sha)],
                )
            )
            release_files_to_upload[dest_cover_name] = dest_cover_path

        tracks_meta = proposal.get("tracks", [])
        if not tracks_meta:
            raise BandApplyError("Proposal specifies has_release=true but has no tracks")

        if tracks_dir is None or not tracks_dir.exists():
            raise BandApplyError("Tracks directory required when release is included")

        recordings: list[MusicRecording] = []
        for track_info in tracks_meta:
            pos = track_info["position"]
            track_name = track_info["name"]
            content_type = track_info.get("content_type", "audio/mpeg")
            orig_filename = track_info.get("original_filename", f"track_{pos}.mp3")

            candidate_files = [
                tracks_dir / f"track_{pos}.bin",
                tracks_dir / f"track_{pos}.mp3",
                tracks_dir / f"{pos}.mp3",
                tracks_dir / track_info.get("stored_filename", ""),
                tracks_dir / orig_filename,
            ]
            src_track_path = next((f for f in candidate_files if f and f.exists() and f.is_file()), None)
            if src_track_path is None:
                found = list(tracks_dir.glob(f"*{pos}*"))
                src_track_path = found[0] if found else None

            if src_track_path is None or not src_track_path.exists():
                raise BandApplyError(
                    f"Audio file for track {pos} ({track_name!r}) not found in {tracks_dir}"
                )

            ext = resolve_media_extension(content_type, orig_filename, default=".mp3")
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

            release_files_to_upload[dest_filename] = dest_track_path

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

        album_data: dict[str, Any] = {
            "@type": "MusicAlbum",
            "name": proposal["release_name"],
            "slug": release_slug,
            "datePublished": str(proposal["release_date_published"]),
            "byArtist": band_slug,
            "genre": proposal.get("release_genre") or [],
            "license": proposal.get("release_license")
            or "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            "track": [r.model_dump(by_alias=True, exclude_none=True) for r in recordings],
        }
        if proposal.get("release_description"):
            album_data["description"] = proposal["release_description"]
        if proposal.get("release_description_en"):
            album_data["description_en"] = proposal["release_description_en"]
        if release_images:
            album_data["image"] = [img.model_dump(by_alias=True, exclude_none=True) for img in release_images]

        try:
            album = MusicAlbum.model_validate(album_data)
        except ValidationError as exc:
            raise BandApplyError(f"Generated album metadata failed validation: {exc}") from exc

        release_yaml_path = release_dir / "release.yaml"
        if not dry_run:
            dump_yaml(release_yaml_path, album.model_dump(by_alias=True, exclude_none=True))

    # Archive.org uploads
    if not skip_upload:
        if band_files_to_upload:
            b_item_id = band_item_id(band_slug)
            b_meta = band_metadata(band)
            print(f"Publishing band item {b_item_id} to archive.org ({len(band_files_to_upload)} files)...")
            ok = publish_item(
                item_id=b_item_id,
                files=band_files_to_upload,
                metadata=b_meta,
                dry_run=dry_run,
                yaml_path=band_yaml_path,
            )
            if not ok:
                raise BandApplyError(f"archive.org upload failed for {b_item_id}")

        if has_release and album and release_yaml_path:
            r_item_id = release_item_id(band_slug, album.slug)
            r_meta = release_metadata(album, band)
            print(
                f"Publishing release item {r_item_id} to archive.org ({len(release_files_to_upload)} files)..."
            )
            ok = publish_item(
                item_id=r_item_id,
                files=release_files_to_upload,
                metadata=r_meta,
                dry_run=dry_run,
                yaml_path=release_yaml_path,
            )
            if not ok:
                raise BandApplyError(f"archive.org upload failed for {r_item_id}")

        # Update consolidated metadata backup bundle
        print("Updating consolidated metadata backup bundle on archive.org...")
        try:
            publish_metadata_bundle(bands_dir, dry_run=dry_run)
        except Exception as exc:
            raise BandApplyError(f"archive.org metadata bundle sync failed: {exc}") from exc

    print(f"Successfully applied band {band_slug}.")
    return band_yaml_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--proposal-file", type=Path, required=True, help="Path to proposal JSON file")
    parser.add_argument("--photo-file", type=Path, default=None, help="Path to band photo file (optional)")
    parser.add_argument("--cover-file", type=Path, default=None, help="Path to cover art image file (optional)")
    parser.add_argument(
        "--tracks-dir", type=Path, default=None, help="Path to directory containing track audio files (optional)"
    )
    parser.add_argument("--bands-dir", type=Path, default=REPO_ROOT / "bands", help="Path to bands directory")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing files or uploading")
    parser.add_argument("--skip-upload", action="store_true", help="Write local files but skip archive.org upload")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not args.proposal_file.exists():
        print(f"Error: proposal file not found: {args.proposal_file}", file=sys.stderr)
        sys.exit(1)

    try:
        proposal = json.loads(args.proposal_file.read_text())
        apply_band_proposal(
            proposal=proposal,
            photo_file=args.photo_file,
            cover_file=args.cover_file,
            tracks_dir=args.tracks_dir,
            bands_dir=args.bands_dir,
            dry_run=args.dry_run,
            skip_upload=args.skip_upload,
        )
    except BandApplyError as exc:
        print(f"Error applying band proposal: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
