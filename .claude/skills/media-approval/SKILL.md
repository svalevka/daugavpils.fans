---
name: media-approval
description: Find approved photo/video submissions waiting in the review-app queue and finish publishing them by hand (the repo's media pipeline is deliberately manual, not CI-driven) - retrieve the file, add the YAML entry, validate, upload to archive.org, commit/push, mark it published, redeploy, and confirm it's live. Use when the user says "publish this photo/video", "process pending media", "check media submissions", or invokes /media-approval.
---

Approving a photo/video on `review.daugavpils.fans/dashboard` never touches
git or archive.org (GitHub issue #21) - it only moves the row to "approved,
awaiting publish". Finishing it is a manual job normally done by hand
by whoever holds archive.org credentials; this skill does that job.
See `documentation/ARCHITECTURE.md` and `webapp/deploy/README.md`'s
"Photo/video proposals" section for the authoritative description - if
either has changed since this skill was written, trust them over this file.

Two separate proposal queues live in the same `review.db`: `media_proposals`
(this skill's job) and `proposals` (text edits, applied automatically by
`.github/workflows/apply-proposal.yml` on approval - not this skill's job,
but see "Also check text proposals" below for why it's worth a glance).

## 0. Prerequisites (check once, not per run)

- `ssh cherry` works (see `~/.ssh/config`).
- `.venv/bin/python` in the repo root has `tools/requirements.txt` installed
  (`internetarchive`, `pyyaml`, `pydantic`).
- `~/.config/internetarchive/ia.ini` holds the project's (not personal)
  archive.org credentials.
- `cwebp` is on PATH (`brew install webp`) - every existing `image:` entry
  in this repo is `.webp`; convert new photos to match, don't leave them
  as the submitted jpg/png.
- Current branch is `main`, clean, up to date (`git fetch && git status`).

## 1. Find the work

Query the DB directly over SSH - logging into `/dashboard` as an approver
needs a magic-link email, which isn't automatable, but reading/writing
`review.db` via `docker compose exec` is the same data the dashboard reads:

```bash
ssh cherry "cd /opt/daugavpils-fans && sudo docker compose exec -T review-app python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('/data/review.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT * FROM media_proposals WHERE status IN ('pending','approved') ORDER BY created_at\").fetchall()
for r in rows:
    print(dict(r))
EOF"
```

- **`status = 'pending'`**: nobody has curated this yet - deciding
  approve/reject is an editorial judgment call (is this really the band,
  is the photo appropriate, does the caption make sense) that belongs to
  a human on the dashboard. List these for the user; don't auto-approve.
- **`status = 'approved'`**: ready to finish. Process each with steps 2-8.

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

## 2. Retrieve and sanity-check the file

```bash
ssh cherry "sudo cat /opt/daugavpils-fans/review-app-data/uploads/<stored_filename>" > /tmp/<stored_filename>
```
(root owns it; a plain `scp` as your own user will fail - `sudo cat` over
the existing ssh session is the practical equivalent.)

**Read/view the file before publishing anything public** - confirm it
actually matches the submitted caption and band. This is a public,
permanent archive.org upload; a wrong photo is not cheaply undone.

## 3. Place it and write the YAML entry

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

## 4. Validate

```bash
.venv/bin/python tools/validate.py --write
.venv/bin/python tools/validate.py   # confirm clean
```

## 5. Publish to archive.org - run this exactly once

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

## 6. Commit and push directly to `main`

No PR/merge step for this repo (standing preference) - commit and push
straight to `main`, one commit per proposal, in the same descriptive style
as prior media commits (`git log --oneline -- bands/` for examples):

```
Publish media proposal #<id>: <short description> for <Band Name>
```

If the push is rejected (non-fast-forward - likely if a text proposal
landed via CI while you were working), `git fetch && git rebase
origin/main` and push again; don't force-push.

## 7. Mark it published in the queue

This is bookkeeping, not a curation decision - it's what the dashboard's
"Mark published" button does, replicated directly since that button also
requires a logged-in approver session:

```bash
ssh cherry "cd /opt/daugavpils-fans && sudo docker compose exec -T review-app python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('/data/review.db')
cur = conn.execute(
    \"UPDATE media_proposals SET status = 'published', published_at = datetime('now') \"
    \"WHERE id = <id> AND status = 'approved'\"
)
conn.commit()
print('rows updated:', cur.rowcount)   # must be 1
EOF"
```

Then delete the original upload (`review-app-data/uploads/` isn't backed
up, and the dashboard's own button deletes it at this point too):

```bash
ssh cherry "sudo rm -fv /opt/daugavpils-fans/review-app-data/uploads/<stored_filename>"
```

**Confirm with the user before running that delete** - it's a destructive,
irreversible action on the only copy of the original upload, and the
permission system will likely ask anyway.

## 8. Redeploy and verify it's actually live

The VPS's `daugavpils-fans-sync.timer` polls `main` every 5 minutes and
redeploys on its own - no action is strictly required. To confirm sooner
instead of waiting:

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
