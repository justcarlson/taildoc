"""Brief layout: front matter, run of show, locators, callouts, verdicts, and escaping."""
import re
from pathlib import Path

import pytest

import tailplan_themes as themes
from tailplan_themes import brief

FIXTURE = Path(__file__).parent / 'fixtures' / 'brief-example.md'
YOUTUBE = 'https://www.youtube.com/watch?v=abc123'


def render(source, **options):
    return themes.render(source, layout='brief', **options)


def video(sections, extra=''):
    return f'---\nsource: {YOUTUBE}\nkind: video\nlength: 1:00:00\n{extra}---\n\n# Title\n\n{sections}'


def test_fixture_renders_header_run_of_show_and_metadata():
    doc, metadata = render(FIXTURE.read_text(encoding='utf-8'))
    assert metadata['layout'] == 'brief' and metadata['layoutVersion'] == 2
    assert '<body class="brief" data-mode="light">' in doc
    assert '<title>Harbor Hour #42</title>' in doc
    assert 'class="meta"' not in doc
    assert '<p class="kicker">' in doc and '<p class="dek">' in doc
    assert 'Watch on YouTube' in doc and '3 h 52 min' in doc
    assert '<nav class="ros" aria-label="Contents">' in doc
    assert doc.count('<section class="seg"') == 9
    assert '<aside class="callout callout-summary">' in doc
    assert '<ul class="claims">' in doc
    body = doc.split('</style>', 1)[1]
    assert re.findall(r'badge verdict-([a-z]+)', body) == ['ok', 'partial', 'unverified', 'unverified']
    assert 'badge verdict-other">Maybe</span>' in render('# T\n\n| Verdict | Claim |\n| --- | --- |\n| Maybe | X |\n')[0]
    assert 'brief.css' not in doc and '.bar a' in doc


def test_youtube_locator_uses_query_and_other_sources_use_fragment():
    doc, _ = render(video('## One {at=0:00}\n\nText.\n\n## Two {at=42:03}\n\nText.'))
    assert 'href="https://www.youtube.com/watch?v=abc123&amp;t=2523s"' in doc
    other = video('## One {at=0:00}\n\n## Two {at=2:05}\n').replace(YOUTUBE, 'https://example.com/talk.mp3')
    assert 'href="https://example.com/talk.mp3#t=125"' in render(other)[0]
    assert brief.deep_link('https://youtu.be/abc', 90) == 'https://youtu.be/abc?t=90s'


def test_bar_needs_length_and_two_timed_sections():
    with_bar, _ = render(video('## One {at=0:00}\n\n## Two {at=30:00}\n'))
    assert '<div class="bar">' in with_bar and 'flex: 1800' in with_bar
    no_length = video('## One {at=0:00}\n\n## Two {at=30:00}\n').replace('length: 1:00:00\n', '')
    assert '<div class="bar">' not in render(no_length)[0]
    article = '---\nkind: article\n---\n\n# Essay\n\n## First\n\nText.\n\n## Second\n\nText.'
    doc, _ = render(article)
    assert '<div class="bar">' not in doc and '<ol class="toc">' in doc
    assert 'class="time"' not in doc


def test_axis_ticks_stay_apart_on_a_phone():
    for total in (47 * 60, 3600, 13930, 86000):
        ticks = brief.axis_ticks(total)
        positions = [position for position, _ in ticks]
        gaps = [b - a for a, b in zip(positions, positions[1:])]
        assert all(gap * brief.AXIS_WIDTH_PX / 100 >= brief.AXIS_MIN_GAP_PX for gap in gaps[:-1])
        assert ticks[-1][1].endswith(('h', 'min'))
        assert not any(label.endswith(('h', 'min')) for _, label in ticks[:-1])


def test_topics_set_colors_and_unknown_topic_fails():
    doc, _ = render(video('## One {at=0:00 topic=a}\n\n## Two {at=5:00 topic=b}\n', 'topics: a=Alpha, b=Beta\n'))
    assert '--k: var(--k1);' in doc and '--k: var(--k2);' in doc
    assert '<div class="legend"' in doc and 'Alpha' in doc
    with pytest.raises(themes.ThemeError, match='topic'):
        render(video('## One {at=0:00 topic=zzz}\n', 'topics: a=Alpha\n'))


@pytest.mark.parametrize('front, sections', [
    ('unknown: x\n', '## One\n'),
    ('kind: hologram\n', '## One\n'),
    ('source: javascript:alert(1)\n', '## One\n'),
    ('', '## One {at=5:00}\n\n## Two {at=4:00}\n'),
    ('', '## One {at=2:00:00}\n'),
    ('', '## One {color=red}\n'),
])
def test_invalid_briefs_fail(front, sections):
    source = f'---\nkind: video\nlength: 1:00:00\n{front}---\n\n# T\n\n{sections}'
    with pytest.raises(themes.ThemeError):
        render(source)


