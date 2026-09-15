"""Check installed artifacts outside the checkout and without development dependencies."""
from pathlib import Path
import sys

import tailplan_server as server
import tailplan_themes as themes


def main():
    source = Path(sys.argv[1]).resolve() / 'tailplan_themes'
    prefix = Path(sys.prefix).resolve()
    for module in (server, themes):
        assert module.__file__ is not None
        assert Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
    expected = [p for p in source.rglob('*') if p.suffix in {'.py', '.css', '.json', '.txt'}]
    assert expected
    for original in expected:
        installed = themes.ROOT / original.relative_to(source)
        assert installed.read_bytes() == original.read_bytes(), str(installed)
    source_palettes = {p.stem for p in (source / 'palettes').glob('*.json')}
    assert set(themes.registry()) == source_palettes
    assert set(themes.typography_registry()) == {'serif-sans', 'all-sans'}
    count = 0
    for theme in themes.registry():
        for typography in themes.typography_registry():
            doc, metadata = themes.render(
                '# Installed package\n\n| Name | Detail |\n| --- | --- |\n| Test | Readable |',
                theme=theme, typography=typography,
            )
            assert server.validate_html(doc)[0]
            assert 'table-cell-label' in doc
            assert metadata['layoutVersion'] == themes.catalog()['layouts'][0]['version']
            count += 1
    print(f'Installed artifact: {len(expected)} matching assets; {count} render combinations passed')


if __name__ == '__main__':
    main()
