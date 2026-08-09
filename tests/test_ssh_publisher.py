from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "bin" / "tailplan-publish-guard"
INSTALLER = ROOT / "install-ssh-publisher.sh"
README = ROOT / "README.md"
loader = importlib.machinery.SourceFileLoader("tailplan_publish_guard", str(GUARD))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def publisher_installer_prefix() -> list[str]:
    if os.geteuid() == 0:
        return []
    reason = "The SSH publisher installer test requires root or support for `unshare -Ur`."
    try:
        probe = subprocess.run(
            ["unshare", "-Ur", "true"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        pytest.skip(reason)
    if probe.returncode != 0:
        pytest.skip(reason)
    return ["unshare", "-Ur"]


def write_command_mock(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
name="$(basename "$0")"
printf '%s' "$name" >> "$MOCK_LOG"
for argument in "$@"; do
  printf ' <%s>' "$argument" >> "$MOCK_LOG"
done
printf '\\n' >> "$MOCK_LOG"
case "$name" in
  getent) exit 2 ;;
  install)
    source="${@: -2:1}"
    destination="${@: -1}"
    if [[ "$destination" == /etc/tailplan-publisher.json ]]; then
      /usr/bin/cp "$source" "$MOCK_CONFIG"
    fi
    ;;
  id) printf 'tailplan-publisher\\n' ;;
esac
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_guard_allows_only_the_scoped_creation_command() -> None:
    with (
        patch.object(mod.os, "execv", side_effect=SystemExit(0)) as execute,
        pytest.raises(SystemExit) as exited,
    ):
        mod.execute(["mktemp", "-d", "/tmp/tailplan-upload.XXXXXXXX"])
    assert exited.value.code == 0
    execute.assert_called_once_with(
        "/usr/bin/mktemp",
        ["mktemp", "-d", "/tmp/tailplan-upload.XXXXXXXX"],
    )

    with pytest.raises(SystemExit) as raised:
        mod.execute(["mktemp", "-d"])
    assert raised.value.code == 126


def test_guard_allows_the_exact_legacy_scp_sink_command() -> None:
    directory = Path("/tmp/tailplan-upload.Ab12Cd34")
    directory.mkdir(mode=0o700, exist_ok=False)
    try:
        command = ["scp", "-t", f"{directory}/draft.md"]
        with (
            patch.object(mod.os, "execv", side_effect=SystemExit(0)) as execute,
            pytest.raises(SystemExit) as exited,
        ):
            mod.execute(command)
        assert exited.value.code == 0
        execute.assert_called_once_with("/usr/bin/scp", command)
    finally:
        directory.rmdir()


