"""Brief layout: one page that outlines a source, such as a video, a talk, or a paper."""
from __future__ import annotations

import html
import re
import urllib.parse

from .markdown import (_fence_closes, _fence_opening, _heading, _plain_text,
                       _safe_link_destination, inline, markdown_to_body)

MAX_FRONT_MATTER_LINES = 64
MAX_FIELD_LENGTH = 2000
TOPIC_SLOTS = 8
SOURCE_LABELS = {"video": "Watch", "talk": "Watch", "audio": "Listen", "article": "Read",
                 "paper": "Read", "book": "Read", "meeting": "Open", "other": "Open"}
FRONT_MATTER_KEYS = {"kicker", "dek", "source", "source-label", "kind", "length", "date",
                     "people", "topics", "note"}
HEADING_ATTRIBUTES = {"at", "topic"}
# Axis labels must stay readable on a 360 px phone with a 16 px gutter on each side.
AXIS_WIDTH_PX = 328
AXIS_MIN_GAP_PX = 64
AXIS_STEPS = (60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 10800, 21600, 43200, 86400)


class BriefError(ValueError):
    """The brief source is not valid."""


def plain(markdown_text: str) -> str:
    """Return the visible text of inline Markdown, escaped for an HTML attribute or element."""
    return html.escape(_plain_text(inline(markdown_text)), quote=True)


def parse_time(value: str) -> int | None:
    """Parse M:SS or H:MM:SS. Return seconds, or None for a locator that is not a time."""
    match = re.fullmatch(r"(\d{1,3}):([0-5]\d)(?::([0-5]\d))?", value.strip())
    if match is None:
        return None
    first, second, third = match.groups()
    if third is None:
        return int(first) * 60 + int(second)
    return int(first) * 3600 + int(second) * 60 + int(third)


def clock(seconds: int, long_form: bool) -> str:
    if long_form:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def stamp(seconds: int, long_form: bool) -> str:
    """Section time. Seconds stay visible unless the time is a whole minute."""
    if long_form and seconds % 60:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return clock(seconds, long_form)


def duration_text(seconds: int) -> str:
    hours, minutes = seconds // 3600, round(seconds % 3600 / 60)
    if minutes == 60:
        hours, minutes = hours + 1, 0
    if hours and minutes:
        return f"{hours} h {minutes} min"
    return f"{hours} h" if hours else f"{max(minutes, 1)} min"


def deep_link(source: str, seconds: int) -> str:
    parts = urllib.parse.urlsplit(source)
    host = (parts.hostname or "").lower()
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                 if k != "t"]
        query.append(("t", f"{seconds}s"))
        return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    return urllib.parse.urlunsplit(parts._replace(fragment=f"t={seconds}"))


