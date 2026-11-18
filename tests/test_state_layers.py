"""Exact pre-extraction identity/capture and retained-state recovery contracts."""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import state
import task_capture
import task_policy
import task_primitives
import task_runtime
import task_storage
import task_state_layout as layout
from tasks import Tasks

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'tests/fixtures/task-state-v2.json'


@pytest.fixture
def retained(tmp_path, monkeypatch):
    # Freeze capture conditions as well as repository-construction conditions.
    for name in list(os.environ):
        if name.startswith('GIT_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    root = tmp_path/'sources'
    layout.sources(root)
    monkeypatch.chdir(root)
    specs = layout.specifications(state, root)
    fixture = json.loads(FIXTURE.read_text())
    store = state.Store(layout.REPOSITORY, layout.PR)
    for name, content in fixture['files'].items():
        target = store.root/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        target.chmod(0o600)
    return specs, store, fixture


def snapshot(store):
    return {str(p.relative_to(store.root)): p.read_bytes() for p in store.root.rglob('*.json')}


def test_frozen_pre_extraction_captures_match_exactly(retained):
    specs, _, fixture = retained
    assert fixture['source_revision'] == 'b9122f3a7f8a61cfad95ee4acfad39dad95d7707'
    for name, spec in specs.items():
        actual = state.capture(spec)
        expected = deepcopy(fixture['captures'][name])
        if expected['provenance']:
            expected['provenance']['checkout'] = str(Path(spec['git']['checkout']).resolve())
        assert actual == expected, name  # Includes exact key, components, fingerprint and provenance.
    assert fixture['captures']['old-assessment']['key'] == fixture['captures']['new-assessment']['key']


def test_fresh_process_reopens_original_results_and_counters(retained, tmp_path):
    specs, store, _ = retained
    before = snapshot(store)
    source = tmp_path/'specs.json'
    source.write_text(json.dumps([specs[n] for n in ('complete', 'failed', 'pending', 'incomplete', 'legacy', 'managed')]))
    program = ('import json,sys; from state import Store; '
               'v=json.load(open(sys.argv[1])); s=Store(v[0]["repository"],v[0]["pr"]); '
               'print(json.dumps(s.status(v,compact=True,explain=True)))')
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'runtime')}
    for _ in range(2):
        result = json.loads(subprocess.check_output([sys.executable, '-c', program, str(source)], env=env, text=True, timeout=15))
        tasks = {v['task']: v for v in result['tasks']}
        assert tasks['complete']['reusable'] and tasks['legacy']['reusable'] and tasks['managed']['reusable']
        assert tasks['complete']['findings'] == [{'id': 'original', 'text': 'Retained finding'}]
        assert tasks['failed']['completion'] == 'failed' and tasks['incomplete']['completion'] == 'incomplete'
        assert tasks['pending']['reason'] == 'unfinished'
        assert result['runs']['retained']['review_budget'] == {'used': 5, 'limit': 5, 'remaining': 0}
    assert snapshot(store) == before
    # Low-level definitions and the original managed descriptor remain reusable.
    assert store.start(specs['legacy'])['action'] == 'reuse'
    managed = Tasks(layout.REPOSITORY, layout.PR).prepare({'tasks': [{'key': state.capture(specs['managed'])['key']}]})
    assert managed['tasks'][0]['action'] == 'reuse'
    expected = json.loads(before['manifest.json'])
    key = state.capture(specs['managed'])['key']
    # Existing prepare normalizes relative legacy source paths, without replacing
    # original results, fingerprints, claims, attempts or allowance history.
    definition = expected['entries'][key]['managed']['definition']
    for field in ('resources', 'instructions', 'inputs'):
        definition[field] = {name: str(Path(path).absolute()) for name, path in definition[field].items()}
    assert json.loads(store.manifest.read_text()) == expected
    after = snapshot(store)
    assert {k: v for k, v in after.items() if k != 'manifest.json'} == {k: v for k, v in before.items() if k != 'manifest.json'}
    assert Tasks(layout.REPOSITORY, layout.PR).prepare({'tasks': [{'key': key}]})['tasks'][0]['action'] == 'reuse'
    assert snapshot(store) == after


@pytest.mark.parametrize('name,reason', [('missing', 'missing_result_artifact'), ('damaged', 'damaged_result')])
def test_missing_or_damaged_original_is_never_reusable(retained, name, reason):
    specs, store, _ = retained
    before = snapshot(store)
    task = store.status([specs[name]], explain=True)['tasks'][0]
    assert not task['reusable'] and reason in task['explanation']['blockers']
    assert snapshot(store) == before


def test_lost_claim_response_and_explicit_recovery_preserve_siblings(retained):
    specs, store, _ = retained
    before = snapshot(store)
    original = store.status([specs['pending']])['unfinished'][0]
    response = store.start(specs['pending'], run='retained', cycle='cycle-2')
    assert response['action'] == 'recover_required' and response['unfinished'] == original
    assert snapshot(store) == before
    recovered = store.start(specs['pending'], recover=original['id'], run='retained', cycle='cycle-2')
    assert recovered['action'] == 'invoke' and recovered['claim']['id'] != original['id']
    assert store.start(specs['complete'])['action'] == 'reuse'
    assert store.start(specs['managed'])['action'] == 'reuse'
    assert store.status([])['runs']['retained']['cycles'] == ['cycle-'+str(n) for n in range(1, 6)]
    for name, content in before.items():
        if name != 'manifest.json':
            assert (store.root/name).read_bytes() == content


