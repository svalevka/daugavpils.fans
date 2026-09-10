# What's in this folder

Plain-English guide to each file in `tools/`: what problem it solves and
when you'd reach for it. For the technical contract (what the archive
actually looks like), see the root `README.md` and `schema/*.schema.json`.

## `models.py`

**Problem it solves:** defines what a valid band, release, track, and
media file actually look like, in one place.

**How:** Pydantic classes (`MusicGroup`, `MusicAlbum`, `MusicRecording`,
`AudioObject`/`VideoObject`/`ImageObject`, `PropertyValue`, `GroupMember`)
aligned to schema.org vocabulary. This is the working representation that
both `validate.py` and `export_schema.py` are built on.

**When you'd touch it:** you're adding a new field to the archive's data
model (e.g. a new media type, a new metadata attribute). Any change here
needs `export_schema.py` re-run afterwards (see `test_export_schema_drift.py`
below), and if it makes an existing field required, existing `band.yaml`/
`release.yaml` files may need updating too.

## `export_schema.py`

**Problem it solves:** the archive's real, portable contract needs to be
usable from any language, not just Python.

**How:** reads the Pydantic models in `models.py` and writes standalone
JSON Schema files to `schema/band.schema.json` and `schema/release.schema.json`.
Those files -- not this script, not `models.py` -- are what a Go, Node, or
any other non-Python tool should validate against.

**When you'd run it:** after any change to `models.py`. Run it, then
commit the regenerated `schema/*.schema.json` alongside your model change.

## `validate.py`

**Problem it solves:** catching a broken archive entry (missing file,
corrupted checksum, mismatched slug, invalid YAML) before it's trusted --
this is the tool a maintainer or CI run actually calls.

**How:** walks `bands/`, validates every `band.yaml`/`release.yaml`
against the Pydantic models, cross-checks folder-name/slug consistency,
confirms every referenced media file exists, and checks each file's
SHA-256 checksum plus (for audio/video) duration/bitrate.

**When you'd run it:**
- `python tools/validate.py` -- check the archive, fail loudly listing
  every problem found. Run this before opening a PR that touches `bands/`.
- `python tools/validate.py --write` -- also compute and fill in any
  missing checksums/duration/bitrate, rewriting the affected YAML files.
  Run this once after adding a new release's files, before your first
  plain-check run.
- `python tools/validate.py --bands-dir PATH` -- point it at a directory
  other than the repo's own `bands/`. This is what the test suite uses to
  validate temporary fixture trees without touching real archive data.

## `editable_fields.py`

