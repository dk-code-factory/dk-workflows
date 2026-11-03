import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from test_stats_capture import pinned as original_pinned, finding, runner, responses, prepare_current
from stats_capture import api
from shared.review_sessions import Session, result_artifact
from shared import review_coverage
from preparation_fixtures import interrupt_after_checkpoint


@pytest.fixture
def pinned(original_pinned):
    return original_pinned


@pytest.mark.parametrize('mode', ['standalone', 'iteration'])
def test_interrupted_review_reuses_completed_provider_without_analytics(pinned, mode):
    context, decisions, prep, names, git = pinned
    prep['mode'] = mode
    prepare_current(context, decisions, prep)
    path = Path(context['patch']).parent / 'session.json'
    session = Session(context, prep, names, path=path)
    with session.execution(), patch.object(runner, 'run_skill_reviewer',
            side_effect=responses(names, [{'findings': [finding()]}, RuntimeError('provider unavailable')])):
        first = runner.review(context, names, '', decisions, '', prep, session=session)
        first_item = session.finish(first)
    assert not first['complete'] and first['executed_reviewers'] == names[:1]
    resumed = Session(context, prep, names, path=path, resume=True)
    with resumed.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as calls:
        result = runner.review(context, names, '', decisions, '', prep, session=resumed)
        item = resumed.finish(result)
    assert calls.call_count == 1 and calls.call_args.args[0] == names[1]
    assert result['complete'] and result['reused_reviewers'] == names[:1]
    assert result['executed_reviewers'] == names[1:]
    assert result_artifact(path, item)['session_id'] == session.id
    assert review_coverage.verify(item, prep)['result'] == result
    assert len(json.loads(path.read_text())['results']) == 2
    with pytest.raises(ValueError, match='latest registered'):
        result_artifact(path, first_item)
    with pytest.raises(ValueError, match='finished'):
        Session(context, prep, names, path=path, resume=True)
    fresh = Session(context, prep, names)
    assert fresh.id != session.id
    with fresh.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as calls:
        runner.review(context, names, '', decisions, '', prep, session=fresh)
    assert calls.call_count == 2


@pytest.mark.parametrize('failure', ['descriptor', 'status'])
def test_coordinator_failure_preserves_completed_checkpoint_for_following_resume(pinned, failure):
    context, decisions, prep, names, git = pinned
    session = Session(context, prep, names)
    with session.execution(), patch.object(runner, 'run_skill_reviewer',
            side_effect=interrupt_after_checkpoint(names[0], {'findings': [finding()]},
                lambda: session.cached(names[0]) is not None)):
        with pytest.raises(KeyboardInterrupt):
            runner.review(context, names, '', decisions, '', prep, session=session)
    before = json.loads(session.path.read_text())
    checkpoint = Path(before['reviews'][names[0]]['checkpoint']['path'])
    checkpoint_bytes = checkpoint.read_bytes()
    resumed = Session(context, prep, names, path=session.path, resume=True)
    describe, run = runner.describe_reviewer, runner.run
    status_reads = 0

    def describe_reviewer(name):
        if failure == 'descriptor':
            raise OSError('transient descriptor read failed')
        return describe(name)

    def checked_run(args, cwd=None):
        nonlocal status_reads
        if args == ['git', 'status', '--porcelain=v1']:
            status_reads += 1
            if failure == 'status' and status_reads == 2:
                raise OSError('transient status read failed')
        return run(args, cwd)

    with resumed.execution(), patch.object(runner, 'describe_reviewer', side_effect=describe_reviewer), \
            patch.object(runner, 'run', side_effect=checked_run), \
            patch.object(runner, 'run_skill_reviewer') as providers:
        incomplete = runner.review(context, names, '', decisions, '', prep, session=resumed)
        resumed.finish(incomplete)
    providers.assert_not_called()
    assert not incomplete['complete'] and incomplete['failed_reviewer'] == names[0]
    assert incomplete['reason'] == f'transient {failure} read failed'
    assert incomplete['executed_reviewers'] == incomplete['reused_reviewers'] == []
    after = json.loads(session.path.read_text())
    assert after['reviews'] == before['reviews']
    assert after['review_history'] == before['review_history']
    assert checkpoint.read_bytes() == checkpoint_bytes

    recovered = Session(context, prep, names, path=session.path, resume=True)
    with recovered.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as providers:
        result = runner.review(context, names, '', decisions, '', prep, session=recovered)
        item = recovered.finish(result)
    assert [call.args[0] for call in providers.call_args_list] == names[1:]
    assert result['complete'] and result['reused_reviewers'] == names[:1]
    assert result['executed_reviewers'] == names[1:]
    assert result['findings'] == [{**finding(), 'reviewers': names[:1]}]
    assert result_artifact(session.path, item)['result'] == result
    assert review_coverage.verify(item, prep)['result'] == result
    assert not git('status', '--porcelain')


