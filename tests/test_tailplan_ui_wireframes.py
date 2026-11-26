"""Checks for the Tailplan UI wireframe kit: device frames, app primitives, and the zero-JS stage."""
import json
import os
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
VIEWPORTS = [(320, 568), (360, 800), (382, 580), (390, 844), (594, 368), (669, 867), (951, 585), (1280, 800)]
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

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.slots.update(v for k, v in attrs if k == 'data-slot')


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
        assert 'script' not in found.tags and 'form' not in found.tags
    assert set(slots) >= set(APP_SLOTS) | set(OVERLAY_SLOTS) | set(DEVICES) | {'device-frame', 'device-screen', 'mockup-stage'}
    frame = next(i for i in items if i['slot'] == 'device-frame')
    assert frame['variants'] == list(DEVICES)
    assert {'safari-pill', 'safari-strip', 'ruler-mark'} <= set(frame['subslots'])


def test_registry_markup_bundles_without_warnings():
    for item in registry():
        _, meta = B.bundle(page(item['markup']))
        assert meta['warnings'] == [], item['slot']


def test_example_is_opted_in_zero_js_and_uses_every_primary_slot():
    html = EXAMPLE.read_text(encoding='utf-8')
    assert B.is_opted_in(html)
    found = parse(html)
    assert 'script' not in found.tags and 'form' not in found.tags
    primary = {i['slot'] for i in registry()} - set(DEVICES)
    assert primary <= found.slots, primary - found.slots
    out, meta = B.bundle(html)
    assert meta['warnings'] == [] and B.bundle(out)[0] == out
    assert meta['uiCssBytes'] <= 40 * 1024
    for device in DEVICES:
        assert f'data-device="{device}"' in html


# ---- browser checks. They follow tests/test_theme_browser.py: opt in with TAILPLAN_BROWSER_TESTS.

browser = pytest.mark.skipif(not os.getenv('TAILPLAN_BROWSER_TESTS'), reason='Set TAILPLAN_BROWSER_TESTS=1 for browser checks')

SHELL_JS = """() => [...document.querySelectorAll('[data-slot="app-shell"]')].map(shell => {
  const screen = shell.closest('[data-slot="device-screen"]');
  const kids = n => shell.querySelector(`:scope > [data-slot="${n}"]`);
  return {width: screen.clientWidth, sidebar: getComputedStyle(kids('sidebar')).display,
          tabs: getComputedStyle(kids('tab-bar')).display, device: screen.parentElement.dataset.device};
}).filter(s => s.width > 0)"""


@pytest.fixture(scope='module')
def example_html():
    return B.bundle(EXAMPLE.read_text(encoding='utf-8'))[0]


def open_example(new_context, html, width, height, scheme):
    context = new_context(viewport={'width': width, 'height': height}, color_scheme=scheme)
    pg = context.new_page()
    problems = []
    pg.on('pageerror', lambda error: problems.append(str(error)))
    pg.on('console', lambda message: problems.append(message.text) if message.type == 'error' else None)
    pg.set_content(html)
    return pg, problems


@browser
@pytest.mark.parametrize('scheme', ['light', 'dark'])
@pytest.mark.parametrize('width,height', VIEWPORTS)
def test_example_never_scrolls_sideways_and_app_shells_follow_their_screen(new_context, example_html, width, height, scheme):
    pg, problems = open_example(new_context, example_html, width, height, scheme)
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    assert pg.evaluate('document.body.scrollWidth <= document.documentElement.clientWidth')
    shells = pg.evaluate(SHELL_JS)
    assert len(shells) >= 12
    for s in shells:
        wide = s['width'] >= 620
        assert (s['sidebar'] != 'none') == wide, s
        assert (s['tabs'] != 'none') == (not wide), s
    # Every frame keeps its device aspect ratio at every host size.
    for device, (w, h) in DEVICES.items():
        box = pg.evaluate(
            """d => { const s = document.querySelector(`#frames [data-device="${d}"] [data-slot="device-screen"]`).getBoundingClientRect();
                      return [s.width, s.height]; }""", device)
        assert box[0] <= w
        assert box[0] / box[1] == pytest.approx(w / h, rel=0.01)
    assert not problems


