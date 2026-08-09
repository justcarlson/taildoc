from __future__ import annotations

import functools
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "install.sh"

SKILL = REPO / "skills" / "tailplan" / "SKILL.md"
USER_UNIT_BASELINE = """\
[Unit]
Description=Tailplan tailnet-only static HTML draft publisher
After=default.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=__TAILPLAN_ENV_FILE__
ExecStart=/usr/bin/env bash -c 'exec "$${TAILPLAN_APP_DIR}/bin/run-tailplan"'
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=__TAILPLAN_DATA_DIR__
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectKernelLogs=true
LockPersonality=true
RestrictSUIDSGID=true
RestrictRealtime=true
RestrictNamespaces=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
UMask=0077

[Install]
WantedBy=default.target
"""


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def parse_env(path: Path) -> dict[str, str]:
    script = 'set -a; source "$1"; env -0'
    completed = subprocess.run(
        ["bash", "-c", script, "bash", str(path)],
        check=True,
        stdout=subprocess.PIPE,
    )
    values: dict[str, str] = {}
    for item in completed.stdout.split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            if key.startswith(b"TAILPLAN_"):
                values[key.decode()] = value.decode()
    return values


def unit_escape(value: str) -> str:
    safe = b"/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-:"
    return "".join(chr(byte) if byte in safe else f"\\x{byte:02x}" for byte in value.encode())


