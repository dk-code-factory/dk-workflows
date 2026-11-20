"""Rejected review creation must not become an unrecoverable uncertain write."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from shared import publication
from shared.diagnostics import Diagnostic
from shared.private_state import write


ROOT = Path(__file__).resolve().parents[2]
ERROR = 'User can only have one pending review per pull request'
BODY = {'message': 'Unprocessable Entity', 'errors': [ERROR], 'status': '422'}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


fixture = module('review_submission_remote', ROOT/'tests/pr/fixtures/github_remote.py')


def rejection(body=None, stderr='gh: Unprocessable Entity (HTTP 422)\n', code=1):
    return subprocess.CalledProcessError(code, ['gh', 'api'],
        output=json.dumps(BODY) if body is None else body, stderr=stderr)


@pytest.fixture
def setup(monkeypatch):
    publisher = module('review_submission_publisher', ROOT/'scripts/publish.py')
    remote = fixture.Remote()
    dispatch = {'error': None, 'attempts': 0}

    def run(argv, **kwargs):
        if '--method' in argv and 'repos/owner/repo/pulls/7/reviews' in argv:
            dispatch['attempts'] += 1
            if dispatch['error'] is not None:
                raise dispatch['error']
        return remote.run(argv, **kwargs)

    monkeypatch.setattr(publisher, 'metadata', remote.metadata)
    monkeypatch.setattr(publisher, 'api', remote.api)
    monkeypatch.setattr(publisher, 'run', run)
    return publisher, remote, dispatch


def draft(actor='author'):
    return {'id': 400, 'state': 'PENDING', 'user': {'login': actor},
            'body': 'Unrelated draft text', 'commit_id': fixture.HEAD,
            'html_url': fixture.PR+'#pullrequestreview-400'}


def legacy_pending(setup, monkeypatch, error):
    publisher, _, dispatch = setup
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    dispatch['error'] = error
    with monkeypatch.context() as old:
        old.setattr(publisher, 'rejected_review', lambda _: False, raising=False)
        with pytest.raises(Diagnostic) as failure:
            publisher.publish(operation)
        assert failure.value.code == 'publication_uncertain'
    return operation


@pytest.mark.parametrize('actor', ['author', 'AUTHOR'])
def test_own_pending_review_blocks_preparation_without_changing_draft(setup, actor):
    publisher, remote, dispatch = setup
    original = draft(actor)
    remote.state['reviews'] = [deepcopy(original)]
    with pytest.raises(Diagnostic) as failure:
        publisher.prepare(fixture.request('post-review'))
    assert failure.value.code == 'pending_review'
    assert '400' in str(failure.value)
    assert failure.value.action['argv'][-1] == 'repos/owner/repo/pulls/7/reviews/400'
    assert remote.state['reviews'] == [original]
    assert dispatch['attempts'] == 0 and remote.state['writes'] == {}


def test_another_accounts_draft_does_not_block_review_publication(setup):
    publisher, remote, _ = setup
    original = draft('another-user')
    remote.state['reviews'] = [deepcopy(original)]
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    assert publisher.publish(operation)['status'] == 'verified'
    assert remote.state['reviews'][0] == original
    assert remote.state['writes'] == {'post-review': 1}


def test_submitted_review_from_deleted_account_does_not_create_a_draft_hold(setup):
    publisher, remote, _ = setup
    original = {**draft(), 'state': 'COMMENTED', 'user': None}
    remote.state['reviews'] = [deepcopy(original)]
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    assert publisher.publish(operation)['status'] == 'verified'
    assert remote.state['reviews'][0] == original
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('field,value', [('state', None), ('state', []), ('state', 'UNKNOWN'), ('user', {}),
                                       ('user', {'login': None})])
def test_incomplete_review_listing_is_not_treated_as_no_pending_review(setup, field, value):
    publisher, remote, dispatch = setup
    remote.state['reviews'] = [{**draft(), field: value}]
    with pytest.raises(ValueError):
        publisher.prepare(fixture.request('post-review'))
    assert dispatch['attempts'] == 0


def test_pending_review_appearing_before_dispatch_preserves_prepared_intent(setup):
    publisher, remote, dispatch = setup
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    original = Path(operation).read_bytes()
    remote.state['reviews'] = [draft()]
    with pytest.raises(Diagnostic) as failure:
        publisher.publish(operation)
    assert failure.value.code == 'pending_review'
    assert Path(operation).read_bytes() == original
    assert remote.state['reviews'] == [draft()] and dispatch['attempts'] == 0


def test_actor_change_cannot_adopt_another_accounts_pending_review(setup):
    publisher, remote, dispatch = setup
    remote.state['reviews'] = [draft('another-user')]
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    original = Path(operation).read_bytes()
    remote.state['actor'] = 'another-user'
    with pytest.raises(Diagnostic, match='Review actor changed'):
        publisher.publish(operation)
    assert Path(operation).read_bytes() == original and dispatch['attempts'] == 0


@pytest.mark.parametrize('actor', [None, '', 1])
def test_incomplete_authenticated_actor_blocks_preparation(setup, actor):
    publisher, remote, dispatch = setup
    remote.state['actor'] = actor
    with pytest.raises(ValueError, match='actor identity'):
        publisher.prepare(fixture.request('post-review'))
    assert dispatch['attempts'] == 0


def test_initial_rejection_is_terminal_and_requires_fresh_preparation(setup):
    publisher, remote, dispatch = setup
    request = fixture.request('post-review')
    operation = publisher.prepare(request)['operation']
    before = publication.load(operation)
    dispatch['error'] = rejection()
    with pytest.raises(Diagnostic) as failure:
        publisher.publish(operation)
    assert failure.value.code == 'fresh_preparation_required'
    rejected = publication.load(operation)
    assert rejected['status'] == 'not_applied' and 'receipt' not in rejected
    assert rejected['request'] == before['request']
    assert rejected['failure']['stdout'] == json.dumps(BODY)
    stable = Path(operation).read_bytes()
    dispatch['error'] = None
    assert publisher.publish(operation, reconcile=True)['status'] == 'not_applied'
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    assert Path(operation).read_bytes() == stable and dispatch['attempts'] == 1
    # A still-existing draft is a separate live guard; terminal recovery cannot submit it.
    remote.state['reviews'] = [draft()]
    with pytest.raises(Diagnostic):
        publisher.prepare(request)
    remote.state['reviews'] = []  # An independently authorized action resolved the draft.
    fresh = publisher.prepare(request)['operation']
    assert fresh != operation
    assert publisher.publish(fresh)['status'] == 'verified'
    assert dispatch['attempts'] == 2 and Path(operation).read_bytes() == stable


def test_retained_rejection_recovers_in_fresh_process_without_remote_reads(setup, monkeypatch):
    publisher, _, dispatch = setup
    operation = legacy_pending(setup, monkeypatch, rejection())
    original = publication.load(operation)
    script = '''
import importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root/'runtime'))
spec = importlib.util.spec_from_file_location('publisher', root/'scripts/publish.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
def forbidden(*args, **kwargs):
    raise AssertionError('Rejection reconciliation must not read or mutate GitHub')
publisher.run = publisher.api = publisher.metadata = forbidden
print(json.dumps(publisher.publish(sys.argv[2], reconcile=True)))
'''
    for _ in range(2):
        child = subprocess.run([sys.executable, '-c', script, str(ROOT), operation],
                               capture_output=True, text=True, timeout=30)
        assert child.returncode == 0, child.stderr
        assert json.loads(child.stdout)['status'] == 'not_applied'
    assert publication.load(operation) == {**original, 'status': 'not_applied'}
    assert dispatch['attempts'] == 1


@pytest.mark.parametrize('error', [
    rejection(body=''), rejection(body='not JSON'), rejection(body='[]'),
    rejection(body=json.dumps({**BODY, 'errors': ['Validation Failed']})),
    rejection(body=json.dumps({**BODY, 'errors': [ERROR, 'Another error']})),
    rejection(body=json.dumps({**BODY, 'status': '500'})),
    rejection(body=json.dumps({**BODY, 'message': 'Server Error'})),
    rejection(body=json.dumps(BODY)[:-1]+',"status":"500"}'),
    rejection(body=json.dumps(BODY)[:-1]+',"status":"422"}'),
    rejection(stderr='gh: Bad Gateway (HTTP 502)'),
    rejection(stderr='connection reset'), rejection(code=0), rejection(code=-9),
    subprocess.TimeoutExpired(['gh', 'api'], 1, output=json.dumps(BODY),
                              stderr='gh: Unprocessable Entity (HTTP 422)'),
    OSError('connection lost'),
])
def test_ambiguous_failures_remain_pending_and_never_resend(setup, error):
    publisher, _, dispatch = setup
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    dispatch['error'] = error
    for reconcile in [False, True, True]:
        with pytest.raises(Diagnostic) as failure:
            publisher.publish(operation, reconcile=reconcile)
        assert failure.value.code == 'publication_uncertain'
        assert publication.load(operation)['status'] == 'pending'
    assert dispatch['attempts'] == 1


@pytest.mark.parametrize('change', ['phase', 'response', 'receipt', 'exit_status'])
def test_conflicting_retained_evidence_cannot_be_settled_as_rejection(setup, monkeypatch, change):
    publisher, _, dispatch = setup
    operation = legacy_pending(setup, monkeypatch, rejection())
    original = publication.load(operation)
    if change == 'phase':
        original['failure']['phase'] = 'observe'
    elif change == 'exit_status':
        original['failure']['exit_status'] = True
    else:
        original[change] = {'id': 401}
    write(operation, original)
    with pytest.raises(Diagnostic):
        publisher.publish(operation, reconcile=True)
    assert publication.load(operation)['status'] == 'pending'
    assert dispatch['attempts'] == 1


def test_lost_successful_response_still_reconciles_without_resending(setup):
    publisher, remote, dispatch = setup
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    remote.state['lose_response'] = ['post-review']
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    original = publication.load(operation)
    assert original['status'] == 'pending'
    assert publisher.publish(operation, reconcile=True)['status'] == 'verified'
    assert publication.load(operation)['failure'] == original['failure']
    assert dispatch['attempts'] == 1 and remote.state['writes'] == {'post-review': 1}
