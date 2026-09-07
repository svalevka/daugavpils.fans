---
status: accepted
---

# GitHub Pages mirror, built by CI

ADR-0001 ruled out CI/CD for the Site Build, and rejected a static host
like GitHub Pages, on the stated grounds that "CI has no access to the
real media files, so it cannot produce a correct Site Build." `webapp/
build.py`'s own template-rendering logic bears this out only partly: it
never reads media bytes itself - it renders `band.yaml`/`release.yaml`
metadata and links straight to archive.org. But it also shells out to
`tools/validate.py` first (`require_valid_archive`), which very much does
need the real local audio/image/video files to recheck their checksums -
and those are gitignored (see README), so a plain CI checkout has none of
them. Confirmed by trying it: the first CI run failed exactly there, with
every single file logged as missing.

The fix isn't to weaken that check for everyone - it's real and it's what
makes `validate.py` trustworthy. Instead, `webapp/build.py` now accepts
`SITE_SKIP_LOCAL_VALIDATION` to skip *only* that local re-check when
there's deliberately no local media tree (CI). The build's actual
integrity gate becomes `require_media_published`, which calls archive.org's
public read API and needs no credentials (confirmed: `ia.get_item()` works
unauthenticated for a public item) - and that check is sound on its own
because `tools/publish_to_archive_org.py` never lets a file reach
archive.org without `validate.py` having already passed for real, against
the maintainer's actual local files, at publish time. CI is trusting work
already done, not skipping it.

Given that, we're adding a second, independent way to serve the Site:

- **`.github/workflows/pages.yml`** runs `webapp/build.py` (with
  `SITE_SKIP_LOCAL_VALIDATION=1`) on every push to `main` (and on manual
  trigger) and publishes `webapp/dist/` to this repo's GitHub Pages.
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
- The GitHub Pages build's integrity guarantee is one step removed from
  the Hetzner build's: Hetzner re-verifies every file's checksum locally,
  every time, via `tools/validate.py`; the Pages build trusts that
  `publish_to_archive_org.py` already did that verification at publish
  time and only re-confirms the files still exist on archive.org. An
  accepted tradeoff, not a gap in practice - metadata never reaches
  archive.org without passing `validate.py` first.
- CI now depends on archive.org being reachable to build successfully
  (same dependency `webapp/build.py` already had locally) — a CI run can
  fail if archive.org is briefly unreachable, unrelated to any bug in
  this repo.
