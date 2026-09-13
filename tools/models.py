"""
Pydantic models for the Daugavpils music archive.

These models are the working representation used by tools/validate.py and
tools/export_schema.py. They are aligned to schema.org vocabulary
(MusicGroup, MusicAlbum, MusicRecording, AudioObject, ImageObject,
VideoObject, PropertyValue) so the *exported JSON Schema*
(schema/*.schema.json) — not this Python code — is the portable,
language-agnostic contract for the archive. Any tool in any language can
validate a band.yaml/release.yaml against those JSON Schema files without
ever touching Python or Pydantic.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PropertyValue(BaseModel):
    """schema.org PropertyValue - used here to carry the sha256 checksum."""

    model_config = ConfigDict(populate_by_name=True)

    type_: str = Field(default="PropertyValue", alias="@type")
    propertyID: str
    value: str


class MediaObjectBase(BaseModel):
    """Shared fields for schema.org MediaObject subtypes (AudioObject,
    VideoObject, ImageObject) - one media file on disk. Like audio, image
    and video files are kept out of git (see .gitignore) and referenced by
    a relative path; they are checksummed the same way."""

    model_config = ConfigDict(populate_by_name=True)

    contentUrl: str = Field(description="Path to the file, relative to its band/release folder")
    encodingFormat: str = Field(description="MIME type, e.g. audio/mpeg, video/mp4, image/jpeg")
    identifier: list[PropertyValue] = Field(
        default_factory=list,
        description="Checksums etc, e.g. a PropertyValue with propertyID=sha256",
    )


class AudioObject(MediaObjectBase):
    """schema.org AudioObject describing one audio file on disk."""

    type_: str = Field(default="AudioObject", alias="@type")
    bitrate: Optional[str] = Field(default=None, description="e.g. '320 kbps'")
    duration: Optional[str] = Field(default=None, description="ISO 8601 duration, e.g. PT1M30S")


class VideoObject(MediaObjectBase):
    """schema.org VideoObject - e.g. concert footage, an interview, a music video."""

    type_: str = Field(default="VideoObject", alias="@type")
    name: Optional[str] = Field(default=None, description="e.g. 'Live at ..., 1995'")
    bitrate: Optional[str] = Field(default=None, description="e.g. '2000 kbps'")
    duration: Optional[str] = Field(default=None, description="ISO 8601 duration, e.g. PT3M12S")


class ImageObject(MediaObjectBase):
    """schema.org ImageObject - e.g. a band photo, album cover, or press clipping."""

    type_: str = Field(default="ImageObject", alias="@type")
    caption: Optional[str] = Field(default=None, description="e.g. 'Band photo, 1995'")
    caption_en: Optional[str] = Field(default=None, description="English translation of caption")
    contentLocation: Optional[str] = Field(
        default=None, description="Where the photo was taken, e.g. 'Valka, Latvia'"
    )
    depicts: list[str] = Field(
        default_factory=list,
        description="Names of people or things shown, e.g. ['Vaida', 'Valera']. "
        "Free text, not required to match member[].name - a photo may show "
        "people who were never formal members.",
    )


class MusicRecording(BaseModel):
    """schema.org MusicRecording - one track."""

    model_config = ConfigDict(populate_by_name=True)

    type_: str = Field(default="MusicRecording", alias="@type")
    position: int = Field(description="Track number, source of truth for ordering")
    name: str = Field(description="Canonical title")
    alternateName: Optional[str] = Field(
        default=None, description="Other known title, e.g. from a differing filename"
    )
    audio: Optional[AudioObject] = Field(
        default=None, description="Audio file on disk; None if the recording is lost/unpreserved"
    )


class GroupMember(BaseModel):
    """A person's involvement in a MusicGroup. Not a formal schema.org type;
    kept intentionally minimal (schema.org has no standard way to attach a
    role/period to a member relationship).

    Reserved for actual, ongoing band membership (hence `period`) - not for
    someone who only contributed to one release (e.g. a guest vocalist on a
    single album). That belongs on that specific MusicAlbum's `creditText`
    instead (GitHub issue #42), which needs no period of its own: the
    release itself is the temporal marker."""

    name: str = Field(description="Name as actually written, in its real script/alphabet")
    name_en: Optional[str] = Field(default=None, description="Latin transliteration of name")
    role: Optional[str] = None
    role_en: Optional[str] = Field(default=None, description="English translation of role")
    period: Optional[str] = Field(default=None, description="e.g. '1994-1996'")


class MusicGroup(BaseModel):
    """schema.org MusicGroup - one band."""

    model_config = ConfigDict(populate_by_name=True)

    type_: str = Field(default="MusicGroup", alias="@type")
    name: str = Field(description="Name as actually written, in its real script/alphabet, e.g. 'M. Spirit'")
    slug: str = Field(description="ASCII, URL-safe folder-name version of the name, e.g. 'm-spirit'")
    alternateName: list[str] = Field(default_factory=list)
    foundingDate: Optional[str] = None
    dissolutionDate: Optional[str] = None
    location: Optional[str] = Field(default=None, description="City, e.g. 'Daugavpils'")
    genre: list[str] = Field(default_factory=list)
    member: list[GroupMember] = Field(default_factory=list)
    description: Optional[str] = Field(
        default=None, description="Biography, in the language it was actually written in - may be long-form"
    )
    description_en: Optional[str] = Field(
        default=None,
        description="English translation of description, only for editorial/provenance text - "
        "never used to translate first-hand testimony, which stays in its original words",
    )
    sameAs: list[str] = Field(default_factory=list, description="External reference URLs")
    image: list[ImageObject] = Field(default_factory=list, description="Band photos")
    video: list[VideoObject] = Field(
        default_factory=list, description="Concert footage, interviews, etc. not tied to one release"
    )


class MusicAlbum(BaseModel):
    """schema.org MusicAlbum - one release by a MusicGroup."""

    # validate_assignment=True (GitHub issue #42): tools/apply_proposal.py's
    # generic edit path mutates an already-loaded model via plain
    # `setattr()`, not by re-running model_validate() - without this,
    # creditText's own model_validator below would only ever fire on
    # initial load (the pre-edit state), never on the proposed edit
    # actually being applied, silently defeating the whole point of the
    # check. Scoped to this one model rather than every model in this
    # file, since it's the one that currently needs a cross-field
    # invariant enforced on the setattr path.
    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    type_: str = Field(default="MusicAlbum", alias="@type")
    name: str = Field(description="Album title as actually written, in its real script/alphabet")
    slug: str = Field(description="ASCII, URL-safe folder-name version of the title")
    datePublished: str = Field(description="Year or ISO date")
    byArtist: str = Field(description="Slug of the MusicGroup this release belongs to")
    genre: list[str] = Field(default_factory=list)
    license: Optional[str] = Field(
        default=None, description="License URL, e.g. a Creative Commons license URL"
    )
    description: Optional[str] = Field(default=None, description="Provenance / liner notes")
    description_en: Optional[str] = Field(
        default=None, description="English translation of description (editorial/provenance text, not lyrics)"
    )
    sameAs: list[str] = Field(default_factory=list, description="External reference URLs")
    track: list[MusicRecording]
    image: list[ImageObject] = Field(default_factory=list, description="Cover art, era photos")
    video: list[VideoObject] = Field(
        default_factory=list, description="e.g. an official music video, or live footage from this release's era"
    )
    creditText: list[str] = Field(
        default_factory=list,
        description="Free-text credits for people who contributed to this specific release only "
        "(e.g. a guest vocalist), not ongoing band members - one line per credit, "
        "e.g. 'Anna Kalniņa - second vocals'. Not band.member: this release is itself "
        "the temporal/scope marker, so no period is needed or wanted here.",
    )
    creditText_en: list[str] = Field(
        default_factory=list,
        description="English translation of creditText, same optional/fallback convention as "
        "description_en/role_en/caption_en. Translates a *prefix* of creditText 1:1 by "
        "position - entries beyond len(creditText_en) simply aren't translated yet (same "
        "'not yet translated' meaning every other _en field already has when unset). Must "
        "never be longer than creditText.",
    )

    @property
    def has_audio(self) -> bool:
        return any(t.audio is not None for t in self.track)

    @model_validator(mode="after")
    def _check_credit_text_translation_alignment(self) -> "MusicAlbum":
        # <=, not == : creditText and creditText_en are each edited by a
        # separate proposal (apply_proposal.py's pipeline only ever
        # touches one field per proposal), so "add a new credit, translate
        # it later" necessarily passes through a state where creditText_en
        # is shorter than creditText - that's the normal, desired
        # "not yet translated" case, not a bug. Only creditText_en ending
        # up *longer* (e.g. creditText shrank without creditText_en being
        # trimmed to match) is the actual dangling-translation bug this
        # guards against.
        if len(self.creditText_en) > len(self.creditText):
            raise ValueError(
                f"creditText_en has {len(self.creditText_en)} entries but creditText only has "
                f"{len(self.creditText)} - creditText_en must never be longer than creditText "
                "(it translates a prefix of it, 1:1 by position)"
            )
        return self
