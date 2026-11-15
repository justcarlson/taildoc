# Briefs

A brief is a short, scannable report about one source.
The source can be a video, podcast, talk, article, paper, book, meeting, or thread.
Write the brief in Markdown. Publish it with the `brief` layout:

```sh
tailplan-share episode.md --new --layout brief
```

The layout adds a header, a contents list, numbered sections, callouts, and claim checks.
Timed sources also get a colored run-of-show bar and links that open the source at each section.
The layout uses the selected palette and typography. Light and dark palettes both work.
No JavaScript, external fonts, or images are necessary.

## Example

```markdown
---
kicker: Example Podcast #12 · 14 November 2026
dek: One sentence that tells the reader why this episode matters.
source: https://www.youtube.com/watch?v=VIDEO_ID
kind: video
length: 1:12:40
people: Host One, Guest Two
topics: news=News, guest=Interview
note: Times come from auto-captions and are approximate.
---

# Episode title

> [!summary] The short version
> - First main point.
> - Second main point.

## Cold open {at=0:00 topic=news}

- What happened in this part.

## Interview {at=14:30 topic=guest}

> [!notes] Panel notes
> **Host One:** A short comment.

## Claims checked

| Verdict | Claim | Detail |
| --- | --- | --- |
| True | A claim from the episode | The source that confirms it. |
| Partly | Another claim | What is different. |
```

The repository fixture `tests/fixtures/brief-example.md` is a full example.

## Front matter

Front matter is optional. It starts and ends with a `---` line.
Each line is `key: value`. Unknown keys and duplicate keys return an error.

| Key | Use |
| --- | --- |
| kicker | Small text above the title |
| dek | One-sentence summary below the title |
| source | Absolute http or https URL of the source |
| source-label | Button text. The default comes from `kind`. |
| kind | video, talk, audio, article, paper, book, meeting, or other |
| length | Total length as M:SS or H:MM:SS |
| date | Date chip |
| people | Comma-separated names. Each name becomes a chip. |
| topics | Comma-separated `id=Label` pairs, at most 8 |
| note | Fine print below the header |

## Sections

The first `#` heading is the title.
Each `##` heading starts a section. Text before the first section stays in the header area.
Add attributes in braces at the end of a heading:

- `at=1:02:30` sets the start time. Times must increase from section to section.
- `at="p. 12"` sets a text locator, for example a page number. Quote values that contain spaces.
- `topic=news` sets the section color from the `topics` list.

A section with a locator gets a number and a locator chip.
A time links to that point in the source. YouTube links use the `t` query parameter.
Other sources use a `#t=` fragment.

The contents list shows when the brief has two or more sections.
The run-of-show bar needs `length` and two or more timed sections.
Bar widths show how long each section runs. The axis labels stay at least 64px apart on a phone.

## Callouts

A quote that starts with `[!kind] Title` becomes a callout.
The kinds are `summary`, `note`, `notes`, `tip`, and `warning`.
In a callout, a paragraph that starts with bold text shows that text as a name pill.

## Claim checks

A table with a `Verdict` column becomes a list of claims with colored badges.
The next column is the claim. Other columns become detail text.

| Verdict words | Badge |
| --- | --- |
| true, yes, supported, checks out, confirmed, correct | green |
| partly, partly true, partial, mixed, misleading, likely, probably, disputed | amber |
| unverified, unconfirmed, unclear, unknown, not checked, open | gray |
| false, no, off, wrong, incorrect, debunked | red |
| Any other word | neutral |

Tables without a `Verdict` column stay normal tables.
The reading layout does not change callouts or claim tables.
