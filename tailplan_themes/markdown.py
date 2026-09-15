"""Deterministic, escaped Markdown content rendering."""
import html
import re
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

MAX_QUOTE_DEPTH = 16

def _safe_link_destination(destination: str) -> bool:
    if not destination or "\\" in destination or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in destination
    ):
        return False
    try:
        parsed = urllib.parse.urlsplit(destination)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc and hostname)


def _anchor(destination: str, label: str) -> str:
    href = html.escape(destination, quote=True)
    return (
        f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'
    )


def _scan_markdown_links(text: str) -> tuple[dict[int, tuple[int, int, int, int, int]], int]:
    """Index complete Markdown links with at most two character visits per input byte."""
    destination_closers: dict[int, int] = {}
    destination_stack: list[int] = []
    character_visits = 0
    for cursor, character in enumerate(text):
        character_visits += 1
        escaped = cursor > 0 and text[cursor - 1] == "\\"
        if character == "(" and not escaped:
            destination_stack.append(cursor)
        elif character == ")" and not escaped and destination_stack:
            destination_closers[destination_stack.pop()] = cursor

    links: dict[int, tuple[int, int, int, int, int]] = {}
    next_label_end: int | None = None
    for cursor in range(len(text) - 1, -1, -1):
        character_visits += 1
        character = text[cursor]
        escaped = cursor > 0 and text[cursor - 1] == "\\"
        if character == "]" and not escaped:
            next_label_end = cursor
        elif character == "[" and next_label_end is not None:
            destination_open = next_label_end + 1
            if destination_open < len(text) and text[destination_open] == "(":
                destination_end = destination_closers.get(destination_open)
                if destination_end is not None:
                    links[cursor] = (
                        destination_end + 1,
                        cursor + 1,
                        next_label_end,
                        destination_open + 1,
                        destination_end,
                    )
    return links, character_visits


def _bare_url_at(text: str, start: int) -> tuple[int, str, str] | None:
    if not (text.startswith("http://", start) or text.startswith("https://", start)):
        return None
    if start and (text[start - 1].isalnum() or text[start - 1] in "_@"):
        return None

    end = start
    while end < len(text) and not text[end].isspace() and text[end] not in '<>"\'`':
        end += 1
    candidate = text[start:end]
    url_end = len(candidate)
    opening_parentheses = candidate.count("(")
    closing_parentheses = candidate.count(")")
    while url_end:
        terminal = candidate[url_end - 1]
        if terminal in ".,;:!?":
            url_end -= 1
        elif terminal == ")" and closing_parentheses > opening_parentheses:
            closing_parentheses -= 1
            url_end -= 1
        else:
            break
    destination = candidate[:url_end]
    if not _safe_link_destination(destination):
        return None
    return end, destination, candidate[url_end:]


def _render_inline(text: str, *, allow_links: bool) -> str:
    rendered: list[str] = []
    markdown_links = _scan_markdown_links(text)[0] if allow_links else {}
    cursor = 0
    while cursor < len(text):
        if text[cursor] == "`":
            run_end = cursor
            while run_end < len(text) and text[run_end] == "`":
                run_end += 1
            delimiter = text[cursor:run_end]
            closer = text.find(delimiter, run_end)
            if closer >= 0:
                rendered.append(f"<code>{html.escape(text[run_end:closer])}</code>")
                cursor = closer + len(delimiter)
                continue

        if text[cursor] == "<":
            tag_end = text.find(">", cursor + 1)
            if tag_end < 0:
                rendered.append(html.escape(text[cursor:]))
                break
            rendered.append(html.escape(text[cursor : tag_end + 1]))
            cursor = tag_end + 1
            continue

        if allow_links and text[cursor] == "[":
            link = markdown_links.get(cursor)
            if link is not None:
                end, label_start, label_end, destination_start, destination_end = link
                label = text[label_start:label_end]
                destination = text[destination_start:destination_end]
                if _safe_link_destination(destination):
                    rendered.append(
                        _anchor(destination, _render_inline(label, allow_links=False))
                    )
                else:
                    rendered.append(html.escape(text[cursor:end]))
                cursor = end
                continue

        if allow_links:
            bare_url = _bare_url_at(text, cursor)
            if bare_url is not None:
                end, destination, suffix = bare_url
                rendered.append(_anchor(destination, html.escape(destination)))
                rendered.append(html.escape(suffix))
                cursor = end
                continue

        if text.startswith("**", cursor):
            closer = text.find("**", cursor + 2)
            if closer > cursor + 2:
                rendered.append(
                    f"<strong>{_render_inline(text[cursor + 2:closer], allow_links=allow_links)}</strong>"
                )
                cursor = closer + 2
                continue

        if text[cursor] == "*" and not text.startswith("**", cursor):
            closer = text.find("*", cursor + 1)
            if closer > cursor + 1:
                rendered.append(
                    f"<em>{_render_inline(text[cursor + 1:closer], allow_links=allow_links)}</em>"
                )
                cursor = closer + 1
                continue

        rendered.append(html.escape(text[cursor]))
        cursor += 1
    return "".join(rendered)