def test_text_locator_labels_a_page_without_a_link():
    doc, _ = render('---\nkind: book\n---\n\n# Book\n\n## Chapter one {at="p. 12"}\n\nText.\n\n## Two {at="p. 40"}\n')
    assert '<span class="time">p. 12</span>' in doc and '<a class="time"' not in doc


def test_duplicate_front_matter_key_fails():
    with pytest.raises(themes.ThemeError):
        render('---\nkicker: a\nkicker: b\n---\n\n# T\n')


def test_text_format_is_rejected():
    with pytest.raises(themes.ThemeError, match='Markdown'):
        themes.render('plain', format='text', layout='brief')


def test_plain_markdown_without_front_matter_renders():
    doc, _ = render('# Notes\n\nIntro.\n\n## Part\n\nBody.')
    assert '<h1>Notes</h1>' in doc and '<section class="seg"' in doc


def test_hostile_input_is_escaped_everywhere():
    evil = '<script>alert(1)</script>'
    source = (f'---\nkicker: {evil}\ndek: {evil}\nsource: https://example.com/"onmouseover=x\n'
              f'source-label: {evil}\nkind: video\nlength: 1:00:00\ndate: {evil}\npeople: {evil}\n'
              f'topics: a={evil}\nnote: {evil}\n---\n\n# {evil}\n\n'
              f'> [!summary] {evil}\n> {evil}\n\n'
              f'## {evil} {{at=0:00 topic=a}}\n\n{evil}\n\n## Two {{at="1:00"}}\n\n'
              f'| Verdict | Claim | Detail |\n| --- | --- | --- |\n| {evil} | {evil} | {evil} |\n')
    doc, _ = render(source)
    body = doc.split('</style>', 1)[1]
    assert '<script' not in body
    assert '"onmouseover' not in body
    assert not re.search(r'<[a-z]+[^>]*\son[a-z]+=', body)


def test_callouts_and_claims_only_change_brief_output():
    source = '> [!note] Heads up\n> Body.\n\n| Verdict | Claim |\n| --- | --- |\n| True | Sky is blue |\n'
    reading, _ = themes.render('# T\n\n' + source)
    assert 'callout' not in reading and 'claims' not in reading and '<table' in reading
    doc, _ = render('# T\n\n' + source)
    assert '<aside class="callout callout-note"><p class="callout-title">Heads up</p>' in doc
    assert '<span class="badge verdict-ok">True</span>' in doc


@pytest.mark.parametrize('ident', list(themes.registry()))
def test_topic_and_verdict_colors_meet_contrast(ident):
    css = (Path(themes.__file__).parent / 'brief.css').read_text(encoding='utf-8')
    light, dark = css.split('body.brief[data-mode="dark"]', 1)
    palette = themes.registry()[ident]
    block = dark.split('}', 1)[0] if palette['mode'] == 'dark' else light.split('}', 1)[0]
    values = dict(re.findall(r'--([a-z0-9-]+):\s*(#[0-9a-f]{6})', block))
    for verdict in ('ok', 'partial', 'unverified', 'false', 'other'):
        assert themes.contrast(values[f'{verdict}-fg'], values[f'{verdict}-bg']) >= 4.5, verdict
    for slot in range(1, brief.TOPIC_SLOTS + 1):
        assert themes.contrast(values[f'k{slot}'], palette['tokens']['background']) >= 3, (ident, slot)


def test_long_sources_keep_seconds_in_section_times():
    doc, _ = render(video('## One {at=0:15}\n\n## Two {at=0:45}\n\n## Three {at=10:00}\n'))
    assert '<span class="ts">0:00:15</span>' in doc and '<span class="ts">0:10</span>' in doc
    assert '>0:00:15–0:00:45</a>' in doc


def test_bar_reserves_the_time_before_the_first_section():
    doc, _ = render(video('## One {at=10:00}\n\n## Two {at=30:00}\n'))
    bar = doc.split('<div class="bar">', 1)[1].split('</div>', 1)[0]
    assert bar.startswith('<span style="flex: 600"></span>')
    assert 'flex: 1200"' in bar and 'flex: 1800"' in bar
    starts_at_zero, _ = render(video('## One {at=0:00}\n\n## Two {at=30:00}\n'))
    assert '<div class="bar"><a ' in starts_at_zero
