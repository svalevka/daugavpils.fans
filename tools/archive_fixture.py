"""
Reusable test fixture harness: builds a minimal, valid archive tree (one
band, one release, one track) under a temporary directory, for tests to
run tools/validate.py against.

Other test modules (idempotency, failure-mode coverage) build on
`build_valid_archive`'s return value to construct their own broken
variations (delete the audio file, corrupt a checksum, mismatch a slug,
etc.) without duplicating the fixture-building boilerplate.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

VALIDATE_PY = Path(__file__).resolve().parent / "validate.py"

# Not a real playable MP3 -- just needs to be stable bytes we can checksum.
# Happy-path/idempotency tests don't invoke ffprobe (that only runs under
# --write when duration/bitrate are missing, and this fixture always
# supplies them), so the bytes don't need to actually decode as audio.
DEFAULT_AUDIO_BYTES = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 32


@dataclass
class ArchiveFixture:
    bands_dir: Path
    band_slug: str
    release_slug: str
    band_dir: Path
    release_dir: Path
    band_yaml: Path
    release_yaml: Path
    audio_path: Path
    audio_sha256: str


def _write_band_yaml(band_dir: Path, band_slug: str, *, extra: dict | None = None) -> Path:
    """Write a minimal band.yaml, merging in any extra top-level fields
    (e.g. description, member, image) a caller needs beyond the bare
    name/slug every fixture requires."""
    path = band_dir / "band.yaml"
    data = {
        "@type": "MusicGroup",
        "name": "Test Band",
        "slug": band_slug,
        **(extra or {}),
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return path


def _write_release_yaml(
    release_dir: Path,
    *,
    release_slug: str,
    band_slug: str,
    audio_filename: str,
    audio_fields: dict,
    track_extra: dict | None = None,
    extra: dict | None = None,
) -> Path:
    """Write a minimal release.yaml with one track, merging in any extra
    fields on the track (e.g. alternateName) and/or the release itself
    (e.g. description, image) a caller needs beyond the bare required
    fields every fixture requires."""
    path = release_dir / "release.yaml"
    data = {
        "@type": "MusicAlbum",
        "name": "Test Release",
        "slug": release_slug,
        "datePublished": "1999",
        "byArtist": band_slug,
        "license": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        "track": [
            {
                "@type": "MusicRecording",
                "position": 1,
                "name": "Test Track",
                **(track_extra or {}),
                "audio": {
                    "@type": "AudioObject",
                    "contentUrl": audio_filename,
                    "encodingFormat": "audio/mpeg",
                    **audio_fields,
                },
            }
        ],
        **(extra or {}),
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return path


def build_valid_archive(
    bands_dir: Path,
    *,
    band_slug: str = "test-band",
    release_slug: str = "1999-test-release",
    audio_filename: str = "01-test-track.mp3",
    audio_bytes: bytes = DEFAULT_AUDIO_BYTES,
) -> ArchiveFixture:
    """Write a fully valid band.yaml/release.yaml + audio file under
    bands_dir/band_slug/release_slug/, with a correct sha256 checksum and
    duration/bitrate already populated. Returns the paths and metadata so
    callers can mutate individual pieces to build defect fixtures."""
    band_dir = bands_dir / band_slug
    release_dir = band_dir / release_slug
    release_dir.mkdir(parents=True)

    audio_path = release_dir / audio_filename
    audio_path.write_bytes(audio_bytes)
    audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()

    band_yaml_path = _write_band_yaml(band_dir, band_slug)
    release_yaml_path = _write_release_yaml(
        release_dir,
        release_slug=release_slug,
        band_slug=band_slug,
        audio_filename=audio_filename,
        audio_fields={
            "bitrate": "320 kbps",
            "duration": "PT1S",
            "identifier": [
                {"@type": "PropertyValue", "propertyID": "sha256", "value": audio_sha256}
            ],
        },
    )

    return ArchiveFixture(
        bands_dir=bands_dir,
        band_slug=band_slug,
        release_slug=release_slug,
        band_dir=band_dir,
        release_dir=release_dir,
        band_yaml=band_yaml_path,
        release_yaml=release_yaml_path,
        audio_path=audio_path,
        audio_sha256=audio_sha256,
    )


def generate_silent_mp3(path: Path, *, seconds: float = 1.0, bitrate_kbps: int = 128) -> None:
    """Generate a real, decodable silent MP3 via ffmpeg, so ffprobe can
    read back its actual duration/bitrate. Used by tests that exercise
    `validate.py --write`'s av-info computation."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=mono",
            "-t", str(seconds),
            "-b:a", f"{bitrate_kbps}k",
            "-codec:a", "libmp3lame",
            "-loglevel", "error",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def build_archive_missing_av_info(
    bands_dir: Path,
    *,
    band_slug: str = "test-band",
    release_slug: str = "1999-test-release",
    audio_filename: str = "01-test-track.mp3",
    seconds: float = 1.0,
    bitrate_kbps: int = 128,
) -> ArchiveFixture:
    """Like build_valid_archive, but with a real ffmpeg-generated audio
    file and no checksum/duration/bitrate recorded in release.yaml --
    for exercising `validate.py --write`'s computation."""
    band_dir = bands_dir / band_slug
    release_dir = band_dir / release_slug
    release_dir.mkdir(parents=True)

    audio_path = release_dir / audio_filename
    generate_silent_mp3(audio_path, seconds=seconds, bitrate_kbps=bitrate_kbps)
    audio_sha256 = hashlib.sha256(audio_path.read_bytes()).hexdigest()

    band_yaml_path = _write_band_yaml(band_dir, band_slug)
    release_yaml_path = _write_release_yaml(
        release_dir,
        release_slug=release_slug,
        band_slug=band_slug,
        audio_filename=audio_filename,
        audio_fields={},
    )

    return ArchiveFixture(
        bands_dir=bands_dir,
        band_slug=band_slug,
        release_slug=release_slug,
        band_dir=band_dir,
        release_dir=release_dir,
        band_yaml=band_yaml_path,
        release_yaml=release_yaml_path,
        audio_path=audio_path,
        audio_sha256=audio_sha256,
    )


