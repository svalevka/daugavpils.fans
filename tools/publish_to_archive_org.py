#!/usr/bin/env python3
"""
Publish the archive's media (audio/image/video) to archive.org, one item per
band and one item per release (see archive_org.py for the identifier
convention). This is the archive's actual distribution mechanism: each
item archive.org creates also gets its own auto-generated torrent, so
publishing here is what makes a band/release durably available independent
of this repo, this maintainer's machine, or any single host - the same goal
CONTEXT.md's "Archive Release" concept describes, implemented via
archive.org instead of a hand-rolled torrent bundle.

A full backup of every band.yaml/release.yaml is also uploaded, as one
final step, to a single dedicated item (metadata_item_id() in
archive_org.py) - one documented, guessable place to recover the whole
Archive's metadata from archive.org alone, independent of GitHub. Earlier
this repeated the metadata inside every band/release's own item instead;
that meant guessing (or already knowing) every band/release's item id just
to reassemble the metadata, which isn't actually recoverable-from-scratch.

Requires an authenticated `ia` (internetarchive) config on this machine -
run `ia configure` once, using the project's archive.org account, before
a real (non---dry-run) publish.

Usage:
    python publish_to_archive_org.py                  # publish everything
    python publish_to_archive_org.py --dry-run         # show what would happen
    python publish_to_archive_org.py --bands-dir PATH  # publish a different tree
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from archive_org import archive_org_url, band_item_id, item_page_url, item_torrent_url, metadata_item_id, release_item_id
from models import MusicAlbum, MusicGroup

REPO_ROOT = Path(__file__).resolve().parent.parent


def require_valid_archive(bands_dir: Path) -> None:
    print("Validating archive (validate.py)...")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "validate.py"), "--bands-dir", str(bands_dir)]
    )
    if result.returncode != 0:
        print("\nAborted: refusing to publish an unvalidated archive. Fix the errors above first.")
        sys.exit(1)


def load_band(band_dir: Path) -> MusicGroup:
    raw = yaml.safe_load((band_dir / "band.yaml").read_text())
    return MusicGroup.model_validate(raw)


def load_release(release_dir: Path) -> MusicAlbum:
    raw = yaml.safe_load((release_dir / "release.yaml").read_text())
    return MusicAlbum.model_validate(raw)


def media_files(base_dir: Path, media_items) -> dict[str, Path]:
    """content_url -> absolute local path, for a list of Audio/Image/VideoObjects."""
    return {item.contentUrl: base_dir / item.contentUrl for item in media_items}


def record_same_as(yaml_path: Path, urls: list[str]) -> None:
    """Add any of `urls` not already listed to yaml_path's `sameAs`, and
    write the file back if that changed anything - the same "compute once,
    record the fact" pattern validate.py --write uses for checksums."""
    data = yaml.safe_load(yaml_path.read_text())
    existing = data.get("sameAs", [])
    changed = False
    for url in urls:
        if url not in existing:
            existing.append(url)
            changed = True
    if not changed:
        return
    data["sameAs"] = existing
    yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    print(f"    recorded sameAs in {yaml_path.relative_to(REPO_ROOT)}")


def publish_item(
    item_id: str,
    files: dict[str, Path],
    metadata: dict[str, str],
    dry_run: bool,
    yaml_path: Path,
) -> None:
    if not files:
        print(f"  {item_id}: nothing to publish, skipping")
        return

    print(f"  {item_id}: {len(files)} file(s)")
    for content_url in sorted(files):
        print(f"    {content_url} -> {archive_org_url(item_id, content_url)}")

    if dry_run:
        return

    import internetarchive as ia

    ia.upload(
        item_id,
        files={content_url: str(path) for content_url, path in files.items()},
        metadata=metadata,
        checksum=True,  # skip files whose remote MD5 already matches
        verbose=True,
    )

    record_same_as(yaml_path, [item_page_url(item_id), item_torrent_url(item_id)])


def publish_metadata_bundle(bands_dir: Path, dry_run: bool) -> None:
    """Uploads every band.yaml/release.yaml under bands_dir into one
    dedicated archive.org item (metadata_item_id()), each at its path
    relative to bands_dir (e.g. "m-spirit/band.yaml",
    "m-spirit/1995-.../release.yaml"). Run last, after the per-band/release
    loop, so every file's sameAs (recorded by publish_item above) is already
    in its final state before this upload. checksum=True means only files
    that actually changed get re-uploaded."""
    yaml_files = sorted(p for p in bands_dir.rglob("*.yaml") if p.name in ("band.yaml", "release.yaml"))
    files = {p.relative_to(bands_dir).as_posix(): p for p in yaml_files}
    item_id = metadata_item_id()

    print(f"\n{item_id}: metadata backup ({len(files)} file(s))")
    for rel_path in sorted(files):
        print(f"    {rel_path} -> {archive_org_url(item_id, rel_path)}")

    if dry_run:
        return

    import internetarchive as ia

    ia.upload(
        item_id,
        files={rel_path: str(path) for rel_path, path in files.items()},
        metadata={
            "mediatype": "data",
            "title": "Daugavpils Music Archive - metadata backup",
            "description": (
                "Full backup of every band.yaml/release.yaml from "
                "https://github.com/svalevka/daugavpils.fans, independent of GitHub. "
                "See that repo's MAINTENANCE.md for context."
            ),
        },
        checksum=True,
        verbose=True,
    )


def band_metadata(band: MusicGroup) -> dict[str, str]:
    metadata = {"mediatype": "data", "title": band.name, "creator": band.name}
    if band.foundingDate:
        metadata["date"] = band.foundingDate
    if band.genre:
        metadata["subject"] = "; ".join(band.genre)
    if band.description:
        metadata["description"] = band.description
    return metadata


def release_metadata(release: MusicAlbum, band: MusicGroup) -> dict[str, str]:
    metadata = {
        "mediatype": "audio",
        "title": release.name,
        "creator": band.name,
        "date": release.datePublished,
        "licenseurl": release.license,
    }
    if release.genre:
        metadata["subject"] = "; ".join(release.genre)
    if release.description:
        metadata["description"] = release.description
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show what would be published, without uploading")
    parser.add_argument(
        "--bands-dir",
        type=Path,
        default=REPO_ROOT / "bands",
        help="directory to publish from (default: this repo's bands/)",
    )
    args = parser.parse_args()

    bands_dir: Path = args.bands_dir
    require_valid_archive(bands_dir)

    for band_dir in sorted(p for p in bands_dir.iterdir() if p.is_dir()):
        band = load_band(band_dir)
        print(f"\n{band.name} ({band.slug})")
        publish_item(
            band_item_id(band.slug),
            media_files(band_dir, band.image + band.video),
            band_metadata(band),
            args.dry_run,
            band_dir / "band.yaml",
        )

        for release_dir in sorted(p for p in band_dir.iterdir() if p.is_dir()):
            release_yaml = release_dir / "release.yaml"
            if not release_yaml.exists():
                continue
            release = load_release(release_dir)
            files = media_files(release_dir, [t.audio for t in release.track] + release.image + release.video)
            publish_item(
                release_item_id(band.slug, release.slug),
                files,
                release_metadata(release, band),
                args.dry_run,
                release_yaml,
            )

    publish_metadata_bundle(bands_dir, args.dry_run)

    print("\nDry run: nothing was uploaded." if args.dry_run else "\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
