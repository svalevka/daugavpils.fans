#!/usr/bin/env python3
"""
The YouTube-link path of the /submit-media flow (see GitHub issue #49):
paste a YouTube URL instead of uploading a file. Covers URL validation,
the required rights-attestation checkbox, submission-time dedupe, the
background fetch (mocked yt-dlp) landing the video in the ordinary
pending-review pipeline, fetch-failure notifications and cleanup, and the
YouTube-only publishing throttle (3 per rolling 24 hours).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard  # noqa: E402
import youtube_fetch  # noqa: E402
from test_support import ReviewAppTestCase  # noqa: E402

VALID_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

FAKE_INFO = {
    "title": "Live at Grifs Club, 1999",
    "uploader": "Some Channel",
    "duration": 245,
    "is_live": False,
    "formats": [
        {
            "format_id": "18",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "filesize": 10 * 1024 * 1024,
        },
        {
            "format_id": "22",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "filesize": 900 * 1024 * 1024,  # far over the test cap
        },
        {
            # video-only, must never be picked (issue #49: no merging/transcoding)
            "format_id": "137",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "none",
            "filesize": 5 * 1024 * 1024,
        },
    ],
}


class _FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL: extract_info returns a canned info
    dict, download() writes a small real file at the resolved outtmpl path
    so _download()'s glob() finds something, exactly like the real thing
    would after actually fetching a video."""

    info = FAKE_INFO
    fail_download = False
    captured_opts: list = []

    def __init__(self, opts):
        self.opts = opts
        type(self).captured_opts.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return type(self).info

    def download(self, urls):
        if type(self).fail_download:
            raise RuntimeError("video unavailable")
        path = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128)


class YoutubeMediaSubmissionTestCase(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["YOUTUBE_SYNC_FETCH"] = True
        self.mock_fetch_failed_submitter = self._patch(
            "youtube_fetch.mail.send_youtube_fetch_failed_notification"
        )
        self.mock_fetch_failed_admin = self._patch(
            "youtube_fetch.mail.send_youtube_fetch_failed_admin_notification"
        )
        self._patch_ydl = mock.patch("youtube_fetch.yt_dlp.YoutubeDL", _FakeYoutubeDL)
        self._patch_ydl.start()
        self.addCleanup(self._patch_ydl.stop)
        _FakeYoutubeDL.info = dict(FAKE_INFO)
        _FakeYoutubeDL.fail_download = False
        _FakeYoutubeDL.captured_opts = []

    def submit_youtube(self, **form):
        base = {
            "band_slug": self.fx.band_slug,
            "release_slug": "",
            "youtube_url": VALID_URL,
            "rights_attested": "1",
            "submitter_name": "",
            "submitter_contact": "",
            "website": "",
        }
        base.update(form)
        return self.client.post("/submit-media", data=base)


class ValidationTest(YoutubeMediaSubmissionTestCase):
    def test_non_youtube_url_is_rejected(self):
        response = self.submit_youtube(youtube_url="https://vimeo.com/12345")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fetch_media_proposals(), [])

    def test_missing_rights_checkbox_is_rejected(self):
        response = self.submit_youtube(rights_attested="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fetch_media_proposals(), [])

    def test_shorts_and_short_link_urls_are_recognized(self):
        for url in (
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        ):
            self.assertTrue(youtube_fetch.is_youtube_url(url), url)
        self.assertFalse(youtube_fetch.is_youtube_url("https://example.com/watch?v=x"))
        self.assertFalse(youtube_fetch.is_youtube_url(""))


class HappyPathTest(YoutubeMediaSubmissionTestCase):
    def test_fetched_video_lands_as_a_pending_video_proposal(self):
        response = self.submit_youtube(submitter_contact="fan@example.com")
        self.assertEqual(response.status_code, 201)

        rows = self.fetch_media_proposals()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["media_type"], "video")
        self.assertEqual(row["source_type"], "youtube")
        self.assertEqual(row["source_url"], VALID_URL)
        self.assertEqual(row["youtube_title"], "Live at Grifs Club, 1999")
        self.assertEqual(row["youtube_channel"], "Some Channel")
        self.assertEqual(row["youtube_duration_seconds"], 245)
        self.assertEqual(row["content_type"], "video/mp4")
        self.assertTrue(row["rights_attested"])

        stored_path = self.config.resolved_media_uploads_path() / row["stored_filename"]
        self.assertTrue(stored_path.exists())

    def test_notifies_maintainer_when_ai_disabled(self):
        # AI_CONFIG defaults to mode='disabled' - the fetch-completion path
        # must still notify approvers, since it never runs through
        # create_media_proposal's own disabled-mode branch (issue #49).
        self.submit_youtube()
        self.mock_send_notification.assert_called_once()

    def test_picks_a_progressive_format_that_fits_the_cap_over_an_oversized_one(self):
        # format 22 is oversized and format 137 is video-only (no audio) -
        # 18 is the only valid, in-budget candidate.
        self.submit_youtube()
        row = self.fetch_media_proposals()[0]
        self.assertGreater(row["size_bytes"], 0)
        self.assertLessEqual(row["size_bytes"], self.config.max_video_upload_bytes)

    def test_live_stream_is_rejected(self):
        _FakeYoutubeDL.info = dict(FAKE_INFO, is_live=True)
        response = self.submit_youtube(submitter_contact="fan@example.com")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.fetch_media_proposals(), [])
        self.mock_fetch_failed_submitter.assert_called_once()
        self.mock_fetch_failed_admin.assert_called_once()


