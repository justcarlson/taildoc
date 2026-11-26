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
CSS_DIR = Path(__file__).resolve().parent / "css"
CSS_FILES = ("layers.css", "reset.css", "density.css", "base.css")
TOKENS_AFTER = "reset.css"

CHUNK_RE = re.compile(r"/\*\s*@chunk\s+(\S+)\s+when=(\S+)\s*\*/")
COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
STYLE_BLOCK_RE = re.compile(
    r"<style\b[^>]*\bid\s*=\s*[\"']?tp-ui[\"']?[^>]*>.*?</style\s*>", re.I | re.S)
INJECTED_META_RE = re.compile(r"<meta\b[^>]*\bdata-tailplan-ui\b[^>]*>", re.I)
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")
HUE_RE = re.compile(r"^\d{1,3}(?:\.\d+)?$")
VIZ_VAR_RE = re.compile(r"--(?:chart|seq|div)-\d")
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
}
MODES = ("auto", "light", "dark")
ROOT = "root"


def minify(css: str) -> str:
    """Remove comments and extra white space from CSS."""
    css = COMMENT_RE.sub("", css)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{};,>])\s*", r"\1", css)
    css = re.sub(r":\s+", ":", css)
    css = css.replace(";}", "}")
    return css.strip()


@lru_cache(maxsize=1)
def chunks() -> tuple[tuple[str, str, str, str], ...]:
    """Return (file, id, condition, css) for each chunk in cascade order."""
    out = []
    for name in CSS_FILES:
        text = (CSS_DIR / name).read_text(encoding="utf-8")
        marks = list(CHUNK_RE.finditer(text))
        for i, mark in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            out.append((name, mark.group(1), mark.group(2), minify(text[mark.end():end])))
    return tuple(out)


@dataclass
class Frame:
    tag: str
    ctx: object
    accent: object
    contrast: str


@dataclass
class Features:
    root_attrs: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    slots: dict = field(default_factory=dict)
    slot_count: int = 0
    clamp: bool = False
    contexts: list = field(default_factory=list)
    modifiers: list = field(default_factory=list)
    more: list = field(default_factory=list)
    modes: set = field(default_factory=set)
    attr_kinds: set = field(default_factory=set)
    tp_hue_inline: bool = False
    tp_hue_sheet: bool = False
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


