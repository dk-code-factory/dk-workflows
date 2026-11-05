"""Concrete remote payloads and recovery, including CI observer receipt handoff."""
import importlib.util
from pathlib import Path
import pytest
from shared import publication
from shared.ci_observation import for_pr
from shared.diagnostics import Diagnostic

ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value
publisher=module('publisher',ROOT/'scripts/publish.py')
fixture=module('github_fixture',ROOT/'tests/pr/fixtures/github_remote.py')
KINDS=sorted(publisher.KINDS - {'ci-approve'})  # Evidence-backed approvals have a dedicated suite.

@pytest.fixture
def remote(monkeypatch):
    value=fixture.Remote()
    for key in ('metadata','api','run'):monkeypatch.setattr(publisher,key,getattr(value,key))
    return value

@pytest.mark.parametrize('kind',KINDS)
def test_lost_response_reconciles_same_intent_once(remote,kind):
    request=fixture.request(kind)
    operation=publisher.prepare(request)['operation']
    remote.state['lose_response']=[kind]
    with pytest.raises(Diagnostic):publisher.publish(operation)
    assert publication.load(operation)['status']=='pending'
    assert publisher.prepare(request)['operation']==operation
    for _ in range(2):
        assert publisher.publish(operation,reconcile=True)['status']=='verified'
    assert remote.state['writes']=={kind:1}

@pytest.mark.parametrize('kind',KINDS)
def test_stale_head_blocks_before_dispatch(remote,kind):
    operation=publisher.prepare(fixture.request(kind))['operation']
    remote.state['metadata']['headRefOid']='d'*40
    with pytest.raises(Diagnostic):publisher.publish(operation)
    assert remote.state['writes']=={}
    assert publication.load(operation)['status']=='prepared'


def test_ci_rerun_receipt_opens_one_new_window_and_resume_keeps_it(remote):
    request=fixture.request('ci-rerun')
    with for_pr(fixture.PR,fixture.HEAD,clock=lambda:10) as old:assert old.current['started']==10
    operation=publisher.prepare(request)['operation']
    publisher.publish(operation)
    with for_pr(fixture.PR,fixture.HEAD,rerun=operation,clock=lambda:20) as refreshed:
        assert refreshed.current['started']==20
    with for_pr(fixture.PR,fixture.HEAD,rerun=operation,clock=lambda:30) as resumed:
        assert resumed.current['started']==20
    assert remote.state['writes']['ci-rerun']==1


def test_advanced_rerun_remains_uncertain_without_resend(remote):
    operation=publisher.prepare(fixture.request('ci-rerun'))['operation']
    remote.state['lose_response']=['ci-rerun']
    with pytest.raises(Diagnostic):publisher.publish(operation)
    remote.state['run']['run_attempt']=3
    for _ in range(2):
        with pytest.raises(Diagnostic):publisher.publish(operation,reconcile=True)
    assert remote.state['writes']['ci-rerun']==1


def test_thread_answer_is_verified_and_paginated(remote):
    request=fixture.request('resolve-thread')
    remote.state['thread']['comments']=[{'id':str(i),'databaseId':i+1000,'body':'context','author':{'login':'reviewer'},'url':'url'} for i in range(102)]+remote.state['thread']['comments']
    operation=publisher.prepare(request)['operation']
    assert publisher.publish(operation)['receipt']['reply_id']==42


def test_thread_without_our_answer_cannot_be_resolved(remote):
    remote.state['thread']['comments'][0]['author']['login']='someone-else'
    with pytest.raises(ValueError,match='verified posted answer'):
        publisher.prepare(fixture.request('resolve-thread'))
    assert not remote.state['writes']


def test_unconfirmed_creation_never_creates_a_second_pr(remote):
    operation=publisher.prepare(fixture.request('create-draft-pr'))['operation']
    remote.state['lose_response']=['create-draft-pr']
    with pytest.raises(Diagnostic):publisher.publish(operation)
    remote.state['pulls'][0]['body']='Changed by another actor'
    with pytest.raises(Diagnostic):publisher.publish(operation,reconcile=True)
    assert remote.state['writes']['create-draft-pr']==1


