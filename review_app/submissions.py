"""
The public /submit flow (see GitHub issue #11): pick a band, optionally a
release, then one editable field - see the live current value, type a
replacement, submit. No account needed. Structured, not freeform: every
(target, field) combination shown or accepted here comes straight from
tools/editable_fields.py's allowlist, so a submission can never name a
field that isn't on it.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, request, session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import archive_read  # noqa: E402
import db  # noqa: E402
from editable_fields import EDITABLE_FIELDS, NESTED_LIST_ATTR, lookup  # noqa: E402
from models import MusicAlbum, MusicGroup  # noqa: E402

bp = Blueprint("submissions", __name__)


@dataclass(frozen=True)
class TargetOption:
    target: str
    field: str
    list_index: int | None
    label: str


def _item_label(target: str, index: int, item) -> str:
    """A human-readable prefix identifying one nested list entry: the
    item's own name for members/tracks (both have a `.name`), else a
    numbered "Photo N"/"Video N" (VideoObject has an optional `.name`
    too - preferred when set, since e.g. "Live at ..., 1995" is more
    useful than "Video 2")."""
    name = getattr(item, "name", None)
    if name:
        return name
    return f"{'Photo' if 'image' in target else 'Video'} {index + 1}"


def _target_options(
    parent: MusicGroup | MusicAlbum, singleton_target: str, nested_targets: frozenset[str]
) -> list[TargetOption]:
    """Every editable field applicable to `parent` - its own top-level
    fields plus one entry per item in each of its nested lists.

    `nested_targets` must be exactly the target names valid for this
    parent's type (e.g. {"member", "band_image", "band_video"} for a
    band) - NOT inferred from attribute presence: "band_image" and
    "release_image" both map to the same underlying attribute name
    ("image", via NESTED_LIST_ATTR) because both MusicGroup and
    MusicAlbum happen to have an `.image` list, so an attribute-existence
    check alone can't tell a band's photos from a release's."""
    options: list[TargetOption] = []
    for ef in EDITABLE_FIELDS:
        if ef.target == singleton_target:
            options.append(TargetOption(singleton_target, ef.field, None, ef.label))
        elif ef.target in nested_targets:
            for i, item in enumerate(getattr(parent, NESTED_LIST_ATTR[ef.target])):
                options.append(TargetOption(ef.target, ef.field, i, f"{_item_label(ef.target, i, item)}: {ef.label}"))
    return options


def _band_target_options(band: MusicGroup) -> list[TargetOption]:
    return _target_options(band, "band", frozenset({"member", "band_image", "band_video"}))


def _release_target_options(release: MusicAlbum) -> list[TargetOption]:
    return _target_options(release, "release", frozenset({"track", "release_image", "release_video"}))


@bp.get("/submit")
def pick_band():
    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    bands = archive_read.list_bands(checkout)
    return render_template("submit_pick_band.html", bands=bands)


@bp.get("/submit/<band_slug>")
def pick_band_scope(band_slug: str):
    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        band = archive_read.get_band(checkout, band_slug)
        releases = archive_read.list_releases(checkout, band_slug)
    except archive_read.ApplyError:
        abort(404)
    return render_template(
        "submit_pick_target.html",
        band_slug=band_slug,
        release_slug=None,
        releases=releases,
        options=_band_target_options(band),
    )


@bp.get("/submit/<band_slug>/<release_slug>")
def pick_release_scope(band_slug: str, release_slug: str):
    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        release = archive_read.get_release(checkout, band_slug, release_slug)
    except archive_read.ApplyError:
        abort(404)
    return render_template(
        "submit_pick_target.html",
        band_slug=band_slug,
        release_slug=release_slug,
        releases=None,
        options=_release_target_options(release),
    )


def _parse_list_index(raw: str) -> int | None:
    return int(raw) if raw else None


@bp.get("/submit/<band_slug>/edit")
def edit_band_field(band_slug: str):
    return _edit_form(band_slug, None)


@bp.get("/submit/<band_slug>/<release_slug>/edit")
def edit_release_field(band_slug: str, release_slug: str):
    return _edit_form(band_slug, release_slug)


