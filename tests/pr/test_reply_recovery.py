"""Historical reply recovery never silently repeats a remote mutation."""
from copy import deepcopy
import fcntl
import importlib.util
from pathlib import Path
import subprocess
import threading

import pytest
from shared import publication
from shared.diagnostics import Diagnostic

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('recovery_publisher', ROOT/'scripts/publish.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
read_replies = publisher.replies
read_handler = publisher.handler
PR = 'https://github.com/owner/repo/pull/7'


@pytest.fixture
def remote(monkeypatch):
    class Remote:
        def __init__(self):
            self.metadata = {'url': PR, 'headRefOid': 'a'*40, 'title': 'Title', 'body': 'Scope'}
            self.actor = 'author'
            self.source = {'id': 10, 'html_url': PR+'#issuecomment-10', 'body': 'Is this fixed?',
                           'user': {'login': 'reviewer'}}
            self.comments, self.writes = [], []
            self.failure, self.lose_response = None, False
            self.thread = {'id': 'T', 'comments': [{'databaseId': 10}]}

        def api(self, pr, path, *, payload=None):
            assert pr == PR
            if path == 'user':
                return {'login': self.actor}
            if payload is not None:
                value = {'id': 100+len(self.writes), 'body': payload['body'], 'user': {'login': self.actor},
                         'html_url': self.metadata['url']+'#issuecomment-'+str(100+len(self.writes))}
                self.comments.append(value)
                self.writes.append(deepcopy(value))
                if self.lose_response:
                    raise OSError('Response lost after write')
                return deepcopy(value)
            if path.endswith('/10'):
                return deepcopy(self.source)
            if self.failure:
                raise self.failure
            found = [c for c in self.comments if path.endswith('/'+str(c['id']))]
            if found:
                return deepcopy(found[0])
            raise subprocess.CalledProcessError(1, ['gh', 'api'], stderr='gh: Not Found (HTTP 404)')

        def request(self, **extra):
            return {'kind': 'reply-comment', 'pr': PR, 'head': self.metadata['headRefOid'],
                    'source': {'kind': 'issue_comment', 'id': 10, 'url': self.source['html_url']},
                    'body': 'In Reply To: [Question]('+self.source['html_url']+')\n\nFixed.', **extra}

        def post(self):
            request = self.request()
            operation = publisher.prepare(request)['operation']
            publisher.publish(operation)
            return operation

        def disposition(self, operation, decision='no_replacement'):
            observed = publisher.recovery().inspect(operation)
            payload = {'decision': decision, 'reason': 'Reassessed the original question and current code.',
                       'observation': observed['fingerprint']}
            return publisher.recovery().dispose(operation, payload)

    result = Remote()
    monkeypatch.setattr(publisher, 'metadata', lambda _: deepcopy(result.metadata))
    monkeypatch.setattr(publisher, 'api', result.api)
    monkeypatch.setattr(publisher, 'replies', lambda request, **kwargs: deepcopy(
        result.comments + ([result.source] if request.get('source', {}).get('kind') != 'review' else [])))
    return result


@pytest.fixture
def inline_operation(remote, monkeypatch, request):
    parent = getattr(request, 'param', 10)
    request = remote.request(kind='reply-inline', parent=parent,
                            source={'kind': 'inline_comment', 'id': 10, 'url': PR+'#discussion_r10', 'thread_id': 'T'})
    if parent != 10:
        remote.thread['comments'].insert(0, {'databaseId': parent})
        remote.comments.append({'id': parent})
        remote.source['in_reply_to_id'] = parent
    remote.source['html_url'] = request['source']['url']
    original_api = remote.api
    def api(pr, path, *, payload=None):
        if payload is not None:
            answer = original_api(pr, path, payload=payload)
            remote.comments[-1].update(in_reply_to_id=parent, html_url=PR+'#discussion_r'+str(answer['id']))
            return deepcopy(remote.comments[-1])
        return original_api(pr, path)
    monkeypatch.setattr(publisher, 'api', api)
    original_handler = publisher.handler
    class ThreadHandler:
        def thread(self):
            return deepcopy(remote.thread)
    monkeypatch.setattr(publisher, 'handler', lambda request: ThreadHandler()
                        if request.get('thread_id') == 'T' and request['kind'] == 'reply-inline'
                        else original_handler(request))
    operation = publisher.prepare(request)['operation']
    publisher.publish(operation)
    return operation


@pytest.mark.parametrize('state', ['unavailable', 'edited'])
def test_verified_reply_disposition_preserves_receipt_and_unblocks_repeated_reconciliation(remote, state):
    operation = remote.post()
    original = publication.load(operation)
    if state == 'unavailable':
        remote.comments.clear()
    else:
        remote.comments[0]['body'] = 'Edited answer; original obligation is obsolete.'
    for _ in range(2):
        with pytest.raises(Diagnostic) as error:
            publisher.publish(operation, reconcile=True)
        assert error.value.code == 'publication_not_ready'
        assert 'inspect-reply' in error.value.action['argv']
    observed = publisher.recovery().inspect(operation)
    assert observed['snapshot']['reply_state'] == state
    remote.disposition(operation)
    assert remote.disposition(operation)['reused']
    for _ in range(2):
        result = publisher.publish(operation, reconcile=True)
        assert result['reply_obligation'] == 'no_replacement'
        assert result['receipt'] == original['receipt']
    saved = publication.load(operation)
    assert saved['status'] == 'verified'
    assert saved['receipt'] == original['receipt']
    assert saved['response'] == original['response']
    assert saved['request'] == original['request']
    assert len(saved['reply_dispositions']) == 1
    assert len(remote.writes) == 1
    # A distinct authorized action remains preparable with the historical receipt retained.
    assert publisher.prepare(remote.request(body='A new answer', action_id='new-question'))['status'] == 'prepared'


@pytest.mark.parametrize('state', ['unavailable', 'edited'])
@pytest.mark.parametrize('source_kind,fragment', [('issue_comment', 'issuecomment-'), ('review', 'pullrequestreview-')])
def test_recovery_preserves_canonical_mixed_case_urls(remote, state, source_kind, fragment):
    canonical_pr = PR.replace('/owner/repo/', '/Owner/Repo/')
    remote.metadata['url'] = canonical_pr
    remote.source['html_url'] = canonical_pr+'#'+fragment+'10'
    request = remote.request(pr=canonical_pr)
    request['source']['kind'] = source_kind
    operation = publisher.prepare(request)['operation']
    publisher.publish(operation)
    original = publication.load(operation)
    if state == 'unavailable':
        remote.comments.clear()
    else:
        remote.comments[0]['body'] = 'The historical answer was edited.'
    remote.disposition(operation)
    assert publisher.publish(operation, reconcile=True)['reply_obligation'] == 'no_replacement'
    saved = publication.load(operation)
    assert saved['request'] == original['request']
    assert saved['receipt'] == original['receipt']
    assert saved['request']['approved']['source']['url'] == remote.source['html_url']
    assert len(remote.writes) == 1


@pytest.mark.parametrize('state', ['unavailable', 'edited'])
@pytest.mark.parametrize('thread_change', [None, 'pr', 'head', 'source'])
def test_inline_recovery_uses_canonical_thread_identity_without_relaxing_checks(remote, monkeypatch, state, thread_change):
    canonical_pr = PR.replace('/owner/repo/', '/Owner/Repo/')
    remote.metadata['url'] = canonical_pr
    remote.source['html_url'] = canonical_pr+'#discussion_r10'
    request = remote.request(pr=canonical_pr, kind='reply-inline', parent=10,
                             source={'kind': 'inline_comment', 'id': 10,
                                     'url': remote.source['html_url'], 'thread_id': 'T'})
    original_api = remote.api
    def api(pr, path, *, payload=None):
        result = original_api(pr, path, payload=payload)
        if payload is not None:
            remote.comments[-1].update(in_reply_to_id=10, html_url=canonical_pr+'#discussion_r100')
            return deepcopy(remote.comments[-1])
        return result
    monkeypatch.setattr(publisher, 'api', api)
    node = {'id': 'T', 'isResolved': False, 'viewerCanResolve': True,
            'pullRequest': {'url': canonical_pr, 'headRefOid': remote.metadata['headRefOid']},
            'comments': {'nodes': [{'id': 'source', 'databaseId': 10, 'url': remote.source['html_url'],
                                   'body': remote.source['body'], 'author': {'login': 'reviewer'}}],
                         'pageInfo': {'hasNextPage': False}}}
    def run(argv, **kwargs):
        assert argv[0:2] == ['gh', 'api'] and 'graphql' in argv
        assert kwargs['payload']['variables']['id'] == 'T'
        return {'data': {'node': deepcopy(node)}}
    monkeypatch.setattr(publisher, 'run', run)
    operation = publisher.prepare(request)['operation']
    publisher.publish(operation)
    original = publication.load(operation)
    assert original['request']['approved']['pr'] == PR
    if state == 'unavailable':
        remote.comments.clear()
    else:
        remote.comments[0]['body'] = 'The historical inline answer was edited.'
    if thread_change == 'pr':
        node['pullRequest']['url'] = canonical_pr.replace('/pull/7', '/pull/8')
    elif thread_change == 'head':
        node['pullRequest']['headRefOid'] = 'b'*40
    elif thread_change == 'source':
        node['comments']['nodes'].clear()
    if thread_change:
        message = 'exact original thread' if thread_change == 'source' else 'different PR or head'
        with pytest.raises(ValueError, match=message):
            remote.disposition(operation)
        assert not publication.load(operation).get('reply_dispositions')
    else:
        remote.disposition(operation)
        for _ in range(2):
            assert publisher.publish(operation, reconcile=True)['reply_obligation'] == 'no_replacement'
    saved = publication.load(operation)
    assert saved['request'] == original['request']
    assert saved['response'] == original['response']
    assert saved['receipt'] == original['receipt']
    assert len(remote.writes) == 1


def test_inspection_rejects_outside_storage_before_creating_a_lock(remote, tmp_path):
    operation = tmp_path/'outside-publication.json'
    with pytest.raises(ValueError, match='private workflow storage'):
        publisher.recovery().inspect(operation)
    assert not operation.exists()
    assert not operation.with_name(operation.name+'.lock').exists()


def test_required_replacement_is_separately_authorized_linked_and_sent_once(remote):
    operation = remote.post()
    remote.comments.clear()
    remote.disposition(operation, 'replacement_required')
    with pytest.raises(Diagnostic, match='still requires'):
        publisher.publish(operation, reconcile=True)
    request = remote.request(body='Replacement current answer', replaces=operation)
    with pytest.raises(ValueError, match='separate authorization'):
        publisher.prepare(request)
    request['authorization'] = 'User explicitly requested this replacement answer.'
    replacement = publisher.prepare(request)['operation']
    assert publisher.prepare(request)['operation'] == replacement
    with pytest.raises(ValueError, match='separate authorization'):
        publisher.prepare({**request, 'action_id': 'another-replacement'})
    remote.lose_response = True
    with pytest.raises(Diagnostic):
        publisher.publish(replacement)
    assert publication.load(replacement)['status'] == 'pending'
    assert publisher.publish(replacement, reconcile=True)['status'] == 'verified'
    for _ in range(2):
        result = publisher.publish(operation, reconcile=True)
        assert result['replacement_operation'] == replacement
        assert result['receipt']['id'] == 100
    assert len(remote.writes) == 2


@pytest.mark.parametrize('change', ['head', 'actor', 'source', 'reply'])
def test_disposition_requires_fresh_unchanged_reassessment_evidence(remote, change):
    operation = remote.post()
    remote.comments.clear()
    remote.disposition(operation)
    if change == 'head':
        remote.metadata['headRefOid'] = 'b'*40
    elif change == 'actor':
        remote.actor = 'another-actor'
    elif change == 'source':
        remote.source['body'] = 'A revised question'
    else:
        remote.comments.append(deepcopy(remote.writes[0]))
    with pytest.raises(Diagnostic):
        publisher.publish(operation, reconcile=True)
    assert len(remote.writes) == 1
    if change == 'head':
        remote.disposition(operation)
        assert publisher.publish(operation, reconcile=True)['status'] == 'verified'
        assert len(publication.load(operation)['reply_dispositions']) == 2


@pytest.mark.parametrize('status', [401, 403, 500])
def test_access_failures_are_not_treated_as_deleted_or_settled(remote, status):
    operation = remote.post()
    remote.comments.clear()
    remote.failure = subprocess.CalledProcessError(1, ['gh'], stderr='gh: failure (HTTP '+str(status)+')')
    with pytest.raises(subprocess.CalledProcessError):
        publisher.recovery().inspect(operation)
    saved = publication.load(operation)
    assert saved['reply_observations'][-1]['state'] == 'evidence_unavailable'
    assert saved['status'] == 'verified' and 'reply_dispositions' not in saved
    assert len(remote.writes) == 1


@pytest.mark.parametrize('pages', [{}, [{}], [[], {}], [[{'id': 11}], {}], [[None]]])
def test_malformed_reply_pages_cannot_settle_a_verified_reply(remote, monkeypatch, pages):
    operation = remote.post()
    original = publication.load(operation)
    remote.comments.clear()
    remote.disposition(operation)
    disposition = publication.load(operation)['reply_dispositions'][-1]
    monkeypatch.setattr(publisher, 'replies', read_replies)
    monkeypatch.setattr(publisher, 'run', lambda *args, **kwargs: deepcopy(pages))
    with pytest.raises(ValueError, match='Reply pages are unavailable or ambiguous'):
        publisher.recovery().inspect(operation)
    with pytest.raises(ValueError, match='Reply pages are unavailable or ambiguous'):
        publisher.recovery().dispose(operation, {k: disposition[k] for k in ('decision', 'reason', 'observation')})
    with pytest.raises(Diagnostic) as error:
        publisher.publish(operation, reconcile=True)
    assert error.value.code == 'publication_not_ready'
    saved = publication.load(operation)
    assert saved['reply_observations'][-1]['state'] == 'evidence_unavailable'
    assert saved['reply_dispositions'] == [disposition]
    assert saved['request'] == original['request'] and saved['receipt'] == original['receipt']
    assert len(remote.writes) == 1


@pytest.mark.parametrize('members', [[{'id': 100}], [{'id': 100, 'in_reply_to_id': 77}],
                                    [{}], [{'id': None}], [{'id': True}], [{'id': '100'}],
                                    [{'id': 0}], [{'id': -1}],
                                    [{'id': 11}, {'id': 11}]])
def test_inline_recovery_rejects_ambiguous_raw_members_before_parent_filtering(
        remote, monkeypatch, inline_operation, members):
    operation = inline_operation
    original = publication.load(operation)
    remote.comments.clear()
    remote.disposition(operation)
    disposition = publication.load(operation)['reply_dispositions'][-1]
    monkeypatch.setattr(publisher, 'replies', read_replies)
    monkeypatch.setattr(publisher, 'run', lambda *args, **kwargs: [deepcopy(members)])
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        publisher.recovery().inspect(operation)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        publisher.recovery().dispose(operation, {k: disposition[k] for k in ('decision', 'reason', 'observation')})
    with pytest.raises(Diagnostic) as error:
        publisher.publish(operation, reconcile=True)
    assert error.value.code == 'publication_not_ready'
    saved = publication.load(operation)
    assert saved['reply_observations'][-1]['state'] == 'evidence_unavailable'
    assert saved['reply_dispositions'] == [disposition]
    assert all(saved[key] == original[key] for key in ('request', 'response', 'receipt'))
    assert len(remote.writes) == 1


def test_inline_recovery_allows_unrelated_root_comments_without_replaying(remote, monkeypatch, inline_operation):
    remote.comments.clear()
    monkeypatch.setattr(publisher, 'replies', read_replies)
    members = [{'id': 10}, {'id': 77}, {'id': 78, 'in_reply_to_id': 77}]
    monkeypatch.setattr(publisher, 'run', lambda *args, **kwargs: [deepcopy(members)])
    remote.disposition(inline_operation)
    assert publisher.publish(inline_operation, reconcile=True)['reply_obligation'] == 'no_replacement'
    assert len(remote.writes) == 1


@pytest.mark.parametrize('field,value', [('id', True), ('id', 10.0), ('id', 0), ('id', -1),
                                      ('id', None), ('user', None), ('user', []),
                                      ('user', {'login': {'unexpected': ['reviewer']}}),
                                      ('user', {'login': ['reviewer']}), ('user', {'login': True}),
                                      ('user', {'login': ''}), ('user', {'login': '   '})])
def test_malformed_individual_source_identity_cannot_receive_a_disposition(remote, field, value):
    operation = remote.post()
    original = publication.load(operation)
    remote.comments.clear()
    remote.source[field] = value
    with pytest.raises(ValueError, match='feedback identity'):
        remote.disposition(operation)
    saved = publication.load(operation)
    assert saved['reply_observations'][-1]['state'] == 'evidence_unavailable'
    assert 'reply_dispositions' not in saved
    assert all(saved[key] == original[key] for key in ('request', 'response', 'receipt'))
    assert len(remote.writes) == 1


@pytest.mark.parametrize('identity', [True, 100.0, 0, -1, None])
def test_malformed_individual_reply_identity_cannot_receive_a_disposition(remote, monkeypatch, identity):
    operation = remote.post()
    remote.comments[0]['body'] = 'Edited historical answer'
    original_api = remote.api
    def api(pr, path, **kwargs):
        result = original_api(pr, path, **kwargs)
        if path.endswith('/100'):
            result['id'] = identity
        return result
    monkeypatch.setattr(publisher, 'api', api)
    with pytest.raises(ValueError, match='Reply identity'):
        remote.disposition(operation)
    assert not publication.load(operation).get('reply_dispositions')
    assert len(remote.writes) == 1


@pytest.mark.parametrize('identity', [True, 10.0, 0, -1, None])
def test_malformed_individual_inline_parent_identity_cannot_receive_a_disposition(remote, inline_operation, identity):
    remote.comments[0].update(body='Edited historical answer', in_reply_to_id=identity)
    with pytest.raises(ValueError, match='Reply identity'):
        remote.disposition(inline_operation)
    assert not publication.load(inline_operation).get('reply_dispositions')
    assert len(remote.writes) == 1


@pytest.mark.parametrize('identity', [True, 10.0, 0, -1, None])
@pytest.mark.parametrize('position', ['source', 'unrelated'])
def test_malformed_thread_database_identity_cannot_receive_a_disposition(remote, inline_operation, identity, position):
    remote.comments.clear()
    if position == 'source':
        remote.thread['comments'][0]['databaseId'] = identity
    else:
        remote.thread['comments'].append({'databaseId': identity})
    with pytest.raises(ValueError, match='exact original thread'):
        remote.disposition(inline_operation)
    assert not publication.load(inline_operation).get('reply_dispositions')
    assert len(remote.writes) == 1


@pytest.mark.parametrize('inline', [False, True])
def test_complete_reply_list_must_include_accessible_original_source(remote, monkeypatch, request, inline):
    operation = request.getfixturevalue('inline_operation') if inline else remote.post()
    remote.comments.clear()
    remote.disposition(operation)
    disposition = publication.load(operation)['reply_dispositions'][-1]
    monkeypatch.setattr(publisher, 'replies', lambda *args, **kwargs: [])
    with pytest.raises(ValueError, match='source or routing parent'):
        remote.disposition(operation)
    with pytest.raises(ValueError, match='source or routing parent'):
        publisher.recovery().dispose(operation, {k: disposition[k] for k in ('decision', 'reason', 'observation')})
    with pytest.raises(Diagnostic) as error:
        publisher.publish(operation, reconcile=True)
    assert error.value.code == 'publication_not_ready'
    assert publication.load(operation)['reply_dispositions'] == [disposition]


@pytest.mark.parametrize('inline_operation', [77], indirect=True)
def test_complete_inline_list_must_include_distinct_routing_parent(remote, inline_operation):
    remote.comments.clear()
    with pytest.raises(ValueError, match='source or routing parent'):
        remote.disposition(inline_operation)
    assert not publication.load(inline_operation).get('reply_dispositions')


@pytest.mark.parametrize('inline_operation', [10, 77], indirect=True)
@pytest.mark.parametrize('claimed_parent', ['absent', None, 10, 43, True, 77.0, 0, -1])
def test_inline_source_parent_must_agree_with_real_thread_root(
        remote, monkeypatch, inline_operation, claimed_parent):
    operation = inline_operation
    original = publication.load(operation)
    parent = original['request']['approved']['parent']
    remote.comments[:] = [{'id': parent}] if parent != remote.source['id'] else []
    monkeypatch.setattr(publisher, 'handler', read_handler)
    calls = []
    def run(argv, **kwargs):
        assert argv[:2] == ['gh', 'api'] and 'graphql' in argv
        calls.append(kwargs['payload']['variables']['id'])
        return {'data': {'node': {
            'id': 'T', 'isResolved': False, 'viewerCanResolve': True,
            'pullRequest': {'url': PR, 'headRefOid': remote.metadata['headRefOid']},
            'comments': {'nodes': [
                {'id': 'comment-'+str(c['databaseId']), 'databaseId': c['databaseId'],
                 'url': PR+'#discussion_r'+str(c['databaseId']), 'body': 'Question',
                 'author': {'login': 'reviewer'}} for c in remote.thread['comments']],
                'pageInfo': {'hasNextPage': False}}}}}
    monkeypatch.setattr(publisher, 'run', run)
    remote.disposition(operation)
    assert publisher.publish(operation, reconcile=True)['reply_obligation'] == 'no_replacement'
    disposition = publication.load(operation)['reply_dispositions'][-1]
    assert calls and set(calls) == {'T'}
    if claimed_parent == 'absent':
        remote.source.pop('in_reply_to_id', None)
    else:
        remote.source['in_reply_to_id'] = claimed_parent
    if parent == remote.source['id'] and claimed_parent in {'absent', None}:
        remote.disposition(operation)
        assert publisher.publish(operation, reconcile=True)['reply_obligation'] == 'no_replacement'
        return
    with pytest.raises(ValueError, match='source parent contradicts'):
        publisher.recovery().inspect(operation)
    with pytest.raises(ValueError, match='source parent contradicts'):
        publisher.recovery().dispose(operation, {k: disposition[k] for k in ('decision', 'reason', 'observation')})
    with pytest.raises(Diagnostic) as error:
        publisher.publish(operation, reconcile=True)
    assert error.value.code == 'publication_not_ready'
    saved = publication.load(operation)
    assert saved['reply_dispositions'] == [disposition]
    assert all(saved[key] == original[key] for key in ('request', 'response', 'receipt'))
    assert len(remote.writes) == 1


@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('field,value', [('body', 'A conflicting question'),
                                      ('html_url', PR+'#issuecomment-99'),
                                      ('user', {'login': 'another-reviewer'})])
def test_original_source_list_and_individual_evidence_must_agree(remote, monkeypatch, request, inline, field, value):
    operation = request.getfixturevalue('inline_operation') if inline else remote.post()
    remote.comments.clear()
    remote.disposition(operation)
    disposition = publication.load(operation)['reply_dispositions'][-1]
    listed = {**deepcopy(remote.source), field: value}
    monkeypatch.setattr(publisher, 'replies', lambda *args, **kwargs: [deepcopy(listed)])
    with pytest.raises(ValueError, match='feedback list and individual observation disagree'):
        remote.disposition(operation)
    with pytest.raises(ValueError, match='feedback list and individual observation disagree'):
        publisher.recovery().dispose(operation, {k: disposition[k] for k in ('decision', 'reason', 'observation')})
    with pytest.raises(Diagnostic) as error:
        publisher.publish(operation, reconcile=True)
    assert error.value.code == 'publication_not_ready'
    assert publication.load(operation)['reply_dispositions'] == [disposition]
    assert len(remote.writes) == 1


@pytest.mark.parametrize('inline_operation', [77], indirect=True)
@pytest.mark.parametrize('witness', ['source', 'reply'])
@pytest.mark.parametrize('listed_parent', [True, 77.0, 0, -1])
def test_list_routing_parent_requires_exact_integer_identity(
        remote, monkeypatch, inline_operation, witness, listed_parent):
    operation = inline_operation
    remote.comments[-1]['body'] = 'Edited historical answer'
    remote.disposition(operation)
    disposition = publication.load(operation)['reply_dispositions'][-1]
    def replies(*args, **kwargs):
        items = deepcopy(remote.comments + [remote.source])
        changed_id = remote.source['id'] if witness == 'source' else 100
        next(item for item in items if item['id'] == changed_id)['in_reply_to_id'] = listed_parent
        return items
    monkeypatch.setattr(publisher, 'replies', replies)
    with pytest.raises(ValueError, match='list and individual observation disagree'):
        publisher.recovery().inspect(operation)
    with pytest.raises(ValueError, match='list and individual observation disagree'):
        publisher.recovery().dispose(operation, {k: disposition[k] for k in ('decision', 'reason', 'observation')})
    with pytest.raises(Diagnostic) as error:
        publisher.publish(operation, reconcile=True)
    assert error.value.code == 'publication_not_ready'
    assert publication.load(operation)['reply_dispositions'] == [disposition]
    assert len(remote.writes) == 1


def test_source_list_profile_metadata_need_not_match_individual_read(remote, monkeypatch):
    operation = remote.post()
    remote.comments.clear()
    listed = deepcopy(remote.source)
    listed['user']['avatar_url'] = 'https://example.test/avatar.png'
    monkeypatch.setattr(publisher, 'replies', lambda *args, **kwargs: [deepcopy(listed)])
    remote.disposition(operation)
    assert publisher.publish(operation, reconcile=True)['reply_obligation'] == 'no_replacement'


@pytest.mark.parametrize('pages', [[], [[]], [[], []]])
def test_valid_empty_reply_pages_allow_explicit_obsolete_answer_disposition(remote, monkeypatch, pages):
    remote.source['html_url'] = PR+'#pullrequestreview-10'
    request = remote.request(source={'kind': 'review', 'id': 10, 'url': remote.source['html_url']})
    operation = publisher.prepare(request)['operation']
    publisher.publish(operation)
    remote.comments.clear()
    monkeypatch.setattr(publisher, 'replies', read_replies)
    monkeypatch.setattr(publisher, 'run', lambda *args, **kwargs: deepcopy(pages))
    remote.disposition(operation)
    assert publisher.publish(operation, reconcile=True)['reply_obligation'] == 'no_replacement'
    assert len(remote.writes) == 1


@pytest.mark.parametrize('field,value', [('url', None), ('headRefOid', None), ('headRefOid', ''),
                                       ('headRefOid', 'not-a-sha'), ('title', {}), ('body', None)])
def test_incomplete_pr_metadata_cannot_settle_a_verified_reply(remote, field, value):
    operation = remote.post()
    original = publication.load(operation)
    remote.comments.clear()
    remote.disposition(operation)
    disposition = publication.load(operation)['reply_dispositions'][-1]
    if value is None:
        del remote.metadata[field]
    else:
        remote.metadata[field] = value
    with pytest.raises(ValueError, match='Recovery PR metadata is unavailable or ambiguous'):
        publisher.recovery().inspect(operation)
    with pytest.raises(ValueError, match='Recovery PR metadata is unavailable or ambiguous'):
        publisher.recovery().dispose(operation, {k: disposition[k] for k in ('decision', 'reason', 'observation')})
    with pytest.raises(Diagnostic) as error:
        publisher.publish(operation, reconcile=True)
    assert error.value.code == 'publication_not_ready'
    saved = publication.load(operation)
    assert saved['reply_observations'][-1]['state'] == 'evidence_unavailable'
    assert saved['reply_dispositions'] == [disposition]
    assert saved['request'] == original['request'] and saved['receipt'] == original['receipt']
    assert len(remote.writes) == 1


def test_uncertain_initial_write_cannot_receive_a_disposition_or_replacement(remote):
    request = remote.request()
    operation = publisher.prepare(request)['operation']
    remote.lose_response = True
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    remote.comments.clear()
    with pytest.raises(ValueError, match='uncertain initial writes'):
        publisher.recovery().inspect(operation)
    with pytest.raises(ValueError, match='uncertain initial writes'):
        publisher.recovery().dispose(operation, {'decision': 'no_replacement', 'reason': 'obsolete', 'observation': 'x'})
    with pytest.raises(ValueError, match='Uncertain publication'):
        publisher.prepare(remote.request(replaces=operation, authorization='New instruction'))
    assert publication.load(operation)['status'] == 'pending'
    assert len(remote.writes) == 1


def test_recovery_cannot_mistake_another_identical_reply_for_the_original(remote):
    operation = remote.post()
    remote.comments[0].update(id=999, html_url=PR+'#issuecomment-999')
    with pytest.raises(Diagnostic):
        publisher.publish(operation, reconcile=True)
    assert publisher.recovery().inspect(operation)['snapshot']['reply_state'] == 'unavailable'
    assert publication.load(operation)['receipt']['id'] == 100


def test_inspection_must_precede_disposition_and_source_identity_must_match(remote):
    operation = remote.post()
    remote.comments.clear()
    with pytest.raises(ValueError, match='Inspect and reassess'):
        publisher.recovery().dispose(operation, {'decision': 'no_replacement', 'reason': 'obsolete', 'observation': 'x'})
    observed = publisher.recovery().inspect(operation)
    remote.source['html_url'] = PR.replace('/7', '/8')+'#issuecomment-10'
    with pytest.raises(ValueError, match='feedback identity'):
        publisher.recovery().dispose(operation, {'decision': 'no_replacement', 'reason': 'obsolete',
                                               'observation': observed['fingerprint']})
    assert 'reply_dispositions' not in publication.load(operation)


@pytest.mark.parametrize('change', ['source', 'actor'])
def test_replacement_rechecks_source_and_actor_before_dispatch(remote, change):
    operation = remote.post()
    remote.comments.clear()
    remote.disposition(operation, 'replacement_required')
    replacement = publisher.prepare(remote.request(replaces=operation, authorization='Explicit replacement'))['operation']
    if change == 'source':
        remote.source['body'] = 'Different current question'
    else:
        remote.actor = 'another-actor'
    with pytest.raises(Diagnostic):
        publisher.publish(replacement)
    assert publication.load(replacement)['status'] == 'prepared'
    assert len(remote.writes) == 1


@pytest.mark.parametrize('state', ['unavailable', 'edited'])
def test_restored_original_reply_can_receive_a_fresh_settled_disposition(remote, state):
    operation = remote.post()
    original = publication.load(operation)
    if state == 'unavailable':
        remote.comments.clear()
    else:
        remote.comments[0]['body'] = 'Edited answer'
    remote.disposition(operation, 'replacement_required')
    stale = publisher.prepare(remote.request(replaces=operation, authorization='Explicit replacement'))['operation']
    remote.comments[:] = [deepcopy(remote.writes[0])]
    with pytest.raises(Diagnostic):
        publisher.publish(operation, reconcile=True)
    observed = publisher.recovery().inspect(operation)
    assert observed['snapshot']['reply_state'] == 'present'
    with pytest.raises(ValueError):
        remote.disposition(operation, 'replacement_required')
    remote.disposition(operation)
    for _ in range(2):
        result = publisher.publish(operation, reconcile=True)
        assert result['reply_obligation'] == 'no_replacement'
        assert result['receipt'] == original['receipt']
    with pytest.raises(Diagnostic):
        publisher.publish(stale)
    retained = publication.load(operation)
    assert retained['request'] == original['request']
    assert retained['response'] == original['response']
    assert len(retained['reply_dispositions']) == 2
    assert len(remote.writes) == 1


def test_superseded_request_cannot_relink_its_stale_replacement_at_the_same_head(remote):
    operation = remote.post()
    remote.comments.clear()
    remote.disposition(operation, 'replacement_required')
    request = remote.request(body='Answer to the original question', replaces=operation,
                             authorization='Explicit original replacement')
    stale = publisher.prepare(request)['operation']
    remote.source['body'] = 'A revised question at the same head'
    remote.disposition(operation, 'replacement_required')
    with pytest.raises(ValueError, match='superseded'):
        publisher.prepare(request)
    with pytest.raises(Diagnostic):
        publisher.publish(stale)
    assert 'replacement_operation' not in publication.load(operation)['reply_dispositions'][-1]
    replacement = publisher.prepare({**request, 'body': 'Answer to the revised question',
                                     'authorization': 'Explicit revised replacement'})['operation']
    assert replacement != stale
    assert publisher.prepare({**request, 'body': 'Answer to the revised question',
                              'authorization': 'Explicit revised replacement'})['operation'] == replacement
    publisher.publish(replacement)
    assert publisher.publish(operation, reconcile=True)['replacement_operation'] == replacement
    assert len(remote.writes) == 2


def test_disposition_waits_for_replacement_dispatch_before_superseding(remote, monkeypatch):
    operation = remote.post()
    remote.comments.clear()
    remote.disposition(operation, 'replacement_required')
    replacement = publisher.prepare(remote.request(replaces=operation, authorization='Explicit replacement'))['operation']
    prechecked, resume, disposing, disposed = (threading.Event() for _ in range(4))
    failures = []
    original_write = publication.write
    original_flock = fcntl.flock
    dispatch_path = Path(operation).parent.parent/'.dispatch.lock'

    def write(path, value):
        if str(path) == replacement and value['status'] == 'pending':
            prechecked.set()
            if not resume.wait(5):
                raise TimeoutError('Test did not release replacement dispatch')
        return original_write(path, value)

    monkeypatch.setattr(publication, 'write', write)

    def flock(lock, flags):
        if threading.current_thread() is disposition and Path(lock.name) == dispatch_path:
            try:
                original_flock(lock, flags | fcntl.LOCK_NB)
            except BlockingIOError:
                disposing.set()
            else:
                raise AssertionError('Disposition acquired the dispatch lock while publication was paused')
        return original_flock(lock, flags)

    monkeypatch.setattr(fcntl, 'flock', flock)

    def publish():
        try:
            publisher.publish(replacement)
        except BaseException as exc:
            failures.append(exc)

    def dispose():
        try:
            remote.disposition(operation)
        except BaseException as exc:
            failures.append(exc)
        finally:
            disposed.set()

    publishing = threading.Thread(target=publish, daemon=True)
    disposition = threading.Thread(target=dispose, daemon=True)
    publishing.start()
    try:
        assert prechecked.wait(5), 'Replacement did not reach the pre-dispatch window'
        disposition.start()
        assert disposing.wait(5), 'Disposition did not reach the held dispatch lock'
        assert not disposed.is_set(), 'Disposition superseded a replacement after its precheck'
    finally:
        resume.set()
        publishing.join(5)
        if disposition.ident is not None:
            disposition.join(5)
    assert not publishing.is_alive() and not disposition.is_alive()
    assert not failures
    assert publication.load(replacement)['status'] == 'verified'
    assert publication.load(operation)['reply_dispositions'][-1]['decision'] == 'no_replacement'
    assert len(remote.writes) == 2


def test_stale_undispatched_replacement_can_be_reassessed_without_erasing_history(remote):
    operation = remote.post()
    remote.comments.clear()
    remote.disposition(operation, 'replacement_required')
    stale = publisher.prepare(remote.request(replaces=operation, authorization='Explicit replacement'))['operation']
    remote.metadata['headRefOid'] = 'b'*40
    remote.disposition(operation)
    assert publisher.publish(operation, reconcile=True)['reply_obligation'] == 'no_replacement'
    with pytest.raises(Diagnostic):
        publisher.publish(stale)
    records = publication.load(operation)['reply_dispositions']
    assert len(records) == 2 and records[0]['replacement_operation'] == stale
    assert publication.load(stale)['status'] == 'prepared'
    assert len(remote.writes) == 1


@pytest.mark.parametrize('restored_original', [False, True])
def test_uncertain_replacement_cannot_be_cleared_by_another_disposition(remote, restored_original):
    operation = remote.post()
    remote.comments.clear()
    remote.disposition(operation, 'replacement_required')
    replacement = publisher.prepare(remote.request(replaces=operation, authorization='Explicit replacement'))['operation']
    remote.lose_response = True
    with pytest.raises(Diagnostic):
        publisher.publish(replacement)
    if restored_original:
        remote.comments.append(deepcopy(remote.writes[0]))
    with pytest.raises(ValueError, match='uncertain linked replacement'):
        remote.disposition(operation)
    assert publication.load(replacement)['status'] == 'pending'
    assert len(remote.writes) == 2


def test_inline_disposition_keeps_live_answer_requirement_for_resolution(remote, monkeypatch, inline_operation):
    operation = inline_operation
    remote.comments.clear()
    remote.disposition(operation)
    assert publisher.publish(operation, reconcile=True)['status'] == 'verified'
    # The ordinary resolution publisher still requires a live answer in the thread.
    def run(argv, **kwargs):
        return {'data': {'node': {'id': 'T', 'isResolved': False, 'viewerCanResolve': True,
                 'pullRequest': {'url': PR, 'headRefOid': 'a'*40},
                 'comments': {'nodes': [{'id': 'source', 'databaseId': 10, 'url': PR+'#discussion_r10',
                                        'body': 'Question', 'author': {'login': 'reviewer'}}],
                              'pageInfo': {'hasNextPage': False}}}}}
    monkeypatch.setattr(publisher, 'run', run)
    with pytest.raises(ValueError, match='verified posted answer'):
        publisher.prepare({'kind': 'resolve-thread', 'pr': PR, 'head': 'a'*40, 'thread_id': 'T', 'reply_id': 100})
    remote.thread['comments'].clear()
    with pytest.raises(Diagnostic, match='exact original thread'):
        publisher.publish(operation, reconcile=True)