DEFAULT_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@dataclass
class NestedFieldsFixture:
    """Like ArchiveFixture, but the band and release also carry the
    scalar/list/nested free-text fields tools/apply_proposal.py edits:
    band description/description_en/location/alternateName/genre, one
    band member, one band image; release description/description_en/
    genre, the track's alternateName, one release image."""

    bands_dir: Path
    band_slug: str
    release_slug: str
    band_dir: Path
    release_dir: Path
    band_yaml: Path
    release_yaml: Path


def build_archive_with_nested_fields(
    bands_dir: Path,
    *,
    band_slug: str = "test-band",
    release_slug: str = "1999-test-release",
    audio_filename: str = "01-test-track.mp3",
    audio_bytes: bytes = DEFAULT_AUDIO_BYTES,
) -> NestedFieldsFixture:
    """Build a valid archive tree like build_valid_archive, then add the
    editable scalar/list/nested fields (band member, band/release images,
    track alternateName, description/genre/location/alternateName on both
    band and release) that tools/test_apply_proposal.py needs to exercise
    every target in tools/editable_fields.py."""
    band_dir = bands_dir / band_slug
    release_dir = band_dir / release_slug
    release_dir.mkdir(parents=True)

    audio_path = release_dir / audio_filename
    audio_path.write_bytes(audio_bytes)
    audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()

    band_image_path = band_dir / "band-photo.png"
    band_image_path.write_bytes(DEFAULT_IMAGE_BYTES)
    band_image_sha256 = hashlib.sha256(DEFAULT_IMAGE_BYTES).hexdigest()

    release_image_bytes = DEFAULT_IMAGE_BYTES + b"\x01"
    release_image_path = release_dir / "cover.png"
    release_image_path.write_bytes(release_image_bytes)
    release_image_sha256 = hashlib.sha256(release_image_bytes).hexdigest()

    band_yaml = _write_band_yaml(
        band_dir,
        band_slug,
        extra={
            "alternateName": ["Alt Name"],
            "location": "Daugavpils",
            "genre": ["post-punk"],
            "description": "Original band description.",
            "description_en": "Original band description (EN).",
            "member": [
                {
                    "name": "Test Member",
                    "name_en": "Test Member EN",
                    "role": "vocals",
                    "role_en": "vocals (EN)",
                    "period": "1994-1996",
                }
            ],
            "image": [
                {
                    "@type": "ImageObject",
                    "contentUrl": band_image_path.name,
                    "encodingFormat": "image/png",
                    "caption": "Original caption",
                    "caption_en": "Original caption (EN)",
                    "contentLocation": "Daugavpils",
                    "depicts": ["Person A"],
                    "identifier": [
                        {"@type": "PropertyValue", "propertyID": "sha256", "value": band_image_sha256}
                    ],
                }
            ],
        },
    )

    release_yaml = _write_release_yaml(
        release_dir,
        release_slug=release_slug,
        band_slug=band_slug,
        audio_filename=audio_filename,
        audio_fields={
            "bitrate": "320 kbps",
            "duration": "PT1S",
            "identifier": [{"@type": "PropertyValue", "propertyID": "sha256", "value": audio_sha256}],
        },
        track_extra={"alternateName": "Original alt title"},
        extra={
            "genre": ["post-punk"],
            "description": "Original release description.",
            "description_en": "Original release description (EN).",
            "image": [
                {
                    "@type": "ImageObject",
                    "contentUrl": release_image_path.name,
                    "encodingFormat": "image/png",
                    "caption": "Original release caption",
                    "identifier": [
                        {"@type": "PropertyValue", "propertyID": "sha256", "value": release_image_sha256}
                    ],
                }
            ],
        },
    )

    return NestedFieldsFixture(
        bands_dir=bands_dir,
        band_slug=band_slug,
        release_slug=release_slug,
        band_dir=band_dir,
        release_dir=release_dir,
        band_yaml=band_yaml,
        release_yaml=release_yaml,
    )


def run_validate(bands_dir: Path, *extra_args: str) -> "subprocess.CompletedProcess[str]":
    """Invoke tools/validate.py --bands-dir <bands_dir> [extra_args...] as a
    subprocess, capturing stdout/stderr as text. Used to assert on exit
    code and reported messages exactly as a real user would see them."""
    return subprocess.run(
        [sys.executable, str(VALIDATE_PY), "--bands-dir", str(bands_dir), *extra_args],
        capture_output=True,
        text=True,
    )


APPLY_PROPOSAL_PY = Path(__file__).resolve().parent / "apply_proposal.py"


def run_apply_proposal(bands_dir: Path, proposal: dict, tmp_path: Path) -> "subprocess.CompletedProcess[str]":
    """Write `proposal` to a scratch JSON file under tmp_path and invoke
    tools/apply_proposal.py --proposal-file <that file> --bands-dir
    <bands_dir> as a subprocess, capturing stdout/stderr as text - the
    same "invoke the real CLI, assert on exit code and output" idiom
    run_validate() uses for validate.py."""
    proposal_file = tmp_path / "proposal.json"
    proposal_file.write_text(json.dumps(proposal))
    return subprocess.run(
        [
            sys.executable,
            str(APPLY_PROPOSAL_PY),
            "--proposal-file", str(proposal_file),
            "--bands-dir", str(bands_dir),
        ],
        capture_output=True,
        text=True,
    )
