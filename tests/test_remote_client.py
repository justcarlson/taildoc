from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REMOTE_CLIENT = ROOT / "bin" / "tailplan-share-remote"
CLIENT_INSTALLER = ROOT / "install-client.sh"
SKILL = ROOT / "skills" / "tailplan" / "SKILL.md"

loader = importlib.machinery.SourceFileLoader("tailplan_share_remote", str(REMOTE_CLIENT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_remote_share_copies_invokes_and_cleans_up(tmp_path: Path) -> None:
    source = tmp_path / "Session recap.md"
    source.write_text("# Session recap\n")
    calls: list[list[str]] = []
    remote_dir = "/tmp/tailplan-upload.Ab12Cd34"

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command == [
            "ssh",
            "tailplan-server",
            "mktemp",
            "-d",
            "/tmp/tailplan-upload.XXXXXXXX",
        ]:
            return subprocess.CompletedProcess(command, 0, f"{remote_dir}\n", "")
        if command[:3] == ["ssh", "tailplan-server", "tailplan-share"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Uploaded draft\nURL: https://tailplan.example/d/abc123\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    result = mod.share_file(
        source,
        target="tailplan-server",
        new=True,
        draft=None,
        run=run,
    )

    assert result.returncode == 0
    assert calls == [
        [
            "ssh",
            "tailplan-server",
            "mktemp",
            "-d",
            "/tmp/tailplan-upload.XXXXXXXX",
        ],
        [
            "scp",
            "-O",
            "--",
            str(source),
            f"tailplan-server:{remote_dir}/Session-recap.md",
        ],
        [
            "ssh",
            "tailplan-server",
            "tailplan-share",
            f"{remote_dir}/Session-recap.md",
            "--new",
        ],
        ["ssh", "tailplan-server", "rm", "-rf", "--", remote_dir],
    ]


def test_remote_share_cleans_up_after_upload_failure(tmp_path: Path) -> None:
    source = tmp_path / "recap.md"
    source.write_text("# Recap\n")
    calls: list[list[str]] = []
    remote_dir = "/tmp/tailplan-upload.Ef56Gh78"

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-2:] == ["-d", "/tmp/tailplan-upload.XXXXXXXX"]:
            return subprocess.CompletedProcess(command, 0, f"{remote_dir}\n", "")
        if command[0] == "scp":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(subprocess.CalledProcessError):
        mod.share_file(
            source,
            target="tailplan-server",
            new=False,
            draft=None,
            run=run,
        )

    assert calls[-1] == ["ssh", "tailplan-server", "rm", "-rf", "--", remote_dir]


@pytest.mark.parametrize("target", ["-bad", "host;touch-x", "host name", "user@host"])
def test_remote_share_rejects_unsafe_ssh_targets(tmp_path: Path, target: str) -> None:
    source = tmp_path / "recap.md"
    source.write_text("# Recap\n")

    with pytest.raises(ValueError, match="SSH target"):
        mod.share_file(source, target=target, new=True, draft=None)


@pytest.mark.parametrize(
    "remote_dir",
    [
        "/tmp/tmp.Ab12Cd34",
        "/tmp/tailplan-upload.short",
        "/tmp/tailplan-upload.Ab12Cd34/extra",
        "/tmp/tailplan-upload.Ab12Cd3!",
    ],
)
def test_remote_share_rejects_unscoped_remote_directories(
    tmp_path: Path,
    remote_dir: str,
) -> None:
    source = tmp_path / "recap.md"
    source.write_text("# Recap\n")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, f"{remote_dir}\n", "")

    with pytest.raises(RuntimeError, match="unsafe path"):
        mod.share_file(
            source,
            target="tailplan-server",
            new=True,
            draft=None,
            run=run,
        )


def test_remote_client_uses_a_generic_default_target() -> None:
    assert mod.DEFAULT_TARGET == "tailplan-server"


@pytest.mark.parametrize("argv", [
    ["--target", "publisher-host", "list", "--json"],
    ["list", "--json", "--target", "publisher-host"],
])
def test_remote_management_accepts_target_before_or_after_command(
    tmp_path: Path, argv: list[str],
) -> None:
    ssh = tmp_path / "ssh"
    ssh.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    ssh.chmod(0o755)
    completed = subprocess.run(
        [sys.executable, str(REMOTE_CLIENT), *argv],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True, text=True, check=True,
    )
    assert json.loads(completed.stdout) == ["publisher-host", "tailplan-share", "list", "--json"]


