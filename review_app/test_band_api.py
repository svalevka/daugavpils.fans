#!/usr/bin/env python3
"""
Unit and integration tests for the authenticated band proposal callback API
used by GitHub Actions (.github/workflows/apply-band-proposal.yml) (GitHub issue #22):
- Auth verification with callback key
- Fetching approved band metadata, photo, release, tracks
- Downloading photo, cover, and audio track binary files
- Listing approved proposals with rolling 24-hour throttle
- Recording success/failure publish results and file cleanup
"""
from __future__ import annotations

import json
import sqlite3
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


class BandApiAuthTest(ReviewAppTestCase):
    def test_missing_or_invalid_bearer_token_is_rejected(self):
        self.submit_band(name="Тест Авторизации")
        prop_id = self.fetch_band_proposals()[0]["id"]

        endpoints = [
            ("GET", f"/api/band-proposals/{prop_id}"),
            ("GET", f"/api/band-proposals/{prop_id}/photo"),
            ("GET", f"/api/band-proposals/{prop_id}/cover"),
            ("GET", f"/api/band-proposals/{prop_id}/tracks/1/file"),
            ("GET", "/api/band-proposals/approved"),
            ("POST", f"/api/band-proposals/{prop_id}/result"),
        ]
        for method, ep in endpoints:
            no_auth = self.client.open(ep, method=method)
            self.assertEqual(no_auth.status_code, 401, f"{method} {ep} without token")

            bad_auth = self.client.open(ep, method=method, headers={"Authorization": "Bearer wrong"})
            self.assertEqual(bad_auth.status_code, 401, f"{method} {ep} with bad token")


