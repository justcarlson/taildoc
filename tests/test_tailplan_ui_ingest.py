"""Checks for Tailplan UI at ingest: opt-in bundling, GET /api/ui, the eight lints, and the audit fields."""
import json
import re
import unittest.mock as mock
import xml.etree.ElementTree as ET

import pytest
from auth_harness import AuthCase

import tailplan_server as server
import tailplan_ui
from tailplan_ui import lint

OPT = '<meta name="tailplan-ui" content="1">'
VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'


def page(body='<p>Hi</p>', head='', html_attrs='', opt=True):
    return (f'<!doctype html><html lang="en"{html_attrs}><head><meta charset="utf-8">{VIEWPORT}'
            f'{OPT if opt else ""}<title>T</title>{head}</head><body>{body}</body></html>')


def codes(html):
    return [f['code'] for f in lint.lint(html)]


# Ingest bundling

def test_an_opted_in_page_is_bundled_and_others_stay_byte_identical():
    opted = page('<button data-slot="button">Go</button>')
    plain = page('<button data-slot="button">Go</button>', opt=False)
    off = page(opt=False).replace('<title>', '<meta name="tailplan-ui" content="0"><title>')
    pages = [{'path': 'index.html', 'html': opted}, {'path': 'a.html', 'html': plain}, {'path': 'b.html', 'html': off}]
    out, meta = server.ui_bundle_pages(pages)
    assert 'id="tp-ui"' in out[0]['html'] and out[0]['html'] != opted
    assert out[1]['html'] == plain and out[2]['html'] == off
    assert out[1]['html'].encode() == plain.encode()
    assert meta['uiVersion'] == tailplan_ui.__version__
    assert pages[0]['html'] == opted


def test_bundle_metadata_comes_from_the_index_page():
    first = page('<p>a</p>', html_attrs=' data-ui-theme="nord"')
    index = page('<p>b</p>', html_attrs=' data-ui-theme="dracula"')
    _, meta = server.ui_bundle_pages([{'path': 'a.html', 'html': first}, {'path': 'index.html', 'html': index}])
    assert meta['uiTheme'] == 'dracula'


def test_odd_input_passes_through():
    assert server.ui_bundle_pages('x') == ('x', None)
    odd = [{'path': 'index.html', 'html': None}, 'text', {'path': 'x'}]
    out, meta = server.ui_bundle_pages(odd)
    assert out == odd and meta is None


def test_a_second_bundle_changes_nothing():
    once, _ = server.ui_bundle_pages([{'path': 'index.html', 'html': page()}])
    twice, _ = server.ui_bundle_pages(once)
    assert once == twice


def test_the_size_limit_counts_the_bundled_page():
    pad = '<p>' + 'x' * (server.MAX_HTML_BYTES - len(page('<p></p>').encode()) - 20) + '</p>'
    raw = page(pad)
    assert len(raw.encode()) <= server.MAX_HTML_BYTES
    assert server.validate_upload([{'path': 'index.html', 'html': raw}])['ok']
    bundled, _ = server.ui_bundle_pages([{'path': 'index.html', 'html': raw}])
    result = server.validate_upload(bundled)
    assert not result['ok'] and any('maximum' in e for e in result['errors'])
    plain = page(pad, opt=False)
    unchanged, _ = server.ui_bundle_pages([{'path': 'index.html', 'html': plain}])
    assert server.validate_upload(unchanged)['ok']


# The eight lints

