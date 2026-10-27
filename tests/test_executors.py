import base64
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import executors
from shared.diagnostics import Diagnostic


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    monkeypatch.delenv('DK_PROVIDER_TIMEOUT_SECONDS', raising=False)
    monkeypatch.delenv('DK_BINDING_STACK', raising=False)
    monkeypatch.delenv('DK_REVIEW_STATS_TRACE', raising=False)
    monkeypatch.setattr(executors, 'managed_update', Mock(return_value=None))


@pytest.fixture
def codex_output(tmp_path, monkeypatch):
    monkeypatch.setenv('DK_HOST', 'codex')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    calls = []
    real_run = subprocess.run
    cleanup = executors.shutil.rmtree

    def install(response=b'{"partial":', stderr=None, returncode=0, stdout='{"complete": true}'):
        def run(argv, **kwargs):
            if argv[:2] != ['codex', 'exec']:
                return real_run(argv, **kwargs)
            output = Path(argv[argv.index('-o') + 1])
            calls.append(output)
            if response is not None:
                output.write_bytes(response)
            detail = (f'Failed to write last message file {json.dumps(str(output))}: '
                      'Disk quota exceeded (os error 122)')
            return subprocess.CompletedProcess(argv, returncode, stdout,
                                               detail if stderr is None else stderr(output))
        monkeypatch.setattr(executors.subprocess, 'run', run)
        return calls
    yield install
    for output in calls:
        if output.parent.exists():
            cleanup(output.parent)


@pytest.mark.parametrize('response', [b'{"partial":', b'{"valid":true}', b'\xff\x00partial'])
@pytest.mark.parametrize('telemetry', [False, True])
def test_codex_write_failure_stops_before_json_and_retains_private_evidence(
    tmp_path, monkeypatch, codex_output, response, telemetry
):
    if telemetry:
        monkeypatch.setenv('DK_REVIEW_STATS_TRACE', str(tmp_path / 'trace.json'))
    calls = codex_output(response)
    validate = Mock(side_effect=AssertionError('transport failure must precede validation'))
    with pytest.raises(Diagnostic) as failure:
        executors.execute_json('Inspect evidence', tmp_path, validate=validate)
    error = failure.value
    assert error.code == 'provider_output_failed'
    assert error.outcome == 'error'
    assert error.dispatched is False
    assert 'repair storage' in error.action['task']
    evidence, = map(Path, error.action['artifacts'])
    assert str(evidence) in str(error)
    saved = json.loads(evidence.read_text())
    assert saved['returncode'] == 0
    assert saved['stdout'] == '{"complete": true}'
    assert 'Disk quota exceeded' in saved['stderr']
    assert base64.b64decode(saved['response_base64']) == response
    assert saved['response_read_error'] is None
    assert saved['attempt'] == 0
    assert saved['argv'][saved['argv'].index('-o') + 1] == str(calls[0])
    assert evidence.stat().st_mode & 0o777 == 0o600
    assert evidence.parent.stat().st_mode & 0o777 == 0o700
    assert evidence.parent.parent.stat().st_mode & 0o777 == 0o700
    assert len(calls) == 1
    assert not calls[0].parent.exists()
    validate.assert_not_called()


