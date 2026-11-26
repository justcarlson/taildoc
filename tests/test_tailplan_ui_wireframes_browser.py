"""Opt-in browser checks for the Tailplan UI wireframe kit. The fast checks live in test_tailplan_ui_wireframes.py."""
import os
from pathlib import Path

import pytest

from tailplan_ui import bundler as B

pytestmark = pytest.mark.skipif(not os.getenv('TAILPLAN_BROWSER_TESTS'), reason='Set TAILPLAN_BROWSER_TESTS=1 for browser checks')

EXAMPLE = Path(__file__).resolve().parents[1] / 'tailplan_ui' / 'examples' / 'wireframe-duo.html'
DEVICES = {
    'duo-folded': (382, 580), 'duo-landscape': (594, 368), 'duo-unfolded': (669, 867), 'duo-wide': (951, 585),
    'iphone': (390, 844), 'ipad': (768, 1024), 'browser': (1280, 800),
}
VIEWPORTS = [(320, 568), (360, 800), (382, 580), (390, 844), (594, 368), (669, 867), (768, 1024), (951, 585), (1280, 800)]
HEAD = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="tailplan-ui" content="1"><title>T</title></head>'

SHELL_JS = """() => [...document.querySelectorAll('[data-slot="app-shell"]')].map(shell => {
  const screen = shell.closest('[data-slot="device-screen"]');
  const kids = n => shell.querySelector(`:scope > [data-slot="${n}"]`);
  return {width: screen.clientWidth, sidebar: getComputedStyle(kids('sidebar')).display,
          tabs: getComputedStyle(kids('tab-bar')).display, device: shell.closest('[data-device]')?.dataset.device};
}).filter(s => s.width > 0)"""


def stamped(body, head=HEAD):
    return B.bundle(head + '<body>' + body + '</body></html>')[0]


@pytest.fixture(scope='module')
def example_html():
    return B.bundle(EXAMPLE.read_text(encoding='utf-8'))[0]


def open_page(new_context, html, width, height, scheme='light'):
    context = new_context(viewport={'width': width, 'height': height}, color_scheme=scheme)
    pg = context.new_page()
    problems = []
    pg.on('pageerror', lambda error: problems.append(str(error)))
    pg.on('console', lambda message: problems.append(message.text) if message.type == 'error' else None)
    pg.set_content(html)
    return pg, problems


def screen_frame(device, inner, attrs=''):
    return f'<div data-slot="device-frame" data-device="{device}"{attrs}><div data-slot="device-screen">{inner}</div></div>'


@pytest.mark.parametrize('scheme', ['light', 'dark'])
@pytest.mark.parametrize('width,height', VIEWPORTS)
def test_example_never_scrolls_sideways_and_app_shells_follow_their_screen(new_context, example_html, width, height, scheme):
    pg, problems = open_page(new_context, example_html, width, height, scheme)
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    assert pg.evaluate('document.body.scrollWidth <= document.documentElement.clientWidth')
    assert pg.locator('[data-slot="device-screen"] [data-slot="device-screen"]').count() == 0
    shells = pg.evaluate(SHELL_JS)
    assert len(shells) >= 12 and all(s['device'] for s in shells)
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


def test_same_page_shows_narrow_and_wide_layouts_side_by_side(new_context, example_html):
    pg, _ = open_page(new_context, example_html, 1280, 800)
    shells = {s['device']: s for s in pg.evaluate(SHELL_JS)}
    folded = pg.evaluate("""() => document.querySelector('#frames [data-device="duo-folded"] [data-slot="device-screen"]').clientWidth""")
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
    assert pg.evaluate('innerWidth') == 1280


