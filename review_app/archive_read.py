"""
Read-only access to the archive checkout, for the submission form: which
bands/releases exist, and the live current value of one editable field.

Deliberately reuses tools/apply_proposal.py's own band/release loading and
container-navigation functions (load_band, load_release, container_for)
rather than re-implementing that traversal here - the whole point of
showing a submitter "the current text" is that it must never drift from
what apply_proposal.py will later compare a proposal's original_value
against, and the only way to guarantee that is to share the actual code,
not just the intent.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from apply_proposal import ApplyError, container_for, load_band, load_release  # noqa: E402
from models import MusicAlbum, MusicGroup  # noqa: E402

__all__ = [
    "ApplyError",
    "list_bands",
    "list_releases",
    "get_band",
    "get_release",
    "current_value",
]


def get_band(archive_checkout_path: Path, band_slug: str) -> MusicGroup:
    _, _, band = load_band(archive_checkout_path / "bands", band_slug)
    return band


def get_release(archive_checkout_path: Path, band_slug: str, release_slug: str) -> MusicAlbum:
    band_dir, _, _ = load_band(archive_checkout_path / "bands", band_slug)
    _, _, release = load_release(band_dir, release_slug)
    return release


def list_bands(archive_checkout_path: Path) -> list[MusicGroup]:
    bands_dir = archive_checkout_path / "bands"
    bands = []
    for band_dir in sorted(p for p in bands_dir.iterdir() if p.is_dir()):
        if (band_dir / "band.yaml").exists():
            _, _, band = load_band(bands_dir, band_dir.name)
            bands.append(band)
    return bands


def list_releases(archive_checkout_path: Path, band_slug: str) -> list[MusicAlbum]:
    bands_dir = archive_checkout_path / "bands"
    band_dir, _, _ = load_band(bands_dir, band_slug)
    releases = []
    for release_dir in sorted(p for p in band_dir.iterdir() if p.is_dir()):
        if (release_dir / "release.yaml").exists():
            _, _, release = load_release(band_dir, release_dir.name)
            releases.append(release)
    return releases


def current_value(
    archive_checkout_path: Path,
    band_slug: str,
    release_slug: str | None,
    target: str,
    field: str,
    list_index: int | None,
) -> str | list[str]:
    """The live current value of one editable field. Raises ApplyError
    (from apply_proposal) if the band/release/index/target combination
    doesn't resolve - the same exception, and the same resolution logic,
    apply_proposal.py itself uses."""
    bands_dir = archive_checkout_path / "bands"
    band_dir, _, band = load_band(bands_dir, band_slug)
    release = None
    if release_slug:
        _, _, release = load_release(band_dir, release_slug)
    container = container_for(target, list_index, band, release)
    return getattr(container, field)
