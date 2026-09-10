# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Engineering priorities

When implementing new features, prioritize stability, consistency, and
security over speed and complexity. This is an archive, not a live SaaS
music service - there's no product pressure to ship fast or cleverly;
getting it right and keeping it boring/predictable matters more than
optimizing for speed or building something clever. When those goals
conflict, default to the simpler, more conservative choice, and say so.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues in `svalevka/daugavpils.fans`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Where documentation lives

`README.md` and `MAINTENANCE.md` stay at the repo root - they're the two
audience-facing entry points (technical contributors, and listeners/
non-technical readers, respectively) and people expect to find them
there. `CONTEXT.md` and `docs/adr/` also stay at the root, per "Domain
docs" above - don't move those into `documentation/` either. Everything
else documentation-shaped (runbooks, recovery procedures, and similar)
goes under `documentation/` - e.g. `documentation/RECOVERY.md`. When
adding a new doc, ask which of these three buckets it belongs to rather
than defaulting to the root.

### Tools inventory

`tools/TOOLS.md` describes, in plain English, what each file in `tools/`
is for, what problem it solves, and when/how to use it. Whenever you add,
remove, or meaningfully change the purpose of a file in `tools/`, update
`tools/TOOLS.md` in the same change.

### Band/release descriptions (testimony)

`description`/`description_en` on a `band.yaml`/`release.yaml` are
first-hand testimony (biographies, provenance notes), not editorial
copy. When a contributor supplies this text, use it in full - never
summarize, condense, or paraphrase it down without their explicit
approval first. If a full transcription seems too long, ask; don't
default to shortening it.

### Where metadata backups go on archive.org

There is exactly **one** place a `band.yaml`/`release.yaml` is ever
uploaded to on archive.org: the single consolidated item at
`archive_org.metadata_item_id()` (currently `daugavpils-fans-metadata`),
via `tools/publish_to_archive_org.py`'s `publish_metadata_bundle()` -
run the whole script, or call that function directly. **Never**
`ia.upload()` a `band.yaml`/`release.yaml` onto a band/release's own
media item (e.g. `daugavpils-fans-<slug>`) - that item holds only
audio/image/video, per `publish_item()`. This was tried once (see
git history: "Consolidate metadata backup into one archive.org item")
and reverted because it meant recovering the Archive's metadata from
archive.org alone required already knowing every band/release's item
id. Mixing the two patterns even for a single file re-creates that
exact problem. If you (the agent) ever upload a metadata file directly
via `ia.upload()` outside `publish_metadata_bundle()`, treat it as a
bug: delete the stray copy from the media item immediately.

### Deleting files from archive.org safely

`ia delete <item> <file>` printing "deleting: X and all derivative
files." is **not proof the file is gone** from what a reader can
actually fetch. The API call itself has been confirmed to genuinely
succeed (HTTP 204 from `item.get_file(...).delete()`), but the deleted
file kept serving `200 OK` from `archive.org/download/<item>/<file>` -
and stayed that way through `ia list` and a second identical delete
pass, both taken well over 20 minutes apart. This looks like slow
propagation across archive.org's mirror/CDN layer rather than the
delete actually failing - **re-running the same `ia delete` command
again does not speed this up**, it's just a harmless no-op against an
already-succeeded delete.

**Always verify with a real fetch, not just `ia list`** (which reads the
same lagging metadata):

```bash
curl -sL -o /dev/null -w '%{http_code}\n' "https://archive.org/download/<item>/<file>"
```

`404` (or a redirect that ends in one) confirms it's actually gone;
`200` means it's still propagating. If the delete API call itself
returned success (204) and it's still `200` shortly after, that's
expected lag, not a bug to fix by retrying - tell the user cleanup is
submitted and may take a while to fully propagate, rather than claiming
it's done or looping on redundant delete calls.

Other things worth knowing before deleting anything on an archive.org
item:

- `ia delete <item> --all` cannot remove `_meta.sqlite`, `_files.xml`, or
  `_meta.xml` (403 Access Denied - "use metadata api instead"). An item
  can never be fully purged this way, only emptied of its actual
  audio/image/video/torrent content - it'll still exist as a bare shell.
  That's expected, not a failure to fix.
- Files deleted via the API land in a `history/files/` folder on
  archive.org's side rather than being purged outright, but there's no
  ordinary way to restore from there - treat every deletion as
  effectively permanent when deciding whether to do it.
- Because of both points above, prefer the least destructive option that
  satisfies the actual goal: removing a band/release from `bands/**/*.yaml`
  (so the site and `tools/publish_to_archive_org.py` stop referencing it)
  is often enough on its own - an orphaned, unlinked archive.org item
  sitting there costs nothing. Only delete the underlying files/item
  when someone has actually asked for that specifically.
- Confirm the exact scope (which items, whole item vs. specific stale
  files, full delete vs. leave-orphaned) with the user before running
  any `ia delete`, the same as any other hard-to-reverse action on a
  public/shared system.

### SSH to `cherry` - avoid rapid-fire connections

Many `ssh cherry ...` calls in quick succession (e.g. fetching a batch of
review-app upload files one `ssh` per file) has been observed to trigger
"Connection refused" shortly after - looks like an IP mismatch but the
Hetzner firewall allowlist is usually still correct (worth checking with
`public-ip-reset` regardless, it's cheap to rule out). It resolves on its
own after a short wait (well under 10 minutes). Space out sequential `ssh
cherry` calls (a couple of seconds each) rather than firing them back to
back, and if it happens anyway, just retry after a short backoff instead
of assuming the firewall/IP allowlist needs fixing.

When fetching more than a couple of files from `cherry` (e.g. several
`review-app-data/uploads/` files for `/media-approval`), don't loop
`ssh cherry "sudo cat ..."` once per file - that's exactly the rapid-fire
pattern that triggers the refusal. Fetch them all in **one** connection
instead, e.g. tar them up remotely and stream the tar back:

```bash
ssh cherry "cd /opt/daugavpils-fans/review-app-data/uploads && sudo tar -cf - file1 file2 file3" > /tmp/batch.tar
tar -xf /tmp/batch.tar -C /tmp/wherever
```

### Downloading from Bandcamp - the format `<select>` needs a real keystroke

On a Bandcamp `/download` page, setting the format `<select>`'s `.value`
via JavaScript and dispatching a synthetic `change` event does **not**
update the actual download link - it still points at whatever format was
selected by the initial page load (usually the lowest-quality option,
e.g. MP3 V0 instead of 320). This produced a wrong-bitrate download that
looked successful (right filename, plausible size) and wasn't caught
until checked with `ffprobe`. The fix: interact with the select the way a
real user would - click it, then send an actual `Down`/`Up` arrow key
press and `Return` (browser automation `computer` tool, not a JS
`dispatchEvent`) - which does trigger Bandcamp's own handler and updates
the download link/filesize shown on the page. Verify the resulting file's
real bitrate with `ffprobe` afterward regardless; don't trust the
filename or the page's displayed size alone.