@pytest.mark.parametrize('scheme', ['light', 'dark'])
def test_wire_and_graphite_islands_paint_differently(new_context, example_html, scheme):
    pg, _ = open_page(new_context, example_html, 1280, 800, scheme)
    probe = """theme => {
      const root = document.querySelector(`#apps [data-island="${theme}"]`);
      const get = (sel, props) => { const cs = getComputedStyle(root.querySelector(sel)); return props.map(p => cs[p]); };
      return {row: get('[data-slot=list-row]', ['borderBottomStyle', 'borderBottomColor', 'fontFamily']),
              bar: get('[data-slot=app-bar]', ['backgroundColor', 'borderBottomStyle', 'borderBottomColor']),
              search: get('[data-slot=search]', ['borderBottomStyle', 'borderBottomColor', 'borderTopStyle']),
              chip: get('[data-slot=filter-bar] > :nth-child(2)', ['borderLeftStyle', 'borderBottomLeftRadius']),
              active: get('[data-slot=filter-bar] > :first-child', ['fontWeight', 'borderBottomWidth', 'borderBottomStyle']),
              shelf: get('[data-slot=shelf-strip] > :nth-child(2)', ['borderLeftStyle', 'borderTopStyle', 'borderBottomStyle']),
              box: get('[data-slot=wire-box]', ['backgroundImage', 'borderTopStyle']),
              art: get('[data-slot=empty-state] [data-part=art]', ['backgroundImage'])};
    }"""
    wire, hifi = pg.evaluate(probe, 'wire'), pg.evaluate(probe, 'graphite')
    assert wire['row'][0] == 'dashed' and hifi['row'][0] == 'solid'
    assert wire['row'][1] != hifi['row'][1]
    assert wire['row'][2] != hifi['row'][2] and 'monospace' in wire['row'][2] and 'system-ui' in hifi['row'][2]
    assert wire['bar'][0] != hifi['bar'][0] and wire['bar'][2] != hifi['bar'][2]
    assert wire['search'][:2] != hifi['search'][:2]
    # Fidelity shows in the line style, never in a radius: wire is dashed, hi-fi is solid.
    assert wire['chip'] == ['dashed', '0px'] and hifi['chip'] == ['solid', '0px']
    assert wire['active'][:2] == hifi['active'][:2] == ['700', '2px']
    assert wire['shelf'] == ['dashed', 'none', 'none'] and hifi['shelf'] == ['solid', 'none', 'none']
    assert wire['search'][2] == hifi['search'][2] == 'none'
    assert 'gradient' in wire['box'][0] and 'gradient' in hifi['box'][0]
    assert wire['box'][1] == 'dashed' and hifi['box'][1] == 'solid'
    assert 'gradient' in wire['art'][0] and hifi['art'][0] == 'none'


def test_standalone_primitives_take_wire_or_hifi_defaults_without_an_island(new_context):
    body = ('<div data-slot="wire-box" data-label="x"></div>'
            + screen_frame('iphone', '<div data-slot="app-shell"><div data-part="body">x</div></div>'))
    probe = """() => ({art: getComputedStyle(document.querySelector('[data-slot=wire-box]')).backgroundImage,
                      font: getComputedStyle(document.querySelector('[data-slot=app-shell]')).fontFamily})"""
    pg, _ = open_page(new_context, stamped(body), 800, 900)
    hifi = pg.evaluate(probe)
    assert hifi['art'] == 'none' and 'monospace' not in hifi['font']
    wire_head = HEAD.replace('content="1"', 'content="1 theme=wire"')
    pg, _ = open_page(new_context, stamped(body, wire_head), 800, 900)
    wire = pg.evaluate(probe)
    assert 'gradient' in wire['art'] and 'monospace' in wire['font']


@pytest.mark.parametrize('scheme,alpha', [('light', 0.32), ('dark', 0.56)])
def test_modal_scrim_is_a_dark_veil_in_both_color_schemes(new_context, scheme, alpha):
    body = screen_frame('iphone', '<div data-slot="app-shell"><div data-part="body">x</div></div>'
                        '<div data-slot="sheet-preview"><div data-part="title">Share</div></div>')
    pg, _ = open_page(new_context, stamped(body), 800, 900, scheme)
    shadow = pg.evaluate("getComputedStyle(document.querySelector('[data-slot=sheet-preview]')).boxShadow")
    import re
    found = re.search(r'/\s*([\d.]+)\)|rgba?\([^)]*,\s*([\d.]+)\)', shadow)
    assert found, shadow
    assert float(found.group(1) or found.group(2)) == pytest.approx(alpha, abs=0.01)
    assert not re.search(r'rgb\(\s*(?:[1-9]\d{1,2})', shadow), shadow


