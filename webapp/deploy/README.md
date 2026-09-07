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
  site/                  # NOT committed - bind-mounted as a whole into the nginx
                          # container (`./site:/site:ro`); nginx's `root` is
                          # /site/current, so everything actually served lives
                          # somewhere under here
    checkouts/            # one directory per deployed commit - a git-worktree
                          # checkout for the automatic timer's deploys (pruned
                          # automatically), or `manual/` for a hand-run deploy
                          # (step 4 below)
    current                # symlink to whichever checkouts/<x>/webapp/dist was
                          # deployed most recently, by either path - atomically
                          # re-pointed on every deploy, manual or automatic
  sync-and-deploy.sh     <- this file, installed here and made executable
  repo/                  # NOT committed - the timer's own persistent git clone
                          # (bootstrapped automatically on first run)
  .last-deployed-sha      # NOT committed - state file the timer uses to no-op
                          # when main hasn't moved
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
4. Build and deploy the site itself, once, by hand - this remains the
   right way to do a fully-verified deploy on demand (it re-checks every
   local media file's checksum, which nothing running on the server
   itself can do - see "Automatic redeploy" below). Media isn't part of
   the Site Build - it's published separately to archive.org
   (`tools/publish_to_archive_org.py`, requires the full local `bands/`
   media tree) and `webapp/build.py` refuses to build if anything it
   needs isn't already there. `webapp/dist/` ends up as HTML/CSS only.
   Rsync it into its own `checkouts/manual/` slot, then atomically
   re-point `current` at it - the same mechanism the automatic timer
   uses, so a manual deploy is exactly as atomic:
   ```bash
   python tools/publish_to_archive_org.py   # first time, or after adding media
   python webapp/build.py
   rsync -avz --delete webapp/dist/ <server>:/opt/daugavpils-fans/site/checkouts/manual/webapp/dist/
   ssh <server> ln -sfn checkouts/manual/webapp/dist /opt/daugavpils-fans/site/current
   ```
5. Start nginx:
   ```bash
   cd /opt/daugavpils-fans && docker compose up -d
   ```
6. Install the automatic-redeploy timer (see below) so the site also
   keeps itself current between manual deploys.

## Automatic redeploy from `main` (the pull-based timer)

GitHub Actions never gets inbound access to this box - its SSH firewall
stays allowlisted to a single IP (see ADR-0001's rationale and this
repo's own review-app work), deliberately not widened to GitHub's runner
IP ranges. So instead of an Action pushing a deploy in, `sync-and-deploy.sh`
runs here on a `systemd` timer and pulls: every 5 minutes it fetches
`main`, and if it has moved since the last run, builds the Site from an
isolated `git worktree` under `site/checkouts/`, then atomically re-points
the `current` symlink at it. There is no separate copy into a Docker
bind-mounted directory - `current` is served straight out of the
checkout, and nginx re-resolves that symlink fresh on every request, so
it's always either the fully-old or the fully-new build, never a
half-written one (see `sync-and-deploy.sh`'s header comment for why a
plain rsync into a bind-mounted `dist/` - an earlier version of this -
doesn't actually give you that guarantee).

This box has no local media tree (same limitation the GitHub Pages CI
mirror already has - see ADR-0002), so the timer's build always sets
`SITE_SKIP_LOCAL_VALIDATION=1`, trusting archive.org's already-published
checksums rather than re-verifying local files. **It redeploys on *any*
push to `main`, not only ones the review-app pipeline made** - step 4's
manual, fully-verified path above still exists and is still the way to
do an on-demand deploy with a real local-checksum recheck; the timer just
means the site no longer sits stale between manual deploys.

Install:

```bash
sudo cp sync-and-deploy.sh /opt/daugavpils-fans/sync-and-deploy.sh
sudo chmod +x /opt/daugavpils-fans/sync-and-deploy.sh
sudo cp daugavpils-fans-sync.service daugavpils-fans-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now daugavpils-fans-sync.timer
```

The service unit's `Environment=` lines already point at this layout
(`/opt/daugavpils-fans/repo`, `/opt/daugavpils-fans/site/checkouts`, etc.)
and at a venv Python (`/opt/daugavpils-fans/venv/bin/python3` - create it with
`python3 -m venv /opt/daugavpils-fans/venv && /opt/daugavpils-fans/venv/bin/pip install -r tools/requirements.txt -r webapp/requirements.txt`
if it doesn't exist yet); `REPO_URL` points at this repo over SSH, so the
`sergei` user's SSH agent/key needs read access to it. First run
bootstraps `/opt/daugavpils-fans/repo` itself via `git clone` - nothing
else needs to pre-exist.

Check on it:

```bash
systemctl status daugavpils-fans-sync.timer
journalctl -u daugavpils-fans-sync.service -n 50
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
