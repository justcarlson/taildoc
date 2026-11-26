"""Render the public /ui gallery: Pigment, every catalog slot, and the Duo frames.

The page has no script and no form. Radio inputs and :has() rules drive every control.
"""
from __future__ import annotations

import html
from functools import lru_cache

import tailplan_ui
from tailplan_ui.bundler import UI_VERSION
from tailplan_ui.color import solve as S
from tailplan_ui.color import tokens as T

PAGE_THEME = "graphite"
MODES = ("auto", "light", "dark")
ACCENT_INKS = ("--primary-ink", "--accent-foreground", "--ring")
ROLES = (
    ("background", "foreground"), ("card", "card-foreground"), ("muted", "muted-foreground"),
    ("primary", "primary-foreground"), ("accent", "accent-foreground"),
    ("success-surface", "success"), ("warning-surface", "warning"),
    ("destructive-surface", "destructive"), ("info-surface", "info"),
)
FRAMES = (
    ("duo-folded", "382 × 580", "Folded Duo. One column with a tab bar.", 402),
    ("duo-landscape", "594 × 368", "Folded Duo on its side. The body scrolls under a short bar.", 614),
    ("duo-wide", "951 × 585", "Wide Duo. The sidebar shows because the screen is wider than 620 px.", 971),
)
STAGE_VIEWPORTS = (("duo-folded", "Duo folded"), ("duo-landscape", "Duo landscape"), ("duo-wide", "Duo wide"))
CLI = """tailplan ui list --category blocks
tailplan ui show verdict
tailplan ui init plan.html --theme graphite --accent cobalt
tailplan ui color seed '#ffd400' --surface slate
tailplan ui color check --preset nord
tailplan ui bundle plan.html -o plan.out.html
tailplan ui verify plan.out.html --strict"""

GALLERY_CSS = """
.g{max-width:80rem;margin:0 auto;padding:var(--gutter);padding-bottom:calc(64px + env(safe-area-inset-bottom,0px));display:grid;gap:calc(var(--space)*2.5);min-width:0}
.g section,.g figure{min-width:0;margin:0;display:grid;gap:var(--space)}
.g h2{margin:0}
.g-lede{margin:0;color:var(--muted-foreground);max-width:46rem}
.g-nav{display:flex;flex-wrap:wrap;gap:.5rem 1rem}
.g-grid{display:grid;gap:var(--space);grid-template-columns:repeat(auto-fill,minmax(min(100%,19rem),1fr))}
.g-item{min-width:0;display:grid;gap:.75rem;align-content:start;padding:var(--space);border:var(--line) solid var(--border);border-radius:var(--radius);background:var(--card);color:var(--card-foreground)}
.g-item h3{margin:0;font-size:1rem}
.g-item p{margin:0;color:var(--muted-foreground);overflow-wrap:anywhere}
.g-live{min-width:0;display:flex;flex-wrap:wrap;gap:.5rem;align-items:flex-start}
.g-live>*{max-width:100%;min-width:0}
.g-item pre,.mx-snippet{margin:0;max-width:100%;overflow-x:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8125rem}
.g-wire{display:grid;gap:.75rem}
.g-frames{display:grid;gap:calc(var(--space)*1.5);grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr))}
.g-frames figcaption{color:var(--muted-foreground)}
.mx{display:grid;gap:var(--space);min-width:0}
.mx-controls{display:grid;gap:.75rem;min-width:0}
.mx-group{display:grid;gap:.375rem;min-width:0}
.mx-legend{font-weight:600;font-size:.875rem}
.mx-opts{display:flex;flex-wrap:wrap;gap:.375rem}
.mx-opt{position:relative;display:inline-flex;align-items:center;gap:.375rem;min-height:44px;padding:0 .75rem;border:var(--line) solid var(--border);border-radius:999px;background:var(--card);color:var(--card-foreground);cursor:pointer;font-size:.875rem}
.mx-opt input{position:absolute;inset:0;margin:0;opacity:0;cursor:pointer}
.mx-opt:has(input:checked){border-color:var(--primary-edge);background:var(--accent);color:var(--accent-foreground);font-weight:600}
.mx-opt:has(input:focus-visible){outline:2px solid var(--ring);outline-offset:2px}
.mx-dot{inline-size:.875rem;block-size:.875rem;border-radius:50%;border:var(--line) solid var(--border-strong)}
.mx-stage{display:none;min-width:0;gap:var(--space);padding:var(--gutter);border:var(--line) solid var(--border);border-radius:var(--radius);background:var(--background);color:var(--foreground)}
.mx-stage p{margin:0}
.mx-stage>header{display:grid;gap:.25rem}
.mx-stage>header span{color:var(--muted-foreground)}
.mx-roles{display:grid;gap:.5rem;grid-template-columns:repeat(auto-fill,minmax(min(100%,8.5rem),1fr))}
.mx-role{display:grid;align-content:end;min-height:4.5rem;padding:.5rem .625rem;border:var(--line) solid var(--border);border-radius:var(--radius);font-size:.8125rem;font-weight:600;overflow-wrap:anywhere}
.mx-row{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.mx-ink{color:var(--primary-ink);font-weight:600}
.mx-viz{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:.375rem;align-items:end;block-size:6rem}
.mx-ramp{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));block-size:1.25rem;border-radius:var(--radius);overflow:hidden}
.mx-bar{border-radius:var(--radius) var(--radius) 0 0;min-block-size:1rem}
.mx-snippet{padding:.75rem;border:var(--line) solid var(--border);border-radius:var(--radius);background:var(--code-background);color:var(--code-foreground)}
.mx-snippet code{user-select:all}
.sn{display:none}
"""


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


