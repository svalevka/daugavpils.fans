"""
Identifier and URL conventions for the archive's archive.org distribution.

Every band and every release gets its own archive.org "item" (see
tools/publish_to_archive_org.py), holding that band/release's full-quality
media. The identifier is derived deterministically from the same `slug`
fields already validated by tools/validate.py, so no new metadata field is
needed on band.yaml/release.yaml - these functions are the single source of
truth for the naming convention, shared by the publish tool and
webapp/build.py.
"""
from __future__ import annotations

ITEM_PREFIX = "daugavpils-fans"


def band_item_id(band_slug: str) -> str:
    return f"{ITEM_PREFIX}-{band_slug}"


def release_item_id(band_slug: str, release_slug: str) -> str:
    return f"{ITEM_PREFIX}-{band_slug}-{release_slug}"


def metadata_item_id() -> str:
    """The one archive.org item holding a full backup of every
    band.yaml/release.yaml (see tools/publish_to_archive_org.py). Recovering
    the Archive's metadata from archive.org alone should mean going to one
    documented, guessable place - not deriving a separate item id per
    band/release from the slug-naming convention above."""
    return f"{ITEM_PREFIX}-metadata"


def archive_org_url(item_id: str, content_url: str) -> str:
    """The public URL for one file within an archive.org item. content_url
    is used as-is as the file's path within the item, so it matches the
    band/release.yaml `contentUrl` exactly."""
    return f"https://archive.org/download/{item_id}/{content_url}"


def item_page_url(item_id: str) -> str:
    """The public archive.org details page for a whole item (band or release)."""
    return f"https://archive.org/details/{item_id}"


def item_torrent_url(item_id: str) -> str:
    """archive.org auto-generates one torrent per item, at this predictable
    URL - same naming convention as archive_org_url, just for the whole item
    rather than one file."""
    return f"https://archive.org/download/{item_id}/{item_id}_archive.torrent"
