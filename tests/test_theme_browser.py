"""Opt-in Chromium checks for every palette at mobile, desktop, and print sizes."""
import os
from pathlib import Path

import pytest
import tailplan_themes as themes

pytestmark = pytest.mark.skipif(not os.getenv('TAILPLAN_BROWSER_TESTS'), reason='Set TAILPLAN_BROWSER_TESTS=1 for Chromium checks')


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runner:
        options = {'headless': True}
        if os.getenv('TAILPLAN_CHROMIUM'):
            options['executable_path'] = os.environ['TAILPLAN_CHROMIUM']
        instance = runner.chromium.launch(**options)
        yield instance
        instance.close()


@pytest.mark.parametrize('ident', list(themes.registry()))
@pytest.mark.parametrize('width', [375, 1280])
def test_reading_layout_table_code_and_print(browser, tmp_path, ident, width):
    page = browser.new_page(viewport={'width':width, 'height':900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    code = 'long_code_' * 35
    source = '# Field notes\n\nA readable paragraph with [a link](https://example.com).\n\n> Context and detail.\n\n| Task | Owner | Status |\n| --- | --- | --- |\n| Layout | Reader | Ready |\n\n```text\n' + code + '\n```'
    doc, _ = themes.render(source, theme=ident)
    page.set_content(doc)
    metrics = page.evaluate('''() => {
      const main = document.querySelector('main');
      const table = document.querySelector('.table-wrap');
      const code = document.querySelector('pre');
      return {width: innerWidth, scroll: document.documentElement.scrollWidth,
        column: main.clientWidth - 48, padding: getComputedStyle(main).paddingLeft,
        font: getComputedStyle(document.body).fontSize,
        family: getComputedStyle(document.body).fontFamily,
        heading: getComputedStyle(document.querySelector('h1')).fontFamily,
        line: getComputedStyle(document.body).lineHeight,
        tableOverflow: table.scrollWidth > table.clientWidth,
        codeOverflow: code.scrollWidth > code.clientWidth};
    }''')
    assert metrics['scroll'] == width
    assert metrics['column'] <= 690
    assert metrics['padding'] == '24px'
    assert metrics['font'] == '17px' and metrics['line'] == '26.35px'
    assert 'Georgia' in metrics['heading'] and 'system-ui' in metrics['family']
    assert page.locator('.table-wrap').get_attribute('tabindex') == '0'
    page.locator('.table-wrap').focus()
    assert page.locator('.table-wrap').evaluate('(e) => e === document.activeElement')
    assert page.evaluate("""() => {
      const c = document.createElement('canvas').getContext('2d');
      c.font = getComputedStyle(document.body).font;
      return c.measureText('iii').width < c.measureText('WWW').width;
    }""")
    assert metrics['codeOverflow']
    assert metrics['tableOverflow'] == (width == 375)
    assert page.locator('pre').inner_text().strip() == code
    assert page.locator('table tbody tr').count() == 1
    assert page.locator('script').count() == 0
    page.locator('.table-wrap').evaluate('(e) => e.scrollLeft = 100')
    if width == 375:
        assert page.locator('.table-wrap').evaluate('(e) => e.scrollLeft') == 100
    page.emulate_media(media='print')
    assert page.locator('body').evaluate('(e) => getComputedStyle(e).backgroundColor') == 'rgb(255, 255, 255)'
    assert page.locator('body').evaluate('(e) => getComputedStyle(e).color') == 'rgb(0, 0, 0)'
    assert page.locator('pre').evaluate('(e) => getComputedStyle(e).whiteSpace') == 'pre-wrap'
    assert page.locator('table').evaluate('(e) => getComputedStyle(e).minWidth') == '0px'
    assert page.pdf().startswith(b'%PDF')
    assert not errors
    page.close()
