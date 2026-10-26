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
@pytest.mark.parametrize('configured,expected', [(None, 2400), ('1800', 1800)])
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


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_unrelated_native_requests_preserve_acceptance_above_discovery_limit(tmp_path, monkeypatch, host):
    monkeypatch.setenv('DK_HOST', host)
    prompt = '\u2603' * 160000
    assert len(json.dumps(prompt)) > 900_000

    def run(argv, **kwargs):
        assert kwargs['input'] == prompt
        if host == 'codex':
            Path(argv[argv.index('-o') + 1]).write_text('provider result')
        return subprocess.CompletedProcess(argv, 0,
            json.dumps({'subtype': 'success', 'result': 'provider result'}), '')

    process = Mock(side_effect=run)
    monkeypatch.setattr(executors.subprocess, 'run', process)
    assert executors.execute(prompt, tmp_path) == 'provider result'
    process.assert_called_once()


def test_explicit_size_check_uses_serialized_characters_and_inclusive_boundary():
    prompt = '\u2603"\n'
    serialized_size = len(json.dumps(prompt))
    assert serialized_size > len(prompt)
    executors.check_prompt_size(prompt, serialized_size)
    executors.check_prompt_size('small request', 100)
    with pytest.raises(ValueError, match='Provider request too large.*Retain evidence in files'):
        executors.check_prompt_size(prompt, serialized_size - 1)


@pytest.mark.parametrize('escaped', [False, True])
@pytest.mark.parametrize('diagnostic,prompt_phrase,expected', [
    ('Input exceeds maximum length.', '', RuntimeError),
    ('transport failure.', 'usage limit reached', RuntimeError),
    ('usage limit reached.', 'authentication failed', executors.ProviderExhausted),
])
def test_provider_error_redacts_prompt_and_bounds_output(
    tmp_path, monkeypatch, escaped, diagnostic, prompt_phrase, expected
):
    monkeypatch.setenv('DK_HOST', 'codex')
    prompt = ('private evidence \"\n\u2603' + prompt_phrase) * 1000
    echo = json.dumps(prompt, ensure_ascii=True)[1:-1] if escaped else prompt
    stderr = diagnostic + ' ' + echo + 'x' * 20000 + ' transport failure'
    monkeypatch.setattr(executors.subprocess, 'run', Mock(return_value=
        subprocess.CompletedProcess([], 1, '', stderr)))
    with pytest.raises(expected) as failure:
        executors.execute(prompt, tmp_path)
    assert type(failure.value) is expected
    message = str(failure.value)
    assert len(message) <= executors.MAX_PROVIDER_ERROR_CHARS
    assert 'private evidence' not in message
    assert diagnostic in message
    assert 'transport failure' in message


def test_claude_error_envelope_redacts_prompt_and_bounds_output(tmp_path, monkeypatch):
    monkeypatch.setenv('DK_HOST', 'claude')
    prompt = 'private evidence "\n\u2603' * 1000
    envelope = {'subtype': 'error_during_execution', 'is_error': True,
                'result': 'Input exceeds maximum length. ' + prompt + 'x' * 20000 + ' transport failure'}
    monkeypatch.setattr(executors.subprocess, 'run', Mock(return_value=
        subprocess.CompletedProcess([], 0, json.dumps(envelope), '')))
    with pytest.raises(RuntimeError) as failure:
        executors.execute(prompt, tmp_path)
    message = str(failure.value)
    assert len(message) <= executors.MAX_PROVIDER_ERROR_CHARS
    assert 'private evidence' not in message
    assert 'Input exceeds maximum length' in message
    assert 'transport failure' in message


def test_bounded_error_preserves_exhaustion_and_authentication_classification():
    with pytest.raises(executors.ProviderExhausted) as failure:
        executors.provider_failure('x' * 20000 + ' usage limit reached ' + 'x' * 20000)
    assert len(str(failure.value)) <= executors.MAX_PROVIDER_ERROR_CHARS
    with pytest.raises(RuntimeError) as failure:
        executors.provider_failure('usage limit reached ' + 'x' * 20000 + ' authentication failed')
    assert type(failure.value) is RuntimeError


