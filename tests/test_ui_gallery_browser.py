"""Opt-in browser checks for the public /ui gallery. The fast checks live in test_ui_gallery.py."""
import os

import pytest

from tailplan_ui.color import tokens as T
from tailplan_web import ui_gallery

pytestmark = pytest.mark.skipif(not os.getenv('TAILPLAN_BROWSER_TESTS'), reason='Set TAILPLAN_BROWSER_TESTS=1 for browser checks')

VIEWPORTS = [(320, 568), (360, 800), (382, 580), (390, 844), (594, 368), (669, 867), (768, 1024), (951, 585), (1280, 800)]
DEVICES = {'duo-folded': (382, 580), 'duo-landscape': (594, 368), 'duo-wide': (951, 585)}
SHELL_JS = """() => [...document.querySelectorAll('#wireframes .g-frames [data-slot="app-shell"]')].map(shell => {
  const kids = n => shell.querySelector(`:scope > [data-slot="${n}"]`);
  return {width: shell.closest('[data-slot="device-screen"]').clientWidth,
          sidebar: getComputedStyle(kids('sidebar')).display, tabs: getComputedStyle(kids('tab-bar')).display};
})"""


@pytest.fixture(scope='module')
def page_html():
    return ui_gallery.render_gallery('')


def open_page(new_context, html, width=1280, height=800, scheme='light'):
    context = new_context(viewport={'width': width, 'height': height}, color_scheme=scheme)
    pg = context.new_page()
    problems = []
    pg.on('pageerror', lambda error: problems.append(str(error)))
    pg.on('console', lambda message: problems.append(message.text) if message.type == 'error' else None)
    pg.set_content(html)
    # Buttons fade their colors. Stop the fade so a read after a click sees the end color.
    pg.add_style_tag(content='*{transition:none!important}')
    return pg, problems


def decl(decls, name):
    return next(d.split(':', 1)[1] for d in decls if d.split(':', 1)[0] == name)


def prop(pg, selector, name):
    return pg.locator(selector).evaluate(f'e => getComputedStyle(e).getPropertyValue("{name}")').replace(' ', '')


def style(pg, selector, name):
    return pg.locator(selector).evaluate(f'e => getComputedStyle(e).{name}')


def pick(pg, group, value):
    pg.locator(f'label.mx-opt:has(#mx-{group}-{value})').click()
    assert pg.locator(f'#mx-{group}-{value}').is_checked()


@pytest.mark.parametrize('scheme', ['light', 'dark'])
@pytest.mark.parametrize('width,height', VIEWPORTS)
def test_gallery_never_scrolls_sideways_and_frames_keep_their_shape(new_context, page_html, width, height, scheme):
    pg, problems = open_page(new_context, page_html, width, height, scheme)
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    assert pg.evaluate('document.body.scrollWidth <= document.documentElement.clientWidth')
    assert pg.evaluate("document.querySelectorAll('script, form').length") == 0
    for device, (w, h) in DEVICES.items():
        box = pg.evaluate(
            """d => { const s = document.querySelector(`[data-example="${d}"] [data-slot="device-screen"]`).getBoundingClientRect();
                      return [s.width, s.height]; }""", device)
        assert 0 < box[0] <= w
        assert box[0] / box[1] == pytest.approx(w / h, rel=0.01)
    for shell in pg.evaluate(SHELL_JS):
        wide = shell['width'] >= 620
        assert (shell['sidebar'] != 'none') == wide, shell
        assert (shell['tabs'] != 'none') == (not wide), shell
    assert not problems