def _edit_form(band_slug: str, release_slug: str | None):
    target = request.args.get("target", "")
    field = request.args.get("field", "")
    list_index = _parse_list_index(request.args.get("index", ""))

    editable = lookup(target, field)
    if editable is None:
        abort(404)

    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        value = archive_read.current_value(checkout, band_slug, release_slug, target, field, list_index)
    except archive_read.ApplyError:
        abort(404)

    current_text = "\n".join(value) if editable.kind == "list" else (value or "")
    return render_template(
        "submit_edit.html",
        band_slug=band_slug,
        release_slug=release_slug,
        target=target,
        field=field,
        list_index=list_index,
        label=editable.label,
        current_text=current_text,
    )


def _resolve_submitted_by_approver_id(
    conn, session_approver_id: int | None, submitter_contact: str | None
) -> int | None:
    """A proposal is stamped with the approver it came from when either
    is true: the submitter was logged in as an active approver (session,
    re-checked against the DB rather than trusted as-is - a stale session
    referencing a since-deactivated approver must not stamp), or they
    gave a contact email matching an active approver's, case-
    insensitively. Neither case: unstamped (None)."""
    if session_approver_id is not None:
        row = conn.execute(
            "SELECT id FROM approvers WHERE id = ? AND is_active = 1", (session_approver_id,)
        ).fetchone()
        if row is not None:
            return row["id"]
    if submitter_contact:
        row = conn.execute(
            "SELECT id FROM approvers WHERE is_active = 1 AND LOWER(email) = LOWER(?)",
            (submitter_contact,),
        ).fetchone()
        if row is not None:
            return row["id"]
    return None


@bp.post("/submit")
def create_proposal():
    conn = db.get_connection()
    ip = request.remote_addr or "unknown"

    limit = current_app.config["RATE_LIMIT_PER_IP_PER_HOUR"]
    recent = conn.execute(
        "SELECT COUNT(*) FROM submission_log WHERE ip = ? AND submitted_at > datetime('now', '-1 hour')",
        (ip,),
    ).fetchone()[0]
    if recent >= limit:
        abort(429)

    # Logged regardless of what happens next (honeypot included) - a bot
    # that fills the honeypot every time should still get rate-limited.
    conn.execute("INSERT INTO submission_log (ip) VALUES (?)", (ip,))
    conn.commit()

    if request.form.get("website"):  # honeypot: real users never see/fill this field
        # Same status code as a real success (201) - a differing code
        # would itself be a signal a bot could probe for.
        return render_template("submit_done.html"), 201

    band_slug = request.form.get("band_slug", "")
    release_slug = request.form.get("release_slug") or None
    target = request.form.get("target", "")
    field = request.form.get("field", "")
    list_index = _parse_list_index(request.form.get("list_index", ""))
    proposed_raw = request.form.get("proposed_value", "")
    submitter_name = request.form.get("submitter_name", "").strip() or None
    submitter_contact = request.form.get("submitter_contact", "").strip() or None

    editable = lookup(target, field)
    if editable is None:
        abort(400)

    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        original = archive_read.current_value(checkout, band_slug, release_slug, target, field, list_index)
    except archive_read.ApplyError:
        abort(400)

    if editable.kind == "list":
        proposed_value: str | list[str] = [line.strip() for line in proposed_raw.splitlines() if line.strip()]
        original_value: str | list[str] = list(original)
    else:
        proposed_value = proposed_raw
        original_value = original

    submitted_by_approver_id = _resolve_submitted_by_approver_id(
        conn, session.get("approver_id"), submitter_contact
    )

    conn.execute(
        """
        INSERT INTO proposals (
            band_slug, release_slug, target, list_index, field,
            original_value, proposed_value,
            submitter_name, submitter_contact, submitter_ip,
            submitted_by_approver_id, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            band_slug,
            release_slug,
            target,
            list_index,
            field,
            json.dumps(original_value),
            json.dumps(proposed_value),
            submitter_name,
            submitter_contact,
            ip,
            submitted_by_approver_id,
        ),
    )
    conn.commit()

    return render_template("submit_done.html"), 201
