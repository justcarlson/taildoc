"""Opt-in browser checks for iPhone Duo reader and brief viewports."""
import os
from pathlib import Path

import pytest

import tailplan_themes as themes

pytestmark = pytest.mark.skipif(not os.getenv('TAILPLAN_BROWSER_TESTS'), reason='Set TAILPLAN_BROWSER_TESTS=1 for browser checks')

PHONE_FIXTURES = Path(__file__).parent / 'fixtures' / 'phone'
VIEWPORTS = [(382, 580), (594, 368)]


@pytest.mark.parametrize('ident', list(themes.registry()))
@pytest.mark.parametrize('layout', ['reading', 'brief'])
@pytest.mark.parametrize('width,height', VIEWPORTS)
def test_duo_viewports_fit_and_paint_seamlessly(new_context, ident, layout, width, height):
    context = new_context(viewport={'width': width, 'height': height}, has_touch=True, is_mobile=True)
    page = context.new_page()
    problems = []
    page.on('pageerror', lambda error: problems.append(str(error)))
    page.on('console', lambda message: problems.append(message.text) if message.type == 'error' else None)
    doc, frozen = themes.render((PHONE_FIXTURES / f'{layout}.md').read_text(), theme=ident, layout=layout)
    page.set_content(doc)
    metrics = page.evaluate("""() => {
      const main = document.querySelector('main');
      const style = getComputedStyle(main);
      return {width: innerWidth, scroll: document.documentElement.scrollWidth,
        htmlBackground: getComputedStyle(document.documentElement).backgroundColor,
        bodyBackground: getComputedStyle(document.body).backgroundColor,
        themeColor: document.querySelector('meta[name="theme-color"]').content,
        left: style.paddingLeft, top: style.marginTop, bottom: parseFloat(style.paddingBottom)};
    }""")
    swatch = page.evaluate("(c) => { const e = document.createElement('i'); e.style.color = c; document.body.append(e); return getComputedStyle(e).color; }", metrics['themeColor'])
    assert metrics['scroll'] == metrics['width']
    assert metrics['htmlBackground'] == metrics['bodyBackground'] == swatch
    assert metrics['themeColor'] == frozen['tokens']['background']
    if width == 382:
        assert metrics['left'] == '16px'
    assert metrics['bottom'] >= 64
    assert metrics['top'] == ('14px' if height <= 440 else '20px')
    page.emulate_media(media='print')
    assert page.evaluate("() => getComputedStyle(document.querySelector('main')).paddingBottom") == '0px'
    assert page.evaluate("() => getComputedStyle(document.documentElement).backgroundColor") == 'rgb(255, 255, 255)'
    assert not problems
