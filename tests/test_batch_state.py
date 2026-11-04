"""Batch claims preserve individual task identity, recovery and retained budgets."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_state import case, done, git, require_git_context_paths, REPO, PR, ROOT
import state
from state import capture, read


def sibling(spec, name='b'):
    other = deepcopy(spec)
    other['skill']['identity'] = 'reviewer:' + name
    return other


def definition(spec):
    return dict(skill_identity=spec['skill']['identity'], resources=spec['skill']['resources'],
                responsibility=spec['task']['id'], assigned_instructions=spec['task']['instructions'],
                instructions=spec['instructions'], inputs=spec['inputs'], options=spec['options'],
                git=spec['git'], governing_inputs_complete=spec['governing_inputs_complete'])


def cli(*args):
    return [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR, *args]


@pytest.mark.parametrize('mode', ['content', 'history', 'head_sensitive'])
def test_generated_specs_match_existing_capture_and_reuse(case, mode):
    spec, store = case
    if mode != 'content':
        spec['git'][mode] = True
    fields = definition(spec)
    generated = state.make_spec(repository=REPO, pr=PR, **fields)
    assert capture(generated) == capture(spec)
    done(store, spec, run='standalone', cycle='c1')
    assert store.start_batch([generated], run='iteration', cycle='c1')['tasks'][0]['action'] == 'reuse'
    assert 'iteration' not in store.status([])['runs']
    generated['options']['changed'] = True
    assert fields['options'] == {}
    keys = []
    for responsibility in ('scope-partition', 'series-cleanup'):
        fields['responsibility'] = responsibility
        keys.append(capture(state.make_spec(repository=REPO, pr=PR, **fields))['key'])
    assert len(set(keys)) == 2


def test_generation_keeps_uncertain_inputs_noncacheable_and_cli_has_no_state(case, tmp_path):
    spec, store = case
    fields = definition(spec)
    fields.pop('governing_inputs_complete')
    generated = state.make_spec(repository=REPO, pr=PR, **fields)
    assert capture(generated)['cacheable'] is False
    with pytest.raises(ValueError):
        state.make_spec(repository=REPO, pr=PR, cacheable=True, **fields)
    fields['resources'] = {}
    fields['governing_inputs_complete'] = True
    assert not state.make_spec(repository=REPO, pr=PR, **fields)['cacheable']
    path = tmp_path/'definition.json'
    path.write_text(json.dumps(fields))
    generated = json.loads(subprocess.check_output(cli('spec', '--definition', str(path)), text=True))
    assert generated == state.make_spec(repository=REPO, pr=PR, **fields)
    assert not store.manifest.exists()


def test_batch_returns_each_outcome_and_records_successful_siblings(case):
    a, store = case
    b, c = sibling(a), sibling(a, 'c')
    done(store, a)
    old = store.start(b, run='old', cycle='c1')['claim']
    result = store.start_batch([a, b, c], run='run', cycle='c1')
    assert [t['action'] for t in result['tasks']] == ['reuse', 'recover_required', 'invoke']
    assert [t['key'] for t in result['tasks']] == [capture(s)['key'] for s in (a, b, c)]
    assert result['tasks'][0]['result'] and result['tasks'][1]['unfinished'] == old
    claim = result['tasks'][2]['claim']
    assert {t['id'] for t in result['unfinished']} == {old['id'], claim['id']}
    store.record(c, claim['id'], b'success', {'complete': True, 'findings': [{'id': 'f1'}]}, 'complete')
    assert store.start_batch([c])['tasks'][0]['findings'] == [{'id': 'f1'}]
    assert store.status([])['runs']['run']['cycles'] == ['c1']
    assert store.status([])['unfinished'] == [old]


@pytest.mark.parametrize('problem', ['missing', 'invalid', 'subject', 'duplicate', 'recover', 'unused-recover'])
def test_invalid_batch_leaves_existing_claims_and_manifest_untouched(case, problem):
    a, store = case
    existing = store.start(a, run='old', cycle='c1')['claim']
    b, c = sibling(a), sibling(a, 'c')
    recover = None
    if problem == 'missing':
        c['inputs']['context'] += '.missing'
    elif problem == 'invalid':
        c['task']['instructions'] = ''
    elif problem == 'subject':
        c['pr'] = PR.replace('/7', '/8')
    elif problem == 'duplicate':
        c = deepcopy(b)
    elif problem == 'recover':
        recover = {capture(c)['key']: 'absent-claim'}
    else:
        recover = {'unknown-key': existing['id']}
    before = store.manifest.read_bytes()
    with pytest.raises((ValueError, OSError)):
        store.start_batch([b, c], recover=recover, run='run', cycle='c1')
    assert store.manifest.read_bytes() == before
    assert store.status([])['unfinished'] == [existing]


def test_concurrent_cli_batches_cannot_duplicate_invocations(case, tmp_path):
    a, store = case
    specs = [a, sibling(a)]
    args = ['start-batch', '--run', 'run', '--cycle', 'c1']
    for index, spec in enumerate(specs):
        path = tmp_path/f'spec-{index}.json'
        path.write_text(json.dumps(spec))
        args += ['--spec', str(path)]
    def launch(_):
        return json.loads(subprocess.check_output(cli(*args), text=True, timeout=60))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch, range(2)))
    assert sorted([t['action'] for t in r['tasks']] for r in results) == [
        ['invoke', 'invoke'], ['recover_required', 'recover_required']]
    assert len(store.status([])['unfinished']) == 2
    assert store.status([])['runs']['run']['cycles'] == ['c1']


def test_batch_recovery_retains_run_cycle_and_successful_results(case):
    a, store = case
    b = sibling(a)
    tasks = store.start_batch([a, b], run='run', cycle='c1')['tasks']
    store.record(a, tasks[0]['claim']['id'], b'original', {'complete': True}, 'complete')
    claim = tasks[1]['claim']
    recovery = {claim['key']: claim['id']}
    before = store.manifest.read_bytes()
    for args in ({'run': 'new', 'cycle': 'c1'}, {'run': 'run', 'cycle': 'c2'}):
        with pytest.raises(ValueError, match='Recovery must retain'):
            store.start_batch([a, b], recover=recovery, **args)
        assert store.manifest.read_bytes() == before
    recovered = store.start_batch([a, b], recover=recovery, run='run', cycle='c1')['tasks']
    assert [t['action'] for t in recovered] == ['reuse', 'invoke']
    assert recovered[1]['claim']['id'] != claim['id']
    entry = read(store.manifest)['entries'][claim['key']]
    assert entry['attempts'][0]['status'] == 'interrupted'
    assert store.status([])['runs']['run']['cycles'] == ['c1']


@pytest.mark.parametrize('budget', ['cycles', 'retry', 'ci_repair', 'ci_rerun', 'changed'])
def test_late_batch_budget_failure_rolls_back_earlier_claims_and_counters(case, budget):
    a, store = case
    b = sibling(a)
    if budget == 'cycles':
        for index in range(5):
            a['options'] = {'pass': index}
            done(store, a, run='run', cycle=f'c{index}')
        kwargs = {'cycle': 'c5'}
    elif budget == 'retry':
        for spec in (a, b):
            done(store, spec, run='run', cycle='c1')
            store.invalidate([capture(spec)['key']], 'retry')
        done(store, b, run='run', cycle='c1', retry=True)
        store.invalidate([capture(b)['key']], 'retry exhausted')
        kwargs = {'cycle': 'c1', 'retry': True}
    elif budget in {'ci_repair', 'ci_rerun'}:
        a['cacheable'] = b['cacheable'] = False
        kwargs = {'ci_repair': True} if budget == 'ci_repair' else {'ci_rerun': True, 'head': a['git']['head']}
        for _ in range(4 if budget == 'ci_repair' else 2):
            done(store, a, run='run', **kwargs)
    else:
        done(store, b, run='run', cycle='c1')
        b['options']['changed'] = True
        kwargs = {'cycle': 'c1'}
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError):
        store.start_batch([a, b], run='run', **kwargs)
    assert store.manifest.read_bytes() == before
    assert store.status([])['unfinished'] == []


@pytest.mark.parametrize('kind', ['resource', 'instruction', 'input', 'assigned', 'options',
                                  'mutable-ref', 'git-options', 'git-context'])
def test_batch_captures_each_task_freshly_even_when_noncacheable(case, monkeypatch, kind):
    a, store = case
    b = sibling(a)
    a['cacheable'] = b['cacheable'] = False
    if kind == 'mutable-ref':
        git(a['git']['checkout'], 'branch', 'moving', a['git']['head'])
        a['git']['head'] = b['git']['head'] = 'moving'
    before = capture(a)
    def specs():
        yield a
        if kind == 'resource':
            Path(a['skill']['resources']['entry']).write_text('changed reviewer')
        elif kind == 'instruction':
            Path(a['instructions']['user']).write_text('changed instruction')
        elif kind == 'input':
            Path(a['inputs']['context']).write_text('changed context')
        elif kind == 'assigned':
            b['task']['instructions'] += ' New responsibility.'
        elif kind == 'options':
            b['options'] = {'strict': True}
        elif kind == 'mutable-ref':
            git(a['git']['checkout'], 'branch', '-f', 'moving', a['git']['base'])
        elif kind == 'git-options':
            b['git']['history'] = True
        else:
            git(a['git']['checkout'], 'config', 'diff.noprefix', 'true')
        yield b
    observed = []
    original = state.capture
    def collect(spec, **kwargs):
        value = original(spec, **kwargs)
        observed.append(value)
        return value
    monkeypatch.setattr(state, 'capture', collect)
    result = store.start_batch(specs())
    assert observed[0] == before
    assert observed[1] == capture(b)
    assert observed[0]['components']['inputs'] != observed[1]['components']['inputs'] or kind in {
        'resource', 'instruction', 'assigned', 'options'}
    assert all(t['action'] == 'invoke' and not t['claim']['cacheable'] for t in result['tasks'])


def test_record_and_final_status_refresh_after_batch_capture(case):
    a, store = case
    b = sibling(a)
    tasks = store.start_batch([a, b])['tasks']
    store.record(a, tasks[0]['claim']['id'], b'success', {'complete': True}, 'complete')
    Path(a['inputs']['context']).write_text('new governing context')
    with pytest.raises(ValueError, match='changed during'):
        store.record(b, tasks[1]['claim']['id'], b'stale', {'complete': True}, 'complete')
    status = store.status([a, b])
    assert not any(t['reusable'] for t in status['tasks'])
    assert status['unfinished'] == [tasks[1]['claim']]


@pytest.mark.parametrize('mode', ['content', 'history', 'head_sensitive'])
def test_batch_preserves_independent_fingerprints_provenance_and_cli_recovery(case, tmp_path, mode):
    a, store = case
    if mode != 'content':
        a['git'][mode] = True
    specs = [a, sibling(a)]
    expected = [capture(spec) for spec in specs]
    result = store.start_batch(specs, run='run', cycle='c1')
    entries = read(store.manifest)['entries']
    for task, captured in zip(result['tasks'], expected):
        claim = task['claim']
        assert (claim['key'], claim['fingerprint'], claim['provenance']) == (
            captured['key'], captured['fingerprint'], captured['provenance'])
        assert entries[claim['key']]['components'] == captured['components']
    path = tmp_path/'recovery-spec.json'
    path.write_text(json.dumps(a))
    claim = result['tasks'][0]['claim']
    recovered = json.loads(subprocess.check_output(cli(
        'start-batch', '--spec', str(path), '--run', 'run', '--cycle', 'c1',
        '--recover', claim['key']+'='+claim['id']), text=True, timeout=60))
    assert recovered['tasks'][0]['action'] == 'invoke'
    assert recovered['tasks'][0]['claim']['id'] != claim['id']
    assert result['tasks'][1]['claim'] in recovered['unfinished']


@pytest.mark.parametrize('linked', [False, True])
def test_start_batch_process_count_comparison(case, tmp_path, monkeypatch, linked):
    a, store = case
    require_git_context_paths(a['git']['checkout'])
    if linked:
        checkout = tmp_path/'linked'
        git(a['git']['checkout'], 'worktree', 'add', '--detach', '-q', str(checkout), a['git']['head'])
        a['git']['checkout'] = str(checkout)
    specs = [a, sibling(a)]
    for spec in specs:
        done(store, spec)
    before = store.manifest.read_bytes()
    calls = []
    original = state.git
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(state, 'git', counted)
    def probe(batch):
        calls.clear()
        assert all(t['reusable'] for t in store.status(specs)['tasks'])
        tasks = store.start_batch(specs)['tasks'] if batch else [store.start(s) for s in specs]
        assert all(t['action'] == 'reuse' for t in tasks)
        assert all(t['reusable'] for t in store.status(specs)['tasks'])
        assert store.manifest.read_bytes() == before
        return len(calls)
    with monkeypatch.context() as independent:
        independent.setattr(state._CaptureContext, 'git', lambda self, source: state._capture_git(source))
        independent = probe(False)
    status_only = probe(False)
    batched = probe(True)
    assert (independent, status_only, batched) == (42, 28, 21)
    print(f'Git processes: independent={independent}, status-only={status_only}, batch-start={batched}; '
          'helper commands: single-start=4, batch-start=3')
