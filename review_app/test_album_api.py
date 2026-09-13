#!/usr/bin/env python3
"""
Unit and integration tests for the authenticated album proposal callback API
used by GitHub Actions (.github/workflows/apply-album-proposal.yml)
(GitHub issue #20):
- Auth verification with callback key
- Fetching approved album metadata and track lists
- Downloading cover and audio track binary files
- Listing approved/publishing proposal IDs
- Recording success/failure publish results and file cleanup
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


class AlbumApiAuthTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_missing_or_invalid_bearer_token_is_rejected(self, _mock_probe):
        self.submit_album()
        prop_id = self.fetch_album_proposals()[0]["id"]

        endpoints = [
            ("GET", f"/api/album-proposals/{prop_id}"),
            ("GET", f"/api/album-proposals/{prop_id}/cover"),
            ("GET", f"/api/album-proposals/{prop_id}/tracks/1/file"),
            ("GET", "/api/album-proposals/approved"),
            ("POST", f"/api/album-proposals/{prop_id}/result"),
        ]
        for method, ep in endpoints:
            no_auth = self.client.open(ep, method=method)
            self.assertEqual(no_auth.status_code, 401, f"{method} {ep} without token")

            bad_auth = self.client.open(ep, method=method, headers={"Authorization": "Bearer wrong"})
            self.assertEqual(bad_auth.status_code, 401, f"{method} {ep} with bad token")


class GetAlbumProposalTest(ReviewAppTestCase):
    def _create_approved_album(self, **kwargs) -> int:
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(**kwargs)
        prop_id = self.fetch_album_proposals()[-1]["id"]
        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/approve")
        return prop_id

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_fetch_approved_album_proposal_content(self, _mock_probe):
        prop_id = self._create_approved_album(
            name="Debut LP",
            date_published="1996",
            genre="Punk Rock",
            description="Rare cassette",
            cover_tuple=("cover.jpg", JPEG_BYTES),
        )

        resp = self.client.get(f"/api/album-proposals/{prop_id}", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertEqual(data["id"], prop_id)
        self.assertEqual(data["band_slug"], self.fx.band_slug)
        self.assertEqual(data["name"], "Debut LP")
        self.assertEqual(data["date_published"], "1996")
        self.assertEqual(data["genre"], ["Punk Rock"])
        self.assertEqual(data["description"], "Rare cassette")
        self.assertTrue(data["has_cover"])
        self.assertEqual(len(data["tracks"]), 1)
        self.assertEqual(data["tracks"][0]["position"], 1)

        # Confirm status transitioned to 'publishing'
        prop = self.fetch_album_proposals()[0]
        self.assertEqual(prop["status"], "publishing")

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_pending_or_rejected_cannot_be_fetched(self, _mock_probe):
        self.submit_album(name="Pending Album")
        prop_id = self.fetch_album_proposals()[0]["id"]

        resp = self.client.get(f"/api/album-proposals/{prop_id}", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 409)

        approver_id = self.seed_approver("curator@example.com")
        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/reject")

        resp2 = self.client.get(f"/api/album-proposals/{prop_id}", headers=self.callback_headers())
        self.assertEqual(resp2.status_code, 409)

    def test_unknown_proposal_returns_404(self):
        resp = self.client.get("/api/album-proposals/99999", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 404)


class AlbumApiMediaFilesTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_download_cover_and_track_files(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(
            track_tuples=[("01-track.mp3", MP3_BYTES)],
            cover_tuple=("cover.jpg", JPEG_BYTES),
        )
        prop_id = self.fetch_album_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/approve")

        # Download cover
        cover_resp = self.client.get(f"/api/album-proposals/{prop_id}/cover", headers=self.callback_headers())
        self.assertEqual(cover_resp.status_code, 200)
        self.assertEqual(cover_resp.data, JPEG_BYTES)

        # Download track 1
        track_resp = self.client.get(f"/api/album-proposals/{prop_id}/tracks/1/file", headers=self.callback_headers())
        self.assertEqual(track_resp.status_code, 200)
        self.assertEqual(track_resp.data, MP3_BYTES)
        self.assertIn("audio/mpeg", track_resp.headers.get("Content-Type", ""))

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_download_missing_cover_or_track_returns_404(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(cover_tuple=None)
        prop_id = self.fetch_album_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/approve")

        self.assertEqual(
            self.client.get(f"/api/album-proposals/{prop_id}/cover", headers=self.callback_headers()).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/album-proposals/{prop_id}/tracks/2/file", headers=self.callback_headers()).status_code,
            404,
        )


class AlbumApiApprovedListTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_list_approved_proposals(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(name="Album 1")
        self.submit_album(name="Album 2")
        self.submit_album(name="Album 3")
        props = self.fetch_album_proposals()

        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{props[0]['id']}/approve")
        self.client.post(f"/album-proposals/{props[1]['id']}/approve")

        resp = self.client.get("/api/album-proposals/approved", headers=self.callback_headers())
        self.assertEqual(resp.status_code, 200)
        ids = resp.get_json()
        self.assertEqual(ids, [props[0]["id"], props[1]["id"]])


class AlbumApiResultRecordingTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_recording_success_unlinks_staged_files(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(cover_tuple=("cover.jpg", JPEG_BYTES))
        prop = self.fetch_album_proposals()[0]
        prop_id = prop["id"]
        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/approve")

        uploads_dir = Path(self.config.resolved_media_uploads_path())
        cover_path = uploads_dir / prop["cover_stored_filename"]
        tracks = json.loads(prop["tracks_json"])
        track_path = uploads_dir / tracks[0]["stored_filename"]
        self.assertTrue(cover_path.exists())
        self.assertTrue(track_path.exists())

        # Callback reports success
        resp = self.client.post(
            f"/api/album-proposals/{prop_id}/result",
            headers=self.callback_headers(),
            json={"success": True, "run_id": "456789"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "published")

        prop_after = self.fetch_album_proposals()[0]
        self.assertEqual(prop_after["status"], "published")
        self.assertEqual(prop_after["github_run_id"], "456789")
        self.assertIsNotNone(prop_after["published_at"])
        self.assertIsNone(prop_after["publish_error"])

        # Staged files must be cleaned up
        self.assertFalse(cover_path.exists())
        self.assertFalse(track_path.exists())

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_recording_failure_preserves_staged_files(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(cover_tuple=("cover.jpg", JPEG_BYTES))
        prop = self.fetch_album_proposals()[0]
        prop_id = prop["id"]
        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/approve")

        uploads_dir = Path(self.config.resolved_media_uploads_path())
        cover_path = uploads_dir / prop["cover_stored_filename"]
        tracks = json.loads(prop["tracks_json"])
        track_path = uploads_dir / tracks[0]["stored_filename"]

        # Callback reports failure
        resp = self.client.post(
            f"/api/album-proposals/{prop_id}/result",
            headers=self.callback_headers(),
            json={"success": False, "run_id": "456789", "error": "archive.org 503 error"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "publish_failed")

        prop_after = self.fetch_album_proposals()[0]
        self.assertEqual(prop_after["status"], "publish_failed")
        self.assertEqual(prop_after["github_run_id"], "456789")
        self.assertEqual(prop_after["publish_error"], "archive.org 503 error")

        # Staged files preserved for debugging / manual retry
        self.assertTrue(cover_path.exists())
        self.assertTrue(track_path.exists())


if __name__ == "__main__":
    unittest.main()
