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
KINDS=sorted(publisher.KINDS)

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
