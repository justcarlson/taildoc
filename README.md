# Tailplan

Tailplan publishes static drafts inside a Tailscale tailnet.
Agents can publish plans, reports, Markdown files, text files, and static HTML files.
Tailplan returns a URL that tailnet devices can open.

Read [SUPPORT.md](SUPPORT.md) for compatibility and support information.
Read [CHANGELOG.md](CHANGELOG.md) for release changes.

Read [document themes](docs/themes.md) for built-in palettes, CLI/API selection, and extension instructions.
Read [briefs](docs/briefs.md) for reports on a video, podcast, article, or other source.
Read [single sign-on, roles, and sharing](#single-sign-on-roles-and-sharing) to control who can read each draft.

## Features

- Tailplan serves each draft at `/d/<draft-id>`.
- Tailplan accepts authenticated uploads at `POST /api/uploads`.
- Tailplan stores each draft on local disk.
- Tailplan binds the primary listener to a Tailscale address by default.
- Tailplan can use Tailscale Serve for HTTPS access.
- Tailplan runs inline HTML scripts inside an opaque CSP sandbox and rejects external scripts, forms, embeds, and unsafe URLs.
- Tailplan renders Markdown tables as responsive HTML tables.
- Tailplan can mirror one approved draft to Postplan.
- The dashboard groups drafts by repository and displays every version.
- Named API keys identify the account that owns each draft.
- Draft descriptions and Git metadata remain available through the dashboard and CLI.
- Optional OpenID Connect sign-in maps identity provider groups to workspace roles.
- Each draft has a visibility and can be shared with people or groups.
- An append-only audit log records sign-in, sharing, and draft events.

Read [Postplan compatibility](docs/postplan-parity.md) for the observed API and behavior contracts.

## Draft dashboard and CLI

Open `/dashboard` under your Tailplan base URL.
Sign in with an existing API key, or use configured Tailscale identity sign-in.
The existing upload token opens the local account, which owns all earlier drafts.

Open `/cli/auth` to generate a named key.
Tailplan shows the key once and stores only its hash.
Use a named key with the direct client:

```sh
tailplan auth login --api-url https://host.example.ts.net/tailplan
tailplan whoami
tailplan check ./plan.html --slug migration-plan
tailplan upload ./plan.html --slug migration-plan --description "Migration plan"
tailplan upload ./site-folder/
tailplan download migration-plan
tailplan favorite migration-plan
tailplan unfavorite migration-plan
tailplan list --json
tailplan history migration-plan --json
tailplan disable <draft-id> --reason "Revision in progress"
tailplan enable <draft-id>
tailplan delete <draft-id>
tailplan keys create "Build agent"
tailplan keys list
tailplan keys revoke <key-id>
tailplan auth login --device --base-url https://tailplan.example.test
tailplan upload ./plan.html --workspace team --visibility workspace
tailplan share migration-plan --visibility authenticated --grant-email reader@example.test
tailplan upload ./plan.html --visibility private --grant-email reader@example.test --grant-tailscale alice@github
tailplan share migration-plan --grant-tailscale alice@github --revoke-tailscale bob@passkey
```

The `tailplan-share FILE` command remains available.
Both upload commands accept `--slug`, `--description`, `--json`, and `--api-url`, and also accept folders of `.html` pages.
The CLI collects Git repository, branch, commit, dirty state, and supported CI metadata from the source directory.
An update retains the description and slug when those options are omitted.
`--metadata-file FILE` supplies a JSON object instead of automatic metadata collection.
Per-draft bindings are stored as `<sha256>.json` files under `~/.tailplan/drafts/` with read-only fallback to legacy `drafts.json`.
When a local server shares `~/.tailplan`, `drafts/` holds both `<draftId>/` server folders and `<sha256>.json` client bindings.

The SSH client supports upload, check, download, favorite, unfavorite, list, history, whoami, keys, disable, enable, and delete.
The SSH client keeps source mappings on the workstation and sends local Git metadata with each upload.
The direct client uses `TAILPLAN_API_KEY` or the configured token file.
`TAILPLAN_TOKEN_FILE` overrides `~/.tailplan/token`.

Draft responses include `publicUrl`, `rawUrl`, `versionUrl`, `versionId`, `versionNumber`, `fileSha256`, and `contentHash`.
Re-uploading identical content keeps the current version and returns `"unchanged": true`.
The canonical URL and its `/raw` alias return identical uploaded bytes.
Append `/v/N` or `/v/N/raw` to retrieve an immutable version.
Draft responses include `X-Tailplan-Draft-Id`, `X-Tailplan-Draft-Version`, and `X-Tailplan-Content-SHA256` headers.
Equivalent `X-Postplan-Draft-Id` and `X-Postplan-Draft-Version` headers support existing Postplan clients.

Authenticated HTML drafts may run inline JavaScript, including `type="module"` scripts and standard `on*` handlers, inside a sandboxed CSP.
The sandbox uses an opaque origin without `allow-same-origin` and sets `connect-src 'none'`, `worker-src 'none'`, `form-action 'none'`, `frame-src 'none'`, and `object-src 'none'`.
External HTTPS images, media, fonts, allowlisted `<link>` stylesheets or icons, and escaped popups remain permitted.
Script-free drafts, Markdown pages, anonymous uploads, and pre-0.0.6 versions keep `script-src 'none'`.
External scripts, unsupported script types, forms, embeds, meta refresh, and unsafe URL attributes are rejected.

### Server settings

| Setting | Default | Effect |
| --- | --- | --- |
| `TAILPLAN_TRUST_TAILSCALE_IDENTITY` | `0` | Trust identity headers only on the loopback proxy listener. |
| `TAILPLAN_OWNER_LOGIN` | Empty | Map this Tailscale login to the existing local account. |
| `TAILPLAN_ALLOWED_LOGINS` | Empty | Comma-separated Tailscale logins that may sign in. Empty allows any login. |
| `TAILPLAN_TAILSCALE_VIEWER` | `0` | Let the proxy listener show drafts from the Tailscale login header, as a viewer, with no sign-in step. |
| `TAILPLAN_VIEWER_404_LIMIT` | `30` | Maximum missing-draft responses per viewer each minute before `429`. |
| `TAILPLAN_ALLOW_ANONYMOUS_UPLOADS` | `0` | Allow uploads into a separate anonymous account. |
| `TAILPLAN_UPLOAD_IP_LIMIT` | `60` | Maximum uploads per client address each minute. |
| `TAILPLAN_UPLOAD_KEY_LIMIT` | `30` | Maximum uploads per API key each minute. |

Set these values in the installer environment or service environment file.
The native installer preserves existing values on later installations.
Enable identity-header trust only when Tailscale Serve is the sole remote path to the proxy listener.
Other tailnet users receive separate accounts when they sign in.
The owner mapping does not transfer drafts between accounts.

The APIs require bearer keys for management operations.
Browser sessions use signed cookies with a 30-day lifetime and form tokens.
Revoking a named key also invalidates browser sessions created with that key.
Set `TAILPLAN_SESSION_SECRET_FILE` (`--session-secret-file`) to sign cookies with a dedicated secret.
Without it, the server derives the cookie key from the upload token.
See `docs/security.md` for rotation rules.
Deleted drafts disappear from listings, and every viewer URL returns 404.
The files remain on disk for operator recovery.

## Security model

The tailnet is the default viewer security boundary.
The upload API requires an upload token unless anonymous publication is explicitly enabled.
Management accounts restrict draft updates, listings, history, and key operations.
With the default `TAILPLAN_VIEWER_AUTH_MODE=network`, viewer access remains a tailnet boundary.
In that mode, any tailnet user with a draft URL can view a `link` draft.
The other viewer modes require sign-in for some or all drafts.

Do not upload credentials or access links.
Do not upload personal, medical, legal, or financial records.
Use Tailscale access controls to restrict service access.
Read [the security policy](SECURITY.md) before a public deployment.

## Single sign-on, roles, and sharing

Tailplan 0.5.0 adds optional OpenID Connect (OIDC) sign-in.
Install the `oidc` extra to get PyJWT, for example `pip install "tailplan[oidc]"`.
The container image includes it.
Without `TAILPLAN_OIDC_ISSUER`, the server does not import PyJWT, and the standard library install works as before.

| Setting | Default | Effect |
| --- | --- | --- |
| `TAILPLAN_OIDC_ISSUER` | Empty | Issuer URL. Setting it turns on single sign-on. |
| `TAILPLAN_OIDC_CLIENT_ID` | Empty | Client ID that the identity provider gave Tailplan. |
| `TAILPLAN_OIDC_CLIENT_SECRET_FILE` | Empty | File with the client secret. Use mode `0600`. |
| `TAILPLAN_OIDC_SCOPES` | `openid profile email groups` | Scopes that Tailplan asks for. |
| `TAILPLAN_OIDC_GROUPS_CLAIM` | `groups` | Claim that holds the group list. |
| `TAILPLAN_OIDC_ALLOW_PRIVATE_ISSUER` | `0` | Allow an issuer on a private address or plain HTTP. Use it only for tests. |
| `TAILPLAN_OIDC_LINK_EMAILS` | Empty | Verified emails whose first sign-in may link to an existing account. |
| `TAILPLAN_AUTH_MODES` | See below | Comma-separated sign-in methods: `oidc`, `api_key`, `tailscale`. |
| `TAILPLAN_RBAC_POLICY_FILE` | Empty | JSON file that maps groups and emails to roles. |
| `TAILPLAN_VIEWER_AUTH_MODE` | `network` | `network`, `per-draft`, or `session`. |
| `TAILPLAN_MAX_VISIBILITY` | `link` | Widest visibility that any draft can have. |
| `TAILPLAN_SESSION_TTL_SECONDS` | `604800` | Lifetime of a browser session. |

With an issuer, the default sign-in methods are `oidc` and `api_key`.
Without one, they are `api_key` and `tailscale`.
Register `<base URL>/auth/oidc/callback` as the redirect URI.
Register `<base URL>/auth/oidc/backchannel-logout` as the back-channel logout URI.
Keycloak refuses a scope that the client does not have.
For Keycloak, set `TAILPLAN_OIDC_SCOPES=openid profile email` and add a group membership mapper to the client.

The policy file assigns roles.
A sign-in that matches no rule gets `403`, and the audit log records `auth.denied`.
A `tailscale_login` rule matches a Tailscale login that is not an email address, such as `alice@github`.
An `email` rule needs a dotted domain.

```json
{
  "default_workspace": "team",
  "rules": [
    {"match": {"group": "staff"}, "workspaces": {"team": "editor"}},
    {"match": {"group": "readers"}, "workspaces": {"team": "viewer"}},
    {"match": {"group": "auditors"}, "instance_role": "auditor"},
    {"match": {"email": "owner@example.test"}, "instance_role": "admin"},
    {"match": {"tailscale_login": "alice@github"}, "workspaces": {"team": "viewer"}}
  ]
}
```

Workspace roles are `viewer`, `editor`, and `admin`.
Instance roles are `admin` and `auditor`.
The bootstrap upload token has the `admin` role.
An API key has the roles of its account, and no more.

Each draft has one of four visibilities:

| Visibility | Readers |
| --- | --- |
| `private` | The owner, editors, and people or groups with a grant. |
| `workspace` | Members of the draft's workspace, plus grants. |
| `authenticated` | Any signed-in user. |
| `link` | Anyone who can reach the URL, except in `session` mode. |

The viewer mode sets the default visibility and the sign-in rule:

| Viewer mode | Default visibility | Sign-in |
| --- | --- | --- |
| `network` | `link` | Only for drafts that are not `link`. |
| `per-draft` | `workspace` | Only for drafts that are not `link`. |
| `session` | `workspace` | For every draft. |

Draft pages, `/source`, and the draft API use the same check.
A signed-out browser goes to the sign-in page.
A signed-out API call gets `401`.
A signed-in reader without access gets `404`, so the draft stays hidden.

The dashboard shows a sharing card on each draft, a visibility badge, and a "Shared with me" section.
The API has `GET` and `POST /api/drafts/<ref>/sharing`, plus `POST` and `DELETE /api/drafts/<ref>/sharing/grants`.
The `tailplan share` command uses these routes.
Grant types are `email`, `tailscale_login`, `group`, and `workspace`.
`tailplan upload` can also send `--grant-email`, `--grant-tailscale`, `--grant-group`, and `--grant-role`.
The server writes those grants with the draft in one transaction.

With `TAILPLAN_TAILSCALE_VIEWER=1`, a tailnet user or sharee opens a draft link and reads it at once.
The server reads the Tailscale login header on the proxy listener, for draft pages only.
It gives the `viewer` role and keeps no session.
The dashboard, the API, and every change still need a sign-in or an API key.

`tailplan auth login --device` prints a link and a code.
Open the link, check the code, and approve.
The CLI then saves a new API key that expires after 90 days.

Admins and auditors can read the audit log at `GET /api/audit`.
The server also writes each event to standard output as one JSON line.
Read [the security model](docs/security.md) for the details.

## Choose a deployment

| Deployment | Use |
| --- | --- |
| Local process | Evaluate Tailplan or develop it without a service. |
| Docker Compose | Run an isolated server with persistent container data. |
| Native user service | Run Tailplan for one Linux user. |
| Native system service | Run Tailplan as a dedicated system account. |
| Remote client | Publish from a tailnet workstation without an upload token. |

The local process and Docker Compose do not configure HTTPS or Tailscale Serve.
Before you give other devices access, use an HTTPS proxy with a certificate that every viewer device validates.

## Requirements

All server deployments need a verified Tailplan release.

A local process needs Linux and Python 3.11 through 3.13.
The local publisher also needs Bash and curl.

The Docker deployment needs Docker Engine and Docker Compose v2.

A native service needs Linux, Python 3.11 through 3.13, Bash, curl, GNU core utilities, and systemd.
The default native deployment also needs Tailscale.

A remote client installation needs Bash, Python 3.11 through 3.13, OpenSSH `ssh`, and OpenSSH `scp`.
The remote client uses legacy SCP protocol mode with a forced-command upload path.
Use an OpenSSH version that supports the `scp -O` option.

## Verify release source

Source authenticity and archive integrity are different checks.
The repository authorizes the release SSH key in protected `main`.
The signed annotated tag does not depend on a GitHub account signing identity.
The SHA-256 file detects archive changes after the release workflow creates the file.
A checksum from the same release does not authenticate the source by itself.

Install Git and the GitHub CLI before source verification.
Authenticate the GitHub CLI to GitHub.
Replace `<owner>` with the public repository owner:

```sh
REPOSITORY='<owner>/taildoc'
TAG='v0.2.1'
SOURCE_DIR="$(mktemp -d)"
git clone --filter=blob:none --no-checkout --no-tags \
  "https://github.com/${REPOSITORY}.git" "$SOURCE_DIR"
git -C "$SOURCE_DIR" fetch --force --no-tags origin \
  "refs/heads/main:refs/remotes/origin/main"
git -C "$SOURCE_DIR" fetch --force --no-tags origin \
  "refs/tags/${TAG}:refs/tags/${TAG}"
ALLOWED_SIGNERS="$SOURCE_DIR/release-allowed-signers"
git -C "$SOURCE_DIR" show \
  "refs/remotes/origin/main:.github/release-allowed-signers" > "$ALLOWED_SIGNERS"
TAG_REF="refs/tags/${TAG}"
test "$(git -C "$SOURCE_DIR" cat-file -t "$TAG_REF")" = tag
test "$(git -C "$SOURCE_DIR" for-each-ref --format='%(tag)' "$TAG_REF")" = "$TAG"
COMMIT_SHA="$(git -C "$SOURCE_DIR" rev-parse "${TAG_REF}^{commit}")"
test "$COMMIT_SHA" = "$(git -C "$SOURCE_DIR" rev-parse refs/remotes/origin/main)"
git -C "$SOURCE_DIR" \
  -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile="$ALLOWED_SIGNERS" \
  verify-tag "$TAG_REF"
printf 'Verified release commit: %s\n' "$COMMIT_SHA"
```

The tag-object check rejects a lightweight tag.
The tag-name check requires the tag object name to match the release tag reference.
The commit check requires the tag target to equal the protected `main` commit from the repository.
The SSH check trusts only the public keys in the allowed-signers file from protected `main`.
Do not download release assets when a source verification command fails.

Verify both release assets with the repository, workflow, tag-reference, and source-digest options in the following commands:

```sh
gh release download "$TAG" \
  --repo "$REPOSITORY" \
  --pattern 'tailplan-0.2.1.tar.gz' \
  --pattern 'tailplan-0.2.1.tar.gz.sha256'
gh attestation verify tailplan-0.2.1.tar.gz \
  --repo "$REPOSITORY" \
  --signer-workflow "${REPOSITORY}/.github/workflows/release.yml" \
  --source-ref "refs/tags/${TAG}" \
  --source-digest "$COMMIT_SHA" \
  --deny-self-hosted-runners
gh attestation verify tailplan-0.2.1.tar.gz.sha256 \
  --repo "$REPOSITORY" \
  --signer-workflow "${REPOSITORY}/.github/workflows/release.yml" \
  --source-ref "refs/tags/${TAG}" \
  --source-digest "$COMMIT_SHA" \
  --deny-self-hosted-runners
sha256sum --check tailplan-0.2.1.tar.gz.sha256
```

The archive attestation proves that the repository release workflow produced the archive for the verified source commit.
The checksum attestation proves that the same workflow produced the checksum file for the verified source commit.
The SHA-256 result proves that the downloaded archive matches the attested checksum file.

Do not install the archive when an attestation or checksum command fails.
Keep the verified commit, attestation results, and checksum result with the deployment record.

## Run Tailplan locally

Use the local process to evaluate or develop Tailplan.
Run these commands in sequence:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
install -d -m 700 "$HOME/.tailplan"
umask 077
python -c 'import secrets; print(secrets.token_urlsafe(32))' > "$HOME/.tailplan/token"
tailplan-server \
  --host 127.0.0.1 \
  --port 9127 \
  --data-dir "$HOME/.tailplan" \
  --token-file "$HOME/.tailplan/token" \
  --base-url http://127.0.0.1:9127
```

Keep the server process open.
Run the following commands in a second shell:

```sh
printf '# First Tailplan draft\n' > /tmp/tailplan-example.md
bin/tailplan-share /tmp/tailplan-example.md \
  --new \
  --base-url http://127.0.0.1:9127 \
  --allow-insecure-http
curl -fsS http://127.0.0.1:9127/readyz
```

Use HTTP only for a loopback evaluation.
Press `Ctrl-C` in the server shell to stop Tailplan.

## Run Tailplan with Docker Compose

Create the upload token on the Docker host:

```sh
install -d -m 700 "$HOME/.tailplan"
umask 077
test -s "$HOME/.tailplan/token" || \
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$HOME/.tailplan/token"
docker compose up --build --detach
docker compose ps
curl -fsS http://127.0.0.1:9127/readyz
```

Compose mounts the upload-token file as a read-only runtime secret.
The image does not contain the upload token.
The `tailplan-data` named volume stores drafts across container restarts.
The entrypoint starts as root to read the runtime secret.
The entrypoint copies the upload token to a memory-backed file.
The entrypoint drops all groups and starts the server as user `10001`.
The image health check requests `/readyz`.
To use a dedicated cookie secret, mount a file in the container and set `TAILPLAN_SESSION_SECRET_FILE` to its path.
The entrypoint copies that file to the same memory-backed directory.

The default port binds only to host loopback.
Set the bind address, port, and public base URL for an HTTPS proxy:

WARNING: Do not bind the Tailplan HTTP listener to `0.0.0.0` until an HTTPS proxy and network access controls are active.
Without these controls, other network devices can read Tailplan drafts over HTTP.

```sh
TAILPLAN_BIND_ADDRESS=0.0.0.0 \
TAILPLAN_PORT=9127 \
TAILPLAN_BASE_URL=https://tailplan.example.test \
docker compose up --build --detach
```

The Compose file does not include network access controls.
The Compose file does not include an HTTPS proxy or Tailscale.

### Container image and Tailscale sidecar

The release workflow publishes `ghcr.io/justcarlson/tailplan` for each push to `main` and each `v*` tag.
Each image has a build provenance attestation.
Use the `sha-<short>` tag to pin a commit and the `<version>` tag to pin a release.

To use Tailscale identity in a container, run the container in the network namespace of a Tailscale sidecar.
Set two listeners in that shared namespace.
The proxy listener must use a loopback address:

```yaml
services:
  tailplan:
    image: ghcr.io/justcarlson/tailplan:0.6.0
    network_mode: service:ts-tailplan
    environment:
      TAILPLAN_HOST: 127.0.0.1
      TAILPLAN_PORT: 9127
      TAILPLAN_PROXY_HOST: 127.0.0.1
      TAILPLAN_PROXY_PORT: 9128
      TAILPLAN_TRUST_TAILSCALE_IDENTITY: 1
      TAILPLAN_OWNER_LOGIN: owner@example.test
      TAILPLAN_ALLOWED_LOGINS: owner@example.test,guest@example.test
      TAILPLAN_BASE_URL: https://tailplan.example.test
```

Point Tailscale Serve at `127.0.0.1:9128`.
The image health check requests `/readyz` on `TAILPLAN_PORT`, so it passes in both modes.

Publish from the Docker host with the local publisher command:

```sh
bin/tailplan-share ./plan.md \
  --new \
  --base-url http://127.0.0.1:9127 \
  --allow-insecure-http
```

## Install a native server
### User service

Run the installer as the service user:

```sh
./install.sh
```

The user service starts after the user systemd instance starts.
The default data directory is `~/.tailplan`.
The installer keeps an existing upload token.

### System service

Run the installer with root privileges.
Name the non-root account that will run the local publisher:

```sh
sudo ./install.sh --system --operator "$USER"
```

The operator must be an existing non-root login account.
The operator must own a non-symlink home directory.
The home directory must not be writable by a group or other users.
The installer validates the account before it changes the host.

The installer creates `~/.tailplan` in the operator home with mode `0700`.
The installer copies the upload token to `~/.tailplan/token` and the base URL to `~/.tailplan/env`.
Both files have mode `0600`, and the operator owns both files.

A direct `sudo ./install.sh --system` invocation can infer `SUDO_USER`.
The installer accepts the inferred account only after the same account and home checks.
For automation, set the operator explicitly:

```sh
sudo TAILPLAN_OPERATOR="$USER" ./install.sh --system
```

If the `tailplan` service account does not exist, the installer creates and locks the account.
The system service starts with the machine.
The default data directory is `/var/lib/tailplan`.

Use this command to rotate the upload token:

```sh
sudo ./install.sh --system --operator "$USER" --rotate-token
```

Upload-token rotation invalidates the previous upload token.
Run `install-ssh-publisher.sh` again with the same options after upload-token rotation.

Rerun the initial `--operator` command if either operator file is missing.
The installer keeps the upload token and creates both operator files again.

### Installation paths

| Item | User service | System service |
| --- | --- | --- |
| Application | `~/apps/tailplan` | `/opt/tailplan` |
| Commands | `~/.local/bin` | `/usr/local/bin` |
| Data and upload-token file | `~/.tailplan` | `/var/lib/tailplan` |
| Environment | `~/.tailplan/env` | `/etc/tailplan.env` |
| Unit | `~/.config/systemd/user/tailplan.service` | `/etc/systemd/system/tailplan.service` |
| Backups | `~/.tailplan-backups` | `/var/backups/tailplan` |

The installer validates all source files before the installer changes the host.
The installer creates a mode `0700` backup before each installation.
The installer verifies the service and each managed endpoint.
The installer restores the previous deployment after an installation failure.
The installer never prints the upload token.

### Tailscale Serve deployment

The default deployment creates two listeners:

- The primary listener uses the Tailscale IPv4 address and port `9127`.
- The proxy listener uses `127.0.0.1` and port `9128`.

The installer adds only the `/tailplan` Serve handler.
The installer keeps unrelated Serve handlers.
The public base format is `https://<node>.<tailnet>.ts.net/tailplan`.
Replace each bracketed value with local Tailscale data.

Use loopback for a container that uses Tailscale userspace networking:

```sh
sudo env TAILPLAN_HOST=127.0.0.1 \
  ./install.sh --system --defer-https-verify
```

Then verify HTTPS from a second tailnet device:

```sh
curl -fsS https://<node>.<tailnet>.ts.net/tailplan/healthz
curl -fsS https://<node>.<tailnet>.ts.net/tailplan/readyz
```

If either `curl` command fails, do not complete the cutover.
Install the previous verified release to restore the previous application version.

### Deployment without Tailscale Serve

Use `--no-serve` when an external HTTPS proxy serves Tailplan.
Set `TAILPLAN_BASE_URL` to the absolute HTTPS URL of the proxy.
If `tailscale ip -4` does not return an IPv4 address, set `TAILPLAN_HOST` to the listener address.

```sh
TAILPLAN_HOST=127.0.0.1 \
TAILPLAN_BASE_URL=https://tailplan.example.test/tailplan \
./install.sh --no-serve
```

Add `--system` for a system service.
The loopback proxy listener remains available on port `9128`.

The installer supports these common overrides:

- `TAILPLAN_APP_DIR`
- `TAILPLAN_BIN_DIR`
- `TAILPLAN_DATA_DIR`
- `TAILPLAN_BACKUP_DIR`
- `TAILPLAN_HOST`
- `TAILPLAN_PORT`
- `TAILPLAN_PROXY_HOST`
- `TAILPLAN_PROXY_PORT`
- `TAILPLAN_BASE_URL`

System mode also supports these overrides:

- `TAILPLAN_ENV_FILE`
- `TAILPLAN_UNIT_FILE`
- `TAILPLAN_USER`
- `TAILPLAN_GROUP`

Run `./install.sh --help` for installation modes.

Set common overrides in the environment for a user service:

```sh
TAILPLAN_APP_DIR="$HOME/apps/tailplan" \
TAILPLAN_DATA_DIR="$HOME/.tailplan" \
./install.sh
```

## Install only the remote client

Use the remote client on a tailnet workstation.
The remote client does not need the upload token.
The remote client sends the source file through OpenSSH.
The server runs the authenticated `tailplan-share` command.

Run the remote client installer:

```sh
./install-client.sh
```

The installer creates these files:

- `~/.local/bin/tailplan-share`
- `~/.agents/skills/tailplan/SKILL.md`

If the shell cannot find `tailplan-share`, add `~/.local/bin` to `PATH`.
Create an SSH alias named `tailplan-server`:

```sshconfig
Host tailplan-server
    HostName <tailnet-dns-name>
    User <publisher-account>
    IdentityFile ~/.ssh/<publisher-key>
    IdentitiesOnly yes
```

Test the alias before publication:

```sh
ssh tailplan-server true
```

A forced-command account can reject `true` by design.
For that account, test one publication with a file that contains no credential, upload token, or personal data.

Use a non-default SSH alias for one command:

```sh
tailplan-share ./plan.md --new --target <ssh-alias>
```

Set `TAILPLAN_SSH_TARGET` to change the default for a shell or service.

### Install a forced-command SSH publisher account

Install the native server before you create the publisher account.
Create a dedicated SSH key for the publisher.
Run this command when Tailplan uses the native system service:

```sh
sudo ./install-ssh-publisher.sh \
  --public-key-file ./tailplan-publisher.pub
```

Supply these paths when Tailplan uses the native user service:

```sh
sudo ./install-ssh-publisher.sh \
  --public-key-file ./tailplan-publisher.pub \
  --token-file "$HOME/.tailplan/token" \
  --env-file "$HOME/.tailplan/env" \
  --share-command "$HOME/.local/bin/tailplan-share"
```

The installer creates a dedicated `tailplan-publisher` account by default.
The installer installs the forced-command guard.
The installer copies the upload token only to the dedicated publisher account on the server.
The installer copies the selected local publisher to `/usr/local/libexec/tailplan-share`.
The installed copy is root-owned and has mode `0755`.
The forced-command guard uses the installed copy.
It does not execute the source below the operator home.
The installer sets the publisher account password field to `*`.
No password can produce this value.
OpenSSH can still use the configured public key because the account is not locked.
The installer does not change the OpenSSH password-authentication policy.
Run `./install-ssh-publisher.sh --help` for account and path overrides.
Run the same SSH publisher installer command after every Tailplan upgrade.
The normal Tailplan installer does not update the isolated local publisher.
Complete this step before the next forced-command publication.

The installer writes this forced-command key form:

```text
restrict,command="/usr/local/libexec/tailplan-publish-guard" <key-type> <public-key>
```

Do not copy the upload token to a workstation.
Disable forwarding, terminal access, and agent forwarding for the key.

The guard must validate `SSH_ORIGINAL_COMMAND` without a shell evaluation.
The guard must allow only these command forms:

```text
mktemp -d /tmp/tailplan-upload.XXXXXXXX
scp -t /tmp/tailplan-upload.<eight-allowed-characters>/<allowed-filename>
tailplan-share [--files-json] /tmp/tailplan-upload.<eight-allowed-characters>/<allowed-filename> [validated publish options]
tailplan-share check [staged file or --files-json] [--slug <slug>] [--json]
tailplan-share download <ref> --print-source
tailplan-share <list|whoami|themes|typography|history|disable|enable|delete|favorite|unfavorite|keys> [validated args]
tailplan-share share <ref> [validated sharing options] [--json]
rm -rf -- /tmp/tailplan-upload.<eight-allowed-characters>
```

The guard must reject every other command.
The guard must accept only the shown temporary path prefix.
The guard must reject path traversal and extra arguments.
The `scp -O` option keeps uploads on the forced-command SCP path.

## Publish a draft

Create a new private draft:

```sh
tailplan-share ./plan.md --new
```

Update a known draft:

```sh
tailplan-share ./plan.md --draft <draft-id>
```

The local publisher can also reuse the source mapping:

```sh
tailplan-share ./plan.md
```

The command prints a URL:

```text
URL: https://<node>.<tailnet>.ts.net/tailplan/d/<draft-id>
```

Use Markdown pipe tables for tabular data:

```markdown
| Task | Owner | Status |
| --- | --- | --- |
| Verify mobile layout | Release team | Done |
```

Tailplan adds a horizontal scroll area around each table.

## Mirror one draft publicly

Run this command only after the user explicitly approves public access for the selected draft:

```sh
tailplan-share-public '<tailplan-url-or-draft-id>' \
  --i-approve-public-sharing --new
```

The command creates a separate public Postplan URL.
The command does not expose the Tailplan service.

## Service operations

Check a user service:

```sh
systemctl --user status tailplan.service
journalctl --user -u tailplan.service -n 100 --no-pager
```

Check a system service:

```sh
sudo systemctl status tailplan.service
sudo journalctl -u tailplan.service -n 100 --no-pager
```

Verify the listeners:

```sh
curl -fsS http://<tailscale-ip>:9127/healthz
curl -fsS http://127.0.0.1:9128/readyz
tailscale serve status
curl -fsS https://<node>.<tailnet>.ts.net/tailplan/readyz
```

The primary listener must not use `0.0.0.0`.
The proxy listener must use a loopback address.

## Metadata storage

Tailplan keeps draft metadata in `tailplan.db`, a SQLite database in the data directory.
Draft HTML files stay on disk under `drafts/`, as in earlier releases.
The database holds accounts, drafts, versions, API key hashes, and request keys.
The server opens it in write-ahead log mode, so a running server also uses `tailplan.db-wal` and `tailplan.db-shm`.

The first start imports `metadata.json` into `tailplan.db` without any manual step.
The server checks every imported record before it finishes.
It then saves the original file as `metadata.json.pre-sqlite.bak` with mode `0600` and removes `metadata.json`.
When `tailplan.db` already exists, the server uses it and ignores `metadata.json`.

The `/readyz` check runs a SQLite quick check and a write probe.
It also confirms that each current draft file exists.
It does not read or hash draft files, so it stays fast with many drafts.
The viewer still checks the hash of a draft file each time it serves the draft.

Back up the whole data directory while the server is stopped.
To roll back to version 0.4.0, stop the server and move `tailplan.db`, `tailplan.db-wal`, and `tailplan.db-shm` out of the data directory.
Copy `metadata.json.pre-sqlite.bak` to `metadata.json`, then start the older release.
The older release does not see changes made after the upgrade.
Version 0.5.0 upgrades the database schema to version 2 at start.
A release that knows only schema version 1 refuses to open it.
Version 0.6.0 upgrades the schema to version 3, which adds the `tailscale_login` grant type.
A 0.5.0 release refuses to open a version 3 database.

## Upgrade, rollback, and removal

### Local process

Activate the virtual environment.
Run `python -m pip install -e .` again after each verified source update.
Remove `.venv` to remove the local Python installation.
Keep `~/.tailplan` when you must keep the upload token, drafts, or source mappings.

### Docker Compose

Stop Tailplan before an upgrade.
Back up the named volume:

```sh
docker compose stop
docker run --rm \
  --volume tailplan_tailplan-data:/source:ro \
  --volume "$PWD":/backup \
  alpine:3.22 \
  tar -C /source -czf /backup/tailplan-data.tar.gz .
```

Verify the new release source.
Extract the new release source.
Build the container image:

```sh
docker compose build --pull
docker compose up --detach
curl -fsS http://127.0.0.1:${TAILPLAN_PORT:-9127}/readyz
```

Use the previous verified source to roll back the container image.
Restore the volume backup only when the release notes require a data rollback.

The following command stops the container.
The command removes the container without data loss:

```sh
docker compose down --remove-orphans
```

`docker compose down --volumes --remove-orphans` deletes the named volume and all stored drafts.
Run the command only after you verify a data backup:

```sh
docker compose down --volumes --remove-orphans
```

Remove the old version images separately:

```sh
docker image rm tailplan:0.2.1
docker image rm tailplan:0.3.0
docker image rm tailplan:0.4.0
docker image rm tailplan:0.5.0
```

### Native service

Verify the new release.
Extract the new release.
Run the same installer command that created the service.
The installer keeps the upload token and drafts.
The installer creates a backup and prints its path.
If this server has an SSH publisher account, rerun the SSH publisher installer next.
Use the same options that created the publisher account.

Use the previous verified release for an application rollback.
Run the same installer command again.
The installer verifies the restored version.

Remove a user service:

```sh
systemctl --user disable --now tailplan.service
rm -f "$HOME/.config/systemd/user/tailplan.service"
systemctl --user daemon-reload
rm -rf "$HOME/apps/tailplan"
rm -f "$HOME/.local/bin/tailplan-share"
rm -f "$HOME/.local/bin/tailplan-share-public"
skills_root="${TAILPLAN_SKILLS_ROOT:-$HOME/.agents/skills}"
rm -f -- "$skills_root/tailplan/SKILL.md"
rmdir -- "$skills_root/tailplan" 2>/dev/null || true
```

Remove a system service:

```sh
sudo systemctl disable --now tailplan.service
sudo rm -f /etc/systemd/system/tailplan.service
sudo systemctl daemon-reload
sudo rm -rf /opt/tailplan
sudo rm -f /usr/local/bin/tailplan-share
sudo rm -f /usr/local/bin/tailplan-share-public
skills_root="${TAILPLAN_SKILLS_ROOT:-$HOME/.agents/skills}"
sudo rm -f -- "$skills_root/tailplan/SKILL.md"
sudo rmdir -- "$skills_root/tailplan" 2>/dev/null || true
```

Use the configured paths instead when the installation used overrides.
Use the same `TAILPLAN_SKILLS_ROOT` value that the installer used.
Each `rmdir` command removes only an empty Tailplan skill directory.
Files for other skills remain in the skills root.

System-service removal keeps the operator upload-token file and environment file.
Remove the operator copies when this operator will not publish to this Tailplan server:

```sh
rm -f -- "$HOME/.tailplan/token" "$HOME/.tailplan/env"
```
The service removal commands keep server data, the server upload-token file, the server environment file, backups, and the service account.
Copy the drafts before you remove the data directory.
Remove the dedicated `tailplan` account only after you remove all owned data.

Remove the Tailscale Serve handler when the installer configured it:

```sh
sudo tailscale serve --https=443 --set-path=/tailplan off
```

### Remote client

Run `./install-client.sh` again from a verified release to upgrade the remote client.
Remove the remote client files:

```sh
./install-client.sh --uninstall
```

The remote client removal does not remove server data or credentials.

Remove the default forced-command publisher account from its server:

```sh
sudo userdel --remove tailplan-publisher
sudo rm -f /usr/local/libexec/tailplan-publish-guard
sudo rm -f /usr/local/libexec/tailplan-share
sudo rm -f /etc/tailplan-publisher.json
```

Use the configured account name when the publisher installation used an override.
This removal does not remove the upload token on the Tailplan server or drafts.

## Development

Run these checks:

```sh
python -m pytest -q
bash tests/smoke.sh
python tests/check_ste100.py
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before a change.
The project uses ASD-STE100 Simplified Technical English for documents and source comments.

## Repository layout

```text
Dockerfile                   Container image
compose.yaml                 Container deployment example
tailplan_server.py           HTTP service
install.sh                   Native server installer
install-client.sh            Remote client installer
install-ssh-publisher.sh      Forced-command publisher installer
bin/run-tailplan             Service launcher
bin/tailplan-share           Local publisher
bin/tailplan-share-remote    Remote client
bin/tailplan-publish-guard    Forced-command publisher guard
bin/tailplan-share-public    Approval-gated public mirror
skills/tailplan/SKILL.md     Agent publishing skill
systemd/tailplan.service     Service unit template
tests/                       Test suite and smoke test
```

## License

Tailplan uses the MIT License.
Read [LICENSE](LICENSE) for the legal terms.

## Choose document typography

Typography is independent of the color palette.
The default `serif-sans` preset uses semibold Iowan Old Style headings, with Palatino and Georgia fallbacks, and system sans-serif body text.
The `all-sans` preset uses system sans-serif headings and body text.
Code stays monospace with both presets.

```sh
tailplan typography --json
tailplan upload plan.md --new --typography serif-sans
tailplan-share notes.md --new --theme nord --typography all-sans
```

These commands work through direct and SSH clients.
See [document themes](docs/themes.md#typography-presets) for API fields, preset extension, and immutable snapshots.
