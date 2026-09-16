"""
Audio inspection, ffprobe validation, AI generator watermark detection,
and deterministic slug generation for new album proposals (GitHub issue #20).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import unidecode

AI_TAG_PATTERNS = [
    re.compile(r"\b(suno(?:\.ai)?|chirp)\b", re.IGNORECASE),
    re.compile(r"\budio\b", re.IGNORECASE),
    re.compile(r"\b(boomy|soundraw|musiclm|musicfx|mubert)\b", re.IGNORECASE),
    re.compile(r"\b(ai-generated|ai generated|artificial intelligence|text-to-music)\b", re.IGNORECASE),
    re.compile(r"\bprompt:\s*", re.IGNORECASE),
]

AI_TEXT_SYNTAX_PATTERNS = [
    re.compile(r"\[(?:verse|chorus|bridge|outro|intro|hook|drop|pre-chorus)(?:\s*\d+)?\]", re.IGNORECASE),
    re.compile(r"\b(generated with suno|generated with udio|created with suno|created with udio)\b", re.IGNORECASE),
]

SLUG_RE = re.compile(r"^[0-9]{4}-[a-z0-9]+(?:-[a-z0-9]+)*$")
BAND_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AudioValidationError(Exception):
    """Raised when an audio file cannot be validated or has invalid properties."""


def generate_band_slug(band_name: str) -> str:
    """Generate a deterministic, URL-safe band slug: <transliterated-name>.
    e.g. 'CrossFire' -> 'crossfire', 'Крики Мартина' -> 'kriki-martina'
    """
    transliterated = unidecode.unidecode(band_name).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    if not slug or not BAND_SLUG_RE.match(slug):
        raise AudioValidationError(f"Could not derive a valid slug from band name {band_name!r}")
    return slug


def sha256_of(path: Path) -> str:
    """Compute sha256 checksum of a file on disk."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_ai_audio_signatures(tags: dict[str, Any]) -> list[str]:
    """Scan ID3/Vorbis tags for watermarks and signatures left by AI music generators."""
    flags: list[str] = []
    for key, value in tags.items():
        val_str = str(value)
        for pat in AI_TAG_PATTERNS:
            if pat.search(val_str):
                flags.append(f"AI generator watermark detected in tag '{key}': {val_str[:80]}")
                break
    return flags


def detect_ai_text_syntax(text: str) -> list[str]:
    """Check text (lyrics/description/liner notes) for prompt syntax tropes from generative AI music engines."""
    flags: list[str] = []
    if not text:
        return flags
    for pat in AI_TEXT_SYNTAX_PATTERNS:
        match = pat.search(text)
        if match:
            flags.append(f"AI generative music syntax marker detected: '{match.group(0)}'")
    return flags


def check_ffprobe_available() -> bool:
    """Returns True if ffprobe executable is found on PATH."""
    return shutil.which("ffprobe") is not None


def probe_audio_file(path: Path) -> dict[str, Any]:
    """Run ffprobe to verify audio integrity, duration, bitrate, and tags.
    Raises AudioValidationError if the file is invalid, zero-length, or ffprobe fails.
    """
    if not check_ffprobe_available():
        raise AudioValidationError("ffprobe binary is not available on PATH")

    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-show_entries", "format=duration,bit_rate:format_tags",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise AudioValidationError(f"ffprobe failed to inspect audio file: {exc}") from exc

    try:
        format_info = json.loads(out.stdout).get("format", {})
    except (json.JSONDecodeError, KeyError) as exc:
        raise AudioValidationError("ffprobe returned invalid JSON output") from exc

    if "duration" not in format_info or not format_info["duration"]:
        raise AudioValidationError("Audio file has no detectable duration (corrupt or unreadable)")

    try:
        seconds = float(format_info["duration"])
    except (ValueError, TypeError) as exc:
        raise AudioValidationError(f"Invalid duration value: {exc}") from exc

    if seconds <= 0:
        raise AudioValidationError("Audio track has zero duration")

    minutes, secs = divmod(round(seconds), 60)
    duration_iso = f"PT{minutes}M{secs}S" if minutes else f"PT{secs}S"

    bitrate_str = "unknown"
    if "bit_rate" in format_info and format_info["bit_rate"]:
        try:
            bitrate_kbps = round(int(format_info["bit_rate"]) / 1000)
            bitrate_str = f"{bitrate_kbps} kbps"
        except (ValueError, TypeError):
            pass

    tags = format_info.get("tags", {})
    ai_flags = detect_ai_audio_signatures(tags)

    return {
        "duration_iso": duration_iso,
        "duration_seconds": seconds,
        "bitrate": bitrate_str,
        "tags": tags,
        "ai_flags": ai_flags,
    }


def generate_release_slug(album_name: str, date_published: str) -> str:
    """Generate a deterministic, URL-safe release slug: <year>-<transliterated-title>.
    e.g. 'Карманный Мир', '1993' -> '1993-karmannyi-mir'
    """
    year_match = re.search(r"\b(19\d\d|20\d\d)\b", date_published)
    year = year_match.group(1) if year_match else date_published.strip()[:4]
    if not re.match(r"^[0-9]{4}$", year):
        raise AudioValidationError(f"Invalid year: {date_published!r} must be a 4-digit year")

    transliterated = unidecode.unidecode(album_name).lower()
    stem = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    if not stem:
        stem = "album"

    # If stem already starts with year (e.g. '1993-karmannyi-mir'), avoid double prefix
    if stem.startswith(f"{year}-"):
        slug = stem
    else:
        slug = f"{year}-{stem}"

    if not SLUG_RE.match(slug):
        # Clean down to valid slug format
        slug = f"{year}-{re.sub(r'[^a-z0-9]+', '-', stem).strip('-')}"
        if not SLUG_RE.match(slug):
            raise AudioValidationError(f"Could not derive a valid slug from album name {album_name!r}")

    return slug


def sanitize_track_title(original_filename: str, tag_title: str | None = None) -> str:
    """Derive a clean track title from ID3 tag or original filename stem."""
    if tag_title and tag_title.strip():
        return tag_title.strip()
    stem = Path(original_filename).stem
    # Strip leading track numbers e.g. "01 - ", "01. ", "01_"
    cleaned = re.sub(r"^\d+[\s._-]+", "", stem).strip()
    return cleaned if cleaned else stem