def inline(text: str) -> str:
    """Render a safe, deterministic subset of CommonMark inline syntax."""
    return _render_inline(text, allow_links=True)


def split_table_row(line: str) -> list[str]:
    """Split a GitHub-flavored Markdown table row.

    Supports leading/trailing pipes and escaped literal pipes.
    """
    text = line.strip().removeprefix("|").removesuffix("|")
    cells: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in text:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if escaped:
        buf.append("\\")
    cells.append("".join(buf).strip())
    return cells


def parse_table_separator(line: str) -> list[str] | None:
    cells = split_table_row(line)
    if not cells:
        return None
    aligns: list[str] = []
    for cell in cells:
        compact = cell.replace(" ", "")
        if not re.fullmatch(r":?-{3,}:?", compact):
            return None
        if compact.startswith(":") and compact.endswith(":"):
            aligns.append("center")
        elif compact.endswith(":"):
            aligns.append("right")
        elif compact.startswith(":"):
            aligns.append("left")
        else:
            aligns.append("")
    return aligns


def render_table(headers: list[str], aligns: list[str], rows: list[list[str]]) -> str:
    col_count = len(headers)

    def cell_attr(index: int) -> str:
        if index < len(aligns) and aligns[index]:
            return f' style="text-align: {aligns[index]}"'
        return ""

    def normalize(row: list[str]) -> list[str]:
        return (row + [""] * col_count)[:col_count]

    class HeaderText(HTMLParser):
        """Copy visible header text, never duplicate interactive markup."""
        def __init__(self, markup):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self.feed(markup)

        def handle_data(self, data):
            self.parts.append(data)

    rendered_headers = [inline(value) for value in normalize(headers)]
    labels = [html.escape(''.join(HeaderText(value).parts).strip() or f'Column {i + 1}')
              for i, value in enumerate(rendered_headers)]
    head = "".join(
        f'<th role="columnheader" scope="col"{cell_attr(i)}>{value}</th>'
        for i, value in enumerate(rendered_headers)
    )
    body_rows = []
    for row in rows:
        cells = "".join(
            f'<td role="cell"{cell_attr(i)}>'
            f'<span class="table-cell-label" aria-hidden="true">{labels[i]}</span>'
            f'<span class="table-cell-value">{inline(value)}</span></td>'
            for i, value in enumerate(normalize(row))
        )
        body_rows.append(f'<tr role="row">{cells}</tr>')
    body = '\n<tbody role="rowgroup">\n' + "\n".join(body_rows) + "\n</tbody>" if body_rows else ""
    return (f'<div class="table-wrap"><table role="table">\n'
            f'<thead role="rowgroup"><tr role="row">{head}</tr></thead>{body}\n</table></div>')


def _line_text(raw_line: str) -> str:
    return raw_line.removesuffix("\n").removesuffix("\r")


def _fence_opening(line: str) -> tuple[str, int, str] | None:
    match = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^\r\n]*)", line)
    if match is None or (match.group(1)[0] == "`" and "`" in match.group(2)):
        return None
    info = match.group(2).strip()
    language = info.split(maxsplit=1)[0] if info else ""
    if language and re.fullmatch(r"[A-Za-z0-9_+-]+", language) is None:
        language = ""
    return match.group(1)[0], len(match.group(1)), language


def _fence_closes(line: str, marker: str, minimum_length: int) -> bool:
    match = re.fullmatch(rf" {{0,3}}({re.escape(marker)}{{{minimum_length},}})[ \t]*", line)
    return match is not None


