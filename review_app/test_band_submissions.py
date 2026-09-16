#!/usr/bin/env python3
"""
Unit and integration tests for the /submit/add-band public flow (GitHub issue #22).
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


class BandSubmissionFormTest(ReviewAppTestCase):
    def test_get_form_returns_200(self):
        resp = self.client.get("/submit/add-band")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Предложить новую группу", resp.get_data(as_text=True))

    def test_get_form_in_english(self):
        resp = self.client.get("/submit/add-band?lang=en")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Propose a new band", resp.get_data(as_text=True))

    def test_form_contains_upload_progress_and_dropzone_elements(self):
        resp = self.client.get("/submit/add-band")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("data-band-dropzone", html)
        self.assertIn("data-band-progress", html)
        self.assertIn("data-band-progress-bar", html)
        self.assertIn("data-band-upload-error", html)
        self.assertIn("data-draft-banner", html)
        self.assertIn("data-draft-clear", html)
        self.assertIn("draft_recovery.js", html)
        self.assertIn("noscript-band-tracks", html)
        self.assertIn("или перетащите аудиофайлы сюда", html)
        self.assertIn('data-msg-uploading="Загрузка...', html)
        self.assertIn('data-msg-draft-restored="Восстановлен', html)

    def test_form_contains_english_i18n_data_attributes(self):
        resp = self.client.get("/submit/add-band?lang=en")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("or drag and drop audio files here", html)
        self.assertIn('data-msg-uploading="Uploading...', html)
        self.assertIn('data-msg-preview="Preview track"', html)
        self.assertIn('data-msg-draft-restored="Restored', html)


class BandSubmissionPostTest(ReviewAppTestCase):
    def test_valid_band_only_submission_creates_pending_proposal(self):
        resp = self.submit_band(
            name="Северный Ветер",
            founding_date="1995",
            genre="Punk Rock",
            description="Легендарная даугавпилсская панк-группа.",
            submitter_name="Иван",
            submitter_contact="ivan@example.com",
            photo_tuple=("photo.jpg", JPEG_BYTES),
        )
        self.assertEqual(resp.status_code, 201)

        proposals = self.fetch_band_proposals()
        self.assertEqual(len(proposals), 1)
        prop = proposals[0]
        self.assertEqual(prop["status"], "pending")
        self.assertEqual(prop["name"], "Северный Ветер")
        self.assertEqual(prop["band_slug"], "severnyi-veter")
        self.assertEqual(prop["founding_date"], "1995")
        self.assertEqual(json.loads(prop["genre"]), ["Punk Rock"])
        self.assertEqual(prop["description"], "Легендарная даугавпилсская панк-группа.")
        self.assertEqual(prop["has_release"], 0)
        self.assertIsNotNone(prop["band_photo_stored_filename"])

        uploads_dir = self.config.resolved_media_uploads_path()
        self.assertTrue((uploads_dir / prop["band_photo_stored_filename"]).exists())

        self.mock_send_notification.assert_called_once()
        _smtp_config, recipients, summary = self.mock_send_notification.call_args.args
        self.assertEqual(recipients, ["maintainer@example.com"])
        self.assertIn("Северный Ветер", summary)
        self.assertIn("severnyi-veter", summary)
        self.assertIn("Иван", summary)

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_valid_band_with_first_release_creates_pending_proposal(self, _mock_probe):
        resp = self.submit_band(
            name="Новая Волна",
            photo_tuple=("band.jpg", JPEG_BYTES),
            cover_tuple=("cover.jpg", JPEG_BYTES),
            track_tuples=[("01-first.mp3", MP3_BYTES), ("02-second.mp3", MP3_BYTES)],
            release_name="Первые Шаги",
            release_date_published="1996",
            release_genre="New Wave, Rock",
            release_description="Дебютный магнитоальбом",
        )
        self.assertEqual(resp.status_code, 201)

        proposals = self.fetch_band_proposals()
        self.assertEqual(len(proposals), 1)
        prop = proposals[0]
        self.assertEqual(prop["status"], "pending")
        self.assertEqual(prop["name"], "Новая Волна")
        self.assertEqual(prop["band_slug"], "novaia-volna")
        self.assertEqual(prop["has_release"], 1)
        self.assertEqual(prop["release_name"], "Первые Шаги")
        self.assertEqual(prop["release_slug"], "1996-pervye-shagi")
        self.assertEqual(json.loads(prop["release_genre"]), ["New Wave", "Rock"])
        self.assertIsNotNone(prop["band_photo_stored_filename"])
        self.assertIsNotNone(prop["release_cover_stored_filename"])

        tracks = json.loads(prop["release_tracks_json"])
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["position"], 1)
        self.assertEqual(tracks[1]["position"], 2)

        uploads_dir = self.config.resolved_media_uploads_path()
        self.assertTrue((uploads_dir / prop["band_photo_stored_filename"]).exists())
        self.assertTrue((uploads_dir / prop["release_cover_stored_filename"]).exists())
        self.assertTrue((uploads_dir / tracks[0]["stored_filename"]).exists())
        self.assertTrue((uploads_dir / tracks[1]["stored_filename"]).exists())

    def test_honeypot_silently_accepted(self):
        resp = self.submit_band(name="Spam Band", website="spam.com")
        self.assertEqual(resp.status_code, 201)
        proposals = self.fetch_band_proposals()
        self.assertEqual(len(proposals), 0)

    def test_missing_band_name_aborts_400(self):
        resp = self.submit_band(name="")
        self.assertEqual(resp.status_code, 400)

    def test_existing_band_slug_in_archive_rejected_400(self):
        # self.fx.band_slug exists in checkout
        resp = self.submit_band(name=self.fx.band_slug)
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_pending_band_slug_rejected_400(self):
        resp1 = self.submit_band(name="Уникальная Группа")
        self.assertEqual(resp1.status_code, 201)

        resp2 = self.submit_band(name="Уникальная Группа")
        self.assertEqual(resp2.status_code, 400)

    def test_rate_limiting_per_ip(self):
        for _ in range(5):
            resp = self.submit_band(name=f"Band {len(self.fetch_band_proposals())}")
            self.assertEqual(resp.status_code, 201)

        # 6th submission from same IP triggers 429
        resp = self.submit_band(name="Excess Band")
        self.assertEqual(resp.status_code, 429)

    def test_mail_failure_does_not_break_submission(self):
        self.mock_send_notification.side_effect = OSError("SMTP connect timeout")
        resp = self.submit_band(name="Mail Fail Band")
        self.assertEqual(resp.status_code, 201)
        proposals = self.fetch_band_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["name"], "Mail Fail Band")

    @patch("ai_agent.dispatch_band_evaluation")
    def test_ai_enabled_dispatches_ai_evaluation_without_curator_email(self, mock_dispatch):
        from config import AiConfig
        self.app.config["AI_CONFIG"] = AiConfig(mode="active")
        resp = self.submit_band(name="AI Handled Band")
        self.assertEqual(resp.status_code, 201)
        mock_dispatch.assert_called_once()
        self.mock_send_notification.assert_not_called()


if __name__ == "__main__":
    unittest.main()