**Problem it solves:** letting the public propose text edits (see the
GitHub issue tracker's #8/#9/#11 work) safely means both the web-facing
form and the script that actually writes to disk need to agree, in one
place, on exactly which `band.yaml`/`release.yaml` fields are safe to
expose - never two separately-maintained lists that can drift apart.

**How:** a plain tuple of `(target, field, kind)` entries and a `lookup()`
function, covering the stage-1 free-text fields on `MusicGroup`,
`MusicAlbum`, `GroupMember`, `MusicRecording`, `ImageObject`, and
`VideoObject`. Deliberately excludes structural identity fields (`slug`,
`byArtist`, `name`) and anything machine-computed by `validate.py --write`
(checksums, `bitrate`, `duration`, `contentUrl`, `encodingFormat`).

**When you'd touch it:** you're adding a new field to the set the public
can propose edits to (e.g. opening up a stage-2/3/4 field). Both
`apply_proposal.py` below and the review app import this same list rather
than each hand-maintaining their own copy.

## `apply_proposal.py`

**Problem it solves:** turning one approved public text-edit proposal
into an actual, safe change to the archive - the one piece of code
authorized to do so, meant to run only inside a GitHub Action after a
curated approver has signed off, never inside the web-facing app itself.

**How:** given a proposal (band/release slug, target, field, the value it
expected to see, the value to write), it independently re-checks the
field against `editable_fields.py`, rejects unsafe/invalid slugs (no path
traversal), refuses to apply if the file's actual current value no longer
matches what the proposal expected (a staleness guard - someone else may
have changed it since), and otherwise mutates the target model in place
and re-dumps the whole file via the exact `yaml.safe_load` -> Pydantic
model -> `model_dump(by_alias=True, exclude_none=True)` ->
`yaml.safe_dump(allow_unicode=True, sort_keys=False)` idiom
`validate.py --write` already uses, keeping diffs minimal. Does not run
`validate.py` itself - the caller (the Action) does that immediately
after as a safety net.

Its band/release-loading and nested-field-navigation functions
(`load_band`, `load_release`, `container_for`) are public and also
imported read-only by `review_app/archive_read.py`, so the "current
value" a submitter sees on the proposal form can never drift from what
this same navigation logic will later compare a proposal's
`original_value` against.

**When you'd run it:**
- `python tools/apply_proposal.py --proposal-file proposal.json` -- apply
  one proposal to the repo's own `bands/`. This is what the
  `apply-proposal.yml` Action invokes.
- `python tools/apply_proposal.py --proposal-file proposal.json --bands-dir PATH`
  -- point it at a directory other than the repo's own `bands/`. This is
  what the test suite uses to validate temporary fixture trees without
  touching real archive data.

## `archive_org.py`

**Problem it solves:** the publish tool and the website both need to agree
on exactly which archive.org item a given band/release lives in, and what
URL a given file has within it - without inventing a new metadata field
just to record that.

**How:** two pure functions deriving an archive.org identifier from the
`slug` fields already in `band.yaml`/`release.yaml` (e.g.
`daugavpils-fans-m-spirit`), plus one building the public download URL for
a file within an item. No I/O, no dependency on the rest of `tools/` or
`webapp/`.

**When you'd touch it:** you're changing the identifier naming convention
itself - a rare, deliberate decision, since existing archive.org items
can't be silently renamed.

## `publish_to_archive_org.py`

**Problem it solves:** getting a band/release's media onto archive.org -
the archive's actual distribution mechanism (see `README.md`) and the only
place the website links to for media (see `webapp/build.py`).

**How:** walks `bands/`, and for each band/release uploads its media via
`internetarchive.upload(..., checksum=True)`, which skips any file already
present with a matching checksum. Uploads go to the item id computed by
`archive_org.py`, with title/creator/date/license metadata drawn from the
band/release YAML. Refuses to run over an archive that hasn't already
passed `validate.py`. After each successful upload, it records that item's
archive.org details-page URL and auto-generated torrent URL into the
band/release YAML's `sameAs` list (skipping any already present) - the same
"compute once, record it" pattern `validate.py --write` uses for checksums,
so `sameAs` reflects a confirmed publish rather than a by-hand-typed guess.
Running it again is always safe: already-recorded URLs and already-matching
files are just skipped.

As a final step, it also uploads **every** `band.yaml`/`release.yaml` (by
then holding their just-recorded `sameAs`) into one more, separate item -
`archive_org.metadata_item_id()`, currently `daugavpils-fans-metadata` -
each at its path relative to `bands/` (e.g. `m-spirit/band.yaml`). This is
the Archive's full metadata backup, independent of GitHub (see
MAINTENANCE.md): one documented, guessable location for the whole thing,
not a copy scattered across every band/release's own item. (An earlier
version did exactly that scattering - guessable only if you already knew
every band/release's item id, which isn't actually recoverable-from-scratch
- and was replaced with this single item after review.)

**When you'd run it:**
- `python tools/publish_to_archive_org.py` - publish anything new or
  changed. Run this after adding a new band/release's media, before the
  next `webapp/build.py`.
