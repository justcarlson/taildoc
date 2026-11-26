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
    assert ':where(:not(:root))[data-ui-theme="wire"]{' in css
    assert ('@scope ([data-ui-theme="wire"]:not(:root)) to ([data-ui-theme],[data-ui-surface])'
            '{:scope[data-ui-accent="cobalt"],[data-ui-accent="cobalt"]{') in css
    assert ':where([data-ui-theme],[data-ui-surface],[data-ui-mode]):not(:root)' in css
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


# Round 1 review fixes.

STAMP = '<style id="tp-ui" data-tailplan-ui="1.0.0">a{}</style>'


def test_package_exports_scan_and_opt_in():
    assert tailplan_ui.scan is B.scan
    assert tailplan_ui.is_opted_in is B.is_opted_in


@pytest.mark.parametrize('content,expected', [('theme=nord', True), ('', True), ('0', False), ('false', False),
                                              (' FALSE ', False)])
def test_meta_tag_opts_in_unless_off(content, expected):
    doc = page(head=f'<meta name="tailplan-ui" content="{content}">')
    assert B.scan(doc).opted_in is expected
    assert B.is_opted_in(doc) is expected


def test_no_meta_tag_does_not_opt_in():
    assert B.is_opted_in(page(html_attrs=' data-ui-theme="nord"')) is False


def test_strip_removes_only_the_real_stamp():
    meta = '<meta name="theme-color" content="#fff" data-tailplan-ui>'
    assert B.strip_stamp(page(head=meta + STAMP)) == page()


@pytest.mark.parametrize('keep', [
    '<style id="tp-ui-custom" data-tailplan-ui>a{}</style>',
    '<style data-id="tp-ui" data-tailplan-ui>a{}</style>',
    '<style id="tp-ui">a{}</style>',
    '<meta name="description" content="About data-tailplan-ui">',
    '<!-- ' + STAMP + ' -->',
    '<script>var s = \'' + STAMP + '\';</script>',
])
def test_strip_keeps_look_alike_markup(keep):
    doc = page(head=keep)
    assert B.strip_stamp(doc) == doc


@pytest.mark.parametrize('tag', ['title', 'textarea'])
def test_strip_and_scan_skip_rcdata_text(tag):
    text = f'<{tag}>{STAMP}<style>p{{--tp-hue:1}}</style></{tag}>'
    doc = page(text) if tag == 'textarea' else page(head=text)
    assert B.strip_stamp(doc) == doc
    f = B.scan(doc)
    assert not f.tp_hue_sheet and not f.custom_css


def test_tp_hue_in_a_comment_is_not_a_use():
    out, meta = B.bundle(page('<div style="color:red /* --tp-hue */">x</div>',
                              head='<style>/* --tp-hue: 20 */ p{color:red}</style>'))
    assert '--tp-hue,' not in ui_css(out)
    assert meta['warnings'] == []


def test_hue_envelope_never_lands_on_root():
    out, meta = B.bundle(page('<div data-ui-hue-var>x</div>', head='<style>div{--tp-hue:20}</style>'))
    css = ui_css(out)
    assert '[data-ui-hue-var],[style*="--tp-hue"]{--primary:oklch(' in css
    assert not re.search(r':root[^{]*\{--primary:oklch', css)
    assert meta['warnings'] == []


def test_sheet_tp_hue_without_the_opt_in_attribute_warns():
    out, meta = B.bundle(page('<div class="x">x</div>', head='<style>.x{--tp-hue:20}</style>'))
    assert '--tp-hue,' not in ui_css(out)
    assert any('data-ui-hue-var' in w for w in meta['warnings'])


def test_hover_tokens_follow_the_envelope_selector():
    css = ui_css(B.bundle(page('<div style="--tp-hue:20">x</div>'))[0])
    hover = re.search(r'([^{}]*)\{--primary-hover:', css).group(1)
    assert hover.endswith('[data-ui-hue-var],[style*="--tp-hue"]')


def test_root_attributes_never_take_island_paint():
    doc = page('<div data-ui-theme="nord">x</div>', html_attrs=' data-ui-theme="nord" data-ui-accent="cobalt"')
    css = ui_css(B.bundle(doc)[0])
    assert ':where(:not(:root))[data-ui-theme="nord"]{' in css
    assert '\n[data-ui-theme="nord"]{' not in css and '}[data-ui-theme="nord"]{' not in css
    cobalt = T_ACCENT('theme', 'nord', 'cobalt')
    root = re.search(r':root\{color-scheme:light dark;([^}]*)\}', css).group(1)
    assert f'--primary:{cobalt}' in root


def T_ACCENT(kind, ident, accent):
    from tailplan_ui.color import tokens as T
    light = T.accent_tokens(kind, ident, accent, S.LIGHT)['primary']
    dark = T.accent_tokens(kind, ident, accent, S.DARK)['primary']
    return f'light-dark({light},{dark})'


