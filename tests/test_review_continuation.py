"""Explicit continuation preserves retained execution and fresh coverage requirements."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from time import monotonic

import pytest

from test_state import case, done, git, PR, REPO, ROOT
from state import capture, read, Store


def exhaust(spec, store):
    for index in range(5):
        spec['options'] = {'pass': index}
        done(store, spec, run='run', cycle=f'c{index}')


def request(**changes):
    return {'run': 'run', 'request_id': 'user-message-6',
            'authorization': 'Continue reviewing this PR until it is clean.',
            'source': 'user message 6', 'cycles': 2, **changes}


def cli(*args):
    return [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR, *args]


def test_new_session_requires_authorization_and_preserves_legacy_run(case, tmp_path):
    spec, store = case
    exhaust(spec, store)
    # This is an old-format run with no allowance fields.
    assert 'review_allowances' not in read(store.manifest)['runs']['run']
    spec['options'] = {'pass': 5}
    path = tmp_path/'spec.json'
    path.write_text(json.dumps(spec))
    before = store.manifest.read_bytes()
    failed = subprocess.run(cli('start', '--spec', str(path), '--run', 'run', '--cycle', 'c5'),
                            capture_output=True, text=True, timeout=60)
    assert failed.returncode == 2 and 'limit reached' in json.loads(failed.stdout)['reason']
    assert store.manifest.read_bytes() == before
    args = request()
    response = json.loads(subprocess.check_output(cli(
        'continue-run', '--run', args['run'], '--request-id', args['request_id'],
        '--authorization', args['authorization'], '--source', args['source'], '--cycles', '2'),
        text=True, timeout=60))
    assert response['action'] == 'continued'
    assert response['review_budget'] == {'used': 5, 'limit': 7, 'remaining': 2}
    previous = read(store.manifest)
    # Every legacy field and task artifact survives the grant unchanged.
    previous['runs']['run'].pop('review_allowances')
    assert previous == json.loads(before)
    launched = json.loads(subprocess.check_output(cli(
        'start-batch', '--spec', str(path), '--run', 'run', '--cycle', 'c5'), text=True, timeout=60))
    assert launched['tasks'][0]['action'] == 'invoke'
    resumed = Store(REPO, PR).status([], compact=True, run='run')['runs']['run']
    assert resumed['cycles'] == [f'c{i}' for i in range(6)]
    assert resumed['review_budget'] == {'used': 6, 'limit': 7, 'remaining': 1}


def test_continuation_keeps_claims_findings_dispositions_retries_and_ci_counters(case):
    spec, store = case
    exhaust(spec, store)
    store.invalidate([capture(spec)['key']], 'record an incomplete retry')
    retry = store.start(spec, run='run', cycle='c4', retry=True)['claim']
    store.record(spec, retry['id'], b'incomplete with a finding',
                 {'complete': False, 'findings': [{'id': 'open-finding'}]}, 'incomplete',
                 progress={'dispositions': {'open-finding': 'open'},
                           'acceptances': [{'instruction': 'Accept f0', 'source': 'message 4'}]})
    ci = deepcopy(spec)
    ci['task']['id'] = 'ci-action'
    ci['cacheable'] = False
    for _ in range(5):
        done(store, ci, run='run', cycle='c4', ci_repair=True)
    for _ in range(3):
        done(store, ci, run='run', ci_rerun=True, head=spec['git']['head'])
    interrupted = deepcopy(spec)
    interrupted['task']['id'] = 'interrupted-sibling'
    claim = store.start(interrupted, run='run', cycle='c4')['claim']
    journal = store.root/'publication-journal.json'
    journal.write_text('{"status":"uncertain","operation":"original-write"}\n')
    before = read(store.manifest)
    artifacts = {p: p.read_bytes() for p in (store.root/'results').iterdir()}
    store.continue_run(**request())
    after = read(store.manifest)
    after['runs']['run'].pop('review_allowances')
    assert after == before
    assert {p: p.read_bytes() for p in artifacts} == artifacts
    assert json.loads(journal.read_text())['status'] == 'uncertain'
    saved = store.saved(read(store.manifest)['entries'][capture(spec)['key']])
    assert saved['normalized']['findings'] == [{'id': 'open-finding'}]
    assert store.start(interrupted, run='run', cycle='c5')['action'] == 'recover_required'
    with pytest.raises(ValueError, match='Recovery must retain'):
        store.start(interrupted, run='run', cycle='c5', recover=claim['id'])
    store.start(interrupted, run='run', cycle='c4', recover=claim['id'])
    for task, kwargs, reason in [
            (spec, {'cycle': 'c4', 'retry': True}, 'retry'),
            (ci, {'cycle': 'c4', 'ci_repair': True}, 'CI repair'),
            (ci, {'ci_rerun': True, 'head': spec['git']['head']}, 'CI reruns')]:
        with pytest.raises(ValueError, match=reason):
            store.start(task, run='run', **kwargs)


def test_target_advancement_after_final_clean_review_requires_current_coverage(case):
    spec, store = case
    exhaust(spec, store)
    original = store.status([spec])['tasks'][0]
    assert original['coverage_complete']
    repo, head = spec['git']['checkout'], spec['git']['head']
    git(repo, 'checkout', '-q', '--detach', spec['git']['base'])
    (Path(repo)/'target-change').write_text('new target content\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'target advanced')
    spec['git']['target'] = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', '--detach', head)
    assert not store.status([spec])['tasks'][0]['coverage_complete']
    with pytest.raises(ValueError, match='limit reached'):
        store.start(spec, run='run', cycle='c5')
    store.continue_run(**request())
    assert not store.status([spec])['tasks'][0]['coverage_complete']
    claim = store.start(spec, run='run', cycle='c5')['claim']
    assert not store.status([spec])['tasks'][0]['coverage_complete']
    store.record(spec, claim['id'], b'current target reviewed', {'complete': True}, 'complete')
    assert store.status([spec])['tasks'][0]['coverage_complete']


def test_replay_is_idempotent_after_consumption_and_new_authorization_is_bounded(case):
    spec, store = case
    exhaust(spec, store)
    first = store.continue_run(**request())
    for index in (5, 6):
        spec['options'] = {'pass': index}
        done(store, spec, run='run', cycle=f'c{index}')
    before = store.manifest.read_bytes()
    replay = store.continue_run(**request())
    assert replay['action'] == 'reuse' and replay['allowance'] == first['allowance']
    assert replay['review_budget'] == {'used': 7, 'limit': 7, 'remaining': 0}
    assert store.manifest.read_bytes() == before
    spec['options'] = {'pass': 7}
    with pytest.raises(ValueError, match='limit reached'):
        store.start(spec, run='run', cycle='c7')
    renewed = request(request_id='user-message-7', source='user message 7', cycles=1)
    store.continue_run(**renewed)
    done(store, spec, run='run', cycle='c7')
    spec['options'] = {'pass': 8}
    with pytest.raises(ValueError, match='limit reached'):
        store.start_batch([spec], run='run', cycle='c8')
    history = store.status([], run='run')['runs']['run']['review_allowances']
    assert [(a['previous_limit'], a['cycle_limit']) for a in history] == [(5, 7), (7, 8)]


@pytest.mark.parametrize('changes', [
    {'request_id': ''}, {'source': ' '}, {'authorization': ''}, {'run': 'unknown'},
    {'cycles': 0}, {'cycles': 6}, {'cycles': True}, {'cycles': 1.5},
])
def test_invalid_continuation_leaves_everything_unchanged(case, changes):
    spec, store = case
    exhaust(spec, store)
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError):
        store.continue_run(**request(**changes))
    assert store.manifest.read_bytes() == before


@pytest.mark.parametrize('changes', [{'source': 'different message'}, {'authorization': 'different quote'}, {'cycles': 3}])
def test_conflicting_replay_rejected_without_new_allowance(case, changes):
    spec, store = case
    exhaust(spec, store)
    store.continue_run(**request())
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='different authorization'):
        store.continue_run(**request(**changes))
    assert store.manifest.read_bytes() == before


def test_cannot_extend_unexhausted_run_and_concurrent_replays_grant_once(case):
    spec, store = case
    done(store, spec, run='run', cycle='c0')
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='exhausted'):
        store.continue_run(**request())
    assert store.manifest.read_bytes() == before
    for index in range(1, 5):
        spec['options'] = {'pass': index}
        done(store, spec, run='run', cycle=f'c{index}')
    args = request()
    command = cli('continue-run', '--run', args['run'], '--request-id', args['request_id'],
                  '--authorization', args['authorization'], '--source', args['source'],
                  '--cycles', str(args['cycles']))
    processes = []
    responses = []
    deadline = monotonic() + 60
    try:
        # Launch both callers before waiting; a blocked lock must fail locally.
        for _ in range(2):
            processes.append(subprocess.Popen(command, stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE, text=True))
        for process in processes:
            stdout, stderr = process.communicate(timeout=max(0, deadline - monotonic()))
            assert process.returncode == 0, stdout + stderr
            responses.append(json.loads(stdout))
    finally:
        # Kill all blocked callers before reaping any of them, including on spawn failure.
        for process in processes:
            if process.poll() is None:
                process.kill()
        for process in processes:
            process.communicate(timeout=10)
    assert sorted(r['action'] for r in responses) == ['continued', 'reuse']
    assert responses[0]['allowance'] == responses[1]['allowance']
    run = store.status([], run='run')['runs']['run']
    assert len(run['review_allowances']) == 1 and run['review_budget']['limit'] == 7
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='exhausted'):
        store.continue_run(**request(request_id='another-message'))
    assert store.manifest.read_bytes() == before