@browser
def test_same_page_shows_narrow_and_wide_layouts_side_by_side(new_context, example_html):
    pg, _ = open_example(new_context, example_html, 1280, 800, 'light')
    shells = {s['device']: s for s in pg.evaluate(SHELL_JS)}
    folded = pg.evaluate("""() => { const s = document.querySelector('#frames [data-device="duo-folded"] [data-slot="device-screen"]'); return s.clientWidth; }""")
    assert folded == 382 and shells['duo-folded']['sidebar'] == 'none' and shells['duo-folded']['tabs'] != 'none'
    for device in ('duo-wide', 'browser', 'ipad'):
        width = pg.evaluate(
            """d => document.querySelector(`#frames [data-device="${d}"] [data-slot="device-screen"]`).clientWidth""", device)
        assert width >= 620
        row = pg.evaluate(
            """d => { const sh = document.querySelector(`#frames [data-device="${d}"] [data-slot="app-shell"]`);
              return [getComputedStyle(sh.querySelector(':scope > [data-slot=sidebar]')).display,
                      getComputedStyle(sh.querySelector(':scope > [data-slot=tab-bar]')).display]; }""", device)
        assert row == ['grid', 'none']
    # The host is 1280px wide. A narrow device on it stays narrow because the screen is the container.
    assert pg.evaluate('innerWidth') == 1280


@browser
@pytest.mark.parametrize('scheme', ['light', 'dark'])
def test_wire_and_graphite_islands_paint_differently(new_context, example_html, scheme):
    pg, _ = open_example(new_context, example_html, 1280, 800, scheme)
    probe = """theme => {
      const root = document.querySelector(`#apps [data-island="${theme}"]`);
      const get = (sel, props) => { const cs = getComputedStyle(root.querySelector(sel)); return props.map(p => cs[p]); };
      return {row: get('[data-slot=list-row]', ['borderBottomStyle', 'borderBottomColor', 'fontFamily']),
              bar: get('[data-slot=app-bar]', ['backgroundColor', 'borderBottomStyle', 'borderBottomColor']),
              search: get('[data-slot=search]', ['borderTopStyle', 'borderTopColor', 'borderTopLeftRadius']),
              chip: get('[data-slot=filter-bar] > :first-child', ['borderTopLeftRadius']),
              box: get('[data-slot=wire-box]', ['backgroundImage', 'borderTopStyle']),
              art: get('[data-slot=empty-state] [data-part=art]', ['backgroundImage'])};
    }"""
    wire, hifi = pg.evaluate(probe, 'wire'), pg.evaluate(probe, 'graphite')
    assert wire['row'][0] == 'dashed' and hifi['row'][0] == 'solid'
    assert wire['row'][1] != hifi['row'][1]
    assert wire['row'][2] != hifi['row'][2] and 'monospace' in wire['row'][2] and 'system-ui' in hifi['row'][2]
    assert wire['bar'][0] != hifi['bar'][0] and wire['bar'][2] != hifi['bar'][2]
    assert wire['search'][:2] != hifi['search'][:2]
    assert wire['chip'] != hifi['chip']
    assert 'gradient' in wire['box'][0] and 'gradient' in hifi['box'][0]
    assert wire['box'][1] == 'dashed' and hifi['box'][1] == 'solid'
    assert 'gradient' in wire['art'][0] and hifi['art'][0] == 'none'