@pytest.mark.parametrize('html,code', [
    (page('<div data-slot="buton">x</div>'), 'ui-unknown-slot'),
    (page(head='<style>@media (max-width: 640px){p{margin:0}}</style>'), 'ui-breakpoint'),
    (page(head='<style>@media (min-width:600px) and (max-width:900px){p{margin:0}}</style>'), 'ui-breakpoint'),
    (page(head='<style>p{white-space:nowrap;text-overflow:ellipsis;overflow:hidden}</style>'), 'ui-nowrap-ellipsis'),
    (page('<h2 style="white-space:nowrap;text-overflow:ellipsis">T</h2>'), 'ui-nowrap-ellipsis'),
    (page(head='<style>input{font-size:14px}</style>'), 'ui-input-font-size'),
    (page('<select style="font-size:0.8rem"></select>'), 'ui-input-font-size'),
    (page(opt=False, head='<style>body{background:#fff;color:#111}</style>'), 'ui-no-dark-mode'),
    (page(head='<style>main{min-height:100vh}</style>'), 'ui-viewport-height'),
    (page(head='<style>.s{height:100dvh}</style>'), 'ui-viewport-height'),
    (page(head='<style>.bar{position:fixed;bottom:0;left:0}</style>'), 'ui-fixed-bottom'),
    (page('<svg viewBox="0 0 100 40" role="img"><rect x="1" y="1" width="9" height="9"/>'
          '<rect x="20" y="1" width="9" height="9"/><rect x="40" y="1" width="9" height="9"/></svg>'),
     'ui-chart-unlinked'),
])
def test_each_lint_fires(html, code):
    assert code in codes(html)


@pytest.mark.parametrize('html,code', [
    (page('<div data-slot="button">x</div><div data-slot="card-footer">y</div>'), 'ui-unknown-slot'),
    (page('<div data-slot="mystery">x</div>', opt=False), 'ui-unknown-slot'),
    (page(head='<style>@media (max-width: 700px){p{margin:0}}p{width:640px}</style>'), 'ui-breakpoint'),
    (page(head='<style>.chip{white-space:nowrap;text-overflow:ellipsis}</style>'), 'ui-nowrap-ellipsis'),
    (page(head='<style>p{white-space:nowrap}</style>'), 'ui-nowrap-ellipsis'),
    (page(head='<style>input{font-size:16px}textarea{font-size:1rem}.x{font-size:12px}</style>'), 'ui-input-font-size'),
    (page(head='<style>input[type=checkbox]{font-size:12px}</style>'), 'ui-input-font-size'),
    (page(opt=False, head='<style>body{color:#111}@media (prefers-color-scheme:dark){body{color:#eee}}</style>'),
     'ui-no-dark-mode'),
    (page(opt=False, html_attrs=' data-ui-mode="light"', head='<style>body{color:#111}</style>'), 'ui-no-dark-mode'),
    (page(opt=False, html_attrs=' data-ui-mode="dark"', head='<style>body{color:#eee}</style>'), 'ui-no-dark-mode'),
    (page(opt=True, head='<style>body{color:#111}</style>'), 'ui-no-dark-mode'),
    (page(opt=False), 'ui-no-dark-mode'),
    (page(head='<style>main{min-height:100svh;height:100%}</style>'), 'ui-viewport-height'),
    (page(head='<style>.bar{position:fixed;top:0}.b{position:sticky;bottom:0}</style>'), 'ui-fixed-bottom'),
    (page(head='<style>.bar{position:fixed;bottom:5rem}</style>'), 'ui-fixed-bottom'),
    (page('<svg viewBox="0 0 100 40" role="img"><a href="#s1"><rect x="1" y="1" width="9" height="9"/></a>'
          '<rect x="20" y="1" width="9" height="9"/><rect x="40" y="1" width="9" height="9"/></svg>'),
     'ui-chart-unlinked'),
    (page('<figure><svg viewBox="0 0 9 9" role="img"><rect width="1" height="1"/><rect width="2" height="1"/>'
          '<rect width="3" height="1"/></svg><figcaption><a href="https://example.com/data">Source</a>'
          '</figcaption></figure>'), 'ui-chart-unlinked'),
    (page('<svg data-slot="icon" aria-hidden="true"><use href="#tp-icon-check"/></svg>'), 'ui-chart-unlinked'),
    (page('<svg viewBox="0 0 24 24"><path d="M1 1"/><path d="M2 2"/></svg>'), 'ui-chart-unlinked'),
])
def test_each_lint_stays_quiet(html, code):
    assert code not in codes(html)