def split_front_matter(content: str) -> tuple[dict[str, str], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content
    for end in range(1, min(len(lines), MAX_FRONT_MATTER_LINES + 2)):
        if lines[end].strip() == "---":
            break
    else:
        raise BriefError("Front matter has no closing --- line within 64 lines")
    fields: dict[str, str] = {}
    for number, line in enumerate(lines[1:end], start=2):
        if not line.strip():
            continue
        match = re.fullmatch(r"([a-z][a-z-]*):[ \t]*(.*)", line.strip())
        if match is None:
            raise BriefError(f"Front matter line {number} must be key: value")
        key, value = match.group(1), match.group(2).strip()
        if key not in FRONT_MATTER_KEYS:
            raise BriefError(f"Unknown front matter key: {key}. Keys: "
                             + ", ".join(sorted(FRONT_MATTER_KEYS)))
        if key in fields:
            raise BriefError(f"Duplicate front matter key: {key}")
        if len(value) > MAX_FIELD_LENGTH:
            raise BriefError(f"Front matter value for {key} is too long")
        fields[key] = value
    return fields, "\n".join(lines[end + 1:])


def parse_topics(value: str) -> dict[str, tuple[int, str]]:
    topics: dict[str, tuple[int, str]] = {}
    for item in filter(None, (part.strip() for part in value.split(","))):
        match = re.fullmatch(r"([a-z][a-z0-9-]{0,31})\s*=\s*(.+)", item)
        if match is None:
            raise BriefError("Each topic must be id=Label, for example pol=Politics")
        ident, label = match.groups()
        if ident in topics:
            raise BriefError(f"Duplicate topic: {ident}")
        if len(topics) == TOPIC_SLOTS:
            raise BriefError(f"Use no more than {TOPIC_SLOTS} topics")
        topics[ident] = (len(topics) + 1, label.strip())
    return topics


def parse_heading(text: str) -> tuple[str, dict[str, str]]:
    match = re.fullmatch(r"(.*?)[ \t]*\{([^{}]*)\}[ \t]*", text)
    if match is None:
        return text, {}
    title, raw = match.groups()
    attributes: dict[str, str] = {}
    position = 0
    token = re.compile(r'\s*([a-z]+)=(?:"([^"]*)"|([^\s"]+))\s*')
    while position < len(raw):
        found = token.match(raw, position)
        if found is None or found.end() == position:
            raise BriefError(f'Heading attributes must be key=value or key="value": {text}')
        key = found.group(1)
        if key not in HEADING_ATTRIBUTES:
            raise BriefError(f"Unknown heading attribute: {key}. Use at or topic")
        attributes[key] = found.group(2) if found.group(2) is not None else found.group(3)
        position = found.end()
    return title, attributes


def split_sections(body: str) -> tuple[str | None, list[str], list[tuple[str, list[str]]]]:
    title = None
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    fence = None
    for line in body.splitlines():
        target = sections[-1][1] if sections else preamble
        if fence is not None:
            if _fence_closes(line, *fence):
                fence = None
            target.append(line)
            continue
        opening = _fence_opening(line)
        if opening is not None:
            fence = opening[:2]
            target.append(line)
            continue
        heading = _heading(line)
        if heading is not None and heading[0] == 1 and title is None and not sections:
            title = heading[1]
        elif heading is not None and heading[0] == 2:
            sections.append((heading[1], []))
        else:
            target.append(line)
    return title, preamble, sections


def _topic_color(topic: str | None, topics: dict[str, tuple[int, str]], heading: str) -> str:
    """Return a CSS declaration that sets the topic color, or an empty string."""
    if topic is None:
        return ""
    if topic not in topics:
        raise BriefError(f"Unknown topic {topic} in heading {heading}. Define it in topics")
    return f"--k: var(--k{topics[topic][0]}); "


def axis_ticks(total: int) -> list[tuple[float, str]]:
    long_form = total >= 3600
    step = next((s for s in AXIS_STEPS if total / s <= AXIS_WIDTH_PX / AXIS_MIN_GAP_PX - 1),
                AXIS_STEPS[-1])
    minimum_tail = total * AXIS_MIN_GAP_PX / AXIS_WIDTH_PX
    ticks = []
    for at in range(0, total, step):
        if at and total - at < minimum_tail:
            break
        label = clock(at, True) if long_form else str(at // 60)
        ticks.append((round(at / total * 100, 1), label))
    end = (clock(total, True) + " h") if long_form else f"{round(total / 60)} min"
    ticks.append((100.0, end))
    return ticks


def brief_to_body(content: str) -> tuple[str, str | None]:
    """Render brief Markdown. Return the body HTML and the document title."""
    fields, body = split_front_matter(content)
    title, preamble, raw_sections = split_sections(body)
    topics = parse_topics(fields.get("topics", ""))

    kind = fields.get("kind", "other")
    if kind not in SOURCE_LABELS:
        raise BriefError("Unknown kind. Kinds: " + ", ".join(sorted(SOURCE_LABELS)))
    source = fields.get("source")
    if source is not None and not _safe_link_destination(source):
        raise BriefError("Source must be an absolute http or https URL")
    length = None
    if "length" in fields:
        length = parse_time(fields["length"])
        if length is None or length <= 0:
            raise BriefError("Length must be M:SS or H:MM:SS")

    sections = []
    for heading, lines in raw_sections:
        text, attributes = parse_heading(heading)
        locator = attributes.get("at")
        seconds = parse_time(locator) if locator else None
        sections.append({"heading": text, "lines": lines, "locator": locator,
                         "seconds": seconds,
                         "color": _topic_color(attributes.get("topic"), topics, text),
                         "topic": attributes.get("topic")})
    timed = [section for section in sections if section["seconds"] is not None]
    starts = [section["seconds"] for section in timed]
    if any(later <= earlier for earlier, later in zip(starts, starts[1:])):
        raise BriefError("Section times must increase from one section to the next")
    if length is not None and starts and starts[-1] >= length:
        raise BriefError("Section times must be less than length")
    long_form = (length or (starts[-1] if starts else 0)) >= 3600
    for index, section in enumerate(timed):
        section["end"] = starts[index + 1] if index + 1 < len(timed) else length

    out = ['<header class="brief-head">']
    if "kicker" in fields:
        out.append(f'<p class="kicker">{inline(fields["kicker"])}</p>')
    out.append(f"<h1>{inline(title)}</h1>" if title else "")
    if "dek" in fields:
        out.append(f'<p class="dek">{inline(fields["dek"])}</p>')
    facts = []
    if source is not None:
        label = fields.get("source-label") or SOURCE_LABELS[kind]
        facts.append(f'<a class="source" href="{html.escape(source, quote=True)}" '
                     f'target="_blank" rel="noopener noreferrer">{plain(label)} ↗</a>')
    if length is not None:
        facts.append(f'<span class="chip">{duration_text(length)}</span>')
    if "date" in fields:
        facts.append(f'<span class="chip">{plain(fields["date"])}</span>')
    for person in filter(None, (p.strip() for p in fields.get("people", "").split(","))):
        facts.append(f'<span class="chip">{plain(person)}</span>')
    if facts:
        out.append('<div class="facts">' + "".join(facts) + "</div>")
    if "note" in fields:
        out.append(f'<p class="fine">{inline(fields["note"])}</p>')
    out.append("</header>")

    intro = markdown_to_body("\n".join(preamble), brief=True)
    if intro:
        out.append(intro)

    for number, section in enumerate(sections, start=1):
        section["id"] = f"s{number}"
    if len(sections) >= 2:
        out.append(run_of_show(sections, timed, topics, length, long_form))

    count = 0
    for section in sections:
        head = []
        if section["locator"] is not None:
            count += 1
            head.append(f'<span class="n">{count:02d}</span>')
        head.append(f'<h2>{inline(section["heading"])}</h2>')
        if section["seconds"] is not None:
            span = stamp(section["seconds"], long_form)
            if section["end"] is not None:
                span += "–" + stamp(section["end"], long_form)
            if source is not None:
                href = html.escape(deep_link(source, section["seconds"]), quote=True)
                head.append(f'<a class="time" href="{href}" target="_blank" '
                            f'rel="noopener noreferrer" title="Open the source at '
                            f'{stamp(section["seconds"], long_form)}">{span}</a>')
            else:
                head.append(f'<span class="time">{span}</span>')
        elif section["locator"] is not None:
            head.append(f'<span class="time">{plain(section["locator"])}</span>')
        style = f' style="{section["color"].strip()}"' if section["color"] else ""
        out.append(f'<section class="seg" id="{section["id"]}"{style}>'
                   f'<div class="seg-head">{"".join(head)}</div>'
                   + markdown_to_body("\n".join(section["lines"]), brief=True) + "</section>")
    return "\n".join(out), title


def run_of_show(sections, timed, topics, length, long_form) -> str:
    out = ['<nav class="ros" aria-label="Contents">', "<h2>Contents</h2>"]
    if length is not None and len(timed) >= 2:
        out.append('<p class="sub">Bar widths show how long each part runs. '
                   'Select a part to go to it.</p><div class="bar">')
        if timed[0]["seconds"] > 0:
            out.append(f'<span style="flex: {timed[0]["seconds"]}"></span>')
        for section in timed:
            label = f'{plain(section["heading"])}, {stamp(section["seconds"], long_form)}'
            out.append(f'<a href="#{section["id"]}" title="{label}" aria-label="{label}"'
                       f' style="{section["color"]}flex: {section["end"] - section["seconds"]}"></a>')
        out.append("</div>")
        ticks = axis_ticks(length)
        out.append('<div class="axis" aria-hidden="true">' + "".join(
            f'<span style="left: {left}%">{label}</span>' for left, label in ticks) + "</div>")
        used = [ident for ident in topics if any(s["topic"] == ident for s in sections)]
        if used:
            out.append('<div class="legend">' + "".join(
                f'<span style="--k: var(--k{topics[ident][0]})">{plain(topics[ident][1])}</span>'
                for ident in used) + "</div>")
    out.append('<ol class="toc">')
    for section in sections:
        if section["seconds"] is not None:
            label_time = stamp(section["seconds"], long_form)
        else:
            label_time = plain(section["locator"] or "")
        style = f' style="{section["color"].strip()}"' if section["color"] else ""
        out.append(f'<li><a href="#{section["id"]}"{style}><span class="ts">{label_time}'
                   f'</span><span class="t">{plain(section["heading"])}</span></a></li>')
    out.append("</ol></nav>")
    return "".join(out)
