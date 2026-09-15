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

8. Copy the `URL:` value from the command output. It uses the automatic preview
   page at `/d/ID/share` when the server supports it. For JSON output, prefer
   `shareUrl` and fall back to `publicUrl`.
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

## Built-in document themes

Use the server theme system for Markdown and plain text documents.
List installed themes before choosing a non-default palette:

```sh
tailplan themes --json
tailplan upload /path/to/plan.md --new --theme warm-editorial
tailplan-share /path/to/notes.md --new --theme flexoki-light
tailplan upload /path/to/report.md --new --theme auto --document-type technical
```

Omitted theme options use automatic selection with warm editorial as the fallback.
Types `plan`, `report`, and `essay` use warm editorial. `notes` uses Flexoki Light.
Type `technical` uses Tokyo Night. `reference` uses Solarized Light.
An explicit theme ID overrides automatic selection.
The only supported layout is `reading`, selected with `--layout reading`.
Both direct and SSH clients support these commands.

### User typography preference

Use `serif-sans` by default, with serif headings and sans-serif body text.
Use `all-sans` when the user requests all sans-serif typography.
The requested alternatives are serif headings with sans-serif body, and all sans-serif.
Do not interpret this preference as all-serif.
Typography is independent of the color palette. Keep code monospace.
Use system font stacks without external fonts or images.

```sh
tailplan typography --json
tailplan-share plan.md --new --typography serif-sans
tailplan-share notes.md --new --theme flexoki-light --typography all-sans
```

Direct, local, and SSH publication support `--typography ID`.
The API accepts `typography` on Markdown or text uploads.
List presets at `GET /api/typography`.
Add presets through `tailplan_themes/typography/*.json` and its schema, as described in `docs/themes.md`.
Each version stores resolved font tokens and a preset checksum.
Never regenerate historical documents to change typography.

The server stores resolved HTML and theme metadata in each published version.
Palette updates do not change stored documents.
Plain `.txt` files preserve literal markup and line breaks in the reading layout.
Arbitrary static HTML keeps its original styles. Do not pass theme options with HTML files.
Use HTML when the document requires a custom layout beyond the reading layout.
Do not claim unsupported theme flags or a popularity ranking.
For custom HTML, retain the warm cream and olive palette unless the user requests another style.
Use readable full-width prose on phones with modest padding. Generated Markdown
tables automatically stack into labeled rows on screens up to 640px; larger
screens retain readable-width columns with local scrolling. Do not rewrite
content to work around a renderer defect. Test prose-heavy tables, not only short
status cells. Old frozen versions and custom HTML do not inherit new layout CSS.
Avoid gradients, heavy dashboard controls, or dense card grids by default.
Adapt the layout to the material. Preserve existing drafts unless the user requests an update.

## Update a draft

Update a draft only when the user requests a revision:

```sh
tailplan-share /path/to/artifact.md --draft <draft-id>
```

Use `--new` for all other requests.
Use the public mirror command only after the user approves public access.

## Automatic link cards

Share pages add Open Graph metadata and a programmatic PNG from the document title.
No image-generation step, browser renderer, or external font service is needed.
Existing drafts also support `/share`; fixed versions support `/v/N/share`.
Original document, `/raw`, and `/content` routes still return exact stored bytes.

The image and share page remain tailnet-private. Verify the page and image over
HTTPS, then distinguish those checks from the receiving app actually showing a card.
iMessage requires a preview fetcher with tailnet access. Do not enable public
access to make a card appear without the user's approval. Cached previews in
receiving apps can outlive a draft update or revocation.

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
