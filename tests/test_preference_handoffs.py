"""Task-specific preference handoffs use the ordinary task-state freshness gates."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import preferences
from state import Store, make_spec, write

REPO = 'github.com/owner/repo'
PR = 'https://github.com/owner/repo/pull/7'


@pytest.fixture
def workflow(tmp_path):
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    subprocess.run(['git', 'init', '-q', str(checkout)], check=True, timeout=15)
    subprocess.run(['git', '-C', str(checkout), 'remote', 'add', 'origin', 'https://github.com/owner/repo'], check=True, timeout=15)
    (checkout / 'code.py').write_text('VALUE = 2\n')
    value = {'owner': {'type': 'user'}, 'prompt': 'Check the settled return value.',
             'scope': {'include': [{'type': 'file', 'path': 'code.py'}], 'exclude': [],
                       'condition': 'When the task specifies a settled return value.'}}
    proposal = preferences.prepare(checkout, {'upsert': [value]})
    preferences.commit(checkout, proposal)
    preference_id = proposal['changes']['upsert'][0]['id']
    return checkout, preference_id, Store(REPO, PR, checkout=checkout)


def handoff(checkout, mode, paths, *, return_value=True):
    selection = preferences.resolve(checkout, mode, paths)
    # This explicit fixture coordinator evaluates the task's settled scope. The
    # production helper deliberately returns unevaluated condition candidates.
    return {'selection': selection, 'condition_context': {'settled_return_value': return_value},
            'decisions': [{'id': entry['record']['id'], 'applicable': return_value,
                           'paths': entry['paths'] if return_value else [],
                           'reason': 'Task specifies a return value.' if return_value else 'Task has no settled return value.'}
                          for entry in selection['records'] if entry['requires_condition']]}


def spec(tmp_path, responsibility, preference_input):
    source = tmp_path / (responsibility + '-preferences.json')
    write(source, preference_input)
    return make_spec(repository=REPO, pr=PR, skill_identity='fixture:' + responsibility,
                     resources={'entry': str(ROOT / 'tests/fixtures/recipe/reviewer.py'),
                                'preferences': str(ROOT / 'docs/preferences.md'),
                                'preference_runtime': str(ROOT / 'runtime/preferences.py')},
                     responsibility=responsibility, assigned_instructions='Perform the assigned task within the supplied scope.',
                     instructions={}, inputs={'preferences': str(source)}, options={}, governing_inputs_complete=True)


def complete(store, task):
    claim = store.start(task)['claim']
    store.record(task, claim['id'], b'Complete native stand-in report.', {'complete': True, 'findings': []}, 'complete')
    return claim


@pytest.mark.parametrize('responsibility,mode', [
    ('fix-feedback', 'implementation'), ('incoming-review', 'review'),
    ('standalone-review', 'review'), ('finding-verification', 'review'),
    ('direct-feedback', 'implementation'),
])
def test_downstream_process_receives_complete_scoped_records(workflow, tmp_path, responsibility, mode):
    checkout, preference_id, store = workflow
    selected = handoff(checkout, mode, ['code.py', 'unrelated.py'])
    task = spec(tmp_path, responsibility, selected)
    claim = store.start(task)['claim']
    output = tmp_path / 'native-output.json'
    subprocess.run([sys.executable, str(ROOT / 'tests/fixtures/recipe/reviewer.py'),
                    '--skill', responsibility, '--checkout', str(checkout), '--output', str(output),
                    '--log', str(tmp_path / 'launches.jsonl'), '--preferences', task['inputs']['preferences']], check=True, timeout=15)
    result = json.loads(output.read_text())
    assert result['preferences'] == selected
    assert result['preferences']['selection']['records'][0]['record']['id'] == preference_id
    assert result['preferences']['selection']['records'][0]['paths'] == ['code.py']
    assert result['preferences']['decisions'][0]['paths'] == ['code.py']
    store.record(task, claim['id'], output.read_bytes(), result, 'complete')
    # Identical fresh resolution and condition decisions preserve reuse.
    unchanged = spec(tmp_path, responsibility, handoff(checkout, mode, ['unrelated.py', 'code.py']))
    assert store.start(unchanged)['action'] == 'reuse'


def test_relevant_preferences_refresh_only_affected_work(workflow, tmp_path):
    checkout, preference_id, store = workflow
    a = spec(tmp_path, 'a', handoff(checkout, 'review', ['code.py']))
    b = spec(tmp_path, 'b', handoff(checkout, 'review', ['docs/readme.md']))
    complete(store, a)
    complete(store, b)
    unrelated = {'owner': {'type': 'user'}, 'prompt': 'Use explicit types.', 'applies_to': ['implementation'],
                 'scope': {'include': [{'type': 'file', 'path': 'code.py'}]}}
    preferences.commit(checkout, preferences.prepare(checkout, {'upsert': [unrelated]}))
    a = spec(tmp_path, 'a', handoff(checkout, 'review', ['code.py']))
    assert store.start(a)['action'] == 'reuse'
    record = preferences.show(checkout, preference_id)['record']
    record['prompt'] = 'Check the settled return value and its type.'
    preferences.commit(checkout, preferences.prepare(checkout, {'upsert': [record]}))
    a = spec(tmp_path, 'a', handoff(checkout, 'review', ['code.py']))
    b = spec(tmp_path, 'b', handoff(checkout, 'review', ['docs/readme.md']))
    assert store.status([a])['tasks'][0]['reusable'] is False
    assert store.start(b)['action'] == 'reuse'
    complete(store, a)
    preferences.remove(checkout, [preference_id], preferences.list_records(checkout)['revision'])
    a = spec(tmp_path, 'a', handoff(checkout, 'review', ['code.py']))
    assert store.status([a])['tasks'][0]['reusable'] is False


def test_changed_context_paths_and_decisions_refresh_task_coverage(workflow, tmp_path):
    checkout, _, store = workflow
    selected = handoff(checkout, 'review', ['code.py'])
    task = spec(tmp_path, 'review', selected)
    complete(store, task)
    # Isolate a revised decision against completed analysis with no active claim.
    accepted = deepcopy(selected)
    accepted['decisions'][0].update(applicable=False, paths=[], reason='Corrected applicability assessment.')
    decision_status = store.status([spec(tmp_path, 'review', accepted)])
    assert not decision_status['unfinished'] and not decision_status['tasks'][0]['reusable']
    task = spec(tmp_path, 'review', selected)
    assert store.status([task])['tasks'][0]['reusable']
    changed = handoff(checkout, 'review', ['code.py'], return_value=False)
    changed_task = spec(tmp_path, 'review', changed)
    assert not store.status([changed_task])['tasks'][0]['reusable']
    claim = store.start(changed_task)['claim']
    expanded = spec(tmp_path, 'review', handoff(checkout, 'review', ['code.py', 'new/file.py'], return_value=False))
    with pytest.raises(ValueError, match='changed'):
        store.record(expanded, claim['id'], b'Result for old scope.', {'complete': True, 'findings': []}, 'complete')
