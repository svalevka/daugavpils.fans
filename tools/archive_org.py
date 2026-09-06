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


def archive_org_url(item_id: str, content_url: str) -> str:
    """The public URL for one file within an archive.org item. content_url
    is used as-is as the file's path within the item, so it matches the
    band/release.yaml `contentUrl` exactly."""
    return f"https://archive.org/download/{item_id}/{content_url}"
