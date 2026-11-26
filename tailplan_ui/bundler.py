"""Scan an HTML page and stamp a tree-shaken Tailplan UI style block into it.

The scanner uses the standard library HTMLParser.
The block goes before the first author style, so unlayered author CSS wins.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from .color import solve as S
from .color import tokens as T

UI_VERSION = "1.0.0"
TARGETS = ("standalone", "t3-embed")
UI_DIR = Path(__file__).resolve().parent
CSS_FILES = ("layers.css", "reset.css", "density.css", "base.css")
LAYER_DIRS = ("primitives", "blocks", "wireframes")
TOKENS_AFTER = "reset.css"

CHUNK_RE = re.compile(r"/\*\s*@chunk\s+(\S+)\s+when=(\S+)\s*\*/")
COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
# Strings and comments in one pass, so "/*" in a string and a quote in a comment stay safe.
CSS_TOKEN_RE = re.compile(r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')|/\*.*?\*/", re.S)
HEX_RE = S.HEX_RE
HUE_RE = re.compile(r"[0-9]{1,3}(?:\.[0-9]+)?\Z")
VIZ_VAR_RE = re.compile(r"--(?:chart|seq|div)-\d")
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
}
RCDATA_TAGS = ("title", "textarea")
# Style tags in these elements do not style the page, or do not parse as HTML.
INERT_TAGS = ("svg", "math", "template", "noscript")
HEAD_TAGS = {"title", "meta", "link", "style", "script", "base", "noscript", "template"}
MODES = ("auto", "light", "dark")
META_KEYS = ("theme", "surface", "accent", "seed", "hue", "viz", "mode", "contrast")
HUE_VAR_SEL = '[data-ui-hue-var],[style*="--tp-hue"]'
ISLAND_LIMIT = "([data-ui-theme],[data-ui-surface])"
ROOT = "root"


def is_hue(value: str) -> bool:
    """Return True for an ASCII hue from 0 to 360."""
    return HUE_RE.fullmatch(value) is not None and float(value) <= 360


def strip_comments(css: str) -> str:
    """Remove CSS comments and keep string literals."""
    return CSS_TOKEN_RE.sub(lambda m: m.group(1) or "", css)


def minify(css: str) -> str:
    """Remove comments and extra white space from CSS. String literals stay as they are."""
    strings: list[str] = []

    def mask(m: re.Match) -> str:
        if m.group(1) is None:
            return " "
        strings.append(m.group(1))
        return f"\0{len(strings) - 1}\0"

    css = CSS_TOKEN_RE.sub(mask, css)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{};,>])\s*", r"\1", css)
    css = re.sub(r":\s+", ":", css)
    css = css.replace(";}", "}").strip()
    return re.sub(r"\0(\d+)\0", lambda m: strings[int(m.group(1))], css)


def css_files(ui_dir: Path = UI_DIR) -> list[Path]:
    """Return the CSS source files in cascade layer order."""
    files = [ui_dir / "css" / name for name in CSS_FILES]
    for layer in LAYER_DIRS:
        files += sorted((ui_dir / layer).glob("*.css"))
    return files


@lru_cache(maxsize=4)
def chunks(ui_dir: Path = UI_DIR) -> tuple[tuple[str, str, str, str], ...]:
    """Return (file, id, condition, css) for each chunk in cascade order.

    A file with no chunk marker is one chunk. Its id is the file stem and it always loads.
    """
    out = []
    for path in css_files(ui_dir):
        text = path.read_text(encoding="utf-8")
        marks = list(CHUNK_RE.finditer(text))
        if not marks:
            out.append((path.name, path.stem, "always", minify(text)))
        for i, mark in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            out.append((path.name, mark.group(1), mark.group(2), minify(text[mark.end():end])))
    return tuple(out)


@dataclass
class Frame:
    tag: str
    ctx: object
    accent: object
    contrast: str | None


@dataclass
class Features:
    root_attrs: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    opted_in: bool = False
    slots: dict = field(default_factory=dict)
    slot_count: int = 0
    attrs: set = field(default_factory=set)
    contexts: list = field(default_factory=list)
    modifiers: list = field(default_factory=list)
    contrasts: list = field(default_factory=list)
    modes: set = field(default_factory=set)
    attr_kinds: set = field(default_factory=set)
    tp_hue_inline: bool = False
    tp_hue_sheet: bool = False
    hue_var: bool = False
    viz_needed: bool = False
    custom_css: list = field(default_factory=list)
    theme_color: bool = False
    warnings: list = field(default_factory=list)
    seeds: list = field(default_factory=list)
    first_style: tuple | None = None
    head_end: tuple | None = None
    head_open: tuple | None = None
    html_open: tuple | None = None


def _parse_meta(content: str) -> dict:
    out = {}
    for part in re.split(r"[\s;,]+", content.strip()):
        if "=" in part:
            key, _, value = part.partition("=")
            out[key.strip().lower()] = value.strip()
    return out


class _Parser(HTMLParser):
    """An HTMLParser that treats title and textarea as RCDATA on every Python version."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rcdata: str | None = None

    def set_cdata_mode(self, elem, *args, **kwargs):
        # In RCDATA, a <style> or <script> is text. It must not start a CDATA run.
        if self.rcdata is not None and elem != self.rcdata:
            return
        super().set_cdata_mode(elem, *args, **kwargs)

    def _after_tag(self) -> tuple:
        line, col = self.getpos()
        text = self.get_starttag_text() or ""
        lines = text.split("\n")
        if len(lines) == 1:
            return line, col + len(text)
        return line + len(lines) - 1, len(lines[-1])

    def handle_starttag(self, tag, attrs):
        if self.rcdata is None:
            self.start(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}, False)
            if tag in RCDATA_TAGS:
                self.rcdata = tag

    def handle_startendtag(self, tag, attrs):
        if self.rcdata is None:
            self.start(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}, True)

    def handle_endtag(self, tag):
        if self.rcdata is not None:
            if tag != self.rcdata:
                return
            self.rcdata = None
        self.end(tag)

    def handle_data(self, data):
        if self.rcdata is None:
            self.data(data)

    def start(self, tag: str, a: dict, closed: bool) -> None:
        pass

    def end(self, tag: str) -> None:
        pass

    def data(self, data: str) -> None:
        pass


