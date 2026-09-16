#!/usr/bin/env python3
"""
Unit and integration tests for nginx stale media proxy cache configuration
and media stream behavior (GitHub issue #59):
- Bounded 10g disk cache definition in nginx/daugavpils.conf
- Persistent docker-compose.yml volume mount for /var/cache/nginx/media
- Proxy routing, byte-range slicing (slice 1m), HTTP 206 Partial Content support
- Upstream 302 redirect following (@media_redirect)
- proxy_cache_use_stale serving cached audio/video during archive.org 500/502/503/504 outages
- Functional simulation of proxy cache behavior with range requests and stale cache fallback
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
NGINX_CONF = DEPLOY_DIR / "nginx" / "daugavpils.conf"
DOCKER_COMPOSE_YML = DEPLOY_DIR / "docker-compose.yml"


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

    def test_media_stream_location_configured(self) -> None:
        """location /media-stream/ must proxy to archive.org/download/ with cache and range support."""
        self.assertIn("location /media-stream/ {", self.conf_text)
        self.assertIn("proxy_pass https://archive.org/download/;", self.conf_text)
        self.assertIn("proxy_cache archive_media_cache;", self.conf_text)
        self.assertIn("slice 1m;", self.conf_text)
        self.assertIn("proxy_cache_key $uri$is_args$args$slice_range;", self.conf_text)
        self.assertIn("proxy_set_header Range $slice_range;", self.conf_text)
        self.assertIn("proxy_cache_valid 200 206 30d;", self.conf_text)
        self.assertIn("proxy_force_ranges on;", self.conf_text)
        self.assertIn("proxy_ssl_server_name on;", self.conf_text)
        self.assertIn("proxy_set_header Host archive.org;", self.conf_text)

    def test_proxy_cache_use_stale_configured_for_outages(self) -> None:
        """proxy_cache_use_stale must cover error, timeout, updating, and 500, 502, 503, 504."""
        match = re.search(r"^\s*proxy_cache_use_stale\s+([^;]+);", self.conf_text, re.MULTILINE)
        self.assertIsNotNone(match, "proxy_cache_use_stale directive not found in nginx config")
        stale_flags = match.group(1).split()
        for expected in ["error", "timeout", "updating", "http_500", "http_502", "http_503", "http_504"]:
            self.assertIn(expected, stale_flags, f"Missing {expected} in proxy_cache_use_stale")

    def test_redirect_interception_configured(self) -> None:
        """Archive.org /download/ responds with 302 redirects which must be intercepted and followed."""
        self.assertIn("proxy_intercept_errors on;", self.conf_text)
        self.assertIn("error_page 301 302 307 = @media_redirect;", self.conf_text)
        self.assertIn("location @media_redirect {", self.conf_text)
        self.assertIn("set $saved_redirect_location '$upstream_http_location';", self.conf_text)
        self.assertIn("proxy_pass $saved_redirect_location;", self.conf_text)
        self.assertIn("resolver 1.1.1.1 8.8.8.8", self.conf_text)

    def test_docker_compose_mounts_media_cache_volume(self) -> None:
        """docker-compose.yml must mount a persistent volume for /var/cache/nginx/media."""
        self.assertIn("media-cache:/var/cache/nginx/media", self.compose_text)
        self.assertIn("volumes:\n  media-cache:", self.compose_text)


class MediaProxyCacheSimulator:
    """Simulates the Nginx media proxy cache behavior:
    - 1MB slice chunking for byte ranges (HTTP 206 Partial Content)
    - Storing 200/206 responses in cache
    - Serving stale cached slices when simulated upstream returns 502/503/timeout
    """

    def __init__(self, slice_size: int = 1024 * 1024) -> None:
        self.slice_size = slice_size
        self.cache: dict[str, bytes] = {}
        self.upstream_data: dict[str, bytes] = {}
        self.upstream_status: int = 200  # 200 normal, 502/503 for simulated outage

    def set_upstream_file(self, uri: str, data: bytes) -> None:
        self.upstream_data[uri] = data

    def set_upstream_outage(self, status_code: int = 502) -> None:
        self.upstream_status = status_code

    def request(self, uri: str, range_header: str | None = None) -> tuple[int, dict[str, str], bytes]:
        full_data = self.upstream_data.get(uri)
        total_len = len(full_data) if full_data is not None else 0

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
                # Cache hit or stale cache hit
                slice_bytes = self.cache[slice_key]
            else:
                # Cache miss: query upstream
                if self.upstream_status in (500, 502, 503, 504):
                    # Upstream failure and no cached slice -> 502 Bad Gateway
                    return self.upstream_status, {"X-Cache": "MISS"}, b""
                if full_data is None:
                    return 404, {"X-Cache": "MISS"}, b""

                slice_start = slice_idx * self.slice_size
                slice_end = min(slice_start + self.slice_size, total_len)
                slice_bytes = full_data[slice_start:slice_end]
                # Store in cache
                self.cache[slice_key] = slice_bytes

            response_chunks.append(slice_bytes)

        assembled = b"".join(response_chunks)
        # Offset into assembled chunk
        slice_offset = start - (start_slice * self.slice_size)
        needed_len = end - start + 1
        output = assembled[slice_offset:slice_offset + needed_len]

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(output)),
        }
        if is_range:
            headers["Content-Range"] = f"bytes {start}-{end}/{total_len}"
            return 206, headers, output
        return 200, headers, output


class StaleMediaProxySimulationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.proxy = MediaProxyCacheSimulator(slice_size=1024)
        # Create a 4KB dummy audio file (4 slices of 1024 bytes)
        self.dummy_audio = b"TRACK_DATA_" * 350  # ~3850 bytes
        self.uri = "/media-stream/band-album/01-track.mp3"
        self.proxy.set_upstream_file(self.uri, self.dummy_audio)

    def test_initial_request_populates_cache(self) -> None:
        """First request fetches from upstream and populates cache."""
        status, headers, body = self.proxy.request(self.uri)
        self.assertEqual(status, 200)
        self.assertEqual(body, self.dummy_audio)
        self.assertGreater(len(self.proxy.cache), 0)

    def test_byte_range_request_serves_partial_content_206(self) -> None:
        """Seeking with Range header returns HTTP 206 Partial Content."""
        status, headers, body = self.proxy.request(self.uri, range_header="bytes=500-1500")
        self.assertEqual(status, 206)
        self.assertEqual(body, self.dummy_audio[500:1501])
        self.assertEqual(headers["Content-Range"], f"bytes 500-1500/{len(self.dummy_audio)}")

    def test_stale_cache_serves_media_during_simulated_archive_org_outage(self) -> None:
        """When archive.org drops offline (502/503), previously cached tracks continue streaming."""
        # 1. Listener requests track while archive.org is online
        status, _, body = self.proxy.request(self.uri)
        self.assertEqual(status, 200)

        # 2. Archive.org goes down with 502 Bad Gateway
        self.proxy.set_upstream_outage(502)

        # 3. Subsequent request for the cached track continues to succeed (HTTP 200)
        status2, _, body2 = self.proxy.request(self.uri)
        self.assertEqual(status2, 200)
        self.assertEqual(body2, self.dummy_audio)

        # 4. Range seek requests on cached track also succeed with 206 Partial Content
        status_range, headers_range, body_range = self.proxy.request(self.uri, range_header="bytes=100-800")
        self.assertEqual(status_range, 206)
        self.assertEqual(body_range, self.dummy_audio[100:801])

    def test_uncached_track_during_outage_returns_502(self) -> None:
        """A track never previously requested returns 502 during an outage."""
        self.proxy.set_upstream_outage(503)
        status, _, body = self.proxy.request("/media-stream/unknown/track.mp3")
        self.assertEqual(status, 503)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
