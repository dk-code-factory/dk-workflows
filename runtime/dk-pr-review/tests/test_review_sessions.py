import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from test_stats_capture import pinned as original_pinned, finding, runner
from stats_capture import api
from shared.review_sessions import Session, result_artifact
from shared import review_coverage


@pytest.fixture
def pinned(original_pinned):
    context, decisions, prep, names, git = original_pinned
    for key, value in (('context', context), ('decisions', decisions)):
        path = Path(context['patch']).parent / (key + '.json')
        path.write_text(json.dumps(value))
        prep['sources'][key] = {'path': str(path), 'sha256': runner.preparation_api.file_hash(path)}
    prep['preparation_id'] = runner.preparation_api.digest({k: v for k, v in prep.items() if k != 'preparation_id'})
    return context, decisions, prep, names, git


@pytest.mark.parametrize('mode', ['standalone', 'iteration'])
def test_interrupted_review_reuses_completed_provider_without_analytics(pinned, mode):
    context, decisions, prep, names, git = pinned
    prep['mode'] = mode
    prep['preparation_id'] = runner.preparation_api.digest({k: v for k, v in prep.items() if k != 'preparation_id'})
    path = Path(context['patch']).parent / 'session.json'
    session = Session(context, prep, names, path=path)
    with session.execution(), patch.object(runner, 'run_skill_reviewer',
            side_effect=[{'findings': [finding()]}, RuntimeError('provider unavailable')]):
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


def test_resumed_main_preserves_statistics_comparison_cohort(pinned, monkeypatch, capsys):
    context, decisions, prep, names, git = pinned
    third = 'dk-pr-read-comments'
    prep['reviewers'][third] = {**prep['reviewers'][names[0]], 'reviewer': third}
    prep['analysis']['reviewer_addenda'][third] = prep['analysis']['reviewer_addenda'][names[0]]
    names.append(third)
    prep['preparation_id'] = runner.preparation_api.digest({k: v for k, v in prep.items() if k != 'preparation_id'})
    directory = Path(context['patch']).parent
    preparation_path = directory / 'preparation.json'
    preparation_path.write_text(json.dumps(prep))
    session_path = directory / 'session.json'
    argv = ['run_reviewers.py', '--context', str(directory / 'context.json'),
            '--decisions-file', str(directory / 'decisions.json'),
            '--preparation-file', str(preparation_path), '--session-file', str(session_path),
            '--stats-cycle', '1']
    real_run = runner.run
    def run(args, cwd=None):
        if args[0] == 'python3':
            return json.dumps({'reviewers': names})
        return real_run(args, cwd)
    monkeypatch.setattr(runner, 'run', run)
    monkeypatch.setattr(sys, 'argv', argv)
    with patch.object(runner, 'run_skill_reviewer', side_effect=[
            {'findings': [finding()]}, RuntimeError('provider interrupted')]) as calls:
        runner.main()
    first = json.loads(capsys.readouterr().out)
    assert not first['complete'] and first['executed_reviewers'] == names[:1]
    assert [call.args[0] for call in calls.call_args_list] == names[:2]
    first_run = json.loads((directory / 'review-stats.json').read_text())['run_id']

    monkeypatch.setattr(sys, 'argv', [*argv, '--resume'])
    with patch.object(runner, 'run_skill_reviewer', side_effect=[
            {'findings': [finding()]}, {'findings': []}]) as calls:
        runner.main()
    resumed = json.loads(capsys.readouterr().out)
    assert resumed['complete'] and resumed['reused_reviewers'] == names[:1]
    assert resumed['executed_reviewers'] == names[1:]
    assert [call.args[0] for call in calls.call_args_list] == names[1:]
    assert resumed['findings'] == [{**finding(), 'reviewers': names[:2]}]
    assert resumed['review_session'] == first['review_session']
    assert len(json.loads(session_path.read_text())['results']) == 2

    sidecar = json.loads((directory / 'review-stats.json').read_text())
    assert sidecar['run_id'] != first_run
    runs = api.ledger(sidecar['repository'])['runs']
    initial, latest = runs[first_run], runs[sidecar['run_id']]
    assert [initial['reviews'][name]['status'] for name in names] == ['completed', 'failed', 'not_run']
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
    assert [latest['reviews'][name]['status'] for name in names] == ['not_run', 'completed', 'completed']
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
        prep = {**prep, 'preparation_id': 'different'}
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
