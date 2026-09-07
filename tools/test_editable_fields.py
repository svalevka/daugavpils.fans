#!/usr/bin/env python3
"""
tools/editable_fields.py is the single allowlist tools/apply_proposal.py
(and, in a follow-on ticket, review_app) trust to decide what a public
text-edit proposal may touch. This checks its public interface (lookup())
directly against the exact stage-1 field list agreed in the PRD, and
confirms the explicitly-excluded structural/computed fields are absent.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from editable_fields import lookup


class EditableFieldsAllowlistTest(unittest.TestCase):
    def test_allows_exactly_the_stage_one_fields(self):
        expected = {
            ("band", "description"),
            ("band", "description_en"),
            ("band", "location"),
            ("band", "alternateName"),
            ("band", "genre"),
            ("member", "name_en"),
            ("member", "role"),
            ("member", "role_en"),
            ("member", "period"),
            ("release", "description"),
            ("release", "description_en"),
            ("release", "genre"),
            ("track", "alternateName"),
            ("band_image", "caption"),
            ("band_image", "caption_en"),
            ("band_image", "contentLocation"),
            ("band_image", "depicts"),
            ("release_image", "caption"),
            ("release_image", "caption_en"),
            ("release_image", "contentLocation"),
            ("release_image", "depicts"),
            ("band_video", "name"),
            ("release_video", "name"),
        }
        for target, field in expected:
            with self.subTest(target=target, field=field):
                self.assertIsNotNone(lookup(target, field))

    def test_excludes_structural_and_computed_fields(self):
        excluded = [
            ("band", "slug"),
            ("band", "name"),
            ("band", "sameAs"),
            ("member", "name"),
            ("release", "slug"),
            ("release", "byArtist"),
            ("release", "sameAs"),
            ("release_image", "contentUrl"),
            ("release_image", "encodingFormat"),
            ("release_image", "identifier"),
            ("band_video", "bitrate"),
            ("band_video", "duration"),
            ("track", "position"),
        ]
        for target, field in excluded:
            with self.subTest(target=target, field=field):
                self.assertIsNone(lookup(target, field))

    def test_lookup_returns_none_for_unknown_field(self):
        self.assertIsNone(lookup("band", "not_a_real_field"))


if __name__ == "__main__":
    unittest.main()
