#!/usr/bin/env python3
import os
import sys
from pathlib import Path


RUNTIME_UID = 10001
RUNTIME_GID = 10001
RUNTIME_DIRECTORY = Path("/run/tailplan")
RUNTIME_TOKEN = RUNTIME_DIRECTORY / "upload-token"
RUNTIME_SESSION_SECRET = RUNTIME_DIRECTORY / "session-secret"
RUNTIME_OIDC_CLIENT_SECRET = RUNTIME_DIRECTORY / "oidc-client-secret"
# Optional settings. An empty value, such as Compose passes for an unset
# `${NAME:-}`, means "not set", so the server must not see it.
OPTIONAL_ENVIRONMENT = (
    "TAILPLAN_PROXY_HOST",
    "TAILPLAN_PROXY_PORT",
    "TAILPLAN_TRUST_TAILSCALE_IDENTITY",
    "TAILPLAN_OWNER_LOGIN",
    "TAILPLAN_ALLOWED_LOGINS",
    "TAILPLAN_VIEWER_404_LIMIT",
    "TAILPLAN_REDIRECT_VIEW_BASE_URL",
    "TAILPLAN_SESSION_SECRET_FILE",
    "TAILPLAN_SESSION_SECRET",
    "TAILPLAN_AUTH_MODES",
    "TAILPLAN_OIDC_ISSUER",
    "TAILPLAN_OIDC_CLIENT_ID",
    "TAILPLAN_OIDC_CLIENT_SECRET_FILE",
    "TAILPLAN_OIDC_SCOPES",
    "TAILPLAN_OIDC_GROUPS_CLAIM",
    "TAILPLAN_OIDC_ALLOW_PRIVATE_ISSUER",
    "TAILPLAN_OIDC_LINK_EMAILS",
    "TAILPLAN_SESSION_TTL_SECONDS",
    "TAILPLAN_RBAC_POLICY_FILE",
    "TAILPLAN_VIEWER_AUTH_MODE",
    "TAILPLAN_MAX_VISIBILITY",
    "TAILPLAN_OIDC_CLIENT_SECRET",
)


def drop_empty_optional_environment(environ: "os._Environ[str] | dict[str, str]") -> None:
    for name in OPTIONAL_ENVIRONMENT:
        if name in environ and not environ[name].strip():
            del environ[name]


def install_runtime_secret(secret: bytes, destination: Path) -> None:
    """Write a secret that only the runtime user can read."""
    destination.unlink(missing_ok=True)
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o400,
    )
    try:
        remaining = memoryview(secret)
        while remaining:
            remaining = remaining[os.write(descriptor, remaining) :]
        os.fchmod(descriptor, 0o400)
        os.fchown(descriptor, RUNTIME_UID, RUNTIME_GID)
    finally:
        os.close(descriptor)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("container command is required")

    drop_empty_optional_environment(os.environ)
    source = Path(os.environ["TAILPLAN_TOKEN_FILE"])
    token = source.read_bytes()
    if not token.strip():
        raise SystemExit("upload token is empty")
    session_source = os.environ.get("TAILPLAN_SESSION_SECRET_FILE")
    session_secret = b""
    if session_source:
        session_secret = Path(session_source).read_bytes()
        if not session_secret.strip():
            raise SystemExit("session secret is empty")
    oidc_source = os.environ.get("TAILPLAN_OIDC_CLIENT_SECRET_FILE")
    oidc_secret = b""
    if oidc_source:
        oidc_secret = Path(oidc_source).read_bytes()
        if not oidc_secret.strip():
            raise SystemExit("OIDC client secret is empty")

    RUNTIME_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(RUNTIME_DIRECTORY, 0, 0)
    os.chmod(RUNTIME_DIRECTORY, 0o700)
    install_runtime_secret(token, RUNTIME_TOKEN)
    if session_secret:
        install_runtime_secret(session_secret, RUNTIME_SESSION_SECRET)
        os.environ["TAILPLAN_SESSION_SECRET_FILE"] = str(RUNTIME_SESSION_SECRET)
    if oidc_secret:
        install_runtime_secret(oidc_secret, RUNTIME_OIDC_CLIENT_SECRET)
        os.environ["TAILPLAN_OIDC_CLIENT_SECRET_FILE"] = str(RUNTIME_OIDC_CLIENT_SECRET)
    os.chown(RUNTIME_DIRECTORY, RUNTIME_UID, RUNTIME_GID)

    os.environ["TAILPLAN_TOKEN_FILE"] = str(RUNTIME_TOKEN)
    os.setgroups([])
    os.setgid(RUNTIME_GID)
    os.setuid(RUNTIME_UID)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
