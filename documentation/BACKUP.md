# Getting your own full copy of the Archive

This document is for anyone who wants a complete, checksum-verified local
copy of the Archive - metadata *and* media - independent of daugavpils.fans,
GitHub, or anyone currently maintaining this project staying around. See
`CONTEXT.md` for what "Archive" means precisely: the git-tracked metadata
(`bands/**/*.yaml`) plus the audio/image/video files it describes.

This isn't a recovery procedure and doesn't require anything special - it's
the same thing a curious listener, a researcher, or anyone doing their own
offline backup can run at any time, using nothing but a public git clone
and public archive.org downloads. (If instead you're trying to regain the
ability to *publish* to archive.org after losing account access, see
`RECOVERY.md` - a different situation, with its own procedure.)

## Why this needs two steps

Metadata (band/release names, tracklists, biographies, checksums - see
"Metadata schema" in `README.md`) is git-tracked and comes with the repo
for free. Media (the actual audio/image/video files) is deliberately kept
*out* of git (see `.gitignore` and "What's actually portable here" in
`README.md`) and lives only on archive.org, split across one item per band
and one item per release. So a plain `git clone` gets you every band and
release's full metadata, but none of the actual recordings or photos - you
need a second step to fill those in from archive.org.

## Step by step

1. **Clone this repo** (or download it as a ZIP from GitHub's "Code" button
   if you don't have git):

   ```bash
   git clone https://github.com/svalevka/daugavpils.fans.git
   cd daugavpils.fans
   ```

   You now have every `band.yaml`/`release.yaml` - names, tracklists,
   biographies, member lists, checksums - but no media files yet.

2. **Install the tooling's dependencies** (once):

   ```bash
   pip install -r tools/requirements.txt
   ```

3. **Run the download script**, from the repo root:

   ```bash
   python tools/download_archive.py
   ```

   This walks every band and release under `bands/`, and for each one
   downloads its media from that band/release's own archive.org item -
   audio tracks, band photos, release cover art, video - straight over
   plain HTTPS. No archive.org account, API key, or login is needed: every
   file it fetches is already public. As each file lands, the script
   checks it against the `sha256` checksum already recorded in the YAML,
   so you know the copy you end up with is byte-identical to what was
   actually published, not silently corrupted or truncated.

That's it. When it finishes with `OK: every referenced media file is
present on disk and checksum-verified.`, `bands/` on your machine holds
the complete Archive - the same tree this project itself works from,
including every audio file, photo, and video, all checksum-verified.

## If something fails partway through

The script doesn't stop at the first problem. If one file is missing from
archive.org, or fails its checksum check after downloading, that failure
is recorded and the script moves on to the next file - you get everything
that *can* succeed in one run, rather than restarting from scratch over a
single bad file. At the end it prints a summary of anything that failed,
and exits with a non-zero status if there was any.

It's also safe to just run it again: a file already on disk whose checksum
already matches is left alone, not re-downloaded, so re-running after a
partial failure (or an interrupted run - a dropped connection, a closed
laptop) only fetches what's still missing or still wrong.

## How big is this, and how long does it take

The whole point of this being one command is that you don't need to think
about this in advance - just run it and let it work. That said: this is
every audio file, photo, and video across every band and release in the
Archive, at full quality, so expect a real download (currently several
gigabytes, and it only grows as more music is added) rather than a quick
one. There's no way to fetch just one band or release yet - it's the whole
Archive or nothing.

## What you can do with the result

Once you have it, this local `bands/` tree *is* a fully valid, standalone
copy of the Archive - not tied to this repo staying online, to GitHub, or
to archive.org staying reachable at that moment. You can:

- Just keep it as a backup.
- Verify it stays correct over time by re-running
  `python tools/validate.py` against it (same checksum/schema checks this
  project runs on every change).
- Build and browse the Site locally from it: see "Tooling usage" in
  `README.md` for `webapp/build.py`.