def _rule(selector: str, decls) -> str:
    return selector + "{" + ";".join(decls) + "}"


def _ids() -> dict[str, list[str]]:
    return {
        "t": sorted(T.presets()),
        "a": ["none", *sorted(T.accents())],
        "v": ["default", *sorted(T.viz_sets())],
        "m": list(MODES),
        "c": list(T.CONTRASTS),
    }


def _checked(group: str, value: str) -> str:
    return f".mx:has(#mx-{group}-{value}:checked)"


def mixer_css() -> str:
    """Return the unlayered mixer rules. They beat the tp.tokens layer for the checked values."""
    ids = _ids()
    out = []
    for role, ink in ROLES:
        out.append(_rule(f".r-{role}", [f"background:var(--{role})", f"color:var(--{ink})"]))
    for i in range(1, T.CHART_SLOTS + 1):
        out.append(_rule(f".mx-bar.c{i}", [f"background:var(--chart-{i})", f"block-size:{30 + (i * 37) % 70}%"]))
    for ramp in ("seq", "div"):
        for i in range(1, T.RAMP_SLOTS + 1):
            out.append(_rule(f".q-{ramp}-{i}", [f"background:var(--{ramp}-{i})"]))
    for accent, doc in sorted(T.accents().items()):
        out.append(_rule(f".mx-dot.d-{accent}", [f"background:light-dark({doc['light']},{doc['dark']})"]))
    for viz in ids["v"][1:]:
        light = T.viz_tokens("theme", PAGE_THEME, viz, S.LIGHT)
        out.append(_rule(f".mx-dot.d-viz-{viz}", [
            f"background:conic-gradient({light['chart-1']} 0 25%,{light['chart-2']} 0 50%,"
            f"{light['chart-3']} 0 75%,{light['chart-4']} 0)"]))
    for preset in ids["t"]:
        stage = f".st-{preset}"
        neutral = {m: T.neutral_tokens("theme", preset, m) for m in S.MODES}
        out.append(_rule(f".mx-dot.d-t-{preset}", [
            f"background:linear-gradient(135deg,{neutral['light']['background']} 50%,{neutral['dark']['background']} 0)"]))
        out.append(_rule(f"{_checked('t', preset)} {stage}", ["display:grid"]))
        out.append(_rule(stage, T.viz_decls("theme", preset, T.context_viz("theme", preset))))
        for viz in ids["v"][1:]:
            out.append(_rule(f"{_checked('v', viz)} {stage}", T.viz_decls("theme", preset, viz)))
        for accent in ids["a"][1:]:
            out.append(_rule(f"{_checked('a', accent)} {stage}", T.accent_decls("theme", preset, accent)))
        # One rule sets every ink for more contrast. It comes after the accent rules at equal specificity.
        out.append(_rule(f"{_checked('c', 'more')} {stage}", T.contrast_decls("theme", preset, None, contrast="more")))
        # Only these inks depend on the accent, so the two-:has rules set only them.
        for accent in ids["a"][1:]:
            decls = [d for d in T.contrast_decls("theme", preset, accent, contrast="more")
                     if d.split(":", 1)[0] in ACCENT_INKS]
            out.append(_rule(f"{_checked('c', 'more')}{_checked('a', accent)} {stage}", decls))
    out.append(_rule(f"{_checked('m', 'light')} .mx-stage", ["color-scheme:light"]))
    out.append(_rule(f"{_checked('m', 'dark')} .mx-stage", ["color-scheme:dark"]))
    for group, values in ids.items():
        for value in values:
            out.append(_rule(f"{_checked(group, value)} .sn-{group}-{value}", ["display:inline"]))
    return "".join(out)