def test_failed_checkpoint_validation_remains_visible_without_replacing_completed_slot(pinned):
    context, decisions, prep, names, git = pinned
    session = Session(context, prep, names)
    session.record(names[0], 'completed', {'findings': []})
    before = json.loads(session.path.read_text())
    Path(before['reviews'][names[0]]['checkpoint']['path']).write_text('{}')
    with session.execution(), patch.object(runner, 'run_skill_reviewer') as providers:
        result = runner.review(context, names, '', decisions, '', prep, session=session)
    providers.assert_not_called()
    assert not result['complete'] and result['reason'] == 'review checkpoint changed'
    after = json.loads(session.path.read_text())
    assert after['reviews'] == before['reviews'] and after['review_history'] == before['review_history']
    with pytest.raises(ValueError, match='review checkpoint changed'):
        session.cached(names[0])


def test_resumed_main_preserves_statistics_comparison_cohort(pinned, monkeypatch, capsys):
    context, decisions, prep, names, git = pinned
    third = 'dk-pr-read-comments'
    prep['reviewers'][third] = {**prep['reviewers'][names[0]], 'reviewer': third}
    prep['analysis']['reviewer_addenda'][third] = prep['analysis']['reviewer_addenda'][names[0]]
    names.append(third)
    prepare_current(context, decisions, prep)
    directory = Path(context['patch']).parent
    preparation_path = directory / 'preparation.json'
    preparation_path.write_text(json.dumps(prep))
    session_path = directory / 'session.json'
    argv = ['run_reviewers.py', '--context', runner.preparation_api.source_path(prep, 'context'),
            '--decisions-file', runner.preparation_api.source_path(prep, 'decisions'),
            '--preparation-file', str(preparation_path), '--session-file', str(session_path),
            '--stats-cycle', '1']
    real_run = runner.run
    def run(args, cwd=None):
        if args[0] == 'python3':
            return json.dumps({'reviewers': names})
        return real_run(args, cwd)
    monkeypatch.setattr(runner, 'run', run)
    monkeypatch.setattr(sys, 'argv', argv)
    with patch.object(runner, 'run_skill_reviewer', side_effect=responses(names, [
            {'findings': [finding()]}, RuntimeError('provider interrupted'), {'findings': []}])) as calls:
        runner.main()
    first = json.loads(capsys.readouterr().out)
    assert not first['complete'] and first['executed_reviewers'] == [names[0], names[2]]
    assert sorted(call.args[0] for call in calls.call_args_list) == sorted(names)
    first_run = json.loads((directory / 'review-stats.json').read_text())['run_id']

    monkeypatch.setattr(sys, 'argv', [*argv, '--resume'])
    with patch.object(runner, 'run_skill_reviewer', side_effect=[
            {'findings': [finding()]}]) as calls:
        runner.main()
    resumed = json.loads(capsys.readouterr().out)
    assert resumed['complete'] and resumed['reused_reviewers'] == [names[0], names[2]]
    assert resumed['executed_reviewers'] == [names[1]]
    assert [call.args[0] for call in calls.call_args_list] == [names[1]]
    assert resumed['findings'] == [{**finding(), 'reviewers': names[:2]}]
    assert resumed['review_session'] == first['review_session']
    assert len(json.loads(session_path.read_text())['results']) == 2

    sidecar = json.loads((directory / 'review-stats.json').read_text())
    assert sidecar['run_id'] != first_run
    runs = api.ledger(sidecar['repository'])['runs']
    initial, latest = runs[first_run], runs[sidecar['run_id']]
    assert [initial['reviews'][name]['status'] for name in names] == ['completed', 'failed', 'completed']
    api.record_assessments(sidecar['repository'], [{
        'id': 'resumed-finding', 'finding_id': latest['reviews'][names[1]]['findings'][0]['id'],
        'canonical_issue': 'same-defect', 'verdict': 'confirmed', 'severity': 'Major',
        'disposition': 'actionable', 'evidence': ['controlled provider response'],
        'reason': 'Confirmed the resumed reviewer finding; initial reviewer remains unassessed.'}])
    totals = api.summary(sidecar['repository'])['total']
    assert totals['reviews']['completed'] == 3
    assert totals['unverified']['total'] == 1
    assert totals['exclusive']['total'] == 0
    assert totals['exclusive_unknown']['total'] == 1
    assert latest['reviewers'] == names
    assert [latest['reviews'][name]['status'] for name in names] == ['not_run', 'completed', 'not_run']
    cached = latest['reviews'][names[0]]
    assert cached['duration_seconds'] is None and cached['findings'] == [] and cached['artifacts'] == []
    assert not git('status', '--porcelain')


@pytest.mark.parametrize('change', ['scope', 'rules', 'preparation', 'reviewers'])
def test_resume_rejects_changed_evidence_before_provider_execution(pinned, change):
    context, decisions, prep, names, git = pinned
    session = Session(context, prep, names)
    kwargs = {'path': session.path, 'resume': True}
    if change in {'scope', 'rules'}:
        kwargs[change] = 'new request boundary'
    elif change == 'preparation':
        prep['analysis']['worker_instructions'] += ' Inspect the changed boundary.'
        prepare_current(context, decisions, prep)
    else:
        names = names[:1]
    with pytest.raises(ValueError, match='changed evidence'):
        Session(context, prep, names, **kwargs)


