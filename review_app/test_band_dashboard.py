#!/usr/bin/env python3
"""
Unit and integration tests for band proposal dashboard management (GitHub issue #22):
- Auth verification
- Listing pending and approved band proposals
- Slug override support on approval
- Rolling 24-hour throttling enforcement
- Self-approval prevention
- Rejection and file cleanup
- File serving (photo, cover, audio)
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


class BandDashboardAuthTest(ReviewAppTestCase):
    def test_anonymous_access_blocked_for_band_actions(self):
        self.submit_band(photo_tuple=("photo.jpg", JPEG_BYTES))
        prop_id = self.fetch_band_proposals()[0]["id"]

        self.assertEqual(self.client.post(f"/band-proposals/{prop_id}/approve").status_code, 401)
        self.assertEqual(self.client.post(f"/band-proposals/{prop_id}/reject").status_code, 401)
        self.assertEqual(self.client.post(f"/band-proposals/{prop_id}/upload").status_code, 401)
        self.assertEqual(self.client.post(f"/band-proposals/{prop_id}/publish").status_code, 401)
        self.assertEqual(self.client.get(f"/band-proposals/{prop_id}/photo").status_code, 302)
        self.assertEqual(self.client.get(f"/band-proposals/{prop_id}/cover").status_code, 302)
        self.assertEqual(self.client.get(f"/band-proposals/{prop_id}/tracks/1/file").status_code, 302)


class BandDashboardListingTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_dashboard_lists_pending_and_awaiting_publish_bands(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(
            name="Алиби",
            photo_tuple=("photo.jpg", JPEG_BYTES),
            cover_tuple=("cover.jpg", JPEG_BYTES),
            track_tuples=[("01-intro.mp3", MP3_BYTES)],
            release_name="Демо 1996",
        )

        self.login_as(approver_id)
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"\xd0\x90\xd0\xbb\xd0\xb8\xd0\xb1\xd0\xb8", resp.data)  # "Алиби"
        self.assertIn(b"/band-proposals/", resp.data)

        # Approve proposal
        prop_id = self.fetch_band_proposals()[0]["id"]
        approve_resp = self.client.post(f"/band-proposals/{prop_id}/approve")
        self.assertEqual(approve_resp.status_code, 302)

        # Now appears in awaiting publish / published section
        resp2 = self.client.get("/dashboard")
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b"\xd0\x90\xd0\xbb\xd0\xb8\xd0\xb1\xd0\xb8", resp2.data)


class BandDecisionTest(ReviewAppTestCase):
    def test_approver_can_approve_unthrottled_proposal_and_dispatches_workflow(self):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(name="Группа Без Троттлинга")
        prop_id = self.fetch_band_proposals()[0]["id"]

        self.login_as(approver_id)
        resp = self.client.post(f"/band-proposals/{prop_id}/approve")
        self.assertEqual(resp.status_code, 302)

        prop = self.fetch_band_proposals()[0]
        self.assertEqual(prop["status"], "publishing")
        self.assertEqual(prop["decided_by"], approver_id)
        self.mock_trigger_band_apply.assert_called_once_with(self.config.github, prop_id)

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_approver_can_override_slugs_on_approval(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(
            name="Оригинальное Имя",
            track_tuples=[("01-track.mp3", MP3_BYTES)],
            release_name="Оригинальный Релиз",
        )
        prop_id = self.fetch_band_proposals()[0]["id"]

        self.login_as(approver_id)
        resp = self.client.post(
            f"/band-proposals/{prop_id}/approve",
            data={"band_slug": "custom-band-slug", "release_slug": "1995-custom-release"},
        )
        self.assertEqual(resp.status_code, 302)

        prop = self.fetch_band_proposals()[0]
        self.assertEqual(prop["band_slug"], "custom-band-slug")
        self.assertEqual(prop["release_slug"], "1995-custom-release")

    def test_approver_cannot_override_with_invalid_slug(self):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(name="Проверка Слага")
        prop_id = self.fetch_band_proposals()[0]["id"]

        self.login_as(approver_id)
        resp = self.client.post(
            f"/band-proposals/{prop_id}/approve",
            data={"band_slug": "INVALID SLUG!"},
        )
        self.assertEqual(resp.status_code, 400)
        prop = self.fetch_band_proposals()[0]
        self.assertEqual(prop["status"], "pending")

    def test_approver_cannot_override_with_existing_archive_slug(self):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(name="Коллизия")
        prop_id = self.fetch_band_proposals()[0]["id"]

        self.login_as(approver_id)
        resp = self.client.post(
            f"/band-proposals/{prop_id}/approve",
            data={"band_slug": self.fx.band_slug},
        )
        self.assertEqual(resp.status_code, 409)

    def test_throttling_leaves_proposal_approved_without_dispatch(self):
        # Simulate a band published 2 hours ago
        conn = sqlite3.connect(self.database_path)
        conn.execute(
            """
            INSERT INTO band_proposals (name, band_slug, status, published_at, submitter_ip)
            VALUES ('Ранее Опубликованная', 'ranee-opublikovannaya', 'published', datetime('now', '-2 hours'), '127.0.0.1')
            """
        )
        conn.commit()
        conn.close()

        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(name="Троттлинг Тест")
        prop_id = self.fetch_band_proposals()[-1]["id"]

        self.login_as(approver_id)
        resp = self.client.post(f"/band-proposals/{prop_id}/approve")
        self.assertEqual(resp.status_code, 302)

        # Because a band was published 2 hours ago (< 24h), status must be 'approved', NOT 'publishing'
        prop = self.fetch_band_proposals()[-1]
        self.assertEqual(prop["status"], "approved")
        self.mock_trigger_band_apply.assert_not_called()

    def test_self_approval_is_forbidden(self):
        approver_id = self.seed_approver("submitter@example.com")
        self.login_as(approver_id)
        self.submit_band(name="Моя Собственная Группа")
        prop_id = self.fetch_band_proposals()[0]["id"]

        resp = self.client.post(f"/band-proposals/{prop_id}/approve")
        self.assertEqual(resp.status_code, 403)

        prop = self.fetch_band_proposals()[0]
        self.assertEqual(prop["status"], "pending")

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_rejecting_unlinks_staged_files(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(
            name="Группа На Отклонение",
            photo_tuple=("photo.jpg", JPEG_BYTES),
            cover_tuple=("cover.jpg", JPEG_BYTES),
            track_tuples=[("01-track.mp3", MP3_BYTES)],
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

        self.login_as(approver_id)
        resp = self.client.post(f"/band-proposals/{prop_id}/reject")
        self.assertEqual(resp.status_code, 302)

        prop_after = self.fetch_band_proposals()[0]
        self.assertEqual(prop_after["status"], "rejected")
        self.assertFalse(photo_path.exists())
        self.assertFalse(cover_path.exists())
        self.assertFalse(track_path.exists())
        self.mock_trigger_band_apply.assert_not_called()


class BandUploadAndPublishTest(ReviewAppTestCase):
    def test_upload_button_dispatches_workflow_when_unthrottled(self):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(name="Ручная Отправка")
        prop_id = self.fetch_band_proposals()[0]["id"]

        self.login_as(approver_id)
        # Put proposal in approved state
        conn = sqlite3.connect(self.database_path)
        conn.execute("UPDATE band_proposals SET status = 'approved' WHERE id = ?", (prop_id,))
        conn.commit()
        conn.close()

        resp = self.client.post(f"/band-proposals/{prop_id}/upload")
        self.assertEqual(resp.status_code, 302)

        prop = self.fetch_band_proposals()[0]
        self.assertEqual(prop["status"], "publishing")
        self.mock_trigger_band_apply.assert_called_once_with(self.config.github, prop_id)

    def test_upload_button_returns_429_when_throttled(self):
        conn = sqlite3.connect(self.database_path)
        conn.execute(
            """
            INSERT INTO band_proposals (name, band_slug, status, published_at, submitter_ip)
            VALUES ('Недавняя', 'nedavnyaya', 'published', datetime('now', '-1 hour'), '127.0.0.1')
            """
        )
        conn.commit()
        conn.close()

        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(name="Вторая Группа")
        prop_id = self.fetch_band_proposals()[-1]["id"]

        self.login_as(approver_id)
        conn = sqlite3.connect(self.database_path)
        conn.execute("UPDATE band_proposals SET status = 'approved' WHERE id = ?", (prop_id,))
        conn.commit()
        conn.close()

        resp = self.client.post(f"/band-proposals/{prop_id}/upload")
        self.assertEqual(resp.status_code, 429)

    def test_manual_publish_marks_published_and_unlinks_files(self):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(
            name="Ручная Публикация",
            photo_tuple=("photo.jpg", JPEG_BYTES),
        )
        prop = self.fetch_band_proposals()[0]
        prop_id = prop["id"]
        photo_path = Path(self.config.resolved_media_uploads_path()) / prop["band_photo_stored_filename"]
        self.assertTrue(photo_path.exists())

        self.login_as(approver_id)
        conn = sqlite3.connect(self.database_path)
        conn.execute("UPDATE band_proposals SET status = 'approved' WHERE id = ?", (prop_id,))
        conn.commit()
        conn.close()

        resp = self.client.post(f"/band-proposals/{prop_id}/publish")
        self.assertEqual(resp.status_code, 302)

        prop_after = self.fetch_band_proposals()[0]
        self.assertEqual(prop_after["status"], "published")
        self.assertIsNotNone(prop_after["published_at"])
        self.assertFalse(photo_path.exists())


class BandFileServingTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_serving_photo_cover_and_audio_to_approver(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_band(
            name="Медиа Группа",
            photo_tuple=("band.jpg", JPEG_BYTES),
            cover_tuple=("cover.jpg", JPEG_BYTES),
            track_tuples=[("01-test.mp3", MP3_BYTES)],
        )
        prop_id = self.fetch_band_proposals()[0]["id"]

        self.login_as(approver_id)

        photo_resp = self.client.get(f"/band-proposals/{prop_id}/photo")
        self.assertEqual(photo_resp.status_code, 200)
        self.assertEqual(photo_resp.data, JPEG_BYTES)

        cover_resp = self.client.get(f"/band-proposals/{prop_id}/cover")
        self.assertEqual(cover_resp.status_code, 200)
        self.assertEqual(cover_resp.data, JPEG_BYTES)

        track_resp = self.client.get(f"/band-proposals/{prop_id}/tracks/1/file")
        self.assertEqual(track_resp.status_code, 200)
        self.assertEqual(track_resp.data, MP3_BYTES)


if __name__ == "__main__":
    unittest.main()
