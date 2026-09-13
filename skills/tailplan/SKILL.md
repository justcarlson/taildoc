---
name: tailplan
description: >-
  Publish plans, recaps, reports, and static HTML files to a private Tailplan
  service. Use this skill when a user requests a Tailplan, a private draft URL,
  draft history, dashboard access, or publishing key management.
metadata:
  visibility: exported
---

# Tailplan

Tailplan is a private, tailnet-only draft publisher.
The name Tailplan does not mean a checklist or an agent plan mode.

## Publish a draft

1. Read the complete source material.
2. Separate completed work from incomplete work.
3. Create one standalone source file in Markdown, plain text, or static HTML format without active content.
4. Use a temporary source file unless the user requests a durable repository document.
5. Check the source file for credentials, secret values, access URLs, and medical, legal, financial, or identifying personal data.
6. Remove each credential, secret value, access URL, and personal record before publication.
7. Publish a new draft:

   ```sh
   tailplan-share /path/to/artifact.md --new
   ```

8. Copy the `URL:` value from the command output.
9. Verify the URL with `curl -fsS` from a tailnet device.
10. If the source material describes a change to a rendered Tailplan page, open the published page with the `agent-browser` UAT tool.
11. Return the URL and one short description.
12. If step 4 created a durable repository document, report its repository path.

The remote client transfers the source file and repository metadata through OpenSSH.
The upload token remains on the Tailplan server.
The local publisher uploads directly.

Always produce a Tailplan URL when the user asks for a Tailplan.
Do not return only the path to a Markdown source file as a fallback.
If `tailplan-share` is unavailable, run `./install-client.sh` from the repository.
Start a new agent session after skill installation.

## Update a draft

Update a draft only when the user requests a revision:

```sh
tailplan-share /path/to/artifact.md --draft <draft-id>
```

Use `--new` for all other requests.
Use the public mirror command only after the user approves public access.

## Manage published drafts

Use `tailplan list --json` to find drafts and `tailplan history ID --json` to inspect their versions.
Use `tailplan upload FILE --description TEXT` to attach a stable description.
Open `/dashboard` under the configured base URL for repository groups and version history.
Open `/cli/auth` to generate or revoke named API keys.
The direct client accepts those keys through `tailplan auth login`.
The SSH client uses the existing SSH identity.

Use `tailplan disable ID`, `tailplan enable ID`, or `tailplan delete ID` for requested lifecycle changes.
Verify the resulting state with the draft list and viewer URL.
Inline script source can be published, but the viewer blocks its execution.
Use static HTML and CSS for content that must work in the browser.