class _StampFinder(_Parser):
    """Find the spans of a Tailplan UI style block and the meta tags it added."""

    def __init__(self) -> None:
        super().__init__()
        self.spans: list[tuple[tuple, tuple]] = []
        self.open_style: tuple | None = None

    def start(self, tag, a, closed):
        if tag == "style":
            if a.get("id") == "tp-ui" and "data-tailplan-ui" in a:
                if closed:
                    self.spans.append((self.getpos(), self._after_tag()))
                else:
                    self.open_style = self.getpos()
        elif tag == "meta" and "data-tailplan-ui" in a:
            self.spans.append((self.getpos(), self._after_tag()))

    def end(self, tag):
        if tag == "style":
            if self.open_style is not None:
                self.spans.append((self.open_style, ("end", self.getpos())))
                self.open_style = None


class Scanner(_Parser):
    """Collect the Tailplan UI features that one page uses."""

    def __init__(self) -> None:
        super().__init__()
        self.f = Features()
        self.stack: list[Frame] = []
        self.in_style = False
        self.body_started = False
        self.seen_html = False

    def _top(self) -> Frame:
        return self.stack[-1] if self.stack else Frame("", ROOT, None, None)

    def _in_inert(self) -> bool:
        return any(fr.tag in INERT_TAGS for fr in self.stack)

    def start(self, tag, a, closed):
        f = self.f
        f.attrs.update(a)
        is_root = tag == "html" and not self.seen_html
        if is_root:
            self.seen_html = True
            f.html_open = self._after_tag()
            f.root_attrs = {k: v for k, v in a.items() if k.startswith("data-ui-")}
        elif tag == "head" and f.head_open is None:
            f.head_open = self._after_tag()
        elif tag == "body" or (tag not in HEAD_TAGS and tag not in ("html", "head")
                               and not self._in_inert()):
            self.body_started = True
        if tag == "meta":
            name = a.get("name", "").lower()
            if name == "tailplan-ui":
                content = a.get("content", "")
                f.opted_in = content.strip().lower() not in ("0", "false")
                f.meta.update(_parse_meta(content))
            elif name == "theme-color":
                f.theme_color = True
        is_sheet = tag == "style" or (tag == "link" and "stylesheet" in a.get("rel", "").lower())
        if (f.first_style is None and is_sheet and not self.body_started
                and not self._in_inert()):
            f.first_style = self.getpos()
        style = a.get("style")
        if style:
            f.custom_css.append(style)
            plain = strip_comments(style)
            if "--tp-hue" in plain:
                f.tp_hue_inline = True
            if VIZ_VAR_RE.search(plain):
                f.viz_needed = True
        if "data-ui-hue-var" in a:
            f.hue_var = True
        if "data-slot" in a:
            f.slot_count += 1
            f.slots[a["data-slot"]] = f.slots.get(a["data-slot"], 0) + 1
        parent = self._top()
        # The Composer checks the root attributes once, with the meta tag.
        frame = parent if is_root else self._frame(tag, a, parent)
        if tag == "style" and not closed:
            self.in_style = True
        if not closed and tag not in VOID_TAGS:
            self.stack.append(Frame(tag, frame.ctx, frame.accent, frame.contrast))

    def _frame(self, tag: str, a: dict, parent: Frame) -> Frame:
        f = self.f
        ctx = parent.ctx
        accent = parent.accent
        contrast = parent.contrast
        for kind in ("theme", "surface"):
            value = a.get(f"data-ui-{kind}")
            if value is None:
                continue
            f.attr_kinds.add(kind)
            if not _valid_context(kind, value):
                f.warnings.append(f"Unknown data-ui-{kind}: {value}")
                continue
            ctx = (kind, value)
            accent = None
            if ctx not in f.contexts:
                f.contexts.append(ctx)
        spec = None
        for kind in ("accent", "seed", "hue", "viz"):
            value = a.get(f"data-ui-{kind}")
            if value is None:
                continue
            parsed = _modifier(kind, value, f.warnings)
            if parsed is None:
                continue
            f.attr_kinds.add(kind)
            if kind == "viz":
                f.viz_needed = True
            if kind == "seed":
                f.seeds.append(S.normalize_hex(value))
            entry = (ctx, kind, parsed[0], parsed[1])
            if entry not in f.modifiers:
                f.modifiers.append(entry)
            if kind != "viz":
                spec = (kind, parsed[0], parsed[1])
        if spec is not None:
            accent = spec
        value = a.get("data-ui-contrast")
        explicit = False
        if value is not None:
            if value in T.CONTRASTS:
                f.attr_kinds.add("contrast")
                contrast = value
                explicit = True
            else:
                f.warnings.append(f"Unknown data-ui-contrast: {value}")
        mode = a.get("data-ui-mode")
        if mode is not None:
            if mode in MODES:
                f.modes.add(mode)
            else:
                f.warnings.append(f"Unknown data-ui-mode: {mode}")
        changed = spec is not None or explicit or ctx != parent.ctx
        if contrast is not None and changed:
            entry = (ctx, accent, contrast)
            if entry not in f.contrasts:
                f.contrasts.append(entry)
        return Frame(tag, ctx, accent, contrast)

    def end(self, tag):
        if tag == "head":
            if self.f.head_end is None:
                self.f.head_end = self.getpos()
            self.body_started = True
        if tag == "style":
            self.in_style = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def data(self, data):
        if self.in_style:
            self.f.custom_css.append(data)
            if re.search(r"--tp-hue\s*:", CSS_TOKEN_RE.sub(" ", data)):
                self.f.tp_hue_sheet = True
            if VIZ_VAR_RE.search(strip_comments(data)):
                self.f.viz_needed = True


