import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared.reviewer_telemetry import ENV, capture, provider_metadata


def test_claude_totals_preserve_cache_and_model_fields_without_double_counting():
    envelope = {'subtype': 'success', 'result': '{"findings": []}',
                'usage': {'input_tokens': 10, 'output_tokens': 5,
                          'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 20},
                'modelUsage': {'model-a': {'inputTokens': 130}, 'model-b': {'inputTokens': 30}},
                'total_cost_usd': 0.25}
    process = subprocess.CompletedProcess([], 0, json.dumps(envelope), '')
    model, usage = provider_metadata(process, 'claude')
    assert model == 'model-a, model-b'
    assert usage['input_tokens'] == 10
    assert usage['cache_read_input_tokens'] == 100
    assert usage['cache_creation_input_tokens'] == 20
    assert usage['model_usage'] == envelope['modelUsage']
    assert usage['total_cost_usd'] == 0.25
    assert 'model_usage' not in envelope['usage']


def test_codex_text_summary_does_not_invent_a_token_breakdown():
    process = subprocess.CompletedProcess([], 0, '',
        'OpenAI Codex v1\n--------\nmodel: model-a\nprovider: openai\n--------\n'
        'user\nmodel: prompt-text\ncodex\n{"findings": []}\ntokens used\n101,570\n')
    model, usage = provider_metadata(process, 'codex')
    assert model == 'model-a'
    assert usage == {'total_tokens': 101570, 'source': 'codex_cli_summary'}


@pytest.mark.parametrize('provider', ['claude', 'anthropic', 'codex', 'openai', 'command', None])
@pytest.mark.parametrize('stdout,stderr', [('', ''), ('not json', 'unavailable'),
                                         ('[]', ''), ('{"findings": []}', '')])
def test_unavailable_usage_stays_unknown(provider, stdout, stderr):
    assert provider_metadata(subprocess.CompletedProcess([], 1, stdout, stderr), provider) == (None, None)


def test_explicit_provider_metadata_is_preserved():
    process = subprocess.CompletedProcess([], 0, '{"usage": {"input_tokens": 999}}', '')
    process.model, process.usage = 'exposed', {'input_tokens': 5}
    assert provider_metadata(process, 'claude') == ('exposed', {'input_tokens': 5})


def test_real_subprocess_transport_usage_reaches_trace_without_changing_output(tmp_path, monkeypatch):
    trace = tmp_path / 'trace.json'
    monkeypatch.setenv(ENV, str(trace))
    envelope = {'subtype': 'success', 'usage': {'input_tokens': 7},
                'modelUsage': {'model-a': {'inputTokens': 7}}, 'result': '{"findings": []}'}
    process = capture(subprocess.run, [sys.executable, '-c', 'print(' + repr(json.dumps(envelope)) + ')'],
                      text=True, capture_output=True, telemetry_prompt='review',
                      telemetry_attempt=0, telemetry_provider='claude')
    assert json.loads(process.stdout) == envelope
    attempt, = json.loads(trace.read_text())['attempts']
    assert attempt['usage']['input_tokens'] == 7
    assert attempt['model'] == 'model-a'
    assert attempt['stdout'] == process.stdout
    assert attempt['returncode'] == 0


def test_timeout_retains_real_subprocess_diagnostics_and_partial_artifact(tmp_path, monkeypatch):
    trace = tmp_path / 'trace.json'
    partial = tmp_path / 'partial.txt'
    monkeypatch.setenv(ENV, str(trace))
    script = ('import sys,time; from pathlib import Path; '
              'Path(sys.argv[1]).write_text("partial review"); '
              'print("provider started", flush=True); '
              'print("model: model-a", file=sys.stderr, flush=True); '
              'time.sleep(30)')
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        capture(subprocess.run, [sys.executable, '-c', script, str(partial)],
                text=True, capture_output=True, timeout=1,
                telemetry_prompt='review', telemetry_attempt=0,
                telemetry_provider='codex', telemetry_output=partial)
    attempt, = json.loads(trace.read_text())['attempts']
    assert attempt['error_type'] == 'TimeoutExpired'
    assert attempt['timeout_seconds'] == failure.value.timeout == 1
    assert attempt['stdout'] == 'provider started\n'
    assert attempt['stderr'] == 'model: model-a\n'
    assert attempt['output'] == 'partial review'
    assert attempt['model'] == 'model-a'
    assert attempt['usage'] is None and attempt['returncode'] is None
    assert 'timed out' in attempt['error']
    assert os.environ[ENV] == str(trace)


def test_timeout_decodes_invalid_utf8_without_losing_trace(tmp_path, monkeypatch):
    trace = tmp_path / 'trace.json'
    monkeypatch.setenv(ENV, str(trace))
    timeout = subprocess.TimeoutExpired(['provider'], 1, output=b'partial \xff', stderr=b'failure \xff')
    def provider():
        raise timeout
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        capture(provider, telemetry_prompt='review', telemetry_attempt=0, telemetry_provider='claude')
    assert failure.value is timeout
    attempt, = json.loads(trace.read_text())['attempts']
    assert attempt['stdout'] == 'partial \ufffd'
    assert attempt['stderr'] == 'failure \ufffd'
    assert attempt['usage'] is None



def test_explicit_subprocess_environment_cannot_reinstrument_nested_provider(tmp_path, monkeypatch):
    trace = tmp_path / 'trace.json'
    monkeypatch.setenv(ENV, str(trace))
    child_env = dict(os.environ)
    process = capture(subprocess.run,
                      [sys.executable, '-c', 'import os; print(os.environ.get("' + ENV + '"))'],
                      env=child_env, text=True, capture_output=True,
                      telemetry_prompt='review', telemetry_attempt=0)
    assert process.stdout == 'None\n'
    assert child_env[ENV] == os.environ[ENV] == str(trace)
    assert len(json.loads(trace.read_text())['attempts']) == 1