@pytest.mark.parametrize('kind,columns', [('duo-folded', 2), ('duo-unfolded', 2), ('ipad', 4), ('duo-wide', 4)])
def test_inspector_grid_keeps_values_readable_beside_the_sidebar(new_context, kind, columns):
    grid = ('<dl data-slot="inspector-grid"><dt>Status</dt><dd>Open</dd><dt>Owner</dt><dd>Ada</dd>'
            '<dt>Width</dt><dd>669</dd><dt>Height</dt><dd>867</dd></dl>')
    shell = ('<div data-slot="app-shell"><header data-slot="app-bar"><span data-part="title">Inspector</span></header>'
             '<nav data-slot="sidebar"><span>Inbox</span></nav><div data-part="body">' + grid + '</div>'
             '<nav data-slot="tab-bar"><span>Inbox</span></nav></div>')
    pg, _ = open_page(new_context, stamped(screen_frame(kind, shell)), 1280, 1100)
    info = pg.evaluate("""() => { const g = document.querySelector('[data-slot=inspector-grid]');
      return {cols: getComputedStyle(g).gridTemplateColumns.split(' ').length,
              dd: Math.min(...[...g.querySelectorAll('dd')].map(e => e.getBoundingClientRect().width))}; }""")
    assert info['cols'] == columns, info
    assert info['dd'] >= 90, info


def test_landscape_safe_area_insets_the_app_by_the_safari_strips(new_context):
    inner = ('<div data-slot="app-shell"><div data-part="body">x</div></div>'
             '<div data-slot="safari-strip">84 pt</div><div data-slot="safari-strip" data-side="end">84 pt</div>')
    pg, _ = open_page(new_context, stamped(screen_frame('duo-landscape', inner, ' data-safe-area')), 1600, 900)
    inset = pg.evaluate("parseFloat(getComputedStyle(document.querySelector('[data-slot=app-shell]')).paddingLeft)")
    strip = pg.locator('[data-slot=safari-strip]').first.bounding_box()
    assert inset == pytest.approx(84, abs=0.5) and strip['width'] == pytest.approx(84, abs=0.5)
    # On a small host the screen shrinks, and the strips and the inset shrink with it.
    pg, _ = open_page(new_context, stamped(screen_frame('duo-landscape', inner, ' data-safe-area')), 320, 568)
    screen = pg.locator('[data-slot=device-screen]').bounding_box()
    strip = pg.locator('[data-slot=safari-strip]').first.bounding_box()
    inset = pg.evaluate("parseFloat(getComputedStyle(document.querySelector('[data-slot=app-shell]')).paddingLeft)")
    assert strip['width'] < 84 and strip['width'] / screen['width'] == pytest.approx(84 / 594, rel=0.02)
    assert inset == pytest.approx(strip['width'], abs=0.5)
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')


def test_flow_children_of_a_screen_do_not_overlap(new_context):
    inner = '<p id="a">One</p><p id="b">Two</p>'
    pg, _ = open_page(new_context, stamped(screen_frame('duo-unfolded', inner)), 1200, 1000)
    a, b = pg.locator('#a').bounding_box(), pg.locator('#b').bounding_box()
    assert b['y'] >= a['y'] + a['height'] - 0.5


def test_safari_pill_keeps_its_measured_height_and_centre(new_context):
    inner = '<div data-slot="safari-pill">example.com</div>'
    pg, _ = open_page(new_context, stamped(screen_frame('duo-landscape', inner)), 1600, 900)
    pill = pg.locator('[data-slot=safari-pill]').bounding_box()
    screen = pg.locator('[data-slot=device-screen]').bounding_box()
    assert pill['height'] == 56
    assert abs((pill['x'] + pill['width'] / 2) - (screen['x'] + screen['width'] / 2)) < 1


def test_alias_slots_size_like_data_device_frames(new_context):
    body = ''.join(f'<div data-slot="{d}"><div data-slot="device-screen"></div></div>' for d in DEVICES)
    pg, _ = open_page(new_context, stamped(body), 1600, 900)
    for device, (w, h) in DEVICES.items():
        size = pg.evaluate(
            """d => { const e = document.querySelector(`[data-slot="${d}"] [data-slot="device-screen"]`);
                      const cs = getComputedStyle(e); return [e.clientWidth, e.clientHeight, cs.containerName, cs.containerType]; }""", device)
        assert size[:2] == [w, h] and size[2] == 'screen' and size[3] == 'size'


@pytest.mark.parametrize('width,height', [(390, 844), (1280, 800)])
def test_stage_switches_work_with_zero_javascript(new_context, example_html, width, height):
    pg, problems = open_page(new_context, example_html, width, height)
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
    assert visible('[data-device="duo-folded"]') == 2
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
    tops = stage.locator('[data-pane]').evaluate_all('es => es.map(e => e.getBoundingClientRect().top)')
    assert (abs(tops[0] - tops[1]) < 1) == (width >= 1280)
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


