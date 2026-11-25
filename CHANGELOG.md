# Changelog

This file records user-visible changes for each release.
The project uses the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) section names.
The project uses semantic version numbers.

## Unreleased

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
