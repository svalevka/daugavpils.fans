# Server-side deploy config (ADR-0001)

These are the files that actually run on the hosting server - everything
here is config, not secrets or generated output, so it's safe to commit.
What's deliberately *not* here (and why): see the table in this repo's
history, or just re-derive it - it's all reissuable.

## Layout on the server

```
/opt/daugavpils-fans/
  docker-compose.yml   <- this file
  nginx/
    daugavpils.conf    <- this file
  cloudflare.ini        # NOT committed - real Cloudflare API token, chmod 600
  certbot/
    conf/                # NOT committed - Let's Encrypt certs, regenerated on demand
    www/
  dist/                  # NOT committed - webapp/build.py output, rsynced here
```

## Rebuilding this from scratch on a fresh server

1. Copy `docker-compose.yml` and `nginx/daugavpils.conf` to
   `/opt/daugavpils-fans/` on the server, preserving this relative layout.
2. Copy `cloudflare.ini.example` to `/opt/daugavpils-fans/cloudflare.ini`,
   fill in a real Cloudflare API token (Zone:DNS:Edit, scoped to the
   `daugavpils.fans` zone), then `chmod 600` it.
3. Issue the cert (DNS-01, so only port 443 needs to be open - see ADR-0001
   for why):
   ```bash
   docker run --rm \
     -v /opt/daugavpils-fans/certbot/conf:/etc/letsencrypt \
     -v /opt/daugavpils-fans/cloudflare.ini:/cloudflare.ini:ro \
     certbot/dns-cloudflare certonly \
     --dns-cloudflare \
     --dns-cloudflare-credentials /cloudflare.ini \
     --dns-cloudflare-propagation-seconds 30 \
     -d daugavpils.fans -d www.daugavpils.fans \
     --agree-tos --non-interactive --email daugavpils@gmail.com --no-eff-email
   ```
4. Build and deploy the site itself. Media isn't part of the Site Build -
   it's published separately to archive.org (`tools/publish_to_archive_org.py`,
   requires the full local `bands/` media tree) and `webapp/build.py`
   refuses to build if anything it needs isn't already there. `dist/`
   ends up as HTML/CSS only:
   ```bash
   python tools/publish_to_archive_org.py   # first time, or after adding media
   python webapp/build.py
   rsync -avz --delete webapp/dist/ <server>:/opt/daugavpils-fans/dist/
   ```
5. Start nginx:
   ```bash
   cd /opt/daugavpils-fans && docker compose up -d
   ```

## Cert renewal

The cert expires 90 days after issuance and renewal is currently manual:

```bash
docker run --rm \
  -v /opt/daugavpils-fans/certbot/conf:/etc/letsencrypt \
  -v /opt/daugavpils-fans/cloudflare.ini:/cloudflare.ini:ro \
  certbot/dns-cloudflare renew
docker compose restart nginx
```
