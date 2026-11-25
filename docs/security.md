# Security model

Tailplan uses the tailnet as the viewer security boundary.
The upload API requires an upload token unless anonymous publication is explicitly enabled.
Management accounts restrict draft listings, changes, history, and API key operations.
Viewer URLs remain accessible to authorized tailnet devices.
Version 0.5.0 adds optional single sign-on, roles, and draft visibility.
The sections below describe them.

Named keys are stored as SHA-256 hashes.
Each key belongs to one account.
Browser sessions expire after 30 days and use signed cookies with form tokens.
The server signs the `tailplan_session` and `tailplan_login` cookies with a session secret.
Set `TAILPLAN_SESSION_SECRET_FILE` or the `--session-secret-file` option to give the server a dedicated secret.
Set `TAILPLAN_SESSION_SECRET` only when a file is not practical, because environment values show in process listings.
The secret must differ from the upload token.
The server refuses to start when the file is empty, unreadable, or equal to the upload token.
Create the file with mode `0600` and a long random value.

When you set no session secret, the server derives the cookie key from the upload token.
The derivation is HMAC-SHA256 of the label `tailplan-session-v1`, keyed by the upload token.
The derived key differs from the upload token, so a cookie cannot help an attacker guess the token.
In this mode, upload-token rotation invalidates all browser sessions.
With a dedicated secret, upload-token rotation keeps browser sessions.
Rotate the session secret to invalidate all browser sessions in that case.

Releases before this change signed cookies with the upload token itself.
After an upgrade, existing browser sessions stop working, and users sign in again once.

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

### Passive Tailscale viewer

Set `TAILPLAN_TAILSCALE_VIEWER=1` to let tailnet users and sharees read drafts with no sign-in step.
The setting needs `TAILPLAN_TRUST_TAILSCALE_IDENTITY=1` and a proxy listener (`TAILPLAN_PROXY_PORT`).
Without them, the server does not start.
The primary listener always ignores the header.
`TAILPLAN_AUTH_MODES` does not control the passive viewer.
That setting controls interactive sign-in only.
For example, with `TAILPLAN_AUTH_MODES=oidc,api_key`, the passive viewer still works, and "Continue with Tailscale" stays off.
The server uses the `Tailscale-User-Login` header only for `GET` and `HEAD` requests to draft pages.
These pages are `/d/<ref>`, `/share`, `/preview.png`, `/raw`, and `/content`.
A session, a view cookie, or a view token comes first.
A bearer key comes next, and the header comes last.
A request with an `Authorization` header never uses the Tailscale header.

The header identity has no account and no state.
The server does not write accounts, principals, or sessions for it.
It does not set cookies or make `tv=` tokens.
The identity gets the `viewer` role and no more, even when a grant or rule gives more.
The dashboard, `/api/*`, `/source`, and every `POST`, `PUT`, or `DELETE` ignore the header.
`TAILPLAN_ALLOWED_LOGINS` also applies to the header.
The audit log records each `GET` as `draft.view`, with the login as the actor email and `authMethod` `tailscale_header`.
The event also records the login as `actorLogin`.
This includes `/share` and `/preview.png`.

When a header identity cannot read a draft, the server returns `404`.
It does not send the reader to sign-in.
The `404` page is the same for a missing draft and a hidden draft.
Thus the page does not show that a draft exists.
When the reader has no session cookie, the page shows a "Sign in" link.

> [!WARNING]
> Without `TAILPLAN_RBAC_POLICY_FILE`, every login on the tailnet matches the open policy.
> Every such login then reads all `authenticated` drafts with no sign-in step.
> Set a policy file before you turn on the passive viewer on a shared tailnet.

A device with a tag has no user login, so Tailscale Serve sends no header.
That device falls back to the usual sign-in page.

### Logins and email addresses

A Tailscale login is not always an email address.
Logins such as `alice@github` and `alice@passkey` have no dotted domain.
An email grant or an email rule needs a dotted domain, so these logins never match one.
Use a `tailscale_login` grant or a `{"match": {"tailscale_login": "alice@github"}}` rule for them.
A Tailscale login with a dotted domain also matches email grants and email rules.

A login that matches no policy rule is a guest.
A guest reads only drafts granted to it and `link` drafts.
A guest does not read `authenticated` drafts.
The server checks the policy file on each request.
When you remove a rule, the user loses its roles at once.

Tailscale sign-in always uses its own account, keyed to the `tailscale` issuer.
It never joins a single sign-on account, even when the email is the same.
Thus a Tailscale sign-in never gets the roles of a single sign-on account.
Those roles can need the identity provider's MFA, and back-channel logout revokes them.