@functools.cache
def system_installer_command_prefix() -> tuple[str, ...] | None:
    if os.geteuid() == 0:
        return ("bash",)
    try:
        probe = subprocess.run(
            ["unshare", "-Ur", "true"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0:
        return None
    return ("unshare", "-Ur", "bash")


class InstallerHarness:
    def __init__(self, root: Path, *, spaces: bool = False) -> None:
        self.root = root
        self.home = root / ("home with spaces" if spaces else "home")
        self.mock_bin = root / "mock-bin"
        self.state = root / "state"
        self.home.mkdir(parents=True)
        self.mock_bin.mkdir()
        self.state.mkdir()
        self.log = self.state / "commands.log"
        self.owner_db = self.state / "owners.tsv"
        self.owner_db.touch()
        self.identity_dir = self.state / "identity"
        self.identity_dir.mkdir()
        (self.identity_dir / "group").touch()
        (self.identity_dir / "passwd").touch()
        self.serve_config = self.state / "serve.json"
        self.serve_config.write_text(
            json.dumps(
                {
                    "TCP": {"443": {"HTTPS": True}},
                    "Web": {
                        "tailnode.example.ts.net:443": {
                            "Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}
                        },
                        "unrelated.example.ts.net:443": {
                            "Handlers": {"/": {"Proxy": "http://127.0.0.1:6060"}}
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        self.app_dir = self.home / ("apps with spaces/tailplan app" if spaces else "apps/tailplan")
        self.bin_dir = self.home / ("local bin" if spaces else ".local/bin")
        self.data_dir = self.home / ("tailplan data" if spaces else ".tailplan")
        self.unit = self.home / ".config/systemd/user/tailplan.service"
        self._write_mocks()
        self.skills_root = self.home / ".agents/skills"

    def _write_mocks(self) -> None:
        write_executable(
            self.mock_bin / "systemctl",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'systemctl' >> "$MOCK_LOG"
            printf ' <%s>' "$@" >> "$MOCK_LOG"
            printf '\n' >> "$MOCK_LOG"
            case " $* " in
              *" is-enabled "*)
                [[ "${MOCK_PRIOR_ENABLED:-1}" == 1 ]]
                ;;
              *" is-active "*)
                [[ "${MOCK_ACTIVE:-1}" == 1 ]]
                ;;
            esac
            """,
        )
        write_executable(
            self.mock_bin / "tailscale",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'tailscale' >> "$MOCK_LOG"
            printf ' <%s>' "$@" >> "$MOCK_LOG"
            printf '\n' >> "$MOCK_LOG"
            if [[ "${MOCK_TAILSCALE_FORBIDDEN:-0}" == 1 ]]; then
              echo 'tailscale must not be called' >&2
              exit 91
            fi
            if [[ "${1:-}" == ip && "${2:-}" == -4 ]]; then
              printf '%s\n' '100.100.100.100'
              exit 0
            fi
            if [[ "${1:-}" == status && "${2:-}" == --json ]]; then
              printf '%s\n' '{"Self":{"DNSName":"tailnode.example.ts.net."}}'
              exit 0
            fi
            if [[ "${1:-}" == serve && "${2:-}" == status && "${3:-}" == --json ]]; then
              cp "$MOCK_SERVE_CONFIG" /dev/stdout
              exit 0
            fi
            if [[ "${1:-}" == serve && "${2:-}" == --bg && "${3:-}" == --https=443 && "${4:-}" == --set-path=/tailplan && "${5:-}" == --yes && "$#" == 6 ]]; then
              target_host='tailnode.example.ts.net:443'
              if [[ "${MOCK_SERVE_WRONG_HOST:-0}" == 1 ]]; then
                target_host='unrelated.example.ts.net:443'
              fi
              python3 - "$MOCK_SERVE_CONFIG" "$6" "$target_host" <<'PY'
            import json, sys
            path, proxy, target_host = sys.argv[1:]
            with open(path, encoding="utf-8") as stream:
                config = json.load(stream)
            host = config.setdefault("Web", {}).setdefault(target_host, {"Handlers": {}})
            host.setdefault("Handlers", {})["/tailplan"] = {"Proxy": proxy}
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(config, stream)
            PY
              exit 0
            fi
            if [[ "${1:-}" == serve && "${2:-}" == --https=443 && "${3:-}" == --set-path=/tailplan && "${4:-}" == off && "$#" == 4 ]]; then
              python3 - "$MOCK_SERVE_CONFIG" <<'PY'
            import json, sys
            path = sys.argv[1]
            with open(path, encoding="utf-8") as stream:
                config = json.load(stream)
            handlers = config.get("Web", {}).get(
                "tailnode.example.ts.net:443", {}
            ).get("Handlers", {})
            handlers.pop("/tailplan", None)
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(config, stream)
            PY
              exit 0
            fi
            echo "unexpected tailscale invocation: $*" >&2
            exit 92
            """,
        )
        write_executable(
            self.mock_bin / "curl",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'curl' >> "$MOCK_LOG"
            printf ' <%s>' "$@" >> "$MOCK_LOG"
            printf '\n' >> "$MOCK_LOG"
            url="${!#}"
            if [[ "$url" == https://* && "${MOCK_HTTPS_FAIL:-0}" == 1 ]]; then
              exit 22
            fi
            build="$(sha256sum "$MOCK_APP_DIR/tailplan_server.py" | cut -c1-12)"
            printf '{"ok":true,"service":"tailplan","build":"%s"}\n' "$build"
            """,
        )
        write_executable(
            self.mock_bin / "getent",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'getent' >> "$MOCK_LOG"
            printf ' <%s>' "$@" >> "$MOCK_LOG"
            printf '\n' >> "$MOCK_LOG"
            file="$MOCK_IDENTITY_DIR/$1"
            [[ -f "$file" ]] || exit 2
            entry="$(grep -E "^${2}:" "$file" || true)"
            [[ -n "$entry" ]] || exit 2
            printf '%s\n' "$entry"
            """,
        )
        write_executable(
            self.mock_bin / "groupadd",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'groupadd' >> "$MOCK_LOG"
            printf ' <%s>' "$@" >> "$MOCK_LOG"
            printf '\n' >> "$MOCK_LOG"
            group="${!#}"
            printf '%s:x:450:\n' "$group" >> "$MOCK_IDENTITY_DIR/group"
            """,
        )
        write_executable(
            self.mock_bin / "useradd",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'useradd' >> "$MOCK_LOG"
            printf ' <%s>' "$@" >> "$MOCK_LOG"
            printf '\n' >> "$MOCK_LOG"
            group='' home='' shell=''
            args=("$@")
            for ((i = 0; i < ${#args[@]}; i++)); do
              case "${args[i]}" in
                --gid) group="${args[i+1]}" ;;
                --home-dir) home="${args[i+1]}" ;;
                --shell) shell="${args[i+1]}" ;;
              esac
            done
            user="${!#}"
            gid="$(grep -E "^${group}:" "$MOCK_IDENTITY_DIR/group" | cut -d: -f3)"
            printf '%s:x:449:%s::%s:%s\n' "$user" "$gid" "$home" "$shell" >> "$MOCK_IDENTITY_DIR/passwd"
            """,
        )
        for command in ("userdel", "groupdel"):
            write_executable(
                self.mock_bin / command,
                f"""
                #!/usr/bin/env bash
                set -euo pipefail
                printf '{command}' >> "$MOCK_LOG"
                printf ' <%s>' "$@" >> "$MOCK_LOG"
                printf '\\n' >> "$MOCK_LOG"
                name="${{!#}}"
                file="$MOCK_IDENTITY_DIR/{"passwd" if command == "userdel" else "group"}"
                if [[ -f "$file" ]]; then
                  grep -Ev "^${{name}}:" "$file" > "$file.tmp" || true
                  mv "$file.tmp" "$file"
                fi
                """,
            )
        write_executable(
            self.mock_bin / "chown",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'chown' >> "$MOCK_LOG"
            printf ' <%s>' "$@" >> "$MOCK_LOG"
            printf '\n' >> "$MOCK_LOG"
            recursive=0
            args=("$@")
            index=0
            while ((index < ${#args[@]})); do
              case "${args[index]}" in
                -R) recursive=1; ((index += 1)) ;;
                -*) ((index += 1)) ;;
                *) break ;;
              esac
            done
            owner="${args[index]}"
            ((index += 1))
            for ((; index < ${#args[@]}; index++)); do
              printf '%s\t%s\t%s\n' "$recursive" "$owner" "${args[index]}" >> "$MOCK_OWNER_DB"
            done
            """,
        )
        write_executable(
            self.mock_bin / "stat",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ -n "${MOCK_STAT_FAIL_PATH:-}" && "${3:-}" == "$MOCK_STAT_FAIL_PATH" ]]; then
              exit 96
            fi
            if [[ "$#" == 3 && "$1" == -c && "$2" == '%u:%g' ]]; then
              owner="$(python3 - "$MOCK_OWNER_DB" "$3" <<'PY'
            import os
            import sys

            database, target = sys.argv[1:]
            owner = ""
            with open(database, encoding="utf-8") as stream:
                for line in stream:
                    recursive, candidate_owner, candidate = line.rstrip("\n").split("\t", 2)
                    if candidate == target or (
                        recursive == "1"
                        and os.path.commonpath((candidate, target)) == candidate
                    ):
                        owner = candidate_owner
            identities = {"root:root": "0:0", "tailplan:tailplan": "449:450"}
            print(identities.get(owner, owner), end="")
            PY
            )"
              if [[ -n "$owner" ]]; then
                printf '%s\n' "$owner"
                exit 0
              fi
            fi
            exec /usr/bin/stat "$@"
            """,
        )
        write_executable(
            self.mock_bin / "python3",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            boundary="${MOCK_FAIL_BOUNDARY:-}"
            fail=0
            case "$boundary" in
              token-write) [[ "$#" == 1 && "$1" == - ]] && fail=1 ;;
              env-write) [[ "${2:-}" == */.tailplan.env.*.tmp ]] && fail=1 ;;
              unit-write) [[ "${3:-}" == */.tailplan.service.*.tmp ]] && fail=1 ;;
            esac
            /usr/bin/python3 "$@"
            if [[ "$fail" == 1 ]]; then
              exit 97
            fi
            """,
        )
        write_executable(
            self.mock_bin / "mv",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            boundary="${MOCK_FAIL_BOUNDARY:-}"
            source="${@: -2:1}"
            case "$boundary:$source" in
              token-move:*/.token.*.tmp|env-move:*/.tailplan.env.*.tmp|unit-move:*/.tailplan.service.*.tmp)
                exit 98
                ;;
            esac
            exec /usr/bin/mv "$@"
            """,
        )

    def env(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.mock_bin}:{env['PATH']}",
                "TAILPLAN_APP_DIR": str(self.app_dir),
                "TAILPLAN_BIN_DIR": str(self.bin_dir),
                "TAILPLAN_DATA_DIR": str(self.data_dir),
                "MOCK_APP_DIR": str(self.app_dir),
                "MOCK_IDENTITY_DIR": str(self.identity_dir),
                "MOCK_LOG": str(self.log),
                "MOCK_OWNER_DB": str(self.owner_db),
                "MOCK_SERVE_CONFIG": str(self.serve_config),
                "TAILPLAN_VERIFY_ATTEMPTS": "2",
                "TAILPLAN_VERIFY_INTERVAL": "0",
            }
        )
        env.update(overrides)
        return env

    def run(
        self, *arguments: str, check: bool = True, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(INSTALLER), *arguments],
            cwd=REPO,
            env=self.env(**overrides),
            text=True,
            capture_output=True,
            check=check,
            timeout=30,
        )

    def commands(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def seed_owner(self, path: Path, owner: str, *, recursive: bool = False) -> None:
        with self.owner_db.open("a", encoding="utf-8") as stream:
            stream.write(f"{int(recursive)}\t{owner}\t{path}\n")

    def mocked_owner(self, path: Path) -> str:
        owner = ""
        for line in self.owner_db.read_text(encoding="utf-8").splitlines():
            recursive, candidate_owner, candidate = line.split("\t", 2)
            if candidate == str(path) or (
                recursive == "1" and Path(candidate) in (path, *path.parents)
            ):
                owner = candidate_owner
        return {"root:root": "0:0", "tailplan:tailplan": "449:450"}.get(owner, owner)


class SystemInstallerHarness(InstallerHarness):
    def __init__(self, root: Path) -> None:
        command_prefix = system_installer_command_prefix()
        if command_prefix is None:
            raise unittest.SkipTest(
                "System installer tests require root or support for `unshare -Ur`."
            )
        self.command_prefix = command_prefix
        super().__init__(root)
        prefix = root / "system-root"
        self.app_dir = prefix / "opt/tailplan"
        self.bin_dir = prefix / "usr/local/bin"
        self.data_dir = prefix / "var/lib/tailplan"
        self.env_file = prefix / "etc/tailplan.env"
        self.unit = prefix / "etc/systemd/system/tailplan.service"
        self.backup_dir = prefix / "var/backups/tailplan"

    def seed_identity(
        self,
        *,
        user: str = "tailplan",
        group: str = "tailplan",
        home: Path | None = None,
        shell: str = "/usr/sbin/nologin",
        primary_gid: int = 450,
    ) -> None:
        service_home = self.data_dir if home is None else home
        (self.identity_dir / "group").write_text(f"{group}:x:450:\n", encoding="utf-8")
        (self.identity_dir / "passwd").write_text(
            f"{user}:x:449:{primary_gid}::{service_home}:{shell}\n",
            encoding="utf-8",
        )

    def seed_operator(
        self,
        *,
        name: str = "operator",
        uid: int = 1000,
        gid: int = 1000,
    ) -> None:
        self.home.chmod(0o700)
        with (self.identity_dir / "passwd").open("a", encoding="utf-8") as stream:
            stream.write(f"{name}:x:{uid}:{gid}::{self.home}:/bin/bash\n")
        self.seed_owner(self.home, f"{uid}:{gid}")

    def env(self, **overrides: str) -> dict[str, str]:
        system_overrides = {
            "TAILPLAN_APP_DIR": str(self.app_dir),
            "TAILPLAN_BIN_DIR": str(self.bin_dir),
            "TAILPLAN_DATA_DIR": str(self.data_dir),
            "TAILPLAN_ENV_FILE": str(self.env_file),
            "TAILPLAN_UNIT_FILE": str(self.unit),
            "TAILPLAN_BACKUP_DIR": str(self.backup_dir),
        }
        system_overrides.update(overrides)
        environment = super().env(**system_overrides)
        for name in ("SUDO_USER", "SUDO_UID"):
            if name not in overrides:
                environment.pop(name, None)
        return environment

    def run(
        self, *arguments: str, check: bool = True, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self.command_prefix, str(INSTALLER), "--system", *arguments],
            cwd=REPO,
            env=self.env(**overrides),
            text=True,
            capture_output=True,
            check=check,
            timeout=30,
        )


class InstallTests(unittest.TestCase):
    def test_system_install_uses_system_paths_identity_unit_ownership_and_systemctl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))

            completed = harness.run(TAILPLAN_HOST="127.0.0.1")

            self.assertEqual(0, completed.returncode)
            self.assertTrue((harness.app_dir / "tailplan_server.py").is_file())
            self.assertTrue((harness.bin_dir / "tailplan-share").is_file())
            self.assertTrue(harness.env_file.is_file())
            self.assertFalse((harness.data_dir / "env").exists())
            self.assertTrue(harness.unit.is_file())
            self.assertEqual(0o600, stat.S_IMODE(harness.env_file.stat().st_mode))
            self.assertEqual(0o644, stat.S_IMODE(harness.unit.stat().st_mode))
            self.assertEqual(0o755, stat.S_IMODE(harness.app_dir.stat().st_mode))
            self.assertEqual(0o755, stat.S_IMODE((harness.app_dir / "bin").stat().st_mode))
            self.assertEqual("127.0.0.1", parse_env(harness.env_file)["TAILPLAN_HOST"])
            expected_unit = textwrap.dedent(
                f"""\
                [Unit]
                Description=Tailplan tailnet-only static HTML draft publisher
                After=network-online.target tailscaled.service
                Wants=network-online.target tailscaled.service

                [Service]
                User=tailplan
                Group=tailplan
                WorkingDirectory={harness.app_dir}
                Type=simple
                EnvironmentFile={harness.env_file}
                ExecStart=/usr/bin/env bash -c 'exec "$${{TAILPLAN_APP_DIR}}/bin/run-tailplan"'
                Restart=on-failure
                RestartSec=3
                NoNewPrivileges=true
                PrivateTmp=true
                PrivateDevices=true
                ProtectSystem=strict
                ProtectHome=read-only
                ReadWritePaths={harness.data_dir}
                ProtectControlGroups=true
                ProtectKernelModules=true
                ProtectKernelTunables=true
                ProtectKernelLogs=true
                LockPersonality=true
                RestrictSUIDSGID=true
                RestrictRealtime=true
                RestrictNamespaces=true
                RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
                UMask=0077

                [Install]
                WantedBy=multi-user.target
                """
            )
            self.assertEqual(expected_unit, harness.unit.read_text(encoding="utf-8"))

            commands = harness.commands()
            self.assertNotIn("<--user>", commands)
            self.assertIn("groupadd <--system> <tailplan>", commands)
            self.assertIn(
                f"useradd <--system> <--gid> <tailplan> <--home-dir> <{harness.data_dir}> "
                "<--shell> </usr/sbin/nologin> <--no-create-home> <tailplan>",
                commands,
            )
            self.assertIn("systemctl <daemon-reload>", commands)
            self.assertIn("systemctl <enable> <tailplan.service>", commands)
            self.assertIn("systemctl <restart> <tailplan.service>", commands)
            self.assertIn("chown <-R> <-P> <--no-dereference> <tailplan:tailplan>", commands)
            for root_owned in (
                harness.app_dir,
                harness.app_dir / "bin",
                harness.app_dir / "tailplan_server.py",
                harness.app_dir / "bin/run-tailplan",
                harness.bin_dir / "tailplan-share",
                harness.bin_dir / "tailplan-share-public",
                harness.env_file,
                harness.unit,
            ):
                with self.subTest(root_owned=root_owned):
                    self.assertEqual("0:0", harness.mocked_owner(root_owned))
            for service_owned in (
                harness.data_dir,
                harness.data_dir / "token",
                harness.data_dir / "drafts",
                harness.data_dir / "generated",
            ):
                with self.subTest(service_owned=service_owned):
                    self.assertEqual("449:450", harness.mocked_owner(service_owned))

            config = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            handlers = config["Web"]["tailnode.example.ts.net:443"]["Handlers"]
            self.assertEqual({"Proxy": "http://127.0.0.1:8080"}, handlers["/"])
            self.assertEqual({"Proxy": "http://127.0.0.1:9128"}, handlers["/tailplan"])

    def test_system_operator_provisioning_and_rotation_are_private_and_usable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.seed_operator()

            completed = harness.run("--operator", "operator")

            operator_dir = harness.home / ".tailplan"
            operator_token = operator_dir / "token"
            operator_env = operator_dir / "env"
            operator_skill = harness.home / ".agents/skills/tailplan/SKILL.md"
            server_token = harness.data_dir / "token"
            initial_token = server_token.read_bytes()
            self.assertEqual(initial_token, operator_token.read_bytes())
            self.assertEqual(
                'TAILPLAN_BASE_URL="https://tailnode.example.ts.net/tailplan"\n',
                operator_env.read_text(encoding="utf-8"),
            )
            self.assertEqual(0o700, stat.S_IMODE(operator_dir.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(operator_token.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(operator_env.stat().st_mode))
            self.assertEqual("1000:1000", harness.mocked_owner(operator_dir))
            self.assertEqual("1000:1000", harness.mocked_owner(operator_token))
            self.assertEqual("1000:1000", harness.mocked_owner(operator_env))
            self.assertEqual("1000:1000", harness.mocked_owner(operator_skill))
            self.assertIn(f"Operator configuration: {operator_dir}", completed.stdout)

            harness.run("--operator", "operator", "--rotate-token")

            self.assertNotEqual(initial_token, server_token.read_bytes())
            self.assertEqual(server_token.read_bytes(), operator_token.read_bytes())

    def test_system_install_uses_a_matching_safe_sudo_user_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.seed_operator()

            harness.run(SUDO_USER="operator", SUDO_UID="1000")

            self.assertEqual(
                (harness.data_dir / "token").read_bytes(),
                (harness.home / ".tailplan/token").read_bytes(),
            )

    def test_system_install_accepts_an_explicit_operator_environment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.seed_operator()

            harness.run(TAILPLAN_OPERATOR="operator")

            self.assertEqual(
                (harness.data_dir / "token").read_bytes(),
                (harness.home / ".tailplan/token").read_bytes(),
            )

    def test_system_install_rejects_a_mismatched_sudo_identity_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.seed_operator()

            completed = harness.run(
                check=False,
                SUDO_USER="operator",
                SUDO_UID="2000",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("SUDO_UID does not match SUDO_USER", completed.stderr)
            self.assertFalse(harness.backup_dir.exists())
            self.assertFalse(harness.app_dir.exists())

    def test_failed_rotation_restores_both_private_token_copies(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.seed_operator()
            harness.run("--operator", "operator")
            server_token = harness.data_dir / "token"
            operator_token = harness.home / ".tailplan/token"
            original_token = server_token.read_bytes()

            completed = harness.run(
                "--operator",
                "operator",
                "--rotate-token",
                check=False,
                MOCK_HTTPS_FAIL="1",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual(original_token, server_token.read_bytes())
            self.assertEqual(original_token, operator_token.read_bytes())
            self.assertIn("Rollback complete.", completed.stderr)

    def test_system_reinstall_preserves_valid_identity_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.seed_identity()

            harness.run()
            token = (harness.data_dir / "token").read_bytes()
            harness.run()

            self.assertEqual(token, (harness.data_dir / "token").read_bytes())
            commands = harness.commands()
            for identity_command in ("groupadd", "useradd", "groupdel", "userdel"):
                self.assertNotIn(identity_command, commands)
            self.assertEqual(2, commands.count("systemctl <restart> <tailplan.service>"))
            self.assertEqual(
                f"tailplan:x:449:450::{harness.data_dir}:/usr/sbin/nologin\n",
                (harness.identity_dir / "passwd").read_text(encoding="utf-8"),
            )

    def test_system_preflight_rejects_invalid_existing_identity_before_mutation(self) -> None:
        for invalid_field in ("group", "home", "shell"):
            with self.subTest(invalid_field=invalid_field), tempfile.TemporaryDirectory() as td:
                harness = SystemInstallerHarness(Path(td))
                if invalid_field == "group":
                    harness.seed_identity(primary_gid=451)
                elif invalid_field == "home":
                    harness.seed_identity(home=Path("/wrong/home"))
                else:
                    harness.seed_identity(shell="/bin/bash")

                completed = harness.run(check=False)

                self.assertNotEqual(0, completed.returncode)
                self.assertFalse(harness.backup_dir.exists())
                self.assertFalse(harness.app_dir.exists())
                commands = harness.commands()
                self.assertNotIn("groupadd", commands)
                self.assertNotIn("useradd", commands)
                self.assertNotIn("groupdel", commands)
                self.assertNotIn("userdel", commands)
                self.assertNotIn("systemctl", commands)

    def test_system_preflight_rejects_nested_migrated_data_symlink_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            external = harness.root / "external"
            external.mkdir()
            marker = external / "keep"
            marker.write_text("untouched\n", encoding="utf-8")
            nested = harness.data_dir / "migrated/nested"
            nested.mkdir(parents=True)
            (nested / "link").symlink_to(external, target_is_directory=True)

            completed = harness.run(check=False)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("must not contain symlinks", completed.stderr)
            self.assertEqual("untouched\n", marker.read_text(encoding="utf-8"))
            self.assertFalse(harness.backup_dir.exists())
            self.assertEqual("", harness.commands())

    def test_system_preflight_rejects_noncanonical_unit_filename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            invalid_unit = harness.unit.with_name("custom.service")

            completed = harness.run(
                check=False,
                TAILPLAN_UNIT_FILE=str(invalid_unit),
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("tailplan.service", completed.stderr)
            self.assertFalse(harness.backup_dir.exists())
            self.assertEqual("", harness.commands())

    def test_system_no_serve_never_calls_tailscale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))

            harness.run(
                "--no-serve",
                TAILPLAN_HOST="127.0.0.1",
                TAILPLAN_BASE_URL="https://portable.example/tailplan",
                MOCK_TAILSCALE_FORBIDDEN="1",
            )

            self.assertNotIn("tailscale", harness.commands())
            env = parse_env(harness.env_file)
            self.assertEqual("https://portable.example/tailplan", env["TAILPLAN_BASE_URL"])

    def test_system_rollback_restores_every_preexisting_data_tree_owner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.seed_identity()
            app_bin = harness.app_dir / "bin"
            app_bin.mkdir(parents=True)
            drafts = harness.data_dir / "drafts"
            generated = harness.data_dir / "generated"
            drafts.mkdir(parents=True)
            generated.mkdir()
            nested = drafts / "migrated nested"
            nested.mkdir()
            token = harness.data_dir / "token"
            token.write_text("preserved-secret\n", encoding="utf-8")
            metadata = harness.data_dir / "metadata.json"
            metadata.write_text('{"draft":"safe"}\n', encoding="utf-8")
            draft = nested / "draft with spaces.html"
            draft.write_text("<title>Preserved</title>\n", encoding="utf-8")
            generated_file = generated / "rendered.html"
            generated_file.write_text("<title>Generated</title>\n", encoding="utf-8")
            prior = {
                harness.app_dir: (0o751, "120:453"),
                app_bin: (0o752, "121:454"),
                harness.data_dir: (0o750, "123:456"),
                drafts: (0o751, "124:457"),
                generated: (0o752, "125:458"),
                token: (0o640, "126:459"),
                metadata: (0o644, "127:460"),
                nested: (0o750, "128:461"),
                draft: (0o640, "129:462"),
                generated_file: (0o644, "130:463"),
            }
            for path, (mode, owner) in prior.items():
                path.chmod(mode)
                harness.seed_owner(path, owner)

            completed = harness.run(check=False, MOCK_HTTPS_FAIL="1")

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("Rollback complete", completed.stderr)
            for path, (mode, owner) in prior.items():
                with self.subTest(path=path):
                    self.assertEqual(mode, stat.S_IMODE(path.stat().st_mode))
                    self.assertEqual(owner, harness.mocked_owner(path))
            commands = harness.commands()
            for identity_command in ("groupadd", "useradd", "groupdel", "userdel"):
                self.assertNotIn(identity_command, commands)

    def test_system_ownership_snapshot_failure_is_fail_closed_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.seed_identity()
            nested = harness.data_dir / "drafts/nested.html"
            nested.parent.mkdir(parents=True)
            nested.write_text("preserved\n", encoding="utf-8")

            completed = harness.run(
                check=False,
                MOCK_STAT_FAIL_PATH=str(nested),
                TAILPLAN_HOST="127.0.0.1",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual("preserved\n", nested.read_text(encoding="utf-8"))
            self.assertFalse(harness.backup_dir.exists())
            commands = harness.commands()
            self.assertNotIn("chown", commands)
            self.assertNotIn("systemctl <daemon-reload>", commands)
            self.assertNotIn("systemctl <enable>", commands)
            self.assertNotIn("systemctl <restart>", commands)

    def test_system_preflight_rejects_control_characters_in_service_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))

            completed = harness.run(
                check=False,
                TAILPLAN_USER="tail\x01plan",
                TAILPLAN_HOST="127.0.0.1",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("control", completed.stderr.lower())
            self.assertFalse(harness.backup_dir.exists())
            self.assertEqual("", harness.commands())

    def test_system_preflight_rejects_special_migrated_data_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.data_dir.mkdir(parents=True)
            fifo = harness.data_dir / "metadata.pipe"
            os.mkfifo(fifo)

            completed = harness.run(check=False, TAILPLAN_HOST="127.0.0.1")

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("regular files and directories", completed.stderr)
            self.assertTrue(fifo.exists())
            self.assertFalse(harness.backup_dir.exists())
            self.assertEqual("", harness.commands())

    def test_system_failure_removes_only_created_identity_and_preserves_inactive_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            harness.unit.parent.mkdir(parents=True)
            harness.unit.write_text("old system unit\n", encoding="utf-8")

            completed = harness.run(
                check=False,
                MOCK_ACTIVE="0",
                MOCK_HTTPS_FAIL="1",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("Rollback complete", completed.stderr)
            commands = harness.commands()
            self.assertIn("userdel <tailplan>", commands)
            self.assertIn("groupdel <tailplan>", commands)
            self.assertIn("systemctl <stop> <tailplan.service>", commands)
            self.assertEqual(1, commands.count("systemctl <restart> <tailplan.service>"))
            self.assertNotIn("<--user>", commands)
            self.assertEqual("", (harness.identity_dir / "passwd").read_text())
            self.assertEqual("", (harness.identity_dir / "group").read_text())
            self.assertEqual("old system unit\n", harness.unit.read_text())

    def test_system_failure_removes_created_user_but_preserves_existing_group(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = SystemInstallerHarness(Path(td))
            (harness.identity_dir / "group").write_text("tailplan:x:450:\n", encoding="utf-8")

            completed = harness.run(check=False, MOCK_HTTPS_FAIL="1")

            self.assertNotEqual(0, completed.returncode)
            commands = harness.commands()
            self.assertIn("useradd <--system>", commands)
            self.assertIn("userdel <tailplan>", commands)
            self.assertNotIn("groupadd", commands)
            self.assertNotIn("groupdel", commands)
            self.assertEqual(
                "tailplan:x:450:\n",
                (harness.identity_dir / "group").read_text(encoding="utf-8"),
            )
            self.assertEqual("", (harness.identity_dir / "passwd").read_text(encoding="utf-8"))

    def test_system_install_requires_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))

            if os.geteuid() == 0:
                completed = subprocess.run(
                    ["runuser", "-u", "nobody", "--", "bash", str(INSTALLER), "--system"],
                    cwd=REPO,
                    env=harness.env(),
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            else:
                completed = harness.run("--system", check=False)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("root", completed.stderr.lower())
            self.assertFalse(harness.app_dir.exists())

    def test_user_unit_rendering_remains_byte_for_byte_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))

            harness.run()

            expected = USER_UNIT_BASELINE.replace(
                "__TAILPLAN_ENV_FILE__", unit_escape(str(harness.data_dir / "env"))
            ).replace("__TAILPLAN_DATA_DIR__", unit_escape(str(harness.data_dir)))
            self.assertEqual(expected, harness.unit.read_text(encoding="utf-8"))

    def test_default_install_preserves_root_handler_and_explicitly_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            harness.unit.parent.mkdir(parents=True)
            harness.unit.write_text("old unit\n", encoding="utf-8")

            completed = harness.run()

            self.assertIn("Backup retained:", completed.stdout)
            self.assertNotIn("token", completed.stdout.lower())
            backup_line = next(
                line
                for line in completed.stdout.splitlines()
                if line.startswith("Backup retained: ")
            )
            backup = Path(backup_line.removeprefix("Backup retained: "))
            self.assertEqual(0o700, stat.S_IMODE(backup.stat().st_mode))
            self.assertFalse(any(path.name == "token" for path in backup.rglob("*")))
            commands = harness.commands()
            self.assertIn("systemctl <--user> <restart> <tailplan.service>", commands)
            self.assertIn(
                "tailscale <serve> <--bg> <--https=443> <--set-path=/tailplan> <--yes> <http://127.0.0.1:9128>",
                commands,
            )
            config = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            handlers = config["Web"]["tailnode.example.ts.net:443"]["Handlers"]
            self.assertEqual({"Proxy": "http://127.0.0.1:8080"}, handlers["/"])
            self.assertEqual({"Proxy": "http://127.0.0.1:9128"}, handlers["/tailplan"])
            self.assertNotIn(
                "/tailplan",
                config["Web"]["unrelated.example.ts.net:443"]["Handlers"],
            )

    def test_install_backs_up_verifies_and_reports_the_configured_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            skills_root = harness.home / "custom skills"
            installed_skill = skills_root / "tailplan/SKILL.md"
            installed_skill.parent.mkdir(parents=True)
            installed_skill.write_text("prior skill\n", encoding="utf-8")

            completed = harness.run(TAILPLAN_SKILLS_ROOT=str(skills_root))

            self.assertEqual(SKILL.read_bytes(), installed_skill.read_bytes())
            self.assertEqual(0o644, stat.S_IMODE(installed_skill.stat().st_mode))
            self.assertIn(f"Agent skill: {installed_skill}", completed.stdout)
            backup_line = next(
                line
                for line in completed.stdout.splitlines()
                if line.startswith("Backup retained: ")
            )
            backup = Path(backup_line.removeprefix("Backup retained: "))
            self.assertEqual(
                "prior skill\n",
                (backup / "files/agent-skill").read_text(encoding="utf-8"),
            )

    def test_failed_install_restores_the_configured_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            skills_root = harness.home / "custom skills"
            installed_skill = skills_root / "tailplan/SKILL.md"
            installed_skill.parent.mkdir(parents=True)
            installed_skill.write_text("prior skill\n", encoding="utf-8")

            completed = harness.run(
                check=False,
                TAILPLAN_SKILLS_ROOT=str(skills_root),
                MOCK_HTTPS_FAIL="1",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual("prior skill\n", installed_skill.read_text(encoding="utf-8"))
            self.assertIn("Rollback complete.", completed.stderr)

    def test_deferred_https_verification_rejects_no_serve_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))

            completed = harness.run(
                "--no-serve",
                "--defer-https-verify",
                check=False,
                TAILPLAN_HOST="127.0.0.1",
                TAILPLAN_BASE_URL="https://portable.example/tailplan",
                MOCK_TAILSCALE_FORBIDDEN="1",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("requires Tailscale Serve", completed.stderr)
            self.assertFalse((harness.home / ".tailplan-backups").exists())
            self.assertFalse(harness.app_dir.exists())
            self.assertEqual("", harness.commands())

    def test_deferred_https_verification_rejects_non_loopback_host_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))

            completed = harness.run(
                "--defer-https-verify",
                check=False,
                TAILPLAN_HOST="100.100.100.100",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("requires a loopback TAILPLAN_HOST", completed.stderr)
            self.assertFalse((harness.home / ".tailplan-backups").exists())
            self.assertFalse(harness.app_dir.exists())
            commands = harness.commands()
            self.assertNotIn("systemctl", commands)
            self.assertNotIn("tailscale <serve> <--bg>", commands)

    def test_deferred_https_verification_checks_local_deployment_and_exact_serve_handler(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))

            completed = harness.run(
                "--defer-https-verify",
                TAILPLAN_HOST="127.0.0.1",
                MOCK_HTTPS_FAIL="1",
            )

            self.assertEqual(0, completed.returncode)
            self.assertIn(
                "Tailplan local deployment ready; external HTTPS verification required: "
                "https://tailnode.example.ts.net/tailplan",
                completed.stdout,
            )
            self.assertNotIn("Tailplan ready:", completed.stdout)
            self.assertIn("Backup retained:", completed.stdout)
            self.assertNotIn("token", completed.stdout.lower())
            commands = harness.commands()
            self.assertIn("<http://127.0.0.1:9127/healthz>", commands)
            self.assertIn("<http://127.0.0.1:9128/readyz>", commands)
            self.assertNotIn("curl <-fsS> <--max-time> <2> <https://", commands)
            self.assertIn("systemctl <--user> <is-enabled> <tailplan.service>", commands)
            self.assertIn("systemctl <--user> <is-active> <tailplan.service>", commands)
            self.assertIn(
                "tailscale <serve> <--bg> <--https=443> <--set-path=/tailplan> <--yes> "
                "<http://127.0.0.1:9128>",
                commands,
            )
            config = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            handlers = config["Web"]["tailnode.example.ts.net:443"]["Handlers"]
            self.assertEqual({"Proxy": "http://127.0.0.1:8080"}, handlers["/"])
            self.assertEqual({"Proxy": "http://127.0.0.1:9128"}, handlers["/tailplan"])
            self.assertNotIn(
                "/tailplan",
                config["Web"]["unrelated.example.ts.net:443"]["Handlers"],
            )

    def test_deferred_https_verification_rerun_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))

            first = harness.run("--defer-https-verify", TAILPLAN_HOST="::1")
            token = (harness.data_dir / "token").read_bytes()
            second = harness.run("--defer-https-verify", TAILPLAN_HOST="::1")

            self.assertEqual(0, first.returncode)
            self.assertEqual(0, second.returncode)
            self.assertEqual(token, (harness.data_dir / "token").read_bytes())
            self.assertEqual(
                2,
                harness.commands().count("systemctl <--user> <restart> <tailplan.service>"),
            )
            config = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            handlers = config["Web"]["tailnode.example.ts.net:443"]["Handlers"]
            self.assertEqual({"Proxy": "http://127.0.0.1:8080"}, handlers["/"])
            self.assertEqual({"Proxy": "http://127.0.0.1:9128"}, handlers["/tailplan"])

    def test_env_runner_arguments_checksums_and_modes_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            harness.run()

            env = parse_env(harness.data_dir / "env")
            self.assertEqual("100.100.100.100", env["TAILPLAN_HOST"])
            self.assertEqual("9127", env["TAILPLAN_PORT"])
            self.assertEqual("127.0.0.1", env["TAILPLAN_PROXY_HOST"])
            self.assertEqual("9128", env["TAILPLAN_PROXY_PORT"])
            self.assertEqual("https://tailnode.example.ts.net/tailplan", env["TAILPLAN_BASE_URL"])
            self.assertEqual(env["TAILPLAN_BASE_URL"], env["TAILPLAN_REDIRECT_VIEW_BASE_URL"])

            runner_mock = harness.root / "runner-bin"
            runner_mock.mkdir()
            write_executable(
                runner_mock / "python3",
                '#!/usr/bin/env bash\nprintf \'<%s>\\n\' "$@" > "$RUNNER_LOG"\n',
            )
            runner_log = harness.root / "runner.log"
            subprocess.run(
                [str(harness.app_dir / "bin/run-tailplan")],
                env={
                    **os.environ,
                    **env,
                    "PATH": f"{runner_mock}:{os.environ['PATH']}",
                    "RUNNER_LOG": str(runner_log),
                },
                check=True,
            )
            args = runner_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                [
                    f"<{harness.app_dir / 'tailplan_server.py'}>",
                    "<--host>",
                    "<100.100.100.100>",
                    "<--port>",
                    "<9127>",
                    "<--proxy-host>",
                    "<127.0.0.1>",
                    "<--proxy-port>",
                    "<9128>",
                    "<--data-dir>",
                    f"<{harness.data_dir}>",
                    "<--token-file>",
                    f"<{harness.data_dir / 'token'}>",
                    "<--base-url>",
                    "<https://tailnode.example.ts.net/tailplan>",
                    "<--redirect-view-base-url>",
                    "<https://tailnode.example.ts.net/tailplan>",
                ],
                args,
            )

            installed = {
                harness.app_dir / "tailplan_server.py": REPO / "tailplan_server.py",
                harness.app_dir / "bin/run-tailplan": REPO / "bin/run-tailplan",
                harness.bin_dir / "tailplan-share": REPO / "bin/tailplan-share",
                harness.bin_dir / "tailplan-share-public": REPO / "bin/tailplan-share-public",
            }
            for destination, source in installed.items():
                with self.subTest(destination=destination):
                    self.assertEqual(
                        hashlib.sha256(source.read_bytes()).digest(),
                        hashlib.sha256(destination.read_bytes()).digest(),
                    )
                    self.assertEqual(0o755, stat.S_IMODE(destination.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE((harness.data_dir / "token").stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE((harness.data_dir / "env").stat().st_mode))
            for directory in (
                harness.data_dir,
                harness.data_dir / "drafts",
                harness.data_dir / "generated",
            ):
                self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode))

    def test_failed_https_verification_restores_absent_serve_handler_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            old_config = harness.serve_config.read_bytes()
            harness.app_dir.mkdir(parents=True)
            (harness.app_dir / "tailplan_server.py").write_text("old server\n", encoding="utf-8")
            (harness.app_dir / "run-tailplan.sh").write_text("legacy\n", encoding="utf-8")
            harness.bin_dir.mkdir(parents=True)
            (harness.bin_dir / "tailplan-share").write_text("old cli\n", encoding="utf-8")
            harness.data_dir.mkdir(parents=True)
            (harness.data_dir / "env").write_text("OLD_ENV=1\n", encoding="utf-8")
            (harness.data_dir / "token").write_text("preserved-secret\n", encoding="utf-8")
            harness.unit.parent.mkdir(parents=True)
            harness.unit.write_text("old unit\n", encoding="utf-8")

            completed = harness.run(check=False, MOCK_HTTPS_FAIL="1")

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("Rollback complete", completed.stderr)
            self.assertNotIn("preserved-secret", completed.stdout + completed.stderr)
            self.assertEqual(old_config, harness.serve_config.read_bytes())
            self.assertEqual("old server\n", (harness.app_dir / "tailplan_server.py").read_text())
            self.assertEqual("legacy\n", (harness.app_dir / "run-tailplan.sh").read_text())
            self.assertEqual("old cli\n", (harness.bin_dir / "tailplan-share").read_text())
            self.assertFalse((harness.bin_dir / "tailplan-share-public").exists())
            self.assertEqual("OLD_ENV=1\n", (harness.data_dir / "env").read_text())
            self.assertEqual("old unit\n", harness.unit.read_text())
            self.assertEqual("preserved-secret\n", (harness.data_dir / "token").read_text())
            self.assertIn(
                "tailscale <serve> <--https=443> <--set-path=/tailplan> <off>",
                harness.commands(),
            )
            self.assertNotIn("get-config", harness.commands())
            self.assertNotIn("set-config", harness.commands())

    def test_failed_install_restores_prior_proxy_with_targeted_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            config = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            handlers = config["Web"]["tailnode.example.ts.net:443"]["Handlers"]
            handlers["/tailplan"] = {"Proxy": "http://127.0.0.1:7777"}
            handlers["/other"] = {"Proxy": "http://127.0.0.1:9999"}
            harness.serve_config.write_text(json.dumps(config), encoding="utf-8")

            completed = harness.run(check=False, MOCK_HTTPS_FAIL="1")

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual(
                config,
                json.loads(harness.serve_config.read_text(encoding="utf-8")),
            )
            self.assertIn(
                "tailscale <serve> <--bg> <--https=443> <--set-path=/tailplan> <--yes> <http://127.0.0.1:7777>",
                harness.commands(),
            )
            backup_line = next(
                line
                for line in completed.stderr.splitlines()
                if line.startswith("Rollback complete. Backup retained: ")
            )
            backup = Path(backup_line.removeprefix("Rollback complete. Backup retained: "))
            metadata = backup / "state/serve-prior.json"
            self.assertEqual(
                {"present": True, "proxy": "http://127.0.0.1:7777"},
                json.loads(metadata.read_text(encoding="utf-8")),
            )
            self.assertEqual(0o600, stat.S_IMODE(metadata.stat().st_mode))
            self.assertFalse((backup / "serve-config.json").exists())

    def test_idempotent_reinstall_preserves_token_handler_and_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            harness.run()
            token = (harness.data_dir / "token").read_bytes()
            config = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            config["Web"]["tailnode.example.ts.net:443"]["Handlers"]["/other"] = {
                "Proxy": "http://127.0.0.1:9999"
            }
            harness.serve_config.write_text(json.dumps(config), encoding="utf-8")

            harness.run()

            self.assertEqual(token, (harness.data_dir / "token").read_bytes())
            final = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            handlers = final["Web"]["tailnode.example.ts.net:443"]["Handlers"]
            self.assertIn("/", handlers)
            self.assertIn("/other", handlers)
            self.assertEqual("http://127.0.0.1:9128", handlers["/tailplan"]["Proxy"])
            self.assertEqual(
                2,
                harness.commands().count("systemctl <--user> <restart> <tailplan.service>"),
            )

    def test_no_serve_requires_base_url_and_never_calls_tailscale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            failed = harness.run(
                "--no-serve",
                check=False,
                TAILPLAN_HOST="127.0.0.1",
                MOCK_TAILSCALE_FORBIDDEN="1",
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("TAILPLAN_BASE_URL", failed.stderr)
            self.assertNotIn("tailscale", harness.commands())

            harness.run(
                TAILPLAN_CONFIGURE_SERVE="0",
                TAILPLAN_HOST="127.0.0.1",
                TAILPLAN_BASE_URL="https://portable.example/tailplan",
                MOCK_TAILSCALE_FORBIDDEN="1",
            )
            self.assertNotIn("tailscale", harness.commands())
            env = parse_env(harness.data_dir / "env")
            self.assertEqual("https://portable.example/tailplan", env["TAILPLAN_BASE_URL"])

    def test_paths_with_spaces_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td), spaces=True)
            harness.run(
                "--no-serve",
                TAILPLAN_HOST="127.0.0.1",
                TAILPLAN_BASE_URL="https://portable.example/tailplan",
                MOCK_TAILSCALE_FORBIDDEN="1",
            )

            env = parse_env(harness.data_dir / "env")
            self.assertEqual(str(harness.app_dir), env["TAILPLAN_APP_DIR"])
            self.assertTrue((harness.app_dir / "bin/run-tailplan").is_file())
            self.assertTrue((harness.bin_dir / "tailplan-share").is_file())
            installed_unit = harness.unit.read_text(encoding="utf-8")
            escaped_env = str(harness.data_dir / "env").replace(" ", r"\x20")
            escaped_data = str(harness.data_dir).replace(" ", r"\x20")
            self.assertIn(f"EnvironmentFile={escaped_env}", installed_unit)
            self.assertIn(f"ReadWritePaths={escaped_data}", installed_unit)

    def test_preflight_rejects_control_characters_in_listener_hosts(self) -> None:
        cases = (
            ("TAILPLAN_HOST", "127.0.0.1\nINJECTED=1"),
            ("TAILPLAN_HOST", "127.0.0.1\rINJECTED=1"),
            ("TAILPLAN_HOST", "127.0.0.1\t"),
            ("TAILPLAN_PROXY_HOST", "127.0.0.1\nINJECTED=1"),
            ("TAILPLAN_PROXY_HOST", "127.0.0.1\x7f"),
        )
        for name, value in cases:
            with self.subTest(name=name, value=repr(value)), tempfile.TemporaryDirectory() as td:
                harness = InstallerHarness(Path(td))
                overrides = {
                    "TAILPLAN_HOST": "127.0.0.1",
                    "TAILPLAN_BASE_URL": "https://portable.example/tailplan",
                    "MOCK_TAILSCALE_FORBIDDEN": "1",
                    name: value,
                }

                completed = harness.run("--no-serve", check=False, **overrides)

                self.assertNotEqual(0, completed.returncode)
                self.assertFalse((harness.home / ".tailplan-backups").exists())
                self.assertFalse(harness.app_dir.exists())

    def test_preflight_rejects_all_control_characters_in_serialized_values(self) -> None:
        cases = (
            ("TAILPLAN_APP_DIR", "app\troot"),
            ("TAILPLAN_BASE_URL", "https://portable.example/tail\tplan"),
            ("TAILPLAN_BASE_URL", "https://portable.example/tail\x01plan"),
            ("TAILPLAN_REDIRECT_VIEW_BASE_URL", "https://portable.example/view\x7f"),
            ("TAILPLAN_PORT", "9127\x1f"),
        )
        for name, raw_value in cases:
            with self.subTest(name=name, value=repr(raw_value)), tempfile.TemporaryDirectory() as td:
                harness = InstallerHarness(Path(td))
                value = str(harness.root / raw_value) if name == "TAILPLAN_APP_DIR" else raw_value
                overrides = {
                    "TAILPLAN_HOST": "127.0.0.1",
                    "TAILPLAN_BASE_URL": "https://portable.example/tailplan",
                    "MOCK_TAILSCALE_FORBIDDEN": "1",
                    name: value,
                }

                completed = harness.run("--no-serve", check=False, **overrides)

                self.assertNotEqual(0, completed.returncode)
                self.assertIn("control", completed.stderr.lower())
                self.assertFalse((harness.home / ".tailplan-backups").exists())
                self.assertFalse(harness.app_dir.exists())

    def test_listener_hosts_must_be_ip_addresses_and_proxy_must_be_loopback(self) -> None:
        cases = (
            ("TAILPLAN_HOST", "localhost"),
            ("TAILPLAN_HOST", "999.1.1.1"),
            ("TAILPLAN_PROXY_HOST", "localhost"),
            ("TAILPLAN_PROXY_HOST", "192.0.2.10"),
        )
        for name, value in cases:
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as td:
                harness = InstallerHarness(Path(td))
                overrides = {
                    "TAILPLAN_HOST": "127.0.0.1",
                    "TAILPLAN_BASE_URL": "https://portable.example/tailplan",
                    "MOCK_TAILSCALE_FORBIDDEN": "1",
                    name: value,
                }

                completed = harness.run("--no-serve", check=False, **overrides)

                self.assertNotEqual(0, completed.returncode)
                self.assertFalse((harness.home / ".tailplan-backups").exists())

    def test_ipv6_listener_hosts_use_bracketed_verification_urls(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))

            harness.run(
                "--no-serve",
                TAILPLAN_HOST="::1",
                TAILPLAN_PROXY_HOST="::1",
                TAILPLAN_BASE_URL="https://portable.example/tailplan",
                MOCK_TAILSCALE_FORBIDDEN="1",
            )

            env = parse_env(harness.data_dir / "env")
            self.assertEqual("::1", env["TAILPLAN_HOST"])
            self.assertEqual("::1", env["TAILPLAN_PROXY_HOST"])
            commands = harness.commands()
            self.assertIn("<http://[::1]:9127/healthz>", commands)
            self.assertIn("<http://[::1]:9128/readyz>", commands)

    def test_rollback_never_restarts_a_previously_inactive_service(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            harness.unit.parent.mkdir(parents=True)
            harness.unit.write_text("old unit\n", encoding="utf-8")

            completed = harness.run(check=False, MOCK_ACTIVE="0")

            self.assertNotEqual(0, completed.returncode)
            commands = harness.commands()
            self.assertEqual(
                1,
                commands.count("systemctl <--user> <restart> <tailplan.service>"),
            )
            self.assertIn("systemctl <--user> <stop> <tailplan.service>", commands)

    def test_atomic_write_and_move_failures_leave_no_temps_or_fresh_directories(self) -> None:
        boundaries = (
            "token-write",
            "token-move",
            "env-write",
            "env-move",
            "unit-write",
            "unit-move",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as td:
                harness = InstallerHarness(Path(td))

                completed = harness.run(
                    "--no-serve",
                    check=False,
                    TAILPLAN_HOST="127.0.0.1",
                    TAILPLAN_BASE_URL="https://portable.example/tailplan",
                    MOCK_TAILSCALE_FORBIDDEN="1",
                    MOCK_FAIL_BOUNDARY=boundary,
                )

                self.assertNotEqual(0, completed.returncode)
                self.assertIn("Rollback complete", completed.stderr)
                self.assertNotIn("Rollback incomplete", completed.stderr)
                self.assertEqual([], list(harness.root.rglob(".token.*.tmp")))
                self.assertEqual([], list(harness.root.rglob(".tailplan.env.*.tmp")))
                self.assertEqual([], list(harness.root.rglob(".tailplan.service.*.tmp")))
                for directory in (
                    harness.app_dir,
                    harness.bin_dir,
                    harness.data_dir,
                    harness.unit.parent,
                ):
                    self.assertFalse(directory.exists())

    def test_fresh_failed_install_removes_every_created_deployment_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))

            completed = harness.run(check=False, MOCK_HTTPS_FAIL="1")

            self.assertNotEqual(0, completed.returncode)
            for directory in (
                harness.app_dir,
                harness.bin_dir,
                harness.data_dir,
                harness.data_dir / "drafts",
                harness.data_dir / "generated",
                harness.unit.parent,
            ):
                with self.subTest(directory=directory):
                    self.assertFalse(directory.exists())

    def test_failed_install_restores_existing_data_directory_modes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            drafts = harness.data_dir / "drafts"
            generated = harness.data_dir / "generated"
            drafts.mkdir(parents=True)
            generated.mkdir()
            harness.data_dir.chmod(0o750)
            drafts.chmod(0o755)
            generated.chmod(0o711)
            (harness.data_dir / "token").write_text("preserved-secret\n", encoding="utf-8")

            completed = harness.run(check=False, MOCK_HTTPS_FAIL="1")

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual(0o750, stat.S_IMODE(harness.data_dir.stat().st_mode))
            self.assertEqual(0o755, stat.S_IMODE(drafts.stat().st_mode))
            self.assertEqual(0o711, stat.S_IMODE(generated.stat().st_mode))

    def test_failed_install_restores_existing_token_mode_without_copying_secret(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            harness.data_dir.mkdir(parents=True)
            token = harness.data_dir / "token"
            token.write_text("preserved-secret\n", encoding="utf-8")
            token.chmod(0o640)

            completed = harness.run(check=False, MOCK_HTTPS_FAIL="1")

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual("preserved-secret\n", token.read_text(encoding="utf-8"))
            self.assertEqual(0o640, stat.S_IMODE(token.stat().st_mode))
            self.assertNotIn("preserved-secret", completed.stdout + completed.stderr)
            backup_line = next(
                line
                for line in completed.stderr.splitlines()
                if line.startswith("Rollback complete. Backup retained: ")
            )
            backup = Path(backup_line.removeprefix("Rollback complete. Backup retained: "))
            self.assertFalse(any(path.name == "token" for path in backup.rglob("*")))

    def test_preflight_rejects_symlink_deployment_roots_without_touching_targets(self) -> None:
        for root_name in ("app", "bin", "data", "unit"):
            with self.subTest(root=root_name), tempfile.TemporaryDirectory() as td:
                harness = InstallerHarness(Path(td))
                external = harness.root / f"external-{root_name}"
                external.mkdir()
                marker = external / "keep"
                marker.write_text("untouched\n", encoding="utf-8")
                target = {
                    "app": harness.app_dir,
                    "bin": harness.bin_dir,
                    "data": harness.data_dir,
                    "unit": harness.unit.parent,
                }[root_name]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(external, target_is_directory=True)

                completed = harness.run(
                    "--no-serve",
                    check=False,
                    TAILPLAN_HOST="127.0.0.1",
                    TAILPLAN_BASE_URL="https://portable.example/tailplan",
                    MOCK_TAILSCALE_FORBIDDEN="1",
                )

                self.assertNotEqual(0, completed.returncode)
                self.assertEqual(["keep"], sorted(path.name for path in external.iterdir()))
                self.assertEqual("untouched\n", marker.read_text(encoding="utf-8"))
                self.assertFalse((harness.home / ".tailplan-backups").exists())

    def test_preflight_rejects_symlink_token_env_and_unit_files(self) -> None:
        for target_name in ("token", "env", "unit"):
            with self.subTest(target=target_name), tempfile.TemporaryDirectory() as td:
                harness = InstallerHarness(Path(td))
                external = harness.root / f"external-{target_name}"
                external.write_text("external-content\n", encoding="utf-8")
                external.chmod(0o640)
                if target_name == "unit":
                    target = harness.unit
                else:
                    target = harness.data_dir / target_name
                target.parent.mkdir(parents=True)
                target.symlink_to(external)

                completed = harness.run(
                    "--no-serve",
                    check=False,
                    TAILPLAN_HOST="127.0.0.1",
                    TAILPLAN_BASE_URL="https://portable.example/tailplan",
                    MOCK_TAILSCALE_FORBIDDEN="1",
                )

                self.assertNotEqual(0, completed.returncode)
                self.assertEqual("external-content\n", external.read_text(encoding="utf-8"))
                self.assertEqual(0o640, stat.S_IMODE(external.stat().st_mode))
                self.assertFalse((harness.home / ".tailplan-backups").exists())

    def test_preflight_rejects_nonregular_token_env_and_unit_files(self) -> None:
        for target_name in ("token", "env", "unit"):
            with self.subTest(target=target_name), tempfile.TemporaryDirectory() as td:
                harness = InstallerHarness(Path(td))
                if target_name == "unit":
                    target = harness.unit
                else:
                    target = harness.data_dir / target_name
                target.mkdir(parents=True)

                completed = harness.run(
                    "--no-serve",
                    check=False,
                    TAILPLAN_HOST="127.0.0.1",
                    TAILPLAN_BASE_URL="https://portable.example/tailplan",
                    MOCK_TAILSCALE_FORBIDDEN="1",
                )

                self.assertNotEqual(0, completed.returncode)
                self.assertTrue(target.is_dir())
                self.assertFalse((harness.home / ".tailplan-backups").exists())

    def test_preflight_rejects_unsafe_or_overlapping_deployment_roots(self) -> None:
        for overrides in (
            {"TAILPLAN_APP_DIR": "/"},
            {"TAILPLAN_APP_DIR": "relative/app"},
            {"TAILPLAN_BIN_DIR": "APP_DIR"},
            {"TAILPLAN_DATA_DIR": "APP_CHILD"},
            {"TAILPLAN_APP_DIR": "UNIT_DIR"},
            {"TAILPLAN_BACKUP_DIR": "DATA_CHILD"},
        ):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as td:
                harness = InstallerHarness(Path(td))
                resolved = {
                    key: (
                        str(harness.app_dir)
                        if value == "APP_DIR"
                        else str(harness.unit.parent)
                        if value == "UNIT_DIR"
                        else str(harness.app_dir / "data")
                        if value == "APP_CHILD"
                        else str(harness.data_dir / "backups")
                        if value == "DATA_CHILD"
                        else value
                    )
                    for key, value in overrides.items()
                }

                completed = harness.run(
                    "--no-serve",
                    check=False,
                    TAILPLAN_HOST="127.0.0.1",
                    TAILPLAN_BASE_URL="https://portable.example/tailplan",
                    MOCK_TAILSCALE_FORBIDDEN="1",
                    **resolved,
                )

                self.assertNotEqual(0, completed.returncode)
                self.assertFalse((harness.home / ".tailplan-backups").exists())

    def test_preflight_rejects_empty_existing_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            harness.data_dir.mkdir(parents=True)
            token = harness.data_dir / "token"
            token.touch()

            completed = harness.run(
                "--no-serve",
                check=False,
                TAILPLAN_HOST="127.0.0.1",
                TAILPLAN_BASE_URL="https://portable.example/tailplan",
                MOCK_TAILSCALE_FORBIDDEN="1",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual(b"", token.read_bytes())
            self.assertFalse((harness.home / ".tailplan-backups").exists())

    def test_serve_preflight_rejects_unsupported_existing_handler(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            config = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            config["Web"]["tailnode.example.ts.net:443"]["Handlers"]["/tailplan"] = {
                "Path": "/tmp/not-supported"
            }
            harness.serve_config.write_text(json.dumps(config), encoding="utf-8")

            completed = harness.run(check=False)

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual(config, json.loads(harness.serve_config.read_text(encoding="utf-8")))
            self.assertNotIn("systemctl <--user> <daemon-reload>", harness.commands())
            self.assertFalse((harness.home / ".tailplan-backups").exists())

    def test_serve_verification_rejects_matching_path_on_unrelated_host(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            harness = InstallerHarness(Path(td))
            config = json.loads(harness.serve_config.read_text(encoding="utf-8"))
            unrelated_handlers = config["Web"]["unrelated.example.ts.net:443"]["Handlers"]
            unrelated_handlers["/tailplan"] = {"Proxy": "http://127.0.0.1:9128"}
            harness.serve_config.write_text(json.dumps(config), encoding="utf-8")

            completed = harness.run(check=False, MOCK_SERVE_WRONG_HOST="1")

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("does not exactly match", completed.stderr)
            self.assertEqual(config, json.loads(harness.serve_config.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