- `python tools/publish_to_archive_org.py --bands <slug> [<slug> ...]` -
  same, but only walk the given band(s)/their releases. Since media files
  are gitignored, this script has no other way to know what's already on
  archive.org besides asking it (a checksum check per file), so an
  unscoped run touches every band/release in the archive - one API
  round-trip per existing file - even when only one band changed. Prefer
  this flag whenever you know which band(s) you touched (e.g. after
  `/media-approval` or adding one new release); the metadata backup
  upload still covers every band.yaml/release.yaml regardless of this
  flag.
- `python tools/publish_to_archive_org.py --dry-run` - show what would be
  published without uploading anything (combine with `--bands` to preview
  just those).
- Requires an authenticated `ia` config on the machine running it (`ia
  configure`, once, using the project's archive.org account - not a
  personal one).

## `download_archive.py`

**Problem it solves:** the inverse of `publish_to_archive_org.py` - someone
with a clone of this repo has metadata but no media (audio/image/video is
gitignored, see `README.md`), and today the only way to fill that in is
going band-by-band, release-by-release on archive.org by hand. This is the
tool behind `documentation/BACKUP.md`'s "get your own full copy of the
Archive" instructions.

**How:** walks `bands/`, and for each band/release downloads every media
file it references from that band/release's archive.org item (id computed
by `archive_org.py`) via a plain, unauthenticated HTTPS GET - no
`internetarchive` package or archive.org account needed, since that's only
an upload-time concern. Verifies each downloaded file against the `sha256`
already recorded in its YAML. A file already on disk whose checksum
already matches is left alone rather than re-downloaded, so re-running is
always safe. Never fetches metadata from anywhere - `bands/**/*.yaml`
already on disk (from cloning this repo) is the sole source of truth for
which bands/releases/files exist. A missing item or a checksum mismatch on
one file doesn't stop the run: it's collected and reported in a summary at
the end, with a non-zero exit if anything failed.

**When you'd run it:**
- `python tools/download_archive.py` - fill in every missing/incorrect
  media file under the repo's own `bands/`.
- `python tools/download_archive.py --bands-dir PATH` - point it at a
  directory other than the repo's own `bands/`. This is what the test
  suite uses to validate temporary fixture trees without touching real
  archive data.

## `archive_fixture.py`

**Problem it solves:** every test that exercises `validate.py` needs a
throwaway, minimal archive tree to run it against -- writing that
boilerplate out by hand in every test file would be repetitive and easy
to get subtly wrong.

**How:** a fixture-building library (not a test file itself -- nothing in
here runs under `unittest` directly). `build_valid_archive()` writes a
fully valid one-band/one-release/one-track tree under a temp directory,
with a correct checksum and duration/bitrate already filled in.
`build_archive_missing_av_info()` does the same but with a real
ffmpeg-generated audio file and no checksum/duration/bitrate recorded,
for testing `--write`. `build_archive_with_nested_fields()` builds on
`build_valid_archive()`'s layout but also populates every free-text
field `apply_proposal.py`/`editable_fields.py` can touch (band
description/location/alternateName/genre, one band member, one band
image, release description/genre, the track's alternateName, one release
image), for testing proposal application. `run_validate()` invokes
`validate.py` as a subprocess against a given directory and captures its
output; `run_apply_proposal()` does the same for `apply_proposal.py`,
writing a given proposal dict to a scratch JSON file first.

**When you'd touch it:** you're writing a new test that needs a fixture
variation this file doesn't yet support (e.g. a fixture with two tracks,
or a band with no releases). Extend the builder here rather than
constructing YAML by hand in the test file, so later tests can reuse it.

## `test_editable_fields.py`

**Problem it solves:** `editable_fields.py`'s allowlist is a security
boundary (it's what stands between the public internet and being able to
name arbitrary fields on `band.yaml`/`release.yaml`) - it needs to be
checked against the exact agreed field list, not just "does it import."