def _valid_context(kind: str, value: str) -> bool:
    if kind == "theme":
        try:
            T.resolve_theme(value)
        except KeyError:
            return False
        return True
    return value in T.surfaces()


def _modifier(kind: str, value: str, warnings: list) -> tuple | None:
    """Return (selector value, solver spec) for a modifier attribute."""
    if kind == "accent":
        if value in T.accents():
            return value, value
    elif kind == "viz":
        if value in T.viz_sets():
            return value, value
    elif kind == "seed":
        if S.is_hex(value):
            return value.lower(), S.normalize_hex(value)
    elif kind == "hue":
        if is_hue(value):
            return value, float(value)
    warnings.append(f"Unknown data-ui-{kind}: {value}")
    return None


def _attr(kind: str, value: str) -> str:
    flag = " i" if kind == "seed" else ""
    return f'[data-ui-{kind}="{value}"{flag}]'


def _resolve(ctx) -> tuple[str, str, str | None]:
    """Return (kind, preset or surface id, pinned mode) for a context key."""
    kind, value = ctx
    if kind == "theme":
        ident, pinned = T.resolve_theme(value)
        return "theme", ident, pinned
    return "surface", value, None


def _contrast_forms(x: str, a: str | None, island: bool) -> list[str]:
    """Return selectors for one contrast value X and an optional accent A.

    In an island scope, a plain selector matches only descendants of the scope root.
    The :scope forms reach the island root and its ancestors outside the scope.
    """
    if a is None:
        return [f":scope{x}", x, f"{x} :scope"] if island else [x]
    forms = [x + a, f"{x} {a}", f"{a} {x}"]
    if island:
        forms += [f":scope{x}{a}", f":scope{x} {a}", f":scope{a} {x}",
                  f"{x} :scope{a}", f"{x} :scope {a}"]
    return forms


