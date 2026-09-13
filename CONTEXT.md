# Daugavpils Music Archive

A portable archive of Daugavpils music-scene recordings and metadata, plus a public website that presents that archive to listeners.

## Language

**Archive**:
The git-tracked metadata (`bands/**/*.yaml`) plus the locally-held audio/image/video files it describes, validated against `schema/`. Portable and hosting-independent — it doesn't depend on this repo, any one host, or the website being online.
_Avoid_: "the repo", "the data" (too vague — say Archive when you mean the metadata+media combination)

**Archive Release**:
Publishing the Archive's media to archive.org — one item per `band.yaml`, one per `release.yaml` (see `tools/archive_org.py`, `tools/publish_to_archive_org.py`), checksum-verified against the `sha256` values already recorded in `release.yaml`. Each item is a durable, checksum-verifiable copy of that band/release's media, independent of this repo or any host we operate, and archive.org auto-generates a torrent for it. This is the archive's own distribution mechanism.
_Avoid_: "release" alone — ambiguous with the `release.yaml`/`MusicAlbum` sense (an album). When the archive.org-publishing sense is meant, say Archive Release.

**Site**:
The public website that lets listeners browse bands, read bios, view photos, and play tracks — streaming/displaying media directly from archive.org rather than hosting a copy of it. A separate, additional consumer of the Archive — not a replacement for the Archive Release, and not required for the Archive to be useful.
_Avoid_: "webapp" when talking about the concept (fine as the folder name); "the frontend"

**Site Build**:
The static HTML produced by `webapp/build.py` from a fully-validated Archive tree whose media is already published to archive.org — no media is copied into it. Deployed two independent ways (see ADR-0002): manually rsynced to the primary Hetzner server (daugavpils.fans), where it re-validates the real local media tree every time (`tools/validate.py`); and automatically to a GitHub Pages mirror by `.github/workflows/pages.yml`, which has no local media tree to validate and instead builds with `SITE_SKIP_LOCAL_VALIDATION=1`, trusting `tools/publish_to_archive_org.py`'s publish-time checksum guarantee and only reconfirming each file still exists on archive.org. Distinct from an Archive Release: a Site Build is disposable/regenerable output for the Site, not a distribution artifact of the Archive itself.
_Avoid_: "release", "deploy" on their own — say Site Build for the artifact, and be explicit ("push the Site Build to the server") for the act of publishing it

**Member**:
A person's *ongoing* involvement with a band — `GroupMember` entries on a `band.yaml`'s `member` list, carrying a `period` (e.g. `'1994-1996'`). Reserved for people who were actually, continuingly part of the band. Someone who only contributed to a single release (e.g. a guest vocalist on one album) is not a Member — that goes on that specific `release.yaml`'s `creditText` (schema.org's own `creditText` property) instead, which needs no period of its own since the release itself is the temporal/scope marker.
_Avoid_: adding a `member` entry for a one-release contributor "for lack of anywhere else to put it" — say it belongs on that release's `creditText` instead
