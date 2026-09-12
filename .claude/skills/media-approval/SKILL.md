---
name: media-approval
description: Find approved photo/video submissions waiting in the review-app queue and finish publishing them - dispatch the automated upload pipeline (apply-media-proposal.yml), verify it actually landed, and fall back to a manual publish only if that Action fails. Use when the user says "publish this photo/video", "process pending media", "check media submissions", or invokes /media-approval.
---

Approving a photo/video on `review.daugavpils.fans/dashboard` never touches
git or archive.org by itself (GitHub issue #21) - it only moves the row to
"approved, ready to publish". Finishing it normally means dispatching
`apply-media-proposal.yml` (GitHub issue #36), the same automated pipeline
the dashboard's own "Upload" button triggers: it fetches the file and
metadata, uploads to archive.org, writes the YAML entry, and commits/pushes
to `main` on its own. This skill does that, verifies it actually landed,
and only falls back to the old by-hand process (steps 2-8 below) if the
Action itself fails. See `documentation/ARCHITECTURE.md` and
`webapp/deploy/README.md`'s "Photo/video proposals" section for the
authoritative description - if either has changed since this skill was
written, trust them over this file.

Two separate proposal queues live in the same `review.db`: `media_proposals`
(this skill's job) and `proposals` (text edits, applied automatically by
`.github/workflows/apply-proposal.yml` on approval - not this skill's job,
but see "Also check text proposals" below for why it's worth a glance).

## 0. Prerequisites (check once, not per run)

- `ssh cherry` works (see `~/.ssh/config`) - needed to read the queue
  directly from `review.db` (logging into `/dashboard` needs a magic-link
  email, which isn't automatable).
- `gh` is authenticated for this repo (`gh auth status`) - needed to
  dispatch and watch `apply-media-proposal.yml`.
- Current branch is `main`, clean, up to date (`git fetch && git status`)
  - mainly so a manual fallback (step 2b) starts from the right place.
- Only needed for the manual fallback (step 2b), not the normal path:
  `.venv/bin/python` with `tools/requirements.txt` installed, project
  archive.org credentials in `~/.config/internetarchive/ia.ini`, and
  `cwebp` on PATH (`brew install webp`).

## 1. Find the work

Query the DB directly over SSH:

```bash
ssh cherry "cd /opt/daugavpils-fans && sudo docker compose exec -T review-app python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('/data/review.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT * FROM media_proposals WHERE status IN ('pending','approved','publish_failed') ORDER BY created_at\").fetchall()
for r in rows:
    print(dict(r))
EOF"
```

- **`status = 'pending'`**: nobody has curated this yet - deciding
  approve/reject is an editorial judgment call (is this really the band,
  is the photo appropriate, does the caption make sense) that belongs to
  a human on the dashboard. List these for the user; don't auto-approve.
- **`status = 'approved'` or `'publish_failed'`**: ready to (re-)publish.
  Process each with step 2.

### Also check text proposals while you're in there

Approving a *text* proposal (the `proposals` table) dispatches
`apply-proposal.yml` with just its id - the fast path, applying it near
instantly. As of GitHub issue #28 the same workflow also runs on a
15-minute `schedule` as a self-healing sweep (it applies every proposal
still at `status='approved'`, via `GET /api/proposals/approved`), so a
dispatch that GitHub's concurrency-group queue evicts before it ever runs
(only one *running* + one *queued* run are kept per group - a burst of
approvals can silently drop an older queued one) now gets caught within
15 minutes instead of sitting stuck forever. A proposal that genuinely
*fails* to apply (bad content, a push race that outlasts its 5 retries)
still lands at `status='apply_failed'` and is deliberately **not** swept
- that needs a human look, not an automatic retry loop. Cheap check while
you're already looking at the DB, for either case:

```bash
ssh cherry "cd /opt/daugavpils-fans && sudo docker compose exec -T review-app python3 -c \"
import sqlite3
conn = sqlite3.connect('/data/review.db')
conn.row_factory = sqlite3.Row
for r in conn.execute(\\\"SELECT id, field, status, applied_at, github_run_id, apply_error FROM proposals WHERE status IN ('approved','apply_failed') AND applied_at IS NULL\\\"):
    print(dict(r))
for r in conn.execute(\\\"SELECT id, field, status, applied_at, apply_error FROM proposals WHERE status = 'apply_failed'\\\"):
    print(dict(r))
\""
```

An `apply_failed` row needs the underlying cause fixed first (or, if the
failure really was just a transient race the retry loop didn't cover,
reset it - `UPDATE proposals SET status='approved' WHERE id=<id> AND
status='apply_failed'` - so either the next sweep or a manual dispatch can
pick it back up). Either way, don't trust the Action log's "Report
result" text as the signal - its script source always contains both the
success *and* failure `PAYLOAD=` lines regardless of which branch
actually ran, so verify against the DB instead:

```bash
gh workflow run apply-proposal.yml -f proposal_id=<id> --ref main   # or leave -f blank to sweep everything approved
# wait, then:
gh run list --workflow=apply-proposal.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch <that id> --exit-status
ssh cherry "cd /opt/daugavpils-fans && sudo docker compose exec -T review-app python3 -c \"
import sqlite3; conn = sqlite3.connect('/data/review.db'); conn.row_factory = sqlite3.Row
print(dict(conn.execute('SELECT status, applied_at, apply_error FROM proposals WHERE id = <id>').fetchone()))
\""
```

`status = 'applied'` and `apply_error IS NULL` is the only real confirmation.

## 2. Dispatch the automated upload pipeline (the normal path)

For each `approved`/`publish_failed` media proposal, this is the same
action as clicking "Upload" on the dashboard:

```bash
gh workflow run apply-media-proposal.yml -f proposal_id=<id> --ref main   # or leave -f blank to sweep everything approved
gh run list --workflow=apply-media-proposal.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch <that id> --exit-status
```

`tools/apply_media_proposal.py` (run by that Action) fetches the file and
metadata from `review_app`'s callback API, derives a clean filename,
computes its `sha256` (and, for video, `duration`/`bitrate` via `ffprobe`),
appends the `image`/`video` entry to `band.yaml`/`release.yaml`, uploads
the file to the band/release's archive.org item, syncs metadata, and
commits/pushes to `main` - then reports success/failure back to
`review_app`. Verify against the DB, not just the Action's exit status
(same reasoning as the text-proposal check above):

```bash
ssh cherry "cd /opt/daugavpils-fans && sudo docker compose exec -T review-app python3 -c \"
import sqlite3; conn = sqlite3.connect('/data/review.db'); conn.row_factory = sqlite3.Row
print(dict(conn.execute('SELECT status, published_at, publish_error, github_run_id FROM media_proposals WHERE id = <id>').fetchone()))
\""
```

`status = 'published'` with `publish_error IS NULL` is the only real
confirmation - the Action having reported "Upload complete" or exited 0
is not, since it explicitly reports its own failures back too. On
success, `review_app` has already deleted the original from
`review-app-data/uploads/` and closed the row - there's nothing left to
do. Confirm the file is actually live before telling the user it's done:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -L "https://archive.org/download/daugavpils-fans-<band-slug>/media/<filename>"   # expect 200
curl -s --compressed "https://daugavpils.fans/bands/<band-slug>/" | grep -F "<filename>"   # expect a hit
```

(the filename `apply_media_proposal.py` chose is in the commit it pushed -
`git log --oneline -- bands/<band-slug>/` - or in the YAML entry itself.)

If `status` came back `publish_failed` (or the Action run failed outright),
read `publish_error`/the Action log to understand why before falling back
to step 2b - a bad submission (corrupt file, unsupported format) should be
rejected on the dashboard instead of force-published by hand, whereas a
transient failure (network flake, archive.org rate limit) is usually worth
just re-running step 2 once rather than jumping to a manual publish.

## 2b. Manual fallback - only if step 2's Action genuinely can't do it

Everything below is the pre-#36 by-hand process, kept for the cases the
automated pipeline can't handle itself (e.g. a format `ffprobe`/the MIME
allowlist rejects, or archive.org access from Actions is unavailable).
This is also what the dashboard's own "Mark published manually" button
assumes a curator has already done by hand.

### Retrieve and sanity-check the file

```bash
ssh cherry "sudo cat /opt/daugavpils-fans/review-app-data/uploads/<stored_filename>" > /tmp/<stored_filename>
```
(root owns it; a plain `scp` as your own user will fail - `sudo cat` over
the existing ssh session is the practical equivalent.)

**Read/view the file before publishing anything public** - confirm it
actually matches the submitted caption and band. This is a public,
permanent archive.org upload; a wrong photo is not cheaply undone.

### Place it and write the YAML entry

- Band-level media (no `release_slug`): `bands/<band_slug>/media/`.
- Release-level media (`release_slug` set): the equivalent `media/`
  subfolder next to that `release.yaml` - there's no existing example of
  this in the repo yet, so if you hit one, follow the band-level pattern
  and flag it in your summary rather than assuming silently.
- Pick a descriptive, slug-style filename (`<band-slug>-<short-context>.webp`),
  not the random `stored_filename`.
- Images: `cwebp -q 90 <input> -o <target>.webp` - every current `image:`
  entry in the repo is webp; match it.
- Videos: keep the submitted format (existing `video:` entries are all mp4).
- Add the entry to `band.yaml`/`release.yaml` under `image:`/`video:`,
  matching an existing entry's shape exactly (`@type`, `caption`,
  `caption_en`, `contentUrl`, `depicts: []`, `encodingFormat`) - leave
  `identifier:` (sha256) out, `validate.py --write` fills it in.
- `caption`: use the submitter's caption verbatim (this is contributor
  testimony, like `description`/`description_en` per this repo's
  conventions - don't paraphrase or shorten it).
- `caption_en`: your own faithful translation. If the submitter left no
  caption at all, don't invent one - ask the user for one instead.

### Validate

```bash
.venv/bin/python tools/validate.py --write
.venv/bin/python tools/validate.py   # confirm clean
```

### Publish to archive.org - run this exactly once

```bash
.venv/bin/python tools/publish_to_archive_org.py --bands <band-slug> [<band-slug> ...]
```

Scope it to the band(s) this run actually touched with `--bands` - it
skips the archive.org checksum walk over every other band/release, which
is the expensive part (one API round-trip per existing file). Omit
`--bands` only when you genuinely mean to publish everything (e.g.
verifying nothing else drifted); that walks the *entire* `bands/` tree
and re-verifies every already-published file's checksum, so it takes a
few minutes even for one new photo. Either way, **do not re-run it "to
check" or because a `tail`/timeout hid the output.**
Two back-to-back runs have been observed to make the *second* one
re-upload files that had just been uploaded seconds earlier, because
archive.org's own item metadata hadn't finished settling yet when the
second run's checksum check queried it - so a redundant second run isn't
just wasted time, it can turn a 30-second image publish into a multi-minute
re-upload of unrelated existing videos.

Run it in the background with a long timeout from the start
(`run_in_background: true`, or a `Monitor` polling for the `Done.` line in
a log file) rather than trying it in the foreground first - large existing
audio/video already in an item can push a single run past a couple of
minutes even when nothing meaningful changed.

### Commit and push directly to `main`

No PR/merge step for this repo (standing preference) - commit and push
straight to `main`, one commit per proposal, in the same descriptive style
as prior media commits (`git log --oneline -- bands/` for examples):

```
Publish media proposal #<id>: <short description> for <Band Name>
```

If the push is rejected (non-fast-forward - likely if a text proposal
landed via CI while you were working), `git fetch && git rebase
origin/main` and push again; don't force-push.

### Redeploy and verify it's actually live - before doing anything else below

Don't wait for the VPS's `daugavpils-fans-sync.timer` (polls `main` every
5 minutes) - force it immediately after pushing:

```bash
ssh cherry "sudo systemctl start daugavpils-fans-sync.service"
```

Then poll (don't guess) until it's caught up to your pushed commit:

```bash
ssh cherry "cat /opt/daugavpils-fans/.last-deployed-sha"   # should become your new commit sha
```

Finally verify the media itself, not just the deploy:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -L "https://archive.org/download/daugavpils-fans-<band-slug>/media/<filename>"   # expect 200
curl -s --compressed "https://daugavpils.fans/bands/<band-slug>/" | grep -F "<filename>"   # expect a hit (no /ru//en/ prefix - check the page's actual <a href> links if unsure)
```

archive.org can lag a minute or two after upload before the file resolves
- retry rather than concluding failure immediately. Report the live
URL(s) back to the user once confirmed.

**Do this before marking it published, not after.** Marking a proposal
published and offering to delete its only-copy original is irreversible
bookkeeping - do it only once you've actually confirmed the photo/video is
live on the site, not on the assumption that publish+push+deploy will just
work out.

### Mark it published in the queue

Only after the live check above. This is bookkeeping, not a curation
decision - it's what the dashboard's "Mark published manually" button
does, replicated directly since that button also requires a logged-in
approver session:

```bash
ssh cherry "cd /opt/daugavpils-fans && sudo docker compose exec -T review-app python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('/data/review.db')
cur = conn.execute(
    \"UPDATE media_proposals SET status = 'published', published_at = datetime('now') \"
    \"WHERE id = <id> AND status IN ('approved', 'publish_failed')\"
)
conn.commit()
print('rows updated:', cur.rowcount)   # must be 1
EOF"
```

Then, only once the file is confirmed live and marked published above,
offer to delete the original upload (`review-app-data/uploads/` isn't
backed up, and the dashboard's own button deletes it at this same point
too):

```bash
ssh cherry "sudo rm -fv /opt/daugavpils-fans/review-app-data/uploads/<stored_filename>"
```

**Confirm with the user before running that delete** - it's a destructive,
irreversible action on the only copy of the original upload, and the
permission system will likely ask anyway. Don't even raise the question
of deleting it until the live check above has actually passed.