@pytest.mark.parametrize('field', ['resource', 'instruction', 'input', 'option'])
def test_relevant_changes_refresh_affected_work_and_reject_drift(retained, field):
    specs, store, _ = retained
    first = specs['complete']
    second = deepcopy(first)
    second['task']['id'] = 'independent'
    second['skill']['resources'] = {'skill': 'independent.md'}
    second['instructions'] = {'user': 'independent.md'}
    second['inputs'] = {'context': 'independent.md'}
    Path('independent.md').write_text('Independent original source')
    claim = store.start(second)['claim']
    store.record(second, claim['id'], b'Independent original', {'complete': True, 'findings': []}, 'complete')
    if field == 'resource': Path('skill.md').write_text('Changed skill')
    elif field == 'instruction': Path('user.txt').write_text('Changed instruction')
    elif field == 'input': Path('context.txt').write_text('Changed evidence')
    else: first['options']['changed'] = True
    assert not store.status([first])['tasks'][0]['reusable']
    assert store.start(second)['action'] == 'reuse'
    claim = store.start(first)['claim']
    first['task']['instructions'] += ' Changed during execution.'
    with pytest.raises(ValueError, match='Inputs changed during execution'):
        store.record(first, claim['id'], b'Stale report', {'complete': True, 'findings': []}, 'complete')
    assert store.start(second)['action'] == 'reuse'


def test_pre_extraction_exhaustion_survives_and_does_not_write(retained):
    specs, store, _ = retained
    before = snapshot(store)
    for name in ('failed', 'incomplete'):
        with pytest.raises(ValueError, match='cycle limit'):
            store.start(specs[name], run='retained', cycle='sixth')
        assert snapshot(store) == before


def test_concurrent_claim_still_has_one_winner(retained):
    specs, store, _ = retained
    spec = deepcopy(specs['complete'])
    spec['task']['id'] = 'concurrent'
    # Bound the whole worker process so a locking regression cannot hang pytest
    # during executor shutdown; a future timeout alone would still wait there.
    program = '''import json,sys
from concurrent.futures import ThreadPoolExecutor
from state import Store
spec=json.loads(sys.argv[1])
store=Store(spec['repository'],spec['pr'])
with ThreadPoolExecutor(max_workers=6) as pool:
    results=list(pool.map(lambda _: store.start(spec),range(6)))
print(json.dumps(results))
'''
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'runtime')}
    results = json.loads(subprocess.check_output(
        [sys.executable, '-c', program, json.dumps(spec)], env=env, text=True, timeout=15))
    assert [v['action'] for v in results].count('invoke') == 1
    assert [v['action'] for v in results].count('recover_required') == 5
    claims = [v.get('claim', v.get('unfinished'))['id'] for v in results]
    assert len(set(claims)) == 1
    assert store.manifest.stat().st_mode & 0o777 == 0o600
    assert (store.root/'.lock').stat().st_mode & 0o777 == 0o600


def test_storage_and_raw_capture_do_not_load_workflow_policy(tmp_path):
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'runtime')}
    program = ('import sys,task_storage,task_capture; '
               'blocked={"state","tasks","task_runtime","task_policy","config","skill_config"}; '
               'assert not blocked.intersection(sys.modules),blocked.intersection(sys.modules)')
    subprocess.check_call([sys.executable, '-c', program], cwd=tmp_path, env=env, timeout=15)
    # Policy alone may construct/capture without loading the storage/runtime layer.
    subprocess.check_call([sys.executable, '-c', 'import task_policy,sys; assert not {"task_storage","task_runtime","state","tasks"}.intersection(sys.modules)'], cwd=tmp_path, env=env, timeout=15)
    for module in (task_primitives, task_capture, task_storage):
        imports = {n.module for n in ast.walk(ast.parse(Path(module.__file__).read_text())) if isinstance(n, ast.ImportFrom)}
        assert not {'state', 'config', 'skill_config', 'task_policy', 'task_runtime', 'tasks'} & imports


def test_compatibility_imports_point_to_the_single_implementation():
    assert state.Store is task_runtime.Store
    assert issubclass(state.Store, task_storage.TaskStorage)
    assert state.capture is task_policy.capture
    assert state.make_spec is task_policy.make_spec
    assert state.resource is task_capture.resource
    assert state.digest is task_primitives.digest


@pytest.mark.parametrize('source', ['global', 'environment'])
def test_frozen_contract_ignores_external_git_configuration(tmp_path, source):
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    environment['PYTHONPATH'] = str(ROOT/'runtime')
    if source == 'global':
        home = tmp_path/'external-home'
        home.mkdir()
        (home/'.gitconfig').write_text('[diff]\n\tnoprefix = true\n')
        environment['HOME'] = str(home)
    else:
        environment.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='diff.noprefix',
                           GIT_CONFIG_VALUE_0='true')
    # Invoke only the frozen comparison, so this regression cannot recurse.
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q',
         str(Path(__file__).resolve())+'::test_frozen_pre_extraction_captures_match_exactly',
         '--basetemp', str(tmp_path/'nested'), '-p', 'no:cacheprovider'],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