def _option(group: str, value: str, label: str, checked: bool, dot: str | None = None) -> str:
    mark = f'<span class="mx-dot {dot}" aria-hidden="true"></span>' if dot else ""
    state = " checked" if checked else ""
    return (f'<label class="mx-opt"><input type="radio" name="mx-{group}" id="mx-{group}-{value}" '
            f'value="{_e(value)}"{state}>{mark}<span>{_e(label)}</span></label>')


def _group(label: str, options: list[str]) -> str:
    return (f'<div class="mx-group" role="radiogroup" aria-label="{_e(label)}">'
            f'<span class="mx-legend">{_e(label)}</span><div class="mx-opts">{"".join(options)}</div></div>')


def _stage(preset: str, doc: dict) -> str:
    roles = "".join(f'<div class="mx-role r-{role}">{_e(role.replace("-surface", ""))}</div>'
                    for role, _ in ROLES)
    seq = "".join(f'<span class="q-seq-{i}"></span>' for i in range(1, T.RAMP_SLOTS + 1))
    div = "".join(f'<span class="q-div-{i}"></span>' for i in range(1, T.RAMP_SLOTS + 1))
    bars = "".join(f'<span class="mx-bar c{i}" title="chart-{i}"></span>' for i in range(1, T.CHART_SLOTS + 1))
    return (
        f'<div class="mx-stage st-{_e(preset)}" data-ui-theme="{_e(preset)}" data-mixer-stage="{_e(preset)}">'
        f'<header><strong>{_e(doc.get("name", preset))}</strong><span>{_e(doc.get("description", ""))}</span></header>'
        f'<div class="mx-roles">{roles}</div>'
        '<div class="mx-row"><button data-slot="button" data-variant="primary" type="button" data-mixer-primary>Primary</button>'
        '<button data-slot="button" data-variant="secondary" type="button">Secondary</button>'
        '<button data-slot="button" data-variant="outline" type="button">Outline</button>'
        '<a class="mx-ink" href="#pigment">Primary ink link</a></div>'
        '<div class="mx-row"><span data-slot="badge" data-variant="success">Passing</span>'
        '<span data-slot="badge" data-variant="warning">At risk</span>'
        '<span data-slot="badge" data-variant="destructive">Blocked</span>'
        '<span data-slot="badge" data-variant="info">Note</span></div>'
        '<aside data-slot="callout" data-variant="info"><strong data-slot="callout-title">Solved contrast</strong>'
        '<p>Every role in this preset passes WCAG AA in light and dark mode.</p></aside>'
        f'<div class="mx-viz" role="img" aria-label="Eight chart colors">{bars}</div>'
        f'<div class="mx-ramp" role="img" aria-label="Sequential ramp">{seq}</div>'
        f'<div class="mx-ramp" role="img" aria-label="Diverging ramp">{div}</div>'
        '</div>'
    )


def _snippet() -> str:
    ids = _ids()
    parts = ['&lt;html data-ui-theme="']
    parts += [f'<span class="sn sn-t-{p}">{p}</span>' for p in ids["t"]]
    parts.append('"')
    parts += [f'<span class="sn sn-a-{a}"> data-ui-accent="{a}"</span>' for a in ids["a"][1:]]
    parts += [f'<span class="sn sn-v-{v}"> data-ui-viz="{v}"</span>' for v in ids["v"][1:]]
    parts += [f'<span class="sn sn-m-{m}"> data-ui-mode="{m}"</span>' for m in ("light", "dark")]
    parts.append('<span class="sn sn-c-more"> data-ui-contrast="more"</span>&gt;')
    return f'<pre class="mx-snippet" aria-label="HTML for this choice"><code>{"".join(parts)}</code></pre>'


