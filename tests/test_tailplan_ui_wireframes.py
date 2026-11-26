"""Checks for the Tailplan UI wireframe kit: device frames, app primitives, and the zero-JS stage."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tailplan_ui import bundler as B

ROOT = Path(__file__).resolve().parents[1]
WIRE_DIR = ROOT / 'tailplan_ui' / 'wireframes'
EXAMPLE = ROOT / 'tailplan_ui' / 'examples' / 'wireframe-duo.html'
CSS_NAMES = ('app.css', 'frames.css', 'overlays.css', 'stage.css')
SCHEMA = {'slot', 'category', 'chunk', 'summary', 'subslots', 'variants', 'markup', 'zeroJs'}
DEVICES = {
    'duo-folded': (382, 580), 'duo-landscape': (594, 368), 'duo-unfolded': (669, 867), 'duo-wide': (951, 585),
    'iphone': (390, 844), 'ipad': (768, 1024), 'browser': (1280, 800),
}
APP_SLOTS = ['app-shell', 'app-bar', 'tab-bar', 'sidebar', 'filter-bar', 'search', 'shelf-strip', 'list-row',
             'sheet-preview', 'command-palette', 'inspector-grid', 'empty-state']
OVERLAY_SLOTS = ['safari-pill', 'safari-strip', 'ruler-mark', 'pin', 'pin-legend', 'wire-box']
HEAD = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="tailplan-ui" content="1"><title>T</title></head>'
CONDITION_RE = re.compile(r'always|standalone|island|(?:slot|attr):[\w-]+(?:\|[\w-]+)*')


def registry():
    return json.loads((WIRE_DIR / 'registry_items.json').read_text(encoding='utf-8'))


def wire_chunks():
    return [c for c in B.chunks() if c[0] in CSS_NAMES]


def page(body):
    return HEAD + '<body>' + body + '</body></html>'


def stamped(body):
    return B.bundle(page(body))[0]


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.slots = set()
        self.handlers = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.slots.update(v for k, v in attrs if k == 'data-slot')
        self.handlers += [k for k, _ in attrs if k.lower().startswith('on')]


def parse(html):
    p = Tags()
    p.feed(html)
    return p


# ---- fast checks

def test_four_css_files_exist_and_every_rule_sits_in_the_wireframes_layer():
    for name in CSS_NAMES:
        text = (WIRE_DIR / name).read_text(encoding='utf-8')
        assert '@chunk' in text
    for _, ident, _, css in wire_chunks():
        assert css.startswith('@layer tp.wireframes{') and css.endswith('}'), ident
        assert css.count('{') == css.count('}'), ident


def test_css_has_no_important_no_viewport_breakpoints_and_no_script_or_form():
    for name in CSS_NAMES:
        text = (WIRE_DIR / name).read_text(encoding='utf-8')
        assert '!important' not in text
        assert not re.search(r'\b(?:600|640)px', text), name
        assert not re.search(r'@media[^{]*(?:min|max)-(?:width|height)', text), name
        assert not re.search(r'<script|<form', text, re.I)
    assert '@container screen' in (WIRE_DIR / 'app.css').read_text(encoding='utf-8')
    assert 'container: screen / size' in (WIRE_DIR / 'frames.css').read_text(encoding='utf-8')


def test_every_chunk_condition_is_valid_for_the_bundler():
    composer = B.Composer(B.scan(page('<p>x</p>')), 'standalone')
    for _, ident, condition, _ in wire_chunks():
        assert CONDITION_RE.fullmatch(condition), (ident, condition)
        composer.wants(condition)


def test_bundler_tree_shakes_wireframe_chunks_by_slot():
    by_id = {ident: css for _, ident, _, css in wire_chunks()}
    assert set(by_id) >= {'wf-base', 'device-frame', 'wf-overlays', 'app-shell', 'app-browse', 'app-layers', 'app-data', 'mockup-stage'}
    assert not any(css in stamped('<p>plain</p>') for css in by_id.values())
    box = stamped('<div data-slot="wire-box"></div>')
    assert by_id['wf-overlays'] in box and by_id['wf-base'] in box
    assert not any(by_id[i] in box for i in ('device-frame', 'app-shell', 'mockup-stage'))
    shell = stamped('<div data-slot="app-bar"></div>')
    assert by_id['app-shell'] in shell and by_id['wf-overlays'] not in shell and by_id['mockup-stage'] not in shell
    alias = stamped('<div data-slot="duo-wide"></div>')
    assert by_id['device-frame'] in alias
    stage = stamped('<div data-slot="mockup-stage"></div>')
    assert by_id['mockup-stage'] in stage and by_id['app-data'] not in stage


def test_wireframe_css_loads_after_the_primitives_and_blocks_layer_names():
    css = B.build_css(page('<div data-slot="device-frame"></div>'))
    assert css.startswith('@layer tp.reset,tp.tokens,tp.density,tp.base,tp.primitives,tp.blocks,tp.wireframes;')
    assert css.count('@layer tp.wireframes{') >= 2


def test_registry_items_follow_the_schema_and_point_at_real_chunks():
    items = registry()
    assert isinstance(items, list)
    chunks = {ident: cond for _, ident, cond, _ in wire_chunks()}
    slots = [i['slot'] for i in items]
    assert len(slots) == len(set(slots))
    for item in items:
        assert set(item) == SCHEMA, item['slot']
        assert item['category'] == 'wireframes' and item['zeroJs'] is True
        assert all(isinstance(item[k], str) and item[k] for k in ('slot', 'chunk', 'summary', 'markup'))
        assert all(isinstance(item[k], list) and all(isinstance(v, str) for v in item[k]) for k in ('subslots', 'variants'))
        assert item['chunk'] in chunks, item['slot']
        assert chunks[item['chunk']].startswith('slot:') and item['slot'] in chunks[item['chunk']][5:].split('|'), item['slot']
        found = parse(item['markup'])
        assert item['slot'] in found.slots, item['slot']
        assert 'script' not in found.tags and 'form' not in found.tags and not found.handlers
    assert set(slots) >= set(APP_SLOTS) | set(OVERLAY_SLOTS) | set(DEVICES) | {'device-frame', 'device-screen', 'mockup-stage'}
    by_slot = {i['slot']: i for i in items}
    assert by_slot['device-frame']['variants'] == list(DEVICES)
    # A subslot that is also a top-level item would shadow its own entry in a registry lookup.
    assert all(i['subslots'] == [] for i in items)
    stage = by_slot['mockup-stage']
    assert stage['variants'] == []
    assert 'data-ctl=fidelity|mode|viewport|view' in stage['summary'] and 'data-ui-theme=wire|graphite' in stage['summary']
    for ctl in ('fidelity', 'view'):
        assert f'data-ctl="{ctl}"' in stage['markup']
    assert 'data-ui-theme="wire"' in stage['markup'] and 'data-ui-theme="graphite"' in stage['markup']


def test_registry_markup_bundles_without_warnings():
    for item in registry():
        _, meta = B.bundle(page(item['markup']))
        assert meta['warnings'] == [], item['slot']


def test_example_is_opted_in_zero_js_and_uses_every_primary_slot():
    html = EXAMPLE.read_text(encoding='utf-8')
    assert B.is_opted_in(html)
    found = parse(html)
    assert 'script' not in found.tags and 'form' not in found.tags and not found.handlers
    assert 'device-screen"><div data-slot="device-screen' not in html
    assert re.search(r'data-device="duo-landscape" data-safe-area', html)
    primary = {i['slot'] for i in registry()} - set(DEVICES)
    assert primary <= found.slots, primary - found.slots
    out, meta = B.bundle(html)
    assert meta['warnings'] == [] and B.bundle(out)[0] == out
    assert meta['uiCssBytes'] <= 40 * 1024
    for device in DEVICES:
        assert f'data-device="{device}"' in html




def stage_rules():
    text = (WIRE_DIR / 'stage.css').read_text(encoding='utf-8')
    return text[text.index('The rules below set state'):]


def test_stage_state_rules_add_no_specificity_and_load_after_the_frames():
    rules = stage_rules()
    selectors = [line.strip() for line in rules.splitlines() if line.strip().startswith(('[', ':'))]
    assert selectors and all(line.startswith(':where(') for line in selectors)
    by_file = [name for name, _, _, _ in wire_chunks()]
    assert by_file.index('stage.css') > by_file.index('frames.css')


def test_stage_hides_alias_frames_and_sizes_plain_frames_per_viewport():
    rules = stage_rules()
    for device, (w, h) in DEVICES.items():
        assert f'[data-ctl="viewport"][value="{device}"]:checked) :is([data-device]:not([data-device="{device}"])' in rules
        assert f':not([data-device])) {{ --dw: {w}; --dh: {h}; }}' in rules
        assert rules.count(f'[data-slot="{device}"]') == len(DEVICES) - 1
    assert '[data-ui-theme]:not([data-ui-theme="wire"])' in rules and '[data-ui-theme="wire"]' in rules


def test_scrim_is_dark_in_both_color_schemes_and_inspector_waits_for_760px():
    text = (WIRE_DIR / 'app.css').read_text(encoding='utf-8')
    assert text.count('light-dark(oklch(0% 0 0 / 32%), oklch(0% 0 0 / 56%))') == 2
    assert 'color-mix(in oklab, var(--foreground) 32%' not in text
    assert re.search(r'@container screen \(min-width: 760px\) \{\s*:where\(\[data-slot="inspector-grid"\]\)', text)


def test_frame_children_stack_in_flow_and_the_strip_scales_with_the_screen():
    frames = (WIRE_DIR / 'frames.css').read_text(encoding='utf-8')
    assert 'grid-area: 1 / 1' not in frames
    assert 'min(84px, 14.14cqw)' in frames and 'min(84px, 14.14cqw)' in (WIRE_DIR / 'overlays.css').read_text(encoding='utf-8')


HARDWARE = {
    'frames.css': ['calc(var(--radius, 4px) * 5)', 'calc(var(--radius, 4px) * 3)'],
    'overlays.css': ['999px', '50%'],
    'app.css': ['50%'],
}


def test_app_slots_are_flat_and_only_hardware_chrome_keeps_a_radius():
    for name in ('app.css', 'stage.css'):
        radii = re.findall(r'border-radius:\s*([^;}]+)', (WIRE_DIR / name).read_text(encoding='utf-8'))
        # app.css keeps two circles: the round avatar and the search lens glyph. stage.css keeps none.
        assert all(r.strip() in ('0', '50%') for r in radii), (name, radii)
        assert radii.count('50%') == (2 if name == 'app.css' else 0), name
    app = (WIRE_DIR / 'app.css').read_text(encoding='utf-8')
    assert app.count('--wf-pill: 0;') == 2 and '999px' not in app
    assert '--radius' not in ' '.join(re.findall(r'border-radius:[^;}]+', app))
    overlays = (WIRE_DIR / 'overlays.css').read_text(encoding='utf-8')
    assert re.findall(r'border-radius:\s*([^;}]+)', overlays).count('999px') == 1
    assert re.findall(r'border-radius:\s*([^;}]+)', overlays).count('50%') == 1
    for name, values in HARDWARE.items():
        text = (WIRE_DIR / name).read_text(encoding='utf-8')
        assert all(v in text for v in values), name


def test_registry_documents_the_hardware_chrome_allowlist():
    by_slot = {i['slot']: i['summary'] for i in registry()}
    for slot in ('device-frame', 'device-screen', 'safari-pill', 'pin'):
        assert 'Hardware chrome' in by_slot[slot], slot
    assert 'round avatar' in by_slot['list-row']
    assert 'hairlines' in by_slot['filter-bar'] and 'chips' not in by_slot['filter-bar']
    assert 'hairlines' in by_slot['shelf-strip'] and 'cards' not in by_slot['shelf-strip']


def test_example_island_is_open_not_a_rounded_card():
    html = EXAMPLE.read_text(encoding='utf-8')
    rule = re.search(r'\.island \{[^}]*\}', html).group(0)
    assert 'border-radius: 0' in rule and 'border-block-start' in rule and 'var(--radius)' not in rule
