# Tailplan

Tailplan publishes static drafts inside a Tailscale tailnet.
Agents can publish plans, reports, Markdown files, text files, and HTML files without active content.
Tailplan returns a URL that tailnet devices can open.

Read [SUPPORT.md](SUPPORT.md) for compatibility and support information.
Read [CHANGELOG.md](CHANGELOG.md) for release changes.

## Features

- Tailplan serves each draft at `/d/<draft-id>`.
- Tailplan accepts authenticated uploads at `POST /api/uploads`.
- Tailplan stores each draft on local disk.
- Tailplan binds the primary listener to a Tailscale address by default.
- Tailplan can use Tailscale Serve for HTTPS access.
- Tailplan rejects active HTML content and prohibited URL attributes.
- Tailplan renders Markdown tables as responsive HTML tables.
- Tailplan can mirror one approved draft to Postplan.

## Security model

The tailnet is the default viewer security boundary.
The upload API also requires an upload token.
Tailplan does not provide individual viewer accounts.
Any tailnet user with a draft URL can view that draft.

Do not upload credentials or access links.
Do not upload personal, medical, legal, or financial records.
Use Tailscale access controls to restrict service access.
Read [the security policy](SECURITY.md) before a public deployment.

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
tailplan-share /tmp/tailplan-upload.<eight-allowed-characters>/<allowed-filename> [--new|--draft <draft-id>]
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

Remove the version 0.2.1 image separately:

```sh
docker image rm tailplan:0.2.1
```

### Native service

Verify the new release.
Extract the new release.
Run the same installer command that created the service.
The installer keeps the upload token and drafts.
The installer creates a backup and prints its path.

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
