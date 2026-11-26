"""Pigment color math and accent solver.

This module uses only the Python standard library.
It converts between sRGB hex, linear sRGB, OKLab, and OKLCH.
It measures WCAG 2.1 contrast and simulates color vision deficiency.
"""
from __future__ import annotations

import math
from functools import lru_cache

LIGHT = "light"
DARK = "dark"
MODES = (LIGHT, DARK)

# WCAG targets. Solvers aim a little above the limits.
TEXT_MIN = 4.5
TEXT_TARGET = 4.6
MORE_TARGET = 7.0
MORE_SOLVE = 7.1
EDGE_MIN = 3.0
EDGE_TARGET = 3.05
INK_FLIP_Y = 0.179
GAMUT_MARGIN = 0.92
EPS = 1e-7


# sRGB transfer functions and hex helpers.

def srgb_to_linear(c: float) -> float:
    """Convert one gamma-encoded sRGB channel to linear light."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c: float) -> float:
    """Convert one linear channel to gamma-encoded sRGB."""
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1 / 2.4)) - 0.055


def parse_hex(value: str) -> tuple[float, float, float]:
    """Return gamma-encoded sRGB channels for a #rgb or #rrggbb string."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ValueError(f"Not a hex color: {value!r}")
    return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def is_hex(value: object) -> bool:
    """Return True when the value is a #rgb or #rrggbb string."""
    if not isinstance(value, str) or not value.startswith("#"):
        return False
    try:
        parse_hex(value)
    except ValueError:
        return False
    return True


def to_hex(rgb: tuple[float, float, float]) -> str:
    """Clip gamma-encoded channels to 0..1 and format them as #rrggbb."""
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def normalize_hex(value: str) -> str:
    """Return the lowercase #rrggbb form of a hex color."""
    return to_hex(parse_hex(value))


# OKLab, after Björn Ottosson (2020).

