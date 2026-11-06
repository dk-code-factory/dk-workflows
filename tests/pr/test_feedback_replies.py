"""Reply payload and recovery scenarios, independent of native source selection."""
import importlib.util
from pathlib import Path

import pytest
from shared import publication

spec = importlib.util.spec_from_file_location(
    'feedback_reply_exercise', Path(__file__).with_name('fixtures')/'feedback_reply_exercise.py')
exercise = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exercise)


@pytest.mark.parametrize('source_kind', ['inline_comment', 'review', 'issue_comment'])
def test_reply_destination_attribution_and_exact_source_survive_resume(tmp_path, source_kind):
    root = tmp_path/'exercise'
    exercise.initialize(root)
    pr, head = exercise.fixture.PR, exercise.fixture.HEAD
    source_id = {'inline_comment': 99, 'review': 789, 'issue_comment': 901}[source_kind]
    fragment = {'inline_comment': 'discussion_r', 'review': 'pullrequestreview-',
                'issue_comment': 'issuecomment-'}[source_kind]
    source = {'kind': source_kind, 'id': source_id, 'url': pr+'#'+fragment+str(source_id)}
    body = 'Empty input now returns an empty list; the targeted check passes.'
    request = {'kind': 'reply-comment', 'pr': pr, 'head': head, 'source': source}
    if source_kind == 'inline_comment':
        source['thread_id'] = 'PRRT_fixture'
        request.update(kind='reply-inline', parent=41)
    else:
        body = 'In Reply To: [Original feedback]('+source['url']+')\n\n'+body
    request['body'] = body
    result = exercise.operate(root, 'submit', request, lose_response=True)
    operation = result['operation']
    saved = publication.load(operation)
    assert saved['status'] == 'pending'
    assert saved['request']['approved']['source'] == source
    assert saved['request']['approved']['body'] == body
    # Recover the same retained intent even when the previous caller lost its
    # operation path, then reconcile without a second dispatch.
    recovered = exercise.operate(root, 'submit', request)
    assert recovered['operation'] == operation
    assert exercise.operate(root, 'reconcile', operation=operation)['status'] == 'verified'
    state = exercise.operate(root, 'inspect')
    assert state['writes'] == {request['kind']: 1}
    if source_kind == 'inline_comment':
        assert len(state['replies']) == 3
        reply = state['replies'][-1]
        assert reply['in_reply_to_id'] == 41
        assert not state['issue_comments']
        assert saved['request']['approved']['parent'] == 41
    else:
        assert len(state['replies']) == 2
        assert len(state['issue_comments']) == 1
        reply = state['issue_comments'][0]
        assert reply['body'].startswith('In Reply To: [Original feedback]('+source['url']+')\n\n')
    assert reply['body'] == body
    assert reply['user']['login'] == 'author'


@pytest.mark.parametrize('state', ['unavailable', 'edited'])
def test_recovery_exercise_uses_real_journal_and_separate_obligation_disposition(tmp_path, state):
    root = tmp_path/'exercise'
    exercise.initialize(root)
    pr, head = exercise.fixture.PR, exercise.fixture.HEAD
    source = {'kind': 'review', 'id': 789, 'url': pr+'#pullrequestreview-789'}
    request = {'kind': 'reply-comment', 'pr': pr, 'head': head, 'source': source,
               'body': 'In Reply To: [Original review]('+source['url']+')\n\nFixed.'}
    operation = exercise.operate(root, 'submit', request)['operation']
    receipt = publication.load(operation)['receipt']
    exercise.operate(root, 'change-reply', {'id': receipt['id'], 'state': state, 'body': 'Edited answer'})
    observed = exercise.operate(root, 'inspect-reply', operation=operation)
    assert observed['snapshot']['reply_state'] == state
    exercise.operate(root, 'dispose-reply', {'decision': 'no_replacement', 'reason': 'The question is obsolete.',
                                           'observation': observed['fingerprint']}, operation=operation)
    assert exercise.operate(root, 'reconcile', operation=operation)['reply_obligation'] == 'no_replacement'
    assert exercise.operate(root, 'inspect')['writes'] == {'reply-comment': 1}
    assert publication.load(operation)['receipt'] == receipt


@pytest.mark.parametrize('state', ['unavailable', 'edited'])
@pytest.mark.parametrize('source_kind', ['review', 'inline_comment'])
@pytest.mark.parametrize('lose_response', [False, True])
def test_recovery_exercise_required_replacement_handles_response_outcomes_without_id_reuse(tmp_path, state, source_kind, lose_response):
    root = tmp_path/'exercise'
    exercise.initialize(root)
    pr, head = exercise.fixture.PR, exercise.fixture.HEAD
    source = {'kind': source_kind, 'id': 789, 'url': pr+'#pullrequestreview-789'}
    request = {'kind': 'reply-comment', 'pr': pr, 'head': head, 'source': source,
               'body': 'In Reply To: [Original review]('+source['url']+')\n\nOriginal answer.'}
    if source_kind == 'inline_comment':
        source.update(id=99, url=pr+'#discussion_r99', thread_id='PRRT_fixture')
        request.update(kind='reply-inline', parent=41, body='Original inline answer.')
    operation = exercise.operate(root, 'submit', request)['operation']
    original = publication.load(operation)
    exercise.operate(root, 'change-reply', {'id': original['receipt']['id'], 'state': state, 'body': 'Edited answer'})
    observed = exercise.operate(root, 'inspect-reply', operation=operation)
    exercise.operate(root, 'dispose-reply', {'decision': 'replacement_required', 'reason': 'A current answer is required.',
                                           'observation': observed['fingerprint']}, operation=operation)
    replacement_request = {**request, 'body': request['body']+' Current correction.', 'replaces': operation,
                           'authorization': 'User authorized this concrete current answer.'}
    result = exercise.operate(root, 'submit', replacement_request, lose_response=lose_response)
    replacement = result['operation']
    if not lose_response:
        assert result['result']['status'] == 'verified'
    assert publication.load(replacement)['status'] == ('pending' if lose_response else 'verified')
    assert exercise.operate(root, 'submit', replacement_request)['operation'] == replacement
    assert exercise.operate(root, 'reconcile', operation=replacement)['status'] == 'verified'
    for _ in range(2):
        result = exercise.operate(root, 'reconcile', operation=operation)
        assert result['reply_obligation'] == 'replacement_required'
        assert result['replacement_operation'] == replacement
    retained = publication.load(operation)
    assert retained['request'] == original['request']
    assert retained['response'] == original['response']
    assert retained['receipt'] == original['receipt']
    assert publication.load(replacement)['receipt']['id'] != original['receipt']['id']
    assert exercise.operate(root, 'inspect')['writes'] == {request['kind']: 2}
