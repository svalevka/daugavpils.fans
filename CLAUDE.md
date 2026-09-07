# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues in `svalevka/daugavpils.fans`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

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