def linear_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert linear sRGB to OKLab."""
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab_to_linear(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Convert OKLab to linear sRGB. The result can be out of gamut."""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def oklab_to_oklch(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Convert OKLab to OKLCH with the hue in degrees."""
    C = math.hypot(a, b)
    H = math.degrees(math.atan2(b, a)) % 360 if C > 1e-6 else 0.0
    return L, C, H


def oklch_to_oklab(L: float, C: float, H: float) -> tuple[float, float, float]:
    """Convert OKLCH to OKLab."""
    h = math.radians(H)
    return L, C * math.cos(h), C * math.sin(h)


def hex_to_oklab(value: str) -> tuple[float, float, float]:
    """Convert a hex color to OKLab."""
    return linear_to_oklab(*(srgb_to_linear(c) for c in parse_hex(value)))


def hex_to_oklch(value: str) -> tuple[float, float, float]:
    """Convert a hex color to OKLCH."""
    return oklab_to_oklch(*hex_to_oklab(value))


def oklab_to_hex(L: float, a: float, b: float) -> str:
    """Convert OKLab to hex. Channels outside the gamut are clipped."""
    return to_hex(tuple(linear_to_srgb(max(0.0, c)) for c in oklab_to_linear(L, a, b)))  # type: ignore[arg-type]


def in_gamut(L: float, C: float, H: float, eps: float = 1e-6) -> bool:
    """Return True when the OKLCH color is inside the sRGB gamut."""
    return all(-eps <= c <= 1 + eps for c in oklab_to_linear(*oklch_to_oklab(L, C, H)))


@lru_cache(maxsize=65536)
def _max_chroma(L: float, H: float) -> float:
    if L <= 0 or L >= 1:
        return 0.0
    lo, hi = 0.0, 0.5
    for _ in range(32):
        mid = (lo + hi) / 2
        if in_gamut(L, mid, H, eps=0):
            lo = mid
        else:
            hi = mid
    return lo


def max_srgb_chroma(L: float, H: float) -> float:
    """Return the largest OKLCH chroma inside sRGB for this lightness and hue."""
    return _max_chroma(round(L, 6), round(H % 360, 4))


def clamp_chroma(L: float, C: float, H: float, margin: float = GAMUT_MARGIN) -> tuple[float, float, float]:
    """Limit chroma to a margin of the sRGB maximum. Keep L and H."""
    L = min(1.0, max(0.0, L))
    return L, min(max(C, 0.0), margin * max_srgb_chroma(L, H)), H % 360


def oklch_hex(L: float, C: float, H: float, margin: float = GAMUT_MARGIN) -> str:
    """Return the hex for an OKLCH color after a gamut clamp."""
    return oklab_to_hex(*oklch_to_oklab(*clamp_chroma(L, C, H, margin)))


def mix_oklab(a: str, b: str, amount: float) -> str:
    """Mix two hex colors in OKLab. An amount of 0 gives a and 1 gives b."""
    la, lb = hex_to_oklab(a), hex_to_oklab(b)
    return oklab_to_hex(*(x + (y - x) * amount for x, y in zip(la, lb)))


# WCAG 2.1 luminance and contrast.

def relative_luminance(value: str) -> float:
    """Return the WCAG 2.1 relative luminance of a hex color."""
    r, g, b = (srgb_to_linear(c) for c in parse_hex(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """Return the WCAG 2.1 contrast ratio between two hex colors."""
    ya, yb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(ya, yb), min(ya, yb)
    return (hi + 0.05) / (lo + 0.05)


def min_contrast(color: str, others) -> float:
    """Return the lowest contrast between one color and a list of colors."""
    return min(contrast_ratio(color, other) for other in others)


def is_light(value: str) -> bool:
    """Return True when dark ink gives more contrast than white ink."""
    return relative_luminance(value) >= INK_FLIP_Y


# Color vision deficiency simulation (Viénot, Brettel and Mollon 1999).
# These are the linear sRGB matrices that DaltonLens publishes.
_CVD = {
    "protanopia": ((0.11238, 0.88762, 0.0), (0.11238, 0.88762, 0.0), (0.00401, -0.00401, 1.0)),
    "deuteranopia": ((0.29275, 0.70725, 0.0), (0.29275, 0.70725, 0.0), (-0.02234, 0.02234, 1.0)),
}
CVD_KINDS = tuple(_CVD)


def simulate_cvd(value: str, kind: str) -> str:
    """Return how a hex color looks with protanopia or deuteranopia."""
    rgb = [srgb_to_linear(c) for c in parse_hex(value)]
    out = tuple(linear_to_srgb(max(0.0, min(1.0, sum(m * c for m, c in zip(row, rgb)))))
                for row in _CVD[kind])
    return to_hex(out)  # type: ignore[arg-type]


def delta_e(a: str, b: str) -> float:
    """Return the OKLab distance between two hex colors."""
    return math.dist(hex_to_oklab(a), hex_to_oklab(b))


# Solvers.

def solve_lightness(
    start: str,
    against,
    target: float,
    *,
    toward: str | None = None,
    chroma: float | None = None,
) -> str:
    """Move one color in lightness until it reaches a contrast target.

    The hue stays fixed and the chroma stays inside the gamut.
    The result is the passing color that is nearest to the start.
    """
    against = list(against)
    if min_contrast(start, against) >= target:
        return normalize_hex(start)
    L0, C0, H = hex_to_oklch(start)
    if chroma is not None:
        C0 = chroma
    if toward is None:
        toward = DARK if is_light(against[0]) else LIGHT
    end = 0.0 if toward == DARK else 1.0

    def color(L: float) -> str:
        return oklch_hex(L, C0, H, margin=1.0)

    if min_contrast(color(end), against) < target:
        extreme = "#000000" if toward == DARK else "#ffffff"
        return extreme
    lo, hi = L0, end
    for _ in range(30):
        mid = (lo + hi) / 2
        if min_contrast(color(mid), against) >= target:
            hi = mid
        else:
            lo = mid
    result = color(hi)
    # Hex rounding can lose a little contrast. Step on until it passes.
    step = -0.002 if toward == DARK else 0.002
    L = hi
    while min_contrast(result, against) < target and 0.0 < L < 1.0:
        L += step
        result = color(L)
    if min_contrast(result, against) < target:
        return "#000000" if toward == DARK else "#ffffff"
    return result


@lru_cache(maxsize=4096)
def _cusp(H: float) -> tuple[float, float]:
    best = (0.6, 0.0)
    for step in range(30, 96):
        L = step / 100
        C = max_srgb_chroma(L, H)
        if C > best[1]:
            best = (L, C)
    return best


def solid_from_hue(H: float, chroma: float = 0.2) -> str:
    """Return a solid fill for a hue, like Radix step 9.

    The lightness follows the sRGB cusp of the hue.
    Yellow, amber, lime, and cyan stay bright. Blue and violet stay deep.
    """
    cusp_L, _ = _cusp(round(H % 360, 2))
    L = min(0.88, max(0.55, cusp_L))
    return oklch_hex(L, chroma, H)


def solid_ink(solid: str) -> str:
    """Return the text color for a solid fill.

    White is used when the fill luminance is below 0.179.
    Otherwise the ink is a dark color of the same hue.
    Both choices give at least 4.5:1.
    """
    if relative_luminance(solid) < INK_FLIP_Y:
        return "#ffffff"
    _, C, H = hex_to_oklch(solid)
    dark = oklch_hex(0.24, min(C, 0.05), H)
    return solve_lightness(dark, [solid], TEXT_TARGET, toward=DARK)


def _seed_solid(seed) -> str:
    if isinstance(seed, (int, float)) and not isinstance(seed, bool):
        if not 0 <= float(seed) <= 360:
            raise ValueError(f"Hue must be 0..360: {seed!r}")
        return solid_from_hue(float(seed))
    if is_hex(seed):
        L, C, H = hex_to_oklch(seed)
        return oklch_hex(L, C, H, margin=1.0)
    raise ValueError(f"Seed must be a hex color or a hue: {seed!r}")


def _soft(solid: str, background: str, mode: str) -> tuple[str, str, str]:
    """Return the accent, accent-surface, and accent-border colors."""
    if mode == LIGHT:
        return (mix_oklab(background, solid, 0.14), mix_oklab(background, solid, 0.08),
                mix_oklab(background, solid, 0.38))
    return (mix_oklab(background, solid, 0.20), mix_oklab(background, solid, 0.12),
            mix_oklab(background, solid, 0.45))


def solve_mode(seed, background: str, card: str | None = None, *, mode: str = LIGHT,
               contrast: str = "normal") -> dict[str, str]:
    """Solve the accent tokens for one mode and one surface."""
    card = card or background
    solid = _seed_solid(seed)
    toward = DARK if mode == LIGHT else LIGHT
    edge = solve_lightness(solid, [background, card], EDGE_TARGET, toward=toward)
    accent, accent_surface, accent_border = _soft(solid, background, mode)
    target = MORE_SOLVE if contrast == "more" else TEXT_TARGET
    ink = solve_lightness(solid, [background, card, accent, accent_surface], target, toward=toward)
    return {
        "primary": solid,
        "primary-foreground": solid_ink(solid),
        "primary-edge": edge,
        "primary-ink": ink,
        "accent": accent,
        "accent-surface": accent_surface,
        "accent-border": accent_border,
        "accent-foreground": ink,
        "ring": ink,
    }


def solve_accent(
    seed_or_hue,
    surface_light_bg: str,
    surface_dark_bg: str,
    *,
    contrast: str = "normal",
    card_light: str | None = None,
    card_dark: str | None = None,
) -> dict[str, dict[str, str]]:
    """Solve the accent tokens for light and dark modes.

    The seed is a hex color or a hue in degrees.
    A dict with light and dark keys gives a different seed for each mode.
    """
    if contrast not in ("normal", "more"):
        raise ValueError("contrast must be normal or more")
    seeds = seed_or_hue if isinstance(seed_or_hue, dict) else {LIGHT: seed_or_hue, DARK: seed_or_hue}
    return {
        LIGHT: solve_mode(seeds[LIGHT], surface_light_bg, card_light, mode=LIGHT, contrast=contrast),
        DARK: solve_mode(seeds[DARK], surface_dark_bg, card_dark, mode=DARK, contrast=contrast),
    }


# The pure CSS envelope for --tp-hue. Each entry is (light, dark) OKLCH.
HUE_ENVELOPE = {
    "primary": ((0.50, 0.075), (0.50, 0.075)),
    "primary-edge": ((0.50, 0.075), (0.70, 0.08)),
    "primary-ink": ((0.46, 0.075), (0.80, 0.07)),
    "accent": ((0.95, 0.02), (0.30, 0.03)),
    "accent-surface": ((0.97, 0.012), (0.26, 0.022)),
    "accent-border": ((0.86, 0.04), (0.42, 0.05)),
}


def hue_envelope_css(fallback_hue: float) -> str:
    """Return CSS declarations that follow --tp-hue in pure CSS."""
    h = f"var(--tp-hue,{fallback_hue:g})"
    parts = []
    for name, ((l1, c1), (l2, c2)) in HUE_ENVELOPE.items():
        light = f"oklch({l1:.2f} {c1:g} {h})"
        dark = f"oklch({l2:.2f} {c2:g} {h})"
        value = light if (l1, c1) == (l2, c2) else f"light-dark({light},{dark})"
        parts.append(f"--{name}:{value}")
    parts += ["--primary-foreground:#fff", "--accent-foreground:var(--primary-ink)",
              "--ring:var(--primary-ink)"]
    return ";".join(parts)


def hue_envelope_hex(H: float, mode: str) -> dict[str, str]:
    """Return the envelope colors for one hue as hex, for tests."""
    index = 0 if mode == LIGHT else 1
    out = {}
    for name, pair in HUE_ENVELOPE.items():
        L, C = pair[index]
        if not in_gamut(L, C, H):
            raise ValueError(f"Envelope color {name} leaves sRGB at hue {H}")
        out[name] = oklab_to_hex(*oklch_to_oklab(L, C, H))
    out["primary-foreground"] = "#ffffff"
    return out
