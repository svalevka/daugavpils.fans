#!/usr/bin/env python3
"""
Duration-based likely-duplicate video detection (see GitHub issue #51):
parsing the ISO 8601 durations this codebase writes, and matching a new
submission's duration against everything already published anywhere
under a band (band-level and every release combined).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import duplicate_detection  # noqa: E402
from test_support import ReviewAppTestCase  # noqa: E402
from validate import dump_yaml, load_yaml  # noqa: E402


class ParseIso8601DurationTest(unittest.TestCase):
    def test_minutes_and_seconds(self):
        self.assertEqual(duplicate_detection.parse_iso8601_duration_seconds("PT2M25S"), 145)

    def test_seconds_only(self):
        self.assertEqual(duplicate_detection.parse_iso8601_duration_seconds("PT45S"), 45)

    def test_hours_minutes_seconds(self):
        self.assertEqual(duplicate_detection.parse_iso8601_duration_seconds("PT1H2M3S"), 3723)

    def test_none_input(self):
        self.assertIsNone(duplicate_detection.parse_iso8601_duration_seconds(None))

    def test_empty_string(self):
        self.assertIsNone(duplicate_detection.parse_iso8601_duration_seconds(""))

    def test_malformed_string(self):
        self.assertIsNone(duplicate_detection.parse_iso8601_duration_seconds("not a duration"))


class FindDuplicateVideoTest(ReviewAppTestCase):
    def _band_yaml_path(self):
        return self.config.archive_checkout_path / "bands" / self.fx.band_slug / "band.yaml"

    def _release_yaml_path(self):
        return (
            self.config.archive_checkout_path
            / "bands"
            / self.fx.band_slug
            / self.fx.release_slug
            / "release.yaml"
        )

    def _add_band_video(self, name: str, duration_iso: str, content_url: str = "media/existing.mp4"):
        path = self._band_yaml_path()
        data = load_yaml(path)
        data.setdefault("video", []).append(
            {
                "@type": "VideoObject",
                "contentUrl": content_url,
                "encodingFormat": "video/mp4",
                "identifier": [{"@type": "PropertyValue", "propertyID": "sha256", "value": "a" * 64}],
                "name": name,
                "duration": duration_iso,
            }
        )
        dump_yaml(path, data)

    def _add_release_video(self, name: str, duration_iso: str, content_url: str = "video-existing.mp4"):
        path = self._release_yaml_path()
        data = load_yaml(path)
        data.setdefault("video", []).append(
            {
                "@type": "VideoObject",
                "contentUrl": content_url,
                "encodingFormat": "video/mp4",
                "identifier": [{"@type": "PropertyValue", "propertyID": "sha256", "value": "b" * 64}],
                "name": name,
                "duration": duration_iso,
            }
        )
        dump_yaml(path, data)

    def test_no_candidate_duration_means_no_match(self):
        self._add_band_video("Existing", "PT2M25S")
        match = duplicate_detection.find_duplicate_video(
            self.config.archive_checkout_path, self.fx.band_slug, None
        )
        self.assertIsNone(match)

    def test_no_existing_video_means_no_match(self):
        match = duplicate_detection.find_duplicate_video(
            self.config.archive_checkout_path, self.fx.band_slug, 145
        )
        self.assertIsNone(match)

    def test_exact_duration_match_at_band_level(self):
        self._add_band_video("Live at Grifs Club", "PT2M25S", content_url="media/live.mp4")
        match = duplicate_detection.find_duplicate_video(
            self.config.archive_checkout_path, self.fx.band_slug, 145
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "Live at Grifs Club")
        self.assertEqual(match.duration_seconds, 145)
        self.assertIn(self.fx.band_slug, match.url)
        self.assertIn("media/live.mp4", match.url)

    def test_within_tolerance_still_matches(self):
        self._add_band_video("Live at Grifs Club", "PT2M25S")
        # 145s existing vs 147s candidate - within the 3s tolerance
        match = duplicate_detection.find_duplicate_video(
            self.config.archive_checkout_path, self.fx.band_slug, 147
        )
        self.assertIsNotNone(match)

    def test_outside_tolerance_does_not_match(self):
        self._add_band_video("Live at Grifs Club", "PT2M25S")
        match = duplicate_detection.find_duplicate_video(
            self.config.archive_checkout_path, self.fx.band_slug, 200
        )
        self.assertIsNone(match)

    def test_release_level_video_is_also_checked(self):
        self._add_release_video("Album track video", "PT3M10S")
        match = duplicate_detection.find_duplicate_video(
            self.config.archive_checkout_path, self.fx.band_slug, 190
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "Album track video")
        self.assertIn(self.fx.release_slug, match.url)

    def test_unknown_band_returns_none(self):
        match = duplicate_detection.find_duplicate_video(
            self.config.archive_checkout_path, "does-not-exist", 145
        )
        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
