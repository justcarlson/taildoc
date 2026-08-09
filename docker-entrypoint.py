#!/usr/bin/env python3
import os
import sys
from pathlib import Path


RUNTIME_UID = 10001
RUNTIME_GID = 10001
RUNTIME_DIRECTORY = Path("/run/tailplan")
RUNTIME_TOKEN = RUNTIME_DIRECTORY / "upload-token"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("container command is required")

    source = Path(os.environ["TAILPLAN_TOKEN_FILE"])
    token = source.read_bytes()
    if not token.strip():
        raise SystemExit("upload token is empty")

    RUNTIME_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(RUNTIME_DIRECTORY, 0, 0)
    os.chmod(RUNTIME_DIRECTORY, 0o700)
    RUNTIME_TOKEN.unlink(missing_ok=True)

    descriptor = os.open(
        RUNTIME_TOKEN,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o400,
    )
    try:
        remaining = memoryview(token)
        while remaining:
            remaining = remaining[os.write(descriptor, remaining) :]
        os.fchmod(descriptor, 0o400)
        os.fchown(descriptor, RUNTIME_UID, RUNTIME_GID)
    finally:
        os.close(descriptor)
    os.chown(RUNTIME_DIRECTORY, RUNTIME_UID, RUNTIME_GID)

    os.environ["TAILPLAN_TOKEN_FILE"] = str(RUNTIME_TOKEN)
    os.setgroups([])
    os.setgid(RUNTIME_GID)
    os.setuid(RUNTIME_UID)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
