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
    apply_error TEXT
);

CREATE TABLE IF NOT EXISTS submission_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
