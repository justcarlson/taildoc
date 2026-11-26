# Changelog

This file records user-visible changes for each release.
The project uses the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) section names.
The project uses semantic version numbers.

## Unreleased

- Change the audit log lines on standard output to wide events.
  Each line is compact JSON with sorted keys and has `event` (`tailplan.` and the name), `event_version`, `run_id`, `started_at`, `duration_ms`, `outcome`, `error_type`, and `release`.
  The line has an `actor_hash` and an `actor_kind` in place of `actor_email`, and flat detail fields in place of `detail`.
  It has no `ts` and no `service`.
  A failed event goes to standard error.
  Set `TAILPLAN_AUDIT_HMAC_KEY` so that the hashes stay the same after a restart.
  The `audit_events` table and `GET /api/audit` do not change.
- Add schema version 4. It adds draft tags, an archive state, and a full-text search index.
  The index covers titles, slugs, descriptions, tags, and page text.
  The server builds the index from existing drafts at start.
- Add a search query language to `GET /api/drafts` and `/dashboard`.
  It takes free words, quoted phrases, and the `is:`, `v:`, `tag:`, `repo:`, `vis:`, `ws:`, `after:`, `before:`, and `age:` operators.
- Add `PATCH /api/drafts/<ref>` for `tags`, `addTags`, `removeTags`, `archived`, and `favorite`. `POST /api/uploads` also takes `tags` and `favorite`.
  One PATCH changes all fields in one transaction.
- `GET /api/drafts` also takes `group` (`time`, `tag`, `repo`, or `none`) and `repo`.
- A reader of a shared draft never sees the owner's tags, star, or archive state, and search skips the owner's tags.
- Archive hides a draft from the active list only. Its links still work, and it keeps `updatedAt` and its star.
- Add `POST /dashboard/bulk`, `POST /dashboard/theme`, and the `archive`, `unarchive`, and `tags` dashboard actions.
  Row actions return JSON when asked, else they redirect to the row.
- Add `draft.archive`, `draft.unarchive`, `draft.tag`, and `draft.bulk` audit events. They never hold page text or search text.

## 0.6.0 - 2026-11-25

- Add the passive Tailscale viewer, `TAILPLAN_TAILSCALE_VIEWER=1` (`--tailscale-viewer`).
  On the trusted proxy listener, a `GET` or `HEAD` draft page reads the `Tailscale-User-Login` header with no sign-in step.
  It never makes an account, a session, a cookie, or a `tv=` token, and it gives only the `viewer` role.
  The dashboard, the API, `/source`, and every write still need a session, an API key, or the upload token.
  The audit log records each view as `draft.view` with `authMethod` `tailscale_header`.
- Add the `tailscale_login` grant type and the `tailscale_login` policy match.
  Use them for logins that are not email addresses, such as `alice@github` or `alice@passkey`.
- Require a dotted domain in email grants, so a login such as `alice@github` never matches an email grant or rule.
- A signed-in user that matches no policy rule now reads only drafts granted to them and `link` drafts, not `authenticated` drafts.
- Tailscale sign-in always uses its own account, keyed to the `tailscale` issuer.
  It never joins a single sign-on account with the same email, so it never gets that account's roles.
- `TAILPLAN_AUTH_MODES` no longer controls the passive viewer.
  The server does not start when `TAILPLAN_TAILSCALE_VIEWER=1` has no `TAILPLAN_TRUST_TAILSCALE_IDENTITY=1` or proxy listener.
  Compose, the container entrypoint, and `install.sh` pass the setting.
- A bearer key on a draft page now comes before the Tailscale header.
- Every `404` page is the same, and it shows a "Sign in" link to a reader with no session.
- `/share` and `/preview.png` reads now write `draft.view` audit events.
  Upload grants now write `sharing.grant` audit events.
- The server checks the loaded policy on each request. After a policy edit and server restart, a removed rule takes its roles away at once.
- A reused `Idempotency-Key` with different grants, workspace, or visibility now returns `409`.
- **Breaking:** an email policy rule with no dotted domain, such as `bob@passkey`, now stops the server at start.
  Change it to a `tailscale_login` rule.
- Add `grants` to `POST /api/uploads`. The server writes the grants and the draft in one transaction.
- Add `--grant-email`, `--grant-tailscale`, `--grant-group`, and `--grant-role` to `tailplan upload`.
- Add `--grant-tailscale` and `--revoke-tailscale` to `tailplan share`, and a "Tailscale login" grant to the sharing card.
- Let the SSH publisher guard and the remote client run `tailplan share` and the upload sharing options.
  The guard checks each value against a strict pattern.
- Upgrade the SQLite schema to version 3. The upgrade copies every grant to the new table.
  An `email` grant with no dotted domain becomes a `tailscale_login` grant.
  A Tailscale principal with no dotted domain loses its stored email.
  Back up `tailplan.db` before you upgrade: the server cannot go back to schema version 2.

## 0.5.0 - 2026-11-25

- Add optional OpenID Connect sign-in behind the `oidc` extra (`PyJWT[crypto]>=2.10,<3`).
  The server imports PyJWT only when `TAILPLAN_OIDC_ISSUER` is set.
- Validate ID tokens with asymmetric algorithms only, PKCE `S256`, `client_secret_basic`, and 30 seconds of clock skew.
- Key accounts on issuer and subject, and link by email only for verified emails in `TAILPLAN_OIDC_LINK_EMAILS`.
- Store browser sessions in the database, so sign-out and back-channel logout revoke them at once.
- Add deny-by-default roles from `TAILPLAN_RBAC_POLICY_FILE`, with workspaces and the `admin` and `auditor` instance roles.
- Add draft visibility (`private`, `workspace`, `authenticated`, `link`), grants, and `TAILPLAN_MAX_VISIBILITY`.
- Add `TAILPLAN_VIEWER_AUTH_MODE` with `network`, `per-draft`, and `session` modes.
  The default `network` mode keeps the 0.4.0 viewer behavior.
