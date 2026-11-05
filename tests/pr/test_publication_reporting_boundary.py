"""Exercise publication failures without publishing operational follow-ups.

Requests here are explicitly authored substantive payloads. Semantic selection
from reviewer/verification failures is exercised separately with native agents.
"""
import importlib.util
from pathlib import Path

import pytest

from shared import publication
from shared.diagnostics import Diagnostic


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publisher = load('private_reporting_publisher', ROOT/'scripts/publish.py')
fixture = load('private_reporting_remote', Path(__file__).with_name('fixtures')/'github_remote.py')


@pytest.fixture
def remote(monkeypatch):
    value = fixture.Remote()
    for key in ('api', 'metadata', 'run'):
        monkeypatch.setattr(publisher, key, getattr(value, key))
    return value


def review_request():
    # The two supported findings are independent of the unavailable review
    # coverage. This is the exact payload selected by a conforming caller.
    return {'kind': 'post-review', 'pr': fixture.PR, 'head': fixture.HEAD,
            'event': 'COMMENT', 'body': '', 'comments': [
                {'path': 'code.py', 'line': 1, 'side': 'RIGHT',
                 'body': 'Empty input divides by zero before the guard executes.'},
                {'path': 'code.py', 'line': 1, 'side': 'RIGHT',
                 'body': 'The last page is discarded when its cursor is empty.'}]}


def test_findings_only_review_does_not_require_an_operational_summary(remote):
    request = review_request()
    operation = publisher.prepare(request)['operation']
    result = publisher.publish(operation)
    assert result['status'] == 'verified'
    assert remote.state['reviews'][0]['body'] == ''
    assert remote.state['reviews'][0]['state'] == 'COMMENTED'
    assert [v['body'] for v in remote.state['review_comments']['100']] == [
        v['body'] for v in request['comments']]
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('kind', ['post-review', 'reply-inline', 'reply-comment', 'edit-pr'])
def test_unavailable_publication_evidence_is_returned_privately_without_a_diagnostic_reply(remote, monkeypatch, kind):
    request = review_request() if kind == 'post-review' else {
        'kind': kind, 'pr': fixture.PR, 'head': fixture.HEAD,
        'body': 'Empty input now returns an empty list.', 'parent': 41,
        'fields': {'body': 'Handle empty input.'}}
    diagnostic = 'INTERNAL: supporting source unavailable; publication verification incomplete'
    monkeypatch.setattr(publisher, 'metadata', lambda pr: (_ for _ in ()).throw(ValueError(diagnostic)))
    with pytest.raises(ValueError, match=diagnostic):
        publisher.prepare(request)
    assert not remote.state['writes']
    assert not remote.state['reviews']
    assert not remote.state['issue_comments']
    assert not remote.state['replies']


@pytest.mark.parametrize('kind', ['post-review', 'reply-inline', 'reply-comment'])
def test_uncertain_publication_keeps_diagnostics_private_and_original_payload_durable(remote, kind):
    request = review_request() if kind == 'post-review' else {
        'kind': kind, 'pr': fixture.PR, 'head': fixture.HEAD, 'parent': 41,
        'body': 'Empty input now returns an empty list.'}
    operation = publisher.prepare(request)['operation']
    remote.state['lose_response'].append(kind)
    with pytest.raises(Diagnostic) as error:
        publisher.publish(operation)
    # This envelope is returned to the caller; it is never published as a new
    # review/comment. Preserve it for the invoking user's recovery report.
    private_report = error.value.envelope()
    assert private_report['status'] == 'blocked'
    assert private_report['diagnostic']['code'] == 'publication_uncertain'
    assert private_report['diagnostic']['dispatched'] is True
    assert operation in private_report['diagnostic']['action']['artifacts']
    saved = publication.load(operation)
    assert saved['status'] == 'pending'
    assert saved['request']['approved']['body'] == request['body']
    if kind == 'post-review':
        assert saved['request']['approved']['comments'] == request['comments']
        assert remote.state['reviews'][0]['body'] == ''
    elif kind == 'reply-inline':
        assert remote.state['replies'][0]['body'] == request['body']
    else:
        assert remote.state['issue_comments'][0]['body'] == request['body']
    assert remote.state['writes'] == {kind: 1}
    assert publisher.publish(operation, reconcile=True)['status'] == 'verified'
    assert remote.state['writes'] == {kind: 1}
    assert private_report['status'] == 'blocked'
    assert private_report['diagnostic']['code'] == 'publication_uncertain'
