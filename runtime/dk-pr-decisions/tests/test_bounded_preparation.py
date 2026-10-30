"""Large requests preserve evidence and correction slots through file transport."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preparation_fixtures import Package, prep, response, retain
from shared import executors
from shared.diagnostics import Diagnostic


def source(envelope):
    assert len(json.dumps(envelope)) < 16_000
    assert 'offset' in envelope and 'single line' in envelope
    return json.loads(envelope.split('ORIGINAL REQUEST FILE: ', 1)[1])


def original(envelope):
    reference = source(envelope)
    path = Path(reference['path'])
    content = path.read_bytes()
    assert len(content) == reference['bytes']
    assert hashlib.sha256(content).hexdigest() == reference['sha256']
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    text = content.decode('utf-8')
    assert len(json.dumps(text)) == reference['serialized_characters']
    return text


@pytest.mark.parametrize('incremental', [False, True])
def test_oversized_assembled_request_preserves_sources_and_parent(tmp_path, monkeypatch, incremental):
    package = Package(tmp_path)
    package.manifest['accumulated_evidence'] = 'historical "雪"\r\n' * 45_000
    package.write('manifest.json', package.manifest)
    previous = package.prepare() if incremental else None
    parent = prep.read(previous['artifact']) if previous else None
    if incremental:
        package.write('user.json', {'instruction': 'Inspect an additional governing constraint.'})
    before = {path: path.read_bytes() for path in package.root.rglob('*') if path.is_file()}
    if previous:
        before[Path(previous['artifact'])] = Path(previous['artifact']).read_bytes()
    calls = []

    def provider(envelope, checkout, **kwargs):
        prompt = original(envelope)
        assert len(json.dumps(prompt)) > executors.CODEX_INPUT_LIMIT
        assert 'INPUT INDEX' in prompt
        if incremental:
            assert 'REASSESSMENT' in prompt and parent['preparation_id'] in prompt
        calls.append(envelope)
        return json.dumps(retain(parent) if incremental else response())

    monkeypatch.setattr(executors, 'execute', provider)
    result = prep.prepare(package.manifest_path, package.versions,
                          previous_preparation=previous['artifact'] if previous else None)
    assert result['readiness'] == 'ready' and len(calls) == 1
    artifact = prep.read(result['artifact'])
    for path, content in before.items():
        assert path.read_bytes() == content
    for item in artifact['sources'].values():
        assert prep.file_hash(item['path']) == item['sha256']
    if parent:
        assert artifact['analysis'] == parent['analysis']
        assert artifact['graph'] == parent['graph']
        assert artifact['parent']['preparation_id'] == parent['preparation_id']
    journal, = Path(source(calls[0])['path']).parent.glob('model-exchange.json')
    saved = json.loads(journal.read_text())
    assert saved['request']['prompt'] == original(calls[0])
    assert saved['attempts'][0]['input_file'] == source(calls[0])


def test_large_correction_resumes_same_spill_and_response_slot(tmp_path, monkeypatch):
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    # Real CRLF, Unicode and quoting must survive byte-for-byte, including in
    # a rejected raw response subsequently embedded in the correction prompt.
    prompt = 'Inspect "雪"\r\n' * 30_000
    rejected = 'invalid "☃"\r\n' * 80_000
    envelopes = []

    def provider(envelope, directory, **kwargs):
        envelopes.append((envelope, kwargs['attempt']))
        request = original(envelope)
        if kwargs['attempt'] == 0:
            assert request == prompt
            return rejected
        assert request.startswith(prompt) and request.endswith(rejected)
        if len(envelopes) == 2:
            raise RuntimeError('provider interrupted')
        return '{}'

    monkeypatch.setattr(executors, 'execute', provider)
    kwargs = {'diagnostic_scope': {'phase': 'initial', 'evidence': 'unchanged'}}
    with pytest.raises(RuntimeError, match='provider interrupted'):
        prep.analyze(prompt, checkout, **kwargs)
    correction = source(envelopes[1][0])
    assert source(envelopes[0][0])['path'] != correction['path']
    assert prep.analyze(prompt, checkout, **kwargs) == {}
    assert [attempt for _, attempt in envelopes] == [0, 1, 1]
    assert envelopes[1] == envelopes[2]
    journal = json.loads((Path(correction['path']).parent / 'model-exchange.json').read_text())
    assert journal['request']['prompt'] == prompt
    assert journal['attempts'][0]['response'] == rejected
    assert journal['attempts'][0]['validation']['type'] == 'JSONDecodeError'
    assert journal['attempts'][1]['input_file'] == journal['attempts'][2]['input_file'] == correction


def test_corrupt_retained_input_blocks_unchanged_retry(tmp_path, monkeypatch):
    prompt = 'governing evidence ' * 80_000
    provider = Mock(side_effect=RuntimeError('provider unavailable'))
    monkeypatch.setattr(executors, 'execute', provider)
    with pytest.raises(RuntimeError, match='provider unavailable'):
        prep.analyze(prompt, tmp_path)
    reference = source(provider.call_args.args[0])
    path = Path(reference['path'])
    exact = path.read_bytes()
    path.write_bytes(b'corrupt retained evidence')
    with pytest.raises(Diagnostic) as failure:
        prep.analyze(prompt, tmp_path)
    assert failure.value.code == 'preparation_input_failed'
    assert str(path) in failure.value.action['artifacts']
    assert path.read_bytes() == b'corrupt retained evidence'
    assert provider.call_count == 1
    # A pre-dispatch failure consumes no response; restoring exact evidence
    # resumes that slot without requiring deletion/reset of the private index.
    path.write_bytes(exact)
    provider.side_effect = None
    provider.return_value = '{}'
    assert prep.analyze(prompt, tmp_path) == {}
    assert provider.call_count == 2
    assert provider.call_args_list[0] == provider.call_args_list[1]


def test_small_initial_with_oversized_rejected_response_spills_only_correction(tmp_path, monkeypatch):
    invalid = 'rejected "雪"\r\n' * 80_000
    requests = []
    def provider(prompt, checkout, **kwargs):
        requests.append(prompt)
        if kwargs['attempt'] == 0:
            return invalid
        assert original(prompt).endswith(invalid)
        return '{}'
    monkeypatch.setattr(executors, 'execute', provider)
    assert prep.analyze('Small original request\r\n', tmp_path) == {}
    assert requests[0] == 'Small original request\r\n'
    assert source(requests[1])['serialized_characters'] > executors.CODEX_INPUT_LIMIT