def mixer_html() -> str:
    presets = T.presets()
    accents = T.accents()
    viz = T.viz_sets()
    controls = [
        _group("Preset", [_option("t", p, presets[p].get("name", p), p == PAGE_THEME, f"d-t-{p}")
                          for p in sorted(presets)]),
        _group("Accent", [_option("a", "none", "Preset accent", True)]
               + [_option("a", a, accents[a].get("name", a), False, f"d-{a}") for a in sorted(accents)]),
        _group("Viz set", [_option("v", "default", "Preset viz", True)]
               + [_option("v", v, viz[v].get("name", v), False, f"d-viz-{v}") for v in sorted(viz)]),
        _group("Mode", [_option("m", m, m.title(), m == "auto") for m in MODES]),
        _group("Contrast", [_option("c", c, c.title(), c == "normal") for c in T.CONTRASTS]),
    ]
    stages = "".join(_stage(p, presets[p]) for p in sorted(presets))
    return (
        '<section id="pigment" class="mx" aria-labelledby="pigment-title">'
        '<h2 id="pigment-title">Pigment mixer</h2>'
        f'<p class="g-lede">Pick a preset, an accent, a viz set, a mode and a contrast level. '
        f'{len(presets)} presets, {len(accents)} accents and {len(viz)} viz sets, all solved for WCAG. '
        'Copy the html tag to use the same choice on your page.</p>'
        f'<div class="mx-controls">{"".join(controls)}</div>'
        f'{_snippet()}<div class="mx-stages">{stages}</div></section>'
    )


def _item(item: dict, live: bool) -> str:
    tags = []
    for label, key in (("Variants", "variants"), ("Sizes", "sizes"), ("Subslots", "subslots")):
        if item.get(key):
            tags.append(f'<p>{label}: {_e(", ".join(item[key]))}</p>')
    body = f'<div class="g-live">{item["markup"]}</div>' if live else ""
    return (
        f'<article class="g-item" id="slot-{_e(item["slot"])}" data-gallery-slot="{_e(item["slot"])}">'
        f'<h3><code>{_e(item["slot"])}</code></h3><p>{_e(item["summary"])}</p>{"".join(tags)}{body}'
        f'<details><summary>Markup</summary><pre><code>{_e(item["markup"])}</code></pre></details>'
        '</article>'
    )


def _catalog_section(category: str, title: str, lede: str, live: bool, anchor: str | None = None) -> str:
    items = [i for i in tailplan_ui.catalog()["items"] if i["category"] == category]
    cards = "".join(_item(i, live) for i in items)
    anchor = anchor or category
    return (f'<section id="{anchor}" aria-labelledby="{anchor}-title">'
            f'<h2 id="{anchor}-title">{_e(title)} ({len(items)})</h2><p class="g-lede">{_e(lede)}</p>'
            f'<div class="g-grid">{cards}</div></section>')


def _app(label: str) -> str:
    rows = (("A", "Ada", "9:41", "Reader fits 382 by 580"), ("G", "Grace", "8:15", "Brief needs short screens"),
            ("L", "Linus", "Mon", "Dashboard is next"))
    body = "".join(
        f'<div data-slot="list-row"><span data-part="icon" data-shape="round">{i}</span>'
        f'<span data-part="title">{n}</span><span data-part="meta">{t}</span>'
        f'<span data-part="subtitle">{s}</span><span data-part="chevron"></span></div>' for i, n, t, s in rows)
    tabs = ('<span aria-current="page"><span data-part="icon">I</span>Inbox</span>'
            '<span><span data-part="icon">P</span>Plans</span><span><span data-part="icon">R</span>Reviews</span>')
    return (
        '<div data-slot="app-shell"><header data-slot="app-bar">'
        '<button type="button" data-part="action" aria-label="Back">‹</button>'
        f'<span data-part="title">{_e(label)}</span>'
        '<button type="button" data-part="action" aria-label="More">⋯</button></header>'
        f'<nav data-slot="sidebar" aria-label="Sections"><span data-part="title">Tailplan</span>{tabs}</nav>'
        f'<div data-part="body"><div data-slot="search"><span data-part="placeholder">Search plans</span></div>{body}</div>'
        f'<nav data-slot="tab-bar" aria-label="Tabs">{tabs}</nav></div>'
    )


def _frame(device: str, label: str) -> str:
    return (f'<div data-slot="device-frame" data-device="{device}" aria-label="{_e(label)}">'
            f'<div data-slot="device-screen">{_app("Inbox")}</div></div>')


def _seg(name: str, ctl: str, label: str, values) -> str:
    opts = "".join(
        f'<label><input type="radio" name="{name}" data-ctl="{ctl}" value="{v}"{" checked" if i == 0 else ""}>'
        f'{_e(text)}</label>' for i, (v, text) in enumerate(values))
    return (f'<div data-part="group" role="radiogroup" aria-label="{_e(label)}">'
            f'<span data-part="legend">{_e(label)}</span><span data-part="seg">{opts}</span></div>')


