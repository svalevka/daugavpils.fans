# Dead-man's-switch: getting the archive.org credential if the maintainer disappears

This document describes a separate, narrower mechanism from
`RECOVERY.md`. `RECOVERY.md` is the self-serve fallback for *anyone,
with zero prior arrangement* - it works even for a total stranger, but
its fastest path (recovering the old account) may not be available, and
its fallback (a brand-new account + `ITEM_PREFIX`) leaves old items
owned by an account nobody can add to anymore.

This mechanism exists to hand the *actual, working*
`IA_ACCESS_KEY_ID`/`IA_SECRET_ACCESS_KEY` credential (the one
`tools/publish_to_archive_org.py` and the CI workflows already use) to a
small, pre-registered list of trusted people, automatically, if the
maintainer is gone or permanently unreachable - without needing anyone
to hold that credential, or any other password, in the meantime. It
lives entirely in GitHub (issues + Actions + a private repo), so it
works independently of whether daugavpils.fans (the site/hosting) is up.

## For a trusted contact: how to use this

1. Go to this repo's Issues tab and open a new issue using the
   **"Dead-man's-switch: request archive.org access"** template. You
   need a GitHub account, but no special permissions on this repo.
2. If your GitHub username isn't on the pre-registered trusted-contacts
   list, the issue is closed automatically with no further action - this
   isn't for anyone but a specific short list of people the maintainer
   chose ahead of time.
3. If it is, the maintainer is notified (assigned + mentioned on the
   issue) and has **7 days** to cancel by closing the issue themselves.
   The rest of the trusted contacts are also mentioned, as witnesses -
   not because their approval is required, just so more than one person
   is aware a request is in flight.
4. If nobody cancels within 7 days, a daily check notices, and you'll be
   invited as a collaborator on a private vault repository
   (`svalevka/daugavpils-fans-recovery-vault`) containing the current
   credential and short instructions. Accept the GitHub invite (you'll
   get a normal GitHub notification/email for it) to see it.
5. Only open this if you genuinely believe the maintainer is gone or
   permanently unreachable - not to test it (see "Testing" below for how
   to do that safely) and not because they're slow to reply.

## How it works, mechanically

Three workflows in `.github/workflows/`:

- **`switch-request.yml`** - fires when the request issue is opened.
  Checks the opener's GitHub login against the `SWITCH_TRUSTED_CONTACTS`
  secret (a JSON array of `{"github", "email", "name"}` - a secret, not
  a repo file, so these people's emails aren't sitting in a public repo
  forever). Unlisted openers: issue closed immediately. Listed openers:
  issue labelled `switch-pending`, maintainer assigned, everyone else on
  the list @mentioned.
- **`switch-check.yml`** - runs daily (also runnable on-demand via
  "Run workflow" for testing). Finds `switch-pending` issues older than
  `SWITCH_DELAY_DAYS` (7) that are still open, re-verifies the requester
  is still on the trusted list, reads the **live**
  `IA_ACCESS_KEY_ID`/`IA_SECRET_ACCESS_KEY` secrets fresh (never a
  pre-copied value that could go stale if the key is ever rotated),
  writes them into the vault repo, and invites the requester there using
  `SWITCH_VAULT_PAT`.
- **`switch-selftest.yml`** - runs monthly. Confirms the trusted-contacts
  secret still parses and the vault PAT still has admin access to the
  vault repo. Tracks consecutive failures in
  `.github/switch-selftest-state.json`; on failure, files/updates an
  issue for the maintainer, and after 3 consecutive unfixed failures also
  notifies the whole trusted list, so nobody trusts a switch that's
  quietly broken. This mirrors the existing nightly
  `verify-archive-org.yml` pattern in this repo.

The delivery step deliberately uses GitHub's own collaborator-invite
access control (a private repo you're invited into) rather than posting
the credential anywhere in the public `daugavpils.fans` repo - an issue
comment or a workflow log would be visible to literally anyone, not just
the verified requester.

## Maintaining this (for the current maintainer)

Two secrets on `svalevka/daugavpils.fans` need occasional attention:

- **`SWITCH_TRUSTED_CONTACTS`** - a JSON array, e.g.:
  ```json
  [{"github": "username", "email": "person@example.com", "name": "Full Name"}]
  ```
  Update via `gh secret set SWITCH_TRUSTED_CONTACTS` when the list of
  trusted people changes. Keep it short and to people you'd actually
  want holding this.
- **`SWITCH_VAULT_PAT`** - a **fine-grained personal access token**,
  scoped to only the `svalevka/daugavpils-fans-recovery-vault` repo,
  with **Administration: Read and write** (needed to invite
  collaborators) and **Contents: Read and write** (needed to write the
  credential file) permissions, nothing broader. Fine-grained PATs cap
  out at a 1-year expiration - GitHub won't let you create one that
  lasts forever - so **this will need renewing at least yearly**. The
  monthly self-test is there specifically to catch this before it
  becomes a problem: if it starts failing, mint a new PAT at
  <https://github.com/settings/personal-access-tokens/new> (scoped as
  above) and run `gh secret set SWITCH_VAULT_PAT`.

The vault repo itself (`svalevka/daugavpils-fans-recovery-vault`) should
be left alone otherwise - private, empty except for the vault README
until a switch actually trips.

## Testing this safely

Never open a real request issue just to test - that notifies everyone on
the trusted list for real, and pastes a warning on the tin for good
reason. Instead:

1. Temporarily lower `SWITCH_DELAY_DAYS` in `switch-check.yml` (e.g. to
   a very small value) in a throwaway commit, or just note the real
   issue's timestamp and manually run `switch-check.yml` via
   "Run workflow" - it only acts on issues past the threshold, so with
   the default 7-day value a same-day manual run of `switch-check.yml`
   is a safe no-op dry run.
2. To test the full trip path end-to-end, add a second, disposable
   GitHub account to `SWITCH_TRUSTED_CONTACTS` temporarily, open a real
   request issue as that account, lower `SWITCH_DELAY_DAYS` to something
   that will elapse immediately, manually run `switch-check.yml`, and
   confirm the vault invite arrives. Afterwards: revert
   `SWITCH_DELAY_DAYS`, remove the test account from
   `SWITCH_TRUSTED_CONTACTS`, and remove it as a collaborator from the
   vault repo.
3. `switch-selftest.yml` is safe to run on-demand ("Run workflow") any
   time - it only reads/verifies, and only writes if the failure count
   actually changes.

## Caveats

- This depends on the maintainer's GitHub notification settings/email
  actually reaching them during the 7-day window - the same
  single-point-of-failure tradeoff already accepted for the archive.org
  account's recovery email (see `RECOVERY.md`).
- Any single trusted contact can start the clock alone; there's no
  multi-person approval requirement. The cross-notification to the rest
  of the list is a social safety net, not an enforced check.
- If the maintainer is unreachable for longer than the vault PAT's
  validity (up to a year) without anyone renewing it, delivery will
  fail at trip-time even though the request/notification steps still
  work - the monthly self-test is the mitigation, not a guarantee.
- This hands out the same credential the live CI already uses, not a
  separate one - anyone it's delivered to can do anything that
  credential can do on the archive.org account. If a delivered
  credential is later rotated, the vault's copy simply goes stale until
  the next real trip regenerates it (see "how it works" above).
