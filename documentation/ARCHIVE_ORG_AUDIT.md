# How the nightly archive.org consistency check works

This document explains `tools/verify_archive_org.py` and the
`.github/workflows/verify-archive-org.yml` schedule that runs it - the
standing check that archive.org actually matches `bands/**/*.yaml`, not
just that `tools/publish_to_archive_org.py` exited 0 the last time
someone ran it. Built after a real incident (GitHub issue #31): a reader
compared the site's Глазки Стекольщика track counts/descriptions against
Bandcamp and found drift that no tool had caught, because a "successful"
publish isn't actually proof of anything past the moment it ran - see
"Why a clean publish isn't enough" below.

## What it checks

For every band and every release item on archive.org, `verify_archive_org.py`:

- Fetches the item's real file listing and metadata from archive.org's
  public API (`ia.get_item()` - no `ia configure` credentials needed;
  only *publishing* needs an authenticated account, reading doesn't).
- Compares the file listing against what `bands/**/*.yaml` says should be
  there:
  - **MISSING** - referenced by the YAML but not actually on archive.org.
  - **ORPHAN** - present on archive.org but not referenced by any current
    YAML - almost always a leftover from a rename or a replaced
    digitization (see the propagation-lag section below for why these
    can take a while to clear even after a correct `ia delete`).
    Archive.org's own generated derivatives (waveform `.png`/`.afpk`,
    `_meta.xml`, video `.thumbs/`, etc.) are filtered out of this check by
    a conservative pattern list in `_is_ia_generated()` - a real orphan
    always survives that filter, but a new kind of IA-generated file
    might need adding to it someday.
  - **CONTENT MISMATCH** - present under the right name, but an MD5
    comparison against the actual local file shows different bytes. Only
    runs when a full local media tree exists (a maintainer's own
    checkout via `tools/download_archive.py`, not CI - see below).
- Compares `description`/`title`/etc. between the local YAML and what
  archive.org's metadata API actually returns for that item, flagging
  any **METADATA DRIFT**.

## Why a clean publish isn't enough

Two real bugs made "the publish script ran with no errors" a false
signal, both found and fixed alongside this tool (full writeup in issue
#31):

1. **`internetarchive.upload()`'s `metadata` argument only applies at
   item *creation* time** (it says so in its own docstring) - re-running
   `publish_to_archive_org.py` after editing a release's `description`
   silently never pushed that change to an item that already existed.
   Fixed by having `publish_item()` call
   `internetarchive.modify_metadata()` explicitly, every time, regardless
   of whether any files needed uploading.
2. **archive.org is eventually consistent, not immediately consistent.**
   A `ia delete` call can return HTTP 204 (genuine success) while the
   deleted file keeps serving `200 OK` for hours afterward - confirmed
   directly during the incident this tool was built for: the same file
   was still downloadable well over 20 minutes after a successful delete,
   and the item's own archive.org details page showed "currently being
   modified/updated by the task: archive" for the whole stretch. Nothing
   in this repo can make that finish faster - re-running the same delete
   doesn't help, it's a no-op against an already-succeeded request. See
   `CLAUDE.md`'s "Deleting files from archive.org safely" for the
   practical guidance this produced.

Neither of these produces any error from the publish script. The only
way to actually know is to ask archive.org directly and compare - which
is exactly what this tool does, and what running it nightly is for:
catching drift on a schedule instead of waiting for a reader to notice
first.

## Where it runs, and why there

`.github/workflows/verify-archive-org.yml` runs the audit at midnight UTC
(`schedule: cron: "0 0 * * *"`) and on-demand via `workflow_dispatch`, in
GitHub Actions - deliberately not as a scheduled job on the `cherry` VPS,
even though that was the first idea. Two reasons:

- **Cherry has no local media tree either.** Its own site-deploy timer
  already runs with `SITE_SKIP_LOCAL_VALIDATION=1` for exactly this
  reason (see the "Resilience" section of `README.md`) - so running the
  audit there would get no more out of it than CI does. The CONTENT
  MISMATCH check (the one thing that needs local media) only ever runs
  when a maintainer runs the tool by hand on their own machine.
- **CI needs no long-lived credential.** The audit only reads public
  archive.org data, so no archive.org account is involved at all. Filing
  a GitHub issue on failure uses `${{ github.token }}` - scoped
  automatically to just that one workflow run - rather than a personal
  or service token that would otherwise have to sit in a file on a
  public-facing VPS indefinitely.

## What happens when it finds something

The workflow's job fails and, before failing, looks for an existing open
issue labeled `archive-org-audit`:

- **Found one** → comments on it with the run's full output and a link
  to the Action run.
- **None open** → creates a new one, titled with that day's date, labeled
  `archive-org-audit` and `needs-triage`.

This keeps every finding in one running thread per open incident rather
than a fresh issue every night for the same still-unresolved drift.
Issue #31 (the incident this tool was built for) carries the
`archive-org-audit` label for exactly this reason - closing it once
everything it tracks is actually clean will let the next real finding
open a fresh issue rather than reopening old history.

## Running it yourself

```bash
python tools/verify_archive_org.py                         # the whole archive
python tools/verify_archive_org.py --bands <slug> [<slug> ...]   # just these band(s)
```

Worth running by hand right after a `publish_to_archive_org.py` you want
to actually confirm landed, rather than waiting for the next midnight
run. See `tools/TOOLS.md` for the full option reference.
