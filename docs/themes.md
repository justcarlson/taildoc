# Document themes

Tailplan separates content, layout, palette, and typography.
The server renders Markdown or plain text once, when you publish a version.
Each version stores complete HTML, inline CSS, and resolved theme metadata.
Later palette changes do not change stored versions.
Static HTML uploads keep their original bytes and styles.
Theme options on HTML uploads return an error.

## List and select

```sh
tailplan themes --json
tailplan upload plan.md --new
tailplan upload notes.md --new --theme flexoki-light
tailplan upload report.md --new --theme auto --document-type technical
tailplan-share report.md --new --theme nord --layout reading
```

The direct client and SSH client support the same theme options.
The server owns palette assets. Clients send content and selection options.
The direct client verifies the fixed published version against the server checksum.
`bin/tailplan-share` retains its old local HTML helpers for existing Python callers.
Normal CLI publication uses the server renderer.

`GET /api/themes` returns the validated registry, layouts, and selection policy.
The catalog needs no upload credential. Uploads retain their existing authentication requirements.
Send this JSON to `POST /api/uploads` with your configured bearer credential:

```json
{
  "content": "# Field notes\n\nA readable document.",
  "format": "markdown",
  "filename": "notes.md",
  "theme": "flexoki-light",
  "documentType": "notes",
  "layout": "reading"
}
```

Use `format: "text"` to preserve literal text and line breaks with prose typography.
Omitted options select Markdown, `auto`, `document`, and `reading`.
Use `draftId` to publish another version of an existing draft.
Send `html` alone for existing static HTML behavior.
Do not send both `html` and `content`.
Invalid themes and content options return HTTP 422 with an error message.
An invalid registry makes catalog requests return HTTP 503.

The upload response and version history include `theme` for rendered documents.
This object records the ID, version, tokens, source, document type, selection, and layout and palette checksums.
The stored HTML also contains this object in a `tailplan-theme` meta element.
No browser JavaScript, network fonts, or generated images are required for themes.

## Automatic selection

Selection uses the explicit document type. It does not guess from private text or use a model.
The policy is versioned in `tailplan_themes/selection.json`.
Explicit `--theme ID` overrides the policy.
Omitted types and unknown types use warm editorial.

| Document type | Theme |
| --- | --- |
| document, plan, report, essay | warm-editorial |
| notes | flexoki-light |
| reference | solarized-light |
| technical | tokyo-night |
| Any other valid type | warm-editorial |

## Built-in variants

This list describes supported variants. It is not a popularity ranking.
Each palette file pins the upstream source revision and license path.
The upstream links below identify the original projects.