def test_island_modifiers_sit_in_a_donut_scope():
    doc = page('<aside data-ui-theme="nord" data-ui-accent="amber"><section data-ui-surface="slate">'
               '<p data-ui-accent="cobalt">x</p></section></aside>')
    css = ui_css(B.bundle(doc)[0])
    nord = '@scope ([data-ui-theme="nord"]:not(:root)) to ([data-ui-theme],[data-ui-surface]){'
    slate = '@scope ([data-ui-surface="slate"]:not(:root)) to ([data-ui-theme],[data-ui-surface]){'
    assert nord + ':scope[data-ui-accent="amber"],[data-ui-accent="amber"]{' in css
    assert slate + ':scope[data-ui-accent="cobalt"],[data-ui-accent="cobalt"]{' in css
    assert '[data-ui-theme="nord"] [data-ui-accent' not in css


def test_root_contrast_more_gets_normal_overrides():
    doc = page('<p data-ui-contrast="normal" data-ui-accent="amber">x</p>', html_attrs=' data-ui-contrast="more"')
    css = ui_css(B.bundle(doc)[0])
    rule = re.search(r'\[data-ui-contrast="normal"\]\[data-ui-accent="amber"\][^{]*\{([^}]*)\}', css).group(1)
    ink = re.search(r'--primary-ink:light-dark\((#[0-9a-f]{6}),', rule).group(1)
    assert S.contrast_ratio(ink, '#faf6ee') < S.MORE_TARGET


def test_contrast_matching_the_root_adds_no_rules():
    doc = page('<p data-ui-contrast="more">x</p>', html_attrs=' data-ui-contrast="more"')
    assert 'data-ui-contrast=' not in ui_css(B.bundle(doc)[0])


def test_ancestor_contrast_reaches_an_accent_below_it():
    doc = page('<div data-ui-contrast="more"><p data-ui-accent="amber">x</p></div>')
    css = ui_css(B.bundle(doc)[0])
    assert '[data-ui-contrast="more"] [data-ui-accent="amber"]' in css


def test_ancestor_contrast_reaches_an_island_root():
    doc = page('<div data-ui-contrast="more"><aside data-ui-theme="nord" data-ui-accent="amber">x</aside></div>')
    css = ui_css(B.bundle(doc)[0])
    scope = re.search(r'@scope \(\[data-ui-theme="nord"\]:not\(:root\)\)[^{]*\{(.*)', css).group(1)
    assert '[data-ui-contrast="more"] :scope[data-ui-accent="amber"]' in scope


@pytest.mark.parametrize('attr,value', [('seed', '#fff\n'), ('seed', '#ffff'), ('hue', '120\n'),
                                        ('hue', '١٢٠'), ('hue', '361')])
def test_strict_seed_and_hue_values_warn(attr, value):
    _, meta = B.bundle(page(f'<p data-ui-{attr}="{value}">x</p>'))
    assert len(meta['warnings']) == 1


