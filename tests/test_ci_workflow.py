"""Keep required CI lanes and failure evidence wired to the release gate."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest
import yaml

from check_junit import check_report

ROOT = Path(__file__).parents[1]


def workflow(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def commands(job):
    return '\n'.join(step.get('run', '') for step in job.get('steps', []))


def test_required_gate_covers_every_lane_and_rejects_skips():
    jobs = workflow('ci.yml')['jobs']
    gate = jobs['quality-gate']
    assert set(gate['needs']) == set(jobs) - {'quality-gate'}
    assert gate['if'] == 'always()'
    assert "job['result'] != 'success'" in commands(gate)
    assert 'continue-on-error' not in str(jobs)


def test_ci_permissions_actions_and_locked_dependencies():
    ci = workflow('ci.yml')
    assert ci['permissions'] == {'contents': 'read'}
    assert 'workflow_call' in ci['on']
    for name, job in ci['jobs'].items():
        assert int(job['timeout-minutes']) <= 15
        for step in job.get('steps', []):
            if 'uses' in step:
                assert re.fullmatch(r'[\w-]+/[\w-]+@[0-9a-f]{40}', step['uses'])
            if step.get('uses', '').startswith('actions/checkout@'):
                assert step['with']['persist-credentials'] == 'false'
        if name in {'tests', 'system-installer', 'browser'}:
            assert 'uv sync --locked' in commands(job)
    assert ci['jobs']['tests']['strategy']['matrix']['python-version'] == ['3.11', '3.12', '3.13']


def test_browser_lane_is_cross_engine_and_keeps_failure_evidence():
    job = workflow('ci.yml')['jobs']['browser']
    assert set(job['strategy']['matrix']['browser']) == {'chromium', 'webkit'}
    script = commands(job)
    for required in ('--browser', '--tracing retain-on-failure', '--screenshot only-on-failure',
                     '--junitxml=test-results/browser.xml', 'check_junit.py', '--minimum 42'):
        assert required in script
    run = next(step for step in job['steps'] if 'tests/test_theme_browser.py' in step.get('run', ''))
    assert run['env']['TAILPLAN_BROWSER_TESTS'] == '1'
    upload = next(step for step in job['steps'] if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert upload['if'] == 'always()'
    assert upload['with']['path'] == 'test-results/'
    assert upload['with']['if-no-files-found'] == 'error'


def test_packaging_cannot_be_satisfied_by_checkout_imports():
    script = commands(workflow('ci.yml')['jobs']['distributions'])
    for required in ('uv build', 'dist/*.whl dist/*.tar.gz', 'uv venv', 'uv pip install',
                     'cd "$RUNNER_TEMP"', '" -I ', 'check_distribution.py'):
        assert required in script


def test_signed_release_runs_the_same_quality_workflow_before_publishing():
    jobs = workflow('release.yml')['jobs']
    assert jobs['quality']['needs'] == 'verify-tag'
    assert jobs['quality']['uses'] == './.github/workflows/ci.yml'
    assert set(jobs['release']['needs']) == {'verify-tag', 'quality'}


@pytest.mark.parametrize('content,minimum', [
    ('<testsuites/>', 1),
    ('<testsuite><testcase/></testsuite>', 2),
    *[(f'<testsuite><testcase><{tag}/></testcase></testsuite>', 1)
      for tag in ('failure', 'error', 'skipped')],
])
def test_report_gate_rejects_incomplete_runs(tmp_path, content, minimum):
    report = tmp_path / 'results.xml'
    report.write_text(content)
    with pytest.raises(ValueError):
        check_report(report, minimum)


def test_report_gate_accepts_complete_pass_and_rejects_missing_or_corrupt_files(tmp_path):
    report = tmp_path / 'results.xml'
    with pytest.raises(FileNotFoundError):
        check_report(report)
    report.write_text('<testsuites><testsuite><testcase/><testcase/></testsuite></testsuites>')
    assert check_report(report, 2) == 2
    report.write_text('broken')
    with pytest.raises(ET.ParseError):
        check_report(report)
