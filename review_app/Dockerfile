# Build context is the repo root (see webapp/deploy/docker-compose.yml's
# `context: ../.. / dockerfile: review_app/Dockerfile`), not review_app/
# itself - review_app imports tools/ as a sibling directory via
# sys.path.insert (see e.g. archive_read.py), so the image needs both
# present with the same relative layout they have in the repo. GitHub
# issue #14.
FROM python:3.12-slim

WORKDIR /app

# Deno - yt-dlp (see GitHub issue #49) needs an external JavaScript runtime
# to solve YouTube's "n challenge" (anti-throttling puzzle); without one,
# extraction succeeds but returns no real video/audio formats, only
# storyboard thumbnails - see https://github.com/yt-dlp/yt-dlp/wiki/EJS.
# Installed to /usr/local/bin (already on PATH for appuser below) before
# any pip install, so it's cached independently of Python dependency churn.
# Deno version and official GitHub release SHA-256 checksums are pinned
# to eliminate unpinned curl|sh shell execution (GitHub issue #91).
ARG DENO_VERSION=2.9.7
ARG DENO_SHA256_AMD64=c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490
ARG DENO_SHA256_ARM64=c832298b1ad4422481334855f6003e0f54145762c5a134f20a489511d2f65bbf

RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates ffmpeg && \
    ARCH="$(dpkg --print-architecture)" && \
    case "$ARCH" in \
        amd64) DENO_ARCH="x86_64"; EXPECTED_SHA="$DENO_SHA256_AMD64" ;; \
        arm64) DENO_ARCH="aarch64"; EXPECTED_SHA="$DENO_SHA256_ARM64" ;; \
        *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;; \
    esac && \
    curl -fsSL -o /tmp/deno.zip "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${DENO_ARCH}-unknown-linux-gnu.zip" && \
    echo "${EXPECTED_SHA}  /tmp/deno.zip" | sha256sum -c - && \
    unzip -d /usr/local/bin /tmp/deno.zip && \
    chmod 755 /usr/local/bin/deno && \
    rm /tmp/deno.zip && \
    apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY tools/requirements.txt tools/requirements.txt
COPY review_app/requirements.txt review_app/requirements.txt
COPY review_app/requirements.lock review_app/requirements.lock
RUN pip install --no-cache-dir -r review_app/requirements.lock

COPY tools/ tools/
COPY review_app/ review_app/

WORKDIR /app/review_app
# Offline GeoIP database for visitor country resolution (GitHub issues #34, #91).
# SHA-256 checksum is verified upon download to ensure asset integrity.
ARG MMDB_SHA256=7ca6753b093a69441a5e9185f498ee1e767c24ef6c0915494d1f68090cca032d
RUN python3 -c "import urllib.request, hashlib, sys; \
    url = 'https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-Country.mmdb'; \
    data = urllib.request.urlopen(url, timeout=30).read(); \
    h = hashlib.sha256(data).hexdigest(); \
    (h == '${MMDB_SHA256}') or sys.exit('SECURITY ERROR: MMDB SHA-256 mismatch! Got ' + h + ', expected ${MMDB_SHA256}'); \
    open('/app/review_app/country.mmdb', 'wb').write(data)"



RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -m -s /bin/bash appuser && \
    mkdir -p /data && \
    chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000
# --timeout 600: gunicorn's 30s default would kill a worker mid-upload on
# a large video, matching nginx's own client_body_timeout/proxy_read_timeout
# (see webapp/deploy/nginx/daugavpils.conf) - see GitHub issue #21.
CMD ["gunicorn", "--workers", "2", "--timeout", "600", "--bind", "0.0.0.0:8000", "wsgi:app"]
