"""
Single source of truth for which band.yaml/release.yaml fields a public
text-edit proposal (see review_app/) is allowed to touch.

Both review_app (form rendering + submit-time check) and
tools/apply_proposal.py (the code that actually writes to disk) import this
list rather than each hand-maintaining their own copy of "which fields are
safe" - apply_proposal.py re-checks against it independently at write time
rather than trusting review_app's own check, since apply_proposal.py is the
code with push power and review_app is the code exposed to the public
internet.

Deliberately excluded, even though they're free text on the underlying
Pydantic models: `name` on a band or member (canonical identity, not safe
to swap out from under a slug/relationship), and `slug`/`byArtist`/`sameAs`/
anything under MediaObjectBase (contentUrl, encodingFormat, identifier,
bitrate, duration) - those are structural identity or machine-computed by
validate.py --write / publish_to_archive_org.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Target = Literal[
    "band",
    "release",
    "member",
    "track",
    "band_image",
    "release_image",
    "band_video",
    "release_video",
]
Kind = Literal["scalar", "list"]

# Targets whose field lives directly on band.yaml's top-level object /
# release.yaml's top-level object, vs. nested inside an indexed list on one
# of those two objects.
TOP_LEVEL_TARGETS: frozenset[Target] = frozenset({"band", "release"})
BAND_SCOPED_TARGETS: frozenset[Target] = frozenset({"band", "member", "band_image", "band_video"})
RELEASE_SCOPED_TARGETS: frozenset[Target] = frozenset({"release", "track", "release_image", "release_video"})

# Which attribute on the parent model each nested target's list lives at.
NESTED_LIST_ATTR: dict[Target, str] = {
    "member": "member",
    "track": "track",
    "band_image": "image",
    "release_image": "image",
    "band_video": "video",
    "release_video": "video",
}


@dataclass(frozen=True)
class EditableField:
    target: Target
    field: str
    kind: Kind
    label: str  # human-readable, for the submission form


EDITABLE_FIELDS: tuple[EditableField, ...] = (
    EditableField("band", "description", "scalar", "Biography"),
    EditableField("band", "description_en", "scalar", "Biography (English translation)"),
    EditableField("band", "location", "scalar", "Location"),
    EditableField("band", "alternateName", "list", "Alternate names"),
    EditableField("band", "genre", "list", "Genres"),
    EditableField("member", "name_en", "scalar", "Member name (transliteration)"),
    EditableField("member", "role", "scalar", "Member role"),
    EditableField("member", "role_en", "scalar", "Member role (English translation)"),
    EditableField("member", "period", "scalar", "Member period, e.g. '1994-1996'"),
    EditableField("release", "description", "scalar", "Provenance / liner notes"),
    EditableField("release", "description_en", "scalar", "Provenance / liner notes (English translation)"),
    EditableField("release", "genre", "list", "Genres"),
    EditableField("track", "alternateName", "scalar", "Alternate track title"),
    EditableField("band_image", "caption", "scalar", "Photo caption"),
    EditableField("band_image", "caption_en", "scalar", "Photo caption (English translation)"),
    EditableField("band_image", "contentLocation", "scalar", "Photo location"),
    EditableField("band_image", "depicts", "list", "Who/what is shown"),
    EditableField("release_image", "caption", "scalar", "Photo caption"),
    EditableField("release_image", "caption_en", "scalar", "Photo caption (English translation)"),
    EditableField("release_image", "contentLocation", "scalar", "Photo location"),
    EditableField("release_image", "depicts", "list", "Who/what is shown"),
    EditableField("band_video", "name", "scalar", "Video title"),
    EditableField("release_video", "name", "scalar", "Video title"),
)

_BY_KEY: dict[tuple[str, str], EditableField] = {(f.target, f.field): f for f in EDITABLE_FIELDS}


def lookup(target: str, field: str) -> EditableField | None:
    """Return the EditableField for (target, field), or None if it is not
    on the allowlist. Callers must treat None as "reject this proposal"."""
    return _BY_KEY.get((target, field))
