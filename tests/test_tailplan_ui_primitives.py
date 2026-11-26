"""Checks for the Tailplan UI primitives, blocks, registry, and icon sprite."""
import json
import re
from pathlib import Path

import pytest

import tailplan_ui
from tailplan_ui import bundler as B
from tailplan_ui import icons, registry

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / 'tailplan_ui'
LAYER_DIRS = {'primitives': 'tp.primitives', 'blocks': 'tp.blocks'}
STYLE_RE = re.compile(r'<style id="tp-ui" data-tailplan-ui="[^"]+">(.*?)</style>', re.S)
HEAD = '<!doctype html><html lang="en"><head><meta charset="utf-8">' \
       '<meta name="viewport" content="width=device-width, initial-scale=1">' \
       '<meta name="tailplan-ui" content="1"><title>T</title></head><body>'


def css_files():
    return sorted(p for d in LAYER_DIRS for p in (UI / d).glob('*.css'))


def page(body):
    return HEAD + body + '</body></html>'


def ui_css(html):
    return STYLE_RE.search(B.bundle(html)[0]).group(1)


def items():
    return registry.catalog()['items']


def test_eighteen_primitives_and_twelve_blocks():
    cats = [i['category'] for i in items()]
    assert cats.count('primitives') == 18
    assert cats.count('blocks') == 12
    assert len(icons.icon_names()) == 24


@pytest.mark.parametrize('path', css_files(), ids=lambda p: f'{p.parent.name}/{p.name}')
def test_every_rule_sits_in_its_layer(path):
    text = path.read_text(encoding='utf-8')
    layer = LAYER_DIRS[path.parent.name]
    code = B.strip_comments(text)
    assert code.count('@layer') == code.count(f'@layer {layer} {{')
    depth, top = 0, []
    for ch in code:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        elif depth == 0 and not ch.isspace():
            top.append(ch)
    assert ''.join(top).replace(f'@layer{layer}', '').replace('{', '').replace('}', '') == ''


@pytest.mark.parametrize('path', css_files(), ids=lambda p: f'{p.parent.name}/{p.name}')
def test_css_files_follow_the_house_rules(path):
    text = path.read_text(encoding='utf-8')
    assert '!important' not in text
    assert '600px' not in text and '640px' not in text
    assert '@chunk' in text
    assert '<script' not in text and '<form' not in text
    for rule in re.finditer(r'(?m)^\s{2}([^\s@/}][^{]*)\{', text):
        selector = rule.group(1)
        if selector.startswith(':where('):
            continue
        assert selector.startswith((':where(', '@')) or ':where(' in selector, selector


def test_layer_order_puts_primitives_before_blocks_after_base():
    css = ui_css(page('<button data-slot="button">x</button><div data-slot="verdict">x</div>'))
    first = css.index('@layer tp.reset,tp.tokens,tp.density,tp.base,tp.primitives,tp.blocks,tp.wireframes;')
    assert first == 0
    assert css.index('@layer tp.primitives{') < css.index('@layer tp.blocks{')
    assert css.index('@layer tp.base{') < css.index('@layer tp.primitives{')


def test_chunks_are_tree_shaken_by_slot():
    plain = ui_css(page('<p>x</p>'))
    assert '@layer tp.primitives{' not in plain and '@layer tp.blocks{' not in plain
    button = ui_css(page('<button data-slot="button">x</button>'))
    assert '[data-slot="button"]' in button
    assert '[data-slot="verdict"]' not in button and '[data-slot="dialog"]' not in button
    verdict = ui_css(page('<div data-slot="verdict">x</div>'))
    assert '[data-slot="verdict"]' in verdict and '[data-slot="button"]' not in verdict


def test_a_subslot_loads_its_parent_chunk():
    css = ui_css(page('<div data-slot="card-footer">x</div>'))
    assert '[data-slot="card-footer"]' in css


def test_every_chunk_is_reachable_and_every_slot_has_css():
    by_id = {c[1]: c for c in B.chunks()}
    for item in items():
        chunk = by_id[item['chunk']]
        slots = set(chunk[2].removeprefix('slot:').split('|'))
        assert slots == {item['slot'], *item['subslots']}, item['slot']
        css = ui_css(page(item['markup']))
        assert f'[data-slot="{item["slot"]}"]' in css, item['slot']