def radio(ctl, value, checked=False):
    return f'<label><input type="radio" name="x-{ctl}" data-ctl="{ctl}" value="{value}"{" checked" if checked else ""}>{value}</label>'


def test_stage_works_with_theme_islands_alias_frames_and_one_resizable_frame(new_context):
    aliases = ''.join(f'<div data-slot="{d}" data-alias="{d}"><div data-slot="device-screen"></div></div>' for d in DEVICES)
    controls = (radio('fidelity', 'wire', True) + radio('fidelity', 'graphite')
                + ''.join(radio('viewport', d, d == 'iphone') for d in DEVICES))
    body = ('<div data-slot="mockup-stage"><div data-part="controls">' + controls + '</div><div data-part="preview">'
            '<div id="w" data-ui-theme="wire">wire</div><div id="g" data-ui-theme="graphite">graphite</div>'
            '<div data-slot="device-frame" id="one"><div data-slot="device-screen"></div></div>' + aliases + '</div></div>')
    pg, _ = open_page(new_context, stamped(body), 1600, 900)
    shown = lambda sel: pg.locator(sel).evaluate('e => e.getClientRects().length > 0')
    # Fidelity works with data-ui-theme alone, without data-variant.
    assert shown('#w') and not shown('#g')
    pg.locator('label:has(input[value="graphite"])').click()
    assert not shown('#w') and shown('#g')
    # Only the alias that matches the viewport shows. A plain device-frame takes its size from the radio.
    for device, (w, h) in DEVICES.items():
        pg.locator(f'label:has(input[data-ctl="viewport"][value="{device}"])').click()
        assert [shown(f'[data-alias="{d}"]') for d in DEVICES] == [d == device for d in DEVICES]
        size = pg.evaluate("() => { const e = document.querySelector('#one [data-slot=device-screen]'); return [e.clientWidth, e.clientHeight]; }")
        assert size == [w, h], (device, size)


FLAT_JS = """() => {
  const sel = {
    icon: '[data-part=icon]:not([data-shape=round])', action: '[data-slot=app-bar] > [data-part=action]',
    chip: '[data-slot=filter-bar] > *', search: '[data-slot=search]', shelf: '[data-slot=shelf-strip] > *',
    box: '[data-slot=wire-box]', inspector: '[data-slot=inspector-grid]', empty: '[data-slot=empty-state] [data-part=art]',
    emptyAction: '[data-slot=empty-state] [data-part=action]', sheet: '[data-slot=sheet-preview]',
    palette: '[data-slot=command-palette]', kbd: '[data-part=kbd]', side: '[data-slot=sidebar] > [aria-current]',
    seg: '[data-part=seg]', preview: '[data-part=preview]', variant: '[data-variant]', pane: '[data-part=pane]'};
  const radii = e => ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomLeftRadius', 'borderBottomRightRadius'].map(p => getComputedStyle(e)[p]);
  const out = {};
  for (const [k, s] of Object.entries(sel)) out[k] = [...document.querySelectorAll(s)].flatMap(radii);
  const hw = s => radii(document.querySelector(s));
  return {flat: out, frame: hw('[data-slot=device-frame]'), screen: hw('[data-slot=device-screen]'),
          pill: hw('[data-slot=safari-pill]'), pin: hw('[data-slot=pin]'), avatar: hw('[data-part=icon][data-shape=round]')};
}"""


@pytest.mark.parametrize('scheme', ['light', 'dark'])
def test_app_slots_have_square_corners_and_hardware_chrome_stays_rounded(new_context, example_html, scheme):
    pg, _ = open_page(new_context, example_html, 1280, 800, scheme)
    info = pg.evaluate(FLAT_JS)
    for name, values in info['flat'].items():
        assert values, name
        assert set(values) == {'0px'}, (name, values)
    for name in ('frame', 'screen'):
        assert all(float(v[:-2]) > 0 for v in info[name]), (name, info[name])
    assert info['pill'] == ['999px'] * 4 or all(float(v[:-2]) >= 28 for v in info['pill']), info['pill']
    assert all(v.endswith('%') or float(v[:-2]) >= 10 for v in info['avatar']), info['avatar']
    assert all(v.endswith('%') or float(v[:-2]) >= 10 for v in info['pin']), info['pin']