class Composer:
    """Turn scanned features into the tp.tokens rules."""

    def __init__(self, f: Features, target: str) -> None:
        self.f = f
        self.target = target
        self.warnings = list(f.warnings)
        for key in f.meta:
            if key not in META_KEYS:
                self.warnings.append(f"Unknown tailplan-ui meta key: {key}")
        meta_attrs = {f"data-ui-{k}": v for k, v in f.meta.items() if k in META_KEYS}
        root = {**meta_attrs, **f.root_attrs}
        self.root_attrs = root
        self.root_ctx = None
        for source in (f.root_attrs, meta_attrs):
            for kind in ("theme", "surface"):
                key = f"data-ui-{kind}"
                value = source.get(key)
                if value is None or (source is meta_attrs and key in f.root_attrs):
                    continue
                if not _valid_context(kind, value):
                    self.warnings.append(f"Unknown root data-ui-{kind}: {value}")
                elif self.root_ctx is None:
                    self.root_ctx = (kind, value)
        self.root_ctx = self.root_ctx or ("theme", T.DEFAULT_THEME)
        self.root_accent = None
        for source in (f.root_attrs, meta_attrs):
            for kind in ("accent", "seed", "hue"):
                key = f"data-ui-{kind}"
                value = source.get(key)
                if value is None or (source is meta_attrs and key in f.root_attrs):
                    continue
                parsed = _modifier(kind, value, self.warnings)
                if parsed is not None and self.root_accent is None:
                    self.root_accent = (kind, parsed[0], parsed[1])
                    if kind == "seed" and parsed[1] not in f.seeds:
                        f.seeds.append(parsed[1])
        viz = root.get("data-ui-viz")
        self.root_viz = viz if viz in T.viz_sets() else None
        if viz is not None and self.root_viz is None:
            self.warnings.append(f"Unknown root data-ui-viz: {viz}")
        self.viz_needed = f.viz_needed or self.root_viz is not None
        contrast = root.get("data-ui-contrast", "normal")
        if contrast not in T.CONTRASTS:
            self.warnings.append(f"Unknown root data-ui-contrast: {contrast}")
            contrast = "normal"
        self.root_contrast = contrast
        mode = root.get("data-ui-mode", "auto")
        if mode not in MODES:
            self.warnings.append(f"Unknown root data-ui-mode: {mode}")
            mode = "auto"
        self.root_mode = mode
        if f.tp_hue_sheet and not f.hue_var:
            self.warnings.append(
                "A style sheet sets --tp-hue. Add data-ui-hue-var to those elements.")

    def _ctx(self, ctx):
        """Return (kind, ident, pinned, scope prelude) for a context key."""
        key = self.root_ctx if ctx == ROOT else ctx
        kind, ident, pinned = _resolve(key)
        sel = None if ctx == ROOT else _attr(*ctx) + ":not(:root)"
        return kind, ident, pinned, sel

    def context_rule(self, ctx) -> str:
        kind, ident, pinned, _ = self._ctx(ctx)
        accent = self.root_accent if ctx == ROOT else None
        viz = self.root_viz if ctx == ROOT else None
        decls = self._context_decls(kind, ident, pinned, accent, viz)
        if ctx == ROOT:
            if not pinned:
                scheme = {"auto": "light dark", "light": "light", "dark": "dark"}[self.root_mode]
                decls.insert(0, f"color-scheme:{scheme}")
            return T.rule(":root", decls)
        # Zero-specificity :not(:root), so a root attribute never takes island paint.
        return T.rule(f":where(:not(:root)){_attr(*ctx)}", decls)

    def _context_decls(self, kind, ident, pinned, accent, viz) -> list[str]:
        contrast = self.root_contrast
        decls = T.context_decls(kind, ident, pinned=pinned, contrast=contrast)
        if accent is not None:
            extra = T.accent_decls(kind, ident, accent[2], pinned=pinned, contrast=contrast)
            names = {d.split(":", 1)[0] for d in extra}
            decls = [d for d in decls if d.split(":", 1)[0] not in names] + extra
        if self.viz_needed:
            decls += T.viz_decls(kind, ident, viz or T.context_viz(kind, ident), pinned=pinned)
        return decls

    def _modifier_rules(self, ctx) -> list[str]:
        out = []
        ckind, ident, pinned, sel = self._ctx(ctx)
        for entry_ctx, kind, value, spec in self.f.modifiers:
            if entry_ctx != ctx:
                continue
            attr = _attr(kind, value)
            selector = attr if sel is None else f":scope{attr},{attr}"
            if kind == "viz":
                out.append(T.rule(selector, T.viz_decls(ckind, ident, spec, pinned=pinned)))
            else:
                out.append(T.rule(selector, T.accent_decls(
                    ckind, ident, spec, pinned=pinned, contrast=self.root_contrast)))
        return out

    def _contrast_entries(self) -> list[tuple]:
        """Return the contrast entries to emit, in document order.

        Later rules win at equal specificity, so a nested override comes after its region.
        """
        entries = [e for e in self.f.contrasts]
        if not any(c != self.root_contrast for _, _, c in entries):
            return []
        return entries

    def _contrast_rules(self, ctx) -> list[str]:
        out = []
        kind, ident, pinned, sel = self._ctx(ctx)
        for entry_ctx, accent, contrast in self._contrast_entries():
            if entry_ctx != ctx:
                continue
            x = _attr("contrast", contrast)
            if accent is None:
                inner = _contrast_forms(x, None, sel is not None)
                spec = self.root_accent[2] if (ctx == ROOT and self.root_accent) else None
            else:
                inner = _contrast_forms(x, _attr(accent[0], accent[1]), sel is not None)
                spec = accent[2]
            out.append(T.rule(",".join(inner), T.contrast_decls(
                kind, ident, spec, pinned, contrast=contrast)))
        return out

    def scoped_rules(self, ctx) -> str:
        """Return the modifier and contrast rules for one context.

        Rules sit in a donut scope when islands exist, so they stop at nested themes and surfaces.
        """
        rules = "".join(self._modifier_rules(ctx) + self._contrast_rules(ctx))
        if not rules:
            return ""
        _, _, _, sel = self._ctx(ctx)
        if sel is None:
            return f"@scope (:root) to {ISLAND_LIMIT}{{{rules}}}" if self.f.contexts else rules
        return f"@scope ({sel}) to {ISLAND_LIMIT}{{{rules}}}"

    def modifier_rules(self) -> list[str]:
        return [r for ctx in [ROOT, *self.f.contexts] for r in self._modifier_rules(ctx)]

    def contrast_rules(self) -> list[str]:
        return [r for ctx in [ROOT, *self.f.contexts] for r in self._contrast_rules(ctx)]

    def has_envelope(self) -> bool:
        return self.f.tp_hue_inline or self.f.hue_var

    def hue_envelope(self) -> str:
        if not self.has_envelope():
            return ""
        kind, ident, _, _ = self._ctx(ROOT)
        primary = T.mode_tokens(kind, ident, S.LIGHT)["primary"]
        hue = round(S.hex_to_oklch(primary)[2])
        # Never on :root: the envelope binds only to elements that opt in.
        return f"{HUE_VAR_SEL}{{{S.hue_envelope_css(hue)}}}"

    def mode_rules(self) -> list[str]:
        scheme = {"auto": "light dark", "light": "light", "dark": "dark"}
        return [f'{_attr("mode", m)}{{color-scheme:{scheme[m]}}}' for m in MODES if m in self.f.modes]

    def hover_rule(self) -> str:
        selectors = [":root"] + [f"[data-ui-{k}]" for k in
                                 ("theme", "surface", "accent", "seed", "hue", "contrast")
                                 if k in self.f.attr_kinds]
        if self.has_envelope():
            selectors.append(HUE_VAR_SEL)
        decls = [
            "--primary-hover:color-mix(in oklab,var(--primary) 86%,var(--foreground))",
            "--primary-hover-ink:color-mix(in oklab,var(--primary-ink) 78%,var(--foreground))",
            "--accent-hover:color-mix(in oklab,var(--accent) 82%,var(--primary))",
        ]
        return T.rule(",".join(selectors), decls)

    def tokens_css(self) -> str:
        rules = [self.context_rule(ROOT)]
        rules += [self.context_rule(ctx) for ctx in self.f.contexts]
        rules.append(self.hue_envelope())
        rules += [self.scoped_rules(ctx) for ctx in [ROOT, *self.f.contexts]]
        rules += self.mode_rules()
        rules.append(self.hover_rule())
        return "@layer tp.tokens{" + "".join(r for r in rules if r) + "}"

    def wants(self, condition: str) -> bool:
        if condition == "always":
            return True
        if condition == "standalone":
            return self.target == "standalone"
        if condition == "island":
            return bool(self.f.contexts or self.f.modes)
        kind, _, value = condition.partition(":")
        if kind == "slot" and value:
            return any(v in self.f.slots for v in value.split("|"))
        if kind == "attr" and value:
            return any(v in self.f.attrs for v in value.split("|"))
        raise ValueError(f"Unknown chunk condition: {condition}")

    def css(self) -> str:
        parts = []
        for name, ident, condition, css in chunks():
            if self.wants(condition):
                parts.append(css)
            if name == TOKENS_AFTER and ident == "canvas":
                parts.append(self.tokens_css())
        return "".join(parts)


