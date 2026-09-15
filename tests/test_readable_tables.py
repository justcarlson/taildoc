"""Structural and safety contracts for generated responsive tables."""
from html.parser import HTMLParser

import tailplan_server as server
import tailplan_themes as themes
from tailplan_themes.markdown import render_table


class Labels(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.labels = []
        self.in_label = False
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == 'span' and dict(attrs).get('class') == 'table-cell-label':
            assert dict(attrs).get('aria-hidden') == 'true'
            self.in_label = True
            self.labels.append('')
        elif self.in_label:
            raise AssertionError('Labels must contain only escaped text')

    def handle_endtag(self, tag):
        if tag == 'span':
            self.in_label = False

    def handle_data(self, data):
        if self.in_label:
            self.labels[-1] += data


def test_labels_use_visible_header_text_without_duplicate_links_or_unsafe_markup():
    headers = ['**Name**', '[Guide](https://example.com)', '`Code`', '', '<img onerror=x>', 'A & B']
    markup = render_table(headers, [], [['one'], ['two']])
    expected = ['Name', 'Guide', 'Code', 'Column 4', '<img onerror=x>', 'A & B']
    assert Labels(markup).labels == expected * 2
    assert markup.count('href="https://example.com"') == 1
    assert '<img' not in markup
    assert markup.count('scope="col"') == len(headers)
    assert markup.count('role="cell"') == len(headers) * 2
    assert server.validate_html(markup)[0]


def test_multiple_tables_have_no_identifier_collisions_or_extra_cells():
    table = '| Name | Value |\n| --- | --- |\n| One | Two | ignored |\n'
    doc, metadata = themes.render(table + '\n' + table)
    assert doc == themes.render(table + '\n' + table)[0]
    assert doc.count('<table role="table">') == 2
    assert doc.count('role="cell"') == 4
    assert 'ignored' not in doc
    assert Labels(doc).labels == ['Name', 'Value'] * 2
    assert metadata['layoutVersion'] == metadata['rendererVersion'] == 3
    assert themes.catalog()['layouts'] == [{'id': 'reading', 'version': 3}]
    assert server.validate_html(doc)[0]
