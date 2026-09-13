#!/usr/bin/env python3
"""
Unit and integration tests for the /submit/<band_slug>/add-release public flow
(GitHub issue #20).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import JPEG_BYTES, MP3_BYTES, ReviewAppTestCase  # noqa: E402


MOCK_PROBE_RESULT = {
    "duration_iso": "PT3M15S",
    "duration_seconds": 195.0,
    "bitrate": "320 kbps",
    "tags": {"title": "Track Title"},
    "ai_flags": [],
}


class AlbumSubmissionFormTest(ReviewAppTestCase):
    def test_get_form_for_existing_band_returns_200(self):
        resp = self.client.get(f"/submit/{self.fx.band_slug}/add-release")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Добавить новый альбом", resp.get_data(as_text=True))

    def test_get_form_in_english(self):
        resp = self.client.get(f"/submit/{self.fx.band_slug}/add-release?lang=en")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Add a new album", resp.get_data(as_text=True))

    def test_get_form_for_unknown_band_returns_404(self):
        resp = self.client.get("/submit/non-existent-band/add-release")
        self.assertEqual(resp.status_code, 404)


class AlbumSubmissionPostTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_valid_submission_creates_pending_album_proposal(self, _mock_probe):
        resp = self.submit_album(
            name="Debut Album",
            date_published="1995",
            genre="Punk Rock, Hardcore",
            description="Recorded live in Daugavpils",
            submitter_name="Aleksei",
            submitter_contact="aleksei@example.com",
            track_tuples=[("01-intro.mp3", MP3_BYTES), ("02-song.mp3", MP3_BYTES)],
            cover_tuple=("cover.jpg", JPEG_BYTES),
        )
        self.assertEqual(resp.status_code, 201)

        proposals = self.fetch_album_proposals()
        self.assertEqual(len(proposals), 1)
        prop = proposals[0]
        self.assertEqual(prop["status"], "pending")
        self.assertEqual(prop["band_slug"], self.fx.band_slug)
        self.assertEqual(prop["release_slug"], "1995-debut-album")
        self.assertEqual(prop["name"], "Debut Album")
        self.assertEqual(prop["date_published"], "1995")
        self.assertEqual(json.loads(prop["genre"]), ["Punk Rock", "Hardcore"])
        self.assertIsNotNone(prop["cover_stored_filename"])

        tracks = json.loads(prop["tracks_json"])
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["position"], 1)
        self.assertEqual(tracks[0]["duration"], "PT3M15S")
        self.assertEqual(tracks[1]["position"], 2)

        # Verify files were saved to uploads dir
        uploads_dir = self.config.resolved_media_uploads_path()
        self.assertTrue((uploads_dir / prop["cover_stored_filename"]).exists())
        self.assertTrue((uploads_dir / tracks[0]["stored_filename"]).exists())
        self.assertTrue((uploads_dir / tracks[1]["stored_filename"]).exists())

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_submission_without_cover_is_valid(self, _mock_probe):
        resp = self.submit_album(name="No Cover Album", date_published="1996")
        self.assertEqual(resp.status_code, 201)
        proposals = self.fetch_album_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertIsNone(proposals[0]["cover_stored_filename"])

    def test_honeypot_silently_accepted(self):
        resp = self.submit_album(website="spam-bot.com")
        self.assertEqual(resp.status_code, 201)
        proposals = self.fetch_album_proposals()
        self.assertEqual(len(proposals), 0)

    def test_missing_required_fields_aborts_400(self):
        resp = self.submit_album(name="")
        self.assertEqual(resp.status_code, 400)
        resp = self.submit_album(date_published="")
        self.assertEqual(resp.status_code, 400)

    def test_unknown_band_aborts_404(self):
        resp = self.submit_album(band_slug="non-existent-band")
        self.assertEqual(resp.status_code, 404)

    def test_no_tracks_aborts_400(self):
        resp = self.submit_album(track_tuples=[])
        self.assertEqual(resp.status_code, 400)

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_existing_release_collision_aborts_400(self, _mock_probe):
        # self.fx has an existing release in checkout
        existing_year = self.fx.release_slug[:4]
        existing_name = self.fx.release_slug[5:].replace("-", " ")
        resp = self.submit_album(name=existing_name, date_published=existing_year)
        self.assertEqual(resp.status_code, 400)

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_rate_limit_per_ip_enforced(self, _mock_probe):
        for _ in range(self.config.rate_limit_per_ip_per_hour):
            resp = self.submit_album(name=f"Album {_}", date_published=f"199{_ % 10}")
            self.assertEqual(resp.status_code, 201)
        resp = self.submit_album(name="Over limit", date_published="1999")
        self.assertEqual(resp.status_code, 429)

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_logged_in_approver_stamped(self, _mock_probe):
        self.login_as(1)
        self.submit_album()
        prop = self.fetch_album_proposals()[0]
        self.assertEqual(prop["submitted_by_approver_id"], 1)


if __name__ == "__main__":
    unittest.main()
