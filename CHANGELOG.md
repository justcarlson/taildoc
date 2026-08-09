# Changelog

This file records user-visible changes for each release.
The project uses the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) section names.
The project uses semantic version numbers.

## Unreleased

## 0.2.1 - 2026-08-09

### Fixed

- The SSH publisher account now accepts its forced public key without enabling a password.
- The SSH publisher guard now uses a root-owned copy of the selected local publisher.

### Security

- The publisher installer replaces a locked password with a value that no password can produce.

## 0.2.0 - 2026-08-09

### Added

- Tailplan 0.2.0 adds a Docker image and a Docker Compose deployment.
- The SSH publisher account installer creates a dedicated forced-command account.
- The README adds local evaluation and development instructions.
- The repository adds public contribution, security, support, and maintenance files.

### Changed

- The native installer improves installation checks, rollback behavior, and service verification.
- Tailplan 0.2.0 adds health and readiness checks for each server deployment.

### Security

- The forced-command guard limits remote publication to validated command forms.
- The remote client keeps the upload token on the Tailplan server.
