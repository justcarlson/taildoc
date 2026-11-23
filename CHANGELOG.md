# Changelog

This file records user-visible changes for each release.
The project uses the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) section names.
The project uses semantic version numbers.

## Unreleased

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
