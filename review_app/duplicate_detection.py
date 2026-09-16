"""
Flags a video submission as a likely duplicate of something the target
band already has, before it can be auto-approved (see GitHub issue #51).

Duration-only, by design: an incident that started this issue was a
YouTube re-upload of an existing 1998 concert video under a different
title, from a different uploader - the AI agent auto-approved it at 88%
confidence because nothing in the pipeline ever looks at what a band
already has. Both videos happened to be exactly the same length, which
is a cheap, already-available signal (ffprobe/YouTube both report it)
that would have caught this specific case for free. A re-trim/re-encode
landing outside DURATION_TOLERANCE_SECONDS needs real content
fingerprinting (e.g. audio fingerprinting) to catch - deliberately out of
scope here; see the issue for that tradeoff.

Video submissions have a duration available at review time (fetched via
youtube_fetch.py for YouTube submissions, or probed with ffprobe via
probe_video_duration() on direct file uploads - see GitHub issue #52).
find_duplicate_video compares candidate duration against all published
videos under the band.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import archive_read  # noqa: E402
from archive_org import archive_org_url, band_item_id, release_item_id  # noqa: E402

# Not tuned against real data yet - one incident isn't enough to know the
# right number. Cheap to adjust later; deliberately not made configurable
# (see issue #51) since this is a judgment call embedded in the review
# logic, not an operational knob like the upload size caps.
DURATION_TOLERANCE_SECONDS = 3

# Matches only the shape tools/validate.py's ffprobe_av_info ever writes
# (PT{h}H{m}M{s}S, any component optional) - not a general ISO 8601
# parser.
_ISO8601_DURATION_RE = re.compile(r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$")


def parse_iso8601_duration_seconds(duration: str | None) -> int | None:
    """Returns None for anything missing/malformed rather than raising -
    a video with an unparseable duration just means "nothing to compare
    against", not a crash."""
    if not duration:
        return None
    m = _ISO8601_DURATION_RE.match(duration.strip())
    if not m or not any(m.groups()):
        return None
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def probe_video_duration(path: Path) -> int | None:
    """Probes a video file on disk with ffprobe and returns rounded integer seconds.
    Returns None if ffprobe is not installed, fails, or duration is unreadable."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        data = json.loads(out.stdout).get("format", {})
        if "duration" in data and data["duration"]:
            return round(float(data["duration"]))
    except Exception:
        return None
    return None


@dataclass
class DuplicateMatch:
    name: str
    duration_seconds: int
    url: str


def _closest_match(videos, item_id: str, candidate_seconds: int) -> DuplicateMatch | None:
    for video in videos:
        existing_seconds = parse_iso8601_duration_seconds(video.duration)
        if existing_seconds is None:
            continue
        if abs(existing_seconds - candidate_seconds) <= DURATION_TOLERANCE_SECONDS:
            return DuplicateMatch(
                name=video.name or video.contentUrl,
                duration_seconds=existing_seconds,
                url=archive_org_url(item_id, video.contentUrl),
            )
    return None


def find_duplicate_video(
    archive_checkout_path: Path, band_slug: str, candidate_duration_seconds: int | None
) -> DuplicateMatch | None:
    """Checks a new video's duration against every video already
    published anywhere under this band - band-level and every one of its
    releases combined (see issue #51: a live-performance video could
    plausibly be re-submitted against either the band page or a specific
    release page, not necessarily the same scope it already lives under).
    Returns the first match within DURATION_TOLERANCE_SECONDS, or None."""
    if candidate_duration_seconds is None:
        return None

    try:
        band = archive_read.get_band(archive_checkout_path, band_slug)
    except archive_read.ApplyError:
        return None

    match = _closest_match(band.video, band_item_id(band_slug), candidate_duration_seconds)
    if match is not None:
        return match

    try:
        releases = archive_read.list_releases(archive_checkout_path, band_slug)
    except archive_read.ApplyError:
        releases = []

    for release in releases:
        match = _closest_match(
            release.video, release_item_id(band_slug, release.slug), candidate_duration_seconds
        )
        if match is not None:
            return match

    return None
