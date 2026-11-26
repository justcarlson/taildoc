"""Static checks for Tailplan pages. Each finding is a warning, never an error.

lint(html) returns a list of dicts with code, message, and optional fix and line.
The scan is text based. It first removes the Tailplan UI style block, so the
bundled CSS never trips a check.
"""
from __future__ import annotations

import difflib
import re

from .bundler import is_opted_in, strip_stamp
from .registry import known_slots

COMMENT_RE = re.compile(r"<!--.*?-->|/\*.*?\*/", re.S)
STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.S | re.I)
INLINE_STYLE_RE = re.compile(r"<([a-zA-Z][\w-]*)\b[^>]*?\sstyle\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.S)
SLOT_RE = re.compile(r"\bdata-slot\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))", re.I)
RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
MEDIA_RE = re.compile(r"@media([^{]*)\{", re.I)
BAD_WIDTH_RE = re.compile(r"(?<![\d.])(?:600|640)(?:\.0+)?px\b")
UI_MODE_RE = re.compile(r"<html\b[^>]*\bdata-ui-mode\s*=\s*[\"']?(?:light|dark)\b", re.I)
DARK_RE = re.compile(r"prefers-color-scheme|color-scheme\s*:", re.I)
NOWRAP_RE = re.compile(r"white-space\s*:\s*nowrap", re.I)
ELLIPSIS_RE = re.compile(r"text-overflow\s*:\s*ellipsis", re.I)
PROSE_SEL_RE = re.compile(
    r"(?<![\w-])(?:p|h[1-6]|td|th|li|dd|dt|blockquote|figcaption|summary|article)(?![\w-])"
    r"|prose|heading|title|\bcell\b|-cell|-body|-text|-description|-reason", re.I)
CONTROL_SEL_RE = re.compile(r"(?<![\w-])(?:input|select|textarea)(?![\w-])|data-slot=[\"']?(?:input|select|textarea)", re.I)
SKIP_CONTROL_RE = re.compile(r"checkbox|radio|range|color|file|hidden", re.I)
FONT_SIZE_RE = re.compile(r"(?<![\w-])font-size\s*:\s*([0-9.]+)(px|rem|em)\b", re.I)
VIEWPORT_HEIGHT_RE = re.compile(r"(?<![\w-])(?:min-|max-)?height\s*:\s*100d?vh\b", re.I)
FIXED_RE = re.compile(r"(?<![\w-])position\s*:\s*fixed", re.I)
BOTTOM_ZERO_RE = re.compile(r"(?<![\w-])bottom\s*:\s*0(?:px|rem|em)?\s*(?:;|$|!)", re.I)
SVG_RE = re.compile(r"<svg\b([^>]*)>(.*?)</svg\s*>", re.S | re.I)
SHAPE_RE = re.compile(r"<(?:rect|circle|ellipse|path|line|polyline|polygon)\b", re.I)
GENERATED_RE = re.compile(r"<meta\b[^>]*\bname\s*=\s*[\"']?tailplan-theme\b", re.I)
LINK_RE = re.compile(r"<a\b[^>]*\bhref\s*=", re.I)


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _issue(code: str, message: str, fix: str | None = None, line: int | None = None) -> dict:
    item = {"code": code, "message": message}
    if fix:
        item["fix"] = fix
    if line is not None:
        item["line"] = line
    return item


def _css_blocks(text: str):
    """Yield (offset, css) for each style block."""
    for match in STYLE_RE.finditer(text):
        yield match.start(1), match.group(1)


def _inline_styles(text: str):
    """Yield (offset, tag, declarations) for each inline style attribute."""
    for match in INLINE_STYLE_RE.finditer(text):
        yield match.start(2), match.group(1).lower(), match.group(3) if match.group(3) is not None else match.group(4)


def _rules(text: str):
    """Yield (offset, selector, declarations) for each style rule and each inline style."""
    for base, css in _css_blocks(text):
        for rule in RULE_RE.finditer(css):
            yield base + rule.start(), rule.group(1).strip(), rule.group(2)
    for offset, tag, decls in _inline_styles(text):
        yield offset, tag, decls


def _check_slots(text: str, issues: list) -> None:
    known = known_slots()
    sample = sorted(known)
    seen = set()
    scrubbed = STYLE_RE.sub(_blank, SCRIPT_RE.sub(_blank, text))
    for match in SLOT_RE.finditer(scrubbed):
        value = next(g for g in match.groups() if g is not None).strip()
        if not value or value in known or value in seen:
            continue
        seen.add(value)
        close = difflib.get_close_matches(value, sample, n=1)
        hint = f' Did you mean "{close[0]}"?' if close else ""
        issues.append(_issue(
            "ui-unknown-slot", f'Unknown data-slot "{value}".{hint}',
            fix="Use a slot from GET /api/ui.", line=_line(scrubbed, match.start())))


def _check_breakpoints(text: str, issues: list) -> None:
    for base, css in _css_blocks(text):
        for match in MEDIA_RE.finditer(css):
            if BAD_WIDTH_RE.search(match.group(1)):
                issues.append(_issue(
                    "ui-breakpoint", "A media query uses 600px or 640px. Folded iPhone screens are 594px wide.",
                    fix="Use 560px, 700px, or 900px.", line=_line(text, base + match.start())))


def _check_rules(text: str, issues: list) -> None:
    flagged = set()
    for offset, selector, decls in _rules(text):
        line = _line(text, offset)
        if NOWRAP_RE.search(decls) and ELLIPSIS_RE.search(decls) and PROSE_SEL_RE.search(selector):
            if "nowrap" not in flagged:
                flagged.add("nowrap")
                issues.append(_issue(
                    "ui-nowrap-ellipsis", "Prose, a heading, or a cell is cut with nowrap and an ellipsis.",
                    fix="Let the text wrap, or clamp lines with line-clamp.", line=line))
        if CONTROL_SEL_RE.search(selector) and not SKIP_CONTROL_RE.search(selector):
            for size in FONT_SIZE_RE.finditer(decls):
                value, unit = float(size.group(1)), size.group(2).lower()
                px = value if unit == "px" else value * 16
                if px < 16 and "input" not in flagged:
                    flagged.add("input")
                    issues.append(_issue(
                        "ui-input-font-size", "An input, select, or textarea has text smaller than 16px. iOS zooms in on focus.",
                        fix="Set font-size to 16px or more.", line=line))
        if VIEWPORT_HEIGHT_RE.search(decls) and "vh" not in flagged:
            flagged.add("vh")
            issues.append(_issue(
                "ui-viewport-height", "A height of 100vh or 100dvh hides content behind the Safari bars.",
                fix="Use min-height with 100svh, or let content set the height.", line=line))
        if FIXED_RE.search(decls) and BOTTOM_ZERO_RE.search(decls + ";") and "fixed" not in flagged:
            flagged.add("fixed")
            issues.append(_issue(
                "ui-fixed-bottom", "A fixed element sits at bottom 0. The Safari URL pill covers it.",
                fix="Add padding with env(safe-area-inset-bottom) and keep a gap of 64px.", line=line))


def _check_dark_mode(text: str, opted_in: bool, issues: list) -> None:
    if opted_in or UI_MODE_RE.search(text):
        return
    css = "".join(css for _, css in _css_blocks(text))
    if css.strip() and not DARK_RE.search(css) and not DARK_RE.search(text):
        issues.append(_issue(
            "ui-no-dark-mode", "The page sets its own CSS and has no dark mode.",
            fix='Add a prefers-color-scheme query, or set <html data-ui-mode="light"> to opt out.'))


def _check_charts(text: str, issues: list) -> None:
    scrubbed = SCRIPT_RE.sub(_blank, text)
    for match in SVG_RE.finditer(scrubbed):
        attrs, body = match.group(1), match.group(2)
        if re.search(r"<(?:symbol|use)\b", body, re.I) or re.search(r"aria-hidden\s*=\s*[\"']?true|\bhidden\b", attrs, re.I):
            continue
        shapes = len(SHAPE_RE.findall(body))
        chartish = re.search(r"<text\b|data-series|role\s*=\s*[\"']img", attrs + body, re.I)
        if shapes < 3 or not chartish or LINK_RE.search(body):
            continue
        start = scrubbed.rfind("<figure", 0, match.start())
        if start != -1 and "</figure" not in scrubbed[start:match.start()].lower():
            end = scrubbed.lower().find("</figure", match.end())
            if end != -1 and LINK_RE.search(scrubbed[start:end]):
                continue
        issues.append(_issue(
            "ui-chart-unlinked", "A chart has no link to its source.",
            fix="Link each mark or label, or add a source link in the figure.", line=_line(scrubbed, match.start())))
        break


def lint(html: str) -> list[dict]:
    """Return the warnings for one page."""
    if not isinstance(html, str):
        return []
    source = strip_stamp(html)
    if GENERATED_RE.search(source):
        return []  # Pages that Tailplan renders from Markdown carry their own checked CSS.
    text = COMMENT_RE.sub(_blank, source)
    opted_in = is_opted_in(source)
    issues: list[dict] = []
    if opted_in:
        _check_slots(text, issues)
    _check_breakpoints(text, issues)
    _check_rules(text, issues)
    _check_dark_mode(text, opted_in, issues)
    _check_charts(text, issues)
    return issues