def _offset(lines: list[int], pos: tuple) -> int:
    line, col = pos
    return lines[line - 1] + col


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def strip_stamp(html: str) -> str:
    """Remove a Tailplan UI style block and the meta tags it added.

    The parser skips comments, scripts, and RCDATA, so text that looks like a stamp stays.
    """
    finder = _StampFinder()
    finder.feed(html)
    finder.close()
    if not finder.spans:
        return html
    lines = _line_starts(html)
    cuts = []
    for begin, end in finder.spans:
        a = _offset(lines, begin)
        if end[0] == "end":
            b = html.index(">", _offset(lines, end[1])) + 1
        else:
            b = _offset(lines, end)
        cuts.append((a, b))
    out, at = [], 0
    for a, b in sorted(cuts):
        out.append(html[at:a])
        at = b
    out.append(html[at:])
    return "".join(out)


def scan(html: str) -> Features:
    """Return the features that one page uses."""
    scanner = Scanner()
    scanner.feed(html)
    scanner.close()
    return scanner.f


def is_opted_in(html: str) -> bool:
    """Return True when a page has a tailplan-ui meta tag whose content is not 0 or false."""
    return scan(strip_stamp(html)).opted_in


def build_css(html: str, target: str = "standalone") -> str:
    """Return only the Tailplan UI CSS for one page."""
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    return Composer(scan(strip_stamp(html)), target).css()


