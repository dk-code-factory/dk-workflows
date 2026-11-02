"""Oversized JSON corrections retain full inputs without relaxing validation."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import executors

LIMIT = 1024 * 1024 - 64 * 1024


@pytest.fixture
def storage(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    monkeypatch.setenv('XDG_STATE_HOME', str(state))
    return state, checkout


def retained(state):
    index, = state.rglob('json-correction-evidence.json')
    return index, json.loads(index.read_text())['files']


def check_file(files, name, expected):
    entry = files[name]
    path = Path(entry['path'])
    assert path.read_bytes() == expected.encode('utf-8')
    assert entry['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entry['sha256'] in path.name
    assert entry['characters'] == len(expected)
    assert entry['bytes'] == len(path.read_bytes())
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_uid == os.getuid()
    return path


@pytest.mark.parametrize('mode', ['original-inline', 'original-file', 'escaped-size'])
def test_large_correction_retains_exact_private_inputs_and_fits_transport(storage, capsys, mode):
    state, checkout = storage
    original = ('Original constraints\r\nRead all evidence.\n' +
                ('x' * 800_000 if mode == 'original-inline' else
                 '\u00e9' * 150_000 + 'x' * 120_000 if mode == 'original-file' else 'small request'))
    prior = json.dumps({'complete': False, 'payload': '\n' * 530_000 if mode == 'escaped-size' else 'p' * 250_000})
    error = 'complete must be true; preserve exact constraints\r\nNo authority granted.'
    instructions = 'Keep every evidence requirement and field type.\nDo not change policy.'
    calls = []

    def validate(value):
        if value.get('complete') is not True:
            raise ValueError(error)

    def model(prompt, cwd, *, attempt):
        calls.append((prompt, cwd, attempt))
        if attempt == 0:
            assert prompt == original
            return prior
        assert len(json.dumps(prompt, ensure_ascii=True)) <= LIMIT
        index, files = retained(state)
        assert str(index) in capsys.readouterr().err  # Announced before provider execution.
        assert prior not in prompt
        assert ('ALL evidence' in prompt and 'sequential chunks' in prompt
                and 'do not truncate' in prompt and 'without Markdown fences' in prompt)
        check_file(files, 'original-request', original)
        check_file(files, 'prior-response', prior)
        check_file(files, 'validation-error', error)
        check_file(files, 'correction-instructions', instructions)
        check_file(files, 'correction-request', prompt)
        assert all(files[name]['path'] in prompt for name in
                   ('prior-response', 'validation-error', 'correction-instructions'))
        assert prompt.startswith(original) is (mode != 'original-file')
        if mode == 'original-file':
            assert files['original-request']['path'] in prompt
        return '{"complete": true}'

    assert len(json.dumps(original + prior, ensure_ascii=True)) > 1024 * 1024
    assert executors.execute_json(original, checkout, executor=model, validate=validate,
                                 correction_instructions=instructions) == {'complete': True}
    assert [call[2] for call in calls] == [0, 1]
    index, files = retained(state)
    assert index.stat().st_mode & 0o777 == 0o600
    assert index.parent.stat().st_mode & 0o777 == 0o700
    assert index.parent.parent.stat().st_mode & 0o777 == 0o700
    assert index.stat().st_uid == index.parent.stat().st_uid == os.getuid()
    assert not index.is_relative_to(checkout)
    assert not index.is_relative_to(Path(executors.__file__).resolve().parents[2])


def test_small_correction_preserves_exact_inline_behavior_without_storage(storage, capsys):
    state, checkout = storage
    prompts = []

    def model(prompt, cwd, *, attempt):
        prompts.append(prompt)
        return '{"complete": false}' if attempt == 0 else '{"complete": true}'

    def validate(value):
        if value['complete'] is not True:
            raise RuntimeError('complete must be true')

    assert executors.execute_json('Original request', checkout, executor=model,
        validate=validate, correction_instructions='Keep exact fields.') == {'complete': True}
    assert prompts[1] == (
        'Original request\n\nCorrection: complete must be true'
        '\nReturn the complete result as exactly one JSON object without Markdown fences. '
        'Follow the declared field types and preserve the same evidence and constraints. '
        'Do not invent missing evidence or grant publication authority. Keep exact fields.'
        '\nPrior response:\n{"complete": false}')
    assert not state.exists()
    assert not capsys.readouterr().err


@pytest.mark.parametrize('second', ['not JSON\r\n', '[]', '{"complete": false}'])
def test_large_correction_invalid_second_output_stays_blocked_and_is_retained(storage, capsys, second):
    state, checkout = storage
    calls = []
    validations = []
    prior = json.dumps({'complete': False, 'payload': '\n' * 530_000})

    def model(prompt, cwd, *, attempt):
        calls.append(attempt)
        return prior if attempt == 0 else second

    def validate(value):
        validations.append(value)
        if value.get('complete') is not True:
            raise ValueError('complete must be true')

    with pytest.raises(ValueError, match='invalid after one correction') as failure:
        executors.execute_json('Original request', checkout, executor=model, validate=validate)
    index, files = retained(state)
    assert str(index) in str(failure.value) and str(index) in capsys.readouterr().err
    assert str(failure.value) == 'Model JSON invalid after one correction; retained evidence: ' + str(index)
    assert calls == [0, 1]
    assert len(validations) == (2 if second.startswith('{') else 1)
    check_file(files, 'prior-response', prior)
    check_file(files, 'corrected-response', second)
    if second.startswith('not JSON'):
        with pytest.raises(ValueError) as expected:
            json.loads(second)
        message = str(expected.value)
    else:
        message = 'expected one JSON object' if second == '[]' else 'complete must be true'
    check_file(files, 'corrected-validation-error', message)


@pytest.mark.parametrize('failure', [RuntimeError('backend input rejected'),
                                    executors.ProviderExhausted('usage limit reached'),
                                    OSError('provider pipe failed')])
def test_correction_provider_failure_propagates_once_with_prior_evidence(storage, capsys, failure):
    state, checkout = storage
    calls = []
    prior = 'not JSON\n' + '\n' * 530_000

    def model(prompt, cwd, *, attempt):
        calls.append(attempt)
        if attempt:
            raise failure
        return prior

    with pytest.raises(type(failure)) as caught:
        executors.execute_json('Original request', checkout, executor=model)
    assert caught.value is failure
    assert calls == [0, 1]
    index, files = retained(state)
    assert str(index) in capsys.readouterr().err
    check_file(files, 'prior-response', prior)
    assert 'corrected-response' not in files


def test_large_correction_storage_failure_blocks_before_second_provider(storage, monkeypatch):
    _, checkout = storage
    calls = []

    def model(prompt, cwd, *, attempt):
        calls.append(attempt)
        return '\n' * 530_000

    def unavailable(**kwargs):
        raise OSError('private storage unavailable')

    monkeypatch.setattr(executors, 'allocate', unavailable)
    with pytest.raises(OSError, match='private storage unavailable'):
        executors.execute_json('Original request', checkout, executor=model)
    assert calls == [0]


@pytest.mark.parametrize('valid', [False, True])
def test_large_correction_preserves_scoped_diagnostics_and_budget(storage, valid):
    from shared.model_diagnostics import ValidationFailure

    state, checkout = storage
    prior = 'not JSON\n' + '\n' * 530_000
    calls = []

    def model(prompt, cwd, *, attempt):
        calls.append(attempt)
        if attempt == 0:
            return prior
        assert len(json.dumps(prompt, ensure_ascii=True)) <= LIMIT
        return '{"complete": true}' if valid else 'invalid correction'

    kwargs = {'executor': model, 'diagnostic_scope': 'large-correction-fixture'}
    if valid:
        assert executors.execute_json('Inspect all evidence', checkout, **kwargs) == {'complete': True}
    else:
        with pytest.raises(ValidationFailure):
            executors.execute_json('Inspect all evidence', checkout, **kwargs)
        with pytest.raises(ValidationFailure):
            executors.execute_json('Inspect all evidence', checkout, **kwargs)
    journal, = state.rglob('model-exchange.json')
    saved = json.loads(journal.read_text())
    assert saved['status'] == ('accepted' if valid else 'rejected')
    assert [item['attempt'] for item in saved['attempts']] == [0, 1]
    assert saved['attempts'][0]['response'] == prior
    _, files = retained(state)
    assert saved['attempts'][1]['prompt'] == Path(files['correction-request']['path']).read_text()
    assert saved['attempts'][1]['response'] == ('{"complete": true}' if valid else 'invalid correction')
    assert calls == [0, 1]


def test_large_correction_provider_repair_reuses_same_response_slot(storage):
    from shared.model_diagnostics import ValidationFailure

    state, checkout = storage
    calls = []

    def model(prompt, cwd, *, attempt):
        calls.append((prompt, attempt))
        if attempt == 0:
            return 'not JSON\n' + '\n' * 530_000
        raise RuntimeError('provider unavailable')

    scope = 'large-correction-recovery-fixture'
    with pytest.raises(RuntimeError, match='provider unavailable'):
        executors.execute_json('Inspect all evidence', checkout, executor=model, diagnostic_scope=scope)
    _, files = retained(state)
    evidence = {entry['path']: Path(entry['path']).read_bytes() for entry in files.values()}

    def repaired(prompt, cwd, *, attempt):
        assert (prompt, attempt) == calls[-1]
        assert all(Path(path).read_bytes() == content for path, content in evidence.items())
        calls.append((prompt, attempt))
        return 'invalid correction'

    with pytest.raises(ValidationFailure):
        executors.execute_json('Inspect all evidence', checkout, executor=repaired, diagnostic_scope=scope)
    with pytest.raises(ValidationFailure):
        executors.execute_json('Inspect all evidence', checkout, executor=repaired, diagnostic_scope=scope)
    journal, = state.rglob('model-exchange.json')
    saved = json.loads(journal.read_text())
    assert [item['attempt'] for item in saved['attempts']] == [0, 1, 1]
    assert saved['attempts'][1]['provider_error']['detail'] == 'provider unavailable'
    assert saved['attempts'][2]['response'] == 'invalid correction'
    assert [attempt for _, attempt in calls] == [0, 1, 1]
