#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "bin" / "tailplan-share"

loader = importlib.machinery.SourceFileLoader("tailplan_share", str(SHARE))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

KITCHEN_SINK = """# Kitchen Sink

A **strong** and *emphasized* report with `https://code.example/<tag>` and
[a safe link](https://example.com/report?q=one&next=two#summary).

---

- First item
- [x] Completed task

1. Ordered item
2) Alternate ordered marker

> Quoted **context**
> on two lines.

| Item | Owner | Notes |
| :--- | :---: | ---: |
| A\\|B | Hermes | short |
| Missing cells |

```python
if value < limit:
    print("**literal** https://code.example")
```

~~~json
{"unsafe": "<script>"}
~~~

<script>alert("never")</script>
"""


class InlineRenderingTests(unittest.TestCase):
    def test_inline_code_is_escaped_and_protected_from_other_inline_markup(self) -> None:
        body = mod.markdown_to_body(
            "Use `<tag> **not bold** https://example.com [not a link](https://example.org)`."
        )

        self.assertIn(
            "<code>&lt;tag&gt; **not bold** https://example.com "
            "[not a link](https://example.org)</code>",
            body,
        )
        self.assertEqual(body.count("<a "), 0)
        self.assertNotIn("<strong>", body)

    def test_strong_and_emphasis_render_without_exposing_raw_html(self) -> None:
        body = mod.markdown_to_body("**strong <tag>** and *emphasis & more*")

        self.assertEqual(
            body,
            "<p><strong>strong &lt;tag&gt;</strong> and <em>emphasis &amp; more</em></p>",
        )

    def test_markdown_link_renders_once_with_safe_anchor_attributes(self) -> None:
        body = mod.markdown_to_body(
            "Read [the **report**](https://example.com/a?q=one&next=two#result)."
        )

        self.assertEqual(body.count("<a "), 1)
        self.assertIn(
            '<a href="https://example.com/a?q=one&amp;next=two#result" '
            'target="_blank" rel="noopener noreferrer">the <strong>report</strong></a>.',
            body,
        )

    def test_bare_urls_trim_terminal_punctuation_and_balance_parentheses(self) -> None:
        body = mod.markdown_to_body(
            "See https://example.com/a_(b)?q=1&x=2#frag). Then https://example.org/end!."
        )

        self.assertIn(
            '<a href="https://example.com/a_(b)?q=1&amp;x=2#frag" '
            'target="_blank" rel="noopener noreferrer">'
            "https://example.com/a_(b)?q=1&amp;x=2#frag</a>).",
            body,
        )
        self.assertIn(
            '<a href="https://example.org/end" target="_blank" '
            'rel="noopener noreferrer">https://example.org/end</a>!.</p>',
            body,
        )

    def test_bare_urls_repeatedly_trim_mixed_terminal_punctuation(self) -> None:
        cases = (
            ("https://example.com/path.),", "https://example.com/path", ".),"),
            ("https://example.com/path),.", "https://example.com/path", "),."),
            ("https://example.com/path!?))", "https://example.com/path", "!?))"),
            ("https://example.com/a_(b)).", "https://example.com/a_(b)", ")."),
        )
        for source, destination, suffix in cases:
            with self.subTest(source=source):
                body = mod.markdown_to_body(f"See {source}")

                self.assertIn(f'href="{destination}"', body)
                self.assertTrue(body.endswith(f">{destination}</a>{suffix}</p>"))

    def test_unsafe_markdown_link_is_non_clickable_escaped_text(self) -> None:
        body = mod.markdown_to_body("Do not [run this](javascript:alert(1)).")

        self.assertEqual(body.count("<a "), 0)
        self.assertIn("[run this](javascript:alert(1)).", body)

    def test_malformed_link_scanning_is_linear_and_preserves_a_later_valid_link(self) -> None:
        largest_text = ""
        for repetitions in (256, 512, 1_024):
            text = "[x](" * repetitions + " [later](https://example.com/ok)"

            links, character_visits = mod._scan_markdown_links(text)

            self.assertEqual(len(links), 1)
            self.assertLessEqual(character_visits, len(text) * 3)
            largest_text = text

        body = mod.markdown_to_body(largest_text)
        self.assertEqual(body.count("<a "), 1)
        self.assertIn(
            '<a href="https://example.com/ok" target="_blank" '
            'rel="noopener noreferrer">later</a>',
            body,
        )
        self.assertTrue(body.startswith("<p>[x]([x]("))

    def test_nested_link_openers_are_indexed_as_spans_without_copying_suffixes(self) -> None:
        text = "[" * 1_024 + "x](https://example.com/ok)"

        links, character_visits = mod._scan_markdown_links(text)

        self.assertEqual(len(links), 1_024)
        self.assertLessEqual(character_visits, len(text) * 3)
        self.assertTrue(
            all(len(link_span) == 5 and all(isinstance(index, int) for index in link_span)
                for link_span in links.values())
        )
        body = mod.markdown_to_body(text)
        self.assertEqual(body.count("<a "), 1)

    def test_raw_and_malformed_html_remain_escaped(self) -> None:
        body = mod.markdown_to_body('<script>alert("x")</script> <a href="https://evil.test">broken')

        self.assertNotIn("<script>", body)
        self.assertNotIn("<a href=", body)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", body)
        self.assertIn("&lt;a href=&quot;https://evil.test&quot;&gt;broken", body)


