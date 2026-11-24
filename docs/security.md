# Security model

Tailplan uses the tailnet as the viewer security boundary.
The upload API requires an upload token unless anonymous publication is explicitly enabled.
Management accounts restrict draft listings, changes, history, and API key operations.
Viewer URLs remain accessible to authorized tailnet devices.

Named keys are stored as SHA-256 hashes.
Each key belongs to one account.
Browser sessions expire after 30 days and use signed cookies with form tokens.
The bootstrap token also signs browser sessions.
Token rotation invalidates all browser sessions.

Tailscale identity headers are optional.
Only the configured loopback proxy listener accepts those headers.
The primary listener always ignores identity headers.
The operator must keep the proxy listener behind Tailscale Serve.

Set `TAILPLAN_ALLOWED_LOGINS` to a comma-separated list of logins to restrict Tailscale sign-in.
Matching ignores case.
When the list is not empty, the server returns `403` to any other login that attempts Tailscale sign-in.
The server does not create an account for that login.
The server also rejects existing Tailscale sessions for logins that are not in the list.
The sign-in page does not show "Continue with Tailscale" to those logins.
Add the owner login to the list, or the owner must sign in with an API key.
API keys, the bootstrap token, and viewer routes do not use the list.
When the list is empty, any Tailscale login can sign in.

Viewer routes count each missing draft, version, or slug against the caller.
The key is the Tailscale login on the proxy listener when identity is trusted, otherwise the client address.
After `TAILPLAN_VIEWER_404_LIMIT` misses in 60 seconds, the server returns `429` with `Retry-After` for viewer requests from that key.
This slows guessing of custom slugs.
Health and readiness routes do not use this limit.

Uploaded HTML is served without rewriting.
Authenticated HTML drafts may run inline JavaScript, including `type="module"` scripts and standard `on*` handlers, inside an opaque CSP sandbox.
The sandbox omits `allow-same-origin` and sets `connect-src 'none'`, `worker-src 'none'`, `form-action 'none'`, `frame-src 'none'`, `object-src 'none'`, and `frame-ancestors 'none'`.
External HTTPS images, media, fonts, allowlisted stylesheets, and escaped popups remain permitted.
Script-free drafts, Markdown drafts, anonymous uploads, and pre-0.0.6 versions keep `script-src 'none'`.
Forms, external scripts, unsupported script types, embeds, meta refresh, and unsafe URL attributes are rejected.

## Suitable content

Tailplan is suitable for these file types only if the files contain no restricted content:

- Agent plans
- Draft reports
- Static HTML pages
- Internal runbooks
- Research notes

## Restricted content

Do not upload secrets or credentials.
Do not upload links that grant access.
Do not upload mailbox, medical, legal, tax, or financial records.
Do not upload a document if only a subset of the authorized Tailplan viewers can view it.

## Access controls

Keep the upload-token file at mode `0600`.
Keep the upload token only on the Tailplan server.
Use Tailscale access controls to restrict viewer access.
Use a forced-command SSH account for remote publishers.

## Public sharing

Use `tailplan-share-public` only after the user explicitly approves public access for the selected draft.
The command copies one draft to public Postplan storage.
The command does not expose the Tailplan service.