@pytest.mark.parametrize('problem', ['missing', 'unreadable', 'unreadable_bytes', 'encoding'])
def test_codex_unavailable_response_is_transport_failure(tmp_path, monkeypatch, codex_output, problem):
    calls = codex_output(None if problem == 'missing' else
                         b'\xff' if problem == 'encoding' else b'available', stderr=lambda _: '')
    original_read = Path.read_text
    if problem in ('unreadable', 'unreadable_bytes'):
        def read(path, *args, **kwargs):
            if path.name == 'result.txt':
                raise PermissionError('response permission denied')
            return original_read(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', read)
        if problem == 'unreadable_bytes':
            original_bytes = Path.read_bytes
            def read_bytes(path):
                if path.name == 'result.txt':
                    raise PermissionError('response permission denied')
                return original_bytes(path)
            monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    with pytest.raises(Diagnostic, match='could not be read') as failure:
        executors.execute_json('Inspect', tmp_path)
    evidence = json.loads(Path(failure.value.action['artifacts'][0]).read_text())
    assert failure.value.code == 'provider_output_failed'
    assert len(calls) == 1
    if problem in ('missing', 'unreadable_bytes'):
        assert evidence['response_base64'] is None
        assert ('No such file' if problem == 'missing' else 'permission denied') in evidence['response_read_error']
    else:
        assert base64.b64decode(evidence['response_base64']) == (b'\xff' if problem == 'encoding' else b'available')


@pytest.mark.parametrize('operation', ['allocate', 'write'])
def test_codex_diagnostic_persistence_failure_retains_original_temp(
    tmp_path, monkeypatch, codex_output, operation
):
    calls = codex_output()
    monkeypatch.setattr(executors, operation, Mock(side_effect=OSError('diagnostic disk full')))
    with pytest.raises(Diagnostic) as failure:
        executors.execute_json('Inspect', tmp_path)
    error = failure.value
    assert 'failed to write the requested response' in str(error)
    assert 'diagnostic persistence also failed: diagnostic disk full' in str(error)
    assert error.action['artifacts'] == [str(calls[0].parent)]
    assert calls[0].read_bytes() == b'{"partial":'
    assert calls[0].parent.stat().st_mode & 0o777 == 0o700
    assert calls[0].stat().st_mode & 0o777 == 0o600
    assert len(calls) == 1


def test_codex_cleanup_failure_preserves_primary_diagnostic(tmp_path, monkeypatch, codex_output):
    calls = codex_output()
    monkeypatch.setattr(executors.shutil, 'rmtree', Mock(side_effect=OSError('cleanup failed')))
    with pytest.raises(Diagnostic, match='Retained evidence') as failure:
        executors.execute_json('Inspect', tmp_path)
    assert failure.value.code == 'provider_output_failed'
    assert Path(failure.value.action['artifacts'][0]).is_file()


def test_codex_permission_hardening_failure_still_retains_readable_bytes(
    tmp_path, monkeypatch, codex_output
):
    calls = codex_output(b'\xff\x00partial')
    chmod = Path.chmod

    def harden(path, *args, **kwargs):
        if path.name == 'result.txt':
            raise PermissionError('response chmod denied')
        return chmod(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'chmod', harden)
    with pytest.raises(Diagnostic) as failure:
        executors.execute_json('Inspect', tmp_path)
    saved = json.loads(Path(failure.value.action['artifacts'][0]).read_text())
    assert failure.value.code == 'provider_output_failed'
    assert base64.b64decode(saved['response_base64']) == b'\xff\x00partial'
    assert saved['response_read_error'] is None
    assert saved['response_permission_error'] == 'response chmod denied'
    assert len(calls) == 1
    assert not calls[0].parent.exists()


def test_codex_lifecycle_completion_failure_preserves_output_failure(
    tmp_path, monkeypatch, codex_output
):
    calls = codex_output()
    lifecycle = Mock(side_effect=[{'token': 'test-invocation'}, OSError('lifecycle disk full')])
    monkeypatch.setattr(executors, 'managed_update', lifecycle)
    validate = Mock(side_effect=AssertionError('transport failure must precede validation'))
    with pytest.raises(Diagnostic) as failure:
        executors.execute_json('Inspect', tmp_path, validate=validate)
    error = failure.value
    assert error.code == 'provider_output_failed'
    assert error.outcome == 'error'
    assert 'failed to write the requested response' in str(error)
    assert 'lifecycle completion also failed: lifecycle disk full' in error.envelope()['diagnostic']['message']
    assert 'repair storage' in error.action['task']
    saved = json.loads(Path(error.action['artifacts'][0]).read_text())
    assert base64.b64decode(saved['response_base64']) == b'{"partial":'
    assert len(calls) == 1
    validate.assert_not_called()
    assert [call.args[2] for call in lifecycle.call_args_list] == ['entry', 'complete']


@pytest.mark.parametrize('returncode', [0, 1])
def test_lifecycle_completion_failure_still_raises_without_output_failure(
    tmp_path, monkeypatch, codex_output, returncode
):
    calls = codex_output(b'{"complete":true}', stderr=lambda _: 'usage limit reached',
                         returncode=returncode)
    monkeypatch.setattr(executors, 'managed_update', Mock(
        side_effect=[{'token': 'test-invocation'}, OSError('lifecycle disk full')]))
    with pytest.raises(OSError, match='lifecycle disk full'):
        executors.execute_json('Inspect', tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize('detail', [
    lambda p: 'unrelated storage warning',
    lambda p: f'Example: Failed to write last message file "{p}": Disk full',
    lambda p: f'"Failed to write last message file \\"{p}\\": Disk full"',
    lambda p: f'Failed to write last message file "{p}.other": Disk full',
])
def test_codex_unrelated_or_quoted_stderr_does_not_trigger_output_failure(tmp_path, codex_output, detail):
    calls = codex_output(b'{"complete":true}', stderr=detail)
    assert executors.execute_json('Inspect', tmp_path) == {'complete': True}
    assert len(calls) == 1


@pytest.mark.parametrize('response', [b'not json', b'{"wrong_schema":true}'])
def test_codex_genuine_json_and_schema_errors_keep_one_correction(tmp_path, codex_output, response):
    calls = codex_output(response, stderr=lambda _: '')
    def validate(value):
        if 'required' not in value:
            raise ValueError('required field missing')
    with pytest.raises(ValueError, match='invalid after one correction'):
        executors.execute_json('Inspect', tmp_path, validate=validate)
    assert len(calls) == 2


def test_codex_nonzero_exit_preserves_provider_classification(tmp_path, codex_output):
    calls = codex_output(None, stderr=lambda _: 'usage limit reached', returncode=1)
    with pytest.raises(executors.ProviderExhausted):
        executors.execute_json('Inspect', tmp_path)
    assert len(calls) == 1


def test_codex_retains_full_stdout_and_stderr_without_echoing_them_in_error(tmp_path, codex_output):
    stdout = 'private stdout' * 2000
    stderr = 'private stderr' * 2000
    captured_stderr = []

    def detail(output):
        emitted = stderr + f'\nFailed to write last message file "{output}": Disk full\n' + stderr
        captured_stderr.append(emitted)
        return emitted

    calls = codex_output(stdout=stdout, stderr=detail)
    with pytest.raises(Diagnostic) as failure:
        executors.execute('Inspect', tmp_path)
    saved = json.loads(Path(failure.value.action['artifacts'][0]).read_text())
    assert saved['stdout'] == stdout
    assert saved['stderr'] == captured_stderr[0]
    assert 'private stdout' not in str(failure.value)
    assert 'private stderr' not in str(failure.value)
    assert len(calls) == 1


@pytest.mark.parametrize('prompt', ['Failed', 'file', 'Disk', 'result.txt'])
def test_codex_short_prompt_does_not_hide_write_failure(tmp_path, codex_output, prompt):
    calls = codex_output(b'{"complete":true}')
    validate = Mock(side_effect=AssertionError('transport failure must precede validation'))
    with pytest.raises(Diagnostic) as failure:
        executors.execute_json(prompt, tmp_path, validate=validate)
    assert failure.value.code == 'provider_output_failed'
    assert len(calls) == 1
    validate.assert_not_called()


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_codex_write_failure_after_newline_terminated_prompt_echo(tmp_path, newline):
    output = tmp_path / 'result.txt'
    prompt = 'Failed' + newline
    diagnostic = f'Failed to write last message file "{output}": Disk full'
    assert executors.codex_write_failed(prompt + diagnostic, output, prompt)


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
@pytest.mark.parametrize('echo_mode', ['raw', 'json', 'normalized-json'])
def test_codex_quoted_diagnostic_in_prompt_echo_does_not_trigger_output_failure(
    tmp_path, newline, echo_mode
):
    output = tmp_path / 'result.txt'
    prompt = newline.join(['Explain this example:',
                           f'Failed to write last message file "{output}": Disk full', 'End example'])
    echo = prompt if echo_mode == 'json' else prompt.replace('\r\n', '\n').replace('\r', '\n')
    if echo_mode != 'raw':
        echo = json.dumps(echo)[1:-1]
    assert not executors.codex_write_failed('user\n' + echo + '\ncodex\nOK', output, prompt)


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
