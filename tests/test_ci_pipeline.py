"""Release reuse requires exact successful main CI; runner keeps suite isolation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('pipeline_' + name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('release_validation')
runner = load('test')
HEAD = 'a' * 40


def successful_run():
    return {'id': 77, 'head_sha': HEAD, 'head_branch': 'main', 'event': 'push',
            'status': 'completed', 'conclusion': 'success'}


def response(monkeypatch, payload):
    def run(argv, **kwargs):
        assert argv[:2] == ['gh', 'api']
        url = urlsplit(argv[2])
        assert url.path == 'repos/owner/repo/actions/workflows/ci.yml/runs'
        assert parse_qs(url.query) == {'head_sha': [HEAD], 'branch': ['main'],
                                     'event': ['push'], 'status': ['success'], 'per_page': ['1']}
        assert kwargs['timeout'] == 30 and kwargs['check']
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), '')
    monkeypatch.setattr(release.subprocess, 'run', run)


def test_release_reuses_only_exact_successful_main_workflow(monkeypatch):
    run = successful_run()
    response(monkeypatch, {'workflow_runs': [run]})
    assert release.validated_run('owner/repo', HEAD) == run


@pytest.mark.parametrize('field,value', [
    ('head_sha', 'b' * 40), ('head_branch', 'feature'), ('event', 'pull_request'),
    ('status', 'in_progress'), ('conclusion', 'failure'), ('conclusion', None),
    ('id', None), ('id', True), ('id', 0),
])
def test_release_rejects_mismatched_or_incomplete_evidence(monkeypatch, field, value):
    run = successful_run()
    run[field] = value
    response(monkeypatch, {'workflow_runs': [run]})
    assert release.validated_run('owner/repo', HEAD) is None


@pytest.mark.parametrize('payload', [{}, {'workflow_runs': []}, {'workflow_runs': None},
                                    {'workflow_runs': [None]}, [], None])
def test_release_falls_back_without_valid_evidence(monkeypatch, payload):
    response(monkeypatch, payload)
    assert release.validated_run('owner/repo', HEAD) is None


@pytest.mark.parametrize('error', [FileNotFoundError('gh unavailable'),
                                 subprocess.CalledProcessError(1, ['gh', 'api']),
                                 subprocess.TimeoutExpired(['gh', 'api'], 30)])
def test_release_api_errors_fall_back_to_tests(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(release.subprocess, 'run', fail)
    assert release.validated_run('owner/repo', HEAD) is None


def test_release_invalid_json_falls_back_to_tests(monkeypatch):
    monkeypatch.setattr(release.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, 'not JSON', ''))
    assert release.validated_run('owner/repo', HEAD) is None


@pytest.mark.parametrize('run,expected', [(None, 'false'), (successful_run(), 'true')])
def test_release_output_controls_full_test_fallback(monkeypatch, tmp_path, run, expected):
    output = tmp_path/'output'
    output.write_text('existing=value\n')
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(sys, 'argv', ['release_validation.py', '--repository', 'owner/repo', '--head', HEAD])
    monkeypatch.setattr(release, 'validated_run', lambda *_: run)
    release.main()
    assert output.read_text() == f'existing=value\nreuse={expected}\n'


@pytest.mark.parametrize('workers', [0, 2])
def test_runner_parallelizes_only_recipe_suite_and_reports_durations(monkeypatch, workers):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(runner.subprocess, 'run', run)
    runner.main(['--workers', str(workers)])
    assert len(calls) == (4 if workers else 3)
    assert all('--durations=20' in call for call in calls)
    if workers:
        recipe = str(ROOT/'tests/test_recipe_integration.py')
        assert recipe not in calls[0] and recipe in calls[1]
        assert calls[1][-4:] == ['-n', '2', '--dist', 'worksteal']
        assert all('-n' not in call for call in [calls[0], *calls[2:]])
    else:
        assert all('-n' not in call for call in calls)
    assert len({call[call.index('--basetemp') + 1] for call in calls}) == len(calls)


def test_runner_stops_after_failed_suite(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1)
    monkeypatch.setattr(runner.subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['pytest', '-q', 'tests/test_ci_pipeline.py'])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 1 and len(calls) == 1
