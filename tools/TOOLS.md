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

**How:** walks `bands/`, and for each band/release uploads its media plus
its own `band.yaml`/`release.yaml` as one more file in the same item - a
metadata backup independent of GitHub (see MAINTENANCE.md) - via
`internetarchive.upload(..., checksum=True)`, which skips any file already
present with a matching checksum, so an edited YAML gets re-uploaded next
run just like a changed media file would. Uploads go to the item id
computed by `archive_org.py`, with title/creator/date/license metadata
drawn from the band/release YAML. Refuses to run over an archive that
hasn't already passed `validate.py`. Before uploading, it records that
item's archive.org details-page URL and auto-generated torrent URL into
the band/release YAML's `sameAs` list (skipping any already present) - the
same "compute once, record it" pattern `validate.py --write` uses for
checksums - so the backed-up copy already includes its own `sameAs`, and
`sameAs` itself reflects a confirmed publish rather than a by-hand-typed
guess. Running it again is always safe: already-recorded URLs and
already-matching files are just skipped.

**When you'd run it:**
- `python tools/publish_to_archive_org.py` - publish anything new or
  changed. Run this after adding a new band/release's media, before the
  next `webapp/build.py`.
- `python tools/publish_to_archive_org.py --dry-run` - show what would be
  published without uploading anything.
- Requires an authenticated `ia` config on the machine running it (`ia
  configure`, once, using the project's archive.org account - not a
  personal one).

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
for testing `--write`. `run_validate()` invokes `validate.py` as a
subprocess against a given directory and captures its output.

**When you'd touch it:** you're writing a new test that needs a fixture
variation this file doesn't yet support (e.g. a fixture with two tracks,
or a band with no releases). Extend the builder here rather than
constructing YAML by hand in the test file, so later tests can reuse it.

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

## `requirements.txt`

**Problem it solves:** pins the Python dependencies these tools need
(`pydantic`, `pyyaml`, `unidecode`) so `pip install -r tools/requirements.txt`
reproducibly sets up a working environment.
