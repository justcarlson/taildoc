"""Opt-in browser checks for every palette at mobile, desktop, and print sizes."""
import os
from pathlib import Path

import pytest

import tailplan_themes as themes

pytestmark = pytest.mark.skipif(not os.getenv('TAILPLAN_BROWSER_TESTS'), reason='Set TAILPLAN_BROWSER_TESTS=1 for browser checks')


@pytest.mark.parametrize('ident', list(themes.registry()))
@pytest.mark.parametrize('width', [375, 1280])
@pytest.mark.parametrize('typography', ['serif-sans', 'all-sans'])
def test_reading_layout_table_code_and_print(page, tmp_path, ident, width, typography):
    page.set_viewport_size({'width':width, 'height':900})
    errors = []
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.on('pageerror', lambda error: errors.append(str(error)))
    code = 'long_code_' * 35
    source = '# Field notes\n\nA readable paragraph with [a link](https://example.com).\n\n> Context and detail.\n\n| Task | Owner | Status |\n| --- | --- | --- |\n| Layout | Reader | Ready |\n\n```text\n' + code + '\n```'
    doc, _ = themes.render(source, theme=ident, typography=typography)
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
    assert metrics['font'] == '17px'
    assert float(metrics['line'].removesuffix('px')) == pytest.approx(26.35, abs=0.001)
    assert 'system-ui' in metrics['family']
    assert ('Georgia' in metrics['heading']) == (typography == 'serif-sans')
    if typography == 'all-sans':
        assert metrics['heading'] == metrics['family']
    assert 'monospace' in page.locator('code').first.evaluate('(e) => getComputedStyle(e).fontFamily')
    assert page.locator('.table-wrap').get_attribute('tabindex') == '0'
    page.locator('.table-wrap').focus()
    assert page.locator('.table-wrap').evaluate('(e) => e === document.activeElement')
    assert page.evaluate("""() => {
      const c = document.createElement('canvas').getContext('2d');
      c.font = getComputedStyle(document.body).font;
      return c.measureText('iii').width < c.measureText('WWW').width;
    }""")
    assert metrics['codeOverflow']
    assert not metrics['tableOverflow']
    assert page.locator('pre').inner_text().strip() == code
    assert page.locator('table tbody tr').count() == 1
    assert page.locator('script').count() == 0
    page.locator('.table-wrap').evaluate('(e) => e.scrollLeft = 100')
    assert page.locator('.table-wrap').evaluate('(e) => e.scrollLeft') == 0
    page.emulate_media(media='print')
    assert page.locator('body').evaluate('(e) => getComputedStyle(e).backgroundColor') == 'rgb(255, 255, 255)'
    assert page.locator('body').evaluate('(e) => getComputedStyle(e).color') == 'rgb(0, 0, 0)'
    assert page.locator('pre').evaluate('(e) => getComputedStyle(e).whiteSpace') == 'pre-wrap'
    assert page.locator('table').evaluate('(e) => getComputedStyle(e).minWidth') == '0px'
    assert page.locator('h1').evaluate('(e) => getComputedStyle(e).fontFamily') == metrics['heading']
    assert page.locator('body').evaluate('(e) => getComputedStyle(e).fontFamily') == metrics['family']
    assert 'monospace' in page.locator('code').first.evaluate('(e) => getComputedStyle(e).fontFamily')
    assert page.evaluate('document.documentElement.scrollWidth') == width
    if page.context.browser.browser_type.name == 'chromium':
        assert page.pdf().startswith(b'%PDF')
    assert not errors
    assert not requests


