"""Uncertain re-review writes retain history without starving independent replies."""
import importlib.util
from pathlib import Path
import subprocess

import pytest
from shared import publication
from shared.diagnostics import Diagnostic
from shared.private_state import write

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


fixture = module('review_recovery_fixture', ROOT/'tests/pr/fixtures/github_remote.py')


@pytest.fixture
def setup(monkeypatch):
    publisher = module('review_recovery_publisher', ROOT/'scripts/publish.py')
    remote = fixture.Remote()
    for key in ('metadata', 'api', 'run'):
        monkeypatch.setattr(publisher, key, getattr(remote, key))
    return publisher, remote


def pending(publisher, remote):
    request = fixture.request('request-review')
    operation = publisher.prepare(request)['operation']
    remote.state['lose_response'] = ['request-review']
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    remote.state['requested'] = {'users': [], 'teams': []}
    return request, operation


@pytest.mark.parametrize('kind', ['reply-inline', 'reply-comment'])
def test_uncertain_review_request_allows_independent_reply(setup, kind):
    publisher, remote = setup
    request, operation = pending(publisher, remote)
    retained = publication.load(operation)
    reply = {'kind': kind, 'pr': fixture.PR, 'head': fixture.HEAD,
             'parent': 41, 'body': 'A separately authorized answer'}
    reply_operation = publisher.prepare(reply)['operation']
    assert publisher.publish(reply_operation)['status'] == 'verified'
    assert publication.load(operation) == retained
    assert publisher.prepare(request)['operation'] == operation
    with pytest.raises(ValueError, match='Uncertain publication'):
        publisher.prepare({**request, 'action_id': 'another-id'})
    with pytest.raises(Diagnostic) as failure:
        publisher.publish(operation, reconcile=True)
    assert 'inspect-review-request' in failure.value.action['argv']
    assert remote.state['writes'] == {'request-review': 1, kind: 1}


def test_dispatch_guard_covers_reply_prepared_before_uncertainty(setup):
    publisher, remote = setup
    reply = publisher.prepare({'kind': 'reply-comment', 'pr': fixture.PR, 'head': fixture.HEAD,
                               'body': 'Independent'})['operation']
    pending(publisher, remote)
    assert publisher.publish(reply)['status'] == 'verified'


def test_other_uncertain_writes_still_block_replies(setup):
    publisher, remote = setup
    operation = publisher.prepare({'kind': 'edit-pr', 'pr': fixture.PR, 'head': fixture.HEAD,
                                   'fields': {'body': 'changed'}})['operation']
    remote.state['lose_response'] = ['edit-pr']
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    with pytest.raises(ValueError, match='Uncertain publication'):
        publisher.prepare({'kind': 'reply-comment', 'pr': fixture.PR, 'head': fixture.HEAD, 'body': 'Answer'})