def test_mixer_switches_preset_accent_viz_mode_and_contrast(new_context, page_html):
    pg, problems = open_page(new_context, page_html)
    shown = lambda: pg.locator('.mx-stage').evaluate_all('es => es.filter(e => e.getClientRects().length).map(e => e.dataset.mixerStage)')
    snippet = lambda: pg.locator('.mx-snippet').inner_text()
    assert shown() == [ui_gallery.PAGE_THEME]
    assert snippet() == f'<html data-ui-theme="{ui_gallery.PAGE_THEME}">'

    pick(pg, 't', 'nord')
    assert shown() == ['nord']
    stage = '.st-nord'
    button = f'{stage} [data-mixer-primary]'
    before = style(pg, button, 'backgroundColor')

    pick(pg, 'a', 'cobalt')
    assert prop(pg, stage, '--primary') == decl(T.accent_decls('theme', 'nord', 'cobalt'), '--primary')
    assert style(pg, button, 'backgroundColor') != before
    assert style(pg, f'{stage} .r-primary', 'backgroundColor') == style(pg, button, 'backgroundColor')

    pick(pg, 'v', 'okabe-ito')
    assert prop(pg, stage, '--chart-1') == decl(T.viz_decls('theme', 'nord', 'okabe-ito'), '--chart-1')

    ink = lambda: prop(pg, stage, '--primary-ink')
    assert ink() == decl(T.accent_decls('theme', 'nord', 'cobalt'), '--primary-ink')
    pick(pg, 'c', 'more')
    assert ink() == decl(T.contrast_decls('theme', 'nord', 'cobalt', contrast='more'), '--primary-ink')
    pick(pg, 'a', 'none')
    assert ink() == decl(T.contrast_decls('theme', 'nord', None, contrast='more'), '--primary-ink')
    pick(pg, 'a', 'cobalt')

    light = style(pg, button, 'backgroundColor')
    pick(pg, 'm', 'dark')
    assert style(pg, stage, 'colorScheme') == 'dark'
    assert style(pg, button, 'backgroundColor') != light
    pick(pg, 'm', 'light')
    assert style(pg, button, 'backgroundColor') == light

    assert snippet() == ('<html data-ui-theme="nord" data-ui-accent="cobalt" data-ui-viz="okabe-ito" '
                         'data-ui-mode="light" data-ui-contrast="more">')
    assert not problems


@pytest.mark.parametrize('preset', sorted(T.presets()))
def test_every_preset_shows_only_its_own_stage(new_context, page_html, preset):
    pg, _ = open_page(new_context, page_html, 390, 844)
    pick(pg, 't', preset)
    assert pg.locator('.mx-stage').evaluate_all(
        'es => es.filter(e => e.getClientRects().length).map(e => e.dataset.mixerStage)') == [preset]
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')


def test_dialog_popover_and_icon_work_without_script(new_context, page_html):
    pg, problems = open_page(new_context, page_html)
    box = pg.locator('#slot-icon .g-live use').first.evaluate('e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }')
    assert box[0] > 0 and box[1] > 0
    dialog = pg.locator('#slot-dialog dialog')
    assert not dialog.evaluate('d => d.open')
    pg.locator('#slot-dialog button[command="show-modal"]').click()
    assert dialog.evaluate('d => d.open && d.matches(":modal")')
    dialog.locator('button[command="close"]').click()
    assert not dialog.evaluate('d => d.open')

    tip = pg.locator('#slot-popover [popover]')
    assert not tip.evaluate('e => e.matches(":popover-open")')
    pg.locator('#slot-popover [popovertarget]').click()
    assert tip.evaluate('e => e.matches(":popover-open")')
    assert not problems


def test_mockup_stage_switches_fidelity_viewport_and_view(new_context, page_html):
    pg, _ = open_page(new_context, page_html)
    stage = pg.locator('#wireframes [data-slot=mockup-stage]')
    visible = lambda sel: stage.locator(sel).evaluate_all('es => es.filter(e => e.getClientRects().length).length')
    click = lambda ctl, value: stage.locator(f'label:has(input[data-ctl="{ctl}"][value="{value}"])').click()
    assert visible('[data-variant=wire]') == 2 and visible('[data-variant=graphite]') == 0
    assert visible('[data-slot=device-frame]') == 2
    click('fidelity', 'graphite')
    assert visible('[data-variant=wire]') == 0 and visible('[data-variant=graphite]') == 2
    click('viewport', 'duo-wide')
    assert visible('[data-device=duo-wide]') == 2 and visible('[data-device=duo-folded]') == 0
    click('view', 'after')
    assert visible('[data-pane=before]') == 0 and visible('[data-pane=after]') == 1
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
