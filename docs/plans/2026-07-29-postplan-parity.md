# Tailplan parity implementation plan

Status: Completed historical plan.

## Goal

Make Tailplan reliable for one-command private publishing.
Keep the service small.
Use the Python standard library.
Keep tailnet access and upload-token authentication.

## Root causes

1. The sanitizer rejected visible text that contained protocol-like words.
2. Concurrent uploads could lose metadata updates.
3. Equal source names could use the same generated path.
4. Invalid state files could appear as empty state.
5. The local publisher did not retry or verify uploads.
6. Installed files could differ from the running service.
7. Plain HTTP links did not give reliable browser access.
8. API errors did not identify invalid requests correctly.

## Required results

- One command publishes or updates a draft.
- Successful output contains a private HTTPS URL.
- The local publisher verifies the uploaded content.
- The sanitizer accepts visible text and rejects URL attributes with prohibited schemes.
- Each source file has an independent generated path and mapping.
- A stale implicit mapping creates one replacement draft.
- The local publisher retries only transient failures.
- Parallel operations do not lose metadata or version numbers.
- Viewer routes return validated documents without an iframe.
- API responses use the correct error status.
- The installer verifies the running build.
- The installer restores the previous state after a failure.
- Tailscale Serve keeps unrelated handlers.

## Work item 1: Server and storage

Add structural HTML validation.
Add precise API error responses.
Serialize metadata updates.
Write metadata through a unique temporary file.
Reject invalid metadata.
Limit request size, duration, and concurrency.
Add tests for each server contract.

## Work item 2: Document rendering

Return the uploaded HTML from each viewer route.
Set strict response security headers.
Add `rel="noopener noreferrer"` to links that open a new tab.
Keep latest and historical version behavior consistent.
Test the primary routes and compatibility routes.

## Work item 3: Local publisher

Use the resolved source path as the mapping key.
Add a source-path hash to each generated name.
Write local publisher state atomically.
Reject invalid local publisher state.
Retry only transport, rate-limit, and server failures.
Replace a stale implicit mapping one time.
Verify the returned version and content digest.
Provide stable JSON output.

## Work item 4: Installation

Validate staged sources before installation.
Back up each managed file.
Restart the active service after installation.
Verify the build and health endpoints.
Restore the previous service after a failure.
Add only the `/tailplan` Tailscale Serve handler.
Keep unrelated Serve handlers.
Support user services and system services.

## Work item 5: Release gate

Publish representative Markdown content.
Test two equal source names from different directories.
Test an update and a forced new draft.
Test desktop and mobile browser layouts.
Run the supported tests and service checks.
Run an independent security review.
Scan the tracked tree and Git history for credentials, personal paths, and private infrastructure.
Publish only after every release gate passes.