class GetBandProposalTest(ReviewAppTestCase):
    def _create_approved_band(self, **kwargs) -> int:
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(**kwargs)
        prop_id = self.fetch_band_proposals()[-1]["id"]
        self.login_as(approver_id)
        self.client.post(f"/band-proposals/{prop_id}/approve")
        return prop_id

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_fetch_approved_band_proposal_content(self, _mock_probe):
        prop_id = self._create_approved_band(
            name="Дебютная Группа",
            founding_date="1995",
            genre="Hard Rock",
            description="Даугавпилсский хард-рок 90-х",
            photo_tuple=("photo.jpg", JPEG_BYTES),
            cover_tuple=("cover.jpg", JPEG_BYTES),
            track_tuples=[("01-intro.mp3", MP3_BYTES)],
            release_name="Первый Релиз",
            release_date_published="1996",
            release_genre="Hard Rock",
        )

        resp = self.client.get(f"/api/band-proposals/{prop_id}", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertEqual(data["id"], prop_id)
        self.assertEqual(data["name"], "Дебютная Группа")
        self.assertEqual(data["band_slug"], "debiutnaia-gruppa")
        self.assertEqual(data["founding_date"], "1995")
        self.assertEqual(data["genre"], ["Hard Rock"])
        self.assertTrue(data["has_photo"])
        self.assertTrue(data["has_release"])
        self.assertEqual(data["release_name"], "Первый Релиз")
        self.assertEqual(data["release_slug"], "1996-pervyi-reliz")
        self.assertTrue(data["has_release_cover"])
        self.assertEqual(len(data["tracks"]), 1)
        self.assertEqual(data["tracks"][0]["position"], 1)

        # Confirm status transitioned to 'publishing'
        prop = self.fetch_band_proposals()[0]
        self.assertEqual(prop["status"], "publishing")

    def test_pending_or_rejected_cannot_be_fetched(self):
        self.submit_band(name="Ожидающая Группа")
        prop_id = self.fetch_band_proposals()[0]["id"]

        resp = self.client.get(f"/api/band-proposals/{prop_id}", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 409)

        approver_id = self.seed_approver("curator@example.com")
        self.login_as(approver_id)
        self.client.post(f"/band-proposals/{prop_id}/reject")

        resp2 = self.client.get(f"/api/band-proposals/{prop_id}", headers=self.callback_headers())
        self.assertEqual(resp2.status_code, 409)


class BandFileServingApiTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_download_photo_cover_and_audio_track(self, _mock_probe):
        self.submit_band(
            name="Файловая Группа",
            photo_tuple=("photo.jpg", JPEG_BYTES),
            cover_tuple=("cover.jpg", JPEG_BYTES),
            track_tuples=[("01-track.mp3", MP3_BYTES)],
        )
        prop_id = self.fetch_band_proposals()[0]["id"]

        # Photo
        photo_resp = self.client.get(
            f"/api/band-proposals/{prop_id}/photo", headers=self.callback_headers()
        )
        self.assertEqual(photo_resp.status_code, 200)
        self.assertEqual(photo_resp.data, JPEG_BYTES)

        # Cover
        cover_resp = self.client.get(
            f"/api/band-proposals/{prop_id}/cover", headers=self.callback_headers()
        )
        self.assertEqual(cover_resp.status_code, 200)
        self.assertEqual(cover_resp.data, JPEG_BYTES)

        # Track audio
        track_resp = self.client.get(
            f"/api/band-proposals/{prop_id}/tracks/1/file", headers=self.callback_headers()
        )
        self.assertEqual(track_resp.status_code, 200)
        self.assertEqual(track_resp.data, MP3_BYTES)


class ListApprovedBandProposalsTest(ReviewAppTestCase):
    def test_returns_at_most_one_id_when_unthrottled(self):
        # Create two approved proposals
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(name="Первая Группа")
        self.submit_band(name="Вторая Группа")

        conn = sqlite3.connect(self.database_path)
        conn.execute("UPDATE band_proposals SET status = 'approved'")
        conn.commit()
        conn.close()

        resp = self.client.get("/api/band-proposals/approved", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 200)
        ids = resp.get_json()
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0], 1)

    def test_returns_empty_list_when_throttled(self):
        # Record a band published 5 hours ago (<24h)
        conn = sqlite3.connect(self.database_path)
        conn.execute(
            """
            INSERT INTO band_proposals (name, band_slug, status, published_at, submitter_ip)
            VALUES ('Опубликованная', 'opublikovannaya', 'published', datetime('now', '-5 hours'), '127.0.0.1')
            """
        )
        # Add an approved proposal waiting
        conn.execute(
            """
            INSERT INTO band_proposals (name, band_slug, status, submitter_ip)
            VALUES ('Ожидающая', 'ozhidayushchaya', 'approved', '127.0.0.1')
            """
        )
        conn.commit()
        conn.close()

        resp = self.client.get("/api/band-proposals/approved", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_returns_queued_proposal_once_24_hours_elapse(self):
        # Record a band published 25 hours ago (>24h)
        conn = sqlite3.connect(self.database_path)
        conn.execute(
            """
            INSERT INTO band_proposals (name, band_slug, status, published_at, submitter_ip)
            VALUES ('Старая', 'staraya', 'published', datetime('now', '-25 hours'), '127.0.0.1')
            """
        )
        # Add an approved proposal waiting
        cur = conn.execute(
            """
            INSERT INTO band_proposals (name, band_slug, status, submitter_ip)
            VALUES ('Ожидающая', 'ozhidayushchaya', 'approved', '127.0.0.1')
            """
        )
        queued_id = cur.lastrowid
        conn.commit()
        conn.close()

        resp = self.client.get("/api/band-proposals/approved", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [queued_id])


class RecordBandPublishResultTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_success_result_marks_published_and_cleans_up_files(self, _mock_probe):
        self.submit_band(
            name="Успешная Группа",
            photo_tuple=("photo.jpg", JPEG_BYTES),
            cover_tuple=("cover.jpg", JPEG_BYTES),
            track_tuples=[("01-test.mp3", MP3_BYTES)],
        )
        prop = self.fetch_band_proposals()[0]
        prop_id = prop["id"]

        photo_path = Path(self.config.resolved_media_uploads_path()) / prop["band_photo_stored_filename"]
        cover_path = Path(self.config.resolved_media_uploads_path()) / prop["release_cover_stored_filename"]
        tracks = json.loads(prop["release_tracks_json"])
        track_path = Path(self.config.resolved_media_uploads_path()) / tracks[0]["stored_filename"]

        self.assertTrue(photo_path.exists())
        self.assertTrue(cover_path.exists())
        self.assertTrue(track_path.exists())

        # Post success result
        resp = self.client.post(
            f"/api/band-proposals/{prop_id}/result",
            headers=self.callback_headers(),
            json={"success": True, "run_id": "run-12345"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "published")

        prop_after = self.fetch_band_proposals()[0]
        self.assertEqual(prop_after["status"], "published")
        self.assertIsNotNone(prop_after["published_at"])
        self.assertEqual(prop_after["github_run_id"], "run-12345")

        # Files unlinked
        self.assertFalse(photo_path.exists())
        self.assertFalse(cover_path.exists())
        self.assertFalse(track_path.exists())

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_failure_result_marks_publish_failed_and_preserves_files(self, _mock_probe):
        self.submit_band(
            name="Неудачная Группа",
            photo_tuple=("photo.jpg", JPEG_BYTES),
        )
        prop = self.fetch_band_proposals()[0]
        prop_id = prop["id"]
        photo_path = Path(self.config.resolved_media_uploads_path()) / prop["band_photo_stored_filename"]

        resp = self.client.post(
            f"/api/band-proposals/{prop_id}/result",
            headers=self.callback_headers(),
            json={"success": False, "run_id": "run-999", "error": "Network timeout"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "publish_failed")

        prop_after = self.fetch_band_proposals()[0]
        self.assertEqual(prop_after["status"], "publish_failed")
        self.assertEqual(prop_after["publish_error"], "Network timeout")
        self.assertEqual(prop_after["github_run_id"], "run-999")

        # Files preserved for retry
        self.assertTrue(photo_path.exists())


if __name__ == "__main__":
    unittest.main()
