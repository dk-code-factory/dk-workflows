import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_stats_capture import pinned as original_pinned, finding, runner
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