class CookiesConfigTest(YoutubeMediaSubmissionTestCase):
    def test_cookiefile_passed_through_when_configured(self):
        self.app.config["YOUTUBE_COOKIES_PATH"] = "/data/youtube-cookies.txt"
        self.submit_youtube()
        self.assertTrue(_FakeYoutubeDL.captured_opts)
        for opts in _FakeYoutubeDL.captured_opts:
            self.assertEqual(opts.get("cookiefile"), "/data/youtube-cookies.txt")

    def test_no_cookiefile_key_when_not_configured(self):
        self.app.config["YOUTUBE_COOKIES_PATH"] = None
        self.submit_youtube()
        self.assertTrue(_FakeYoutubeDL.captured_opts)
        for opts in _FakeYoutubeDL.captured_opts:
            self.assertNotIn("cookiefile", opts)


class DedupeTest(YoutubeMediaSubmissionTestCase):
    def test_resubmitting_the_same_url_is_rejected_without_a_second_fetch(self):
        first = self.submit_youtube()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(len(self.fetch_media_proposals()), 1)

        second = self.submit_youtube()
        self.assertEqual(second.status_code, 201)
        self.assertEqual(len(self.fetch_media_proposals()), 1)
        self.assertIn(b"already submitted", second.data)

    def test_a_rejected_submission_can_be_resubmitted(self):
        self.submit_youtube()
        row = self.fetch_media_proposals()[0]
        conn = __import__("sqlite3").connect(self.database_path)
        conn.execute("UPDATE media_proposals SET status = 'rejected' WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()

        response = self.submit_youtube()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.fetch_media_proposals()), 2)


