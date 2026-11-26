"""Check Pigment tokens for WCAG contrast, sRGB gamut, and CVD separation."""
from __future__ import annotations

from itertools import combinations

from . import solve as S
from . import tokens as T

# Lowest OKLab distance between any two chart colors after CVD simulation.
CVD_MIN_PAIR = 0.035
# Lowest OKLab distance between neighbor chart colors after CVD simulation.
CVD_MIN_NEIGHBOR = 0.045


def _check(failures: list[str], label: str, fg: str, bg: str, minimum: float) -> None:
    ratio = S.contrast_ratio(fg, bg)
    if ratio < minimum:
        failures.append(f"{label}: {fg} on {bg} is {ratio:.2f}:1, needs {minimum}:1")


def check_mode(tokens: dict[str, str], *, contrast: str = "normal", label: str = "") -> list[str]:
    """Return the contrast failures in one mode's token set."""
    failures: list[str] = []
    text = S.MORE_TARGET if contrast == "more" else S.TEXT_MIN
    for key, value in tokens.items():
        if not S.is_hex(value):
            failures.append(f"{label}{key}: {value!r} is not an sRGB hex color")
    planes = ("background", "card", "muted")
    for ink in ("foreground", "muted-foreground", "heading"):
        for plane in planes:
            _check(failures, f"{label}{ink}/{plane}", tokens[ink], tokens[plane], text)
    _check(failures, f"{label}code", tokens["code-foreground"], tokens["code-background"], text)
    _check(failures, f"{label}solid", tokens["primary-foreground"], tokens["primary"], S.TEXT_MIN)
    for plane in ("background", "card", "muted", "accent", "accent-surface"):
        _check(failures, f"{label}primary-ink/{plane}", tokens["primary-ink"], tokens[plane], text)
    for plane in ("background", "card"):
        _check(failures, f"{label}primary-edge/{plane}", tokens["primary-edge"], tokens[plane], S.EDGE_MIN)
    for key in T.STATUS_KEYS:
        for plane in ("background", "card", f"{key}-surface"):
            _check(failures, f"{label}{key}/{plane}", tokens[key], tokens[plane], text)
    for i in range(1, T.CHART_SLOTS + 1):
        for plane in ("background", "card"):
            _check(failures, f"{label}chart-{i}/{plane}", tokens[f"chart-{i}"], tokens[plane], S.EDGE_MIN)
    return failures


def cvd_report(colors: list[str]) -> dict[str, dict[str, float]]:
    """Return the lowest pair and neighbor distances for each CVD kind."""
    report = {}
    for kind in ("normal",) + S.CVD_KINDS:
        sim = colors if kind == "normal" else [S.simulate_cvd(c, kind) for c in colors]
        pairs = min(S.delta_e(a, b) for a, b in combinations(sim, 2))
        neighbors = min(S.delta_e(a, b) for a, b in zip(sim, sim[1:]))
        report[kind] = {"pair": pairs, "neighbor": neighbors}
    return report


def check_cvd(colors: list[str], label: str = "") -> list[str]:
    """Return CVD separation failures for a list of chart colors."""
    failures = []
    for kind, values in cvd_report(colors).items():
        if values["pair"] < CVD_MIN_PAIR:
            failures.append(f"{label}{kind}: closest pair is {values['pair']:.3f}, needs {CVD_MIN_PAIR}")
        if values["neighbor"] < CVD_MIN_NEIGHBOR:
            failures.append(
                f"{label}{kind}: closest neighbors are {values['neighbor']:.3f}, needs {CVD_MIN_NEIGHBOR}")
    return failures


def contexts():
    """Yield every theme and surface context."""
    for ident in T.presets():
        yield "theme", ident
    for ident in T.surfaces():
        yield "surface", ident


def validate_all() -> list[str]:
    """Check every preset, surface, accent, and viz set in both modes."""
    failures: list[str] = []
    for kind, ident in contexts():
        for mode in S.MODES:
            for contrast in T.CONTRASTS:
                tokens = T.mode_tokens(kind, ident, mode, contrast=contrast)
                failures += check_mode(tokens, contrast=contrast, label=f"{kind}:{ident}:{mode}:{contrast}:")
            for accent in T.accents():
                for contrast in T.CONTRASTS:
                    tokens = T.mode_tokens(kind, ident, mode, accent=accent, contrast=contrast)
                    failures += check_mode(tokens, contrast=contrast,
                                           label=f"{kind}:{ident}:{mode}:{accent}:{contrast}:")
            for viz in T.viz_sets():
                tokens = T.mode_tokens(kind, ident, mode, viz=viz)
                failures += check_mode(tokens, label=f"{kind}:{ident}:{mode}:{viz}:")
                charts = [tokens[f"chart-{i}"] for i in range(1, T.CHART_SLOTS + 1)]
                failures += check_cvd(charts, label=f"{kind}:{ident}:{mode}:{viz}:")
    return failures


def main() -> int:
    """Print failures and return a process exit code."""
    failures = validate_all()
    for line in failures:
        print(line)
    print(f"{len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
