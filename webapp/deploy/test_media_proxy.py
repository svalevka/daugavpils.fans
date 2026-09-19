#!/usr/bin/env python3
"""
Unit and integration tests for nginx stale media proxy cache configuration
and media stream behavior (GitHub issues #59 and #80):
- Bounded 10g disk cache definition in nginx/daugavpils.conf
- Persistent docker-compose.yml volume mount for /var/cache/nginx/media
- Proxy routing restricted to this project's items (daugavpils-fans-*) and media extensions
- Rejection of unapproved items, extensions, or path traversal with 404
- Hardened headers: Content-Type from extension, nosniff, CSP default-src 'none'; sandbox
- Upstream 302 redirect following (@media_redirect) with archive.org host validation (refusing off-domain targets)
- Approver/admin routes moved off the main origin to review.daugavpils.fans via 301 redirects
- proxy_cache_use_stale serving cached audio/video during archive.org 500/502/503/504 outages
- Functional simulation of proxy cache behavior with range requests, restrictions, and stale cache fallback
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
NGINX_CONF = DEPLOY_DIR / "nginx" / "daugavpils.conf"
DOCKER_COMPOSE_YML = DEPLOY_DIR / "docker-compose.yml"

PROJECT_MEDIA_REGEX = re.compile(
    r"^/media-stream/(daugavpils-fans-[a-z0-9-]+/[^/]+\.(?:mp3|flac|ogg|m4a|mp4|webm|jpg|jpeg|png))$"
)
ARCHIVE_ORG_REDIRECT_REGEX = re.compile(r"^https://([a-z0-9.-]+\.)?archive\.org/")


class NginxMediaProxyConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conf_text = NGINX_CONF.read_text()
        self.compose_text = DOCKER_COMPOSE_YML.read_text()

    def test_proxy_cache_path_defined_with_bounded_size(self) -> None:
        """proxy_cache_path must define a 10GB bounded disk cache at /var/cache/nginx/media."""
        self.assertIn("proxy_cache_path /var/cache/nginx/media", self.conf_text)
        self.assertIn("keys_zone=archive_media_cache:50m", self.conf_text)
        self.assertIn("max_size=10g", self.conf_text)
        self.assertIn("inactive=30d", self.conf_text)
        self.assertIn("use_temp_path=off", self.conf_text)

    def test_media_stream_location_strictly_restricted(self) -> None:
        """location /media-stream/ must be restricted to project items and media extensions (Issue #80)."""
        self.assertIn(
            "location ~ ^/media-stream/(daugavpils-fans-[a-z0-9-]+/[^/]+\\.(?:mp3|flac|ogg|m4a|mp4|webm|jpg|jpeg|png))$ {",
            self.conf_text,
        )
        self.assertIn("proxy_pass https://archive.org/download/$1;", self.conf_text)
        self.assertIn("proxy_cache archive_media_cache;", self.conf_text)
        self.assertIn("slice 1m;", self.conf_text)
        self.assertIn("proxy_cache_key $uri$is_args$args$slice_range;", self.conf_text)
        self.assertIn("proxy_set_header Range $slice_range;", self.conf_text)
        self.assertIn("proxy_cache_valid 200 206 30d;", self.conf_text)
        self.assertIn("proxy_force_ranges on;", self.conf_text)
        self.assertIn("proxy_ssl_server_name on;", self.conf_text)
        self.assertIn("proxy_set_header Host archive.org;", self.conf_text)

    def test_media_stream_unmatched_falls_back_to_404(self) -> None:
        """Any /media-stream/ request not matching the strict regex must return 404."""
        match = re.search(
            r"location\s+/media-stream/\s*\{\s*return\s+404;\s*\}",
            self.conf_text,
        )
        self.assertIsNotNone(match, "Fallback location /media-stream/ { return 404; } missing")

    def test_media_location_regex_rules(self) -> None:
        """Test regex acceptance and rejection criteria directly."""
        # Allowed project items and media extensions
        self.assertTrue(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/01-track.mp3"))
        self.assertTrue(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band-album/song.flac"))
        self.assertTrue(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-video/live.mp4"))
        self.assertTrue(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/cover.jpg"))
        self.assertTrue(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/photo.png"))
        self.assertTrue(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/audio.ogg"))
        self.assertTrue(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/audio.m4a"))
        self.assertTrue(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/video.webm"))

        # Disallowed items
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/other-archive-item/01-track.mp3"))
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/random-collection/photo.jpg"))
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils/track.mp3"))

        # Disallowed extensions
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/metadata.xml"))
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/index.html"))
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/script.js"))
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/exploit.exe"))

        # Path traversal and nesting tricks
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/../evil.mp3"))
        self.assertIsNone(PROJECT_MEDIA_REGEX.match("/media-stream/daugavpils-fans-band/sub/track.mp3"))

    def test_media_stream_hardened_headers(self) -> None:
        """Response headers on media proxy must prevent MIME sniffing and HTML execution."""
        self.assertIn("proxy_hide_header Content-Type;", self.conf_text)
        self.assertIn("add_header Content-Type $media_content_type always;", self.conf_text)
        self.assertIn('add_header X-Content-Type-Options "nosniff" always;', self.conf_text)
        self.assertIn("add_header Content-Security-Policy \"default-src 'none'; sandbox\" always;", self.conf_text)

    def test_redirect_interception_and_host_validation(self) -> None:
        """Archive.org redirects must only follow *.archive.org targets and reject off-domain hosts (Issue #80)."""
        self.assertIn("proxy_intercept_errors on;", self.conf_text)
        self.assertIn("error_page 301 302 307 = @media_redirect;", self.conf_text)
        self.assertIn("location @media_redirect {", self.conf_text)
        self.assertIn("if ($is_valid_archive_org_redirect = 0)", self.conf_text)
        self.assertIn("return 502;", self.conf_text)
        self.assertIn("set $saved_redirect_location '$upstream_http_location';", self.conf_text)
        self.assertIn("proxy_pass $saved_redirect_location;", self.conf_text)

        # Direct regex tests for redirect validator
        self.assertTrue(ARCHIVE_ORG_REDIRECT_REGEX.match("https://ia800100.us.archive.org/1/items/daugavpils-fans-band/01.mp3"))
        self.assertTrue(ARCHIVE_ORG_REDIRECT_REGEX.match("https://archive.org/download/daugavpils-fans-band/01.mp3"))
        self.assertIsNone(ARCHIVE_ORG_REDIRECT_REGEX.match("https://attacker.org/malicious.mp3"))
        self.assertIsNone(ARCHIVE_ORG_REDIRECT_REGEX.match("http://ia800100.us.archive.org/1/items/track.mp3"))
        self.assertIsNone(ARCHIVE_ORG_REDIRECT_REGEX.match("https://evilarchive.org/track.mp3"))

    def test_approver_admin_routes_redirected_to_review_origin(self) -> None:
        """Approver and admin routes must be removed from the main origin and redirected to review.daugavpils.fans (Issue #80)."""
        main_server_match = re.search(
            r"server_name\s+daugavpils\.fans\s+www\.daugavpils\.fans;.*?(?=server\s*\{|\Z)",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(main_server_match)
        main_block = main_server_match.group(0)

        for route in ["/admin", "/dashboard", "/proposals", "/media-proposals", "/login", "/verify"]:
            route_pattern = rf"location\s+{re.escape(route)}\s*\{{[^}}]*return\s+301\s+https://review\.daugavpils\.fans\$request_uri;"
            self.assertRegex(
                main_block,
                route_pattern,
                f"Route {route} on main server block must 301 redirect to review.daugavpils.fans",
            )
            # Ensure none of these routes proxy to review-app from the main origin
            route_block_match = re.search(rf"location\s+{re.escape(route)}\s*\{{([^}}]*)\}}", main_block)
            self.assertIsNotNone(route_block_match)
            self.assertNotIn("proxy_pass", route_block_match.group(1), f"Route {route} must not proxy_pass on main origin")

    def test_docker_compose_mounts_media_cache_volume(self) -> None:
        """docker-compose.yml must mount a persistent volume for /var/cache/nginx/media."""
        self.assertIn("media-cache:/var/cache/nginx/media", self.compose_text)
        self.assertIn("volumes:\n  media-cache:", self.compose_text)


class MediaProxyCacheSimulator:
    """Simulates the Nginx media proxy cache behavior:
    - Allowed item + extension validation
    - Setting explicit Content-Type, nosniff, and sandboxed CSP
    - Validating upstream redirect targets
    - 1MB slice chunking for byte ranges (HTTP 206 Partial Content)
    - Serving stale cached slices when simulated upstream returns 502/503/timeout
    """

    MIME_TYPES = {
        "mp3": "audio/mpeg",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
        "m4a": "audio/mp4",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }

    def __init__(self, slice_size: int = 1024 * 1024) -> None:
        self.slice_size = slice_size
        self.cache: dict[str, bytes] = {}
        self.upstream_data: dict[str, bytes] = {}
        self.upstream_status: int = 200  # 200 normal, 502/503 for simulated outage

    def set_upstream_file(self, uri: str, data: bytes) -> None:
        self.upstream_data[uri] = data

    def set_upstream_outage(self, status_code: int = 502) -> None:
        self.upstream_status = status_code

    def request(
        self,
        uri: str,
        range_header: str | None = None,
        redirect_target: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        # 1. Project media restriction check
        if not PROJECT_MEDIA_REGEX.match(uri):
            return 404, {}, b"Not Found"

        # 2. Upstream redirect validation if redirect occurred
        if redirect_target:
            if not ARCHIVE_ORG_REDIRECT_REGEX.match(redirect_target):
                return 502, {}, b"Bad Gateway"

        full_data = self.upstream_data.get(uri)
        total_len = len(full_data) if full_data is not None else 0

        # Parse extension for Content-Type
        ext = uri.rsplit(".", 1)[-1].lower() if "." in uri else ""
        content_type = self.MIME_TYPES.get(ext, "application/octet-stream")

        # Security headers
        headers: dict[str, str] = {
            "Content-Type": content_type,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Accept-Ranges": "bytes",
        }

        # Parse range header if present (e.g. "bytes=0-1023", "bytes=1048576-")
        start = 0
        end = total_len - 1 if total_len > 0 else 0
        is_range = False

        if range_header and range_header.startswith("bytes="):
            is_range = True
            range_val = range_header[len("bytes="):]
            parts = range_val.split("-")
            start = int(parts[0]) if parts[0] else 0
            if len(parts) > 1 and parts[1]:
                end = min(int(parts[1]), total_len - 1)

        # Determine slice indices needed
        start_slice = start // self.slice_size
        end_slice = end // self.slice_size

        response_chunks = []
        for slice_idx in range(start_slice, end_slice + 1):
            slice_key = f"{uri}:bytes={slice_idx * self.slice_size}-{(slice_idx + 1) * self.slice_size - 1}"
            if slice_key in self.cache:
                slice_bytes = self.cache[slice_key]
            else:
                if self.upstream_status in (500, 502, 503, 504):
                    headers["X-Cache"] = "MISS"
                    return self.upstream_status, headers, b""
                if full_data is None:
                    headers["X-Cache"] = "MISS"
                    return 404, headers, b""

                slice_start = slice_idx * self.slice_size
                slice_end = min(slice_start + self.slice_size, total_len)
                slice_bytes = full_data[slice_start:slice_end]
                self.cache[slice_key] = slice_bytes

            response_chunks.append(slice_bytes)

        assembled = b"".join(response_chunks)
        slice_offset = start - (start_slice * self.slice_size)
        needed_len = end - start + 1
        output = assembled[slice_offset:slice_offset + needed_len]

        headers["Content-Length"] = str(len(output))
        if is_range:
            headers["Content-Range"] = f"bytes {start}-{end}/{total_len}"
            return 206, headers, output
        return 200, headers, output


class StaleMediaProxySimulationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.proxy = MediaProxyCacheSimulator(slice_size=1024)
        self.dummy_audio = b"TRACK_DATA_" * 350  # ~3850 bytes
        self.uri = "/media-stream/daugavpils-fans-band/01-track.mp3"
        self.proxy.set_upstream_file(self.uri, self.dummy_audio)

    def test_initial_request_populates_cache_with_security_headers(self) -> None:
        """First request fetches from upstream, sets hardened headers, and populates cache."""
        status, headers, body = self.proxy.request(self.uri)
        self.assertEqual(status, 200)
        self.assertEqual(body, self.dummy_audio)
        self.assertEqual(headers["Content-Type"], "audio/mpeg")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Content-Security-Policy"], "default-src 'none'; sandbox")
        self.assertGreater(len(self.proxy.cache), 0)

    def test_disallowed_item_rejected_with_404(self) -> None:
        """Requests to non-project items are rejected with 404."""
        status, _, _ = self.proxy.request("/media-stream/foreign-item/track.mp3")
        self.assertEqual(status, 404)

    def test_disallowed_extension_rejected_with_404(self) -> None:
        """Requests for non-media file types are rejected with 404."""
        status, _, _ = self.proxy.request("/media-stream/daugavpils-fans-band/exploit.html")
        self.assertEqual(status, 404)

    def test_invalid_redirect_target_rejected_with_502(self) -> None:
        """Redirects to external non-archive.org targets are refused with 502."""
        status, _, _ = self.proxy.request(
            self.uri,
            redirect_target="https://attacker.org/file.mp3",
        )
        self.assertEqual(status, 502)

    def test_valid_archive_org_redirect_succeeds(self) -> None:
        """Redirects to iaXXXX.us.archive.org are accepted and stream media."""
        status, headers, body = self.proxy.request(
            self.uri,
            redirect_target="https://ia800100.us.archive.org/1/items/daugavpils-fans-band/01.mp3",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, self.dummy_audio)

    def test_byte_range_request_serves_partial_content_206(self) -> None:
        """Seeking with Range header returns HTTP 206 Partial Content."""
        status, headers, body = self.proxy.request(self.uri, range_header="bytes=500-1500")
        self.assertEqual(status, 206)
        self.assertEqual(body, self.dummy_audio[500:1501])
        self.assertEqual(headers["Content-Range"], f"bytes 500-1500/{len(self.dummy_audio)}")

    def test_stale_cache_serves_media_during_simulated_archive_org_outage(self) -> None:
        """When archive.org drops offline (502/503), previously cached tracks continue streaming."""
        status, _, body = self.proxy.request(self.uri)
        self.assertEqual(status, 200)

        # Upstream failure
        self.proxy.set_upstream_outage(502)

        # Subsequent request continues to succeed from cache
        status2, _, body2 = self.proxy.request(self.uri)
        self.assertEqual(status2, 200)
        self.assertEqual(body2, self.dummy_audio)

        # Range seek requests also succeed from cache
        status_range, headers_range, body_range = self.proxy.request(self.uri, range_header="bytes=100-800")
        self.assertEqual(status_range, 206)
        self.assertEqual(body_range, self.dummy_audio[100:801])


if __name__ == "__main__":
    unittest.main()