@browser
@pytest.mark.parametrize('width,height', [(390, 844), (1280, 800)])
def test_stage_switches_work_with_zero_javascript(new_context, example_html, width, height):
    pg, problems = open_example(new_context, example_html, width, height, 'light')
    assert pg.locator('script').count() == 0 and pg.locator('form').count() == 0
    stage = pg.locator('[data-slot=mockup-stage]')
    visible = lambda sel: stage.locator(sel).evaluate_all('es => es.filter(e => e.getClientRects().length).length')
    click = lambda ctl, value: stage.locator(f'label:has(input[data-ctl="{ctl}"][value="{value}"])').click()
    # Fidelity
    assert visible('[data-variant="wire"]') == 2 and visible('[data-variant="graphite"]') == 0
    click('fidelity', 'graphite')
    assert visible('[data-variant="wire"]') == 0 and visible('[data-variant="graphite"]') == 2
    click('fidelity', 'wire')
    # Viewport
    assert stage.locator('[data-device="duo-folded"]').evaluate_all('es => es.filter(e => e.getClientRects().length).length') == 2
    click('viewport', 'iphone')
    assert visible('[data-device="iphone"]') == 2 and visible('[data-device="duo-folded"]') == 0
    click('viewport', 'duo-wide')
    assert visible('[data-device="duo-wide"]') == 2 and visible('[data-device="iphone"]') == 0
    click('viewport', 'duo-folded')
    # Before and after
    click('view', 'before')
    assert visible('[data-pane="before"]') == 1 and visible('[data-pane="after"]') == 0
    click('view', 'after')
    assert visible('[data-pane="before"]') == 0 and visible('[data-pane="after"]') == 1
    click('view', 'side')
    assert visible('[data-pane]') == 2
    # Side by side from 920px of stage width, stacked below.
    left = stage.locator('[data-pane]').evaluate_all('es => es.map(e => e.getBoundingClientRect().top)')
    assert (abs(left[0] - left[1]) < 1) == (width >= 1280)
    # Mode sets color-scheme on the preview.
    scheme = lambda: stage.locator('[data-part=preview]').evaluate('e => getComputedStyle(e).colorScheme')
    bg = lambda: stage.locator('[data-part=preview]').evaluate('e => getComputedStyle(e).backgroundColor')
    base = bg()
    click('mode', 'dark')
    assert scheme() == 'dark' and bg() != base
    click('mode', 'light')
    assert scheme() == 'light' and bg() == base
    click('mode', 'auto')
    assert bg() == base
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    assert not problems


@browser
def test_alias_slots_size_like_data_device_frames(new_context):
    body = ''.join(f'<div data-slot="{d}"><div data-slot="device-screen"></div></div>' for d in DEVICES)
    pg, _ = open_example(new_context, stamped(body), 1600, 900, 'light')
    for device, (w, h) in DEVICES.items():
        size = pg.evaluate(
            """d => { const e = document.querySelector(`[data-slot="${d}"] [data-slot="device-screen"]`);
                      const cs = getComputedStyle(e); return [e.clientWidth, e.clientHeight, cs.containerName, cs.containerType]; }""", device)
        assert size[:2] == [w, h] and size[2] == 'screen' and size[3] == 'size'


@browser
def test_landscape_safe_area_insets_the_app_by_the_safari_strips(new_context):
    body = ('<div data-slot="device-frame" data-device="duo-landscape" data-safe-area><div data-slot="device-screen">'
            '<div data-slot="app-shell"><div data-part="body">x</div></div></div></div>')
    pg, _ = open_example(new_context, stamped(body), 1600, 900, 'light')
    assert pg.evaluate("getComputedStyle(document.querySelector('[data-slot=app-shell]')).paddingLeft") == '84px'


@browser
def test_safari_overlays_have_their_measured_size(new_context):
    body = ('<div data-slot="device-frame" data-device="duo-landscape"><div data-slot="device-screen">'
            '<div data-slot="safari-strip">84 pt</div><div data-slot="safari-pill">example.com</div></div></div>')
    pg, _ = open_example(new_context, stamped(body), 1600, 900, 'light')
    pill = pg.locator('[data-slot=safari-pill]').bounding_box()
    strip = pg.locator('[data-slot=safari-strip]').bounding_box()
    screen = pg.locator('[data-slot=device-screen]').bounding_box()
    assert pill['height'] == 56 and strip['width'] == 84
    assert abs((pill['x'] + pill['width'] / 2) - (screen['x'] + screen['width'] / 2)) < 1
