---
status: accepted
---

# Static Site Build, manually deployed to an existing server

The README previously stated a website/frontend was explicitly out of scope. We're reversing that: a public Site (see [CONTEXT.md](../../CONTEXT.md)) will let listeners browse bands, read bios, and play tracks — built from the same Archive that already exists.

The Archive's media files are deliberately excluded from git and only exist in full on a maintainer's local machine; there is no CI environment with access to them. Given that, we decided:

- **Static generation, not a live server.** `webapp/build.py` imports the existing `tools/models.py` Pydantic models, walks `bands/`, renders Jinja2 templates, and writes a Site Build (`webapp/dist/`, gitignored). The server just serves plain files (e.g. nginx) — no app process to run, restart, or redeploy.
- **Reuse the existing Hetzner server** the maintainer already administers, rather than standing up static hosting + object storage for media. One less system to operate, at the cost of not being geographically distributed.
- **`build.py` requires `tools/validate.py`'s checks to pass first**, and fails loud if any referenced media file is missing or checksum-mismatched. The Site Build reuses the Archive's existing integrity guarantee instead of inventing a separate one, and can never link to a broken file.
- **Deploy is manual and maintainer-run**: run `validate.py`, then `build.py`, then rsync `webapp/dist/` to the server. No CI/CD — CI has no access to the real media files, so it cannot produce a correct Site Build.
- **v1 routes**: `/` (band list), `/bands/<band-slug>/` (bio, members, photos), `/bands/<band-slug>/<release-slug>/` (tracklist, audio player, cover art). JSON-LD structured data is deferred — the models already produce schema.org-shaped data, so adding it later is a small addition, not a redesign.

## Considered Options

- **Live server process** reading `bands/` per request — rejected for v1 to avoid a process that needs to stay running and be redeployed on every code or content change.
- **Static host + object storage** (e.g. GitHub Pages + S3/R2) — rejected in favor of the Hetzner server already owned and administered by the maintainer, avoiding a second infrastructure provider for a single-maintainer archive at this scale.
- **Best-effort build** that skips missing media with a warning — rejected in favor of hard-failing via `validate.py`, so the Site never silently ships an incomplete page.

## Consequences

- The Site Build can only be produced from a machine holding the full local media tree — the same constraint `validate.py --write` already has. Publishing a content update is a manual, maintainer-only action.
- The Site depends on the Hetzner server's availability, unlike the Archive itself (whose Archive Release distribution has no single point of failure). This is an accepted, scoped exception: the Archive stays durable independent of the Site.
