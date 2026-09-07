---
status: accepted
---

# GitHub Pages mirror, built by CI

ADR-0001 ruled out CI/CD for the Site Build, and rejected a static host
like GitHub Pages, on the stated grounds that "CI has no access to the
real media files, so it cannot produce a correct Site Build." That's true
for `tools/validate.py --write` and `tools/publish_to_archive_org.py`,
which need the actual local audio/image/video bytes to checksum or
upload. It is **not** true for `webapp/build.py` itself: the Site Build
never reads media bytes at all — it renders `band.yaml`/`release.yaml`
metadata and links straight to archive.org, and its one integrity check
(`require_media_published`) calls archive.org's public read API, which
needs no credentials (confirmed: `ia.get_item()` works unauthenticated
for a public item). So a CI runner with just this git repo checked out
and outbound internet access can produce a Site Build identical to a
maintainer's local one.

Given that, we're adding a second, independent way to serve the Site:

- **`.github/workflows/pages.yml`** runs `webapp/build.py` on every push
  to `main` (and on manual trigger) and publishes `webapp/dist/` to this
  repo's GitHub Pages.
- **This is a live mirror, not a replacement for daugavpils.fans.** It's
  reachable at this repo's default Pages URL
  (`https://<owner>.github.io/<repo>/`), independently of the Hetzner
  server ADR-0001 describes. DNS for `daugavpils.fans` keeps pointing at
  Hetzner; nothing about the primary deploy path changes.
- **`SITE_BASE_PATH`** (`webapp/build.py`, `webapp/i18n.py`) makes every
  site-internal link/asset path prefix-aware, since a GitHub Pages
  project site is served from `/<repo-name>/`, not the domain root. The
  Hetzner build stays unset (root-relative), unaffected.

## Considered Options

- **Committing `webapp/dist/` to a `gh-pages` branch or `/docs` folder
  instead of a CI build** — rejected: it would make the disposable,
  regenerable Site Build a tracked git artifact, drifting from what's
  actually in `bands/**/*.yaml` the moment someone forgets to rebuild.
- **Pointing `daugavpils.fans` at GitHub Pages instead of Hetzner** —
  out of scope here; this is additive (a mirror), not a migration. Worth
  revisiting on its own if the Hetzner server ever becomes a burden.

## Consequences

- Two independent deploy paths now exist for the same Site Build:
  Hetzner (manual rsync, primary, owns the custom domain — see
  `webapp/deploy/README.md`) and GitHub Pages (automatic via CI, mirror
  only). They can drift in *when* they were last built, not in what they
  contain, since both run the same `webapp/build.py` against the same
  git history.
- CI now depends on archive.org being reachable to build successfully
  (same dependency `webapp/build.py` already had locally) — a CI run can
  fail if archive.org is briefly unreachable, unrelated to any bug in
  this repo.
