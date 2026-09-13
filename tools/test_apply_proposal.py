#!/usr/bin/env python3
"""
tools/apply_proposal.py is the only code with authority to turn an
approved public text-edit proposal into a real change to band.yaml/
release.yaml (see review_app/, built in a follow-on ticket). This suite
exercises it exclusively through its real CLI entry point, invoked as a
subprocess against a fixture archive tree - the same "invoke the real
tool, assert on exit code and file contents" idiom
tools/test_validate_write.py uses for validate.py --write - since that is
the actual, agreed seam: apply_proposal.py's behavior as seen by the
GitHub Action that runs it, not its internal functions.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import build_archive_with_nested_fields, run_apply_proposal, run_validate
from models import MusicAlbum, MusicGroup


def _canonical(model_cls, raw: dict) -> dict:
    """Round-trip `raw` through the same Pydantic model + model_dump(
    by_alias=True, exclude_none=True) idiom apply_proposal.py itself uses
    to re-dump a file, so a "before" snapshot taken straight from a
    hand-written fixture YAML can be compared fairly against an "after"
    snapshot that necessarily went through that same round-trip (which
    fills in default-valued fields like empty `video`/`sameAs` lists that
    a hand-written fixture may simply omit)."""
    return model_cls.model_validate(raw).model_dump(by_alias=True, exclude_none=True)


class ApplyValidFieldsTest(unittest.TestCase):
    """Each case asserts the full resulting document equals the full
    canonical "before" snapshot with exactly the target field changed -
    not just that the target field changed and a neighbor survived, so a
    stray mutation anywhere else in the file would fail the test."""

    def test_applies_band_scalar_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.band_yaml.read_text())
            expected = _canonical(MusicGroup, before)
            expected["description"] = "Corrected biography text."

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "band",
                    "list_index": None,
                    "field": "description",
                    "original_value": before["description"],
                    "proposed_value": "Corrected biography text.",
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.band_yaml.read_text()), expected)

            validate_result = run_validate(bands_dir)
            self.assertEqual(validate_result.returncode, 0, msg=validate_result.stdout + validate_result.stderr)

    def test_applies_release_scalar_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.release_yaml.read_text())
            expected = _canonical(MusicAlbum, before)
            expected["description_en"] = "Corrected provenance note (EN)."

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release",
                    "list_index": None,
                    "field": "description_en",
                    "original_value": before["description_en"],
                    "proposed_value": "Corrected provenance note (EN).",
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.release_yaml.read_text()), expected)

            validate_result = run_validate(bands_dir)
            self.assertEqual(validate_result.returncode, 0, msg=validate_result.stdout + validate_result.stderr)

    def test_applies_band_list_field_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.band_yaml.read_text())
            expected = _canonical(MusicGroup, before)
            expected["genre"] = ["post-punk", "coldwave"]

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "band",
                    "list_index": None,
                    "field": "genre",
                    "original_value": before["genre"],
                    "proposed_value": ["post-punk", "coldwave"],
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.band_yaml.read_text()), expected)

    def test_applies_nested_member_field_by_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.band_yaml.read_text())
            expected = _canonical(MusicGroup, before)
            expected["member"][0]["role_en"] = "lead vocals (EN)"

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "member",
                    "list_index": 0,
                    "field": "role_en",
                    "original_value": before["member"][0]["role_en"],
                    "proposed_value": "lead vocals (EN)",
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.band_yaml.read_text()), expected)

    def test_applies_nested_band_image_field_by_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.band_yaml.read_text())
            expected = _canonical(MusicGroup, before)
            expected["image"][0]["caption"] = "Corrected caption"

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "band_image",
                    "list_index": 0,
                    "field": "caption",
                    "original_value": before["image"][0]["caption"],
                    "proposed_value": "Corrected caption",
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.band_yaml.read_text()), expected)

    def test_applies_nested_release_image_field_by_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.release_yaml.read_text())
            expected = _canonical(MusicAlbum, before)
            expected["image"][0]["caption"] = "Corrected cover caption"

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release_image",
                    "list_index": 0,
                    "field": "caption",
                    "original_value": before["image"][0]["caption"],
                    "proposed_value": "Corrected cover caption",
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.release_yaml.read_text()), expected)

    def test_applies_track_field_by_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.release_yaml.read_text())
            expected = _canonical(MusicAlbum, before)
            expected["track"][0]["alternateName"] = "Corrected alt title"

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "track",
                    "list_index": 0,
                    "field": "alternateName",
                    "original_value": before["track"][0]["alternateName"],
                    "proposed_value": "Corrected alt title",
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.release_yaml.read_text()), expected)

    def test_applies_release_credit_text_list_replacement(self):
        # Same length as the fixture's existing creditText_en (1 entry) -
        # only creditText's own content changes, so the translation-
        # alignment invariant stays satisfied without also touching
        # creditText_en in this proposal (see
        # ApplyCreditTextTranslationAlignmentTest for the case where the
        # count itself changes).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.release_yaml.read_text())
            expected = _canonical(MusicAlbum, before)
            expected["creditText"] = ["Corrected Guest Vocalist - second vocals"]

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release",
                    "list_index": None,
                    "field": "creditText",
                    "original_value": before["creditText"],
                    "proposed_value": ["Corrected Guest Vocalist - second vocals"],
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.release_yaml.read_text()), expected)

            validate_result = run_validate(bands_dir)
            self.assertEqual(validate_result.returncode, 0, msg=validate_result.stdout + validate_result.stderr)

    def test_applies_release_credit_text_list_growth_with_matching_translation(self):
        # A real-world "add another credit" scenario: the count changes,
        # so a correct submitter proposal must grow creditText_en to
        # match in the same sitting - exercised here as two separate
        # apply_proposal.py invocations (band.yaml is re-read fresh each
        # time, same as the real Action does per proposal id), confirming
        # the invariant holds once *both* proposals have landed even
        # though it's momentarily unmatched in real review-queue terms
        # only if these were approved out of order (out of scope here -
        # see the alignment test class for the rejection case).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.release_yaml.read_text())

            r1 = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release",
                    "list_index": None,
                    "field": "creditText",
                    "original_value": before["creditText"],
                    "proposed_value": ["Guest Vocalist - second vocals", "Guest Bassist - bass"],
                },
                tmp_path,
            )
            self.assertEqual(r1.returncode, 0, msg=r1.stdout + r1.stderr)

            r2 = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release",
                    "list_index": None,
                    "field": "creditText_en",
                    "original_value": before["creditText_en"],
                    "proposed_value": [
                        "Guest Vocalist - second vocals (EN)",
                        "Guest Bassist - bass (EN)",
                    ],
                },
                tmp_path,
            )
            self.assertEqual(r2.returncode, 0, msg=r2.stdout + r2.stderr)

            after = yaml.safe_load(fx.release_yaml.read_text())
            self.assertEqual(after["creditText"], ["Guest Vocalist - second vocals", "Guest Bassist - bass"])
            self.assertEqual(
                after["creditText_en"],
                ["Guest Vocalist - second vocals (EN)", "Guest Bassist - bass (EN)"],
            )

            validate_result = run_validate(bands_dir)
            self.assertEqual(validate_result.returncode, 0, msg=validate_result.stdout + validate_result.stderr)


class ApplyCreditTextTranslationAlignmentTest(unittest.TestCase):
    """MusicAlbum's model_validator (GitHub issue #42) rejects a
    creditText_en whose length doesn't match creditText's - exercised
    here through the real apply_proposal.py CLI (not by calling the
    Pydantic model directly), since what actually matters is that a bad
    proposal gets cleanly refused (ApplyError, non-zero exit, untouched
    file) rather than crashing the Action with a raw traceback."""

    def test_matching_length_translation_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.release_yaml.read_text())
            expected = _canonical(MusicAlbum, before)
            expected["creditText_en"] = ["Guest Vocalist - second vocals (EN)"]

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release",
                    "list_index": None,
                    "field": "creditText_en",
                    "original_value": before["creditText_en"],
                    "proposed_value": ["Guest Vocalist - second vocals (EN)"],
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.release_yaml.read_text()), expected)

    def test_mismatched_length_translation_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.release_yaml.read_text()
            before = yaml.safe_load(before_text)

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release",
                    "list_index": None,
                    "field": "creditText_en",
                    "original_value": before["creditText_en"],
                    "proposed_value": ["Extra line one (EN)", "Extra line two (EN)"],
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("creditText_en has 2 entries but creditText only has 1", result.stderr)
            self.assertEqual(fx.release_yaml.read_text(), before_text)

    def test_shorter_translation_prefix_is_accepted(self):
        # The "add a credit now, translate it later" case (GitHub issue
        # #42's real design correction): creditText_en shorter than
        # creditText is a normal, valid "not yet translated" state, same
        # as every other unset _en field - must NOT be rejected.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.release_yaml.read_text())
            expected = _canonical(MusicAlbum, before)
            expected["creditText"] = ["Guest Vocalist - second vocals", "Guest Bassist - bass"]
            # creditText_en deliberately left at its original 1-entry
            # value - shorter than the new 2-entry creditText.

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release",
                    "list_index": None,
                    "field": "creditText",
                    "original_value": before["creditText"],
                    "proposed_value": ["Guest Vocalist - second vocals", "Guest Bassist - bass"],
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.release_yaml.read_text()), expected)

    def test_shrinking_credit_text_below_existing_translation_length_is_rejected(self):
        # The actual bug this invariant exists to catch: creditText
        # shrinks (a credit removed) without creditText_en being trimmed
        # to match - now longer than creditText, meaning a dangling
        # translation for a credit that no longer exists.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.release_yaml.read_text()
            before = yaml.safe_load(before_text)

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release",
                    "list_index": None,
                    "field": "creditText",
                    "original_value": before["creditText"],
                    "proposed_value": [],
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("creditText_en has 1 entries but creditText only has 0", result.stderr)
            self.assertEqual(fx.release_yaml.read_text(), before_text)


class ApplyNewMemberTest(unittest.TestCase):
    """target == "new_member" (GitHub issue #39): appends a whole new
    GroupMember to band.member rather than editing an existing one - a
    different code path from every case in ApplyValidFieldsTest above,
    since there's no existing list_index to compare original_value
    against."""

    def test_appends_a_new_member_with_only_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.band_yaml.read_text())
            expected = _canonical(MusicGroup, before)
            expected["member"].append({"name": "New Member"})

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "new_member",
                    "list_index": None,
                    "field": "",
                    "original_value": None,
                    "proposed_value": {"name": "New Member"},
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.band_yaml.read_text()), expected)

            validate_result = run_validate(bands_dir)
            self.assertEqual(validate_result.returncode, 0, msg=validate_result.stdout + validate_result.stderr)

    def test_appends_a_new_member_with_every_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before = yaml.safe_load(fx.band_yaml.read_text())
            expected = _canonical(MusicGroup, before)
            new_member = {
                "name": "Anna Kalniņa",
                "name_en": "Anna Kalnina",
                "role": "bass",
                "role_en": "bass (EN)",
                "period": "1994-1996",
            }
            expected["member"].append(new_member)

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "new_member",
                    "list_index": None,
                    "field": "",
                    "original_value": None,
                    "proposed_value": new_member,
                },
                tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(yaml.safe_load(fx.band_yaml.read_text()), expected)

    def test_rejects_missing_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.band_yaml.read_text()

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "new_member",
                    "list_index": None,
                    "field": "",
                    "original_value": None,
                    "proposed_value": {"role": "bass"},
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fx.band_yaml.read_text(), before_text)

    def test_rejects_unknown_field_in_proposed_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.band_yaml.read_text()

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "new_member",
                    "list_index": None,
                    "field": "",
                    "original_value": None,
                    "proposed_value": {"name": "New Member", "slug": "hijacked"},
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fx.band_yaml.read_text(), before_text)

    def test_rejects_release_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.band_yaml.read_text()

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "new_member",
                    "list_index": None,
                    "field": "",
                    "original_value": None,
                    "proposed_value": {"name": "New Member"},
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fx.band_yaml.read_text(), before_text)

    def test_rejects_list_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.band_yaml.read_text()

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "new_member",
                    "list_index": 0,
                    "field": "",
                    "original_value": None,
                    "proposed_value": {"name": "New Member"},
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fx.band_yaml.read_text(), before_text)


class ApplyRejectsInvalidRequestsTest(unittest.TestCase):
    def test_rejects_disallowed_structural_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.band_yaml.read_text()

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "band",
                    "list_index": None,
                    "field": "slug",
                    "original_value": fx.band_slug,
                    "proposed_value": "hijacked-slug",
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fx.band_yaml.read_text(), before_text)

    def test_rejects_media_object_base_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.release_yaml.read_text()

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": fx.release_slug,
                    "target": "release_image",
                    "list_index": 0,
                    "field": "contentUrl",
                    "original_value": "cover.png",
                    "proposed_value": "/etc/passwd",
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fx.release_yaml.read_text(), before_text)

    def test_rejects_unknown_band_slug_without_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            build_archive_with_nested_fields(bands_dir)
            before_entries = sorted(p.name for p in bands_dir.iterdir())

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": "does-not-exist",
                    "release_slug": None,
                    "target": "band",
                    "list_index": None,
                    "field": "description",
                    "original_value": "anything",
                    "proposed_value": "anything else",
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            # No stray file or directory (e.g. a "does-not-exist" band
            # folder) should be created as a side effect of the attempt.
            self.assertEqual(sorted(p.name for p in bands_dir.iterdir()), before_entries)

    def test_rejects_path_traversal_slug_without_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            build_archive_with_nested_fields(bands_dir)

            sentinel = tmp_path / "sentinel.txt"
            sentinel.write_text("must not be touched")

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": "../sentinel.txt",
                    "release_slug": None,
                    "target": "band",
                    "list_index": None,
                    "field": "description",
                    "original_value": "anything",
                    "proposed_value": "anything else",
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(), "must not be touched")

    def test_rejects_stale_original_value_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bands_dir = tmp_path / "bands"
            fx = build_archive_with_nested_fields(bands_dir)
            before_text = fx.band_yaml.read_text()

            result = run_apply_proposal(
                bands_dir,
                {
                    "band_slug": fx.band_slug,
                    "release_slug": None,
                    "target": "band",
                    "list_index": None,
                    "field": "description",
                    "original_value": "this is not the current text",
                    "proposed_value": "Corrected biography text.",
                },
                tmp_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fx.band_yaml.read_text(), before_text)


if __name__ == "__main__":
    unittest.main()
