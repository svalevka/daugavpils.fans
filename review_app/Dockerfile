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
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates && \
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh && \
    apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY tools/requirements.txt tools/requirements.txt
COPY review_app/requirements.txt review_app/requirements.txt
RUN pip install --no-cache-dir -r tools/requirements.txt -r review_app/requirements.txt

COPY tools/ tools/
COPY review_app/ review_app/

WORKDIR /app/review_app
# Offline GeoIP database for visitor country resolution (GitHub issue #34)
RUN python3 -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-Country.mmdb', '/app/review_app/country.mmdb')" || true

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