def _mockup_stage() -> str:
    controls = "".join((
        _seg("ui-stage-fidelity", "fidelity", "Fidelity", (("wire", "Wire"), ("graphite", "Hi-fi"))),
        _seg("ui-stage-mode", "mode", "Mode", (("auto", "Auto"), ("light", "Light"), ("dark", "Dark"))),
        _seg("ui-stage-viewport", "viewport", "Viewport", STAGE_VIEWPORTS),
        _seg("ui-stage-view", "view", "Compare", (("side", "Side by side"), ("before", "Before"), ("after", "After"))),
    ))
    panes = []
    for pane in ("before", "after"):
        variants = "".join(
            f'<div data-variant="{theme}" data-ui-theme="{theme}">'
            + "".join(_frame(d, f"{pane.title()} {d}") for d, _ in STAGE_VIEWPORTS) + "</div>"
            for theme in ("wire", "graphite"))
        panes.append(f'<div data-part="pane" data-pane="{pane}"><div data-part="caption">{pane.title()}</div>'
                     f'{variants}</div>')
    return (f'<div data-slot="mockup-stage"><div data-part="controls">{controls}</div>'
            f'<div data-part="preview"><div data-part="panes">{"".join(panes)}</div></div></div>')


def wireframes_html() -> str:
    figures = "".join(
        f'<figure data-example="{d}"><figcaption><strong>{d}</strong> {size}. {_e(text)}</figcaption>'
        f'<div data-slot="ruler-mark" style="--ruler:{ruler}">{size.split(" ")[0]} pt</div>'
        f'{_frame(d, d + " device frame")}</figure>'
        for d, size, text, ruler in FRAMES)
    return (
        '<section id="wireframes" aria-labelledby="wireframes-title">'
        '<h2 id="wireframes-title">Wireframes</h2>'
        '<p class="g-lede">Device frames shrink to fit the page. The app layout follows the screen, not the page.</p>'
        f'<div class="g-frames">{figures}</div>'
        '<h3>Mockup stage</h3><p class="g-lede">Compare wire and graphite fidelity with four radio switches.</p>'
        f'{_mockup_stage()}</section>'
    )


def _header(base: str) -> str:
    doc = tailplan_ui.catalog()
    counts = {c: sum(1 for i in doc["items"] if i["category"] == c) for c in ("primitives", "blocks", "wireframes")}
    nav = "".join(f'<a href="#{i}">{t}</a>' for i, t in (
        ("pigment", "Pigment"), ("primitives", "Primitives"), ("blocks", "Blocks"),
        ("wireframes", "Wireframes"), ("catalog", "Wireframe slots"), ("cli", "CLI")))
    return (
        '<header data-slot="doc-header">'
        f'<p data-slot="doc-eyebrow">Tailplan UI {_e(UI_VERSION)}</p>'
        '<h1 data-slot="doc-title">Tailplan UI</h1>'
        '<p data-slot="doc-subtitle">Zero-JS primitives, plan blocks and wireframes with solved color. '
        'Add one meta tag and the bundler ships only what the page uses.</p>'
        f'<ul data-slot="doc-meta"><li>{counts["primitives"]} primitives</li><li>{counts["blocks"]} blocks</li>'
        f'<li>{counts["wireframes"]} wireframe slots</li><li><a href="{base}/">Tailplan home</a></li></ul>'
        f'</header><nav class="g-nav" aria-label="Sections">{nav}</nav>'
    )


def _cli() -> str:
    return ('<section id="cli" aria-labelledby="cli-title"><h2 id="cli-title">Command line</h2>'
            '<p class="g-lede">The same catalog works offline. The commands need no server and no network.</p>'
            f'<pre data-slot="code"><code>{_e(CLI)}</code></pre></section>')


def source_html(base_url: str = "") -> str:
    """Return the gallery page before the bundler adds Tailplan UI CSS."""
    base = _e(base_url.rstrip("/"))
    body = "".join((
        _header(base),
        mixer_html(),
        _catalog_section("primitives", "Primitives", "Each card shows the live slot and its copyable markup.", True),
        _catalog_section("blocks", "Blocks", "Plan blocks for briefs, decisions and reviews.", True),
        wireframes_html(),
        _catalog_section("wireframes", "Wireframe slots",
                         "Markup for every wireframe slot. The live frames are above.", False, "catalog"),
        _cli(),
    ))
    return (
        f'<!doctype html><html lang="en" data-ui-theme="{PAGE_THEME}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="tailplan-ui" content="1"><title>Tailplan UI</title>'
        f'<style>{GALLERY_CSS}{mixer_css()}</style></head>'
        f'<body><main class="g">{body}</main></body></html>'
    )


@lru_cache(maxsize=4)
def render_gallery(base_url: str = "") -> str:
    """Return the bundled gallery page. The result is cached, because the catalog never changes at run time."""
    return tailplan_ui.bundle(source_html(base_url), target="standalone")[0]
