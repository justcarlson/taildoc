"""Opt-in browser checks for every palette at mobile, desktop, and print sizes."""
import os
from pathlib import Path

import pytest

import tailplan_server as server
import tailplan_themes as themes

pytestmark = pytest.mark.skipif(not os.getenv('TAILPLAN_BROWSER_TESTS'), reason='Set TAILPLAN_BROWSER_TESTS=1 for browser checks')


@pytest.mark.parametrize('ident', list(themes.registry()))
@pytest.mark.parametrize('width', [375, 768, 1024, 1280, 1920])
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
        column: document.querySelector('p').getBoundingClientRect().width, padding: getComputedStyle(main).paddingLeft,
        font: getComputedStyle(document.body).fontSize,
        family: getComputedStyle(document.body).fontFamily,
        heading: getComputedStyle(document.querySelector('h1')).fontFamily,
        line: getComputedStyle(document.body).lineHeight,
        tableOverflow: table.scrollWidth > table.clientWidth,
        codeOverflow: code.scrollWidth > code.clientWidth};
    }''')
    assert metrics['scroll'] == width
    assert metrics['column'] <= 690
    assert metrics['padding'] == ('16px' if width <= 480 else '24px')
    assert metrics['font'] == '17px'
    assert float(metrics['line'].removesuffix('px')) == pytest.approx(26.35, abs=0.001)
    assert 'system-ui' in metrics['family']
    assert ('Iowan Old Style' in metrics['heading']) == (typography == 'serif-sans')
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


@pytest.mark.parametrize('width', [320, 375, 390, 640, 641, 768, 1024, 1199, 1200, 1280, 1920])
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
        # Tablet columns are at least 10rem, desktop columns at least 12rem.
        assert all(cell['width'] >= (192 if width >= 1200 else 160) for cell in metrics['cells'])
        assert all(cell['labelDisplay'] == 'none' for cell in metrics['cells'])
        # Tables widen past the 690px text column, up to 1104px, centered on it.
        assert metrics['wrapWidth'] == min(width - 48, 1104)
        assert all(abs(center - width / 2) <= 1 for center in page.locator('.table-wrap').evaluate_all(
            '(ws) => ws.map(w => (w.getBoundingClientRect().left + w.getBoundingClientRect().right) / 2)'))
        # Four prose columns fit from tablet portrait up; narrower screens scroll locally.
        assert (metrics['wrapScroll'] > metrics['wrapWidth']) == (width < 768)
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
        assert ('Iowan Old Style' in families[0]) == (typography == 'serif-sans')
        assert all('monospace' in f for f in page.locator('code').evaluate_all('(es) => es.map(e => getComputedStyle(e).fontFamily)'))
        assert page.evaluate('document.documentElement.scrollWidth') == 375
    source = '# Literal **text**\n' + 'longword' * 120
    page.set_content(themes.render(source, format='text', typography=typography)[0])
    assert page.locator('.plain-text').inner_text() == source
    assert 'sans-serif' in page.locator('.plain-text').evaluate('(e) => getComputedStyle(e).fontFamily')
    assert page.evaluate('document.documentElement.scrollWidth') == 375


@pytest.mark.parametrize('ident', list(themes.registry()))
@pytest.mark.parametrize('width', [360, 390])
def test_brief_layout_fits_a_phone(page, ident, width):
    page.set_viewport_size({'width': width, 'height': 900})
    source = (Path(__file__).parent / 'fixtures' / 'brief-example.md').read_text(encoding='utf-8')
    doc, _ = themes.render(source, theme=ident, layout='brief')
    page.set_content(doc)
    metrics = page.evaluate('''() => {
      const ticks = [...document.querySelectorAll('.axis span')].map(s => s.getBoundingClientRect());
      const claim = document.querySelector('.claims li');
      return {scroll: document.documentElement.scrollWidth,
        padding: getComputedStyle(document.querySelector('main')).paddingLeft,
        bar: [...document.querySelectorAll('.bar a')].every(a => a.getBoundingClientRect().width > 0),
        ticksInside: ticks.every(r => r.left >= 0 && r.right <= innerWidth),
        tickGaps: ticks.slice(1).map((r, i) => r.left - ticks[i].right),
        claimColumns: getComputedStyle(claim).gridTemplateColumns.split(' ').length};
    }''')
    assert metrics['scroll'] == width
    assert metrics['padding'] == '16px'
    assert metrics['bar'] and metrics['ticksInside']
    assert min(metrics['tickGaps']) >= 8
    assert metrics['claimColumns'] == 1


# Every layout in the catalog and every server page must fit a phone.
# A new layout needs tests/fixtures/phone/<layout>.md (enforced in test_themes.py).
PHONE_WIDTHS = [320, 390]
PHONE_FIXTURES = Path(__file__).parent / 'fixtures' / 'phone'
PHONE_CHECK = """() => {
  const width = innerWidth, escaped = [], sideways = [];
  const scrolls = (e) => ['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(e).overflowX);
  for (const el of document.body.querySelectorAll('*')) {
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    let parent = el.parentElement, contained = false;
    for (; parent && parent !== document.body; parent = parent.parentElement) if (scrolls(parent)) { contained = true; break; }
    if (!contained && (box.right > width + 1 || box.left < -1)) escaped.push(`${el.tagName} ${Math.round(box.left)}..${Math.round(box.right)}`);
    if (!['PRE', 'INPUT', 'TEXTAREA'].includes(el.tagName) && el.clientWidth > 1 && scrolls(el) && el.scrollWidth > el.clientWidth + 1) sideways.push(el.tagName + '.' + el.className);
  }
  return {scroll: document.documentElement.scrollWidth, escaped: escaped.slice(0, 5), sideways,
    viewport: document.querySelector('meta[name=viewport]')?.content || ''};
}"""


def assert_fits_phone(page, doc, width):
    page.set_viewport_size({'width': width, 'height': 800})
    page.set_content(doc)
    result = page.evaluate(PHONE_CHECK)
    assert 'width=device-width' in result['viewport']
    assert result['scroll'] == width, result
    assert not result['escaped'], result
    # Only code blocks and text fields may scroll sideways on a phone. Tables stack; text wraps.
    assert not result['sideways'], result


@pytest.mark.parametrize('layout', [item['id'] for item in themes.catalog()['layouts']])
@pytest.mark.parametrize('ident', list(themes.registry()))
@pytest.mark.parametrize('typography', ['serif-sans', 'all-sans'])
@pytest.mark.parametrize('width', PHONE_WIDTHS)
def test_every_layout_fits_a_phone(page, layout, ident, typography, width):
    source = (PHONE_FIXTURES / f'{layout}.md').read_text()
    doc, _ = themes.render(source, theme=ident, layout=layout, typography=typography)
    assert_fits_phone(page, doc, width)


def _server_pages():
    base = 'https://tailnet.example/tailplan'
    link = base + '/' + 'a' * 120
    session = {'csrf': 'c' * 40, 'accountName': 'someone-with-a-long-login-name@example.com'}
    draft = {'draftId': 'd1', 'slug': 'quarterly-plan', 'favorite': True, 'title': 'Draft ' + 'title ' * 12,
             'description': 'Description ' * 20,
             'shareUrl': base + '/d/quarterly-plan/share', 'rawUrl': base + '/d/quarterly-plan/raw',
             'publicUrl': base + '/d/quarterly-plan', 'versionBaseUrl': base + '/d/d1',
             'disabled': False, 'disabledReason': '', 'latestVersionNumber': 2, 'versionCount': 2,
             'updatedAt': '2026-11-14T10:00:00Z', 'repoHost': 'github.com', 'repoOrg': 'org', 'repoName': 'repo'}
    version = {'versionNumber': 1, 'gitCommitSubject': 'fix: a long commit subject ' * 3, 'gitDirty': True,
               'gitBranch': 'feature/a-very-long-branch-name-without-breaks', 'gitCommitSha': 'a' * 40,
               'createdAt': '2026-11-14T10:00:00Z', 'fileSize': 12345, 'ciRunUrl': link,
               'fileSha256': 'b' * 64, 'requestId': 'r' * 32}
    key = {'id': 'k1', 'name': 'Laptop agent ' * 3, 'createdAt': '2026-11-14T10:00:00Z', 'lastUsedAt': None}
    rows = [dict(draft, draftId=f'r{i}', tags=['a-very-long-tag-name-without-breaks', 'ops', 'notes', 'extra'],
                 workspace='team-with-a-long-name', visibility='authenticated', archived=False,
                 updatedAt=f'2026-{11 - i % 3:02d}-14T10:00:00Z') for i in range(6)]
    rows.append(dict(draft, draftId='r9', favorite=False, tags=[], repoOrg=None, repoName=None))
    counts = {'active': 7, 'favorites': 6, 'living': 2, 'shared': 3, 'archived': 1}
    tags = [{'tag': 'a-very-long-tag-name-without-breaks', 'count': 6}, {'tag': 'ops', 'count': 6}]
    state = {'view': 'active', 'q': '', 'sort': 'updated', 'direction': 'desc', 'group': 'time', 'tag': '',
             'repo': '', 'counts': counts, 'tags': tags, 'repos': [{'repo': 'org/repo', 'count': 6}],
             'suggestedTags': {'r9': 'quarterly-plan'}, 'favorites': rows[:6], 'drafts': rows}
    shared = [dict(draft, draftId=f's{i}', favorite=False, workspace='team', visibility='workspace') for i in range(3)]
    snippet = 'a long passage ' * 6 + '<mark>' + 'unbrokenmatch' * 6 + '</mark> and more'
    dash = lambda **change: server.render_dashboard(base, session, rows, shared=shared, state=dict(state, **change),
                                                    theme='dark')
    return {
        'home': server.home(base),
        'not-found': server.not_found(),
        'login': server.render_login(base, 'c' * 40, '/dashboard', True),
        'dashboard': server.render_dashboard(base, session, [draft, dict(draft, draftId='d2', slug=None, favorite=False, repoOrg=None, repoName=None, disabled=True)]),
        'dash-active': dash(),
        'dash-favorites': dash(view='favorites', drafts=rows[:6]),
        'dash-archived': dash(view='archived', drafts=[dict(rows[0], archived=True)]),
        'dash-shared': dash(view='shared', drafts=shared),
        'dash-group-tag': dash(group='tag'),
        'dash-search': dash(q='unbrokenmatch', sort='relevance', tag='ops',
                            drafts=[dict(r, snippet=snippet) for r in rows]),
        'detail': server.render_detail(base, session, {'draft': draft, 'versions': [version, dict(version, versionNumber=2)]}),
        'keys': server.render_keys(base, session, [key]),
        'new-key': server.render_key(base, session, {'apiKey': {'name': 'Laptop'}, 'token': 'tp_' + 'z' * 60}),
    }


@pytest.mark.parametrize('name', list(_server_pages()))
@pytest.mark.parametrize('width', PHONE_WIDTHS)
def test_server_pages_fit_a_phone(page, name, width):
    assert_fits_phone(page, _server_pages()[name], width)


@pytest.mark.parametrize('name', ['detail', 'keys'])
def test_server_tables_stack_with_labels_on_a_phone(page, name):
    page.set_viewport_size({'width': 390, 'height': 800})
    page.set_content(_server_pages()[name])
    assert page.locator('table').evaluate('(e) => getComputedStyle(e).display') == 'block'
    label = page.locator('tbody td').first.evaluate("(e) => getComputedStyle(e, '::before').content")
    assert label.strip('"') in {'Version', 'Name'}
    page.set_viewport_size({'width': 1280, 'height': 800})
    assert page.locator('table').evaluate('(e) => getComputedStyle(e).display') == 'table'


# Tablet and desktop: the same checks, except that a table wider than the screen
# may scroll inside its own focusable box. The 5-column fixture fits from 1024px up.
TABLET_WIDTHS = [768, 1024]
DESKTOP_WIDTHS = [1280, 1920]


def assert_fits_screen(page, doc, width, measure=None):
    page.set_viewport_size({'width': width, 'height': 900})
    page.set_content(doc)
    result = page.evaluate(PHONE_CHECK)
    assert 'width=device-width' in result['viewport']
    assert result['scroll'] == width, result
    assert not result['escaped'], result
    allowed = {'DIV.table-wrap'} if width < 1024 else set()
    assert not set(result['sideways']) - allowed, result
    text = page.evaluate("""() => [...document.querySelectorAll('main > p, main > ul, main > ol, main > blockquote')]
      .map(e => e.getBoundingClientRect().width)""")
    # Document prose keeps a readable measure on wide screens.
    assert measure is None or all(w <= measure for w in text), text


@pytest.mark.parametrize('layout', [item['id'] for item in themes.catalog()['layouts']])
@pytest.mark.parametrize('ident', list(themes.registry()))
@pytest.mark.parametrize('width', TABLET_WIDTHS + DESKTOP_WIDTHS)
def test_every_layout_fits_tablet_and_desktop(page, layout, ident, width):
    source = (PHONE_FIXTURES / f'{layout}.md').read_text()
    doc, _ = themes.render(source, theme=ident, layout=layout)
    assert_fits_screen(page, doc, width, measure=690)


@pytest.mark.parametrize('name', list(_server_pages()))
@pytest.mark.parametrize('width', TABLET_WIDTHS + DESKTOP_WIDTHS)
def test_server_pages_fit_tablet_and_desktop(page, name, width):
    assert_fits_screen(page, _server_pages()[name], width)
    # Server tables stack only on phones.
    if page.locator('table').count():
        assert page.locator('table').first.evaluate('(e) => getComputedStyle(e).display') == 'table'
