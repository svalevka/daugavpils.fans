-- review_app's database. SQLite: single box, single writer process, a
-- handful of rows a week - no case for a separate DB server (see the PRD,
-- GitHub issue #8). Applied idempotently (CREATE TABLE IF NOT EXISTS) on
-- every app startup - see db.py.

CREATE TABLE IF NOT EXISTS approvers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Granular role-based access control (GitHub issue #35):
-- Roles: 'admin', 'changes-approver', 'viewer-stats'.
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES approvers(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, role)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role);

-- Not exercised until the login/dashboard ticket (#12), but part of this
-- ticket's own schema deliverable per the issue tracker.
CREATE TABLE IF NOT EXISTS magic_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    approver_id INTEGER NOT NULL REFERENCES approvers(id),
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    used_at TEXT,
    requested_ip TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    band_slug TEXT NOT NULL,
    release_slug TEXT,
    target TEXT NOT NULL,
    list_index INTEGER,
    field TEXT NOT NULL,
    original_value TEXT NOT NULL,              -- JSON: string or list[string]
    proposed_value TEXT NOT NULL,               -- JSON: string or list[string]
    submitter_name TEXT,
    submitter_contact TEXT,
    submitter_ip TEXT NOT NULL,
    submitted_by_approver_id INTEGER REFERENCES approvers(id),
    status TEXT NOT NULL DEFAULT 'pending',     -- pending | approved | rejected | applying | applied | apply_failed
    decided_by INTEGER REFERENCES approvers(id),
    decided_at TEXT,
    github_run_id TEXT,
    applied_at TEXT,
    apply_error TEXT,
    ai_decision TEXT,
    ai_confidence REAL,
    ai_reasoning TEXT,
    ai_evaluated_at TEXT
);

CREATE TABLE IF NOT EXISTS submission_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Same per-IP-per-hour pattern as submission_log above, but for /login:
-- every request POSTed there (not just ones matching an active approver -
-- an attacker probing emails must be throttled too), so a flood can't be
-- used to spam an inbox with magic links or brute-force approver
-- enumeration via timing.
CREATE TABLE IF NOT EXISTS login_request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    requested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- New photo/video proposals (GitHub issue #21, upload automated in #36)
-- - deliberately not the `proposals` table above: approving one never
-- touches git or archive.org by itself, it only queues the upload as
-- "ready to publish". From there status normally moves through
-- 'publishing' (a curator clicked "Upload", dispatching
-- apply-media-proposal.yml) to 'published' once that Action's
-- apply_media_proposal.py has uploaded the file and committed the YAML
-- entry - or a curator can instead publish the file by hand and set
-- 'published' directly via the dashboard's manual-fallback route.
CREATE TABLE IF NOT EXISTS media_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    band_slug TEXT NOT NULL,
    release_slug TEXT,
    media_type TEXT NOT NULL,                   -- 'image' | 'video', from sniffing, not the filename
    original_filename TEXT NOT NULL,             -- as given by the submitter's browser - display only
    stored_filename TEXT NOT NULL,               -- random, on disk under MEDIA_UPLOADS_PATH
    content_type TEXT NOT NULL,                  -- sniffed MIME type
    size_bytes INTEGER NOT NULL,
    caption TEXT,
    submitter_name TEXT,
    submitter_contact TEXT,
    submitter_ip TEXT NOT NULL,
    submitted_by_approver_id INTEGER REFERENCES approvers(id),
    -- pending | fetching | approved | rejected | publishing | published | publish_failed
    -- ('fetching' is a source_type='youtube'-only transient state: a
    -- background thread is downloading the video - see youtube_fetch.py.
    -- It never reaches the approval queue; on failure the row is
    -- deleted rather than surfaced as a status, per GitHub issue #49.)
    status TEXT NOT NULL DEFAULT 'pending',
    decided_by INTEGER REFERENCES approvers(id),
    decided_at TEXT,
    github_run_id TEXT,
    published_at TEXT,
    publish_error TEXT,
    ai_decision TEXT,
    ai_confidence REAL,
    ai_reasoning TEXT,
    ai_evaluated_at TEXT,
    -- 'upload' (direct file, the original flow) | 'youtube' (see GitHub
    -- issue #49). source_url/youtube_* are populated only for 'youtube'.
    source_type TEXT NOT NULL DEFAULT 'upload',
    source_url TEXT,
    youtube_title TEXT,
    youtube_channel TEXT,
    youtube_duration_seconds INTEGER,
    duration_seconds INTEGER,
    -- The submitter's required attestation checkbox for the YouTube path
    -- only (see issue #49) - not proof of anything, but an explicit,
    -- on-record claim rather than leaving provenance entirely implicit.
    rights_attested INTEGER NOT NULL DEFAULT 0
);

-- Admin authentication for site maintainer statistics (/admin/):
-- Uses passwordless magic links emailed to MAINTAINER_EMAIL (see GitHub issue #34).
CREATE TABLE IF NOT EXISTS admin_magic_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    used_at TEXT,
    requested_ip TEXT
);

CREATE TABLE IF NOT EXISTS admin_login_request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    requested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Privacy-preserving visitor and media analytics (GitHub issue #34):
-- Records human pageviews and media plays (>=5s active playback).
-- visitor_hash is a daily-rotating salted SHA256(ip + UA + daily_salt);
-- no raw IP addresses or cookies are ever stored.
CREATE TABLE IF NOT EXISTS analytics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,           -- 'pageview' | 'track_play' | 'video_play' | 'media_error'
    path TEXT NOT NULL,
    band_slug TEXT,
    release_slug TEXT,
    track_name TEXT,
    video_name TEXT,
    referrer_domain TEXT,
    country_code TEXT,
    device_type TEXT,                   -- 'desktop' | 'mobile' | 'tablet'
    visitor_hash TEXT NOT NULL,         -- SHA256(ip + UA + daily_salt)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analytics_created ON analytics_events(created_at);
CREATE INDEX IF NOT EXISTS idx_analytics_type ON analytics_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_analytics_band ON analytics_events(band_slug, created_at);

-- New album proposals (GitHub issue #20):
-- Allows proposing a brand-new release with audio tracks and optional cover art
-- under an existing band. Similar to media_proposals, approving one dispatches
-- apply-album-proposal.yml which uploads audio/cover to archive.org, creates
-- release.yaml, commits to git, and unlinks staged files.
-- Publishing is throttled to at most 3 releases per rolling 24 hours.
CREATE TABLE IF NOT EXISTS album_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    band_slug TEXT NOT NULL,
    release_slug TEXT NOT NULL,
    name TEXT NOT NULL,
    date_published TEXT NOT NULL,
    genre TEXT,                                  -- JSON list[str]
    license TEXT NOT NULL,
    description TEXT,
    description_en TEXT,
    cover_stored_filename TEXT,
    tracks_json TEXT NOT NULL,                   -- JSON list of track dicts
    submitter_name TEXT,
    submitter_contact TEXT,
    submitter_ip TEXT NOT NULL,
    submitted_by_approver_id INTEGER REFERENCES approvers(id),
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | approved | rejected | publishing | published | publish_failed
    decided_by INTEGER REFERENCES approvers(id),
    decided_at TEXT,
    github_run_id TEXT,
    published_at TEXT,
    publish_error TEXT,
    ai_decision TEXT,
    ai_confidence REAL,
    ai_reasoning TEXT,
    ai_evaluated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_album_proposals_status ON album_proposals(status, created_at);
CREATE INDEX IF NOT EXISTS idx_album_proposals_slug ON album_proposals(band_slug, release_slug);
CREATE INDEX IF NOT EXISTS idx_album_proposals_published ON album_proposals(published_at);

-- New band proposals (GitHub issue #22):
-- Allows proposing a brand-new band (MusicGroup) with optional band photo,
-- and optional first release (MusicAlbum + audio tracks + cover art).
-- Publishing is throttled to at most 1 new band per rolling 24 hours.
CREATE TABLE IF NOT EXISTS band_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    name TEXT NOT NULL,
    band_slug TEXT NOT NULL,
    founding_date TEXT,
    dissolution_date TEXT,
    location TEXT DEFAULT 'Daugavpils, Latvia',
    genre TEXT,                                  -- JSON list[str]
    description TEXT,
    description_en TEXT,
    band_photo_stored_filename TEXT,
    has_release INTEGER NOT NULL DEFAULT 0,
    release_name TEXT,
    release_slug TEXT,
    release_date_published TEXT,
    release_genre TEXT,                          -- JSON list[str]
    release_license TEXT,
    release_description TEXT,
    release_description_en TEXT,
    release_cover_stored_filename TEXT,
    release_tracks_json TEXT,                    -- JSON list of track dicts
    submitter_name TEXT,
    submitter_contact TEXT,
    submitter_ip TEXT NOT NULL,
    submitted_by_approver_id INTEGER REFERENCES approvers(id),
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | approved | rejected | publishing | published | publish_failed
    decided_by INTEGER REFERENCES approvers(id),
    decided_at TEXT,
    github_run_id TEXT,
    published_at TEXT,
    publish_error TEXT,
    ai_decision TEXT,
    ai_confidence REAL,
    ai_reasoning TEXT,
    ai_evaluated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_band_proposals_status ON band_proposals(status, created_at);
CREATE INDEX IF NOT EXISTS idx_band_proposals_slug ON band_proposals(band_slug);
CREATE INDEX IF NOT EXISTS idx_band_proposals_published ON band_proposals(published_at);

-- Decision history indexes (filtering by status and decided_at)
CREATE INDEX IF NOT EXISTS idx_proposals_decided ON proposals(status, decided_at);
CREATE INDEX IF NOT EXISTS idx_media_proposals_decided ON media_proposals(status, decided_at);
CREATE INDEX IF NOT EXISTS idx_album_proposals_decided ON album_proposals(status, decided_at);
CREATE INDEX IF NOT EXISTS idx_band_proposals_decided ON band_proposals(status, decided_at);