def bundle(html: str, target: str = "standalone") -> tuple[str, dict]:
    """Stamp the Tailplan UI style block into a page.

    The function returns the new page and a metadata dict.
    A second run gives the same page.
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    source = strip_stamp(html)
    features = scan(source)
    composer = Composer(features, target)
    css = composer.css()
    style = f'<style id="tp-ui" data-tailplan-ui="{UI_VERSION}">{css}</style>'
    metas = ""
    if target == "standalone" and not features.theme_color:
        kind, ident, pinned, _ = composer._ctx(ROOT)
        colors = T.theme_color(kind, ident)
        forced = pinned or {"light": S.LIGHT, "dark": S.DARK}.get(composer.root_mode)
        for mode in S.MODES:
            value = colors[forced or mode]
            metas += (f'<meta name="theme-color" media="(prefers-color-scheme: {mode})" '
                      f'content="{value}" data-tailplan-ui>')
    lines = _line_starts(source)
    if features.first_style is not None:
        at = _offset(lines, features.first_style)
    elif features.head_end is not None:
        at = _offset(lines, features.head_end)
    elif features.head_open is not None:
        at = _offset(lines, features.head_open)
    elif features.html_open is not None:
        at = _offset(lines, features.html_open)
    else:
        doctype = re.match(r"(?:\s|<!--.*?-->)*<!doctype[^>]*>", source, re.I | re.S)
        at = doctype.end() if doctype else 0
    out = source[:at] + metas + style + source[at:]
    kind, ident, pinned, _ = composer._ctx(ROOT)
    root_accent = composer.root_accent
    if root_accent is not None:
        ui_accent = root_accent[1]
    elif kind == "theme":
        ui_accent = T.presets()[ident].get("accentName", ident)
    else:
        ui_accent = T.surfaces()[ident]["accent"]
    custom = "".join(features.custom_css)
    meta = {
        "uiVersion": UI_VERSION,
        "target": target,
        "uiTheme": composer.root_ctx[1] if composer.root_ctx[0] == "theme" else None,
        "uiSurface": composer.root_ctx[1] if composer.root_ctx[0] == "surface" else None,
        "uiAccent": ui_accent,
        "uiViz": (composer.root_viz or T.context_viz(kind, ident)) if composer.viz_needed else None,
        "uiCssBytes": len(css.encode("utf-8")),
        "customCssBytes": len(custom.encode("utf-8")),
        "uiSlotCount": features.slot_count,
        "hasDarkMode": pinned != S.LIGHT and composer.root_mode != "light",
        "sha256": hashlib.sha256(out.encode("utf-8")).hexdigest(),
        "seeds": sorted(set(features.seeds)),
        "warnings": list(dict.fromkeys(composer.warnings)),
    }
    return out, meta
