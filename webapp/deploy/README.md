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
  site/                  # NOT committed - bind-mounted as a whole into BOTH
                          # the nginx and review-app containers (`./site:/site:ro`);
                          # nginx's `root` is /site/current, so everything
                          # actually served lives somewhere under here
    checkouts/            # one directory per deployed commit - a git-worktree
                          # checkout for the automatic timer's deploys (pruned
                          # automatically), or `manual/` for a hand-run deploy
                          # (step 4 below)
    current                # symlink to whichever checkouts/<x>/webapp/dist was
                          # deployed most recently, by either path - atomically
                          # re-pointed on every deploy, manual or automatic
    current-checkout       # symlink to the same commit's checkout *root* (not
                          # webapp/dist) - what review-app reads bands/**/*.yaml
                          # from (ARCHIVE_CHECKOUT_PATH, see "Deploying
                          # review_app" below); re-pointed atomically alongside
                          # current, by the same sync-and-deploy.sh run
  sync-and-deploy.sh     <- this file, installed here and made executable
  repo/                  # NOT committed - the timer's own persistent git clone
                          # (bootstrapped automatically on first run)
  .last-deployed-sha      # NOT committed - state file the timer uses to no-op
                          # when main hasn't moved
  review-app.env          # NOT committed - review-app's secrets, chmod 600
                          # (see "Deploying review_app" below)
  review-app-data/         # NOT committed - review-app's SQLite database
```

## Rebuilding this from scratch on a fresh server

1. Copy `docker-compose.yml` and `nginx/daugavpils.conf` to
   `/opt/daugavpils-fans/` on the server, preserving this relative layout.
2. Copy `cloudflare.ini.example` to `/opt/daugavpils-fans/cloudflare.ini`,
   fill in a real Cloudflare API token (Zone:DNS:Edit, scoped to the
   `daugavpils.fans` zone), then `chmod 600` it.
3. Issue the cert (DNS-01, so only port 443 needs to be open - see ADR-0001
   for why). Includes `review.daugavpils.fans` in the same cert's SAN list
   (see "Deploying review_app" below) - both containers' server blocks
   already reference the same cert files, nothing else changes if you add
   more subdomains here later:
   ```bash
   docker run --rm \
     -v /opt/daugavpils-fans/certbot/conf:/etc/letsencrypt \
     -v /opt/daugavpils-fans/cloudflare.ini:/cloudflare.ini:ro \
     certbot/dns-cloudflare certonly \
     --dns-cloudflare \
     --dns-cloudflare-credentials /cloudflare.ini \
     --dns-cloudflare-propagation-seconds 30 \
     -d daugavpils.fans -d www.daugavpils.fans -d review.daugavpils.fans \
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
if it doesn't exist yet); `REPO_URL` points at this repo over plain HTTPS
(it's public, so cloning/fetching needs no auth at all this way - no
deploy key to provision on this box). First run bootstraps
`/opt/daugavpils-fans/repo` itself via `git clone` - nothing else needs
to pre-exist.

Check on it:

```bash
systemctl status daugavpils-fans-sync.timer
journalctl -u daugavpils-fans-sync.service -n 50
```

## Deploying review_app

