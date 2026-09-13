# Postplan compatibility

## Reference

The reference is the published `postplan@0.0.4` npm package, inspected on 2026-09-13.
The package includes the CLI, server routes, HTML policy, database schema, and dashboard templates.
Its archive SHA-1 is `c6a22837ee8e47d67e6f3f8be4d3987a2683c8d9`.
The [third-party notice](../THIRD_PARTY_NOTICES.md) retains the reference package's MIT license.

Sources:

- [Package archive](https://registry.npmjs.org/postplan/-/postplan-0.0.4.tgz).
- [Package documentation](https://www.jsdelivr.com/package/npm/postplan).
- [Live service](https://postplan.dev/).

Taildoc implements the observed contracts in Python.
Postplan uses Postgres, S3, and Shoo sign-in.
Taildoc uses its existing disk store, API-key sign-in, and optional Tailscale identity sign-in.

## Feature map

| Postplan behavior | Taildoc contract |
| --- | --- |
| Create and update drafts | `POST /api/uploads`, stable draft IDs, immutable versions. |
| Exact HTML for all clients | `/d/ID`, `/d/ID/raw`, `/d/ID/v/N`, and `/d/ID/v/N/raw`. |
| Draft response headers | Draft ID, version, and content hash headers. |
| Description | Optional upload `description`, retained when omitted. |
| Repository grouping | Repository host, organization, and name in metadata. |
| Version provenance | Branch, commit, subject, dirty state, CI run, actor, and client version. |
| Upload audit | Server time, request ID, client address, key ID, byte count, and content signals. |
| Account feed | Authenticated `GET /api/drafts`, newest first. |
| Version history | Authenticated draft detail API and dashboard page. |
| Named API keys | Create, list, and revoke keys within one account. |
| Web dashboard | `/dashboard`, repository groups, descriptions, and version links. |
| Browser key setup | `/cli/auth`, one-time key display, and revocation. |
| Web sessions | Signed expiring cookies, sign-in, and sign-out. |
| Draft lifecycle | Disable and delete through authenticated endpoints. |
| CLI | Upload, list, whoami, auth set, and browser-assisted auth login. |
| Upload limits | Per-address and per-key limits with `Retry-After`. |
| Inline classic scripts | Accepted as source, with browser execution blocked by CSP. |
| Anonymous publication | Optional, with a separate anonymous account. Disabled by default. |

The private deployment uses path URLs under Tailscale Serve.
Wildcard draft domains are a Postplan hosting choice, rather than a required publishing feature.
Taildoc keeps its `/content` aliases, Markdown renderer, retry receipts, and verified publishing client.
Taildoc also supports draft re-enabling and an API for complete version history.

The server returns uploaded HTML without rewriting anchors or changing line endings.
The Markdown client can add link attributes before upload.
The server accepts inline classic scripts for source compatibility.
Its response policy blocks script execution, network requests, forms, and embedding in browsers.

Old drafts belong to the local account.
Historical versions keep their original bytes.
Unknown historical provenance remains empty.
Draft deletion removes access and listings, but retains files for operator recovery.