| Stable ID | Variant | Upstream | License |
| --- | --- | --- | --- |
| warm-editorial | Original cream and olive | Tailplan | MIT |
| flexoki-light | Light | [Flexoki](https://github.com/kepano/flexoki) | MIT |
| catppuccin-latte | Latte | [Catppuccin palette](https://github.com/catppuccin/palette) | MIT |
| dracula | Classic | [Dracula](https://github.com/dracula/dracula-theme) | MIT |
| tokyo-night | Night | [Tokyo Night](https://github.com/folke/tokyonight.nvim) | Apache-2.0 |
| gruvbox-light | Light medium | [Gruvbox](https://github.com/morhetz/gruvbox) | MIT/X11 |
| nord | Polar Night | [Nord](https://github.com/nordtheme/nord) | MIT |
| solarized-light | Light | [Solarized](https://github.com/altercation/solarized) | MIT |

These are document adaptations of upstream colors.
All palettes share the reading layout.
Typography defaults to Georgia headings and system sans-serif body text.
Only code uses a monospace font.
The reading column is at most 690px, with 24px side padding and 17px body text at 1.55 line height.

Warm editorial uses cream `#faf6ee`, text `#26352e`, olive `#536447`, sage `#e9ecdf`, and border `#d8d7c9`.
Catppuccin uses mauve headings and its text color for links to maintain contrast on shaded blocks.
Dracula uses cyan links and foreground-colored secondary text.
Solarized uses base02 for body text, code, and links, with base01 for secondary text.
These choices preserve upstream colors while meeting the document contrast checks.
Print uses black text and a white background for every palette.

## Add a palette

1. Copy `tailplan_themes/palettes/warm-editorial.json` to a new JSON file.
2. Assign a unique stable `id` and a positive integer `version`.
3. Keep `schemaVersion` at `1`.
4. Set the name, variant, mode, source revision, and license information.
5. Set every semantic color token.
6. Retain the upstream license text under `tailplan_themes/licenses/`.
7. Run the checks below.
8. Install the updated server package through your deployment workflow.

The registry discovers every `palettes/*.json` file. No renderer edit is needed.
For administrator palettes outside the package, set `TAILPLAN_THEME_DIR` to a directory of JSON files.
This directory adds palettes. Duplicate IDs fail validation, including duplicates of built-in IDs.
Do not place credentials or private paths in palette metadata. The catalog exposes that metadata to viewers.

The schema is `tailplan_themes/schema.json`, using JSON Schema draft 2020-12.
Unknown fields, missing tokens, invalid colors, duplicate IDs, and unsupported versions fail validation.
Colors must use six-digit hexadecimal notation. Palette values cannot contain CSS expressions or external resources.
Increase the palette version when its values change. Keep the ID stable.

| Token | Use |
| --- | --- |
| background | Page background |
| text | Body and table text |
| heading | Headings |
| accent | Underlined links, focus outlines, quote border |
| surface | Table headers and quotes |
| border | Rules and table or code borders |
| muted | Source label |
| code-background | Inline and block code background |
| code-text | Inline and block code text |

`reading.css` owns layout, code typography, mobile rules, and print rules.
Typography presets supply heading and body font stacks.
Palette files contain no content or layout CSS.
The current layout ID is `reading`, version 2. Other layout IDs return an error.
The content parser supports escaped Markdown headings, paragraphs, links, lists, quotes, pipe tables, and fenced code.

## Validate

```sh
python -m pytest -q
python -c 'import tailplan_themes; tailplan_themes.catalog()'
```

The runtime validator enforces the schema and a minimum 4.5:1 contrast ratio for text roles.
Decorative borders do not carry text information and use the source palette border colors.
To run the browser tests, install Playwright and Chromium in a development environment:

```sh
python -m pip install playwright
python -m playwright install chromium
TAILPLAN_BROWSER_TESTS=1 python -m pytest -q tests/test_theme_browser.py
```

Set `TAILPLAN_CHROMIUM` to use an existing Chromium executable.
Browser tests check all palettes at 375px and 1280px, horizontal table and code scrolling, and print output.

## Typography presets

Typography is independent of the palette and document type.
Every palette supports both built-in presets:

| Stable ID | Headings | Body |
| --- | --- | --- |
| serif-sans | Georgia with serif fallbacks | System sans-serif |
| all-sans | System sans-serif | System sans-serif |

The default is `serif-sans`. This preserves the warm editorial appearance.
Code always uses the layout's monospace stack, including code inside headings.
System fonts require no downloads, images, or network requests.

```sh
tailplan typography --json
tailplan upload plan.md --new --typography serif-sans
tailplan-share notes.md --new --theme flexoki-light --typography all-sans
tailplan upload report.md --new --theme auto --document-type technical --typography all-sans
```

Direct, local, and SSH clients support these commands.
`GET /api/typography` returns the presets and `typographySelection` without upload credentials.
`GET /api/themes` includes the same typography fields.
For API publication, add `"typography": "all-sans"` to a Markdown or text upload.
Omitting `typography`, or sending `null`, uses the declared default.
Unknown IDs and invalid value types return HTTP 422.
Typography options on static HTML return HTTP 422. HTML without these options keeps its original bytes.
Missing or invalid registry assets make catalog and readiness checks fail.

Each version records the resolved preset under `theme.typography`.
The snapshot includes its ID, schema version, preset version, name, tokens, and canonical JSON SHA-256.
The HTML embeds the same snapshot and complete CSS.
Registry or default changes affect only later publications.
Existing HTML, historical versions, and legacy local HTML helpers remain unchanged.

### Add a typography preset

1. Copy `tailplan_themes/typography/serif-sans.json` to a new JSON file.
2. Assign a unique stable `id`, a name, and a positive integer `version`.
3. Keep `schemaVersion` at `1`.
4. Set both `font-heading` and `font-body` tokens.
5. Use comma-separated system font names, ending in `serif`, `sans-serif`, or `system-ui`.
6. Quote font names that contain spaces with double quotes.
7. Run the tests and install the updated server package.

The schema is `tailplan_themes/typography-schema.json`.
The registry discovers `typography/*.json` without renderer edits.
Use `TAILPLAN_TYPOGRAPHY_DIR` for additional administrator presets.
Duplicate IDs, unknown fields, invalid font syntax, and unsupported schema versions fail validation.
CSS declarations, URLs, escapes, and font downloads are prohibited.
Increment the preset version when its tokens change.
The versioned default policy is `tailplan_themes/typography-selection.json`.
Native installers, wheels, source archives, and containers include the preset assets.
Clients need no font assets.

Browser tests cover both presets with every palette at mobile and desktop sizes.
They verify heading, body, and code fonts, print output, and horizontal overflow.
