"""Checks for the Tailplan UI color math, presets, primitives, and CSS rules."""
import json
import re
from pathlib import Path

import pytest

import tailplan_themes as legacy
from tailplan_ui.color import solve as S
from tailplan_ui.color import tokens as T
from tailplan_ui.color import validate as V

UI = Path(__file__).resolve().parents[1] / 'tailplan_ui'
CSS = {p.name: p.read_text(encoding='utf-8') for p in (UI / 'css').glob('*.css')}
HUES = range(0, 360, 5)
LAYERS = '@layer tp.reset, tp.tokens, tp.density, tp.base, tp.primitives, tp.blocks, tp.wireframes;'


def target(contrast):
    return S.MORE_TARGET if contrast == 'more' else S.TEXT_MIN


# Color math.

@pytest.mark.parametrize('value', ['#000000', '#ffffff', '#808080', '#ff0000', '#00ff00', '#0000ff', '#eab308', '#06b6d4'])
def test_oklab_round_trip_keeps_hex(value):
    assert S.oklab_to_hex(*S.hex_to_oklab(value)) == value


def test_contrast_matches_wcag_reference_values():
    assert S.contrast_ratio('#000000', '#ffffff') == pytest.approx(21.0)
    assert S.contrast_ratio('#777777', '#ffffff') == pytest.approx(4.48, abs=0.01)
    assert S.relative_luminance('#ffffff') == pytest.approx(1.0)


@pytest.mark.parametrize('H', range(0, 360, 30))
def test_max_chroma_is_the_gamut_edge(H):
    for L in (0.3, 0.5, 0.7, 0.9):
        top = S.max_srgb_chroma(L, H)
        assert S.in_gamut(L, top * 0.999, H)
        assert not S.in_gamut(L, top * 1.02 + 0.002, H)


def test_clamp_chroma_keeps_lightness_and_hue_with_a_margin():
    L, C, H = S.clamp_chroma(0.7, 0.5, 145)
    assert (L, H) == (0.7, 145)
    assert C == pytest.approx(S.GAMUT_MARGIN * S.max_srgb_chroma(0.7, 145))
    assert S.clamp_chroma(0.7, 0.01, 145)[1] == 0.01


# The solver contract over all hues.

@pytest.mark.parametrize('contrast', T.CONTRASTS)
@pytest.mark.parametrize('H', HUES)
def test_every_hue_solves_on_the_default_surfaces(H, contrast):
    light_bg, dark_bg = '#faf6ee', '#1c1b18'
    result = S.solve_accent(H, light_bg, dark_bg, contrast=contrast)
    for mode, bg in ((S.LIGHT, light_bg), (S.DARK, dark_bg)):
        t = result[mode]
        assert all(S.is_hex(v) for v in t.values()), t
        assert S.contrast_ratio(t['primary-foreground'], t['primary']) >= S.TEXT_MIN
        assert S.contrast_ratio(t['primary-edge'], bg) >= S.EDGE_MIN
        for key in ('primary-ink', 'accent-foreground', 'ring'):
            assert S.contrast_ratio(t[key], bg) >= target(contrast), (mode, key)
        assert S.contrast_ratio(t['accent-foreground'], t['accent']) >= target(contrast)
        assert S.contrast_ratio(t['primary-ink'], t['accent-surface']) >= target(contrast)


@pytest.mark.parametrize('H', HUES)
def test_ink_flip_follows_the_luminance_threshold(H):
    t = S.solve_accent(H, '#ffffff', '#000000')[S.LIGHT]
    dark_ink = S.relative_luminance(t['primary']) >= S.INK_FLIP_Y
    assert (t['primary-foreground'] != '#ffffff') == dark_ink


@pytest.mark.parametrize('seed', ['#eab308', '#06b6d4', '#1d4ed8', '#a3e635', '#ffffff', '#000000'])
def test_torture_seeds_pass_on_white_and_black(seed):
    for contrast in T.CONTRASTS:
        result = S.solve_accent(seed, '#ffffff', '#000000', contrast=contrast)
        for mode, bg in ((S.LIGHT, '#ffffff'), (S.DARK, '#000000')):
            t = result[mode]
            assert S.contrast_ratio(t['primary-foreground'], t['primary']) >= S.TEXT_MIN
            assert S.contrast_ratio(t['primary-ink'], bg) >= target(contrast)
            assert S.contrast_ratio(t['primary-edge'], bg) >= S.EDGE_MIN