class BlockRenderingTests(unittest.TestCase):
    def test_markdown_tables_render_as_html_tables(self) -> None:
        body = mod.markdown_to_body(
            """# Plan

| Task | Owner | Status |
| --- | :---: | ---: |
| Fix mobile | Hermes | Done |
| Render `tables` | Tailplan | Ready |

After table.
"""
        )

        self.assertIn('<div class="table-wrap"><table>', body)
        self.assertIn("<th>Task</th>", body)
        self.assertIn('<th style="text-align: center">Owner</th>', body)
        self.assertIn('<td style="text-align: right">Done</td>', body)
        self.assertIn("<code>tables</code>", body)
        self.assertIn("<p>After table.</p>", body)
        self.assertNotIn("| --- |", body)

    def test_table_rows_support_escaped_pipes_and_short_rows(self) -> None:
        body = mod.markdown_to_body(
            """| Name | Notes |
| --- | --- |
| A\\|B | keeps pipe |
| Missing note |
"""
        )

        self.assertIn("A|B", body)
        self.assertIn("<td>Missing note</td><td></td>", body)

    def test_atx_headings_require_whitespace_and_support_levels_one_through_six(self) -> None:
        markdown = "\n".join(
            [*("#" * level + f" Level {level}" for level in range(1, 7)), "#not-heading"]
        )

        body = mod.markdown_to_body(markdown)

        for level in range(1, 7):
            self.assertIn(f"<h{level}>Level {level}</h{level}>", body)
        self.assertIn("<p>#not-heading</p>", body)

    def test_horizontal_rule_splits_paragraphs(self) -> None:
        body = mod.markdown_to_body("Before\n\n* * *\n\nAfter")

        self.assertEqual(body, "<p>Before</p>\n<hr>\n<p>After</p>")

    def test_lists_and_task_markers_are_semantic_but_inert(self) -> None:
        body = mod.markdown_to_body(
            "- [ ] Pending\n- [x] Done\n\n1. First\n2) Second\n\nAfter"
        )

        self.assertIn("<ul>\n<li>[ ] Pending</li>\n<li>[x] Done</li>\n</ul>", body)
        self.assertIn("<ol>\n<li>First</li>\n<li>Second</li>\n</ol>", body)
        self.assertNotIn("<input", body)
        self.assertTrue(body.endswith("<p>After</p>"))

    def test_consecutive_blockquotes_share_one_closed_container(self) -> None:
        body = mod.markdown_to_body("> First line\n> second **line**.\n\nOutside")

        self.assertIn(
            "<blockquote>\n<p>First line second <strong>line</strong>.</p>\n</blockquote>",
            body,
        )
        self.assertTrue(body.endswith("</blockquote>\n<p>Outside</p>"))

    def test_blockquotes_render_semantically_through_the_documented_max_depth(self) -> None:
        body = mod.markdown_to_body("> " * mod.MAX_QUOTE_DEPTH + "deep")

        self.assertEqual(mod.MAX_QUOTE_DEPTH, 16)
        self.assertEqual(body.count("<blockquote>"), mod.MAX_QUOTE_DEPTH)
        self.assertIn("<p>deep</p>", body)
        self.assertNotIn("&gt;", body)

    def test_excessive_blockquote_markers_are_bounded_and_remain_inert(self) -> None:
        body = mod.markdown_to_body(">" * 1_001 + " <script>alert(1)</script>")

        self.assertEqual(body.count("<blockquote>"), 16)
        self.assertIn("<p>" + "&gt;" * 985 + " &lt;script&gt;", body)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)

    def test_backtick_and_tilde_fences_preserve_escaped_literal_content(self) -> None:
        body = mod.markdown_to_body(
            "```python\nline = '<tag>'  \n**literal** https://example.com\n```\n\n"
            "~~~json\n{\"x\": \"<script>\"}\n~~~"
        )

        self.assertIn(
            '<pre><code class="language-python">line = &#x27;&lt;tag&gt;&#x27;  \n'
            "**literal** https://example.com\n</code></pre>",
            body,
        )
        self.assertIn(
            '<pre><code class="language-json">{&quot;x&quot;: &quot;&lt;script&gt;&quot;}\n'
            "</code></pre>",
            body,
        )
        self.assertNotIn("<strong>literal</strong>", body)
        self.assertEqual(body.count("<a "), 0)

    def test_unclosed_fence_renders_remaining_content_as_safe_code(self) -> None:
        body = mod.markdown_to_body("~~~html\n<script>alert(1)</script>\n**not strong**")

        self.assertEqual(
            body,
            '<pre><code class="language-html">&lt;script&gt;alert(1)&lt;/script&gt;\n'
            "**not strong**</code></pre>",
        )

    def test_tables_lists_and_quotes_close_before_the_next_block(self) -> None:
        body = mod.markdown_to_body(
            "| A | B |\n| --- | --- |\n| one | two |\n\n- item\n\n> quote\n\nParagraph"
        )

        table_end = body.index("</table></div>")
        list_start = body.index("<ul>")
        list_end = body.index("</ul>")
        quote_start = body.index("<blockquote>")
        quote_end = body.index("</blockquote>")
        paragraph_start = body.index("<p>Paragraph</p>")
        self.assertLess(table_end, list_start)
        self.assertLess(list_start, list_end)
        self.assertLess(list_end, quote_start)
        self.assertLess(quote_start, quote_end)
        self.assertLess(quote_end, paragraph_start)

    def test_kitchen_sink_fixture_renders_all_supported_blocks_safely(self) -> None:
        body = mod.markdown_to_body(KITCHEN_SINK)

        for fragment in (
            "<h1>Kitchen Sink</h1>",
            "<strong>strong</strong>",
            "<em>emphasized</em>",
            "<hr>",
            "<ul>",
            "<ol>",
            "<blockquote>",
            '<div class="table-wrap"><table>',
            'class="language-python"',
            'class="language-json"',
            "&lt;script&gt;alert(&quot;never&quot;)&lt;/script&gt;",
        ):
            self.assertIn(fragment, body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("<input", body)
        self.assertEqual(body.count("<a "), 1)


class DocumentRenderingTests(unittest.TestCase):
    def test_generated_css_has_mobile_overflow_guards(self) -> None:
        css = mod.CSS
        self.assertIn("overflow-x: hidden", css)
        self.assertIn("width: min(100% - 24px", css)
        self.assertIn(".table-wrap", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("-webkit-overflow-scrolling: touch", css)
        self.assertIn("table { min-width: 40rem; }", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn("pre { max-width: 100%; overflow-x: auto", css)

    def test_generated_document_is_deterministic_and_has_one_source_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "kitchen-&-sink.md"
            source.write_text("# Plan <Q>\n\n" + KITCHEN_SINK, encoding="utf-8")

            first_path = mod.build_html(source, home=root)
            first_document = first_path.read_text(encoding="utf-8")
            second_path = mod.build_html(source, home=root)
            second_document = second_path.read_text(encoding="utf-8")

        self.assertEqual(first_path, second_path)
        self.assertEqual(first_document, second_document)
        self.assertTrue(first_document.startswith("<!doctype html>\n<html lang=\"en\">"))
        self.assertIn("<title>Plan &lt;Q&gt;</title>", first_document)
        self.assertIn(
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            first_document,
        )
        self.assertIn("@media (max-width: 640px)", first_document)
        source_marker = "Published from kitchen-&amp;-sink.md via Tailplan."
        self.assertEqual(first_document.count(source_marker), 1)
        self.assertEqual(first_document.count("<main>"), 1)
        self.assertNotIn("<script>", first_document)

    def test_generated_links_open_outside_sandbox(self) -> None:
        body = mod.markdown_to_body("Visit https://example.com/demo")

        self.assertIn('href="https://example.com/demo"', body)
        self.assertIn('target="_blank"', body)
        self.assertIn('rel="noopener noreferrer"', body)


if __name__ == "__main__":
    unittest.main()