@pytest.mark.parametrize('host', ['codex', 'command'])
@pytest.mark.parametrize('boundary', ['leading', 'trailing'])
@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
@pytest.mark.parametrize('echo_mode', ['raw', 'json', 'normalized-json'])
@pytest.mark.parametrize('diagnostic,prompt_phrase,expected', [
    ('usage limit reached', 'authentication failed', executors.ProviderExhausted),
    ('authentication failed', 'usage limit reached', RuntimeError),
])
def test_boundary_echo_redaction_through_real_text_capture(
    tmp_path, monkeypatch, host, boundary, newline, echo_mode, diagnostic, prompt_phrase, expected
):
    monkeypatch.setenv('DK_HOST', 'codex')
    prompt = ' \t' + newline + 'private evidence "\u2603 ' + prompt_phrase + newline + ' \t'
    provider = tmp_path / 'echo_provider.py'
    provider.write_text('''import json,sys
prompt = sys.stdin.buffer.read().decode()
if sys.argv[1] == 'normalized-json':
    prompt = prompt.replace('\\r\\n', '\\n').replace('\\r', '\\n')
echo = prompt if sys.argv[1] == 'raw' else json.dumps(prompt)[1:-1]
diagnostic = sys.argv[3]
detail = echo + '\\n' + diagnostic if sys.argv[2] == 'leading' else diagnostic + '\\n' + echo
sys.stderr.buffer.write(detail.encode())
sys.exit(1)
''')
    provider_argv = [sys.executable, str(provider), echo_mode, boundary, diagnostic]
    run_local = subprocess.run
    captured = []

    def run(argv, **kwargs):
        if 'input' not in kwargs:
            return run_local(argv, **kwargs)
        assert kwargs['input'] == prompt
        if host == 'codex':
            assert argv[:2] == ['codex', 'exec']
        proc = run_local(provider_argv if host == 'codex' else argv, **kwargs)
        captured.append(proc.stderr)
        return proc

    monkeypatch.setattr(executors.subprocess, 'run', run)
    with pytest.raises(expected) as failure:
        if host == 'command':
            executors.invoke({'command': [*provider_argv, '{handoff}']},
                             tmp_path / 'handoff.json', tmp_path, prompt)
        else:
            executors.execute(prompt, tmp_path)
    assert type(failure.value) is expected
    echo = prompt if echo_mode == 'json' else prompt.replace('\r\n', '\n').replace('\r', '\n')
    if echo_mode != 'raw':
        echo = json.dumps(echo)[1:-1]
    assert echo in captured[0]
    message = str(failure.value)
    assert message == message.strip()
    assert diagnostic in message
    assert '[provider input omitted]' in message
    assert 'private evidence' not in message
    assert prompt_phrase not in message
    assert len(message) <= executors.MAX_PROVIDER_ERROR_CHARS


@pytest.mark.parametrize('host,envelope,expected', [
    ('codex', False, 'codex execution failed'),
    ('claude', False, 'claude execution failed'),
    ('command', False, 'Command provider failed'),
    ('claude', True, 'Claude execution failed'),
])
def test_whitespace_only_failure_preserves_adapter_context(tmp_path, monkeypatch, host, envelope, expected):
    monkeypatch.setenv('DK_HOST', host if host != 'command' else 'codex')
    stdout = json.dumps({'subtype': 'error_during_execution', 'is_error': True, 'result': ' \n\t'}) if envelope else ' \n\t'
    monkeypatch.setattr(executors.subprocess, 'run', Mock(return_value=
        subprocess.CompletedProcess([], 0 if envelope else 1, stdout, '\n \t')))
    with pytest.raises(RuntimeError) as failure:
        if host == 'command':
            executors.invoke({'command': [sys.executable, '{handoff}']},
                             tmp_path / 'handoff.json', tmp_path, 'review evidence')
        else:
            executors.execute('review evidence', tmp_path)
    assert type(failure.value) is RuntimeError
    assert str(failure.value) == expected
