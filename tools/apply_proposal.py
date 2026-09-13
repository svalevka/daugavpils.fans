#!/usr/bin/env python3
"""
Apply one approved text-edit proposal (see review_app/) to the target
band.yaml/release.yaml and write the file back.

This is the only piece of code with authority to turn a proposal that a
member of the public typed into a public web form into an actual change to
the archive's metadata - it runs inside the GitHub Action
(.github/workflows/apply-proposal.yml) that review_app triggers on
approval, never inside review_app itself, and review_app never holds a
credential that can push to git. Because of that, this script re-checks
everything from scratch rather than trusting review_app's own checks:

- (target, field) must be on tools/editable_fields.py's allowlist, even
  though review_app already enforced this at submission time.
- band_slug/release_slug must be plain slugs (no path separators/`..`),
  and the resolved paths must land inside bands_dir.
- `original_value` must still match what's actually in the file right now.
  review_app's snapshot could be stale (someone else edited the file, or
  approved a second, conflicting proposal for the same field, since the
  submission was made); the GitHub Action's own `actions/checkout` is
  always fresh HEAD, so this check is meaningful independent of how stale
  review_app's cached copy was.

On success, mutates the target model in place and re-dumps the *entire*
file via the same yaml.safe_load -> Pydantic model -> model_dump(by_alias=
True, exclude_none=True) -> yaml.safe_dump(allow_unicode=True, sort_keys=
False) idiom tools/validate.py --write already uses, so the resulting diff
stays minimal and consistent with how the rest of the tooling writes YAML.
Does not itself run tools/validate.py - the caller (the Action) does that
immediately afterward as a safety net.

Usage:
    python apply_proposal.py --proposal-file proposal.json [--bands-dir PATH]

proposal.json shape (as returned by review_app's GET /api/proposals/<id>):
    {
        "band_slug": "m-spirit",
        "release_slug": null,
        "target": "band",
        "list_index": null,
        "field": "description_en",
        "original_value": "...",
        "proposed_value": "..."
    }
`release_slug`/`list_index` are null unless the target needs them.
`original_value`/`proposed_value` are strings for scalar fields, lists of
strings for list fields (see tools/editable_fields.py's `kind`).

`target: "new_member"` (GitHub issue #39) is a different shape: it
proposes a whole new band member rather than an edit to an existing
field, so `field`/`list_index` are unused (no existing item to attach a
field edit to), `original_value` is always null, and `proposed_value` is
an object whose keys are a subset of tools/editable_fields.py's
NEW_MEMBER_FIELDS - e.g. {"name": "...", "role": "..."}.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from editable_fields import (
    BAND_SCOPED_TARGETS,
    NESTED_LIST_ATTR,
    NEW_MEMBER_FIELDS,
    NEW_MEMBER_TARGET,
    RELEASE_SCOPED_TARGETS,
    TOP_LEVEL_TARGETS,
    lookup,
)
from models import GroupMember, MusicAlbum, MusicGroup

REPO_ROOT = Path(__file__).resolve().parent.parent

# "ASCII, URL-safe folder-name" per models.py's own field descriptions -
# also exactly what rules out path traversal (`..`, `/`, etc.).
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ApplyError(Exception):
    """A proposal could not be applied. The message is safe to print as-is."""


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def dump_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def _resolve_slug_dir(bands_dir: Path, slug: str, what: str) -> Path:
    if not SLUG_RE.match(slug):
        raise ApplyError(f"{what} {slug!r} is not a valid slug")
    resolved_bands_dir = bands_dir.resolve()
    candidate = (bands_dir / slug).resolve()
    if candidate.parent != resolved_bands_dir and candidate != resolved_bands_dir / slug:
        # Belt-and-braces: even a valid-looking slug shouldn't be able to
        # resolve outside bands_dir. SLUG_RE already rules this out in
        # practice, but a symlink inside bands_dir could not - refuse.
        raise ApplyError(f"{what} {slug!r} does not resolve inside {bands_dir}")
    if not candidate.is_dir():
        raise ApplyError(f"{what} {slug!r} does not exist")
    return candidate


def load_band(bands_dir: Path, band_slug: str) -> tuple[Path, Path, MusicGroup]:
    band_dir = _resolve_slug_dir(bands_dir, band_slug, "band")
    band_yaml = band_dir / "band.yaml"
    if not band_yaml.exists():
        raise ApplyError(f"{band_dir} has no band.yaml")
    try:
        band = MusicGroup.model_validate(load_yaml(band_yaml))
    except ValidationError as e:
        raise ApplyError(f"{band_yaml}: schema validation failed:\n{e}") from e
    return band_dir, band_yaml, band


def load_release(band_dir: Path, release_slug: str) -> tuple[Path, Path, MusicAlbum]:
    if not SLUG_RE.match(release_slug):
        raise ApplyError(f"release slug {release_slug!r} is not a valid slug")
    release_dir = (band_dir / release_slug).resolve()
    if release_dir.parent != band_dir.resolve():
        raise ApplyError(f"release slug {release_slug!r} does not resolve inside {band_dir}")
    release_yaml = release_dir / "release.yaml"
    if not release_yaml.exists():
        raise ApplyError(f"{release_dir} has no release.yaml")
    try:
        release = MusicAlbum.model_validate(load_yaml(release_yaml))
    except ValidationError as e:
        raise ApplyError(f"{release_yaml}: schema validation failed:\n{e}") from e
    return release_dir, release_yaml, release


def container_for(
    target: str,
    list_index: int | None,
    band: MusicGroup | None,
    release: MusicAlbum | None,
) -> BaseModel:
    """Return the specific model instance (band, release, or one entry of
    one of their nested lists) that the proposal's field actually lives
    on. Reused read-only by review_app/archive_read.py to show a
    submitter the live current value of whatever they're proposing to
    change, so that display can never drift from what this same
    navigation logic will compare against when the proposal is applied."""
    if target == "band":
        assert band is not None
        return band
    if target == "release":
        assert release is not None
        return release

    parent = band if target in BAND_SCOPED_TARGETS else release
    assert parent is not None
    list_attr = NESTED_LIST_ATTR[target]
    items = getattr(parent, list_attr)
    if list_index is None:
        raise ApplyError(f"target {target!r} requires list_index")
    if not (0 <= list_index < len(items)):
        raise ApplyError(f"{target}[{list_index}] does not exist (list has {len(items)} entries)")
    return items[list_index]


def _apply_new_member(proposal: dict[str, Any], bands_dir: Path) -> Path:
    """Handle target == "new_member": append a whole new GroupMember to
    the band's member list, rather than editing an existing one. No
    list_index (the item doesn't exist yet) and no staleness check
    against original_value (there's nothing existing to have gone stale -
    appending can't clobber a concurrent edit or another concurrent
    addition the way overwriting a field in place could)."""
    band_slug = proposal["band_slug"]
    if proposal.get("release_slug"):
        raise ApplyError("target 'new_member' must not have release_slug")
    if proposal.get("list_index") is not None:
        raise ApplyError("target 'new_member' must not have list_index")

    proposed_value = proposal["proposed_value"]
    if not isinstance(proposed_value, dict):
        raise ApplyError("target 'new_member' requires an object proposed_value")
    unknown_fields = sorted(set(proposed_value) - set(NEW_MEMBER_FIELDS))
    if unknown_fields:
        raise ApplyError(f"new_member proposed_value has unknown field(s): {unknown_fields}")

    _, band_yaml, band = load_band(bands_dir, band_slug)

    try:
        new_member = GroupMember.model_validate(proposed_value)
    except ValidationError as e:
        raise ApplyError(f"invalid new member: {e}") from e

    band.member.append(new_member)
    dump_yaml(band_yaml, band.model_dump(by_alias=True, exclude_none=True))
    return band_yaml


def apply_proposal(proposal: dict[str, Any], bands_dir: Path) -> Path:
    """Apply one proposal in place. Returns the path of the YAML file that
    was rewritten. Raises ApplyError (message is user-safe) on any
    problem, and never touches the filesystem before every check passes."""
    target = proposal["target"]
    if target == NEW_MEMBER_TARGET:
        return _apply_new_member(proposal, bands_dir)

    field = proposal["field"]
    band_slug = proposal["band_slug"]
    release_slug = proposal.get("release_slug")
    list_index = proposal.get("list_index")
    original_value = proposal["original_value"]
    proposed_value = proposal["proposed_value"]

    editable = lookup(target, field)
    if editable is None:
        raise ApplyError(f"({target}, {field}) is not an editable field")

    if target in RELEASE_SCOPED_TARGETS and not release_slug:
        raise ApplyError(f"target {target!r} requires release_slug")
    if target in BAND_SCOPED_TARGETS and target not in RELEASE_SCOPED_TARGETS and release_slug:
        raise ApplyError(f"target {target!r} must not have release_slug")

    band_dir, band_yaml, band = load_band(bands_dir, band_slug)

    release_dir = release_yaml = release = None
    if release_slug:
        release_dir, release_yaml, release = load_release(band_dir, release_slug)

    container = container_for(target, list_index, band, release)

    current_value = getattr(container, field)
    if editable.kind == "list":
        current_value = list(current_value)
    if current_value != original_value:
        raise ApplyError(
            f"stale proposal: {target}.{field} is currently {current_value!r}, "
            f"expected {original_value!r} (someone else may have changed it since this "
            f"proposal was submitted)"
        )

    # A model-level validator (e.g. MusicAlbum's creditText/creditText_en
    # length check, GitHub issue #42) can reject this assignment outright.
    # Caught and re-raised as ApplyError to match every other rejection
    # path in this function, rather than letting a bare pydantic
    # ValidationError crash the CLI with a raw traceback - the field-level
    # allowlist/staleness checks above already all raise ApplyError, and a
    # model-level check should be no different from the caller's
    # perspective. (The container is left with proposed_value's cross-field
    # invariant violated in memory at this point - Pydantic's
    # validate_assignment doesn't roll back a rejected assignment - but
    # nothing downstream reads it: this function returns/raises here,
    # ending this process's use of it.)
    try:
        setattr(container, field, proposed_value)
    except ValidationError as e:
        raise ApplyError(f"invalid value for {target}.{field}: {e}") from e

    if target in TOP_LEVEL_TARGETS:
        rewritten_path = band_yaml if target == "band" else release_yaml
        rewritten_model = band if target == "band" else release
    elif target in BAND_SCOPED_TARGETS:
        rewritten_path, rewritten_model = band_yaml, band
    else:
        rewritten_path, rewritten_model = release_yaml, release

    dump_yaml(rewritten_path, rewritten_model.model_dump(by_alias=True, exclude_none=True))
    return rewritten_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--proposal-file", type=Path, required=True, help="path to the approved proposal, as JSON"
    )
    parser.add_argument(
        "--bands-dir",
        type=Path,
        default=REPO_ROOT / "bands",
        help="directory to apply the proposal to (default: this repo's bands/)",
    )
    args = parser.parse_args()

    proposal = json.loads(args.proposal_file.read_text())

    try:
        rewritten_path = apply_proposal(proposal, args.bands_dir)
    except ApplyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"applied proposal to {rewritten_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