**How:** calls `lookup()` directly for every stage-1 field that should be
allowed and asserts each resolves, then calls it for a representative set
of structural/computed fields (`slug`, `name`, `sameAs`, `byArtist`,
`contentUrl`, `identifier`, etc.) and asserts each is refused.

## `test_apply_proposal.py`

**Problem it solves:** `apply_proposal.py` is the one script with
authority to write an approved public proposal into the archive - proving
it applies valid edits correctly *and* safely refuses invalid ones
(disallowed fields, bad slugs, stale proposals) matters more here than
almost anywhere else in `tools/`.

**How:** invokes `apply_proposal.py` as a real subprocess (via
`archive_fixture.run_apply_proposal()`) against a
`build_archive_with_nested_fields()` fixture - the same "invoke the real
CLI, assert on exit code and file contents" idiom
`test_validate_write.py` uses for `validate.py --write`. Covers every
target kind (band/release scalar and list fields, member/track/image
fields nested by index) applying correctly with everything else in the
file left unchanged, plus each rejection case (disallowed field, unknown
or path-traversal slug, stale `original_value`) leaving the file
completely untouched.

## `test_export_schema_drift.py`

**Problem it solves:** someone changes `models.py` but forgets to re-run
`export_schema.py`, so the checked-in `schema/*.schema.json` silently
falls out of sync with the Python models that supposedly generated it.

**How:** regenerates the schema into a temp directory (by calling
`export_schema.main()` with its output directory redirected) and diffs
the result against the checked-in `schema/*.schema.json` files.

**When it's relevant:** it fails on any PR that changes `models.py`
without also regenerating and committing the schema files.

## `test_validate_happy_path.py`

**Problem it solves:** proving the basic contract holds -- a fully valid,
fully populated archive tree passes `validate.py` cleanly, with no false
positives.

**How:** builds a valid fixture via `archive_fixture.build_valid_archive()`
and asserts `validate.py` exits 0 and reports "OK".

## `test_validate_write.py`

**Problem it solves:** `--write`'s computed values (checksum, duration,
bitrate) need to actually be *correct*, not just present -- and running
`--write` twice shouldn't change anything the second time.

**How:** builds a fixture with a real audio file but no checksum/duration/
bitrate (`archive_fixture.build_archive_missing_av_info()`), runs
`validate.py --write`, and checks the persisted values against numbers
computed independently (a separate `ffprobe`/`hashlib` call, not
`validate.py`'s own code). Then runs a plain check afterwards and
confirms it passes with the YAML unchanged.

## `test_validate_failure_modes.py`

**Problem it solves:** it's not enough for `validate.py` to just fail on
a broken archive -- it needs to say *what's* broken, specifically enough
that a maintainer can fix it without reverse-engineering the schema.

**How:** takes a valid fixture and mutates one thing at a time (deletes
the audio file, corrupts its checksum, strips duration/bitrate, mismatches
a slug, breaks schema validation) and asserts both a nonzero exit code
and that the reported message names the actual problem.

## `test_download_archive.py`

**Problem it solves:** `download_archive.py` needs to actually verify what
it downloads, skip what's already correct without touching the network,
and keep going (not abort the whole run) past a single bad file - offline
and deterministically, without hitting real archive.org.

**How:** builds a fixture via `archive_fixture.build_valid_archive()`,
then deletes its audio file to simulate a fresh clone's gitignored media,
and calls `download_archive.py`'s functions directly (not via subprocess)
with `urllib.request.urlretrieve` patched to write fixed bytes or raise -
so no real network call ever happens. Covers: downloading and verifying a
missing file, skipping an already-correct one, reporting (without
aborting) a post-download checksum mismatch and a fetch error, and `main()`
still processing every band and exiting non-zero after one band's fetch
fails while the other's already-verified file needed no network call at
all.

## `requirements.txt`

**Problem it solves:** pins the Python dependencies these tools need
(`pydantic`, `pyyaml`, `unidecode`) so `pip install -r tools/requirements.txt`
reproducibly sets up a working environment.
