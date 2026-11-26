"""The tailplan ui command line. It uses only the standard library and works offline.

Run it as `tailplan ui ...` or as `python3 -m tailplan_ui ...`.
Usage errors exit 2. Failed checks exit 1.
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from . import lint as L
from .bundler import TARGETS, UI_VERSION, bundle, is_hue, scan, strip_stamp
from .color import solve as S
from .color import tokens as T
from .color import validate as V
from .registry import CATEGORIES, catalog, get_item, known_slots

PROG = "tailplan ui"
MODES = ("auto", "light", "dark")
ROLE_KEYS = ("background", "foreground", "card", "muted", "border", "primary", "primary-foreground",
             "primary-ink", "accent", "success", "warning", "destructive", "info")
SEED_KEYS = ("primary", "primary-foreground", "primary-ink", "primary-edge", "ring",
             "accent", "accent-surface", "accent-border")
JS_URL_RE = re.compile(r"^\s*(?:javascript|vbscript)\s*:", re.I)


class UsageError(Exception):
    """A bad argument. The command exits 2."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _json(value) -> None:
    json.dump(value, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _swatch(value: str) -> str:
    """Return a color block and the hex value. The block shows only on a color terminal."""
    if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
        return value
    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    return f"\x1b[48;2;{r};{g};{b}m  \x1b[0m {value}"


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise UsageError(f"file not found: {path}") from None
    except (OSError, UnicodeError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from None


def _seed(value: str):
    """Return a hex seed or a numeric hue. Raise UsageError on anything else."""
    if is_hue(value) and 0 <= float(value) <= 360:
        return float(value)
    if S.is_hex(value):
        return S.normalize_hex(value)
    raise UsageError(f"seed must be a hex color or a hue (#2563eb, or 0 to 360): {value!r}")


def _context(theme: str | None, surface: str | None) -> tuple[str, str, str | None]:
    """Return (kind, id, pinned mode) for --theme or --surface. The default is the default theme."""
    if theme and surface:
        raise UsageError("give --theme or --surface, not both")
    if surface:
        if surface not in T.surfaces():
            raise UsageError(_unknown("surface", surface, T.surfaces()))
        return "surface", surface, None
    name = theme or T.DEFAULT_THEME
    try:
        ident, pinned = T.resolve_theme(name)
    except KeyError:
        raise UsageError(_unknown("theme", name, list(T.presets()) + list(T.THEME_ALIASES))) from None
    return "theme", ident, pinned


def _unknown(kind: str, value: str, choices) -> str:
    close = difflib.get_close_matches(value, sorted(choices), n=1)
    hint = f' Did you mean "{close[0]}"?' if close else ""
    return f'unknown {kind} "{value}".{hint} Run "{PROG} color presets" for the list.'


# list and show

def cmd_list(args) -> int:
    doc = catalog()
    items = [i for i in doc["items"] if not args.category or i["category"] == args.category]
    if args.json:
        _json({"version": doc["version"], "count": len(items), "items": items})
        return 0
    width = max((len(i["slot"]) for i in items), default=4)
    for category in CATEGORIES:
        group = [i for i in items if i["category"] == category]
        if not group:
            continue
        _out(f"{category} ({len(group)})")
        for item in group:
            _out(f"  {item['slot']:<{width}}  {item['summary']}")
    _out(f'\nRun "{PROG} show SLOT" for markup.')
    return 0


def cmd_show(args) -> int:
    item = get_item(args.slot)
    if item is None:
        close = difflib.get_close_matches(args.slot, sorted(known_slots()), n=1)
        hint = f' Did you mean "{close[0]}"?' if close else ""
        message = f'unknown slot "{args.slot}".{hint}'
        if args.json:
            _json({"ok": False, "error": message, "suggestion": close[0] if close else None})
        else:
            print(f"{PROG}: {message}", file=sys.stderr)
        return 2
    if args.json:
        _json(item)
        return 0
    _out(f"{item['slot']}  ({item['category']}, chunk {item['chunk']})")
    _out(item["summary"])
    for label, key in (("Variants", "variants"), ("Subslots", "subslots"), ("Sizes", "sizes")):
        if item.get(key):
            _out(f"{label}: {', '.join(item[key])}")
    _out("")
    _out(item["markup"])
    return 0


# init

STARTER = """<!doctype html>
<html lang="en"{attrs}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="tailplan-ui" content="1">
<title>{title}</title>
<style>
main {{ max-width: 72rem; margin: 0 auto; padding: var(--gutter); display: grid; gap: calc(var(--space) * 2); }}
</style>
</head>
<body>
<main>
<header data-slot="doc-header">
<p data-slot="doc-eyebrow">Plan</p>
<h1 data-slot="doc-title">{title}</h1>
<p data-slot="doc-subtitle">One sentence on the problem and the proposed change.</p>
<ul data-slot="doc-meta"><li>Owner: you</li><li>Status: draft</li></ul>
</header>
<section data-slot="verdict" data-variant="success">
<span data-slot="verdict-label">Recommendation</span>
<h2 data-slot="verdict-title">Ship option A in two waves</h2>
<p data-slot="verdict-body">State the decision and the main reason.</p>
</section>
<div data-slot="stat-strip">
<div data-slot="stat"><span data-slot="stat-label">Pages</span><span data-slot="stat-value">12</span><span data-slot="stat-delta" data-trend="up" data-variant="success">+3</span></div>
<div data-slot="stat"><span data-slot="stat-label">Risk</span><span data-slot="stat-value">Low</span></div>
</div>
<ul data-slot="option-grid">
<li data-slot="option" data-recommended><span data-slot="option-tag">Pick</span><h3 data-slot="option-title">Option A</h3><p data-slot="option-body">Small change, fast rollout.</p></li>
<li data-slot="option"><h3 data-slot="option-title">Option B</h3><p data-slot="option-body">Larger change, more control.</p></li>
</ul>
<ol data-slot="wave-flow">
<li data-slot="wave" data-state="active"><span data-slot="wave-label">Wave 1</span><strong data-slot="wave-title">Foundation</strong><p data-slot="wave-body">Tokens and tests.</p></li>
<li data-slot="wave"><span data-slot="wave-label">Wave 2</span><strong data-slot="wave-title">Rollout</strong><p data-slot="wave-body">Ship to every page.</p></li>
</ol>
<aside data-slot="callout" data-variant="info"><strong data-slot="callout-title">Next step</strong><p>Run tailplan ui verify on this file, then publish it.</p></aside>
<ol data-slot="decisions">
<li data-slot="decision" data-state="accepted"><span data-slot="decision-title">Use Tailplan UI</span><span data-slot="decision-meta">Today</span><p data-slot="decision-reason">Solved contrast in light and dark mode.</p></li>
</ol>
<ol data-slot="sources">
<li data-slot="source" id="src-1"><a data-slot="source-title" href="https://example.com">Source title</a><span data-slot="source-meta">Publisher, 2026</span></li>
</ol>
</main>
</body>
</html>
"""


def init_attrs(args) -> list[tuple[str, str]]:
    """Return the data-ui attributes for the starter html element. Raise UsageError on bad input."""
    attrs = []
    if args.theme and args.surface:
        raise UsageError("give --theme or --surface, not both")
    if args.accent and args.seed:
        raise UsageError("give --accent or --seed, not both")
    if args.theme:
        _context(args.theme, None)
        attrs.append(("theme", args.theme))
    if args.surface:
        _context(None, args.surface)
        attrs.append(("surface", args.surface))
    if args.accent:
        if args.accent not in T.accents():
            raise UsageError(_unknown("accent", args.accent, T.accents()))
        attrs.append(("accent", args.accent))
    if args.seed:
        if not S.is_hex(args.seed):
            raise UsageError(f"--seed must be a hex color such as #2563eb: {args.seed!r}")
        attrs.append(("seed", S.normalize_hex(args.seed)))
    if args.viz:
        if args.viz not in T.viz_sets():
            raise UsageError(_unknown("viz set", args.viz, T.viz_sets()))
        attrs.append(("viz", args.viz))
    if args.mode and args.mode != "auto":
        attrs.append(("mode", args.mode))
    return attrs


def starter_html(title: str, attrs: list[tuple[str, str]]) -> str:
    """Return the starter page for tailplan ui init."""
    text = "".join(f' data-ui-{k}="{html.escape(v, quote=True)}"' for k, v in attrs)
    return STARTER.format(attrs=text, title=html.escape(title))


def cmd_init(args) -> int:
    path = Path(args.file)
    attrs = init_attrs(args)
    if path.exists() and not args.force:
        print(f"{PROG}: {path} exists. Use --force to replace it.", file=sys.stderr)
        return 1
    path.write_text(starter_html(args.title, attrs), encoding="utf-8")
    _out(f"Wrote {path}")
    _out(f"Preview: {PROG} bundle {path} -o preview.html")
    _out(f"Check:   {PROG} verify {path}")
    return 0


# bundle

def cmd_bundle(args) -> int:
    if args.in_place and args.output:
        raise UsageError("give -o or --in-place, not both")
    if args.in_place and args.input == "-":
        raise UsageError("--in-place needs a file, not stdin")
    source = _read(args.input)
    out, meta = bundle(source, target=args.target)
    path = args.input if args.in_place else (args.output if args.output and args.output != "-" else None)
    if path:
        Path(path).write_text(out, encoding="utf-8")
    report = {**meta, "input": args.input, "output": path or "-",
              "inputBytes": len(source.encode("utf-8")), "outputBytes": len(out.encode("utf-8"))}
    if args.json:
        _json(report if path else {**report, "html": out})
    else:
        if not path:
            sys.stdout.write(out)
        if args.metadata:
            # The legacy flag prints metadata to stdout with a file, else to stderr.
            stream = sys.stdout if path else sys.stderr
            json.dump(meta, stream, indent=2)
            print(file=stream)
        elif path:
            _out(f"Wrote {path}: {report['outputBytes']} bytes, {meta['uiCssBytes']} of them Tailplan UI CSS.")
    for warning in meta["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


# verify

class _SafetyParser(HTMLParser):
    """Find script, form, event handler, and javascript: URL use in real markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[dict] = []

    def _add(self, code: str, message: str, fix: str) -> None:
        self.errors.append({"code": code, "message": message, "fix": fix, "line": self.getpos()[0]})

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._add("ui-script", "The page has a script element.", "Remove it. Tailplan UI needs no JavaScript.")
        elif tag == "form":
            self._add("ui-form", "The page has a form element.", "Remove it. Use native inputs without a form.")
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                self._add("ui-event-handler", f"The {tag} element has an {name} attribute.", "Remove the handler.")
            elif name in ("href", "src", "action", "formaction") and value and JS_URL_RE.match(value):
                self._add("ui-javascript-url", f"The {tag} element has a javascript: URL.", "Use a real link.")

    handle_startendtag = handle_starttag


def verify_html(source: str) -> dict:
    """Return the verify report for one page: errors, warnings, and the scanned features."""
    parser = _SafetyParser()
    parser.feed(source)
    parser.close()
    errors = parser.errors
    warnings = [dict(item) for item in L.lint(source)]
    features = scan(strip_stamp(source))
    stamped = 'data-tailplan-ui="' in source
    outputs = {t: bundle(source, target=t) for t in (TARGETS if stamped else TARGETS[:1])}
    # The bundler also checks the root attributes, so its warnings cover more than scan.
    for message in dict.fromkeys(outputs[TARGETS[0]][1]["warnings"]):
        warnings.append({"code": "ui-attribute", "message": message,
                         "fix": f'Run "{PROG} color presets" for the valid values.'})
    for key in features.meta:
        if key not in ("theme", "surface", "accent", "seed", "hue", "viz", "mode", "contrast"):
            warnings.append({"code": "ui-meta-key", "message": f"Unknown tailplan-ui meta key: {key}"})
    if not features.opted_in:
        warnings.append({"code": "ui-not-opted-in", "message": "The page has no tailplan-ui meta tag, so it gets no Tailplan UI CSS.",
                         "fix": 'Add <meta name="tailplan-ui" content="1"> to the head.'})
    if stamped and source not in {out for out, _ in outputs.values()}:
        warnings.append({"code": "ui-stale-bundle", "message": "The Tailplan UI style block is out of date.",
                         "fix": f"Run {PROG} bundle FILE --in-place."})
    return {
        "ok": not errors and not warnings, "uiVersion": UI_VERSION, "optedIn": features.opted_in,
        "bundled": stamped, "slotCount": features.slot_count, "slots": dict(sorted(features.slots.items())),
        "errors": errors, "warnings": warnings,
    }


def cmd_verify(args) -> int:
    report = verify_html(_read(args.file))
    report["file"] = args.file
    failed = bool(report["errors"]) or (args.strict and bool(report["warnings"]))
    report["ok"] = not failed and (args.strict or not report["errors"])
    if args.json:
        _json(report)
    else:
        for kind, items in (("error", report["errors"]), ("warning", report["warnings"])):
            for item in items:
                where = f"{args.file}:{item['line']}: " if item.get("line") else f"{args.file}: "
                fix = f" Fix: {item['fix']}" if item.get("fix") else ""
                _out(f"{where}{kind} {item['code']}: {item['message']}{fix}")
        _out(f"{args.file}: {len(report['errors'])} errors, {len(report['warnings'])} warnings, "
             f"{report['slotCount']} slots.")
    return 1 if failed else 0


# color

def _mode_check(kind: str, ident: str, *, accent=None, viz=None) -> list[str]:
    failures = []
    for mode in S.MODES:
        for contrast in T.CONTRASTS:
            tokens = T.mode_tokens(kind, ident, mode, accent=accent, viz=viz, contrast=contrast)
            failures += V.check_mode(tokens, contrast=contrast, label=f"{kind}:{ident}:{mode}:{contrast}:")
    return failures


def _roles(kind: str, ident: str, *, accent=None) -> dict:
    return {mode: {k: v for k, v in T.mode_tokens(kind, ident, mode, accent=accent).items() if k in ROLE_KEYS}
            for mode in S.MODES}


def cmd_presets(args) -> int:
    presets = []
    for ident, doc in sorted(T.presets().items()):
        presets.append({"id": ident, "name": doc.get("name", ident), "description": doc.get("description", ""),
                        "viz": T.context_viz("theme", ident), "accent": doc.get("accentName"),
                        "modes": _roles("theme", ident), "passes": not _mode_check("theme", ident)})
    surfaces = []
    for ident, doc in sorted(T.surfaces().items()):
        surfaces.append({"id": ident, "name": doc.get("name", ident), "accent": doc["accent"],
                         "viz": T.context_viz("surface", ident),
                         "modes": _roles("surface", ident), "passes": not _mode_check("surface", ident)})
    accents = []
    for ident, doc in sorted(T.accents().items()):
        solved = {mode: T.accent_tokens("theme", T.DEFAULT_THEME, ident, mode) for mode in S.MODES}
        accents.append({"id": ident, "name": doc.get("name", ident), "seed": {m: doc[m] for m in S.MODES},
                        "modes": {m: {k: solved[m][k] for k in SEED_KEYS} for m in S.MODES},
                        "passes": not _mode_check("theme", T.DEFAULT_THEME, accent=ident)})
    viz = []
    for ident, doc in sorted(T.viz_sets().items()):
        modes = {}
        failures = []
        for mode in S.MODES:
            tokens = T.viz_tokens("theme", T.DEFAULT_THEME, ident, mode)
            charts = [tokens[f"chart-{i}"] for i in range(1, T.CHART_SLOTS + 1)]
            modes[mode] = charts
            failures += V.check_cvd(charts)
        viz.append({"id": ident, "name": doc.get("name", ident), "note": doc.get("note", ""),
                    "modes": modes, "passes": not failures})
    doc = {"presets": presets, "surfaces": surfaces, "accents": accents, "viz": viz,
           "aliases": {k: {"theme": v[0], "mode": v[1]} for k, v in T.THEME_ALIASES.items()}}
    if args.json:
        _json(doc)
        return 0

    def status(entry):
        return "pass" if entry["passes"] else "FAIL"

    def line(entry, keys=("background", "foreground", "primary")):
        cells = []
        for mode in S.MODES:
            cells.append(mode + " " + " ".join(_swatch(entry["modes"][mode][k]) for k in keys))
        return "  ".join(cells)

    _out(f"Presets ({len(presets)}): data-ui-theme")
    for entry in presets:
        _out(f"  {entry['id']:<15} {status(entry)}  {line(entry)}")
    _out(f"Surfaces ({len(surfaces)}): data-ui-surface")
    for entry in surfaces:
        _out(f"  {entry['id']:<15} {status(entry)}  {line(entry)}")
    _out(f"Accents ({len(accents)}): data-ui-accent")
    for entry in accents:
        _out(f"  {entry['id']:<15} {status(entry)}  {line(entry, ('primary', 'primary-foreground', 'primary-ink'))}")
    _out(f"Viz sets ({len(viz)}): data-ui-viz")
    for entry in viz:
        _out(f"  {entry['id']:<15} {status(entry)}  light " + " ".join(_swatch(c) for c in entry["modes"]["light"]))
    _out("Aliases pin one mode: " + ", ".join(f"{k} = {v['theme']} {v['mode']}" for k, v in doc["aliases"].items()))
    return 0


def seed_report(seed, kind: str, ident: str, contrast: str) -> dict:
    """Solve a seed against one context. Return the tokens and their WCAG ratios for each mode."""
    modes = {}
    for mode in S.MODES:
        neutral = T.neutral_tokens(kind, ident, mode)
        solved = T.accent_tokens(kind, ident, seed, mode, contrast)
        bg = neutral["background"]
        ratios = {
            "primary-foreground/primary": S.contrast_ratio(solved["primary-foreground"], solved["primary"]),
            "primary-ink/background": S.contrast_ratio(solved["primary-ink"], bg),
            "primary-ink/card": S.contrast_ratio(solved["primary-ink"], neutral["card"]),
            "primary-edge/background": S.contrast_ratio(solved["primary-edge"], bg),
            "ring/background": S.contrast_ratio(solved["ring"], bg),
        }
        modes[mode] = {"background": bg, "card": neutral["card"],
                       "tokens": {k: solved[k] for k in SEED_KEYS},
                       "contrast": {k: round(v, 2) for k, v in ratios.items()}}
    failures = []
    for mode in S.MODES:
        tokens = T.mode_tokens(kind, ident, mode, accent=seed, contrast=contrast)
        failures += V.check_mode(tokens, contrast=contrast, label=f"{mode}:")
    return {"seed": seed, "context": {"kind": kind, "id": ident}, "contrast": contrast,
            "modes": modes, "passes": not failures, "failures": failures}


def cmd_seed(args) -> int:
    seed = _seed(args.seed)
    kind, ident, _ = _context(args.theme, args.surface)
    report = seed_report(seed, kind, ident, args.contrast)
    if args.json:
        _json(report)
        return 0 if report["passes"] else 1
    label = f"hue {seed:g}" if isinstance(seed, float) else seed
    _out(f"Seed {label} on {kind} {ident}, contrast {args.contrast}")
    for mode, entry in report["modes"].items():
        _out(f"{mode} (background {entry['background']})")
        for key, value in entry["tokens"].items():
            _out(f"  --{key:<19} {_swatch(value)}")
        for key, value in entry["contrast"].items():
            _out(f"  {key:<28} {value:.2f}:1")
    _out("pass" if report["passes"] else "FAIL: " + "; ".join(report["failures"]))
    return 0 if report["passes"] else 1


def cmd_check(args) -> int:
    if args.preset and args.surface:
        raise UsageError("give --preset or --surface, not both")
    if args.accent and args.seed:
        raise UsageError("give --accent or --seed, not both")
    if args.preset:
        contexts = [_context(args.preset, None)[:2]]
    elif args.surface:
        contexts = [_context(None, args.surface)[:2]]
    else:
        contexts = list(V.contexts())
    if args.seed:
        accents = [_seed(args.seed)]
    elif args.accent:
        if args.accent not in T.accents():
            raise UsageError(_unknown("accent", args.accent, T.accents()))
        accents = [args.accent]
    else:
        accents = [None, *T.accents()]
    checked = 0
    failures: list[str] = []
    for kind, ident in contexts:
        for accent in accents:
            failures += _mode_check(kind, ident, accent=accent)
            checked += 1
        for viz in T.viz_sets():
            for mode in S.MODES:
                tokens = T.mode_tokens(kind, ident, mode, viz=viz)
                label = f"{kind}:{ident}:{mode}:{viz}:"
                failures += V.check_mode(tokens, label=label)
                charts = [tokens[f"chart-{i}"] for i in range(1, T.CHART_SLOTS + 1)]
                failures += V.check_cvd(charts, label=label)
            checked += 1
    report = {"ok": not failures, "contexts": [f"{k}:{i}" for k, i in contexts], "checked": checked,
              "failures": failures}
    if args.json:
        _json(report)
    else:
        for item in failures:
            _out(item)
        _out(f"{'pass' if not failures else 'FAIL'}: {len(contexts)} contexts, {checked} token sets, "
             f"{len(failures)} failures.")
    return 1 if failures else 0


def cmd_solve(args) -> int:
    """Print the raw solver output for a seed. Kept for older scripts."""
    result = S.solve_accent(_seed(args.seed), args.light_bg, args.dark_bg, contrast=args.contrast)
    _json(result)
    return 0


# parser

def build_parser(prog: str = PROG) -> argparse.ArgumentParser:
    parser = _Parser(prog=prog, description="Browse, start, bundle, and check Tailplan UI pages offline.")
    parser.add_argument("--version", action="version", version=f"tailplan-ui {UI_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser, metavar="COMMAND")

    p = sub.add_parser("list", help="list the catalog slots")
    p.add_argument("--category", choices=CATEGORIES)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one slot with copyable markup")
    p.add_argument("slot")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("init", help="write an opt-in starter page")
    p.add_argument("file")
    p.add_argument("--theme", help="preset id, such as graphite")
    p.add_argument("--surface", help="neutral surface id, such as slate")
    p.add_argument("--accent", help="accent id, such as cobalt")
    p.add_argument("--seed", help="accent seed hex, such as #2563eb")
    p.add_argument("--viz", help="viz set id, such as okabe-ito")
    p.add_argument("--mode", choices=MODES, default="auto")
    p.add_argument("--title", default="Untitled plan")
    p.add_argument("--force", action="store_true", help="replace an existing file")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("bundle", help="stamp the Tailplan UI style block into a page")
    p.add_argument("input", help="HTML file, or - for stdin")
    p.add_argument("-o", "--output", help="output file (default: stdout)")
    p.add_argument("--in-place", action="store_true", help="replace the input file")
    p.add_argument("--target", choices=TARGETS, default="standalone")
    p.add_argument("--json", action="store_true", help="print metadata and byte counts as JSON")
    p.add_argument("--metadata", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_bundle)

    p = sub.add_parser("verify", help="check a page offline: lint, slots, no script, no form")
    p.add_argument("file", help="HTML file, or - for stdin")
    p.add_argument("--strict", action="store_true", help="fail on warnings too")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    color = sub.add_parser("color", help="Pigment presets, seed solving, and contrast checks")
    csub = color.add_subparsers(dest="color_command", required=True, parser_class=_Parser, metavar="COMMAND")
    p = csub.add_parser("presets", help="list presets, surfaces, accents, and viz sets")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_presets)
    p = csub.add_parser("seed", help="solve accent tokens for a hex seed or a hue")
    p.add_argument("seed", help="hex color, such as #ffd400, or a hue from 0 to 360")
    p.add_argument("--surface")
    p.add_argument("--theme")
    p.add_argument("--contrast", choices=T.CONTRASTS, default="normal")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_seed)
    p = csub.add_parser("check", help="check WCAG contrast and CVD separation")
    p.add_argument("--preset")
    p.add_argument("--surface")
    p.add_argument("--accent")
    p.add_argument("--seed")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("solve", help=argparse.SUPPRESS)
    p.add_argument("seed")
    p.add_argument("--light-bg", default="#faf6ee")
    p.add_argument("--dark-bg", default="#1c1b18")
    p.add_argument("--contrast", choices=T.CONTRASTS, default="normal")
    p.set_defaults(func=cmd_solve)
    sub._choices_actions = [a for a in sub._choices_actions if a.dest != "solve"]
    return parser


def main(argv=None, prog: str = PROG) -> int:
    parser = build_parser(prog)
    try:
        args = parser.parse_args(argv)
        return args.func(args)
    except UsageError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        if "--json" in (argv if argv is not None else sys.argv[1:]):
            _json({"ok": False, "error": str(exc)})
        return 2
    except BrokenPipeError:
        return 0