def test_bright_seeds_stay_vivid_as_fills():
    yellow = S.solve_accent('#eab308', '#ffffff', '#111111')[S.LIGHT]
    assert yellow['primary'] == '#eab308'
    assert yellow['primary-foreground'] != '#ffffff'
    assert S.hex_to_oklch(yellow['primary-ink'])[2] == pytest.approx(S.hex_to_oklch('#eab308')[2], abs=12)


def test_per_mode_seed_dict_is_honored():
    result = S.solve_accent({S.LIGHT: '#536447', S.DARK: '#9cb08a'}, '#faf6ee', '#1c1b18')
    assert result[S.LIGHT]['primary'] == '#536447'
    assert result[S.DARK]['primary'] == '#9cb08a'


def test_solve_accent_rejects_unknown_contrast():
    with pytest.raises(ValueError):
        S.solve_accent(120, '#ffffff', '#000000', contrast='max')


# The --tp-hue envelope.

@pytest.mark.parametrize('H', HUES)
def test_hue_envelope_is_in_gamut_and_readable_on_every_preset(H):
    for mode in S.MODES:
        env = S.hue_envelope_hex(H, mode)
        assert S.contrast_ratio(env['primary-foreground'], env['primary']) >= S.TEXT_MIN
        for ident in T.presets():
            bg = T.neutral_tokens('theme', ident, mode)['background']
            assert S.contrast_ratio(env['primary-ink'], bg) >= S.TEXT_MIN, (ident, mode)
            assert S.contrast_ratio(env['primary-edge'], bg) >= S.EDGE_MIN, (ident, mode)


def test_hue_envelope_css_mixes_nothing_in_oklch():
    css = S.hue_envelope_css(140)
    assert 'var(--tp-hue,140)' in css
    assert '--primary-foreground:#fff' in css
    assert 'color-mix' not in css


# Presets, surfaces, accents, and viz sets.

def test_full_validator_reports_no_failures():
    assert V.validate_all() == []


def test_there_are_ten_designer_presets_and_one_wireframe():
    kinds = {}
    for ident, doc in T.presets().items():
        kinds.setdefault(doc['kind'], []).append(ident)
    assert len(kinds['designer']) == 10
    assert kinds['wireframe'] == ['wire']


@pytest.mark.parametrize('ident', sorted(T.presets()))
def test_preset_schema_and_attribution(ident):
    doc = T.presets()[ident]
    path = UI / 'palettes' / f'{ident}.json'
    assert json.loads(path.read_text()) == doc
    assert doc['schemaVersion'] == 2 and doc['id'] == ident and doc['version'] >= 1
    assert set(doc['variants']) == set(S.MODES) == set(doc['modes'])
    source = doc['source']
    assert source['url'].startswith('https://') and source['license']
    assert (UI / source['licenseFile']).read_text(encoding='utf-8').strip()
    assert doc['viz'] in T.viz_sets()
    for mode in S.MODES:
        assert all(S.is_hex(v) for v in doc['modes'][mode].values())


def test_wire_preset_is_dashed_and_monochrome():
    wire = T.presets()['wire']
    assert (wire['line'], wire['radius'], wire['viz']) == ('dashed', '3px', 'monochrome')


@pytest.mark.parametrize('ident', sorted(T.surfaces()))
def test_surfaces_stay_near_neutral(ident):
    surface = T.surfaces()[ident]
    assert surface['chroma'] <= 0.018
    assert surface['accent'] in T.accents()
    for mode in S.MODES:
        bg = T.neutral_tokens('surface', ident, mode)['background']
        assert S.hex_to_oklch(bg)[1] <= 0.018 + 0.004


def test_primitive_counts_match_the_spec():
    assert len(T.surfaces()) == 8
    assert len(T.accents()) == 14
    assert len(T.viz_sets()) == 6


@pytest.mark.parametrize('viz', sorted(T.viz_sets()))
def test_chart_colors_pass_on_bg_and_card_and_separate_under_cvd(viz):
    for kind, ident in V.contexts():
        for mode in S.MODES:
            tokens = T.mode_tokens(kind, ident, mode, viz=viz)
            charts = [tokens[f'chart-{i}'] for i in range(1, T.CHART_SLOTS + 1)]
            for color in charts:
                for plane in ('background', 'card'):
                    assert S.contrast_ratio(color, tokens[plane]) >= S.EDGE_MIN
            assert V.check_cvd(charts) == [], (kind, ident, mode)
            assert len(set(charts)) == T.CHART_SLOTS
            for i in range(1, T.RAMP_SLOTS + 1):
                assert S.is_hex(tokens[f'seq-{i}']) and S.is_hex(tokens[f'div-{i}'])