class FetchFailureTest(YoutubeMediaSubmissionTestCase):
    def test_download_failure_deletes_the_proposal_and_notifies_both_sides(self):
        _FakeYoutubeDL.fail_download = True
        response = self.submit_youtube(submitter_contact="fan@example.com")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.fetch_media_proposals(), [])
        self.mock_fetch_failed_submitter.assert_called_once()
        self.mock_fetch_failed_admin.assert_called_once()

    def test_no_submitter_contact_still_notifies_approvers(self):
        _FakeYoutubeDL.fail_download = True
        self.submit_youtube(submitter_contact="")
        self.mock_fetch_failed_submitter.assert_not_called()
        self.mock_fetch_failed_admin.assert_called_once()

    def test_socket_timeout_configured_in_yt_dlp_opts(self):
        self.submit_youtube()
        self.assertTrue(_FakeYoutubeDL.captured_opts)
        for opts in _FakeYoutubeDL.captured_opts:
            self.assertEqual(opts.get("socket_timeout"), 15)

    def test_extract_info_timeout_cleans_up_and_notifies(self):
        with mock.patch("youtube_fetch._extract_info", side_effect=youtube_fetch.FetchError("YouTube metadata extraction timed out")):
            response = self.submit_youtube(submitter_contact="fan@example.com")
            self.assertEqual(response.status_code, 201)
            self.assertEqual(self.fetch_media_proposals(), [])
            self.mock_fetch_failed_submitter.assert_called_once()
            self.assertIn("timed out", self.mock_fetch_failed_submitter.call_args[0][3])
            self.mock_fetch_failed_admin.assert_called_once()

    def test_download_timeout_cleans_up_partial_files_and_notifies(self):
        uploads_dir = Path(self.app.config["MEDIA_UPLOADS_PATH"])
        uploads_dir.mkdir(parents=True, exist_ok=True)

        def slow_download(*args, **kwargs):
            partial_file = uploads_dir / "test_partial_token.part"
            partial_file.write_bytes(b"partial video bytes")
            raise youtube_fetch.FetchError("YouTube video download timed out")

        with mock.patch("youtube_fetch._download", side_effect=slow_download):
            response = self.submit_youtube(submitter_contact="fan@example.com")
            self.assertEqual(response.status_code, 201)
            self.assertEqual(self.fetch_media_proposals(), [])
            self.mock_fetch_failed_submitter.assert_called_once()
            self.assertIn("timed out", self.mock_fetch_failed_submitter.call_args[0][3])
            self.mock_fetch_failed_admin.assert_called_once()

    def test_download_function_timeout_deletes_partial_token_file(self):
        uploads_dir = Path(self.app.config["MEDIA_UPLOADS_PATH"])
        uploads_dir.mkdir(parents=True, exist_ok=True)

        import time

        def hang_download(self_ydl, urls):
            token = Path(self_ydl.opts["outtmpl"]).stem
            partial = uploads_dir / f"{token}.part"
            partial.write_bytes(b"some partial bytes")
            time.sleep(0.5)

        with mock.patch.object(_FakeYoutubeDL, "download", hang_download):
            with self.assertRaises(youtube_fetch.FetchError) as ctx:
                youtube_fetch._download(
                    VALID_URL, "18", uploads_dir, 50 * 1024 * 1024, None, timeout_seconds=0.05
                )
            self.assertIn("timed out", str(ctx.exception))
            # Verify partial files were cleaned up
            self.assertEqual(list(uploads_dir.glob("*.part")), [])


class ThrottleTest(YoutubeMediaSubmissionTestCase):
    def _seed_published_youtube_video(self, hours_ago: float = 1.0):
        import sqlite3

        conn = sqlite3.connect(self.database_path)
        conn.execute(
            """
            INSERT INTO media_proposals (
                band_slug, media_type, original_filename, stored_filename, content_type,
                size_bytes, submitter_ip, status, source_type, source_url, published_at
            ) VALUES (?, 'video', 'x', 'x', 'video/mp4', 1, '127.0.0.1', 'published', 'youtube', ?,
                      datetime('now', ?))
            """,
            (self.fx.band_slug, VALID_URL + f"&seed={hours_ago}", f"-{hours_ago} hours"),
        )
        conn.commit()
        conn.close()

    def _connect(self):
        import sqlite3

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_not_throttled_below_three_in_24h(self):
        self._seed_published_youtube_video(1)
        self._seed_published_youtube_video(2)
        is_throttled, count, _ = dashboard.is_youtube_video_publishing_throttled(self._connect())
        self.assertFalse(is_throttled)
        self.assertEqual(count, 2)

    def test_throttled_at_three_in_24h(self):
        for h in (1, 2, 3):
            self._seed_published_youtube_video(h)
        is_throttled, count, _ = dashboard.is_youtube_video_publishing_throttled(self._connect())
        self.assertTrue(is_throttled)
        self.assertEqual(count, 3)

    def test_older_than_24h_does_not_count(self):
        for h in (1, 2, 25):
            self._seed_published_youtube_video(h)
        is_throttled, count, _ = dashboard.is_youtube_video_publishing_throttled(self._connect())
        self.assertFalse(is_throttled)
        self.assertEqual(count, 2)

    def test_non_youtube_published_media_does_not_count(self):
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO media_proposals (
                band_slug, media_type, original_filename, stored_filename, content_type,
                size_bytes, submitter_ip, status, source_type, published_at
            ) VALUES (?, 'video', 'x.mp4', 'x', 'video/mp4', 1, '127.0.0.1', 'published', 'upload',
                      datetime('now', '-1 hours'))
            """,
            (self.fx.band_slug,),
        )
        conn.commit()
        for h in (2, 3):
            self._seed_published_youtube_video(h)
        is_throttled, count, _ = dashboard.is_youtube_video_publishing_throttled(conn)
        self.assertFalse(is_throttled)
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
