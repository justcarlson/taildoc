"""Checks for the Tailplan UI stdlib bundler and its command line."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import tailplan_server as server
import tailplan_ui
from tailplan_ui import bundler as B
from tailplan_ui.color import solve as S

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / 'tailplan_ui' / 'examples' / 'duo-plan.html'
STYLE_RE = re.compile(r'<style id="tp-ui" data-tailplan-ui="1\.0\.0">(.*?)</style>', re.S)
HEAD = '<!doctype html><html lang="en"><head><meta charset="utf-8">' \
       '<meta name="viewport" content="width=device-width, initial-scale=1"><title>T</title>'


def page(body='', head='', html_attrs=''):
    doc = HEAD + head + '</head><body>' + body + '</body></html>'
    return doc.replace('<html lang="en">', f'<html lang="en"{html_attrs}>')


def ui_css(doc):
    found = STYLE_RE.findall(doc)
    assert len(found) == 1
    return found[0]


def test_package_exports_version_and_bundle():
    assert tailplan_ui.__version__ == '1.0.0'
    assert tailplan_ui.bundle is B.bundle


def test_base_page_stays_under_six_kilobytes():
    out, meta = B.bundle(page('<p>Hello</p>'))
    assert meta['uiCssBytes'] <= 6 * 1024
    assert meta['uiCssBytes'] == len(ui_css(out).encode())


def test_typical_page_stays_under_fourteen_kilobytes():
    _, meta = B.bundle(DUO.read_text(encoding='utf-8'))
    assert meta['uiCssBytes'] <= 14 * 1024


def test_layer_statement_comes_first_and_chunks_are_tree_shaken():
    css = ui_css(B.bundle(page('<p>x</p>'))[0])
    assert css.startswith('@layer tp.reset,tp.tokens,tp.density,tp.base,tp.primitives,tp.blocks,tp.wireframes;')
    assert '@scope' not in css and 'data-clamp' not in css and '--chart-1' not in css
    rich = ui_css(B.bundle(page('<div data-slot="prose" data-clamp="2" style="color:var(--chart-1)">x</div>'))[0])
    assert '@scope' in rich and '-webkit-line-clamp' in rich and '--chart-1' in rich


def test_bundle_is_idempotent():
    once, meta1 = B.bundle(DUO.read_text(encoding='utf-8'))
    twice, meta2 = B.bundle(once)
    assert once == twice
    assert meta1 == meta2


def test_ui_style_goes_before_the_first_author_style():
    doc = page(head='<style>p{color:red}</style><style>a{color:blue}</style>')
    out, _ = B.bundle(doc)
    assert out.index('id="tp-ui"') < out.index('<style>p{color:red}')
    assert out.count('<style') == 3


def test_ui_style_goes_before_an_https_stylesheet_link():
    out, _ = B.bundle(page(head='<link rel="stylesheet" href="https://example.com/a.css">'))
    assert out.index('id="tp-ui"') < out.index('<link rel="stylesheet"')


def test_no_author_style_puts_the_block_at_the_end_of_head():
    out, _ = B.bundle(page('<p>x</p>'))
    assert out.index('id="tp-ui"') < out.index('</head>') < out.index('<body>')


def test_standalone_adds_dual_theme_color_metas_once():
    out, _ = B.bundle(page())
    assert 'media="(prefers-color-scheme: light)" content="#faf6ee"' in out
    assert 'media="(prefers-color-scheme: dark)" content="#1c1b18"' in out
    assert B.bundle(out)[0].count('name="theme-color"') == 2


def test_author_theme_color_is_kept():
    out, _ = B.bundle(page(head='<meta name="theme-color" content="#123456">'))
    assert out.count('name="theme-color"') == 1


def test_t3_embed_leaves_canvas_and_metas_to_the_host():
    out, meta = B.bundle(page('<p>x</p>'), target='t3-embed')
    css = ui_css(out)
    assert 'theme-color' not in out
    assert 'padding-bottom:var(--safe-bottom)' not in css
    assert meta['target'] == 't3-embed'
    with pytest.raises(ValueError):
        B.bundle(page(), target='mobile')


def test_metadata_reports_page_features():
    doc = page('<div data-slot="prose"><p data-ui-seed="#06B6D4">x</p></div><span data-slot="kbd-hint">K</span>',
               head='<style>.a{color:red}</style>')
    out, meta = B.bundle(doc)
    assert meta['uiVersion'] == '1.0.0'
    assert meta['uiTheme'] == 'warm-editorial'
    assert meta['uiAccent'] == 'olive'
    assert meta['uiViz'] is None
    assert meta['uiSlotCount'] == 2
    assert meta['customCssBytes'] == len('.a{color:red}')
    assert meta['hasDarkMode'] is True
    assert meta['seeds'] == ['#06b6d4']
    assert meta['sha256'] == __import__('hashlib').sha256(out.encode()).hexdigest()


def test_meta_tag_and_root_attributes_pick_the_root_context():
    out, meta = B.bundle(page(head='<meta name="tailplan-ui" content="theme=nord viz=okabe-ito">'))
    assert meta['uiTheme'] == 'nord' and meta['uiViz'] == 'okabe-ito'
    out, meta = B.bundle(page(html_attrs=' data-ui-surface="slate" data-ui-accent="cobalt"'))
    assert meta['uiSurface'] == 'slate' and meta['uiAccent'] == 'cobalt'


def test_pinned_alias_and_light_mode_turn_off_dark_mode():
    _, meta = B.bundle(page(html_attrs=' data-ui-theme="catppuccin-latte"'))
    assert meta['hasDarkMode'] is False
    out, meta = B.bundle(page(html_attrs=' data-ui-mode="light"'))
    assert meta['hasDarkMode'] is False
    assert ':root{color-scheme:light;' in ui_css(out)


def test_nested_themes_get_scoped_rules_and_island_paint():
    css = ui_css(B.bundle(page('<aside data-ui-theme="wire"><p data-ui-accent="cobalt">x</p></aside>'))[0])
    assert '[data-ui-theme="wire"]{' in css
    assert '[data-ui-theme="wire"] [data-ui-accent="cobalt"]' in css
    assert ':where([data-ui-theme],[data-ui-surface]):not(:root)' in css
    assert '--line:dashed' in css


@pytest.mark.parametrize('seed', ['#eab308', '#06b6d4', '#1d4ed8'])
def test_seed_torture_values_land_as_readable_tokens(seed):
    out, meta = B.bundle(page(f'<section data-ui-seed="{seed.upper()}"><a href="#x">x</a></section>'))
    css = ui_css(out)
    rule = re.search(r'\[data-ui-seed="%s" i\]\{([^}]*)\}' % seed, css).group(1)
    ink = re.search(r'--primary-ink:light-dark\((#[0-9a-f]{6}),(#[0-9a-f]{6})\)', rule)
    light_bg, dark_bg = '#faf6ee', '#1c1b18'
    assert S.contrast_ratio(ink.group(1), light_bg) >= S.TEXT_MIN
    assert S.contrast_ratio(ink.group(2), dark_bg) >= S.TEXT_MIN
    assert meta['seeds'] == [seed]


def test_contrast_more_adds_stronger_inks():
    css = ui_css(B.bundle(page('<div data-ui-contrast="more" data-ui-accent="amber">x</div>'))[0])
    rule = re.search(r'\[data-ui-contrast="more"\]\[data-ui-accent="amber"\][^{]*\{([^}]*)\}', css).group(1)
    ink = re.search(r'--primary-ink:light-dark\((#[0-9a-f]{6}),(#[0-9a-f]{6})\)', rule)
    assert S.contrast_ratio(ink.group(1), '#faf6ee') >= S.MORE_TARGET


def test_tp_hue_gets_the_pure_css_envelope():
    css = ui_css(B.bundle(page('<div style="--tp-hue: 200">x</div>'))[0])
    assert '[style*="--tp-hue"]{--primary:oklch(0.50 0.075 var(--tp-hue,' in css


def test_hover_tokens_mix_in_oklab_only():
    css = ui_css(B.bundle(DUO.read_text(encoding='utf-8'))[0])
    assert 'color-mix(in oklab,var(--primary) 86%,var(--foreground))' in css
    assert 'in oklch' not in css


def test_bundled_css_has_no_important_or_external_loads():
    css = ui_css(B.bundle(DUO.read_text(encoding='utf-8'))[0])
    assert '!' + 'important' not in css
    assert 'url(' not in css and '@import' not in css


def test_unknown_values_warn_and_do_not_break_the_page():
    out, meta = B.bundle(page('<p data-ui-accent="nope" data-ui-theme="nope" data-ui-hue="400">x</p>'))
    assert len(meta['warnings']) == 3
    assert 'nope' not in ui_css(out)


def test_dogfood_duo_plan_passes_server_validation_in_both_modes():
    source = DUO.read_text(encoding='utf-8')
    out, meta = B.bundle(source)
    ok, errors, warnings = server.validate_html(out)
    assert ok, errors
    assert warnings == []
    assert meta['customCssBytes'] < 2048
    assert meta['hasDarkMode'] is True
    css = ui_css(out)
    root = re.search(r':root\{color-scheme:light dark;([^}]*)\}', css).group(1)
    for key in ('background', 'foreground', 'card', 'primary', 'primary-ink', 'chart-1', 'chart-3'):
        assert re.search(rf'--{key}:light-dark\(#[0-9a-f]{{6}},#[0-9a-f]{{6}}\)', root), key


def test_dogfood_author_css_uses_only_tokens_for_color():
    style = re.search(r'<style>(.*?)</style>', DUO.read_text(encoding='utf-8'), re.S).group(1)
    assert not re.search(r'#[0-9a-fA-F]{3,6}\b|rgb\(|hsl\(', style)


def test_minify_keeps_media_and_calc_spaces():
    css = B.minify('@media (min-width: 481px) and (max-width: 700px) { a { b: calc(1px + 2px); } }')
    assert css == '@media (min-width:481px) and (max-width:700px){a{b:calc(1px + 2px)}}'


def test_chunk_conditions_are_known():
    composer = B.Composer(B.scan(page()), 'standalone')
    for _, _, condition, _ in B.chunks():
        composer.wants(condition)


def test_cli_bundle_writes_the_page_and_metadata(tmp_path):
    out = tmp_path / 'out.html'
    result = subprocess.run(
        [sys.executable, '-m', 'tailplan_ui', 'bundle', str(DUO), '-o', str(out), '--metadata'],
        cwd=ROOT, capture_output=True, text=True, check=True)
    meta = json.loads(result.stdout)
    assert meta['uiVersion'] == '1.0.0'
    assert out.read_text(encoding='utf-8').count('id="tp-ui"') == 1


def test_cli_solve_prints_both_modes():
    result = subprocess.run([sys.executable, '-m', 'tailplan_ui', 'solve', '#1d4ed8'],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    assert set(data) == {'light', 'dark'}