def test_the_full_catalog_bundles_without_important_or_bad_breakpoints():
    everything = page(''.join(i['markup'] for i in items()))
    css = ui_css(everything)
    assert '!important' not in css
    assert not re.search(r'(?<![\d.])(?:600|640)px', css)
    assert B.bundle(everything)[1]['warnings'] == []


def test_registry_items_have_the_required_fields():
    seen = set()
    for item in items():
        assert {'slot', 'category', 'chunk', 'summary', 'subslots', 'variants', 'markup', 'zeroJs'} <= set(item)
        assert item['zeroJs'] is True
        assert item['category'] in {'primitives', 'blocks'}
        assert item['slot'] not in seen
        seen.add(item['slot'])
        assert item['summary'] and item['markup']


def test_registry_markup_has_no_form_or_script():
    for item in items():
        assert not re.search(r'<(?:form|script)\b', item['markup'], re.I), item['slot']
        assert any(f'data-slot="{s}"' in item['markup'] for s in (item['slot'], *item['subslots'])), item['slot']
        assert not re.search(r'\son\w+\s*=', item['markup'], re.I), item['slot']


def test_registry_markup_gets_no_lint_warnings():
    from tailplan_ui import lint
    for item in items():
        found = lint.lint(page(item['markup']))
        assert [f['code'] for f in found] == [], item['slot']


def test_known_slots_and_get_item():
    known = registry.known_slots()
    assert {'button', 'card-footer', 'verdict', 'spec-row'} <= known
    assert {'prose', 'device-screen', 'kbd-hint'} <= known
    assert registry.get_item('card-footer')['slot'] == 'card'
    assert registry.get_item('button')['category'] == 'primitives'
    assert registry.get_item('nope') is None
    assert tailplan_ui.known_slots() == known


def test_catalog_lists_colors_from_the_color_package():
    cat = registry.catalog()
    assert cat['version'] == tailplan_ui.__version__
    assert 'warm-editorial' in cat['presets'] and 'paper' in cat['surfaces']
    assert 'cobalt' in cat['accents'] and 'editorial' in cat['viz']
    assert cat['icons'] == icons.icon_names()
    cat['items'].clear()
    assert registry.catalog()['items']


def test_wireframes_registry_items_merge(tmp_path):
    shutil_tree = tmp_path / 'ui'
    (shutil_tree / 'wireframes').mkdir(parents=True)
    (shutil_tree / 'registry.json').write_text(
        json.dumps({'icons': [], 'items': [{'slot': 'a', 'subslots': []}]}), encoding='utf-8')
    (shutil_tree / 'wireframes' / 'registry_items.json').write_text(
        json.dumps({'items': [{'slot': 'wf', 'category': 'wireframes', 'subslots': []},
                              {'slot': 'a', 'category': 'wireframes'}, {'nope': 1}]}), encoding='utf-8')
    merged = registry.load(shutil_tree)
    assert [i['slot'] for i in merged['items']] == ['a', 'wf']
    assert 'category' not in merged['items'][0]
    (shutil_tree / 'wireframes' / 'registry_items.json').write_text(
        json.dumps([{'slot': 'wf2', 'category': 'wireframes'}]), encoding='utf-8')
    assert [i['slot'] for i in registry.load(shutil_tree)['items']] == ['a', 'wf2']
    (shutil_tree / 'wireframes' / 'registry_items.json').unlink()
    assert [i['slot'] for i in registry.load(shutil_tree)['items']] == ['a']


def test_icon_sprite_holds_requested_symbols():
    sprite = icons.icon_sprite(['check', 'x', 'check'])
    assert sprite.count('<symbol') == 2
    assert 'id="tp-icon-check"' in sprite and 'viewBox="0 0 24 24"' in sprite
    assert 'hidden' in sprite and 'aria-hidden="true"' in sprite
    assert icons.icon_sprite().count('<symbol') == 24
    assert registry.icon_sprite(['moon']).count('<symbol') == 1
    with pytest.raises(KeyError):
        icons.icon_sprite(['nope'])
    assert not re.search(r'<(?:script|style|image)\b|href=', icons.icon_sprite(), re.I)


def test_no_forbidden_breakpoint_anywhere_in_the_ui_css():
    for path in (UI / 'css').glob('*.css'):
        assert not re.search(r'@media[^{]*(?<![\d.])(?:600|640)px', path.read_text(encoding='utf-8')), path.name