def test_partition_target_and_diff_line_are_fresh_inputs(remote):
    request=fixture.request('create-draft-pr');request['base_branch']='nondefault'
    with pytest.raises(ValueError,match='default branch'):publisher.prepare(request)
    request=fixture.request('post-review');request['comments'][0]['line']=50
    with pytest.raises(ValueError,match='changed line'):publisher.prepare(request)
    assert not remote.state['writes']


def test_review_request_already_satisfied_does_not_send_again(remote):
    remote.state['requested']={'users':[{'login':'reviewer'}],'teams':[{'slug':'team'}]}
    operation=publisher.prepare(fixture.request('request-review'))['operation']
    assert publisher.publish(operation)['status']=='verified'
    assert not remote.state['writes']


@pytest.mark.parametrize('method', ['squash', 'merge'])
def test_merge_binds_reviewed_head_and_reconciles_exact_commit(remote, method):
    request = fixture.request('merge-pr')
    request['merge_method'] = method
    operation = publisher.prepare(request)['operation']
    result = publisher.publish(operation)
    assert result['receipt']['head'] == fixture.HEAD
    assert result['receipt']['base'] == 'b'*40
    assert result['receipt']['merge_method'] == method
    assert publisher.publish(operation)['status'] == 'verified'
    assert remote.state['writes'] == {'merge-pr': 1}


@pytest.mark.parametrize('decision', ['APPROVED', '', None])
@pytest.mark.parametrize('own_pr', [False, True])
def test_authorized_merge_accepts_own_pr_and_no_required_approval(remote, decision, own_pr):
    remote.state['merge_gate']['reviewDecision'] = decision
    if own_pr:
        remote.state['pr_author']['id'] = remote.state['actor_id']
    operation = publisher.prepare(fixture.request('merge-pr'))['operation']
    assert publisher.publish(operation)['status'] == 'verified'
    assert remote.state['writes'] == {'merge-pr': 1}


@pytest.mark.parametrize('decision', ['REVIEW_REQUIRED', 'CHANGES_REQUESTED', 'UNKNOWN', 'missing'])
def test_merge_rejects_unsatisfied_or_unknown_review_requirements(remote, decision):
    if decision == 'missing':
        del remote.state['merge_gate']['reviewDecision']
    else:
        remote.state['merge_gate']['reviewDecision'] = decision
    with pytest.raises(ValueError, match='review and merge requirements'):
        publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


def test_review_requirement_added_after_preparation_blocks_dispatch(remote):
    remote.state['merge_gate']['reviewDecision'] = ''
    operation = publisher.prepare(fixture.request('merge-pr'))['operation']
    remote.state['merge_gate']['reviewDecision'] = 'REVIEW_REQUIRED'
    with pytest.raises(Diagnostic): publisher.publish(operation)
    assert not remote.state['writes']
    assert publication.load(operation)['status'] == 'prepared'


@pytest.mark.parametrize('failure', ['pending-ci', 'failed-ci', 'missing-ci', 'unapproved', 'target',
                                     'draft', 'unknown-mergeability', 'rest-blocked', 'graphql-blocked'])
def test_merge_readiness_failures_block_all_writes(remote, failure):
    if failure == 'pending-ci': remote.state['checks'][0]['bucket'] = 'pending'
    if failure == 'failed-ci': remote.state['checks'][0]['bucket'] = 'fail'
    if failure == 'missing-ci': remote.state['checks'] = []
    if failure == 'unapproved': remote.state['merge_gate']['reviewDecision'] = 'REVIEW_REQUIRED'
    if failure == 'target': remote.state['branches']['main'] = 'd'*40
    if failure == 'draft': remote.state['pull']['draft'] = True
    if failure == 'unknown-mergeability': remote.state['pull']['mergeable'] = None
    if failure == 'rest-blocked':
        remote.state['merge_gate']['reviewDecision'] = ''
        remote.state['pull']['mergeable_state'] = 'blocked'
    if failure == 'graphql-blocked':
        remote.state['merge_gate'].update(reviewDecision=None, mergeStateStatus='BLOCKED')
    with pytest.raises(ValueError): publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


