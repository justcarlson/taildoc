# Postplan compatibility

## Reference

The reference is the published `postplan@0.0.6` npm package, inspected on 2026-11-22.
The package includes the CLI, server routes, HTML policy, database schema, and dashboard templates.
The [third-party notice](../THIRD_PARTY_NOTICES.md) retains the reference package's MIT license.

Sources:

- [Package archive](https://registry.npmjs.org/postplan/-/postplan-0.0.6.tgz).
- [Package documentation](https://www.jsdelivr.com/package/npm/postplan).
- [Live service](https://postplan.dev/).

Taildoc implements the observed contracts in Python.
Postplan uses Postgres, S3, and Shoo sign-in.
Taildoc uses its existing disk store, API-key sign-in, and optional Tailscale identity sign-in.

## Feature map

| Postplan behavior | Taildoc contract |
| --- | --- |
| Create and update drafts | `POST /api/uploads`, stable draft IDs, immutable versions. |
| Identical-content dedup | Matching `contentHash` keeps the current version and returns `unchanged: true`. |
| Exact HTML for all clients | `/d/REF`, `/d/REF/raw`, `/d/ID/v/N`, and `/d/ID/v/N/raw`. |
| Multi-page folders | Folder uploads with `index.html`, clean URLs, and directory slash redirects. |
| Custom URL slugs | `--slug` DNS-label slugs, portable updates, renames, conflicts, and release on delete. |
| Pre-flight validator | `POST /api/check` and `tailplan check` with local offline fallback. |
| Download to edit | `GET /api/drafts/REF/source` and `tailplan download` with non-clobbering paths. |
| Favorites | `POST /api/drafts/REF/favorite`, CLI commands, and dashboard star toggles. |
| Per-draft CLI bindings | Atomic `~/.tailplan/drafts/<sha256>.json` files with read-only `drafts.json` fallback. |
| Draft response headers | Draft ID, version, and content hash headers. |
| Description | Optional upload `description`, retained when omitted. |
| Repository grouping | Repository host, organization, and name in metadata. |
| Version provenance | Branch, commit, subject, dirty state, CI run, actor, and client version. |
| Upload audit | Server time, request ID, client address, key ID, byte count, and content signals. |
| Account feed | Authenticated `GET /api/drafts`, favorites first, then newest first. |
| Version history | Authenticated draft detail API and dashboard page. |
| Named API keys | Create, list, and revoke keys within one account. |
| Web dashboard | `/dashboard`, favorites, repository groups, descriptions, and version links. |
| Browser key setup | `/cli/auth`, one-time key display, and revocation. |
| Web sessions | Signed expiring cookies, sign-in, and sign-out. |
| Draft lifecycle | Disable, enable, favorite, and delete through authenticated endpoints. |
| CLI | Upload, check, download, favorite, unfavorite, list, history, whoami, auth, and keys. |
| Upload limits | Per-address and per-key limits with `Retry-After`. |
| Sandboxed inline scripts | Authenticated HTML runs inline JS in an opaque CSP sandbox without `allow-same-origin`. |
| Anonymous publication | Optional, with a separate anonymous account. Disabled by default. |

The private deployment uses path URLs under Tailscale Serve.
Wildcard draft domains are a Postplan hosting choice, rather than a required publishing feature.
Taildoc keeps its `/content` aliases, Markdown renderer, retry receipts, and verified publishing client.
Taildoc also supports draft re-enabling and an API for complete version history.

The server returns uploaded HTML without rewriting anchors or changing line endings.
The Markdown client can add link attributes before upload.
Authenticated HTML drafts may run inline classic scripts, `type="module"` scripts, and standard `on*` handlers inside an opaque sandbox.
The sandbox sets `connect-src 'none'` and `worker-src 'none'`, and omits `allow-same-origin`.
External HTTPS images, media, fonts, allowlisted stylesheets, and escaped popups remain permitted.
Script-free drafts, Markdown pages, anonymous uploads, and pre-0.0.6 versions keep `script-src 'none'`.

Old drafts belong to the local account.
Historical versions keep their original bytes.
Unknown historical provenance remains empty.
Draft deletion removes access and listings, but retains files for operator recovery.
