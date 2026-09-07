---
status: accepted
---

# Public proposal/approval app as a live server process

ADR-0001 chose static generation over a live server specifically because
"CI has no access to the real media files, so it cannot produce a
correct Site Build" and rejected "a live server process reading `bands/`
per request" for the Site itself. Issue #8 (public text-edit proposals
with curated approval) needs something ADR-0001's static model can't
provide on its own: state that outlives one request (pending proposals,
who's an approver, a magic-link token's single use), and a public write
path into a private, curated decision. Neither fits a build step that
runs once and produces disposable HTML.

This is a **scoped exception**, not a reversal: `review_app/` is a second,
independent Flask process (gunicorn, SQLite), reachable only through
nginx at `review.daugavpils.fans`, added as a second `docker-compose.yml`
service alongside the existing nginx container (see
`webapp/deploy/README.md`). The Site itself - what `webapp/build.py`
produces and nginx serves at `daugavpils.fans`/the GitHub Pages mirror -
stays exactly as static as ADR-0001 decided. `review_app` never becomes
part of rendering a band/release page; it only reads the live checkout
(`review_app/archive_read.py`, read-only) to show a submitter the current
value of a field, and writes go through a completely separate pipeline
(`tools/apply_proposal.py`, run inside a GitHub Action, never inside
`review_app` itself - see issue #13) that lands as an ordinary commit the
existing Site-Build machinery (the auto-redeploy timer, ADR-0002's Pages
mirror) picks up exactly like any other push to `main`.

## Considered Options

- **Extend `webapp/build.py`'s static model somehow** (e.g. a client-side
  form posting to a third-party form service) - rejected: the PRD (#8)
  explicitly requires this to run on infrastructure the project already
  controls, not a third-party platform, and a public write path into the
  Archive needs server-side validation and a real approval gate, not
  something a static page + external service can provide.
- **A live server for the whole Site**, folding `review_app` and
  `webapp/build.py`'s rendering into one process - rejected: it would
  drag every page of the Site back into needing a live process
  (reopening exactly what ADR-0001 avoided) just to support a feature
  that only concerns the small, separate business of pending proposals.
  Keeping them as two independent processes means a `review_app` bug or
  outage can't take down the Site itself.

## Consequences

- **A second live process now exists on `cherry`** alongside the
  otherwise-static Site, with its own container, its own port (never
  exposed directly - only nginx reaches it), and its own uptime/restart
  concerns. It's an accepted, deliberately narrow exception: it owns
  proposal/approver state, nothing about how the Site itself is built or
  served.
- **`review_app`'s SQLite database (`review-app-data/`, bind-mounted, not
  committed) is not backed up or distributed anywhere** - unlike
  `bands/**/*.yaml`, which is safe in git and, via
  `daugavpils-fans-metadata`, on archive.org too. What's at risk if it's
  lost is only pending proposals and the approver list, both small and
  re-creatable by hand (`review_app/manage.py add-approver`); approved
  content is already safely committed to `main` by the time it matters.
- **The auto-redeploy timer (ADR-0002/#10) redeploys on *any* push to
  `main`, not only proposal-driven ones** - it can't distinguish an
  approved-proposal commit from any other, and was never asked to. This
  was already true before `review_app` existed; the proposal pipeline
  just became one more thing that lands a commit the timer picks up the
  same way, with the same `SITE_SKIP_LOCAL_VALIDATION=1` limitation
  (see ADR-0002).
- **Self-approval prevention is an honor-system-level control, not a hard
  security boundary.** A proposal is only stamped as "submitted by
  approver X" when X was logged in at submission time, or volunteered a
  contact email matching their own approver record. A determined
  approver who submits anonymously with no contact info, then logs in
  separately to approve their own submission, cannot be technically
  distinguished from two different people in this version - this is the
  expected/honest-mistake case being caught, not a guarantee against
  deliberate bypass (see PRD #8's Implementation Decisions for the full
  reasoning).
- **A GitHub Actions gotcha, discovered deploying this (#14):** a push
  made with a workflow's own auto-issued `GITHUB_TOKEN` does not trigger
  another workflow's `on: push` (anti-recursion protection) - so
  `apply-proposal.yml`'s commit to `main` never triggered
  `.github/workflows/pages.yml` on its own. Fixed by having
  `apply-proposal.yml` explicitly call `pages.yml`'s own
  `workflow_dispatch` trigger (needs `actions: write` in addition to
  `contents: write`) right after pushing - a small, non-obvious coupling
  between the two workflows that's easy to silently lose if either one is
  rewritten later.
