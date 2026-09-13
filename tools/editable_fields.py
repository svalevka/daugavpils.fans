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
Pydantic models: `name` on a band (canonical identity, drives the band's
slug, not safe to swap out from under it), and `slug`/`byArtist`/`sameAs`/
anything under MediaObjectBase (contentUrl, encodingFormat, identifier,
bitrate, duration) - those are structural identity or machine-computed by
validate.py --write / publish_to_archive_org.py.

`member.name` is NOT excluded: members are matched by list_index (not by
name) throughout review_app/ and apply_proposal.py, so it carries no
structural identity - it's just the person's real-script name, which is
exactly what public correction proposals are for (e.g. fixing a misspelt
Russian name).

Proposing a brand *new* member (GitHub issue #39) doesn't fit this
per-field allowlist at all - there's no existing list_index to attach a
field edit to, since the item doesn't exist yet. That's `new_member`
below: a separate pseudo-target carrying a whole new record's worth of
fields at once, not looked up via `lookup()` like the field-edit targets
above.
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
    "new_member",
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
    label: str  # human-readable, for the submission form (English)
    label_ru: str = ""  # human-readable, for the submission form (Russian)

    def get_label(self, lang: str = "en") -> str:
        if lang == "ru" and self.label_ru:
            return self.label_ru
        return self.label


EDITABLE_FIELDS: tuple[EditableField, ...] = (
    EditableField("band", "description", "scalar", "Biography", "Биография / описание"),
    EditableField("band", "description_en", "scalar", "Biography (English translation)", "Биография (перевод на английский)"),
    EditableField("band", "location", "scalar", "Location", "Город / место"),
    EditableField("band", "alternateName", "list", "Alternate names", "Другие названия"),
    EditableField("band", "genre", "list", "Genres", "Жанры"),
    EditableField("member", "name", "scalar", "Member name", "Имя участника"),
    EditableField("member", "name_en", "scalar", "Member name (transliteration)", "Имя участника (латинская транслитерация)"),
    EditableField("member", "role", "scalar", "Member role", "Роль / инструменты"),
    EditableField("member", "role_en", "scalar", "Member role (English translation)", "Роль (на английском)"),
    EditableField("member", "period", "scalar", "Member period, e.g. '1994-1996'", "Период участия, например «1994–1996»"),
    EditableField("release", "description", "scalar", "Provenance / liner notes", "История записи / описание"),
    EditableField("release", "description_en", "scalar", "Provenance / liner notes (English translation)", "История записи (перевод на английский)"),
    EditableField("release", "genre", "list", "Genres", "Жанры"),
    EditableField("track", "alternateName", "scalar", "Alternate track title", "Альтернативное название трека"),
    EditableField("band_image", "caption", "scalar", "Photo caption", "Подпись к фотографии"),
    EditableField("band_image", "caption_en", "scalar", "Photo caption (English translation)", "Подпись к фотографии (на английском)"),
    EditableField("band_image", "contentLocation", "scalar", "Photo location", "Место съемки"),
    EditableField("band_image", "depicts", "list", "Who/what is shown", "Кто/что изображено"),
    EditableField("release_image", "caption", "scalar", "Photo caption", "Подпись к фотографии"),
    EditableField("release_image", "caption_en", "scalar", "Photo caption (English translation)", "Подпись к фотографии (на английском)"),
    EditableField("release_image", "contentLocation", "scalar", "Photo location", "Место съемки"),
    EditableField("release_image", "depicts", "list", "Who/what is shown", "Кто/что изображено"),
    EditableField("band_video", "name", "scalar", "Video title", "Название видео"),
    EditableField("release_video", "name", "scalar", "Video title", "Название видео"),
)

# Pseudo-target for proposing a brand new band member rather than editing
# an existing one (see tools/apply_proposal.py's _apply_new_member() and
# review_app/submissions.py's add-member routes). NEW_MEMBER_FIELDS is
# derived from EDITABLE_FIELDS's own "member" entries rather than
# hand-listed again, so the two can never drift apart.
NEW_MEMBER_TARGET: Target = "new_member"
NEW_MEMBER_FIELDS: tuple[str, ...] = tuple(ef.field for ef in EDITABLE_FIELDS if ef.target == "member")

_BY_KEY: dict[tuple[str, str], EditableField] = {(f.target, f.field): f for f in EDITABLE_FIELDS}


def lookup(target: str, field: str) -> EditableField | None:
    """Return the EditableField for (target, field), or None if it is not
    on the allowlist. Callers must treat None as "reject this proposal"."""
    return _BY_KEY.get((target, field))
