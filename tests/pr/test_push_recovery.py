"""Conclusive rejected pushes settle without replaying uncertain sends."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
from threading import Barrier

import pytest
from shared import publication
from shared.diagnostics import Diagnostic
from shared.private_state import write

ROOT = Path(__file__).resolve().parents[2]
HEAD, OLD, TREE = 'b'*40, 'a'*40, 'c'*40
REF = 'refs/heads/pr-branch'
PORCELAIN = f'To https://github.com/owner/repo.git\n!\t{HEAD}:{REF}\t[remote rejected] (hook declined)\nDone\n'
LEGACY = f'To https://github.com/owner/repo.git\n ! [remote rejected] {HEAD} -> pr-branch (hook declined)\nerror: failed to push some refs\n'


@pytest.fixture
def setup(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('push_recovery_publisher', ROOT/'scripts/publish.py')
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    state = {'remote': OLD, 'tree': TREE, 'sends': [], 'error': None}
    request = {'kind': 'push', 'checkout': str(tmp_path), 'remote': 'https://github.com/owner/repo.git',
               'branch': 'pr-branch', 'head': HEAD, 'expected_head': OLD}
    monkeypatch.setattr(publisher, 'remote_head', lambda _: state['remote'])

    def run(argv, **kwargs):
        if argv[:2] == ['git', 'check-ref-format']:
            return ''
        if argv[:2] == ['git', 'rev-parse']:
            return state['tree']
        assert argv[:3] == ['git', 'push', '--porcelain']
        assert '--force-with-lease='+REF+':'+OLD in argv
        assert argv[-2:] == [request['remote'], HEAD+':'+REF]
        state['sends'].append(argv)
        if state['error']:
            raise state['error']
        state['remote'] = HEAD
        return ''

    monkeypatch.setattr(publisher, 'run', run)
    return publisher, state, request


def rejection(stdout=PORCELAIN, stderr='error: failed to push some refs', code=1):
    return subprocess.CalledProcessError(code, ['git', 'push'], output=stdout, stderr=stderr)


def pending(setup, monkeypatch, error):
    """Produce a pre-fix pending record, including the actual original send failure."""
    publisher, state, request = setup
    operation = publisher.prepare(request)['operation']
    state['error'] = error
    with monkeypatch.context() as old:
        old.setattr(publisher, 'rejected_push', lambda _: False)
        with pytest.raises(Diagnostic) as failure:
            publisher.publish(operation)
        assert failure.value.code == 'publication_uncertain'
    return operation


@pytest.mark.parametrize('output', ['porcelain', 'legacy', 'stale-lease'])
def test_initial_rejection_settles_and_identical_preparation_can_succeed(setup, output):
    publisher, state, request = setup
    state['error'] = (rejection('', LEGACY) if output == 'legacy' else
                      rejection(PORCELAIN.replace('[remote rejected] (hook declined)', '[rejected] (stale info)'))
                      if output == 'stale-lease' else rejection())
    operation = publisher.prepare(request)['operation']
    original = publication.load(operation)
    with pytest.raises(Diagnostic) as failure:
        publisher.publish(operation)
    assert failure.value.code == 'fresh_preparation_required'
    saved = publication.load(operation)
    assert saved['status'] == 'not_applied' and 'receipt' not in saved
    assert saved['request'] == original['request'] and saved['request_hash'] == original['request_hash']
    assert saved['intent'] == {'operation_id': saved['operation_id'], 'request_hash': saved['request_hash']}
    stable = Path(operation).read_bytes()
    state.update(remote=HEAD, error=None)
    # Later success on the same ref cannot change this historical rejection.
    assert publisher.publish(operation, reconcile=True)['status'] == 'not_applied'
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    assert Path(operation).read_bytes() == stable
    state['remote'] = OLD
    fresh = publisher.prepare(request)['operation']
    assert fresh != operation and publisher.prepare(request)['operation'] == fresh
    assert publisher.publish(fresh)['status'] == 'verified'
    assert len(state['sends']) == 2 and Path(operation).read_bytes() == stable


@pytest.mark.parametrize('error', [rejection(), rejection('', LEGACY)])
def test_legacy_recovery_and_new_process_reuse_preserve_original_evidence(setup, monkeypatch, error):
    publisher, state, _ = setup
    operation = pending(setup, monkeypatch, error)
    original = publication.load(operation)
    # A new process needs only the retained failure, with no network or send.
    script = '''
import importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root/'runtime'))
spec = importlib.util.spec_from_file_location('publisher', root/'scripts/publish.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
def forbidden(*args, **kwargs):
    raise AssertionError('Terminal recovery must not read the remote or send')
publisher.run = publisher.remote_head = forbidden
print(json.dumps(publisher.publish(sys.argv[2], reconcile=True)))
'''
    for _ in range(2):
        child = subprocess.run([sys.executable, '-c', script, str(ROOT), operation],
                               text=True, capture_output=True, timeout=30)
        assert child.returncode == 0, child.stderr
        assert json.loads(child.stdout)['status'] == 'not_applied'
    saved = publication.load(operation)
    assert saved == {**original, 'status': 'not_applied'}
    stable = Path(operation).read_bytes()
    assert publisher.publish(operation, reconcile=True)['status'] == 'not_applied'
    assert Path(operation).read_bytes() == stable and len(state['sends']) == 1


@pytest.mark.parametrize('error', [
    subprocess.TimeoutExpired(['git', 'push'], 1, output=PORCELAIN.encode()),
    OSError('Connection lost'), rejection('', ''), rejection('', 'error: failed to push some refs'),
    rejection(PORCELAIN.replace(REF, 'refs/heads/another-branch')),
    rejection(PORCELAIN.replace(HEAD, OLD)),
    rejection(PORCELAIN.replace('[remote rejected]', '[remote failure]')),
    rejection(PORCELAIN.replace(' (hook declined)', '')),
    rejection(PORCELAIN+'=\t'+HEAD+':'+REF+'\t[up to date]\n'),
    rejection(PORCELAIN+f' \t{OLD}:{REF}\t{OLD}..{HEAD}\n'),
    rejection(PORCELAIN, LEGACY), rejection(PORCELAIN, 'Everything up-to-date'),
    rejection('remote: '+PORCELAIN.splitlines()[1]),
    rejection(PORCELAIN, code=0), rejection(PORCELAIN, code=-9),
])
def test_ambiguous_incomplete_unrelated_or_conflicting_failure_stays_pending(setup, error):
    publisher, state, request = setup
    state['error'] = error
    operation = publisher.prepare(request)['operation']
    with pytest.raises(Diagnostic) as failure:
        publisher.publish(operation)
    assert failure.value.code == 'publication_uncertain'
    original = publication.load(operation)
    for _ in range(2):
        with pytest.raises(Diagnostic):
            publisher.publish(operation, reconcile=True)
    saved = publication.load(operation)
    assert saved['status'] == 'pending' and saved['failure'] == original['failure']
    assert publisher.prepare(request)['operation'] == operation
    with pytest.raises(ValueError, match='Uncertain publication'):
        publisher.prepare({**request, 'action_id': 'cannot-evade-uncertainty'})
    assert len(state['sends']) == 1


@pytest.mark.parametrize('change', ['phase', 'exit_status', 'missing', 'response', 'receipt'])
def test_only_original_send_failure_can_settle(setup, monkeypatch, change):
    publisher, _, _ = setup
    operation = pending(setup, monkeypatch, rejection())
    saved = publication.load(operation)
    if change == 'missing':
        saved.pop('failure')
    elif change in {'response', 'receipt'}:
        saved[change] = {'head': HEAD}
    else:
        saved['failure'][change] = 'observe' if change == 'phase' else None
    write(operation, saved)
    with pytest.raises(Diagnostic):
        publisher.publish(operation, reconcile=True)
    assert publication.load(operation)['status'] == 'pending'


def test_changed_remote_and_candidate_require_fresh_validation(setup, monkeypatch):
    publisher, state, request = setup
    operation = pending(setup, monkeypatch, rejection())
    state['remote'] = 'd'*40
    assert publisher.publish(operation, reconcile=True)['status'] == 'not_applied'
    with pytest.raises(ValueError, match='Remote lease changed'):
        publisher.prepare(request)
    state['remote'] = OLD
    fresh = publisher.prepare(request)['operation']
    for remote, tree in [('d'*40, TREE), (OLD, 'e'*40)]:
        state.update(remote=remote, tree=tree)
        with pytest.raises(Diagnostic, match='exact remote lease changed'):
            publisher.publish(fresh)
        assert publication.load(fresh)['status'] == 'prepared'
    assert len(state['sends']) == 1
    assert publication.load(fresh)['request']['approved']['expected_head'] == OLD


def test_concurrent_recovery_and_preparation_preserve_siblings(setup, monkeypatch):
    publisher, state, request = setup
    operation = pending(setup, monkeypatch, rejection())
    original = publication.load(operation)
    barrier = Barrier(4, timeout=10)

    def recover(_):
        barrier.wait()
        return publisher.publish(operation, reconcile=True)

    def prepare(_):
        barrier.wait()
        try:
            return publisher.prepare(request)
        except ValueError as exc:
            assert 'Uncertain publication' in str(exc)
            return None

    def exercise():
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(recover, i) for i in range(2)] + [pool.submit(prepare, i) for i in range(2)]
            results = [future.result(timeout=20) for future in futures]
        assert all(result['status'] == 'not_applied' for result in results[:2])
        fresh = publisher.prepare(request)['operation']
        assert fresh != operation
        assert all(result is None or result['operation'] in {operation, fresh} for result in results[2:])
        assert publication.load(operation) == {**original, 'status': 'not_applied'}
        state['error'] = None
        assert publisher.publish(fresh)['status'] == 'verified' and len(state['sends']) == 2

    # An executor waits for its workers on exit, even after a future timeout.
    # Bound the entire exercise in a killable process, including that cleanup.
    process = multiprocessing.get_context('fork').Process(target=exercise)
    process.start()
    try:
        process.join(timeout=30)
        assert process.exitcode == 0, 'Concurrent recovery failed or exceeded its deadline'
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        assert not process.is_alive(), 'Concurrency worker could not be stopped'
    assert publication.load(operation) == {**original, 'status': 'not_applied'}


def test_shared_terminal_reconciliation_never_calls_callbacks(setup):
    publisher, state, request = setup
    state['error'] = rejection()
    operation = publisher.prepare(request)['operation']
    with pytest.raises(Diagnostic):
        publisher.publish(operation)

    def forbidden(*_):
        pytest.fail('Terminal operations cannot be checked, sent or observed')

    assert publication.execute(operation, 'push', check=forbidden, send=forbidden, observe=forbidden,
                               reconcile=True)['status'] == 'not_applied'
    with pytest.raises(Diagnostic) as failure:
        publication.execute(operation, 'push', check=forbidden, send=forbidden, observe=forbidden)
    assert failure.value.code == 'fresh_preparation_required'


@pytest.mark.parametrize('porcelain', [True, False])
@pytest.mark.parametrize('branch', ['pr-branch', 'keep-up-to-date', 'release\u00a0candidate', 'release\u2028candidate'])
def test_real_offline_git_rejection_output_is_supported(setup, tmp_path, porcelain, branch):
    publisher, state, request = setup
    remote, checkout = tmp_path/'bare.git', tmp_path/'checkout'

    def git(*args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=15)

    git('init', '--bare', str(remote))
    git('init', str(checkout))
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'initial', cwd=checkout)
    head = git('rev-parse', 'HEAD', cwd=checkout).stdout.strip()
    hook = remote/'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o700)
    ref = 'refs/heads/'+branch
    git('check-ref-format', ref)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        git('push', *(['--porcelain'] if porcelain else []), str(remote), head+':'+ref, cwd=checkout)
    request['head'] = head
    request['branch'] = branch
    state['error'] = failure.value
    operation = publisher.prepare(request)['operation']
    # Exercise classification on output generated by Git, independently of mocks.
    value = publication.load(operation)
    value['failure'] = {'phase': 'send', 'exit_status': failure.value.returncode,
                        'stdout': failure.value.stdout, 'stderr': failure.value.stderr}
    assert publisher.rejected_push(value), (failure.value.stdout, failure.value.stderr)


@pytest.mark.parametrize('porcelain', [True, False])
def test_rejection_reason_can_name_up_to_date_file(setup, porcelain):
    publisher, state, request = setup
    reason = 'refusing to update workflow up-to-date.yml without workflow scope'
    state['error'] = (rejection(PORCELAIN.replace('hook declined', reason)) if porcelain
                      else rejection('', LEGACY.replace('hook declined', reason)))
    operation = publisher.prepare(request)['operation']
    with pytest.raises(Diagnostic) as failure:
        publisher.publish(operation)
    assert failure.value.code == 'fresh_preparation_required'
    assert publication.load(operation)['status'] == 'not_applied'
