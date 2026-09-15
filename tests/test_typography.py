"""Typography presets across rendering, publication, transport, and installation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest
import tailplan_themes as themes
from test_themes import api, IDS, ROOT
from test_markdown_rendering import KITCHEN_SINK
from test_remote_client import mod as remote
from test_ssh_publisher import mod as guard


@pytest.mark.parametrize('palette', IDS)
@pytest.mark.parametrize('preset', ['serif-sans', 'all-sans'])
@pytest.mark.parametrize('format', ['markdown', 'text'])
def test_presets_compose_with_every_palette(palette, preset, format):
    doc, frozen = themes.render(KITCHEN_SINK, theme=palette, typography=preset, format=format)
    assert (doc, frozen) == themes.render(KITCHEN_SINK, theme=palette, typography=preset, format=format)
    expected = themes.typography_registry()[preset]
    assert frozen['typography'] == {**expected, 'sha256': hashlib.sha256(
        json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    assert frozen['id'] == palette
    assert '--font-heading: ' + expected['tokens']['font-heading'] in doc
    assert 'ui-monospace' in doc and 'https://' not in doc.split('<style>')[1].split('</style>')[0]


def test_default_and_extension(tmp_path, monkeypatch):
    assert themes.render('# Test') == themes.render('# Test', typography='serif-sans')
    preset = themes.typography_registry()['all-sans']
    preset.update(id='custom-sans', version=2)
    preset['tokens']['font-heading'] = 'Verdana, sans-serif'
    (tmp_path / 'custom.json').write_text(json.dumps(preset))
    monkeypatch.setenv('TAILPLAN_TYPOGRAPHY_DIR', str(tmp_path))
    assert len(themes.catalog()['typography']) == 3
    assert themes.render('# Test', typography='custom-sans')[1]['typography']['version'] == 2
    assert themes.resolve_typography()['id'] == 'serif-sans'
    preset['id'] = 'all-sans'
    (tmp_path / 'custom.json').write_text(json.dumps(preset))
    with pytest.raises(themes.ThemeError, match='Duplicate'):
        themes.typography_catalog()


@pytest.mark.parametrize('change', [
    {'version': 0}, {'version': True}, {'schemaVersion': 2}, {'id': '../bad'},
    {'name': ''}, {'tokens': {}}, {'unknown': 'value'},
    {'tokens': {'font-heading': 'serif', 'font-body': 'sans-serif', 'font-code': 'sans-serif'}},
])
def test_invalid_schema(change):
    preset = themes.typography_registry()['serif-sans']
    preset.update(change)
    with pytest.raises(themes.ThemeError):
        themes.validate_typography(preset)


@pytest.mark.parametrize('font', ['url(https://example.com)', 'serif; color:red', '</style><script>',
                                 'var(--font)', '"Bad\\Escape", serif', 'Arial', '', 'inherit'])
def test_font_syntax_rejects_css_and_network(font):
    preset = themes.typography_registry()['serif-sans']
    preset['tokens']['font-heading'] = font
    with pytest.raises(themes.ThemeError):
        themes.validate_typography(preset)


@pytest.mark.parametrize('value', ['', 'all-serif', 'auto', [], {}, 1, True, '../bad'])
def test_api_validation(api, value):
    status, _, result = api.api('/api/uploads', 'POST', {'content': '# Test', 'typography': value})
    assert status == 422 and 'tailplan typography' in result['error']
    assert api.api('/api/drafts')[2]['drafts'] == []


def test_api_snapshot_and_historical_bytes(api, tmp_path, monkeypatch):
    assert api.api('/api/typography', token=None)[2] == themes.typography_catalog()
    payload = {'content': KITCHEN_SINK, 'theme': 'nord', 'typography': 'all-sans'}
    assert api.api('/api/uploads', 'POST', payload, token=None)[0] == 401
    headers = {'Idempotency-Key': 'typography-test'}
    status, _, first = api.api('/api/uploads', 'POST', payload, headers=headers)
    assert status == 201
    assert api.api('/api/uploads', 'POST', payload, headers=headers)[2]['theme'] == first['theme']
    assert api.api('/api/uploads', 'POST', {**payload, 'typography': 'serif-sans'}, headers=headers)[0] == 409
    url = '/d/' + first['draftId']
    before = api.request(url + '/v/1/content')[2]
    assets = tmp_path / 'assets'
    shutil.copytree(themes.ROOT, assets)
    preset = json.loads((assets / 'typography/all-sans.json').read_text())
    preset['version'] = 2
    preset['tokens']['font-heading'] = 'Verdana, sans-serif'
    (assets / 'typography/all-sans.json').write_text(json.dumps(preset))
    monkeypatch.setattr(themes, 'ROOT', assets)
    second = api.api('/api/uploads', 'POST', {**payload, 'draftId': first['draftId']})[2]
    assert second['theme']['typography']['version'] == 2
    assert first['theme']['typography']['version'] == 1
    assert api.request(url + '/v/1/content')[2] == before
    history = api.api('/api/drafts/' + first['draftId'])[2]['versions']
    assert next(v for v in history if v['versionNumber'] == 1)['theme'] == first['theme']
    assert hashlib.sha256(before).hexdigest() == first['fileSha256']
    raw = '<title>Raw</title>\r\n<p style="font-family:serif">Unchanged</p>'
    assert api.api('/api/uploads', 'POST', {'html': raw, 'typography': 'all-sans'})[0] == 422
    result = api.upload(raw)
    assert api.request('/d/' + result['draftId'] + '/content')[2] == raw.encode()


@pytest.mark.parametrize('missing', ['typography/serif-sans.json', 'typography/all-sans.json',
                                     'typography-schema.json', 'typography-selection.json', 'reading.css'])
def test_missing_assets_fail_catalog_readiness_and_install(api, tmp_path, monkeypatch, missing):
    import test_install
    source = tmp_path / 'source'
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
    (source / 'tailplan_themes' / missing).unlink()
    monkeypatch.setattr(themes, 'ROOT', source / 'tailplan_themes')
    assert api.api('/api/themes', token=None)[0] == 503
    assert api.api('/readyz', token=None)[0] == 503
    monkeypatch.setattr(test_install, 'INSTALLER', source / 'install.sh')
    harness = test_install.InstallerHarness(tmp_path / 'install')
    result = harness.run(check=False)
    assert result.returncode != 0
    assert not (harness.app_dir / 'tailplan_server.py').exists()


def test_invalid_registry_assets(api, tmp_path, monkeypatch):
    monkeypatch.setenv('TAILPLAN_TYPOGRAPHY_DIR', str(tmp_path))
    (tmp_path / 'bad.json').write_text('{')
    assert api.api('/api/typography', token=None)[0] == 503
    assert api.api('/readyz', token=None)[0] == 503


def test_standard_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((themes.ROOT / 'typography-schema.json').read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    for preset in themes.typography_registry().values():
        jsonschema.validate(preset, schema)


@pytest.mark.parametrize('entry', ['tailplan', 'tailplan-share'])
def test_actual_direct_cli(api, tmp_path, entry):
    token = tmp_path / 'token'
    env = {**os.environ, 'HOME': str(tmp_path), 'TAILPLAN_TOKEN_FILE': str(token),
           'TAILPLAN_BASE_URL': api.base}
    def cli(*args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'bin' / entry), *args, '--json'],
                                env=env, capture_output=True, text=True)
        assert (result.returncode == 0) == ok, result.stdout + result.stderr
        return json.loads(result.stdout)
    assert len(cli('typography')['typography']) == 2
    token.write_text('bootstrap-test')
    source = tmp_path / 'demo.md'
    source.write_text(KITCHEN_SINK)
    for preset in ['serif-sans', 'all-sans']:
        result = cli(str(source), '--new', '--theme', 'nord', '--typography', preset)
        assert result['theme']['typography']['id'] == preset
    cli(str(source), '--new', '--typography', 'all-serif', ok=False)
    source = tmp_path / 'raw.html'
    source.write_text('<title>Raw</title><p>Untouched</p>')
    cli(str(source), '--new', '--typography', 'all-sans', ok=False)


def test_remote_transport_guard_and_installed_cli(api, tmp_path):
    source = tmp_path / 'demo.md'
    source.write_text('# Test')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '/tmp/tailplan-upload.Ab12Cd34\n', '')
    remote.share_file(source, target='publisher', new=True, draft=None, run=run, typography='all-sans')
    assert '--typography' in calls[-2] and 'all-sans' in calls[-2]
    with patch.object(guard, 'configured_share_command', return_value='/safe/share'), \
         patch.object(guard.os, 'execv', side_effect=SystemExit(0)) as execute:
        with pytest.raises(SystemExit):
            guard.execute(['tailplan-share', 'typography', '--json'])
        execute.assert_called_once_with('/safe/share', ['/safe/share', 'typography', '--json'])
    for invalid in ['../bad', 'all-sans;id', '--help']:
        with pytest.raises(SystemExit) as error:
            guard.execute(['tailplan-share', '/tmp/tailplan-upload.Ab12Cd34/demo.md', '--typography', invalid])
        assert error.value.code == 126
    home = tmp_path / 'home'
    mock = tmp_path / 'mock'
    mock.mkdir()
    ssh = mock / 'ssh'
    ssh.write_text('#!/usr/bin/env python3\nimport os,sys\nos.execv(sys.executable, [sys.executable, ' +
                   repr(str(ROOT / 'bin/tailplan-share')) + ', *sys.argv[3:]])\n')
    ssh.chmod(0o755)
    env = {**os.environ, 'HOME': str(home), 'TAILPLAN_BASE_URL': api.base,
           'PATH': str(mock) + os.pathsep + os.environ['PATH']}
    subprocess.run(['bash', str(ROOT / 'install-client.sh')], env=env, check=True, capture_output=True)
    result = subprocess.run([str(home / '.local/bin/tailplan'), '--target', 'publisher', 'typography', '--json'],
                            env=env, check=True, capture_output=True, text=True)
    assert len(json.loads(result.stdout)['typography']) == 2
    assert (home / '.agents/skills/tailplan/SKILL.md').read_bytes() == (ROOT / 'skills/tailplan/SKILL.md').read_bytes()


def test_installed_remote_upload_through_guard(api, tmp_path):
    """Exercise the installed CLI and guard with a local OpenSSH transport fixture."""
    home = tmp_path / 'client'
    mock = tmp_path / 'mock'
    mock.mkdir()
    ssh = mock / 'ssh'
    ssh.write_text('''#!/usr/bin/env python3
import runpy, shlex, sys
module = runpy.run_path(GUARD)
module['execute'].__globals__['configured_share_command'] = lambda: SHARE
module['execute'](shlex.split(' '.join(sys.argv[2:])))
'''.replace('GUARD', repr(str(ROOT / 'bin/tailplan-publish-guard')))
       .replace('SHARE', repr(str(ROOT / 'bin/tailplan-share'))))
    ssh.chmod(0o755)
    scp = mock / 'scp'
    scp.write_text('''#!/usr/bin/env python3
import shutil,sys
shutil.copyfile(sys.argv[-2], sys.argv[-1].split(':',1)[1])
''')
    scp.chmod(0o755)
    token = tmp_path / 'token'
    token.write_text('bootstrap-test')
    env = {**os.environ, 'HOME': str(home), 'TAILPLAN_BASE_URL': api.base,
           'TAILPLAN_TOKEN_FILE': str(token), 'PATH': str(mock) + os.pathsep + os.environ['PATH']}
    subprocess.run(['bash', str(ROOT / 'install-client.sh')], env=env, check=True, capture_output=True)
    source = tmp_path / 'remote.md'
    source.write_text(KITCHEN_SINK)
    draft = None
    for preset in ['serif-sans', 'all-sans']:
        command = [str(home / '.local/bin/tailplan'), '--target', 'publisher', 'upload', str(source),
                   '--typography', preset, '--theme', 'nord', '--document-type', 'plan',
                   '--layout', 'reading', '--description', 'Font check', '--json']
        command += ['--draft', draft] if draft else ['--new']
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        value = json.loads(result.stdout)
        assert value['theme']['typography']['id'] == preset
        draft = value['draftId']
    assert value['versionNumber'] == 2
    assert api.api('/api/drafts/' + draft)[2]['versions'][1]['theme']['typography']['id'] == 'serif-sans'
