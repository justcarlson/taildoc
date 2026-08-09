# Support

## Supported version

The latest tagged release receives bug fixes and security fixes.
Version 0.2.0 is the current release line.
Upgrade before you report a defect in an older version.

## Compatibility

| Component | Supported environment |
| --- | --- |
| Local server | Linux with Python 3.11 through 3.13 |
| Native service | Linux with systemd and Python 3.11 through 3.13 |
| Container | Docker Engine with Docker Compose v2 |
| Remote client | OpenSSH with legacy SCP protocol support |
| Tailscale Serve | Current stable Tailscale release |

The native installer needs Bash, curl, GNU core utilities, and systemd.
Tailscale is optional when you use `--no-serve` and an HTTPS proxy.
The container does not include Tailscale or an HTTPS proxy.

## Get help

Search the open and closed issues before you create an issue.
Use the bug report form for a reproducible defect.
Include sanitized log entries that show the defect and its timestamp.
Remove upload tokens, private hostnames, draft content, and personal data from all reports.

Read [SECURITY.md](SECURITY.md) to report a vulnerability.
Do not use a public issue for a vulnerability.