def test_remote_publish_transports_description_json_and_local_provenance(tmp_path: Path) -> None:
    source = tmp_path / "report.md"
    source.write_text("# Report\n")
    metadata = tmp_path / "provenance.json"
    metadata.write_text(json.dumps({"repoName": "source-repo", "gitBranch": "feature"}))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        output = "/tmp/tailplan-upload.Ab12Cd34\n" if "mktemp" in command else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    description = "Plan; $(this-is-text)"
    mod.share_file(source, target="tailplan-server", new=True, draft=None, run=run,
                   description=description, json_output=True, metadata_path=metadata)
    published = next(call for call in calls if call[:3] == ["ssh", "tailplan-server", "tailplan-share"])
    parsed = shlex.split(" ".join(published[2:]))
    assert parsed[parsed.index("--description") + 1] == description
    assert "--json" in parsed
    assert parsed[parsed.index("--metadata-file") + 1].endswith("/provenance.json")
    assert ["scp", "-O", "--", str(metadata), "tailplan-server:/tmp/tailplan-upload.Ab12Cd34/provenance.json"] in calls


def test_remote_main_reuses_source_mapping_across_transfers(tmp_path: Path) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    log = tmp_path / "calls.jsonl"
    script = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ["CLIENT_LOG"],"a") as output:
    output.write(json.dumps(args)+"\\n")
if "mktemp" in args:
    print("/tmp/tailplan-upload.Ab12Cd34")
elif "tailplan-share" in args:
    print(json.dumps({"ok":True,"draftId":"remote123","versionNumber":2 if "--draft" in args else 1,
        "publicUrl":"https://tailplan.example.test/d/remote123","rawUrl":"https://tailplan.example.test/d/remote123/raw"}))
'''
    for name in ("ssh", "scp"):
        path = mock_bin / name
        path.write_text(script)
        path.chmod(0o755)
    source = tmp_path / "report.md"
    source.write_text("# Remote report\n")
    env = {**os.environ, "HOME": str(tmp_path), "PATH": str(mock_bin) + os.pathsep + os.environ["PATH"],
           "CLIENT_LOG": str(log)}
    for version in (1, 2):
        completed = subprocess.run([sys.executable, str(REMOTE_CLIENT), str(source), "--json"],
                                   env=env, capture_output=True, text=True, check=True)
        assert json.loads(completed.stdout)["versionNumber"] == version
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    published = [call for call in calls if "tailplan-share" in call]
    assert "--draft" not in published[0]
    assert published[1][published[1].index("--draft") + 1] == "remote123"


def test_client_installer_installs_and_removes_only_client_files(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    skills_root = tmp_path / "skills"
    env = {
        **os.environ,
        "TAILPLAN_BIN_DIR": str(bin_dir),
        "TAILPLAN_SKILLS_ROOT": str(skills_root),
    }

    installed = subprocess.run(
        [str(CLIENT_INSTALLER)],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    client = bin_dir / "tailplan-share"
    skill = skills_root / "tailplan" / "SKILL.md"
    assert client.read_bytes() == REMOTE_CLIENT.read_bytes()
    assert skill.read_bytes() == SKILL.read_bytes()
    assert "Configure the tailplan-server SSH alias" in installed.stdout
    assert not (tmp_path / "token").exists()

    subprocess.run(
        [str(CLIENT_INSTALLER), "--uninstall"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not client.exists()
    assert not skill.exists()


def test_tailplan_skill_routes_plain_language_to_the_app() -> None:
    text = SKILL.read_text()

    assert "requests a Tailplan" in text
    assert "tailplan-share" in text
    assert "The upload token remains on the Tailplan server." in text



def test_tailplan_skill_preserves_artifact_safety_and_placement_rules() -> None:
    rules = [" ".join(line.casefold().split()) for line in SKILL.read_text().splitlines()]

    assert any(
        all(term in line for term in ("credentials", "secret values", "personal data"))
        for line in rules
    )
    assert any("temporary" in line and "durable repository document" in line for line in rules)
    assert any("durable repository document" in line and "repository path" in line for line in rules)
    assert any("agent-browser" in line and "uat" in line for line in rules)


def test_tailplan_skill_prohibits_an_ordinary_markdown_fallback() -> None:
    rules = [" ".join(line.casefold().split()) for line in SKILL.read_text().splitlines()]

    assert any("tailplan url" in line and ("always" in line or "must" in line) for line in rules)
    assert any(
        "markdown" in line and "fallback" in line and ("do not" in line or "never" in line)
        for line in rules
    )
