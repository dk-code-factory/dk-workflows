import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import executors


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    monkeypatch.delenv('DK_PROVIDER_TIMEOUT_SECONDS', raising=False)
    monkeypatch.delenv('DK_BINDING_STACK', raising=False)
    monkeypatch.delenv('DK_REVIEW_STATS_TRACE', raising=False)
    monkeypatch.setattr(executors, 'managed_update', Mock(return_value=None))


@pytest.mark.parametrize('host', ['claude', 'codex', 'command'])
@pytest.mark.parametrize('configured,expected', [(None, 1200), ('1800', 1800)])
def test_provider_subprocess_receives_default_or_configured_timeout(
    tmp_path, monkeypatch, host, configured, expected
):
    monkeypatch.setenv('DK_HOST', host if host != 'command' else 'codex')
    if configured is not None:
        monkeypatch.setenv('DK_PROVIDER_TIMEOUT_SECONDS', configured)
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs)
        if host == 'codex':
            Path(argv[argv.index('-o') + 1]).write_text('provider result')
        result = json.dumps({'subtype': 'success', 'result': 'provider result'})
        return subprocess.CompletedProcess(argv, 0, result, '')

    monkeypatch.setattr(executors.subprocess, 'run', run)
    if host == 'command':
        executors.invoke({'command': [sys.executable, '{handoff}']},
                         tmp_path / 'handoff.json', tmp_path, 'Review evidence')
    else:
        assert executors.execute('Review evidence', tmp_path) == 'provider result'
    assert len(calls) == 1
    assert calls[0]['timeout'] == expected


@pytest.mark.parametrize('host', ['claude', 'codex', 'command'])
@pytest.mark.parametrize('configured', ['', '0', '-1', '1.5', 'abc'])
def test_invalid_timeout_fails_before_provider_start(tmp_path, monkeypatch, host, configured):
    monkeypatch.setenv('DK_HOST', host if host != 'command' else 'codex')
    monkeypatch.setenv('DK_PROVIDER_TIMEOUT_SECONDS', configured)
    run = Mock(side_effect=AssertionError('provider must not start'))
    monkeypatch.setattr(executors.subprocess, 'run', run)
    with pytest.raises(ValueError, match='DK_PROVIDER_TIMEOUT_SECONDS must be a positive integer'):
        if host == 'command':
            executors.invoke({'command': [sys.executable, '{handoff}']},
                             tmp_path / 'handoff.json', tmp_path, 'Review evidence')
        else:
            executors.execute('Review evidence', tmp_path)
    run.assert_not_called()
    executors.managed_update.assert_not_called()


def test_explicit_native_timeout_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv('DK_HOST', 'claude')
    monkeypatch.setenv('DK_PROVIDER_TIMEOUT_SECONDS', 'invalid')
    process = Mock(return_value='provider result')
    monkeypatch.setattr(executors, 'execute_process', process)
    assert executors.execute('Review evidence', tmp_path, timeout=30) == 'provider result'
    assert process.call_args.args[-1] == 30


def test_command_timeout_is_enforced_by_real_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv('DK_HOST', 'codex')
    monkeypatch.setenv('DK_PROVIDER_TIMEOUT_SECONDS', '1')
    provider = tmp_path / 'slow_provider.py'
    provider.write_text('import sys,time\nprint("partial command review", flush=True)\n'
                        'print("provider waiting", file=sys.stderr, flush=True)\ntime.sleep(30)\n')
    trace = tmp_path / 'command-trace.json'
    monkeypatch.setenv('DK_REVIEW_STATS_TRACE', str(trace))
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        executors.invoke({'command': [sys.executable, str(provider), '{handoff}']},
                         tmp_path / 'handoff.json', tmp_path, 'Review evidence')
    assert failure.value.timeout == 1
    attempt, = json.loads(trace.read_text())['attempts']
    assert attempt['provider'] == 'command'
    assert attempt['stdout'] == 'partial command review\n'
    assert attempt['stderr'] == 'provider waiting\n'
    assert attempt['error_type'] == 'TimeoutExpired'



def test_runtime_stop_receipt_blocks_provider_start(tmp_path, monkeypatch):
    monkeypatch.setenv('DK_HOST', 'codex')
    monkeypatch.setattr(executors, 'managed_update', Mock(return_value={
        'status': 'runtime_unavailable', 'action': 'stop', 'message': 'runtime retention failed'}))
    process = Mock()
    monkeypatch.setattr(executors, 'execute_process', process)
    with pytest.raises(RuntimeError, match='runtime retention failed'):
        executors.execute('Review evidence', tmp_path)
    process.assert_not_called()



def test_managed_retention_exception_is_not_reported_as_optional_update_failure(monkeypatch):
    # Call the real manager; this module's autouse fixture replaces the exported name.
    import importlib.util
    path = Path(executors.__file__)
    spec = importlib.util.spec_from_file_location('retention_manager', path)
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    monkeypatch.setattr(manager, 'update_process', Mock(side_effect=RuntimeError('retained runtime changed')))
    with pytest.raises(RuntimeError, match='retained runtime changed'):
        manager.managed_update({'bundle_digest': 'pinned'}, {}, 'entry')