- Add the `tailplan_view` cookie and `?tv=` link tokens, so multi-page drafts work when third-party cookies are blocked.
- Add a sharing card, visibility badges, and a "Shared with me" section to the dashboard.
- Add `tailplan share`, plus `--workspace` and `--visibility` on `tailplan upload`.
- Add `tailplan auth login --device`, which saves an API key that expires after 90 days.
- Add an append-only audit log, JSON lines on standard output, and `GET /api/audit` for admins and auditors.
- Upgrade the SQLite schema to version 2.
- Run sign-in browser checks on Chromium and WebKit, and a Keycloak 26 sign-in check in CI.
- Store metadata in a SQLite database, `tailplan.db`, in write-ahead log mode.
  The first start imports `metadata.json` and keeps the original as `metadata.json.pre-sqlite.bak`, so a rollback to 0.4.0 loses nothing.
  Draft HTML files stay on disk.
- Add `TAILPLAN_SESSION_SECRET_FILE` (`--session-secret-file`) and `TAILPLAN_SESSION_SECRET` for signing browser cookies.
  Without them, the server derives the key from the upload token with HMAC-SHA256.
  Browser sessions made by earlier releases stop working once, so users sign in again after the upgrade.
- Make `/readyz` fast and offline-safe: it runs a SQLite quick check and a write probe, and no longer hashes draft files.
- Add independent serif-heading and all-sans typography presets for every palette.
- Support typography listing and selection through the API, direct CLI, and SSH client.
- Freeze font metadata and CSS in each published version.
- Add the `brief` layout for reports on one source, such as a video, podcast, article, or meeting.
  It adds a header, a timed run-of-show bar, section links into the source, callouts, and claim badges.
- Add a brief workflow, with caption clean-up steps, to the Tailplan skill.
- Stack the version history and API key tables into labeled rows on phones.
- Warn when an uploaded HTML document has no viewport meta tag.
- Require every layout and server page to pass a 320px and 390px phone check in CI.
- Let reading-layout tables widen to 1104px on tablets and desktops, so wide tables fit without sideways scroll.
- Require every layout and server page to pass tablet and desktop checks at 768px, 1024px, 1280px, and 1920px.
- Run inline JavaScript in authenticated HTML drafts inside an opaque CSP sandbox without `allow-same-origin`.
- Expand HTML validation for `type="module"`, data scripts, standard `on*` handlers, allowlisted `<link>` tags, sandbox warnings, and line numbers.
- Add `POST /api/check` and `tailplan check` with an offline markup validator fallback.
- Add multi-page HTML folder uploads with clean URL serving and directory trailing-slash redirects.
- Add custom URL slugs via `--slug` with portable updates, renames, conflict checks, and release on delete.
- Add `GET /api/drafts/<ref>/source` and `tailplan download` for non-clobbering edit round-trips.
- Add draft favorites via `POST /api/drafts/<ref>/favorite`, `tailplan favorite`/`unfavorite`, and dashboard star toggles.
- Store per-draft CLI bindings under `~/.tailplan/drafts/` with read-only fallback to legacy `drafts.json`.
- Deduplicate identical-content updates via `contentHash` and return `"unchanged": true` without bumping version numbers.
- Add `TAILPLAN_ALLOWED_LOGINS` (`--allowed-logins`) to restrict Tailscale identity sign-in.
  When set, other logins get `403` and no account.
  An empty value keeps the old behavior.
- Add `TAILPLAN_VIEWER_404_LIMIT` (`--viewer-404-limit`, default `30` each minute) to return `429` after repeated missing-draft requests on viewer routes.
- Let the container health check follow `TAILPLAN_PORT`, and drop empty optional settings in the entrypoint.
- Publish `ghcr.io/justcarlson/tailplan` with build provenance on `main` and `v*` tags.
- Document the dual-listener container setup for a Tailscale sidecar.
- Bump the version to 0.5.0.

## 0.3.0 - 2026-09-13

### Added

- The dashboard groups drafts by repository and displays descriptions, version history, and upload audit fields.
- Named API keys support account ownership, browser sign-in, CLI setup, and revocation.
- Optional Tailscale identity sign-in connects tailnet users to their accounts.
- The `tailplan` CLI adds list, history, whoami, auth, keys, disable, enable, and delete commands.
- The SSH client preserves source mappings and sends local repository metadata.
- Upload rate limits return `Retry-After`, and anonymous publication is an explicit server option.

### Changed

- Draft routes return exact uploaded HTML, including original line endings.
- Raw aliases and draft identity headers match the Postplan publishing contract.
- Inline classic scripts are accepted as source. Browser execution remains blocked by the response policy.

## 0.2.1 - 2026-08-09

### Fixed

- The SSH publisher account now accepts its forced public key without enabling a password.
- The SSH publisher guard now uses a root-owned copy of the selected local publisher.
- The upgrade instructions now require a refresh of the isolated SSH publisher copy.

### Security

- The publisher installer replaces a locked password with a value that no password can produce.

## 0.2.0 - 2026-08-09

### Added

- Tailplan 0.2.0 adds a Docker image and a Docker Compose deployment.
- The SSH publisher account installer creates a dedicated forced-command account.
- The README adds local evaluation and development instructions.
- The repository adds public contribution, security, support, and maintenance files.

### Changed

- The native installer improves installation checks, rollback behavior, and service verification.
- Tailplan 0.2.0 adds health and readiness checks for each server deployment.

### Security

- The forced-command guard limits remote publication to validated command forms.
- The remote client keeps the upload token on the Tailplan server.
