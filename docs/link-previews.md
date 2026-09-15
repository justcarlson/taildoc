# Automatic link previews

Taildoc renders a 1200 x 630 PNG from each document title. The card uses a bold
monospace font, an offset border, and the configured server hostname. It needs
no image service, browser renderer, network font, or extra runtime package.

`tailplan-share` and `tailplan upload` print a preview-ready URL by default.
JSON responses retain `publicUrl` and add `shareUrl`. Clients that need exact
uploaded bytes can keep using `publicUrl`, `rawUrl`, or `/content`.

| Route | Response |
| --- | --- |
| `/d/ID/share` | Latest document with Open Graph and Twitter card metadata |
| `/d/ID/v/N/share` | Version N with its own title and image URL |
| `/d/ID/preview.png` | Latest title card |
| `/d/ID/v/N/preview.png` | Version N title card |
| `/d/ID`, `/d/ID/raw`, `/d/ID/content` | Exact stored document bytes, unchanged |

The routes also work for drafts uploaded before this change. No migration or
republishing is needed. The configured base URL supplies all metadata URLs.
The request Host header cannot change them. Share pages replace conflicting
Open Graph and Twitter tags without changing the stored HTML.

## Private access

Share pages and images use the same network boundary and draft lifecycle checks
as document URLs. Disabling or deleting a draft blocks its image routes, even
if the renderer has cached that title. The server checks storage integrity before
it returns a cached PNG. The renderer keeps at most 32 images in memory.
Image responses use `Cache-Control: private, no-store`.

A receiving app must reach the tailnet to fetch a preview. A successful HTTPS
page and image check does not prove that iMessage shows a card. Some clients use
remote preview services or keep old previews. Taildoc does not enable public
access to work around those limits. A client may retain an image after the draft
changes or is disabled.

## Font and layout

The embedded Taildoc Preview Atlas comes from DejaVu Sans Mono Bold 2.37.
The font license is in `THIRD_PARTY_NOTICES.md` and the installed server source.
The card supports ASCII and Latin-1 glyphs. It normalizes common smart punctuation.
Other characters appear as `?` in the PNG; HTML metadata retains the Unicode title.
Long titles wrap and shrink within a fixed title area. Excess text gets an ellipsis.

To rebuild the atlas for development, use Pillow 12.3.0 and run:

```sh
python scripts/build_preview_font.py /path/to/DejaVuSansMono-Bold.ttf
```

The command prints the Python constant for `tailplan_server.py`. Pillow is not a
server dependency. The renderer builds and compresses the PNG with Python's
standard library.

## Verify

```sh
python -m pytest -q tests/test_preview.py
python -m pytest -q -rs
bash tests/smoke.sh
```

Check both GET and HEAD responses, fixed-version titles, disabled drafts,
conflicting metadata, content integrity, and the original byte-preserving routes.
Then view a new share link in the receiving messaging app.