def test_merge_target_change_after_preparation_blocks_dispatch(remote):
    operation = publisher.prepare(fixture.request('merge-pr'))['operation']
    remote.state['branches']['main'] = 'd'*40
    with pytest.raises(Diagnostic): publisher.publish(operation)
    assert not remote.state['writes']
    assert publication.load(operation)['status'] == 'prepared'


@pytest.mark.parametrize('drift', ['actor', 'target', 'method'])
def test_unexpected_merge_receipt_remains_uncertain_and_never_resends(remote, drift):
    operation = publisher.prepare(fixture.request('merge-pr'))['operation']
    remote.state['lose_response'] = ['merge-pr']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    if drift == 'actor': remote.state['pull']['merged_by']['id'] = 99
    if drift == 'target': remote.state['merge_commit']['parents'][0]['sha'] = 'd'*40
    if drift == 'method': remote.state['merge_commit']['parents'].append({'sha': fixture.HEAD})
    for _ in range(2):
        with pytest.raises(Diagnostic): publisher.publish(operation, reconcile=True)
    assert remote.state['writes'] == {'merge-pr': 1}
    assert publication.load(operation)['status'] == 'pending'


def test_self_approval_is_blocked_and_actor_drift_blocks_dispatch(remote):
    request = fixture.request('post-review')
    request['event'] = 'APPROVE'
    remote.state['pr_author']['id'] = remote.state['actor_id']
    with pytest.raises(ValueError, match='own PR'): publisher.prepare(request)
    remote.state['pr_author']['id'] = 2
    operation = publisher.prepare(request)['operation']
    remote.state['actor_id'] = 3
    with pytest.raises(Diagnostic): publisher.publish(operation)
    assert not remote.state['writes']


@pytest.fixture
def review_transport(remote, monkeypatch):
    """Vary list and individual evidence independently, as GitHub does."""
    controls = {'list': lambda values: values, 'detail': lambda value: value, 'reads': []}
    original = remote.api

    def api(pr, path, **kwargs):
        value = original(pr, path, **kwargs)
        if '/reviews/' in path and path.split('?')[0].endswith('/comments'):
            return controls['list'](value)
        if '/pulls/comments/' in path:
            controls['reads'].append(path)
            return controls['detail'](value)
        return value

    monkeypatch.setattr(remote, 'api', api)
    monkeypatch.setattr(publisher, 'api', api)
    return controls


