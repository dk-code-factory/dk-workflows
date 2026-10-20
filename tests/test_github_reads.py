import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import github_reads as reads


@pytest.mark.parametrize('args,input_text', [
    (['pr', 'view', 'https://github.com/o/r/pull/1'], None),
    (['pr', 'checks', '1', '--json', 'state'], None),
    (['api', '--hostname', 'github.com', 'repos/o/r/pulls'], None),
    (['api', '--method', 'GET', 'repos/o/r/pulls', '-F', 'per_page=100'], None),
    (['api', 'graphql', '-f', 'query=query { viewer { login } }'], None),
    (['api', 'graphql', '--input', '-'], '{"query":"query { viewer { login } }"}'),
])
def test_read_retries_transient_failure_then_preserves_success(args, input_text, monkeypatch):
    calls = []
    success = subprocess.CompletedProcess([], 0, '{"data": {}}', '')
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return success if len(calls) == 2 else subprocess.CompletedProcess([], 1, '', 'HTTP 503')
    monkeypatch.setattr(reads.subprocess, 'run', run)
    monkeypatch.setattr(reads.time, 'sleep', lambda delay: None)
    assert reads.run(['gh', *args], input=input_text, capture_output=True, text=True) is success
    assert len(calls) == 2
    assert calls[0][1]['input'] == input_text
    assert 0 < calls[1][1]['timeout'] <= calls[0][1]['timeout'] <= 60


@pytest.mark.parametrize('args,input_text', [
    (['pr', 'edit', '1', '--body', 'text'], None),
    (['api', '-X', 'POST', 'repos/o/r/issues/1/comments'], None),
    (['api', '--method=PATCH', 'repos/o/r/pulls/1'], None),
    (['api', 'repos/o/r/pulls/1', '-f', 'body=text'], None),
    (['api', 'graphql', '-f', 'query=mutation { doWork }'], None),
    (['api', 'graphql', '--input', '-'], '{"query":"mutation { doWork }"}'),
    (['api', 'graphql', '-f', 'query=query Q { viewer { login } } mutation M { doWork }'], None),
    (['api', 'graphql', '--input', '-'], 'not json'),
])
def test_publication_and_uncertain_commands_never_retried(args, input_text, monkeypatch):
    calls = []
    failed = subprocess.CompletedProcess([], 1, '', 'HTTP 503')
    monkeypatch.setattr(reads.subprocess, 'run', lambda *a, **kw: calls.append(a) or failed)
    assert reads.run(['gh', *args], input=input_text) is failed
    assert len(calls) == 1


@pytest.mark.parametrize('detail', ['HTTP 401: Bad credentials', 'HTTP 403: Forbidden',
                                  'HTTP 404: Not Found', 'invalid JSON', '', 'unknown field'])
def test_auth_schema_and_ci_outcomes_not_retried(detail, monkeypatch):
    calls = []
    failed = subprocess.CompletedProcess([], 1, '[]', detail)
    monkeypatch.setattr(reads.subprocess, 'run', lambda *a, **kw: calls.append(a) or failed)
    assert reads.run(['gh', 'pr', 'checks', '1']) is failed
    assert len(calls) == 1


def test_retries_are_bounded_and_binary_output_unchanged(monkeypatch):
    calls = []
    failed = subprocess.CompletedProcess([], 1, b'partial archive', b'connection reset')
    monkeypatch.setattr(reads.subprocess, 'run', lambda *a, **kw: calls.append(a) or failed)
    monkeypatch.setattr(reads.time, 'sleep', lambda delay: None)
    assert reads.run(['gh', 'api', 'repos/o/r/actions/runs/1/logs'], capture_output=True) is failed
    assert len(calls) == 3


def test_total_deadline_not_reset_after_transport_failure(monkeypatch):
    clock = [0]
    calls = []
    def run(argv, **kwargs):
        calls.append(kwargs['timeout'])
        clock[0] += 8
        return subprocess.CompletedProcess([], 1, '', 'error connecting to api.github.com')
    monkeypatch.setattr(reads.subprocess, 'run', run)
    monkeypatch.setattr(reads.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(reads.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + delay))
    assert reads.run(['gh', 'pr', 'view', '1'], timeout=10).returncode == 1
    assert len(calls) == 2 and calls[1] < 2


def test_real_cli_transport_recovers_without_restarting_caller(tmp_path, monkeypatch):
    executable = tmp_path / 'gh'
    count = tmp_path / 'count'
    executable.write_text('#!' + sys.executable + '\n'
        'import sys,pathlib\n'
        'p=pathlib.Path(' + repr(str(count)) + ')\n'
        'n=int(p.read_text()) if p.exists() else 0\np.write_text(str(n+1))\n'
        'if n==0:\n print("error connecting to api.github.com",file=sys.stderr)\n sys.exit(1)\n'
        'print("{\\"headRefOid\\":\\"' + 'a'*40 + '\\"}")\n')
    executable.chmod(0o755)
    monkeypatch.setattr(reads.time, 'sleep', lambda delay: None)
    result = reads.run([str(executable), 'pr', 'view', '1', '--json', 'headRefOid'],
                       capture_output=True, text=True)
    assert json.loads(result.stdout)['headRefOid'] == 'a'*40
    assert count.read_text() == '2'



def test_transient_read_recovery_does_not_hide_ci_head_change(monkeypatch):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'runtime/dk-pr-iter/scripts/ci_wait.py'
    spec = importlib.util.spec_from_file_location('retry_ci_wait', path)
    waiter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waiter)
    responses = iter([
        subprocess.CompletedProcess([], 1, '', 'TLS handshake timeout'),
        subprocess.CompletedProcess([], 0, json.dumps({'headRefOid': 'a'*40}), ''),
        subprocess.CompletedProcess([], 0, '[{"bucket":"pass"}]', ''),
        subprocess.CompletedProcess([], 0, json.dumps({'headRefOid': 'b'*40}), ''),
    ])
    monkeypatch.setattr(reads.subprocess, 'run', lambda *a, **kw: next(responses))
    monkeypatch.setattr(reads.time, 'sleep', lambda delay: None)
    result = waiter.observe('https://github.com/o/r/pull/1', 'a'*40)
    assert result['result'] == 'head_changed' and result['actual_head'] == 'b'*40



def test_exhausted_timeouts_preserve_original_exception_and_partial_output(monkeypatch):
    timeout = subprocess.TimeoutExpired(['gh'], 1, output=b'partial', stderr=b'transport timeout')
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        raise timeout
    monkeypatch.setattr(reads.subprocess, 'run', run)
    monkeypatch.setattr(reads.time, 'sleep', lambda delay: None)
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        reads.run(['gh', 'pr', 'view', '1'], capture_output=True)
    assert failure.value is timeout
    assert failure.value.output == b'partial'
    assert len(calls) == 3
