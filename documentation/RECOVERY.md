# If you're picking this project up with no archive.org credentials

This document is for exactly one situation: you have this repo (cloned or
forked), but you have no working `ia configure` for the archive.org
account `tools/publish_to_archive_org.py` was built to publish under -
whoever held it is gone, or the login is lost, and you want to keep the
archive going. Nothing here needs help from anyone previously connected
to the project - it only needs this repo and archive.org, both public.
For the everyday, non-recovery workflow (adding a band, running the
tooling normally), see `README.md`.

## First: nothing published is actually lost

Publishing (`tools/publish_to_archive_org.py`) currently runs under one
archive.org account. If whoever holds that account's login disappears or
loses access, **nothing that's already published is lost** - every item
is public and freely downloadable with no login, and the full metadata
(every `band.yaml`/`release.yaml`) is independently recoverable from
either this repo's git history or the `daugavpils-fans-metadata`
archive.org item (see "Metadata schema" in `README.md`), without needing
the old account at all. What breaks is narrower: **nobody can add new
files to an already-existing item, or publish new items under the
`daugavpils-fans-*` id prefix, without that specific account** - item
ownership on archive.org belongs to whichever account created the item,
and doesn't transfer on its own.

## The recovery sequence

1. **Try to recover the old account first.** This is worth attempting
   before anything below, since it's the only path that keeps every
   existing item's id, URL, and already-merged `sameAs` link working
   unchanged: archive.org's own password-reset flow against the account's
   registered email, and if that fails, contacting archive.org support
   directly - point them at this repo's public commit history as evidence
   you're continuing a legitimate, ongoing open-source archive, not
   hijacking an account. Not guaranteed to work, but low-cost to try.

2. **If it's genuinely unrecoverable, create a new archive.org account**
   (free, no affiliation needed) and authenticate it:

   ```bash
   ia configure   # using the new account, once
   ```

   Then give it its own id namespace, rather than trying to reuse
   `daugavpils-fans-*` (uploads to those existing ids will simply be
   rejected - they're owned by the old account). In `tools/archive_org.py`,
   change:

   ```python
   ITEM_PREFIX = "daugavpils-fans-<something-new>"
   ```

   `band_item_id()`, `release_item_id()`, and `metadata_item_id()` all
   derive from that one constant, so this one-line change is all that's
   needed for every future publish - new bands, existing ones, the
   metadata backup, everything - to land under the new prefix.

3. **Restore every band/release's actual media files locally.** A fresh
   clone of this repo has metadata (`bands/**/*.yaml`) but no media -
   audio/image/video files are gitignored by design (see "What's
   actually portable here" in `README.md`), so a new maintainer's
   checkout starts with none. Every file is still public on archive.org
   under the *old* prefix, at a URL fully computable from the YAML you
   already have (`tools/archive_org.py`'s `archive_org_url()`, using the
   *old* `daugavpils-fans` prefix for this one-time restore, before step
   2's edit takes effect for new publishes). This one-off script walks
   `bands/`, and downloads each referenced file to exactly where its
   `contentUrl` says it belongs:

   ```bash
   python3 - <<'EOF'
   import sys, urllib.request
   from pathlib import Path
   import yaml

   sys.path.insert(0, "tools")
   from archive_org import archive_org_url

   # Deliberately hardcoded to the *old* prefix, not tools/archive_org.py's
   # ITEM_PREFIX - by the time this runs, that constant may already have
   # been changed to the new prefix per step 2 above.
   OLD_PREFIX_BAND_ITEM = lambda slug: f"daugavpils-fans-{slug}"
   OLD_PREFIX_RELEASE_ITEM = lambda b, r: f"daugavpils-fans-{b}-{r}"

   def fetch(item_id, base_dir, media_items):
       for m in media_items:
           url = archive_org_url(item_id, m["contentUrl"])
           dest = base_dir / m["contentUrl"]
           dest.parent.mkdir(parents=True, exist_ok=True)
           print(f"{url} -> {dest}")
           urllib.request.urlretrieve(url, dest)

   for band_dir in Path("bands").iterdir():
       if not (band_dir / "band.yaml").exists():
           continue
       band = yaml.safe_load((band_dir / "band.yaml").read_text())
       fetch(OLD_PREFIX_BAND_ITEM(band["slug"]), band_dir, band.get("image", []) + band.get("video", []))
       for release_dir in band_dir.iterdir():
           release_yaml = release_dir / "release.yaml"
           if not release_yaml.exists():
               continue
           release = yaml.safe_load(release_yaml.read_text())
           media = [t["audio"] for t in release["track"]] + release.get("image", []) + release.get("video", [])
           fetch(OLD_PREFIX_RELEASE_ITEM(band["slug"], release["slug"]), release_dir, media)
   EOF
   ```

   (This is a one-off recovery script, not a permanent tool - it exists
   here specifically so this step never depends on anything beyond this
   file plus a Python interpreter.)

4. **Validate, then do one full republish of the whole archive under the
   new prefix** - not a partial/just-the-one-band publish:

   ```bash
   python tools/validate.py                          # confirm the restore worked: every
                                                       # checksum in the YAML should still match
   python tools/publish_to_archive_org.py --dry-run   # sanity check - every band/release should
                                                       # show up as new under the new prefix
   python tools/publish_to_archive_org.py             # the one-time full migration
   ```

   Do the whole archive in one pass here, even though only one band may
   actually need a fix. Two ways to handle this were considered:

   - **Scope the publish to just the one band that needs fixing** (e.g.
     `python tools/publish_to_archive_org.py --bands-dir /path/to/just-that-band`),
     to avoid re-uploading every other band's media under the new
     account. The problem: `publish_to_archive_org.py` bundles a full
     metadata backup (every `band.yaml`/`release.yaml` it finds under
     `--bands-dir`) into the new `<new-prefix>-metadata` item on every
     run - so a scoped run's metadata backup would cover only that one
     band, not the whole archive, breaking the "one guessable place
     recovers everything" guarantee this step exists to re-establish.
     There's no separate flag today to run just the metadata-backup step
     against the full tree independently of the per-band media upload,
     so avoiding that gap this way would mean an extra manual step (and
     more moving parts for someone doing this with zero prior context).
   - **Publish the entire archive under the new prefix in one pass**
     (chosen): costs a one-time full re-upload of every band's media
     (even the ones that didn't change), but needs no extra steps or
     flags, and the new `<new-prefix>-metadata` item is guaranteed
     complete from the very first run. Simpler and harder to get wrong
     wins here, since this is a rare, one-time migration, not a routine
     operation.

   The checksums already in each YAML are unchanged (the restored files
   are byte-identical to what was already recorded), so this is a
   straightforward bulk upload, not a re-validation from scratch.

5. **From here on, nothing else is special** - publishing works exactly
   as described in "Tooling usage" in `README.md`. The old items stay
   live under the old prefix, permanently and publicly downloadable, as a
   frozen historical record from before the migration; each YAML's
   `sameAs` keeps both the old and new URLs, since
   `publish_to_archive_org.py` only ever adds to that list, never removes
   from it.

In short: an inaccessible account can't destroy anything already
published, but it does permanently freeze *that* account's ability to
add to it - continuing forward means one deliberate, one-time migration
to a new account and id prefix, not recovering write access to items you
can't regain the login for.