`review_app` (see GitHub issue #8/#14) is the self-hosted app that lets
anyone propose a text edit to the archive and a curated group of
approvers decide on it. It runs as a second container next to nginx,
reachable only through it - see `docker-compose.yml`'s `review-app`
service (no `ports:`, only `expose:`) and
`nginx/daugavpils.conf`'s `review.daugavpils.fans` server block
(`proxy_pass http://review-app:8000`).

1. **DNS**: add an `A` record for `review.daugavpils.fans` pointing at
   this box's IP, in the same Cloudflare zone as `daugavpils.fans` -
   DNS-only (grey cloud), matching the existing `daugavpils.fans`/`www`
   records.
2. **Cert**: add `-d review.daugavpils.fans` to the certbot command in
   step 3 above (already shown that way) - one cert covers all three
   names.
3. **Secrets**: copy `review-app.env.example` to
   `/opt/daugavpils-fans/review-app.env`, fill in real values, `chmod 600`
   it - same pattern as `cloudflare.ini`. What each one is:

   | Variable | What it is |
   |---|---|
   | `SECRET_KEY` | Flask session signing key - generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
   | `DATABASE_PATH` | `/data/review.db` (matches the `review-app-data:/data` volume) |
   | `ARCHIVE_CHECKOUT_PATH` | `/site/current-checkout` (matches `sync-and-deploy.sh`'s symlink, see the layout tree above) |
   | `MAINTAINER_EMAIL` | where new-submission notifications go |
   | `SMTP_HOST`/`SMTP_PORT`/`SMTP_FROM`/`SMTP_USER`/`SMTP_PASSWORD` | an outbound mail relay's credentials - any real SMTP provider (leave `SMTP_USER`/`SMTP_PASSWORD` blank only for an unauthenticated local relay, not a real one) |
   | `GITHUB_DISPATCH_TOKEN` | a fine-grained GitHub PAT, scoped to this repo only, `Actions: write` permission only - **not** `Contents` - generate at github.com/settings/personal-access-tokens |
   | `GITHUB_REPO` | `svalevka/daugavpils.fans` |
   | `REVIEW_APP_CALLBACK_KEY` | any long random string (e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`) - **also** set as a GitHub Actions repo secret of the same name (Settings > Secrets and variables > Actions > Secrets), same value in both places |
   | `RATE_LIMIT_PER_IP_PER_HOUR` | optional, defaults to 20 |
   | `LOGIN_RATE_LIMIT_PER_IP_PER_HOUR` | optional, defaults to 5 - tighter than the submit limit above since a match sends a real email |

   Also set, as a GitHub Actions repo **variable** (not secret - Settings
   > Secrets and variables > Actions > Variables), `REVIEW_APP_BASE_URL`
   = `https://review.daugavpils.fans` -
   `.github/workflows/apply-proposal.yml` reads it from there to know
   where to call back.
4. **First approver**: seed yourself once the container is running (step
   5):
   ```bash
   docker compose exec review-app python manage.py add-approver you@example.com "Your Name"
   ```
5. **Start it** - only once `site/current-checkout` exists (i.e. after
   "Automatic redeploy" above has been installed and has run at least
   once): its build context *is* that symlink (see
   `docker-compose.yml`'s comment on the `review-app` service), so there's
   nothing to build from before then. Trigger a run by hand rather than
   waiting up to 5 minutes for the timer:
   ```bash
   sudo systemctl start daugavpils-fans-sync.service
   ls -la /opt/daugavpils-fans/site/current-checkout   # confirm it now exists
   cd /opt/daugavpils-fans && docker compose up -d --build review-app
   ```
6. **Verify**: `docker compose config` (from this directory, with
   `review-app.env` in place) should validate cleanly before any of the
   above; `curl -I https://review.daugavpils.fans/submit` should return
   `200`. Then a real end-to-end pass: submit a proposal at
   `/submit`, confirm the maintainer notification email arrives, log in
   via the emailed magic link, approve it on `/dashboard`, watch the
   triggered Action run in the repo's Actions tab, confirm the commit
   lands on `main`, and confirm both the Pages mirror and (within one
   timer interval) the primary domain pick it up.

### Photo/video proposals (GitHub issue #21)

Unlike text proposals, approving a photo/video on `/dashboard` never
touches git or archive.org - it's a "curated queue, manual finish"
design: approving only moves it to the dashboard's "Approved, awaiting
publish" list. To actually finish one:

```bash
scp <server>:/opt/daugavpils-fans/review-app-data/uploads/<stored-filename> .
```

(the dashboard names the file), then place it under the right
`bands/<band-slug>/` (or `bands/<band-slug>/<release-slug>/`) directory
in your own local checkout, add the `image:`/`video:` entry to
`band.yaml`/`release.yaml` (the dashboard shows the submitter's suggested
caption, if any), run `tools/validate.py --write`, then
`tools/publish_to_archive_org.py`, commit, and push - exactly the same
manual steps as adding media any other way. Once done, click "Mark
published" on the dashboard - this deletes the file from
`review-app-data/uploads/` (unbacked-up, so nothing should linger there
once you're finished with it) and closes out the row.

## Cert renewal

The cert expires 90 days after issuance and renewal is currently manual.
Its SAN list now includes `review.daugavpils.fans` too (see above), but
only nginx ever reads the cert files - it terminates TLS and proxies
plaintext to `review-app` internally (see its `proxy_pass` in
`nginx/daugavpils.conf`) - so only nginx needs restarting:

```bash
docker run --rm \
  -v /opt/daugavpils-fans/certbot/conf:/etc/letsencrypt \
  -v /opt/daugavpils-fans/cloudflare.ini:/cloudflare.ini:ro \
  certbot/dns-cloudflare renew
docker compose restart nginx
```