def _heading(line: str) -> tuple[int, str] | None:
    match = re.fullmatch(r" {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)", line)
    if match is None:
        return None
    content = (match.group(2) or "").strip()
    content = re.sub(r"[ \t]+#+[ \t]*$", "", content)
    return len(match.group(1)), content


def _is_horizontal_rule(line: str) -> bool:
    compact = line.strip().replace(" ", "").replace("\t", "")
    return len(compact) >= 3 and compact[0] in "*_-" and set(compact) == {compact[0]}


def _list_item(line: str) -> tuple[str, str] | None:
    unordered = re.fullmatch(r" {0,3}[-+*][ \t]+(.+)", line)
    if unordered is not None:
        return "ul", unordered.group(1)
    ordered = re.fullmatch(r" {0,3}\d{1,9}[.)][ \t]+(.+)", line)
    if ordered is not None:
        return "ol", ordered.group(1)
    return None


def _is_blockquote(line: str) -> bool:
    return re.match(r"^ {0,3}>", line) is not None


def _starts_table(lines: list[str], index: int) -> tuple[list[str], list[str]] | None:
    if index + 1 >= len(lines) or "|" not in lines[index]:
        return None
    aligns = parse_table_separator(lines[index + 1])
    if aligns is None:
        return None
    headers = split_table_row(lines[index])
    if len(headers) != len(aligns):
        return None
    return headers, aligns


def markdown_to_body(markdown: str, *, _quote_depth: int = 0) -> str:
    """Render a safe block-level subset of GitHub/CommonMark Markdown."""
    raw_lines = markdown.splitlines(keepends=True)
    lines = [_line_text(raw_line) for raw_line in raw_lines]
    out: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        fence = _fence_opening(line)
        if fence is not None:
            marker, minimum_length, language = fence
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not _fence_closes(
                lines[index], marker, minimum_length
            ):
                code_lines.append(raw_lines[index])
                index += 1
            if index < len(lines):
                index += 1
            class_attribute = (
                f' class="language-{html.escape(language, quote=True)}"' if language else ""
            )
            out.append(
                f"<pre><code{class_attribute}>"
                + html.escape("".join(code_lines))
                + "</code></pre>"
            )
            continue

        table = _starts_table(lines, index)
        if table is not None:
            headers, aligns = table
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                rows.append(split_table_row(lines[index]))
                index += 1
            out.append(render_table(headers, aligns, rows))
            continue

        heading = _heading(line)
        if heading is not None:
            level, content = heading
            out.append(f"<h{level}>{inline(content)}</h{level}>")
            index += 1
            continue

        if _is_horizontal_rule(line):
            out.append("<hr>")
            index += 1
            continue

        item = _list_item(line)
        if item is not None:
            list_type = item[0]
            items: list[str] = []
            while index < len(lines):
                candidate = _list_item(lines[index])
                if candidate is None or candidate[0] != list_type:
                    break
                items.append(candidate[1])
                index += 1
            rendered_items = "\n".join(f"<li>{inline(value)}</li>" for value in items)
            out.append(f"<{list_type}>\n{rendered_items}\n</{list_type}>")
            continue

        if _quote_depth < MAX_QUOTE_DEPTH and _is_blockquote(line):
            quoted: list[str] = []
            while index < len(lines) and _is_blockquote(lines[index]):
                quoted.append(re.sub(r"^ {0,3}>[ \t]?", "", lines[index]))
                index += 1
            quote_body = markdown_to_body("\n".join(quoted), _quote_depth=_quote_depth + 1)
            out.append(f"<blockquote>\n{quote_body}\n</blockquote>")
            continue

        paragraph: list[str] = []
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip() or paragraph and (
                _fence_opening(candidate) is not None
                or _starts_table(lines, index) is not None
                or _heading(candidate) is not None
                or _is_horizontal_rule(candidate)
                or _list_item(candidate) is not None
                or _is_blockquote(candidate)
            ):
                break
            paragraph.append(candidate.strip())
            index += 1
        out.append(f"<p>{inline(' '.join(paragraph))}</p>")

    return "\n".join(out)


def title_from_content(path: Path, text: str) -> str:
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+)", line.strip())
        if m:
            return m.group(1).strip()[:100]
    return path.stem.replace("-", " ").replace("_", " ").strip().title() or "Tailplan Draft"
