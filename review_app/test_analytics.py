#!/usr/bin/env python3
"""
Tests for privacy-preserving visitor and media analytics (GitHub issue #34).
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analytics import (  # noqa: E402
    detect_country,
    detect_device,
    hash_visitor,
    is_bot,
    parse_path_slugs,
    parse_referrer,
)
from test_support import ReviewAppTestCase  # noqa: E402


class AnalyticsHelpersTest(unittest.TestCase):
    def test_is_bot(self):
        self.assertTrue(is_bot(None))
        self.assertTrue(is_bot(""))
        self.assertTrue(is_bot("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"))
        self.assertTrue(is_bot("curl/7.68.0"))
        self.assertTrue(is_bot("python-requests/2.31.0"))
        self.assertTrue(is_bot("Bytespider; spider-feedback@bytedance.com"))
        self.assertFalse(is_bot("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"))
        self.assertFalse(is_bot("Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"))

    def test_detect_device(self):
        self.assertEqual(detect_device(None), "desktop")
        self.assertEqual(detect_device("Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)"), "mobile")
        self.assertEqual(detect_device("Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X)"), "tablet")
        self.assertEqual(detect_device("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"), "desktop")

    def test_detect_country(self):
        self.assertIsNone(detect_country(None))
        self.assertIsNone(detect_country({}))
        self.assertEqual(detect_country({"CF-IPCountry": "lv"}), "LV")
        self.assertEqual(detect_country({"X-Country": "DE"}), "DE")
        self.assertEqual(detect_country({"X-GeoIP-Country": "US"}), "US")
        self.assertIsNone(detect_country({"CF-IPCountry": "INVALID"}))

    def test_parse_referrer(self):
        self.assertEqual(parse_referrer(None), "Direct")
        self.assertEqual(parse_referrer(""), "Direct")
        self.assertEqual(parse_referrer("https://daugavpils.fans/bands/dvinsk/"), "Internal")
        self.assertEqual(parse_referrer("https://www.google.com/search?q=dvinsk"), "google.com")
        self.assertEqual(parse_referrer("https://yandex.ru/"), "yandex.ru")

    def test_parse_path_slugs(self):
        self.assertEqual(parse_path_slugs("/"), (None, None))
        self.assertEqual(parse_path_slugs("/bands/dvinsk/"), ("dvinsk", None))
        self.assertEqual(parse_path_slugs("/bands/dvinsk/1993-karmannyi-mir/"), ("dvinsk", "1993-karmannyi-mir"))
        self.assertEqual(parse_path_slugs("/en/bands/dvinsk/1993-karmannyi-mir/"), ("dvinsk", "1993-karmannyi-mir"))
        self.assertEqual(parse_path_slugs("/bands/dvinsk/media/"), ("dvinsk", None))

    def test_hash_visitor_daily_salt(self):
        ip = "192.0.2.1"
        ua = "Mozilla/5.0"
        secret = "secret-key-1"
        h1 = hash_visitor(ip, ua, secret, "2026-09-12")
        h2 = hash_visitor(ip, ua, secret, "2026-09-12")
        h3 = hash_visitor(ip, ua, secret, "2026-09-13")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)


class AnalyticsApiTest(ReviewAppTestCase):
    def test_pageview_event_recorded(self):
        response = self.client.post(
            "/api/event",
            json={
                "type": "pageview",
                "path": "/bands/dvinsk/1993-karmannyi-mir/",
                "referrer": "https://google.com/search",
            },
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "CF-IPCountry": "LV"},
        )
        self.assertEqual(response.status_code, 204)

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM analytics_events").fetchall()
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "pageview")
        self.assertEqual(rows[0]["path"], "/bands/dvinsk/1993-karmannyi-mir/")
        self.assertEqual(rows[0]["band_slug"], "dvinsk")
        self.assertEqual(rows[0]["release_slug"], "1993-karmannyi-mir")
        self.assertEqual(rows[0]["referrer_domain"], "google.com")
        self.assertEqual(rows[0]["country_code"], "LV")
        self.assertEqual(rows[0]["device_type"], "desktop")

    def test_media_play_event_recorded(self):
        response = self.client.post(
            "/api/event",
            json={
                "type": "track_play",
                "path": "/bands/dvinsk/1993-karmannyi-mir/",
                "track": "01-prosnites-liudi.flac",
                "band": "dvinsk",
                "release": "1993-karmannyi-mir",
            },
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)"},
        )
        self.assertEqual(response.status_code, 204)

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM analytics_events").fetchall()
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "track_play")
        self.assertEqual(rows[0]["track_name"], "01-prosnites-liudi.flac")
        self.assertEqual(rows[0]["device_type"], "mobile")

    def test_video_play_event_recorded(self):
        response = self.client.post(
            "/api/event",
            json={
                "type": "video_play",
                "path": "/bands/khoriniye-bega/",
                "video": "Га-га-га",
                "band": "khoriniye-bega",
            },
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        self.assertEqual(response.status_code, 204)

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM analytics_events").fetchall()
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "video_play")
        self.assertEqual(rows[0]["video_name"], "Га-га-га")

    def test_bot_requests_are_ignored(self):
        response = self.client.post(
            "/api/event",
            json={"type": "pageview", "path": "/"},
            headers={"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"},
        )
        self.assertEqual(response.status_code, 204)

        conn = sqlite3.connect(self.database_path)
        rows = conn.execute("SELECT COUNT(*) FROM analytics_events").fetchone()[0]
        conn.close()
        self.assertEqual(rows, 0)

    def test_invalid_event_type_is_ignored(self):
        response = self.client.post(
            "/api/event",
            json={"type": "malicious_type", "path": "/"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        self.assertEqual(response.status_code, 204)

        conn = sqlite3.connect(self.database_path)
        rows = conn.execute("SELECT COUNT(*) FROM analytics_events").fetchone()[0]
        conn.close()
        self.assertEqual(rows, 0)


if __name__ == "__main__":
    unittest.main()
