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
3. Verify numbers and claims against live data or primary sources before writing.
4. Create one standalone source file in static HTML, Markdown, or plain text format without active content.
   Prefer static HTML for plans, comparisons, and decision reports.
   Use Markdown for quick notes or the `brief` layout.
5. Use a temporary source file unless the user requests a durable repository document.
6. Remove credentials, secret values, access URLs, and sensitive personal data before publication.
   Do not over-censor ordinary private context on a tailnet-only draft.
7. Publish a new draft, or update an existing draft in place with `--draft <draft-id>`:

   ```sh
   tailplan-share /path/to/artifact.html --new
   ```

8. Copy the `URL:` value from the command output. It uses the automatic preview
   page at `/d/ID/share` when the server supports it. For JSON output, prefer
   `shareUrl` and fall back to `publicUrl`.
9. Verify the URL with `curl -fsS` from a tailnet device.
10. Open the published page at 390px, 768px, and 1280px with the `agent-browser` UAT tool when publishing custom HTML or layout changes.
11. Return the URL and one short description.
12. If step 5 created a durable repository document, report its repository path.

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
Layouts are `reading` (the default) and `brief`. Select one with `--layout ID`.
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

### Hand-written HTML defaults

When you write the page's CSS yourself, start from these tokens. They match the
`serif-sans` preset and the warm editorial palette.

```css
--serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
--sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
--mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
body { font: 16px/1.6 var(--sans); }
h1, h2, h3 { font-family: var(--serif); font-weight: 600; line-height: 1.2; }
h1 { font-size: clamp(30px, 7vw, 44px); }
h2 { font-size: 22px; }  /* no underline rule under h2 */
```

- Never put Georgia first in the stack, and do not use bold (700) serif headings.
- Put an optional dek (a one-sentence summary) under the h1, in `var(--serif)`, 19px, in the muted color.
- Put an optional kicker (13px uppercase muted text, `letter-spacing: .06em`) above the h1.
- Use small rounded chips for metadata and a surface card for the summary.
  Do not use bordered link boxes or blocks with a heavy left rule.
- When the active model is not the strongest visual designer available, delegate rich HTML authoring and layout refinement to a design subagent.
- Lead with a verdict or summary card.
  Keep default visible prose concise, around 800 to 1,000 words.
  Place deep technical specs, logs, and raw evidence inside `<details>` blocks.
- Prefer inline SVG charts, diagrams, and embedded screenshots over dense data tables.
- Link each chart mark or label to its source in a new tab.
  Show the source title on hover.
  Fit charts inside a 360px phone, keep axis ticks at least 44px apart, and put the unit on the last tick only.
- Write in plain adult technical terms.
  Link factual claims to primary evidence.
  Avoid emojis and patronizing metaphors.
- Pass a phone glance check at 390px, not only zero horizontal scroll.
  Verify readable typography and generous spacing at 390px, 768px, and 1280px.

The server stores resolved HTML and theme metadata in each published version.
Palette updates do not change stored documents.
Plain `.txt` files preserve literal markup and line breaks in the reading layout.
Arbitrary static HTML keeps its original styles. Do not pass theme options with HTML files.
Use HTML when the document requires a custom layout beyond the reading layout.
Do not claim unsupported theme flags or a popularity ranking.
For custom HTML, retain the warm cream and olive palette unless the user requests another style.
Custom HTML must work on a phone.
Include `<meta name="viewport" content="width=device-width, initial-scale=1">`.
Keep the page inside a 320px screen with no sideways scroll.
Stack wide tables and grids into one column below 640px.
Check the published page at 390px before you share it.
Also check 768px and 1280px. Keep prose near 690px wide on large screens.
Let wide tables and grids use more width on tablets and desktops.
Tailplan warns when an HTML upload has no viewport meta tag.
Use readable full-width prose on phones with modest padding. Generated Markdown
tables automatically stack into labeled rows on screens up to 640px. Larger
screens show columns at least 10rem wide, and tables widen past the text column
up to 1104px. A table wider than that scrolls in its own box. Do not rewrite
content to work around a renderer defect. Test prose-heavy tables, not only short
status cells. Old frozen versions and custom HTML do not inherit new layout CSS.
Avoid gradients, heavy dashboard controls, or dense card grids by default.
Adapt the layout to the material. Preserve existing drafts unless the user requests an update.

## Briefs

Use the `brief` layout for a report on one source.
The source can be a video, podcast, talk, article, paper, book, meeting, or thread.
Read `docs/briefs.md` in the repository for the full format.

1. Get the source text. For video or audio, download captions only:

   ```sh
   yt-dlp --skip-download --write-auto-subs --sub-langs 'en.*' --sub-format vtt -o src URL
   python3 - src.en.vtt > transcript.txt <<'PY'
   import html, re, sys
   stamp, seen = None, []
   for raw in open(sys.argv[1], encoding="utf-8"):
       cue = re.match(r"(\d+):(\d\d):(\d\d)\.\d+\s+-->", raw)
       if cue:
           stamp = "[%d:%s:%s]" % (int(cue[1]), cue[2], cue[3])
           continue
       line = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
       if stamp and line and line not in seen[-3:]:
           seen.append(line)
           print(stamp, line)
   PY
   ```

   This removes repeated auto-caption lines and keeps `[H:MM:SS]` times.
2. Outline the source into sections. Use the real start time of each section.
3. Check important claims against outside sources. Run this research in a subagent when one is available.
4. Write the brief in Markdown with front matter, `## Section {at=M:SS topic=id}` headings, and callouts.
   Put claim checks in a table with a `Verdict` column.
5. Scan the Markdown for secrets, private hostnames, and personal data. Remove them.
6. Publish and return the URL:

   ```sh
   tailplan-share brief.md --new --layout brief
   ```

7. Open the URL at 390px wide in light and dark palettes. Check that nothing scrolls sideways.

Mark caption times as approximate in the `note` field.
Do not invent times, quotes, or verdicts. Use `Unverified` when a check is not complete.

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

Use `tailplan check [FILE_OR_DIR] [--slug NAME]` to validate markup, sandbox limits, and slug availability before upload.
It falls back to local markup checks when the server is unreachable.
Use `--slug NAME` for memorable, portable URLs across machines.
Upload a folder of `.html` pages with `index.html` to publish a multi-page draft.
Use `tailplan download REF` to fetch the current source without overwriting existing local files.
Use `tailplan favorite REF` and `tailplan unfavorite REF` to pin drafts at the top of the dashboard and list.
Re-uploading identical content keeps the current version and returns `unchanged`.

Use `tailplan list --json` to find drafts and `tailplan history REF --json` to inspect their versions.
Use `tailplan upload FILE --description TEXT` to attach a stable description.
Open `/dashboard` under the configured base URL for favorites, repository groups, and version history.
Open `/cli/auth` to generate or revoke named API keys.
The direct client accepts those keys through `tailplan auth login`.
The SSH client uses the existing SSH identity.

Use `tailplan disable REF`, `tailplan enable REF`, or `tailplan delete REF` for requested lifecycle changes.
Verify the resulting state with the draft list and viewer URL.
Authenticated HTML drafts may run inline JavaScript, including `type="module"` and `on*` handlers, inside an opaque sandbox.
The sandbox blocks network calls, browser storage, cookies, dialogs, workers, forms, and embeds.
Keep state in memory or `location.hash`, and inline data in `<script type="application/json">` blocks.
Script-free drafts and Markdown pages remain under `script-src 'none'`.