@pytest.mark.parametrize(
    "command",
    [
        ["scp", "-t", "/tmp/other/draft.md"],
        ["scp", "-t", "/tmp/tailplan-upload.Ab12Cd34/../draft.md"],
        ["scp", "-f", "/tmp/tailplan-upload.Ab12Cd34/draft.md"],
        ["bash", "-c", "id"],
    ],
)
def test_guard_rejects_other_transfer_commands(command: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        mod.execute(command)
    assert raised.value.code == 126


def test_guard_allows_publish_options_and_rejects_other_options(tmp_path: Path) -> None:
    directory = Path("/tmp/tailplan-upload.Ef56Gh78")
    directory.mkdir(mode=0o700, exist_ok=False)
    source = directory / "draft.md"
    source.write_text("# Draft\n")
    try:
        with (
            patch.object(mod, "configured_share_command", return_value="/publisher"),
            patch.object(mod.os, "execv", side_effect=SystemExit(0)) as execute,
            pytest.raises(SystemExit) as exited,
        ):
            mod.execute(["tailplan-share", str(source), "--draft", "draft123"])
        assert exited.value.code == 0
        execute.assert_called_once_with(
            "/publisher",
            ["/publisher", str(source), "--draft", "draft123"],
        )

        with pytest.raises(SystemExit) as raised:
            mod.execute(["tailplan-share", str(source), "--json"])
        assert raised.value.code == 126
    finally:
        source.unlink()
        directory.rmdir()


def test_guard_allows_only_scoped_owned_cleanup() -> None:
    directory = Path("/tmp/tailplan-upload.Ij90Kl12")
    directory.mkdir(mode=0o700, exist_ok=False)
    try:
        command = ["rm", "-rf", "--", str(directory)]
        with (
            patch.object(mod.os, "execv", side_effect=SystemExit(0)) as execute,
            pytest.raises(SystemExit) as exited,
        ):
            mod.execute(command)
        assert exited.value.code == 0
        execute.assert_called_once_with("/usr/bin/rm", command)
    finally:
        directory.rmdir()


def test_guard_parses_the_original_command_without_shell_evaluation() -> None:
    original = "tailplan-share /tmp/tailplan-upload.Mn34Op56/draft.md --new"
    with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": original}, clear=False):
        assert mod.parse_original_command() == [
            "tailplan-share",
            "/tmp/tailplan-upload.Mn34Op56/draft.md",
            "--new",
        ]


def test_system_publisher_installer_enables_key_auth_and_installs_a_private_copy(
    tmp_path: Path,
) -> None:
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    for name in ("getent", "id", "install", "passwd", "useradd", "usermod"):
        write_command_mock(mock_bin / name)
    log = tmp_path / "commands.log"
    config = tmp_path / "publisher.json"
    key = tmp_path / "publisher.pub"
    key.write_text("ssh-ed25519 QQ== test\n", encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("test-token\n", encoding="utf-8")
    token.chmod(0o600)
    env_file = tmp_path / "tailplan.env"
    env_file.write_text('TAILPLAN_BASE_URL="https://tailplan.test"\n', encoding="utf-8")
    private_home = tmp_path / "private-home"
    private_home.mkdir(mode=0o700)
    share_command = private_home / "tailplan-share"
    share_command.write_bytes((ROOT / "bin/tailplan-share").read_bytes())
    share_command.chmod(0o700)
    environment = os.environ.copy()
    environment.update(
        {
            "MOCK_LOG": str(log),
            "MOCK_CONFIG": str(config),
            "PATH": f"{mock_bin}:{environment['PATH']}",
        }
    )

    completed = subprocess.run(
        [
            *publisher_installer_prefix(),
            "bash",
            str(INSTALLER),
            "--public-key-file",
            str(key),
            "--token-file",
            str(token),
            "--env-file",
            str(env_file),
            "--share-command",
            str(share_command),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    commands = log.read_text(encoding="utf-8")
    assert "usermod <--password> <*> <tailplan-publisher>" in commands
    assert all(not line.startswith("passwd ") for line in commands.splitlines())
    assert (
        f"install <-o> <root> <-g> <root> <-m> <755> <{share_command}> "
        "</usr/local/libexec/tailplan-share>"
    ) in commands
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "share_command": "/usr/local/libexec/tailplan-share"
    }
    assert "Installed Tailplan SSH publisher account: tailplan-publisher" in completed.stdout
    assert "after each Tailplan upgrade" in completed.stdout


def test_publisher_installer_documents_the_stable_interface() -> None:
    completed = subprocess.run(
        [str(INSTALLER), "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--public-key-file PATH" in completed.stdout
    assert "tailplan-publisher" in completed.stdout
    assert "--token-file PATH" in completed.stdout
    assert "--share-command PATH" in completed.stdout


def test_documented_publisher_removal_removes_the_installed_copy() -> None:
    instructions = README.read_text(encoding="utf-8")

    assert "sudo rm -f /usr/local/libexec/tailplan-publish-guard" in instructions
    assert "sudo rm -f /usr/local/libexec/tailplan-share" in instructions
    assert "sudo rm -f /etc/tailplan-publisher.json" in instructions


def test_documented_upgrade_requires_an_isolated_publisher_refresh() -> None:
    instructions = README.read_text(encoding="utf-8")

    assert "Run the same SSH publisher installer command after every Tailplan upgrade." in instructions
    assert "The normal Tailplan installer does not update the isolated local publisher." in instructions
