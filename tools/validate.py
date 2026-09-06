#!/usr/bin/env python3
"""
Validate the archive tree under bands/ against schema/*.schema.json (via the
Pydantic models that generated them), and check/compute per-file sha256
checksums and audio/video properties (duration, bitrate) for every media
file referenced (audio tracks, band/release photos, band/release videos).

Usage:
    python validate.py                        # check only, fail on any problem
    python validate.py --write                # also fill in missing checksums/
                                               # audio-video properties and
                                               # rewrite the YAML files
    python validate.py --bands-dir PATH       # validate PATH instead of the
                                               # repo's own bands/ (for tests)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from models import AudioObject, ImageObject, MusicAlbum, MusicGroup, PropertyValue, VideoObject

REPO_ROOT = Path(__file__).resolve().parent.parent


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe_av_info(path: Path) -> tuple[str, str]:
    """Return (iso8601_duration, bitrate_str) using ffprobe. Works for both
    audio and video files."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-show_entries", "format=duration,bit_rate",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)["format"]
    seconds = float(data["duration"])
    minutes, secs = divmod(round(seconds), 60)
    duration = f"PT{minutes}M{secs}S" if minutes else f"PT{secs}S"
    bitrate_kbps = round(int(data["bit_rate"]) / 1000)
    return duration, f"{bitrate_kbps} kbps"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def dump_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def check_media(
    base_dir: Path,
    media: AudioObject | ImageObject | VideoObject,
    label: str,
    needs_av_info: bool,
    write: bool,
) -> tuple[list[str], bool]:
    """Check (and optionally fill in) one media file's checksum and, for
    audio/video, its duration/bitrate. Returns (errors, changed)."""
    errors: list[str] = []
    changed = False

    file_path = base_dir / media.contentUrl
    if not file_path.exists():
        return [f"{label}: references missing file {file_path}"], changed

    actual_sha256 = sha256_of(file_path)
    existing = next((pv for pv in media.identifier if pv.propertyID == "sha256"), None)
    if existing is None:
        if write:
            media.identifier.append(PropertyValue(propertyID="sha256", value=actual_sha256))
            changed = True
        else:
            errors.append(
                f"{label} ({file_path.name}) has no sha256 checksum recorded; "
                f"run with --write to compute it"
            )
    elif existing.value != actual_sha256:
        errors.append(
            f"{label} ({file_path.name}) checksum mismatch: "
            f"recorded {existing.value}, actual {actual_sha256}"
        )

    if needs_av_info and (media.duration is None or media.bitrate is None):
        if write:
            duration, bitrate = ffprobe_av_info(file_path)
            media.duration = media.duration or duration
            media.bitrate = media.bitrate or bitrate
            changed = True
        else:
            errors.append(
                f"{label} ({file_path.name}) missing duration/bitrate; "
                f"run with --write to compute it"
            )

    return errors, changed


def check_release(release_dir: Path, band_slug: str, write: bool) -> list[str]:
    errors: list[str] = []
    release_yaml = release_dir / "release.yaml"
    raw = load_yaml(release_yaml)
    try:
        album = MusicAlbum.model_validate(raw)
    except ValidationError as e:
        return [f"{release_yaml}: schema validation failed:\n{e}"]

    if album.byArtist != band_slug:
        errors.append(
            f"{release_yaml}: byArtist={album.byArtist!r} does not match "
            f"parent band slug {band_slug!r}"
        )

    changed = False
    for track in album.track:
        e, c = check_media(release_dir, track.audio, f"{release_yaml}: track {track.position} audio", True, write)
        errors.extend(e)
        changed = changed or c
    for i, img in enumerate(album.image):
        e, c = check_media(release_dir, img, f"{release_yaml}: image[{i}]", False, write)
        errors.extend(e)
        changed = changed or c
    for i, vid in enumerate(album.video):
        e, c = check_media(release_dir, vid, f"{release_yaml}: video[{i}]", True, write)
        errors.extend(e)
        changed = changed or c

    if write and changed:
        dump_yaml(release_yaml, album.model_dump(by_alias=True, exclude_none=True))
        print(f"updated {release_yaml}")

    return errors


def check_band(band_dir: Path, write: bool) -> list[str]:
    errors: list[str] = []
    band_yaml = band_dir / "band.yaml"
    if not band_yaml.exists():
        return [f"{band_dir}: missing band.yaml"]

    raw = load_yaml(band_yaml)
    try:
        band = MusicGroup.model_validate(raw)
    except ValidationError as e:
        return [f"{band_yaml}: schema validation failed:\n{e}"]

    if band.slug != band_dir.name:
        errors.append(f"{band_yaml}: slug={band.slug!r} does not match folder name {band_dir.name!r}")

    changed = False
    for i, img in enumerate(band.image):
        e, c = check_media(band_dir, img, f"{band_yaml}: image[{i}]", False, write)
        errors.extend(e)
        changed = changed or c
    for i, vid in enumerate(band.video):
        e, c = check_media(band_dir, vid, f"{band_yaml}: video[{i}]", True, write)
        errors.extend(e)
        changed = changed or c

    if write and changed:
        dump_yaml(band_yaml, band.model_dump(by_alias=True, exclude_none=True))
        print(f"updated {band_yaml}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="compute and fill in missing data")
    parser.add_argument(
        "--bands-dir",
        type=Path,
        default=REPO_ROOT / "bands",
        help="directory to validate (default: this repo's bands/)",
    )
    args = parser.parse_args()

    bands_dir: Path = args.bands_dir
    all_errors: list[str] = []

    for band_dir in sorted(p for p in bands_dir.iterdir() if p.is_dir()):
        all_errors.extend(check_band(band_dir, args.write))
        for release_dir in sorted(p for p in band_dir.iterdir() if p.is_dir()):
            release_yaml = release_dir / "release.yaml"
            if release_yaml.exists():
                all_errors.extend(check_release(release_dir, band_dir.name, args.write))

    if all_errors:
        print(f"\n{len(all_errors)} problem(s) found:\n")
        for e in all_errors:
            print(f"- {e}")
        return 1

    print("OK: archive is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
