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


@pytest.mark.parametrize('failure', ['pending-ci', 'missing-ci', 'unapproved', 'target',
                                     'draft', 'unknown-mergeability', 'own-pr'])
def test_merge_readiness_failures_block_all_writes(remote, failure):
    if failure == 'pending-ci': remote.state['checks'][0]['bucket'] = 'pending'
    if failure == 'missing-ci': remote.state['checks'] = []
    if failure == 'unapproved': remote.state['merge_gate']['reviewDecision'] = 'REVIEW_REQUIRED'
    if failure == 'target': remote.state['branches']['main'] = 'd'*40
    if failure == 'draft': remote.state['pull']['draft'] = True
    if failure == 'unknown-mergeability': remote.state['pull']['mergeable'] = None
    if failure == 'own-pr': remote.state['pr_author']['id'] = remote.state['actor_id']
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
