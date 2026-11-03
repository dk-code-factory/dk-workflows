"""Iteration runs architecture once before its configured-reviewer loop."""
from pathlib import Path
import subprocess

import pytest

from test_completion_efficiency import fresh, dispatch, refresh, runner, architecture
from test_review_coverage import state, prep, read, write, refresh_prepared
from shared import iteration_architecture, review_coverage, review_sessions


def enable(fresh):
    p, prepared, path, *_ = fresh
    record = read(path, {})
    record['iteration_run']['architecture_review_mode'] = 'once_before_cycles'
    write(path, record)
    return p, prepared, path


def test_gate_precedes_cycles_and_is_reused_across_cycles_and_resume(fresh, monkeypatch):
    p, prepared, path = enable(fresh)
    calls = []
    actual = architecture.review
    def evaluate(*args, **kwargs):
        calls.append('architecture')
        return actual(*args, **kwargs)
    monkeypatch.setattr(architecture, 'review', evaluate)
    record = read(path, {})
    assert state.next_stage(record, p.head, path)['stage'] == 'architecture_review'
    with pytest.raises(ValueError, match='pre-loop architecture review required'):
        state.next_cycle(record, p.head)
    assert not record['cycles']
    reference = state.run_architecture_review(path, p.head)
    assert state.run_architecture_review(path, p.head) == reference
    state.start_run(read(path, {}), resume=True)
    for _ in range(2):
        result, session = dispatch(fresh)
        assert result['complete'] and result['iteration_architecture'] == reference
        assert result['architecture_accounting'] == {'executed': [], 'reused': [], 'failed': [], 'exhausted': []}
        assert 'architecture-review' not in read(session.path, {})['reviews']
    assert len(fresh[4]) == 4 and calls == ['architecture']
    assert state.verify_review(path, prepared)
    assert refresh(fresh)['model_call_count'] == 0
    refreshed = prep.read(read(path, {})['preparation']['path'])
    assert state.verify_review(path, refreshed)
    value = iteration_architecture.verify(reference, refreshed)
    assert value['head'] == p.head
    state.finish_run(record := read(path, {}), 'completed', 'Verified')
    state.start_run(record)
    assert 'architecture_stage' not in record and record['iteration_run']['architecture_review_mode'] == 'once_before_cycles'


def test_new_tree_uses_baseline_architecture_and_fresh_configured_review(fresh, monkeypatch):
    p, prepared, path = enable(fresh)
    reference = state.run_architecture_review(path, p.head)
    original_head = p.head
    dispatch(fresh)
    (p.repo / 'transform.py').write_text('def transform(value):\n    return str(value)\n')
    prep.git(p.repo, 'add', 'transform.py')
    prep.git(p.repo, 'commit', '-qm', 'Update transform behavior')
    p.head = prep.git(p.repo, 'rev-parse', 'HEAD')
    p.decisions['head_sha'] = p.head
    for name in ('inline.json', 'conversation.json'):
        value = read(p.root / name, {})
        value['pullRequest']['headRefOid'] = p.head
        if name == 'conversation.json':
            value.pop('sha256')
            value['sha256'] = prep.digest(value)
        p.write(name, value)
    refresh_prepared(path, p.context, prepared)
    monkeypatch.setattr(architecture, 'review', lambda *a, **kw: pytest.fail('architecture repeated'))
    result, _ = dispatch(fresh)
    assert result['executed_reviewers'] == fresh[3]
    assert result['iteration_architecture'] == reference
    assert iteration_architecture.verify(reference, prepared)['head'] == original_head
    assert state.verify_review(path, prepared)['head'] == p.head


