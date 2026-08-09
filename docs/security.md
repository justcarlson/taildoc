# Security model

Tailplan uses the tailnet as the viewer security boundary.
The upload API also requires an upload token.
Tailplan does not provide individual viewer accounts.

## Suitable content

Tailplan is suitable for these file types only if the files contain no restricted content:

- Agent plans
- Draft reports
- Static HTML pages
- Internal runbooks
- Research notes

## Restricted content

Do not upload secrets or credentials.
Do not upload links that grant access.
Do not upload mailbox, medical, legal, tax, or financial records.
Do not upload a document if only a subset of the authorized Tailplan viewers can view it.

## Access controls

Keep the upload-token file at mode `0600`.
Keep the upload token only on the Tailplan server.
Use Tailscale access controls to restrict viewer access.
Use a forced-command SSH account for remote publishers.

## Public sharing

Use `tailplan-share-public` only after the user explicitly approves public access for the selected draft.
The command copies one draft to public Postplan storage.
The command does not expose the Tailplan service.