def test_legacy_history_inspection_disposition_and_fresh_session_reuse(setup):
    publisher, remote = setup
    request, operation = pending(publisher, remote)
    value = publication.load(operation)
    for key in ('origin', 'created_at', 'dispatch_started_at'):
        value.pop(key)
    value['failure'] = {'stderr': 'unexpected end of JSON input', 'stdout': '', 'response': None}
    write(operation, value)
    original = publication.load(operation)
    remote.state['timeline'] = [
        {'id': 1, 'event': 'review_requested', 'requested_reviewer': {'login': 'reviewer'}, 'created_at': '2026-11-01T00:00:00Z'},
        {'id': 2, 'event': 'review_request_removed', 'requested_team': {'slug': 'team'}, 'created_at': '2026-11-02T00:00:00Z'}]
    remote.state['reviews'] = [{'id': 3, 'user': {'login': 'reviewer'}, 'state': 'APPROVED',
                                'submitted_at': '2026-11-02T00:00:00Z', 'commit_id': fixture.HEAD}]
    inspected = publisher.review_recovery().inspect(operation)
    assert inspected['historical_origin'] == inspected['created_at'] == 'unknown'
    assert inspected['delivery'] == inspected['root_cause'] == 'unknown'
    assert len(inspected['current_observation']['events']) == 2
    assert len(inspected['current_observation']['reviews']) == 1
    stable = Path(operation).read_bytes()
    assert publisher.review_recovery().inspect(operation) == inspected
    assert Path(operation).read_bytes() == stable
    decision = {'decision': 'no_resend', 'reason': 'The reviewer completed review; no further request is needed.',
                'observation': inspected['fingerprint']}
    publisher.review_recovery().dispose(operation, decision)
    stable = Path(operation).read_bytes()
    publisher.review_recovery().dispose(operation, decision)
    assert Path(operation).read_bytes() == stable
    fresh = module('fresh_review_recovery_publisher', ROOT/'scripts/publish.py')
    for key in ('metadata', 'api', 'run'):
        setattr(fresh, key, getattr(remote, key))
    assert fresh.publish(operation, reconcile=True)['review_obligation'] == 'no_resend'
    assert fresh.prepare(request)['operation'] == operation
    with pytest.raises(ValueError, match='Uncertain publication'):
        fresh.prepare({**request, 'action_id': 'evade-duplicate'})
    saved = publication.load(operation)
    assert all(saved[key] == original[key] for key in ('request', 'request_hash', 'intent', 'failure', 'status'))
    assert 'receipt' not in saved
    assert remote.state['writes'] == {'request-review': 1}
    # A settled no-resend decision allows other gated actions, but becomes stale
    # when the PR/reviewer/history evidence changes.
    assert fresh.prepare({'kind': 'edit-pr', 'pr': fixture.PR, 'head': fixture.HEAD,
                          'fields': {'title': 'Current reviewed title'}})['status'] == 'prepared'
    remote.state['metadata']['headRefOid'] = 'b'*40
    with pytest.raises(ValueError, match='Inspect and reassess'):
        fresh.publish(operation, reconcile=True)
    with pytest.raises(ValueError, match='changed'):
        fresh.review_recovery().dispose(operation, decision)
    assert remote.state['writes'] == {'request-review': 1}


def test_transport_diagnostics_preserve_original_failure_and_safe_payload(setup, monkeypatch):
    publisher, remote = setup
    monkeypatch.setenv('CODEX_THREAD_ID', 'origin-session')
    request = fixture.request('request-review')
    operation = publisher.prepare(request)['operation']
    original_run = publisher.run
    def broken(argv, **kwargs):
        if '--method' in argv:
            raise subprocess.CalledProcessError(1, argv, output='Authorization: secret',
                stderr='unexpected end of JSON input\nAuthorization: Bearer secret\nHTTP 502')
        return original_run(argv, **kwargs)
    monkeypatch.setattr(publisher, 'run', broken)
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    saved = publication.load(operation)
    failure = saved['failure']
    assert failure['phase'] == 'send' and failure['exit_status'] == 1 and failure['http_status'] == 502
    assert 'secret' not in repr(failure)
    assert saved['origin'] == {'CODEX_THREAD_ID': 'origin-session'}
    assert saved['created_at'] <= saved['dispatch_started_at']
    assert publisher.review_recovery().inspect(operation)['request_payload'] == {
        'reviewers': request['reviewers'], 'team_reviewers': request['team_reviewers']}
    for _ in range(2):
        with pytest.raises(Diagnostic):
            publisher.publish(operation, reconcile=True)
    resumed = publication.load(operation)
    assert resumed['failure'] == failure
    assert resumed['reconciliation_failure']['phase'] == 'reconcile'
    assert remote.state['writes'] == {}


def test_changed_or_unavailable_history_cannot_admit_disposition(setup, monkeypatch):
    publisher, remote = setup
    _, operation = pending(publisher, remote)
    inspected = publisher.review_recovery().inspect(operation)
    decision = {'decision': 'no_resend', 'reason': 'Reassessed', 'observation': inspected['fingerprint']}
    remote.state['requested']['users'] = [{'login': 'reviewer'}]
    with pytest.raises(ValueError, match='changed'):
        publisher.review_recovery().dispose(operation, decision)
    def unavailable(*args, **kwargs):
        raise OSError('read access unavailable')
    monkeypatch.setattr(publisher, 'run', unavailable)
    with pytest.raises(OSError):
        publisher.review_recovery().dispose(operation, decision)
    assert 'review_request_disposition' not in publication.load(operation)