def test_the_unknown_slot_lint_suggests_a_close_slot():
    message = lint.lint(page('<div data-slot="buton">x</div>'))[0]['message']
    assert 'buton' in message and '"button"' in message


def test_lints_ignore_comments_and_the_bundled_stamp():
    doc = page('<p>x</p>', head='<!-- <style>main{height:100vh}</style> --><style>/* @media (max-width:640px){} */</style>')
    assert codes(doc) == []
    bundled, _ = tailplan_ui.bundle(page('<button data-slot="button">Go</button><dialog data-slot="dialog">x</dialog>'))
    assert lint.lint(bundled) == []


def test_lints_are_warnings_that_never_block():
    doc = page(head='<style>@media (max-width:640px){p{margin:0}}main{height:100vh}</style>',
               body='<div data-slot="buton">x</div>')
    result = server.validate_html_detailed(doc)
    assert result['ok'] and not result['errors']
    levels = {i['code']: i['level'] for i in result['issues'] if i['code'].startswith('ui-')}
    assert set(levels) >= {'ui-unknown-slot', 'ui-breakpoint', 'ui-viewport-height'}
    assert set(levels.values()) == {'warning'}
    assert any(w.startswith('line ') or 'Unknown data-slot' in w for w in result['warnings'])


def test_pages_rendered_from_markdown_skip_the_lints():
    import tailplan_themes
    doc, _ = tailplan_themes.render('# Title\n\nBody', theme='auto')
    assert 'tailplan-theme' in doc
    assert lint.lint(doc) == []
    assert not [w for w in server.validate_html_detailed(doc)['warnings'] if 'media query' in w]


def test_lint_handles_bad_input():
    assert lint.lint(None) == []
    assert lint.lint('') == []
    assert lint.lint('<svg><<<') == []


# Review round 1 fixes: lints

def test_slot_names_in_prose_and_code_are_not_slots():
    doc = page('<p>Write <code>data-slot="buton"</code> or data-slot="oops" in your tag.</p>'
               '<pre>&lt;div data-slot="nope"&gt;</pre><!-- <i data-slot="gone"> -->')
    assert 'ui-unknown-slot' not in codes(doc)
    assert 'ui-unknown-slot' in codes(page('<DIV DATA-SLOT=buton>x</DIV>'))
    assert lint.lint(page('<div data-slot="buton">x</div>'))[0]['line'] == 1
    assert 'ui-unknown-slot' not in codes(page('<script>var s = \'<i data-slot="nope">\';</script>'))


@pytest.mark.parametrize('head', [
    '<style>body{color:light-dark(#111,#eee)}</style>',
    '<style>:root{color-scheme:light dark}body{color:#111}</style>',
    '<meta name="color-scheme" content="light dark"><style>body{color:#111}</style>',
    '<style>body{color:#111}</style><meta content="dark light" name="color-scheme">',
    '<style>.x{margin:0;padding:1rem}</style>',
])
def test_dark_mode_lint_accepts_real_support_and_color_free_css(head):
    assert 'ui-no-dark-mode' not in codes(page(opt=False, head=head))


@pytest.mark.parametrize('head,body', [
    ('<style>:root{color-scheme:light}body{color:#111}</style>', '<p>x</p>'),
    ('<style>body{background:#fff}</style>', '<p>Use prefers-color-scheme and color-scheme: dark in CSS.</p>'),
    ('<style>:root{--ink:#111}</style>', '<p>x</p>'),
    ('<style>body{color:#111}</style><meta name="theme-color" content="dark">', '<p>x</p>'),
])
def test_dark_mode_lint_fires_when_color_css_has_no_dark_support(head, body):
    assert 'ui-no-dark-mode' in codes(page(body, head=head, opt=False))


