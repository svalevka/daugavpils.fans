# Site deployment architecture

This is the map: where metadata and media actually live, and how a
change (a merged PR, or an approved review-app proposal) ends up live.
It's deliberately a **current-state overview, not a decision record and
not a runbook**:

- For *why* it's built this way, see `docs/adr/0001-static-site-build-for-public-website.md`,
  `docs/adr/0002-github-pages-mirror-via-ci.md`, and
  `docs/adr/0003-public-proposal-app-live-server.md`.
- For the exact, runnable commands to stand this up on a fresh server, or
  redeploy by hand, see [`webapp/deploy/README.md`](../webapp/deploy/README.md)
  - this doc doesn't repeat those steps, so it can't drift from them.

This covers **infrastructure/deployment topology only** - not how the
application code itself (`webapp/build.py`, `review_app/`, `tools/`) is
organized internally.

```mermaid
flowchart TD
    subgraph archiveorg["archive.org — media"]
        MEDIA["Published band/release items<br/>(audio/image/video)"]
    end

    subgraph github["github.com — metadata (git) + CI"]
        CONTRIBUTOR["Contributor opens a PR<br/>(new/edited band.yaml or release.yaml)"] -->|"quorum review,<br/>then merged"| MAIN["main branch<br/>bands/**/*.yaml"]

        subgraph gha["GitHub Actions"]
            APPLY["apply-proposal.yml<br/>fetch proposal → commit → push"]
            PAGES["pages.yml<br/>build → deploy"]
        end

        APPLY -->|"push"| MAIN
        APPLY -->|"explicit workflow_dispatch<br/>(a workflow's own commit can't<br/>trigger another's on: push)"| PAGES
        MAIN -->|"on: push"| PAGES
    end

    PUBLISH["tools/publish_to_archive_org.py<br/>run locally by whoever holds<br/>archive.org credentials, after the PR merges<br/>- never runs in CI (needs real media files)"]
    MAIN -.->|"merged band.yaml/release.yaml<br/>declares what should exist"| PUBLISH
    PUBLISH --> MEDIA
    PUBLISH -.->|"writes archive.org + torrent<br/>URLs back (sameAs), a follow-up commit"| MAIN

    subgraph vps["Linux VPS server — docker compose"]
        NGINX["nginx<br/>TLS termination for both subdomains"]
        REVIEWAPP["review_app container<br/>submit + dashboard, SQLite<br/>(ADR-0003)"]
        TIMER["sync-and-deploy.sh<br/>systemd timer - polls main every 5 min"]
        SITE["Site Build<br/>(webapp/dist, served by nginx)"]
    end

    SUBMITTER["Anyone proposes a text edit<br/>or a new photo/video<br/>review.daugavpils.fans/submit"] --> REVIEWAPP
    APPROVER["A curated approver decides<br/>on /dashboard"] --> REVIEWAPP
    REVIEWAPP -->|"text proposal approved:<br/>workflow_dispatch (id only)"| APPLY
    REVIEWAPP -->|"media proposal approved:<br/>no CI, no auto-publish"| MAINT["Maintainer scp/rsyncs the file,<br/>writes the YAML entry,<br/>publishes + commits by hand"]
    MAINT --> PUBLISH
    MAINT -.->|"commit"| MAIN

    MAIN -.->|"polled, not pushed -<br/>the VPS has no inbound GitHub access"| TIMER
    TIMER --> SITE --> NGINX
    PAGES --> GHPAGES["GitHub Pages mirror<br/>&lt;owner&gt;.github.io/&lt;repo&gt;"]

    SITE -.->|"rendered pages link/stream<br/>media directly - never copied"| MEDIA
    GHPAGES -.->|"same"| MEDIA

    NGINX -->|"daugavpils.fans<br/>(primary, custom domain)"| READER1["Reader"]
    GHPAGES -->|"independent mirror -<br/>no review_app here"| READER2["Reader"]
```

## The two independent Site Build paths

The same `webapp/build.py`, run twice, two different ways - either one
being unreachable doesn't take the Site down (see ADR-0002, and
README.md's "Resilience" section for the reasoning):

- **The Linux VPS server (primary, custom domain).** A `systemd` timer
  (`sync-and-deploy.sh`) polls `main` every 5 minutes and, if it's moved,
  builds a fresh Site Build and atomically re-points nginx's `current`
  symlink at it. GitHub never gets inbound access to this box - it pulls,
  nothing pushes to it. Because this box has no local media tree, it
  builds with `SITE_SKIP_LOCAL_VALIDATION=1`, trusting archive.org's
  already-published checksums. A maintainer can still build+rsync by hand
  for a fully-verified, on-demand deploy - see `webapp/deploy/README.md`.
