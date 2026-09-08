# Build context is the repo root (see webapp/deploy/docker-compose.yml's
# `context: ../.. / dockerfile: review_app/Dockerfile`), not review_app/
# itself - review_app imports tools/ as a sibling directory via
# sys.path.insert (see e.g. archive_read.py), so the image needs both
# present with the same relative layout they have in the repo. GitHub
# issue #14.
FROM python:3.12-slim

WORKDIR /app

COPY tools/requirements.txt tools/requirements.txt
COPY review_app/requirements.txt review_app/requirements.txt
RUN pip install --no-cache-dir -r tools/requirements.txt -r review_app/requirements.txt

COPY tools/ tools/
COPY review_app/ review_app/

WORKDIR /app/review_app
EXPOSE 8000
# --timeout 600: gunicorn's 30s default would kill a worker mid-upload on
# a large video, matching nginx's own client_body_timeout/proxy_read_timeout
# (see webapp/deploy/nginx/daugavpils.conf) - see GitHub issue #21.
CMD ["gunicorn", "--workers", "2", "--timeout", "600", "--bind", "0.0.0.0:8000", "wsgi:app"]
