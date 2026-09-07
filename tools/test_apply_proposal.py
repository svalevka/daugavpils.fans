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