@pytest.mark.parametrize('failure', ['provider', 'interrupted'])
def test_failed_or_uncertain_stage_cannot_dispatch_again(fresh, monkeypatch, failure):
    p, prepared, path = enable(fresh)
    calls = []
    def evaluate(*args, **kwargs):
        calls.append('architecture')
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(architecture, 'review', evaluate)
    if failure == 'interrupted':
        record = read(path, {})
        record['architecture_stage'] = {'status': 'running'}
        write(path, record)
    else:
        with pytest.raises(RuntimeError, match='provider unavailable'):
            state.run_architecture_review(path, p.head)
    with pytest.raises(ValueError, match='pre-loop architecture review required'):
        state.run_architecture_review(path, p.head)
    with pytest.raises(ValueError, match='pre-loop architecture review required'):
        state.next_cycle(read(path, {}), p.head)
    assert len(calls) == (failure == 'provider')
    assert not read(path, {})['cycles']


def test_stage_rejects_tampering_and_another_run(fresh):
    p, prepared, path = enable(fresh)
    reference = state.run_architecture_review(path, p.head)
    with pytest.raises(ValueError, match='identity changed'):
        iteration_architecture.verify(reference, prepared, run_id='another-run')
    damaged = read(Path(reference['path']), {})
    damaged['iteration_run_id'] = 'another-run'
    write(Path(reference['path']), damaged)
    with pytest.raises(ValueError, match='identity changed'):
        state.next_cycle(read(path, {}), p.head)


def test_architecture_violations_require_verified_disposition_before_cycles(fresh, monkeypatch):
    p, prepared, path = enable(fresh)
    # Install a catalog rule in the pinned baseline before rebuilding preparation.
    rule = {'id': 'arch-transform-boundary', 'revision': 1, 'text': 'Keep the transform local.', 'origin': 'user-created',
            'agents': [], 'patterns': ['transform.py'], 'auto_approve': False, 'approval_when': ''}
    catalog = architecture.current(p.repo, baseline=p.context['base_sha'])
    architecture.edit(p.repo, rule, expected=catalog['fingerprint'], user={'actor': 'user', 'instruction': 'Keep the transform local.', 'source': 'fixture'})
    refresh_prepared(path, p.context, prepared)
    def judge(prompt, checkout, validate=None):
        packet = architecture.load(architecture._PACKET.get())
        return {'complete': True, 'reason': '', 'coverage': [
            {'rule_id': r['id'], 'revision': r['revision'], 'status': 'violation',
             'rationale': 'Transform crosses the boundary.',
             'evidence': [{'path': 'transform.py', 'start': 1, 'end': 1}]}
            for r in packet['rules']]}
    monkeypatch.setattr(architecture, 'analyze', judge)
    reference = state.run_architecture_review(path, p.head)
    findings = iteration_architecture.verify(reference, prepared)['findings']
    assert len(findings) == 1
    record = read(path, {})
    with pytest.raises(ValueError, match='architecture finding remains actionable'):
        state.next_cycle(record, p.head)
    ref = 'architecture-finding:' + review_coverage.digest(findings[0])
    state.classify(record, ref, p.head, 'unsupported', 'fixture:verified-code', 'The transform remains local.')
    write(path, record)
    assert dispatch(fresh)[0]['complete']
    assert state.verify_review(path, prepared)


def test_role_recovery_does_not_require_or_dispatch_an_architecture_role(fresh, monkeypatch):
    p, prepared, path = enable(fresh)
    state.run_architecture_review(path, p.head)
    original = runner.run_skill_reviewer
    def provider(name, *args):
        if name == 'two':
            raise subprocess.TimeoutExpired(['fixture-provider'], 1)
        return original(name, *args)
    monkeypatch.setattr(runner, 'run_skill_reviewer', provider)
    result, session = dispatch(fresh)
    assert not result['complete']
    assert review_sessions.recoverable_roles(read(session.path, {})) == ['two']
    state.recover_cycle_file(path, p.head)
    monkeypatch.setattr(runner, 'run_skill_reviewer', original)
    monkeypatch.setattr(architecture, 'review', lambda *a, **kw: pytest.fail('architecture repeated'))
    result, _ = dispatch(fresh, resume=True)
    assert result['complete'] and result['reused_reviewers'] == ['one']