@pytest.mark.parametrize('width', [320, 375, 390, 640, 641, 768, 1280])
def test_prose_tables_remain_readable(page, width):
    source = (Path(__file__).parent / 'fixtures/readable-tables.md').read_text()
    page.set_viewport_size({'width': width, 'height': 900})
    page.set_content(themes.render(source)[0])
    metrics = page.locator('table').first.evaluate('''table => {
      const wrap = table.parentElement;
      return {pageWidth: document.documentElement.scrollWidth,
        wrapWidth: wrap.clientWidth, wrapScroll: wrap.scrollWidth,
        cells: [...table.querySelectorAll('td')].map(cell => ({
          width: cell.getBoundingClientRect().width,
          font: getComputedStyle(cell).fontSize,
          label: cell.querySelector('.table-cell-label')?.textContent,
          labelDisplay: cell.querySelector('.table-cell-label') &&
            getComputedStyle(cell.querySelector('.table-cell-label')).display
        }))};
    }''')
    assert metrics['pageWidth'] == width
    if width <= 640:
        assert metrics['wrapScroll'] == metrics['wrapWidth']
        assert all(cell['width'] >= metrics['wrapWidth'] - 4 for cell in metrics['cells'])
        assert [cell['label'] for cell in metrics['cells'][:4]] == [
            'Host', 'Good use', 'Main risk', 'Recommendation']
        assert all(cell['labelDisplay'] != 'none' for cell in metrics['cells'])
    else:
        assert all(cell['width'] >= 192 for cell in metrics['cells'])
        assert all(cell['labelDisplay'] == 'none' for cell in metrics['cells'])
        assert metrics['wrapScroll'] > metrics['wrapWidth']
    assert all(cell['font'] == '17px' for cell in metrics['cells'])
    assert page.get_by_role('table').count() == 3
    assert page.get_by_role('columnheader', name='Good use', exact=True).count() == 1
    assert page.get_by_role('cell', name='Laptop', exact=True).count() == 1
    assert page.get_by_role('link', name='Read the guide').count() == 1
    assert page.locator('.table-cell-label a').count() == 0
    assert page.locator('td code').inner_text() == 'check --read-only'
    # A screen-sized viewport must not accidentally activate stacked print rules.
    page.emulate_media(media='print')
    assert page.locator('table').first.evaluate('(e) => getComputedStyle(e).display') == 'table'
    assert page.locator('thead').first.evaluate('(e) => getComputedStyle(e).position') == 'static'
    assert all(value == 'none' for value in page.locator('.table-cell-label').evaluate_all(
        '(es) => es.map(e => getComputedStyle(e).display)'))
    assert page.locator('td').first.evaluate('(e) => getComputedStyle(e).minWidth') == '0px'
    if page.context.browser.browser_type.name == 'chromium':
        assert page.pdf().startswith(b'%PDF')


def test_header_only_tables_remain_visible_and_header_links_can_receive_focus(page):
    page.set_viewport_size({'width': 375, 'height': 900})
    page.set_content(themes.render('| Name | Value |\n| --- | --- |')[0])
    assert page.locator('thead').evaluate('(e) => getComputedStyle(e).clipPath') == 'none'
    source = '| [Guide](https://example.com) | Value |\n| --- | --- |\n| A | B |'
    page.set_content(themes.render(source)[0])
    page.get_by_role('link', name='Guide').focus()
    assert page.locator('thead').evaluate('(e) => getComputedStyle(e).clipPath') == 'none'
    assert page.get_by_role('link', name='Guide').evaluate('(e) => e === document.activeElement')
    assert page.evaluate('document.documentElement.scrollWidth') == 375


@pytest.mark.parametrize('typography', ['serif-sans', 'all-sans'])
def test_all_heading_levels_inline_code_and_literal_text(page, typography):
    page.set_viewport_size({'width': 375, 'height': 812})
    headings = '\n\n'.join('#' * level + ' Heading `code`' for level in range(1, 7))
    page.set_content(themes.render(headings + '\n\nBody text.', typography=typography)[0])
    for medium in ['screen', 'print']:
        page.emulate_media(media=medium)
        families = page.locator('h1,h2,h3,h4,h5,h6').evaluate_all('(es) => es.map(e => getComputedStyle(e).fontFamily)')
        assert len(families) == 6 and len(set(families)) == 1
        assert ('Georgia' in families[0]) == (typography == 'serif-sans')
        assert all('monospace' in f for f in page.locator('code').evaluate_all('(es) => es.map(e => getComputedStyle(e).fontFamily)'))
        assert page.evaluate('document.documentElement.scrollWidth') == 375
    source = '# Literal **text**\n' + 'longword' * 120
    page.set_content(themes.render(source, format='text', typography=typography)[0])
    assert page.locator('.plain-text').inner_text() == source
    assert 'sans-serif' in page.locator('.plain-text').evaluate('(e) => getComputedStyle(e).fontFamily')
    assert page.evaluate('document.documentElement.scrollWidth') == 375
