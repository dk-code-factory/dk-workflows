"""Runner completion and durable reuse require real native specialist evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from test_stats_capture import runner
from preparation_fixtures import Package, analysis, prep
from shared import bindings, executors, review_execution, review_coverage
from shared.review_sessions import Session


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv('DK_HOST', 'codex')
    package = Package(tmp_path)
    plugin = tmp_path / 'plugin'
    (plugin / '.codex-plugin').mkdir(parents=True)
    (plugin / '.codex-plugin/plugin.json').write_text(json.dumps({'name': 'fixture', 'version': '1.0.0'}))
    procedure = plugin / 'skills/review/SKILL.md'
    procedure.parent.mkdir(parents=True)
    procedure.write_text('Review source read-only using independent Specialist.\n')
    provider = {'skill': 'fixture:review', 'host': 'codex', 'nested_review_roles': ['Specialist']}
    name = bindings.key(provider)
    resolved = {'binding': provider, 'identity': name, 'executor': 'codex', 'version': '1.0.0',
                'root': str(plugin), 'procedure': str(procedure)}
    monkeypatch.setattr(bindings, 'resolve', lambda _: resolved)
    descriptor = runner.describe_reviewer(provider)
    package.manifest['evidence']['reviewers'] = [str(package.write('reviewer.json', descriptor))]
    package.write('manifest.json', package.manifest)
    prepared = prep.read(package.prepare(analysis([name]))['artifact'])
    monkeypatch.setattr(runner, 'describe_reviewer', lambda _: descriptor)
    monkeypatch.setattr(runner, 'skill_path', lambda _: Path(descriptor['execution']['procedure']))
    return package, provider, name, prepared


def events(complete=True, missing=False):
    rows = [{'type': 'thread.started', 'thread_id': 'root'}, {'type': 'turn.started'}]
    if not missing:
        rows += [{'type': 'item.completed', 'item': {'type': 'collab_tool_call', 'tool': 'spawn_agent',
            'sender_thread_id': 'root', 'receiver_thread_ids': ['specialist'], 'status': 'completed',
            'prompt': 'DK_REVIEW_PASS=Specialist\nRead the pinned handoff.', 'agents_states': {}}},
            {'type': 'item.completed', 'item': {'type': 'collab_tool_call', 'tool': 'wait',
            'sender_thread_id': 'root', 'receiver_thread_ids': ['specialist'], 'status': 'completed',
            'prompt': None, 'agents_states': {'specialist': {'status': 'completed' if complete else 'running',
                                                          'message': 'No findings.' if complete else None}}}}]
    rows.append({'type': 'turn.completed', 'usage': {}})
    return '\n'.join(json.dumps(row) for row in rows)


def transport(monkeypatch, responses, traces):
    calls = []
    def run(argv, input, **kwargs):
        assert '--json' in argv and '--ephemeral' in argv and 'read-only' in argv
        assert 'DK_REVIEW_PASS=<role>' in input
        index = len(calls)
        calls.append(argv)
        Path(argv[argv.index('-o') + 1]).write_text(responses[index])
        return subprocess.CompletedProcess(argv, 0, traces[index], '')
    monkeypatch.setattr(executors, 'run_provider', run)
    return calls


@pytest.mark.parametrize('trace,reason', [(events(missing=True), 'missing'), ('', 'unavailable'),
                                         (events(complete=False), 'missing')])
def test_clean_json_cannot_complete_without_native_passes(native, monkeypatch, trace, reason):
    package, provider, name, prepared = native
    original = {'findings': [{'severity': 'Minor', 'path': 'transform.py', 'line': 1,
                            'message': 'Original supported finding', 'fix': 'Preserve behavior'}]}
    transport(monkeypatch, [json.dumps(original)], [trace])
    result = runner.review(package.context, [provider], '', package.decisions, '', prepared)
    assert not result['complete'] and result['executed_reviewers'] == []
    assert 'execution ' + reason in result['reason']
    ref = result['reviewer_execution'][name]['reference']
    saved = json.loads(Path(ref['path']).read_text())
    assert saved['original_result'] == original
    assert saved['assessment']['status'] == reason


@pytest.mark.parametrize('trace', [events(missing=True), ''])
def test_execution_gate_failure_cannot_be_accepted_as_provider_failure(native, monkeypatch, trace):
    package, provider, name, prepared = native
    transport(monkeypatch, ['{"findings":[]}'], [trace])
    session = Session(package.context, prepared, [name])
    with session.execution():
        result = runner.review(package.context, [provider], '', package.decisions, '', prepared, session=session)
        reference = session.finish(result)
    with pytest.raises(ValueError, match='review evidence guard failed'):
        review_coverage.verify(reference, prepared, accepted_failures=[name])
    assert result['guard_failure'] and result['outcome'] == 'guard_failed'
    assert not result.get('failed_reviewers')
    assert result['reviewer_execution'][name]['reference']


@pytest.mark.parametrize('stage', ['allocate', 'finish'])
def test_execution_evidence_storage_failure_stays_blocking(native, monkeypatch, stage):
    package, provider, name, prepared = native
    transport(monkeypatch, ['{"findings":[]}'], [events()])
    session = Session(package.context, prepared, [name])
    monkeypatch.setattr(review_execution, stage, Mock(side_effect=OSError('execution evidence storage failed')))
    with session.execution():
        result = runner.review(package.context, [provider], '', package.decisions, '', prepared, session=session)
        reference = session.finish(result)
    assert result['guard_failure'] and result['outcome'] == 'guard_failed'
    with pytest.raises(ValueError, match='review evidence guard failed'):
        review_coverage.verify(reference, prepared, accepted_failures=[name])


def test_native_pass_completion_caches_proof_and_rechecks_it(native, monkeypatch):
    package, provider, name, prepared = native
    calls = transport(monkeypatch, ['{"findings":[]}'], [events()])
    session = Session(package.context, prepared, [name])
    with session.execution():
        result = runner.review(package.context, [provider], '', package.decisions, '', prepared, session=session)
        assert result['complete'], result
        # Simulate interruption after durable reviewer checkpoint, before the
        # coordinator has committed a terminal aggregate result.
    assert len(calls) == 1
    restored = Session(package.context, prepared, [name], path=session.path, resume=True)
    with restored.execution():
        reused = runner.review(package.context, [provider], '', package.decisions, '', prepared, session=restored)
    assert reused['complete'] and reused['reused_reviewers'] == [name] and len(calls) == 1
    reference = result['reviewer_execution'][name]['reference']
    Path(reference['path']).write_text('{}')
    again = Session(package.context, prepared, [name], path=session.path, resume=True)
    with again.execution():
        rejected = runner.review(package.context, [provider], '', package.decisions, '', prepared, session=again)
    assert not rejected['complete'] and rejected['guard_failure']
    assert len(calls) == 1


def test_one_correction_preserves_original_native_execution(native, monkeypatch):
    package, provider, name, prepared = native
    calls = transport(monkeypatch, ['invalid', '{"findings":[]}'], [events(), events(missing=True)])
    result = runner.review(package.context, [provider], '', package.decisions, '', prepared)
    assert result['complete'], result
    assert len(calls) == 2
    saved = json.loads(Path(result['reviewer_execution'][name]['reference']['path']).read_text())
    assert len(saved['attempts']) == 2 and saved['assessment']['completed'] == ['Specialist']


def test_final_coverage_rejects_historical_clean_json_without_proof(native, monkeypatch):
    package, provider, name, prepared = native
    transport(monkeypatch, ['{"findings":[]}'], [events()])
    session = Session(package.context, prepared, [name])
    with session.execution():
        result = runner.review(package.context, [provider], '', package.decisions, '', prepared, session=session)
        reference = session.finish(result)
    review_coverage.verify(reference, prepared)
    saved = review_coverage.load(reference)
    del saved['result']['reviewer_execution']
    path = Path(reference['path']).parent / 'historical.json'
    from shared.private_state import write
    write(path, saved)
    historical = {'path': str(path), 'sha256': review_coverage.digest(saved)}
    with pytest.raises(ValueError, match='specialist execution evidence unavailable'):
        review_coverage.verify(historical, prepared)
