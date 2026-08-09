---
name: tailplan
description: >-
  Publish plans, recaps, reports, and static HTML files to a private Tailplan
  service. Use this skill when a user requests a Tailplan or a private draft URL.
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

The remote client transfers only the source file through OpenSSH.
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