class Scanner(HTMLParser):
    """Collect the Tailplan UI features that one page uses."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.f = Features()
        self.stack: list[Frame] = []
        self.in_style = False

    # Position helpers.
    def _here(self) -> tuple:
        return self.getpos()

    def _after_tag(self) -> tuple:
        line, col = self.getpos()
        text = self.get_starttag_text() or ""
        lines = text.split("\n")
        if len(lines) == 1:
            return line, col + len(text)
        return line + len(lines) - 1, len(lines[-1])

    def _top(self) -> Frame:
        return self.stack[-1] if self.stack else Frame("", ROOT, None, "normal")

    def handle_starttag(self, tag, attrs):
        self._element(tag, attrs, closed=False)

    def handle_startendtag(self, tag, attrs):
        self._element(tag, attrs, closed=True)

    def _element(self, tag: str, attrs, *, closed: bool) -> None:
        f = self.f
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "html" and f.html_open is None:
            f.html_open = self._after_tag()
            f.root_attrs = {k: v for k, v in a.items() if k.startswith("data-ui-")}
        if tag == "head" and f.head_open is None:
            f.head_open = self._after_tag()
        if tag == "meta":
            name = a.get("name", "").lower()
            if name == "tailplan-ui":
                f.meta.update(_parse_meta(a.get("content", "")))
            elif name == "theme-color":
                f.theme_color = True
        if f.first_style is None and (
                tag == "style" or (tag == "link" and "stylesheet" in a.get("rel", "").lower())):
            f.first_style = self._here()
        style = a.get("style")
        if style:
            f.custom_css.append(style)
            if "--tp-hue" in style:
                f.tp_hue_inline = True
            if VIZ_VAR_RE.search(style):
                f.viz_needed = True
        if "data-slot" in a:
            f.slot_count += 1
            f.slots[a["data-slot"]] = f.slots.get(a["data-slot"], 0) + 1
        if "data-clamp" in a:
            f.clamp = True
        parent = self._top()
        frame = self._frame(tag, a, parent)
        if tag == "style" and not closed:
            self.in_style = True
        if not closed and tag not in VOID_TAGS:
            self.stack.append(frame)

    def _frame(self, tag: str, a: dict, parent: Frame) -> Frame:
        f = self.f
        is_root = tag == "html"
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
            if not is_root:
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
            if is_root:
                continue
            entry = (ctx, kind, parsed[0], parsed[1])
            if entry not in f.modifiers:
                f.modifiers.append(entry)
            if kind != "viz":
                spec = (kind, parsed[0], parsed[1])
        if spec is not None:
            accent = spec
        value = a.get("data-ui-contrast")
        if value is not None:
            if value in T.CONTRASTS:
                f.attr_kinds.add("contrast")
                contrast = value
            else:
                f.warnings.append(f"Unknown data-ui-contrast: {value}")
        mode = a.get("data-ui-mode")
        if mode is not None:
            if mode in MODES:
                f.modes.add(mode)
            else:
                f.warnings.append(f"Unknown data-ui-mode: {mode}")
        changed = spec is not None or value is not None or ctx != parent.ctx
        if contrast == "more" and changed and not is_root:
            entry = (ctx, accent)
            if entry not in f.more:
                f.more.append(entry)
        return Frame(tag, ctx, accent, contrast)

    def handle_endtag(self, tag):
        if tag == "head" and self.f.head_end is None:
            self.f.head_end = self._here()
        if tag == "style":
            self.in_style = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.in_style:
            self.f.custom_css.append(data)
            if "--tp-hue" in data:
                self.f.tp_hue_sheet = True
            if VIZ_VAR_RE.search(data):
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
        if HEX_RE.match(value):
            return value.lower(), S.normalize_hex(value)
    elif kind == "hue":
        if HUE_RE.match(value) and 0 <= float(value) <= 360:
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


def _forms(parts: list[str]) -> list[str]:
    """Return same-element and descendant selector forms for two parts."""
    if len(parts) == 1:
        return parts
    a, b = parts
    return [a + b, f"{a} {b}", f"{b} {a}"]


def _scoped(ctx_sel: str | None, inner: list[str]) -> str:
    if ctx_sel is None:
        return ",".join(inner)
    out = []
    for sel in inner:
        out.append(f"{ctx_sel} {sel}")
        if " " not in sel:
            out.append(ctx_sel + sel)
    return ",".join(out)


class Composer:
    """Turn scanned features into the tp.tokens rules."""

    def __init__(self, f: Features, target: str) -> None:
        self.f = f
        self.target = target
        root = {**{f"data-ui-{k}": v for k, v in f.meta.items()}, **f.root_attrs}
        self.root_attrs = root
        self.warnings = list(f.warnings)
        self.root_ctx = ("theme", T.DEFAULT_THEME)
        for kind in ("theme", "surface"):
            value = root.get(f"data-ui-{kind}")
            if value is not None and _valid_context(kind, value):
                self.root_ctx = (kind, value)
                break
            if value is not None and (kind, value) != ("theme", T.DEFAULT_THEME):
                self.warnings.append(f"Unknown root data-ui-{kind}: {value}")
        self.root_accent = None
        for kind in ("accent", "seed", "hue"):
            value = root.get(f"data-ui-{kind}")
            if value is not None:
                parsed = _modifier(kind, value, self.warnings)
                if parsed is not None:
                    self.root_accent = (kind, parsed[0], parsed[1])
                    if kind == "seed" and parsed[1] not in f.seeds:
                        f.seeds.append(parsed[1])
                    break
        viz = root.get("data-ui-viz")
        self.root_viz = viz if viz in T.viz_sets() else None
        if viz is not None and self.root_viz is None:
            self.warnings.append(f"Unknown root data-ui-viz: {viz}")
        self.viz_needed = f.viz_needed or self.root_viz is not None
        self.root_contrast = root.get("data-ui-contrast", "normal")
        mode = root.get("data-ui-mode", "auto")
        self.root_mode = mode if mode in MODES else "auto"

    def _ctx(self, ctx):
        """Return (kind, ident, pinned, selector) for a context key."""
        key = self.root_ctx if ctx == ROOT else ctx
        kind, ident, pinned = _resolve(key)
        sel = None if ctx == ROOT else _attr(*ctx)
        return kind, ident, pinned, sel

    def context_rule(self, ctx) -> str:
        kind, ident, pinned, sel = self._ctx(ctx)
        accent = self.root_accent if ctx == ROOT else None
        viz = self.root_viz if ctx == ROOT else None
        decls = self._context_decls(kind, ident, pinned, accent, viz)
        if ctx == ROOT:
            if not pinned:
                scheme = {"auto": "light dark", "light": "light", "dark": "dark"}[self.root_mode]
                decls.insert(0, f"color-scheme:{scheme}")
            sel = ":root"
        return T.rule(sel, decls)

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

    def modifier_rules(self) -> list[str]:
        out = []
        for ctx, kind, value, spec in self.f.modifiers:
            ckind, ident, pinned, sel = self._ctx(ctx)
            selector = _scoped(sel, [_attr(kind, value)])
            if kind == "viz":
                out.append(T.rule(selector, T.viz_decls(ckind, ident, spec, pinned=pinned)))
            else:
                out.append(T.rule(selector, T.accent_decls(
                    ckind, ident, spec, pinned=pinned, contrast=self.root_contrast)))
        return out

    def contrast_rules(self) -> list[str]:
        out = []
        more = _attr("contrast", "more")
        for ctx, accent in self.f.more:
            kind, ident, pinned, sel = self._ctx(ctx)
            if accent is None:
                inner = [more]
                spec = self.root_accent[2] if (ctx == ROOT and self.root_accent) else None
            else:
                inner = _forms([more, _attr(accent[0], accent[1])])
                spec = accent[2]
            selector = _scoped(sel, inner)
            if sel is not None and accent is None:
                selector += f",{more} {sel}"
            out.append(T.rule(selector, T.contrast_decls(kind, ident, spec, pinned)))
        return out

    def hue_envelope(self) -> str:
        if not (self.f.tp_hue_inline or self.f.tp_hue_sheet):
            return ""
        kind, ident, _, _ = self._ctx(ROOT)
        primary = T.mode_tokens(kind, ident, S.LIGHT)["primary"]
        hue = round(S.hex_to_oklch(primary)[2])
        selectors = ['[style*="--tp-hue"]']
        if self.f.tp_hue_sheet:
            selectors.insert(0, ":root")
        return f"{','.join(selectors)}{{{S.hue_envelope_css(hue)}}}"

    def mode_rules(self) -> list[str]:
        scheme = {"auto": "light dark", "light": "light", "dark": "dark"}
        return [f'{_attr("mode", m)}{{color-scheme:{scheme[m]}}}' for m in MODES if m in self.f.modes]

    def hover_rule(self) -> str:
        selectors = [":root"] + [f"[data-ui-{k}]" for k in
                                 ("theme", "surface", "accent", "seed", "hue", "contrast")
                                 if k in self.f.attr_kinds]
        if self.f.tp_hue_inline:
            selectors.append('[style*="--tp-hue"]')
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
        rules += self.modifier_rules()
        rules += self.contrast_rules()
        rules += self.mode_rules()
        rules.append(self.hover_rule())
        return "@layer tp.tokens{" + "".join(r for r in rules if r) + "}"

    def wants(self, condition: str) -> bool:
        if condition == "always":
            return True
        if condition == "standalone":
            return self.target == "standalone"
        if condition == "island":
            return bool(self.f.contexts)
        kind, _, value = condition.partition(":")
        if kind == "slot":
            return value in self.f.slots
        if kind == "attr" and value == "data-clamp":
            return self.f.clamp
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
    """Remove a Tailplan UI style block and the meta tags it added."""
    html = STYLE_BLOCK_RE.sub("", html)
    return INJECTED_META_RE.sub("", html)


def scan(html: str) -> Features:
    """Return the features that one page uses."""
    scanner = Scanner()
    scanner.feed(html)
    scanner.close()
    return scanner.f


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
        doctype = re.match(r"\s*<!doctype[^>]*>", source, re.I)
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
        "warnings": composer.warnings,
    }
    return out, meta