def test_session_guard_rejects_overwrite_parallel_execution_and_checkpoint_tampering(pinned):
    context, decisions, prep, names, git = pinned
    session = Session(context, prep, names)
    with pytest.raises(ValueError, match='already exists'):
        Session(context, prep, names, path=session.path)
    other = Session(context, prep, names, path=session.path, resume=True)
    with session.execution():
        with pytest.raises(ValueError, match='already executing'):
            with other.execution():
                pytest.fail('second coordinator acquired session')
        session.record(names[0], 'completed', {'findings': []})
    data = json.loads(session.path.read_text())
    checkpoint = Path(data['reviews'][names[0]]['checkpoint']['path'])
    checkpoint.write_text('{}')
    with pytest.raises(ValueError, match='checkpoint changed'):
        session.cached(names[0])


def test_sessions_stay_outside_reviewed_repository(pinned):
    context, decisions, prep, names, git = pinned
    with pytest.raises(ValueError, match='outside'):
        Session(context, prep, names, path=Path(context['checkout']) / 'session.json')
    with pytest.raises(ValueError, match='session-file'):
        Session(context, prep, names, resume=True)


@pytest.mark.parametrize('changed', [False, 'context', 'catalog', 'provider', 'default'])
def test_resumed_profile_checkpoint_requires_same_effective_settings(pinned, tmp_path, monkeypatch, changed):
    from shared import bindings, codex_profiles
    from shared.runtime_bundles import retained_binding
    context, decisions, prep, names, git = pinned
    monkeypatch.setenv('DK_HOST', 'codex')
    root = tmp_path / 'native-provider'
    (root / '.codex-plugin').mkdir(parents=True)
    (root / 'skills/review').mkdir(parents=True)
    procedure = root / 'skills/review/SKILL.md'
    procedure.write_text('Review pinned changes read-only.')
    (root / '.codex-plugin/plugin.json').write_text(json.dumps({
        'name': 'provider', 'version': '1.0.0', 'skills': './skills/'}))
    binding = {'skill': 'provider:review', 'host': 'codex', 'codex_profile': 'review'}
    selected = {'profile': 'review', 'settings': {'model': 'fixture-model',
                'model_reasoning_effort': 'high', 'model_context_window': 10000}}
    # Configuration parsing/native transport have independent offline coverage;
    # exercise the real retained-binding, preparation and checkpoint gates here.
    monkeypatch.setattr(codex_profiles, 'resolve_profile', lambda *args: dict(selected))
    execution = retained_binding({'binding': binding, 'identity': bindings.key(binding),
        'executor': 'codex', 'root': str(root), 'procedure': str(procedure),
        'version': '1.0.0', 'codex_execution': json.loads(json.dumps(selected))})
    old = names[0]
    names[0] = bindings.key(binding)
    descriptor = prep['reviewers'].pop(old)
    prep['reviewers'][names[0]] = {**descriptor, 'reviewer': names[0], 'execution': execution,
        'effective_prompt': json.dumps(execution, sort_keys=True) + '\nReview the pinned change.'}
    prep['reviewers'] = {name: prep['reviewers'][name] for name in names}
    prep['analysis']['reviewer_addenda'][names[0]] = prep['analysis']['reviewer_addenda'].pop(old)
    prepare_current(context, decisions, prep)
    session = Session(context, prep, names)
    session.record(names[0], 'completed', {'findings': []})
    checkpoint = Path(json.loads(session.path.read_text())['reviews'][names[0]]['checkpoint']['path'])
    before = checkpoint.read_bytes()
    resumed = Session(context, prep, names, path=session.path, resume=True)
    if changed:
        if changed == 'context':
            selected['settings'] = {**selected['settings'], 'model_context_window': 20000}
        elif changed == 'catalog':
            selected['catalog'] = {'sha256': 'changed-active-catalog'}
        elif changed == 'provider':
            selected['provider'] = {'definition_sha256': 'changed-selected-provider'}
        else:
            selected['effective_settings'] = {'model': 'changed-native-default'}
        with resumed.execution(), patch.object(runner, 'run_skill_reviewer') as provider:
            with pytest.raises(ValueError, match='execution settings changed'):
                runner.review(context, [binding, names[1]], '', decisions, '', prep, session=resumed)
        provider.assert_not_called()
        assert resumed.cached(names[0]) == {'findings': []}
        assert checkpoint.read_bytes() == before
    else:
        with resumed.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as provider:
            result = runner.review(context, [binding, names[1]], '', decisions, '', prep, session=resumed)
        assert result['complete'] and result['reused_reviewers'] == names[:1]
        assert provider.call_count == 1 and provider.call_args.args[0] == names[1]
