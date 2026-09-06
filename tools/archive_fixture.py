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

    band_yaml_path = band_dir / "band.yaml"
    band_yaml_path.write_text(
        yaml.safe_dump(
            {
                "@type": "MusicGroup",
                "name": "Test Band",
                "slug": band_slug,
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )

    release_yaml_path = release_dir / "release.yaml"
    release_yaml_path.write_text(
        yaml.safe_dump(
            {
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
                        "audio": {
                            "@type": "AudioObject",
                            "contentUrl": audio_filename,
                            "encodingFormat": "audio/mpeg",
                            "bitrate": "320 kbps",
                            "duration": "PT1S",
                            "identifier": [
                                {
                                    "@type": "PropertyValue",
                                    "propertyID": "sha256",
                                    "value": audio_sha256,
                                }
                            ],
                        },
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        )
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


def run_validate(bands_dir: Path, *extra_args: str) -> "subprocess.CompletedProcess[str]":
    """Invoke tools/validate.py --bands-dir <bands_dir> [extra_args...] as a
    subprocess, capturing stdout/stderr as text. Used to assert on exit
    code and reported messages exactly as a real user would see them."""
    return subprocess.run(
        [sys.executable, str(VALIDATE_PY), "--bands-dir", str(bands_dir), *extra_args],
        capture_output=True,
        text=True,
    )