def test_a_chart_wrapped_in_a_link_counts_as_linked():
    shapes = '<rect x="1" y="1" width="9" height="9"/><rect x="20" y="1" width="9" height="9"/><rect x="40" y="1" width="9" height="9"/>'
    wrapped = page(f'<a href="https://example.com/data"><svg viewBox="0 0 100 40" role="img">{shapes}</svg></a>')
    assert 'ui-chart-unlinked' not in codes(wrapped)
    closed = page(f'<a href="https://example.com/data">Data</a><svg viewBox="0 0 100 40" role="img">{shapes}</svg>')
    assert 'ui-chart-unlinked' in codes(closed)
    assert 'ui-chart-unlinked' in codes(page(f'<a name="x"><svg viewBox="0 0 100 40" role="img">{shapes}</svg></a>'))


@pytest.mark.parametrize('css,code', [
    ('main{block-size:100vh}', 'ui-viewport-height'),
    ('main{min-block-size:100dvh}', 'ui-viewport-height'),
    ('.bar{position:fixed;inset-block-end:0}', 'ui-fixed-bottom'),
    ('.bar{position:fixed;inset:auto 0 0 0}', 'ui-fixed-bottom'),
    ('.bar{position:fixed;inset:auto 0 0}', 'ui-fixed-bottom'),
    ('.bar{position:fixed;inset-block:auto 0}', 'ui-fixed-bottom'),
    ('content_marker{content:"/*"} main{height:100vh} .x{content:"*/"}', 'ui-viewport-height'),
])
def test_logical_properties_and_css_strings(css, code):
    assert code in codes(page(head=f'<style>{css}</style>'))


@pytest.mark.parametrize('css,code', [
    ('main{block-size:100svh}', 'ui-viewport-height'),
    ('.bar{position:fixed;inset:0}', 'ui-fixed-bottom'),
    ('.bar{position:fixed;inset:auto 0 1rem}', 'ui-fixed-bottom'),
    ('.bar{position:fixed;inset-block:0}', 'ui-fixed-bottom'),
    ('/* main{height:100vh} */ .x{margin:0}', 'ui-viewport-height'),
    ('.x{content:"a"} /* .bar{position:fixed;bottom:0} */', 'ui-fixed-bottom'),
])
def test_logical_property_lints_stay_quiet(css, code):
    assert code not in codes(page(head=f'<style>{css}</style>'))


def test_a_css_comment_marker_in_a_string_does_not_hide_later_rules():
    doc = page(head='<style>.a::before{content:"/*"}main{height:100vh}.b::after{content:"*/"}</style>')
    found = lint.lint(doc)
    assert [f['code'] for f in found] == ['ui-viewport-height']


def test_the_icon_sprite_parses_as_xml_and_hides_itself():
    sprite = tailplan_ui.icon_sprite(['check'])
    root = ET.fromstring(sprite)
    assert root.get('hidden') == '' and 'display:none' in root.get('style')


def test_built_in_theme_aliases_are_not_logged_as_custom():
    from tailplan_ui.color import tokens
    for alias in tokens.THEME_ALIASES:
        fields = server.ui_audit_fields({'uiVersion': '1.0.0', 'uiTheme': alias, 'uiCssBytes': 1})
        row = server.audit_wide_event('draft.upload', None, 'draft', 'd', 'success', fields)
        server.check_audit_row(row)
        assert row['ui_theme'] == alias


# Audit fields

def test_audit_fields_map_known_values_and_hide_seeds():
    html_attrs = ' data-ui-seed="#ff00aa" data-ui-theme="nord"'
    _, meta = tailplan_ui.bundle(page(html_attrs=html_attrs))
    fields = server.ui_audit_fields(meta)
    assert fields['uiTheme'] == 'nord' and fields['uiAccent'] == 'seed'
    row = server.audit_wide_event('draft.upload', {'accountId': 'a'}, 'draft', 'd', 'success', fields)
    server.check_audit_row(row)
    assert row['ui_accent'] == 'seed' and row['ui_theme'] == 'nord'
    assert 'ff00aa' not in json.dumps(row).lower()


