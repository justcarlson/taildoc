from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "bin" / "tailplan-publish-guard"
INSTALLER = ROOT / "install-ssh-publisher.sh"
loader = importlib.machinery.SourceFileLoader("tailplan_publish_guard", str(GUARD))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


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
