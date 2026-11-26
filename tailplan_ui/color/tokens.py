"""Build Pigment design tokens and CSS rules from presets and primitives."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import solve as S

ROOT = Path(__file__).resolve().parents[1]
PALETTES = ROOT / "palettes"
PRIMITIVES = Path(__file__).with_name("primitives.json")
DEFAULT_THEME = "warm-editorial"
DEFAULT_VIZ = "editorial"
CONTRASTS = ("normal", "more")
CHART_SLOTS = 8
RAMP_SLOTS = 5

# Attribute aliases for retired single-mode theme ids. They pin one mode.
THEME_ALIASES = {
    "flexoki-light": ("flexoki", S.LIGHT),
    "catppuccin-latte": ("catppuccin", S.LIGHT),
    "gruvbox-light": ("gruvbox", S.LIGHT),
    "solarized-light": ("solarized", S.LIGHT),
}
# The full map from tailplan_themes ids, for the Wave U3 migration.
LEGACY_ALIASES = {
    **THEME_ALIASES,
    "warm-editorial": ("warm-editorial", S.LIGHT),
    "tokyo-night": ("tokyo-night", S.DARK),
    "nord": ("nord", S.DARK),
    "dracula": ("dracula", S.DARK),
}

NEUTRAL_KEYS = (
    "background", "foreground", "card", "card-foreground", "popover", "popover-foreground",
    "muted", "muted-foreground", "secondary", "secondary-foreground", "border", "border-subtle",
    "border-strong", "input", "code-background", "code-foreground", "heading", "text", "surface",
)
STATUS_KEYS = ("destructive", "warning", "success", "info")
ACCENT_KEYS = (
    "primary", "primary-foreground", "primary-edge", "primary-ink", "accent", "accent-foreground",
    "accent-surface", "accent-border", "ring",
)
VIZ_KEYS = (tuple(f"chart-{i}" for i in range(1, CHART_SLOTS + 1))
            + tuple(f"seq-{i}" for i in range(1, RAMP_SLOTS + 1))
            + tuple(f"div-{i}" for i in range(1, RAMP_SLOTS + 1)))
# Ink tokens change with data-ui-contrast="more".
INK_KEYS = ("foreground", "card-foreground", "popover-foreground", "secondary-foreground",
            "muted-foreground", "code-foreground", "heading", "text") + STATUS_KEYS


@lru_cache(maxsize=1)
@lru_cache(maxsize=1)
def presets() -> dict[str, dict]:
    """Return the designer presets keyed by id."""
    out = {}
    for path in sorted(PALETTES.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        out[doc["id"]] = doc
    return out


@lru_cache(maxsize=1)
def primitives() -> dict:
    """Return surfaces, ramps, accents, and viz sets."""
    return json.loads(PRIMITIVES.read_text(encoding="utf-8"))


def surfaces() -> dict[str, dict]:
    """Return the neutral surface ramps keyed by id."""
    return primitives()["surfaces"]


def accents() -> dict[str, dict]:
    """Return the curated accent hues keyed by id."""
    return primitives()["accents"]


def viz_sets() -> dict[str, dict]:
    """Return the data-viz scales keyed by id."""
    return primitives()["viz"]


def resolve_theme(name: str) -> tuple[str, str | None]:
    """Return the preset id and the pinned mode for a theme name or alias."""
    if name in THEME_ALIASES:
        return THEME_ALIASES[name]
    if name in presets():
        return name, None
    raise KeyError(name)


def legacy_theme(old_id: str) -> dict[str, str]:
    """Return the new theme and pinned mode for a tailplan_themes id."""
    theme, mode = LEGACY_ALIASES[old_id]
    return {"theme": theme, "mode": mode}


# Raw per-mode colors for each context.

def _surface_raw(surface_id: str, mode: str) -> dict[str, str]:
    spec = surfaces()[surface_id]
    ramp = primitives()["ramps"][spec["ramp"]][mode]
    H, C = spec["hue"], spec["chroma"]
    raw = {key: S.oklch_hex(L, C, H) for key, L in ramp.items()}
    raw["code-foreground"] = raw["foreground"]
    status_L = 0.52 if mode == S.LIGHT else 0.74
    for key, value in primitives()["status"].items():
        chroma = 0 if C == 0 and spec["ramp"] == "wire" else value["chroma"]
        raw[key] = S.oklch_hex(status_L, chroma, value["hue"])
    accent = accents()[spec["accent"]]
    raw["accent"] = accent[mode]
    return raw


def context_raw(kind: str, ident: str, mode: str) -> dict[str, str]:
    """Return the raw colors of a theme or surface for one mode."""
    if kind == "theme":
        return dict(presets()[ident]["modes"][mode])
    return _surface_raw(ident, mode)


def context_style(kind: str, ident: str) -> dict[str, str]:
    """Return radius and line style for a context."""
    spec = presets()[ident] if kind == "theme" else surfaces()[ident]
    return {"radius": spec.get("radius", "8px"), "line": spec.get("line", "solid")}


def context_viz(kind: str, ident: str) -> str:
    """Return the default viz set for a context."""
    if kind == "theme":
        return presets()[ident].get("viz", DEFAULT_VIZ)
    return "monochrome" if surfaces()[ident]["ramp"] == "wire" else DEFAULT_VIZ


def context_accent_seed(kind: str, ident: str) -> dict[str, str]:
    """Return the default accent seed for each mode."""
    return {mode: context_raw(kind, ident, mode)["accent"] for mode in S.MODES}


# Solved tokens.

def _soft_surface(background: str, color: str, mode: str) -> str:
    return S.mix_oklab(background, color, 0.12 if mode == S.LIGHT else 0.2)


@lru_cache(maxsize=512)
def neutral_tokens(kind: str, ident: str, mode: str, contrast: str = "normal") -> dict[str, str]:
    """Return solved neutral and status tokens for one context and mode."""
    raw = context_raw(kind, ident, mode)
    target = S.MORE_SOLVE if contrast == "more" else S.TEXT_TARGET
    bg, card, muted = (S.normalize_hex(raw[k]) for k in ("background", "card", "muted"))
    toward = S.DARK if mode == S.LIGHT else S.LIGHT
    planes = [bg, card, muted]

    def ink(value: str, against=planes) -> str:
        return S.solve_lightness(value, against, target, toward=toward)

    fg = ink(raw["foreground"])
    code_bg = S.normalize_hex(raw["code-background"])
    border = S.normalize_hex(raw["border"])
    out = {
        "background": bg, "foreground": fg, "card": card, "card-foreground": fg,
        "popover": card, "popover-foreground": fg, "muted": muted,
        "muted-foreground": ink(raw["muted-foreground"]), "secondary": muted,
        "secondary-foreground": fg, "border": border,
        "border-subtle": S.mix_oklab(border, bg, 0.5),
        "border-strong": S.normalize_hex(raw["border-strong"]),
        "input": S.normalize_hex(raw["border-strong"]),
        "code-background": code_bg,
        "code-foreground": ink(raw["code-foreground"], [code_bg]),
        "heading": ink(raw.get("heading", raw["foreground"])),
        "text": fg, "surface": muted,
    }
    for key in STATUS_KEYS:
        seed = S.normalize_hex(raw[key])
        surface = _soft_surface(bg, seed, mode)
        out[f"{key}-surface"] = surface
        out[key] = ink(seed, [bg, card, surface])
    return out


def _accent_seed(spec) -> object:
    """Turn an accent spec into a solver seed."""
    if isinstance(spec, dict):
        return {mode: spec[mode] for mode in S.MODES}
    if isinstance(spec, str) and spec in accents():
        entry = accents()[spec]
        return {mode: entry[mode] for mode in S.MODES}
    return spec


def _freeze(spec) -> object:
    if isinstance(spec, dict):
        return tuple(sorted(spec.items()))
    return spec


@lru_cache(maxsize=2048)
def _accent_tokens(kind: str, ident: str, spec_key, mode: str, contrast: str) -> dict[str, str]:
    spec = dict(spec_key) if isinstance(spec_key, tuple) else spec_key
    seed = _accent_seed(spec)
    seed = seed[mode] if isinstance(seed, dict) else seed
    neutral = neutral_tokens(kind, ident, mode)
    return S.solve_mode(seed, neutral["background"], neutral["card"], mode=mode, contrast=contrast)


def accent_tokens(kind: str, ident: str, spec, mode: str, contrast: str = "normal") -> dict[str, str]:
    """Solve an accent against a context for one mode.

    The spec is an accent id, a hex seed, a hue, or a per-mode dict.
    """
    return _accent_tokens(kind, ident, _freeze(spec), mode, contrast)


def _mono_scale(against: list[str], mode: str) -> list[str]:
    """Return eight grays that each pass the chart target."""
    if mode == S.LIGHT:
        edge = S.solve_lightness("#bdbdbd", against, S.EDGE_TARGET, toward=S.DARK)
        top = S.hex_to_oklch(edge)[0]
        stops = [0.16 + (top - 0.16) * i / (CHART_SLOTS - 1) for i in range(CHART_SLOTS)]
    else:
        edge = S.solve_lightness("#404040", against, S.EDGE_TARGET, toward=S.LIGHT)
        low = S.hex_to_oklch(edge)[0]
        stops = [0.97 - (0.97 - low) * i / (CHART_SLOTS - 1) for i in range(CHART_SLOTS)]
    return [S.solve_lightness(S.oklch_hex(L, 0, 0), against, S.EDGE_TARGET) for L in stops]


@lru_cache(maxsize=65536)
def _views(color: str) -> tuple[tuple[float, float, float], ...]:
    return tuple(S.hex_to_oklab(color if kind is None else S.simulate_cvd(color, kind))
                 for kind in (None,) + S.CVD_KINDS)


def _penalty(colors: list[str], goal: float) -> float:
    """Return the summed squared shortfall of all pair distances below the goal."""
    views = [_views(c) for c in colors]
    total = 0.0
    for i in range(len(views)):
        for j in range(i + 1, len(views)):
            for a, b in zip(views[i], views[j]):
                d = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
                if d < goal:
                    total += (goal - d) ** 2
    return total


def spread(colors: list[str], against: list[str], *, goal: float = 0.06, steps: int = 120) -> list[str]:
    """Move chart colors in lightness until they stay apart under CVD.

    Each step tries some lightness moves for each color.
    A move must keep the chart contrast target on every plane.
    The step keeps the move that most reduces the pair shortfall.
    """
    colors = list(colors)
    best = _penalty(colors, goal)
    for _ in range(steps):
        if best <= 0:
            break
        choice = None
        for index, value in enumerate(colors):
            L, C, H = S.hex_to_oklch(value)
            for delta in (-0.1, -0.05, -0.02, 0.02, 0.05, 0.1):
                moved = S.oklch_hex(L + delta, C, H, margin=1.0)
                if moved == value or S.min_contrast(moved, against) < S.EDGE_TARGET:
                    continue
                trial = colors[:index] + [moved] + colors[index + 1:]
                score = _penalty(trial, goal)
                if score < best - 1e-9 and (choice is None or score < choice[0]):
                    choice = (score, index, moved)
        if choice is None:
            break
        best, index, moved = choice
        colors[index] = moved
    return colors


@lru_cache(maxsize=512)
def viz_tokens(kind: str, ident: str, viz_id: str, mode: str) -> dict[str, str]:
    """Return chart, sequential, and diverging tokens for one context and mode."""
    spec = viz_sets()[viz_id]
    neutral = neutral_tokens(kind, ident, mode)
    against = [neutral["background"], neutral["card"]]
    toward = S.DARK if mode == S.LIGHT else S.LIGHT
    if spec.get("generator") == "lightness":
        charts = _mono_scale(against, mode)
    else:
        charts = [S.solve_lightness(S.normalize_hex(c), against, S.EDGE_TARGET, toward=toward)
                  for c in spec[mode]]
        charts = spread(charts, against)
    out ={f"chart-{i}": c for i, c in enumerate(charts, 1)}
    hue = spec["sequential"]
    chroma = 0 if spec.get("generator") else 0.13
    if mode == S.LIGHT:
        seq_L = (0.93, 0.80, 0.66, 0.52, 0.38)
        div_L = (0.45, 0.68, 0.93, 0.68, 0.45)
    else:
        seq_L = (0.32, 0.45, 0.58, 0.72, 0.86)
        div_L = (0.80, 0.60, 0.34, 0.60, 0.80)
    for i, L in enumerate(seq_L, 1):
        out[f"seq-{i}"] = S.oklch_hex(L, chroma, hue)
    left, right = spec["diverging"]
    div_hues = (left, left, None, right, right)
    for i, (L, H) in enumerate(zip(div_L, div_hues), 1):
        c = 0.006 if H is None else (0.15 if i in (1, 5) else 0.09)
        out[f"div-{i}"] = S.oklch_hex(L, 0 if spec.get("generator") else c, H or 0)
    return out


def mode_tokens(kind: str, ident: str, mode: str, *, accent=None, viz: str | None = None,
                contrast: str = "normal") -> dict[str, str]:
    """Return every color token for one context and mode."""
    out = dict(neutral_tokens(kind, ident, mode, contrast))
    spec = accent if accent is not None else context_accent_seed(kind, ident)
    out.update(accent_tokens(kind, ident, spec, mode, contrast))
    out.update(viz_tokens(kind, ident, viz or context_viz(kind, ident), mode))
    return out


# CSS output.

def _value(light: str, dark: str, pinned: str | None) -> str:
    if pinned == S.LIGHT:
        return light
    if pinned == S.DARK:
        return dark
    return light if light == dark else f"light-dark({light},{dark})"


def declarations(light: dict[str, str], dark: dict[str, str], keys, pinned: str | None = None) -> list[str]:
    """Return --token declarations that pair light and dark values."""
    return [f"--{key}:{_value(light[key], dark[key], pinned)}" for key in keys if key in light]


def rule(selector: str, decls: list[str]) -> str:
    """Return one CSS rule, or an empty string when there are no declarations."""
    return f"{selector}{{{';'.join(decls)}}}" if decls else ""


def context_decls(kind: str, ident: str, *, pinned: str | None = None, with_viz: bool = False,
                  contrast: str = "normal") -> list[str]:
    """Return the declarations for a theme or surface block."""
    light = mode_tokens(kind, ident, S.LIGHT, contrast=contrast)
    dark = mode_tokens(kind, ident, S.DARK, contrast=contrast)
    keys = list(NEUTRAL_KEYS) + [k for s in STATUS_KEYS for k in (s, f"{s}-surface")] + list(ACCENT_KEYS)
    if with_viz:
        keys += list(VIZ_KEYS)
    decls = declarations(light, dark, keys, pinned)
    style = context_style(kind, ident)
    decls += [f"--radius:{style['radius']}", f"--line:{style['line']}"]
    if pinned:
        decls.insert(0, f"color-scheme:{pinned}")
    return decls


def contrast_decls(kind: str, ident: str, accent=None, pinned: str | None = None) -> list[str]:
    """Return the ink declarations for data-ui-contrast="more"."""
    light = mode_tokens(kind, ident, S.LIGHT, accent=accent, contrast="more")
    dark = mode_tokens(kind, ident, S.DARK, accent=accent, contrast="more")
    keys = list(INK_KEYS) + ["primary-ink", "accent-foreground", "ring"]
    return declarations(light, dark, keys, pinned)


def accent_decls(kind: str, ident: str, spec, *, pinned: str | None = None,
                 contrast: str = "normal") -> list[str]:
    """Return the declarations for one accent in one context."""
    light = accent_tokens(kind, ident, spec, S.LIGHT, contrast)
    dark = accent_tokens(kind, ident, spec, S.DARK, contrast)
    return declarations(light, dark, ACCENT_KEYS, pinned)


def viz_decls(kind: str, ident: str, viz_id: str, *, pinned: str | None = None) -> list[str]:
    """Return the declarations for one viz set in one context."""
    light = viz_tokens(kind, ident, viz_id, S.LIGHT)
    dark = viz_tokens(kind, ident, viz_id, S.DARK)
    return declarations(light, dark, VIZ_KEYS, pinned)


def theme_color(kind: str, ident: str) -> dict[str, str]:
    """Return the light and dark page background, for theme-color metadata."""
    return {mode: neutral_tokens(kind, ident, mode)["background"] for mode in S.MODES}