def test_audit_fields_for_hue_surface_and_custom_values():
    _, meta = tailplan_ui.bundle(page(html_attrs=' data-ui-surface="slate" data-ui-hue="200"'))
    fields = server.ui_audit_fields(meta)
    assert (fields['uiTheme'], fields['uiAccent']) == ('slate', 'hue')
    odd = server.ui_audit_fields({'uiVersion': '1.0.0', 'uiTheme': 'My <b>Secret</b>', 'uiAccent': 'rgb(1,2,3)',
                                  'uiViz': 'x', 'uiCssBytes': 5, 'customCssBytes': 0, 'hasDarkMode': True})
    row = server.audit_wide_event('draft.upload', None, 'draft', 'd', 'success', odd)
    server.check_audit_row(row)
    assert (row['ui_theme'], row['ui_accent']) == ('custom', 'none')
    assert 'ui_viz' not in row and 'Secret' not in json.dumps(row)
    assert server.ui_audit_fields(None) == {}


@pytest.mark.parametrize('detail', [
    {'uiVersion': '1.0'}, {'uiVersion': 'v1.0.0'}, {'uiVersion': '1.0.0 token=x'},
    {'uiTheme': 'nope'}, {'uiAccent': '#ff00aa'}, {'uiViz': 'rainbow'},
    {'uiCssBytes': -1}, {'uiCssBytes': True}, {'customCssBytes': '3'}, {'hasDarkMode': 'yes'},
])
def test_bad_ui_values_are_dropped(detail):
    row = server.audit_wide_event('draft.upload', None, 'draft', 'd', 'success', detail)
    assert not [k for k in row if k.startswith(('ui_', 'custom_css', 'has_dark'))]


def test_the_row_check_rejects_a_bad_ui_value():
    row = server.audit_wide_event('draft.upload', None, 'draft', 'd', 'success',
                                  {'uiVersion': '1.2.3', 'uiTheme': 'nord', 'hasDarkMode': False})
    assert server.check_audit_row(row) is row
    for key, bad in (('ui_version', '1.2'), ('ui_theme', 'x'), ('ui_accent', '#fff'), ('ui_viz', 'x'),
                     ('ui_css_bytes', -1), ('custom_css_bytes', 1.5), ('has_dark_mode', 1)):
        broken = {**row, key: bad}
        with pytest.raises(ValueError):
            server.check_audit_row(broken)