An email rule needs a dotted domain.
The server does not start with an email rule such as `bob@passkey`.
Change that rule to `{"match": {"tailscale_login": "bob@passkey"}}`.
A `tailscale_login` value must be printable ASCII with no spaces.

The upgrade to schema version 3 changes earlier data:

- An `email` grant with no dotted domain becomes a `tailscale_login` grant.
- A Tailscale principal with no dotted domain loses its stored email.

You can revoke any grant, also an earlier grant that the new patterns refuse.

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

## Single sign-on

Tailplan uses the OpenID Connect authorization code flow with PKCE `S256`.
It sends the client secret with HTTP Basic authentication (`client_secret_basic`).
Discovery is lazy: the server makes no identity provider request until the first sign-in.
The server checks that the discovery `issuer` is equal to `TAILPLAN_OIDC_ISSUER`.
Every endpoint must use HTTPS and the issuer origin.

The server refuses redirects from the identity provider.
It refuses loopback, private, link-local, and CGNAT addresses unless `TAILPLAN_OIDC_ALLOW_PRIVATE_ISSUER=1`.
It limits each response to 512 KiB and each request to 5 seconds.

The server keeps the key set for one hour.
An unknown key ID causes a refresh, but not more than once each 60 seconds.
The server accepts only `RS256`, `PS256`, `ES256`, `ES384`, and `EdDSA`.
It refuses `none` and every HMAC algorithm.
It checks `iss`, `aud`, `azp`, `exp`, `nbf`, `iat`, and `nonce`, with 30 seconds of clock skew.
When the ID token has no groups, the server reads them from the userinfo endpoint.
The userinfo `sub` must match the ID token.

The server keys each account on the issuer and the subject (`iss`, `sub`).
It never uses the email address as an account key.
A first sign-in links to an existing account only when the email is verified and is in `TAILPLAN_OIDC_LINK_EMAILS`.

The flow state, nonce, and PKCE verifier stay in a signed `tailplan_oidc_flow` cookie for 10 minutes.
The `next` value must be a local path on an allowlist.
Other values go to `/dashboard`.

## Sessions and view tokens

Each browser session has a row in the database.
Sign-out and back-channel logout revoke that row at once.
Back-channel logout accepts a signed logout token at `/auth/oidc/backchannel-logout`.
The token must have the logout event, no nonce, and `sub` or `sid`.

The `tailplan_session` cookie uses `SameSite=Lax` and `HttpOnly`.
It uses `Secure` when the base URL uses HTTPS.
Draft pages also get a `tailplan_view` cookie with `SameSite=None`, `Secure`, `HttpOnly`, and `Path=/d/`.
It lets a draft open in a new tab from another site.

Some browsers block that cookie, for example Safari with third-party cookies off.
For these browsers, links between pages of a multi-page draft carry a `?tv=` token.
The token is an HMAC over the draft ID and the session.
It expires after one hour and works only for `GET` requests to that draft.
When the session ends, its view tokens and view cookies stop working.

## Roles and visibility

Access is deny by default.
A sign-in that matches no policy rule gets `403` and an `auth.denied` audit event.
The bootstrap upload token has the `admin` instance role.
An API key has the roles of its account.
A key can never give more access than its account has.

`TAILPLAN_MAX_VISIBILITY` caps the visibility of every draft.
When you lower it, existing drafts become less visible at once.
Only an owner, editor, or admin can change sharing.
An upload can name a workspace only when the uploader is an editor or admin of that workspace.
An upload can also carry grants.
The server checks every grant first, and then writes the grants and the draft in one transaction.
One bad grant rejects the whole upload with `422`.

## Device login

`tailplan auth login --device` starts a device code flow.
The signed-in user opens `/cli/activate`, checks the code, and approves.
The code expires after 10 minutes and works once.
The new API key expires after 90 days.

## Audit log

The `audit_log` table is append-only.
Database triggers refuse updates and deletes.
The server also writes each event to standard output as one JSON line.
Each line has these fields: `ts`, `service`, `event`, `actor_account_id`, `actor_email`, `target_type`, `target_id`, `outcome`, and `detail`.
The `service` field is always `tailplan`.
Only admins and auditors can read `GET /api/audit`.

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
Use `TAILPLAN_VIEWER_AUTH_MODE=session` when the tailnet alone is too wide a boundary.
Keep the OIDC client secret in a file with mode `0600`.
Use a forced-command SSH account for remote publishers.

## Public sharing

Use `tailplan-share-public` only after the user explicitly approves public access for the selected draft.
The command copies one draft to public Postplan storage.
The command does not expose the Tailplan service.
