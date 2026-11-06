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
    monkeypatch.setattr(publisher, 'replies', lambda _: deepcopy(result.comments))
    return result


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


def test_inline_disposition_keeps_live_answer_requirement_for_resolution(remote, monkeypatch):
    request = remote.request(kind='reply-inline', parent=10,
                            source={'kind': 'inline_comment', 'id': 10, 'url': PR+'#discussion_r10', 'thread_id': 'T'})
    remote.source['html_url'] = request['source']['url']
    # Capture a genuinely verified reply with the normal publisher transport.
    original_api = remote.api
    def api(pr, path, *, payload=None):
        if payload is not None:
            answer = original_api(pr, path, payload=payload)
            remote.comments[-1].update(in_reply_to_id=10, html_url=PR+'#discussion_r100')
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
