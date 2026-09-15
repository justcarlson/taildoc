"""Theme contracts, real API publication, and installed client behavior."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import tailplan_themes as themes
import tailplan_server as server
import test_workspace as workspace
from test_markdown_rendering import KITCHEN_SINK, mod as legacy
from test_remote_client import mod as remote
from test_ssh_publisher import mod as guard

ROOT = Path(__file__).resolve().parents[1]
IDS = ['warm-editorial', 'flexoki-light', 'catppuccin-latte', 'dracula',
       'tokyo-night', 'gruvbox-light', 'nord', 'solarized-light']


@pytest.fixture
def api():
    case = workspace.WorkspaceTests()
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


@pytest.mark.parametrize('ident', IDS)
def test_palette_schema_contrast_and_deterministic_document(ident):
    palette = themes.registry()[ident]
    themes.validate_theme(palette)
    doc, frozen = themes.render(KITCHEN_SINK, theme=ident)
    assert (doc, frozen) == themes.render(KITCHEN_SINK, theme=ident)
    assert server.validate_html(doc)[0]
    assert '<script>' not in doc
    assert '<table role="table">' in doc and 'aria-label="Code block"><code' in doc
    assert frozen['tokens'] == palette['tokens']
    assert (themes.ROOT / palette['source']['licenseFile']).is_file()


def test_new_palette_is_discovered_without_renderer_changes(tmp_path):
    palette = copy.deepcopy(themes.registry()['warm-editorial'])
    palette.update(id='custom-palette', name='Custom palette')
    (tmp_path / 'custom.json').write_text(json.dumps(palette))
    with patch.dict(os.environ, {'TAILPLAN_THEME_DIR': str(tmp_path)}):
        assert themes.resolve('custom-palette')['name'] == 'Custom palette'
        assert len(themes.catalog()['themes']) == 9
        assert themes.render('# Test', theme='custom-palette')[1]['id'] == 'custom-palette'


@pytest.mark.parametrize('change', [
    {'id': '../escape'}, {'version': True}, {'version': 0}, {'schemaVersion': 2},
    {'unknown': 'field'}, {'mode': 'system'}, {'tokens': {}}, {'source': {}},
    {'tokens': {'background': '#fff'}}, {'name': ''},
])
def test_invalid_theme_errors(change):
    palette = copy.deepcopy(themes.registry()['warm-editorial'])
    palette.update(change)
    with pytest.raises(themes.ThemeError):
        themes.validate_theme(palette)


def test_invalid_registry_and_contrast_fail_closed(tmp_path):
    file = tmp_path / 'custom.json'
    file.write_text('{')
    with pytest.raises(themes.ThemeError, match='custom.json'):
        themes.registry(tmp_path)
    palette = themes.registry()['warm-editorial']
    file.write_text(json.dumps(palette))
    with pytest.raises(themes.ThemeError, match='Duplicate'):
        themes.registry(tmp_path)
    palette['tokens']['text'] = palette['tokens']['background']
    with pytest.raises(themes.ThemeError, match='contrast'):
        themes.validate_theme(palette)


@pytest.mark.parametrize('kind,ident', [('document','warm-editorial'), ('unknown','warm-editorial'),
                                       ('plan','warm-editorial'), ('notes','flexoki-light'),
                                       ('technical','tokyo-night'), ('reference','solarized-light')])
def test_automatic_and_explicit_selection(kind, ident):
    assert themes.resolve(document_type=kind)['id'] == ident
    assert themes.resolve('nord', kind)['id'] == 'nord'


def test_text_is_prose_with_literal_markup():
    doc, _ = themes.render('# Literal\n<script>x</script>\n**text**', format='text')
    assert '<div class="plain-text"># Literal\n&lt;script&gt;' in doc
    assert '<strong>' not in doc and '<h1>' not in doc
    # The server renderer adds responsive table markup; legacy helpers stay frozen.
    assert themes.markdown_to_body('# Heading\n\nPlain text.') == legacy.markdown_to_body('# Heading\n\nPlain text.')


def test_api_publication_freezes_theme_and_preserves_html(api):
    status, _, catalog = api.api('/api/themes', token=None)
    assert status == 200 and len(catalog['themes']) == 8
    historical = '<!doctype html>\r\n<title>Old</title><p style="color:purple">Keep bytes</p>'
    first = api.upload(historical)
    status, _, result = api.api('/api/uploads', 'POST', {
        'content': KITCHEN_SINK, 'documentType': 'technical', 'draftId': first['draftId'],
    })
    assert status == 200 and result['theme']['id'] == 'tokyo-night'
    url = '/d/' + result['draftId']
    assert api.request(url + '/v/1/content')[2] == historical.encode()
    published = api.request(url + '/v/2/content')[2]
    assert hashlib.sha256(published).hexdigest() == result['fileSha256']
    with patch.object(themes, 'render', side_effect=AssertionError('must not rerender')):
        assert api.request(url + '/v/2/content')[2] == published
    history = api.api('/api/drafts/' + result['draftId'])[2]
    records = {v['versionNumber']: v for v in history['versions']}
    assert records[2]['theme'] == result['theme']
    assert 'theme' not in records[1]


@pytest.mark.parametrize('payload', [
    {'content':'# Test', 'theme':'missing'}, {'content':'# Test', 'theme':[]},
    {'content':'# Test', 'layout':'missing'}, {'content':'# Test', 'documentType':'../bad'},
    {'content':'# Test', 'format':'html'}, {'content':12}, {'content':''},
    {'html':'<p>Original</p>', 'theme':'nord'}, {'html':'<p>Original</p>', 'content':'# Test'},
])
def test_api_rejects_invalid_theme_requests(api, payload):
    assert api.api('/api/uploads', 'POST', payload)[0] == 422
    assert api.api('/api/drafts')[2]['drafts'] == []


def test_themed_upload_authentication_and_idempotency(api):
    payload = {'content':'# Test', 'theme':'nord'}
    assert api.api('/api/uploads', 'POST', payload, token=None)[0] == 401
    headers = {'Idempotency-Key':'themes-test-request'}
    first = api.api('/api/uploads', 'POST', payload, headers=headers)[2]
    second = api.api('/api/uploads', 'POST', payload, headers=headers)[2]
    assert first['draftId'] == second['draftId'] and second['versionNumber'] == 1
    assert first['theme'] == second['theme']
    payload['theme'] = 'dracula'
    assert api.api('/api/uploads', 'POST', payload, headers=headers)[0] == 409


def test_real_cli_upload_listing_and_static_html(api, tmp_path):
    token = tmp_path / 'token'
    token.write_text('bootstrap-test')
    env = {**os.environ, 'HOME':str(tmp_path), 'TAILPLAN_TOKEN_FILE':str(token),
           'TAILPLAN_BASE_URL': api.base}
    def cli(*args):
        result = subprocess.run([sys.executable, str(ROOT/'bin/tailplan'), *args], env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    token.unlink()
    assert len(cli('themes', '--json')['themes']) == 8
    token.write_text('bootstrap-test')
    source = tmp_path / 'example.md'
    source.write_text(KITCHEN_SINK)
    result = cli('upload', str(source), '--new', '--theme', 'nord', '--document-type', 'plan', '--json')
    assert result['theme']['id'] == 'nord'
    assert cli('upload', str(source), '--new', '--document-type', 'notes', '--json')['theme']['id'] == 'flexoki-light'
    original = tmp_path / 'static.html'
    original.write_bytes(b'<title>Static</title>\r\n<p>unchanged</p>')
    result = cli('upload', str(original), '--new', '--json')
    assert api.request('/d/' + result['draftId'] + '/content')[2] == original.read_bytes()


def test_remote_theme_transport_and_guard(tmp_path):
    source = tmp_path / 'example.md'
    source.write_text('# Test')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '/tmp/tailplan-upload.Ab12Cd34\n', '')
    remote.share_file(source, target='publisher', new=True, draft=None, run=run,
                      theme='nord', document_type='plan', layout='reading')
    publish = next(c for c in calls if c[:3] == ['ssh','publisher','tailplan-share'])
    assert publish[4:10] == ['--theme','nord','--document-type','plan','--layout','reading']
    with patch.object(guard, 'configured_share_command', return_value='/safe/share'), \
         patch.object(guard.os, 'execv', side_effect=SystemExit(0)) as execute:
        with pytest.raises(SystemExit) as exited:
            guard.execute(['tailplan-share','themes','--json'])
        assert exited.value.code == 0
        execute.assert_called_once_with('/safe/share', ['/safe/share','themes','--json'])
    for value in ['../bad', 'nord;id', '--help']:
        with pytest.raises(SystemExit) as exited:
            guard.execute(['tailplan-share','/tmp/tailplan-upload.Ab12Cd34/example.md','--theme',value])
        assert exited.value.code == 126


def test_published_schema_matches_standard_validator():
    jsonschema = pytest.importorskip('jsonschema')
    schema = json.loads((themes.ROOT / 'schema.json').read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    for palette in themes.registry().values():
        jsonschema.validate(palette, schema)


def test_native_installer_includes_and_restores_theme_assets(tmp_path):
    from test_install import InstallerHarness
    harness = InstallerHarness(tmp_path)
    installed = harness.app_dir / 'tailplan_themes'
    installed.mkdir(parents=True)
    (installed / 'prior.txt').write_text('previous theme package')
    failed = harness.run(check=False, MOCK_HTTPS_FAIL='1')
    assert failed.returncode != 0 and 'Rollback complete.' in failed.stderr
    assert (installed / 'prior.txt').read_text() == 'previous theme package'
    assert not (installed / 'palettes').exists()
    harness.run()
    assert not (installed / 'prior.txt').exists()
    for source in themes.ROOT.rglob('*'):
        if source.is_file() and '__pycache__' not in source.parts:
            assert (installed / source.relative_to(themes.ROOT)).read_bytes() == source.read_bytes()


def test_schema_rejects_css_injection():
    palette = themes.registry()['warm-editorial']
    palette['tokens']['accent'] = '#000000; background: url(https://example.com)'
    with pytest.raises(themes.ThemeError):
        themes.validate_theme(palette)