@pytest.mark.parametrize('field', ['line', 'side', 'both'])
@pytest.mark.parametrize('missing', ['omitted', 'null'])
def test_review_list_missing_locations_uses_individual_comment(remote, review_transport, field, missing):
    keys = ('line', 'side') if field == 'both' else (field,)

    def incomplete(values):
        for value in values:
            for key in keys:
                if missing == 'null': value[key] = None
                else: value.pop(key)
        return values

    review_transport['list'] = incomplete
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    result = publisher.publish(operation)
    assert result['status'] == 'verified'
    assert result['receipt']['comments'] == [
        {'id': 200, 'path': 'code.py', 'line': 1, 'side': 'RIGHT', 'body': 'Consider the value.'}]
    assert review_transport['reads'] == ['repos/owner/repo/pulls/comments/200']
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('change', [
    {'id': 201}, {'pull_request_review_id': 101},
    {'pull_request_url': 'https://api.github.com/repos/owner/repo/pulls/8'},
    {'user': {'login': 'someone-else'}}, {'original_commit_id': 'd'*40},
    {'path': 'other.py'}, {'body': 'Edited'}, {'line': 2}, {'side': 'LEFT'},
    {'original_line': 2}, {'original_line': True}, {'original_line': 0},
    {'line': None, 'original_line': None}, {'side': None},
    {'in_reply_to_id': 199}, {'start_line': 1}, {'original_start_line': 1},
])
def test_review_conflicting_or_incomplete_individual_evidence_stays_pending(
        remote, review_transport, change):
    review_transport['detail'] = lambda value: {**value, **change}
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    for _ in range(2):
        with pytest.raises(Diagnostic): publisher.publish(operation, reconcile=True)
    assert publication.load(operation)['status'] == 'pending'
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('field', ['in_reply_to_id', 'start_line', 'original_start_line'])
def test_review_conflicting_list_identity_evidence_stays_pending(remote, review_transport, field):
    review_transport['list'] = lambda values: [{**value, field: 1} for value in values]
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    pending = publication.load(operation)
    with pytest.raises(Diagnostic): publisher.publish(operation, reconcile=True)
    retained = publication.load(operation)
    assert retained['status'] == 'pending'
    assert retained['intent'] == pending['intent']
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('failure', ['unavailable', 'extra', 'missing', 'duplicate', 'ambiguous-review'])
def test_review_unavailable_or_ambiguous_evidence_never_resends(remote, review_transport, failure):
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    remote.state['lose_response'] = ['post-review']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    if failure == 'unavailable':
        def unavailable(value): raise OSError('Individual comment unavailable')
        review_transport['detail'] = unavailable
    elif failure == 'ambiguous-review':
        remote.state['reviews'].append({**remote.state['reviews'][0], 'id': 101})
    else:
        comments = remote.state['review_comments']['100']
        if failure == 'missing': comments.clear()
        elif failure == 'extra': comments.append({**comments[0], 'id': 201})
        else: comments.append(dict(comments[0]))
    with pytest.raises(Diagnostic): publisher.publish(operation, reconcile=True)
    assert publication.load(operation)['status'] == 'pending'
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('current_line', [None, 12])
def test_pending_review_recovers_original_location_after_head_changes(remote, current_line):
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    remote.state['lose_response'] = ['post-review']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    pending = publication.load(operation)
    remote.state['metadata']['headRefOid'] = 'd'*40
    remote.state['review_comments']['100'][0].update(line=current_line, commit_id='d'*40)
    for _ in range(2):
        with pytest.raises(Diagnostic) as exc:
            publisher.publish(operation, reconcile=True)
        assert exc.value.code == 'publication_not_ready'
        verified = publication.load(operation)
        assert verified['status'] == 'verified'
        assert verified['receipt']['comments'][0]['line'] == 1
        for key in ('request', 'request_hash', 'operation_id', 'intent', 'failure'):
            assert verified.get(key) == pending.get(key)
    # The historical receipt releases the pending-target block, while a new
    # publication must independently pass live head validation.
    request = fixture.request('post-review')
    request.update(head='d'*40, body='A new authorized review')
    new_operation = publisher.prepare(request)['operation']
    assert new_operation != operation
    assert publication.load(new_operation)['status'] == 'prepared'
    assert remote.state['writes'] == {'post-review': 1}
    result = publisher.publish(new_operation)
    assert result['status'] == 'verified'
    assert result['receipt']['comments'][0]['id'] != verified['receipt']['comments'][0]['id']
    assert publication.load(operation)['receipt'] == verified['receipt']
    assert remote.state['writes'] == {'post-review': 2}


def test_pending_list_only_verification_failure_recovers_without_changing_intent(remote, monkeypatch):
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    observe = publisher.Handler.observe

    def old_observe(self, expected):
        raise ValueError('Published review comments differ from the prepared payload')

    monkeypatch.setattr(publisher.Handler, 'observe', old_observe)
    with pytest.raises(Diagnostic): publisher.publish(operation)
    pending = publication.load(operation)
    assert pending['status'] == 'pending'
    monkeypatch.setattr(publisher.Handler, 'observe', observe)
    assert publisher.publish(operation, reconcile=True)['status'] == 'verified'
    verified = publication.load(operation)
    for key in ('request', 'request_hash', 'operation_id', 'intent', 'response'):
        assert verified[key] == pending[key]
    assert remote.state['writes'] == {'post-review': 1}


def test_review_current_location_requires_original_commit_and_all_comments_are_verified(remote, review_transport):
    request = fixture.request('post-review')
    request['comments'].append({**request['comments'][0], 'body': 'Second finding'})
    review_transport['detail'] = lambda value: {k: v for k, v in value.items() if k != 'original_line'}
    review_transport['list'] = lambda values: [
        {k: v for k, v in value.items() if k != 'original_line'} for value in reversed(values)]
    operation = publisher.prepare(request)['operation']
    assert publisher.publish(operation)['status'] == 'verified'
    assert len(review_transport['reads']) == 2
    assert remote.state['writes'] == {'post-review': 1}