@pytest.mark.parametrize('seed', ['12abc', '١٢٠', '#ffff', '400'])
def test_cli_solve_rejects_bad_seeds(seed):
    result = subprocess.run([sys.executable, '-m', 'tailplan_ui', 'solve', seed],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'seed must be a hex color or a hue' in result.stderr


def test_cli_solve_takes_a_decimal_hue():
    result = subprocess.run([sys.executable, '-m', 'tailplan_ui', 'solve', '212.5'],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    assert set(json.loads(result.stdout)) == {'light', 'dark'}


@pytest.mark.parametrize('wrap', ['<template>{}</template>', '<noscript>{}</noscript>'])
def test_first_style_ignores_inert_head_styles(wrap):
    doc = page(head=wrap.format('<style>a{}</style>') + '<style>p{color:red}</style>')
    out, _ = B.bundle(doc)
    assert out.index('id="tp-ui"') < out.index('<style>p{color:red}')
    assert out.index('id="tp-ui"') > out.index('<style>a{}')


def test_svg_styles_do_not_place_the_stamp():
    out, _ = B.bundle(page('<svg><style>a{}</style></svg>'))
    assert out.index('id="tp-ui"') < out.index('</head>') < out.index('<svg>')


def test_body_styles_do_not_pull_the_stamp_out_of_head():
    out, _ = B.bundle(page('<style>p{color:red}</style><p>x</p>'))
    head = out[:out.index('</head>')]
    assert 'id="tp-ui"' in head
    assert head.count('name="theme-color"') == 2


def test_root_warnings_are_not_repeated():
    _, meta = B.bundle(page(head='<meta name="tailplan-ui" content="theme=nope">',
                            html_attrs=' data-ui-accent="nope"'))
    assert meta['warnings'] == ['Unknown root data-ui-theme: nope', 'Unknown data-ui-accent: nope']


def test_unknown_meta_keys_and_bad_root_values_warn_and_fall_back():
    doc = page(head='<meta name="tailplan-ui" content="theme=nord colour=red contrast=max mode=sepia">')
    out, meta = B.bundle(doc)
    assert meta['warnings'] == ['Unknown tailplan-ui meta key: colour', 'Unknown root data-ui-contrast: max',
                                'Unknown root data-ui-mode: sepia']
    assert ':root{color-scheme:light dark;' in ui_css(out)
    assert meta['uiTheme'] == 'nord'


def test_minify_keeps_string_literals():
    css = B.minify('a::after { content: " > : ; }  /* x */"; } b { content: \'/* no */\'; }')
    assert css == 'a::after{content:" > : ; }  /* x */"}b{content:\'/* no */\'}'


def test_minify_drops_comments_with_quotes():
    assert B.minify('/* it\'s "x" */ a { b: c; }') == 'a{b:c}'


def make_ui_dir(tmp_path):
    import shutil
    shutil.copytree(ROOT / 'tailplan_ui' / 'css', tmp_path / 'css')
    for layer, name, text in [
            ('primitives', 'b.css', '/* @chunk tabs when=slot:tabs|tab */ @layer tp.primitives { x { a: b; } }'),
            ('primitives', 'a.css', '@layer tp.primitives { y { a: b; } }'),
            ('blocks', 'card.css', '/* @chunk card when=attr:data-card */ @layer tp.blocks { z { a: b; } }'),
            ('wireframes', 'w.css', '@layer tp.wireframes { w { a: b; } }')]:
        (tmp_path / layer).mkdir(exist_ok=True)
        (tmp_path / layer / name).write_text(text, encoding='utf-8')
    return tmp_path


def test_chunks_are_found_in_layer_order(tmp_path):
    ui = make_ui_dir(tmp_path)
    found = [(name, ident, cond) for name, ident, cond, _ in B.chunks(ui)]
    tail = found[-4:]
    assert tail == [('a.css', 'a', 'always'), ('b.css', 'tabs', 'slot:tabs|tab'),
                    ('card.css', 'card', 'attr:data-card'), ('w.css', 'w', 'always')]


@pytest.mark.parametrize('body,want', [('<p data-slot="tab">x</p>', True), ('<p data-slot="tabs">x</p>', True),
                                       ('<p data-slot="other">x</p>', False)])
def test_slot_conditions_take_alternatives(body, want):
    assert B.Composer(B.scan(page(body)), 'standalone').wants('slot:tabs|tab') is want


def test_attr_conditions_take_any_attribute():
    composer = B.Composer(B.scan(page('<div data-card>x</div>')), 'standalone')
    assert composer.wants('attr:data-card') is True
    assert composer.wants('attr:data-other') is False
    with pytest.raises(ValueError):
        composer.wants('attr:')


def test_real_chunk_dirs_load_when_present():
    names = [name for name, *_ in B.chunks()]
    assert names[:1] == ['layers.css']


def test_root_modifiers_stay_in_donut_scope_when_islands_exist():
    doc = (
        '<!doctype html><html data-ui-theme="catppuccin-latte"><head><title>x</title></head><body>'
        '<div data-ui-accent="amber"><p data-ui-contrast="more">root</p>'
        '<aside data-ui-theme="nord" data-ui-mode="dark"><section data-ui-contrast="more">'
        '<h3>Title</h3><a href="#">link</a></section></aside></div></body></html>'
    )
    css = B.build_css(doc)
    assert '@scope (:root) to ([data-ui-theme],[data-ui-surface]){' in css


def test_subtree_mode_flip_loads_island_repaint_chunk():
    doc = page('<aside data-ui-mode="dark"><h3>Title</h3><p>Body</p></aside>')
    css = B.build_css(doc)
    assert ':where([data-ui-theme],[data-ui-surface],[data-ui-mode]):not(:root)' in css


def test_html_root_overrides_meta_across_sibling_keys():
    doc = (
        '<!doctype html><html data-ui-surface="slate" data-ui-seed="#06b6d4"><head>'
        '<meta name="tailplan-ui" content="1; theme=warm-editorial accent=olive">'
        '<title>x</title></head><body><p>hi</p></body></html>'
    )
    _, meta = B.bundle(doc)
    assert meta['uiSurface'] == 'slate'
    assert meta['uiTheme'] is None
    assert meta['uiAccent'] == '#06b6d4'
    assert '#06b6d4' in meta['seeds']


def test_stray_inert_close_and_leading_comment_doctype():
    doc = (
        '<!doctype html><html><head><template></svg><style>a{color:red}</style></template>'
        '<style>p{color:blue}</style></head><body><p>x</p></body></html>'
    )
    out, _ = B.bundle(doc)
    assert out.index('id="tp-ui"') > out.index('</template>')
    assert out.index('id="tp-ui"') < out.index('<style>p{color:blue}</style>')
    bare = '<!-- leading comment -->\n<!doctype html><p>hi</p>'
    out_bare, _ = B.bundle(bare)
    assert out_bare.startswith('<!-- leading comment -->\n<!doctype html>')


def test_tp_hue_read_only_no_warning_and_attr_alternatives():
    doc = page('<style>.x{color:oklch(0.5 0.07 var(--tp-hue));content:"--tp-hue:1"}</style>'
               '<p style="--tp-hue:200">hi</p>')
    _, meta = B.bundle(doc)
    assert not any('--tp-hue' in w for w in meta['warnings'])
    composer = B.Composer(B.scan(page('<div data-card>x</div>')), 'standalone')
    assert composer.wants('attr:data-other|data-card') is True
    assert composer.wants('attr:data-a|data-b') is False