class HttpTests(AuthCase):
    def uploaded_rows(self):
        rows = [json.loads(line) for line in self.audit_lines.getvalue().splitlines() if line.startswith('{')]
        return [r for r in rows if r['event'] == 'tailplan.draft.upload']

    def test_api_ui_is_public_like_themes(self):
        status, ui = self.api('/api/ui', token=None)
        assert status == 200 and ui == tailplan_ui.catalog()
        assert ui['items'] and ui['icons']
        assert self.api('/api/themes', token=None)[0] == 200
        status, _headers, _body = self.request('/api/ui', 'POST', {})
        assert status != 200

    def test_check_reports_the_bundled_size_and_the_warnings(self):
        raw = page('<button data-slot="button">Go</button><div data-slot="buton">x</div>')
        status, report = self.api('/api/check', 'POST', {'html': raw}, token=server_token())
        assert status == 200 and report['ok']
        assert report['pages'][0]['bytes'] > len(raw.encode())
        assert any('Unknown data-slot' in w for w in report['warnings'])
        plain = page('<p>x</p>', opt=False)
        _, report = self.api('/api/check', 'POST', {'html': plain}, token=server_token())
        assert report['pages'][0]['bytes'] == len(plain.encode())

    def test_upload_audit_has_the_ui_fields_and_no_raw_values(self):
        doc = page('<h1 data-slot="doc-title">Secret title</h1>', html_attrs=' data-ui-seed="#12ab9f"',
                   head='<style>.x{color:red}</style>')
        self.upload(doc)
        (row,) = self.uploaded_rows()
        server.check_audit_row(row)
        assert re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', row['ui_version'])
        assert row['ui_accent'] == 'seed' and row['ui_theme'] == 'warm-editorial'
        assert row['ui_css_bytes'] > 0 and row['custom_css_bytes'] == len('.x{color:red}')
        assert row['has_dark_mode'] is True
        text = json.dumps(row).lower()
        assert '12ab9f' not in text and 'secret title' not in text and 'color:red' not in text

    def test_a_plain_upload_adds_no_ui_fields(self):
        self.upload(page('<p>x</p>', opt=False))
        (row,) = self.uploaded_rows()
        server.check_audit_row(row)
        assert not [k for k in row if k.startswith(('ui_', 'custom_css', 'has_dark'))]

    def test_the_stored_page_is_bundled_only_when_opted_in(self):
        opted = self.upload(page('<button data-slot="button">Go</button>'))
        plain_doc = page('<p>x</p>', opt=False)
        plain = self.upload(plain_doc)
        assert b'id="tp-ui"' in self.stored(opted['draftId'])
        assert self.stored(plain['draftId']) == plain_doc.encode()

    def test_a_lone_surrogate_in_an_opted_in_page_is_rejected_not_crashed(self):
        bad = page('<p>\ud800</p>')
        status, result = self.api('/api/uploads', 'POST', {'html': bad}, token=server_token())
        assert status == 422, result
        assert 'invalid-unicode' in json.dumps(result)
        status, result = self.api('/api/check', 'POST', {'html': bad}, token=server_token())
        assert status == 200 and not result['ok'], result
        assert 'invalid-unicode' in json.dumps(result)
        status, result = self.api('/api/uploads', 'POST',
                                  {'files': [{'path': 'index.html', 'html': page()}, {'path': 'b.html', 'html': bad}]},
                                  token=server_token())
        assert status == 422, result

    def test_markdown_content_is_never_bundled(self):
        with mock.patch.object(server, 'ui_bundle_pages', side_effect=AssertionError('bundled')):
            status, result = self.api('/api/uploads', 'POST', {'content': '# Title\n\nBody'}, token=server_token())
            assert status in {200, 201}, result
            status, result = self.api('/api/check', 'POST', {'content': '# Title\n\nBody'}, token=server_token())
            assert status == 200 and result['ok'], result
        (row,) = self.uploaded_rows()
        assert not [k for k in row if k.startswith(('ui_', 'custom_css', 'has_dark'))]

    def test_api_ui_answers_503_when_the_catalog_cannot_load(self):
        for error in (OSError('gone'), ValueError('bad'), KeyError('icons')):
            with mock.patch.object(tailplan_ui, 'catalog', side_effect=error):
                status, body = self.api('/api/ui', token=None)
            assert status == 503 and body == {'ok': False, 'error': 'Tailplan UI catalog unavailable.'}
        assert self.api('/api/ui', token=None)[0] == 200

    def test_readyz_checks_the_ui_catalog_and_chunks(self):
        assert self.api('/readyz', token=None)[0] == 200
        with mock.patch.object(tailplan_ui, 'catalog', side_effect=OSError('gone')):
            assert self.api('/readyz', token=None)[0] == 503
        with mock.patch.object(tailplan_ui.bundler, 'chunks', side_effect=ValueError('bad chunk')):
            assert self.api('/readyz', token=None)[0] == 503


    def stored(self, draft_id):
        status, _headers, body = self.request(f'/d/{draft_id}', token=server_token())
        assert status == 200, body
        return body


def server_token():
    from auth_harness import TOKEN
    return TOKEN
