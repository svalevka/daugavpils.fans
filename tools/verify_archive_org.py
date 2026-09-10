#!/usr/bin/env python3
"""
Audit archive.org against the local bands/**/*.yaml - the "bulletproof
mechanism" half of keeping the two in sync (tools/publish_to_archive_org.py
is the other half, the one that pushes changes).

archive.org's own consistency is eventually-consistent, not immediate: a
`ia delete` can return HTTP 204 (success) while the deleted file keeps
serving 200 for a long time afterward (observed still incomplete *hours*
later - see CLAUDE.md's "Deleting files from archive.org safely"), and an
item can show "currently being modified/updated by the task: archive" on
its own details page while that catches up. Nothing in tools/ can force
that to finish faster. What this script *can* do is tell you, on demand,
exactly what's actually still wrong right now - stale/orphaned files, a
description that never made it to archive.org - rather than everyone
assuming a publish "just worked" and finding out from a confused reader
instead (see GitHub issue for the incident this was built to catch).

This makes real network calls (one archive.org metadata+file-list fetch
per band/release) - it is not fast, and it is not meant to run in CI. Run
it after a publish you want to confirm, or periodically as a standing
health check.

Usage:
    python verify_archive_org.py                  # audit everything
    python verify_archive_org.py --bands SLUG ...  # only these band(s)
    python verify_archive_org.py --bands-dir PATH  # audit a different tree
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from archive_org import band_item_id, item_page_url, metadata_item_id, release_item_id
from publish_to_archive_org import band_metadata, load_band, load_release, media_files, release_metadata

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files archive.org generates itself alongside an upload - never something
# this repo's YAML references, so never "missing", and expected to be
# present as "extra" without that meaning anything is wrong. Necessarily a
# heuristic (archive.org doesn't tag these as derived in the file listing);
# kept conservative on purpose - false negatives here just mean a genuine
# orphan hides among noise once in a while, not that a real one gets
# silently ignored as a category.
_IGNORE_SUFFIXES = (
    "_meta.xml",
    "_meta.sqlite",
    "_files.xml",
    "_archive.torrent",
    "_spectrogram.png",
    ".afpk",
    ".ia.mp4",
)
_IGNORE_EXACT = {"__ia_thumb.jpg", "cover_thumb.jpg"}
_IGNORE_PREFIXES = ("history/",)
_IGNORE_CONTAINS = (".thumbs/",)


def _is_ia_generated(name: str, all_names: set[str]) -> bool:
    if name in _IGNORE_EXACT:
        return True
    if name.startswith(_IGNORE_PREFIXES):
        return True
    if any(part in name for part in _IGNORE_CONTAINS):
        return True
    if name.endswith(_IGNORE_SUFFIXES):
        return True
    # A bare waveform-preview PNG: archive.org derives "<base>.png" for an
    # audio file "<base>.mp3" (or "<base>.afpk") it hosts. A real cover
    # image in this repo is always .webp or .jpg, never a bare .png
    # sharing an audio file's exact basename, so this is safe.
    if name.endswith(".png"):
        stem = name[: -len(".png")]
        if f"{stem}.mp3" in all_names or f"{stem}.afpk" in all_names:
            return True
    return False


def local_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_item(item_id: str, expected_files: dict[str, Path], expected_metadata: dict[str, str]) -> list[str]:
    """Returns a list of human-readable problems found; empty means clean."""
    import internetarchive as ia

    problems: list[str] = []
    item = ia.get_item(item_id)

    if not item.exists:
        if expected_files:
            problems.append(f"item does not exist on archive.org at all (expected {len(expected_files)} file(s))")
        return problems

    actual_by_name = {f["name"]: f for f in item.files}
    actual_names = set(actual_by_name)
    expected_names = set(expected_files)

    missing = expected_names - actual_names
    for name in sorted(missing):
        problems.append(f"MISSING: {name} is in the YAML but not on archive.org")

    extra = actual_names - expected_names
    orphans = sorted(
        n for n in extra
        if actual_by_name[n].get("source") not in ("derivative", "metadata")
        and not _is_ia_generated(n, actual_names)
    )
    for name in orphans:
        problems.append(f"ORPHAN: {name} is on archive.org but not referenced by any current YAML (run: ia delete {item_id} {name})")

    for name in sorted(expected_names & actual_names):
        local_path = expected_files[name]
        if not local_path.exists():
            # No local media tree (CI, or a maintainer's partial checkout)
            # - presence/metadata is still fully checked above, only the
            # byte-level content comparison needs the actual file.
            continue
        remote_md5 = actual_by_name[name].get("md5")
        if not remote_md5:
            continue  # archive.org hasn't finished deriving this file's checksum yet
        if local_md5(local_path) != remote_md5:
            problems.append(f"CONTENT MISMATCH: {name}'s content on archive.org doesn't match the local file")

    for key, expected_value in expected_metadata.items():
        actual_value = item.metadata.get(key, "")
        # archive.org may re-wrap/re-encode whitespace; compare on
        # normalized text, not byte-for-byte, to avoid noise.
        if _normalize(expected_value) != _normalize(str(actual_value)):
            problems.append(f"METADATA DRIFT: '{key}' on archive.org doesn't match the local YAML")

    return problems


def audit_metadata_bundle(bands_dir: Path) -> list[str]:
    """Audit the consolidated metadata backup item (daugavpils-fans-metadata)
    to ensure every YAML file in bands/ is backed up with matching content."""
    import internetarchive as ia

    problems: list[str] = []
    item_id = metadata_item_id()
    item = ia.get_item(item_id)
    if not item.exists:
        problems.append(f"metadata backup item {item_id} does not exist on archive.org")
        return problems

    actual_by_name = {f["name"]: f for f in item.files}
    yaml_files = sorted(p for p in bands_dir.rglob("*.yaml") if p.name in ("band.yaml", "release.yaml"))
    for p in yaml_files:
        rel_path = p.relative_to(bands_dir).as_posix()
        if rel_path not in actual_by_name:
            problems.append(f"MISSING in {item_id}: {rel_path} is not in metadata backup bundle")
        else:
            local_hash = local_md5(p)
            remote_hash = actual_by_name[rel_path].get("md5")
            if remote_hash and local_hash != remote_hash:
                problems.append(
                    f"CONTENT MISMATCH in {item_id}: {rel_path} in metadata backup bundle doesn't match local YAML"
                )

    return problems


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bands-dir", type=Path, default=REPO_ROOT / "bands")
    parser.add_argument("--bands", nargs="+", metavar="SLUG", help="only audit these band(s), by slug")
    args = parser.parse_args()

    bands_dir: Path = args.bands_dir
    requested = set(args.bands) if args.bands else None

    has_local_media = any(bands_dir.glob("*/media")) or any(bands_dir.glob("*/*/[!.]*.mp3"))
    if not has_local_media:
        print(
            "No local media tree found under "
            f"{bands_dir} - presence and metadata are still fully checked, "
            "but byte-level CONTENT MISMATCH checks are skipped (they need "
            "the actual local files). Normal in CI; run with a full "
            "`download_archive.py`'d checkout for the complete check.\n"
        )

    any_problems = False
    for band_dir in sorted(p for p in bands_dir.iterdir() if p.is_dir()):
        if requested is not None and band_dir.name not in requested:
            continue
        band = load_band(band_dir)
        item_id = band_item_id(band.slug)
        print(f"\n{band.name} ({item_id})")
        problems = audit_item(item_id, media_files(band_dir, band.image + band.video), band_metadata(band))
        if problems:
            any_problems = True
            for p in problems:
                print(f"  {p}")
        else:
            print("  OK")

        for release_dir in sorted(p for p in band_dir.iterdir() if p.is_dir()):
            release_yaml = release_dir / "release.yaml"
            if not release_yaml.exists():
                continue
            release = load_release(release_dir)
            item_id = release_item_id(band.slug, release.slug)
            print(f"  {release.name} ({item_id})")
            files = media_files(release_dir, [t.audio for t in release.track] + release.image + release.video)
            problems = audit_item(item_id, files, release_metadata(release, band))
            if problems:
                any_problems = True
                for p in problems:
                    print(f"    {p}")
            else:
                print("    OK")

    if requested is None:
        bundle_id = metadata_item_id()
        print(f"\nMetadata backup bundle ({bundle_id})")
        bundle_problems = audit_metadata_bundle(bands_dir)
        if bundle_problems:
            any_problems = True
            for p in bundle_problems:
                print(f"  {p}")
        else:
            print("  OK")

    if any_problems:
        print("\nProblems found - see item page(s) for context, e.g.:", item_page_url(item_id))
        return 1
    print("\nOK: archive.org matches the local archive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