@pytest.mark.parametrize('viz', sorted(T.viz_sets()))
def test_sequential_ramps_are_monotonic(viz):
    for mode in S.MODES:
        tokens = T.viz_tokens('theme', T.DEFAULT_THEME, viz, mode)
        lightness = [S.hex_to_oklch(tokens[f'seq-{i}'])[0] for i in range(1, T.RAMP_SLOTS + 1)]
        assert lightness == sorted(lightness) or lightness == sorted(lightness, reverse=True)


def test_token_set_has_the_full_alias_list():
    tokens = T.mode_tokens('theme', T.DEFAULT_THEME, S.LIGHT)
    for key in ('background', 'foreground', 'card', 'muted', 'muted-foreground', 'border', 'border-subtle',
                'border-strong', 'input', 'text', 'heading', 'surface', 'primary', 'primary-foreground',
                'primary-edge', 'primary-ink', 'accent', 'accent-surface', 'accent-border', 'accent-foreground',
                'ring', 'chart-1', 'chart-8', 'seq-1', 'seq-5', 'div-1', 'div-5', 'destructive-surface'):
        assert key in tokens, key


def test_more_contrast_lifts_every_ink():
    for kind, ident in V.contexts():
        for mode in S.MODES:
            tokens = T.mode_tokens(kind, ident, mode, contrast='more')
            assert V.check_mode(tokens, contrast='more') == []


def test_legacy_alias_map_covers_every_old_theme():
    assert set(T.LEGACY_ALIASES) == set(legacy.registry())
    for old in legacy.registry():
        new = T.legacy_theme(old)
        assert new['theme'] in T.presets() and new['mode'] in S.MODES


def test_single_mode_aliases_resolve_with_a_pinned_mode():
    assert T.resolve_theme('catppuccin-latte') == ('catppuccin', S.LIGHT)
    assert T.resolve_theme('nord') == ('nord', None)
    with pytest.raises(KeyError):
        T.resolve_theme('nope')


# CSS invariants for every file in the package.

def package_text():
    for path in UI.rglob('*'):
        if path.suffix in {'.css', '.py', '.json'}:
            yield path, path.read_text(encoding='utf-8')


def test_no_important_anywhere_in_the_package():
    bad = [str(p) for p, text in package_text() if '!' + 'important' in text]
    assert bad == []


def test_css_never_mixes_in_oklch_or_loads_external_files():
    for path, text in package_text():
        assert 'in ' + 'oklch' not in text.replace('color-mix(in oklab', ''), path
    for name, text in CSS.items():
        assert 'url(' not in text and '@import' not in text, name


def test_css_has_no_600_or_640_pixel_breakpoints():
    for name, text in CSS.items():
        assert not re.search(r'\b6[04]0px', text), name


def test_layer_order_is_exact():
    assert LAYERS in CSS['layers.css']


def test_density_tiers_and_pointer_rules():
    text = CSS['density.css']
    for query in ('(max-width: 480px)', '(min-width: 481px) and (max-width: 700px)', '(min-width: 701px)',
                  '(max-height: 480px)', '(pointer: coarse)', '(hover: hover) and (pointer: fine)'):
        assert query in text, query
    assert '--tap: 44px' in text
    assert 'max(16px, 1em)' in text
    assert 'calc(64px + env(safe-area-inset-bottom, 0px))' in text
    for n in (1, 2, 3):
        assert f'[data-clamp="{n}"] {{ -webkit-line-clamp: {n};' in text


def test_reset_covers_wrap_and_media_safety():
    text = CSS['reset.css']
    for needle in ('box-sizing: border-box', 'text-size-adjust: 100%', 'overflow-wrap: anywhere',
                   'img, svg, video, canvas { max-width: 100%; }', 'overflow-x: auto'):
        assert needle in text, needle


def test_base_type_rules():
    text = CSS['base.css']
    serif = re.search(r'--font-serif:\s*([^;]+);', text).group(1)
    assert serif.startswith('"Iowan Old Style"')
    assert '--heading-weight: 600' in text
    assert '700' not in text and 'bold' not in text
    assert not re.search(r'h2[^{]*\{[^}]*border-bottom', text)
    assert '@scope ([data-slot="prose"]) to ([data-ui-theme], [data-slot="device-screen"])' in text
    assert ':where(a) { color: var(--primary-ink); }' in text
    assert '@media print' in text