- **GitHub Pages (mirror).** `.github/workflows/pages.yml` runs on every
  push to `main` (and on manual `workflow_dispatch`), builds the same way
  (`SITE_SKIP_LOCAL_VALIDATION=1` - CI has no local media tree either),
  and publishes to this repo's default Pages URL. No `review_app` here;
  this is a read-only mirror.

## `review_app` and the approval pipeline

`review_app` (ADR-0003) is the one live server process in an otherwise
static-site system - a second container on the same VPS, reachable only
through nginx (`review.daugavpils.fans`), with its own SQLite database
(not backed up - see ADR-0003's Consequences). It never holds any
credential that can push to git, and never holds archive.org credentials
at all.

It handles two kinds of proposal, decided the same way on the same
`/dashboard` (one curated approver, no quorum, self-approval blocked -
see ADR-0003's Consequences) but applied completely differently once
approved:

- **Text proposals** (issue #13) - approving dispatches
  `apply-proposal.yml` with just the proposal's id. That Action fetches
  the actual content from `review_app`'s authenticated callback API,
  commits it, and pushes to `main`. Because a workflow's own commit can't
  trigger another workflow's `on: push` (a GitHub anti-recursion rule),
  `apply-proposal.yml` explicitly dispatches `pages.yml` itself right
  after pushing - the VPS's timer picks the same push up on its own next
  poll, no dispatch needed there.
- **Media proposals** (issue #21, "curated queue, manual finish") -
  approving never dispatches anything. It moves the upload to a "ready to
  publish" list on the dashboard; a maintainer retrieves the file
  (`scp`/`rsync` from `review-app-data/uploads/` on the VPS - see
  `webapp/deploy/README.md`), writes the `image`/`video` entry into
  `band.yaml`/`release.yaml` by hand, runs `tools/validate.py --write`
  then `tools/publish_to_archive_org.py`, and commits/pushes directly -
  the same manual process as "Adding a new band, release, or media"
  below, just sourced from a curated queue instead of an ad hoc message.
  This was a deliberate choice, not a gap to fill later: giving CI (or
  `review_app`) real archive.org write credentials to fully automate this
  too would be a real trust escalation ADR-0003 was careful to avoid for
  the text-proposal pipeline.

Both `apply-proposal.yml` and `pages.yml` use a `concurrency:` group
(`apply-proposal` and `pages` respectively) so that proposals approved
seconds apart apply one at a time instead of racing, and a Pages
deploy already in flight is never killed mid-way by a newer one. Neither
of this applies to media proposals - there's no Action in that path to
race.

## Adding a new band, release, or media

This is a separate story from *redeploying* the Site (everything above),
and doesn't touch this deploy pipeline at all - it's how content gets
into the two homes the pipeline then serves from:

- **Metadata (git)**: a contributor opens a PR with a new/edited
  `band.yaml`/`release.yaml`; quorum review merges it to `main` like any
  other PR. `review_app`'s lighter-weight `/submit` flow (above) covers
  *correcting* an existing field or contributing a new photo/video to an
  *existing* band/release, never adding a whole new band or release -
  that still needs a PR.
- **Media (archive.org)**: after the PR is merged (or, for a `review_app`
  media proposal, after a maintainer's picked it up from the "ready to
  publish" queue - same step either way), whoever holds the project's
  archive.org credentials runs `tools/publish_to_archive_org.py`
  **locally** - it needs the real audio/image/video files, which are
  gitignored and never reach GitHub, so this step can't run in CI. It
  uploads the media as its own archive.org item, then writes the
  resulting archive.org/torrent URLs back into `sameAs` as a follow-up
  commit.

Until that publish step runs, a merged PR's media won't resolve on
either Site Build - `webapp/build.py` refuses to build if it references
media that isn't confirmed to exist yet (see `require_media_published`,
ADR-0002). See README.md's "How this works, at a glance" diagram for the
full reasoning, and "Adding a new band, step by step" for the exact
commands.

## Certificates

TLS terminates at nginx on the VPS, one Let's Encrypt cert (DNS-01 via
Cloudflare) covering `daugavpils.fans`, `www`, and
`review.daugavpils.fans`. Issuance and renewal steps: see
`webapp/deploy/README.md`'s "Cert renewal" section.
