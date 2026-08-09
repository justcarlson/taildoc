# Tailplan parity execution report

Status: Complete.

Repository commit: `ac7d0f16e61c687c460230c200df99380eead44c`.

## Outcome

Tailplan provides a private publishing command:

```sh
tailplan-share document.md --new
```

The command renders Markdown and sends an authenticated request.
The command verifies the published content.
The command returns a private HTTPS URL.

## Architecture

- Tailplan runs as a systemd service.
- A locked service account owns the system data.
- The primary and proxy listeners can bind to loopback.
- Tailscale Serve provides private HTTPS access.
- The installer keeps unrelated Serve handlers.
- Compatibility viewer routes can redirect to the HTTPS base URL.
- An old upload service cannot use the active credential.

## Reliability work

### Storage and API

- Tailplan locks metadata updates.
- Tailplan writes metadata atomically.
- Tailplan rejects invalid metadata.
- Tailplan confines all object paths.
- Tailplan verifies SHA-256 content digests.
- Tailplan limits request bodies and request concurrency.
- Tailplan returns stable JSON errors.
- Tailplan reports build-aware health and readiness.
- Tailplan renders documents without iframes.

### Local publisher

- The local publisher keeps durable source mappings.
- The local publisher locks each source publication.
- The local publisher uses collision-resistant generated paths.
- The local publisher uses idempotency keys to prevent duplicate changes during retries.
- The local publisher retries bounded transient failures.
- The local publisher blocks authenticated redirects.
- The local publisher verifies response headers and content.
- The local publisher provides stable JSON output.
- The local publisher does not print credentials or source content.

### Markdown rendering

- The renderer uses deterministic functions from the Python standard library.
- The renderer supports headings, paragraphs, lists, blockquotes, fenced code blocks, and horizontal rules.
- The renderer supports task lists and tables.
- The renderer escapes raw HTML.
- The renderer rejects prohibited URL protocols.
- The renderer limits blockquote depth.
- The document layout prevents mobile overflow.

### Deployment

- The installer supports user and system modes.
- The installer restores managed state after a failure.
- The installer keeps the existing token.
- Installer backups do not contain the token.
- The installer verifies file modes and checksums.
- The installer verifies the service and endpoints.
- The installer supports deferred HTTPS verification.
- Repeated installation keeps application data.

## Historical verification record

The original execution record reported these results:

- The warning-as-error test run passed 176 tests.
- The installer test run passed 41 tests.
- The server test run passed 56 tests.
- The data migration verified all 41 test drafts.
- New uploads succeeded through private HTTPS.
- The local publisher used its configured endpoint.
- The existing Serve root handler did not change.
- Browser checks found no iframe or script.
- Browser checks found no horizontal overflow.
- Shell syntax, Python compilation, and static checks passed.

These results describe the historical deployment.
Run the current release checks before a new release.

## Rollback record

A userspace-networking host could not reach its own tailnet HTTPS name.
The installer detected the failed check.
The installer restored the previous files, service, data, and Serve state.
A later installation used deferred HTTPS verification.
A second tailnet node then verified the deployment.

## Client examples

Create a new draft:

```sh
tailplan-share report.md --new
```

Update the mapped draft:

```sh
tailplan-share report.md
```

Update a known draft:

```sh
tailplan-share report.md --draft <draft-id>
```

Request machine-readable output:

```sh
tailplan-share report.md --new --json
```
