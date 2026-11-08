"""Ordered native finalizer plans, external selections and retained task state."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import config
from state import Store, capture, write

REPOSITORY = 'github.com/owner/repo'
PR = 'https://github.com/owner/repo/pull/1'
FIRST = 'external:first'
LEFT = 'team:left'
RIGHT = 'team:right'
LAST = 'external:last'
PLAN = [FIRST, [LEFT, RIGHT], LAST]


def definition(tmp_path, reference):
    skill = reference.removeprefix('')
    resource = tmp_path/(skill.replace(':', '-')+'.md')
    if not resource.exists():
        resource.write_text('Fixture native skill. Return explicit completion and original evidence.\n')
    return {'skill_identity': skill, 'resources': {'skill': str(resource)},
            'assigned_instructions': 'Perform the selected finalizer. No remote writes.',
            'instructions': {}, 'inputs': {}, 'options': {'host': 'native'},
            'governing_inputs_complete': True}


def spec(tmp_path, reference=FIRST, run='run', **overrides):
    return config.make_finalizer_spec(
        repository=REPOSITORY, pr=PR, run=run, finalizer=reference,
        definition={**definition(tmp_path, reference), **overrides})


def finish(store, task, claim, *, complete=True):
    return store.record(task, claim['id'], b'Original finalizer evidence.\n',
                        {'complete': complete, 'findings': []},
                        'complete' if complete else 'incomplete')


def test_default_plan_uses_shared_invocations_and_keeps_defaults_immutable():
    original = config.effective(REPOSITORY)['settings']
    assert original['processes']['pr-fix']['finalizers'] == config.FINALIZER_DEFAULTS
    write(config.path(), {'skill_defaults': {'dk-workflows:dk-commit-split': {'options': {'max_passes': 2}}}})
    selected = config.effective(REPOSITORY)['settings']
    assert config.select(selected, 'pr-fix', 'finalizer', 0)['options'] == {'max_passes': 2}
    assert config.DEFAULTS['processes']['pr-fix']['finalizers'] == original['processes']['pr-fix']['finalizers']


def test_scoped_lists_replace_inherit_disable_and_include_external_skills_in_preflight():
    upstream = 'github.com/upstream/repo'
    write(config.path(), {'processes': {'pr-fix': {'finalizers': [FIRST]}}})
    write(config.path(upstream), {'processes': {'pr-fix': {'finalizers': PLAN}}})
    write(config.path(REPOSITORY), {})
    inherited = config.effective(REPOSITORY, upstream)['settings']
    assert inherited['processes']['pr-fix']['finalizers'] == PLAN
    assert config.finalizer_groups(PLAN) == [[FIRST], [LEFT, RIGHT], [LAST]]
    available = sorted({call['skill'] for _, call in config.calls(config.DEFAULTS) if 'skill' in call})
    assert config.preflight(inherited, available)['missing_selected_skills'] == ['external:first', 'external:last', 'team:left', 'team:right']
    assert config.preflight(inherited, available + ['external:first', 'external:last', 'team:left', 'team:right'])['ready']
    assert config.preflight(inherited, available, process='pr-review')['ready']
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': []}}})
    disabled = config.effective(REPOSITORY, upstream)['settings']
    assert disabled['processes']['pr-fix']['finalizers'] == []
    assert config.preflight(disabled, available)['ready']
    assert config.effective(REPOSITORY)['settings']['processes']['pr-fix']['finalizers'] == []
    config.path(REPOSITORY).unlink()
    assert config.effective(REPOSITORY)['settings']['processes']['pr-fix']['finalizers'] == [FIRST]
    assert config.effective(REPOSITORY, upstream)['settings']['processes']['pr-fix']['finalizers'] == PLAN


def test_default_plan_keeps_qualified_native_roles_through_public_cli(tmp_path):
    native = 'local:cleanup'
    reference = '' + native
    write(config.path(REPOSITORY), config.validate({'processes': {'pr-fix': {'finalizers': [native]}}}))
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPOSITORY]
    shown = subprocess.run([*command, 'show', '--expanded'], check=True, capture_output=True, text=True)
    settings = json.loads(shown.stdout)['settings']
    assert settings['processes']['pr-fix']['finalizers'][0] == reference
    available = tmp_path/'available.json'
    write(available, sorted({call['skill'] for _,call in config.calls(settings) if 'skill' in call}))
    for workflow in ('pr-fix', 'pr-review'):
        checked = subprocess.run([*command, 'preflight', '--available', str(available),
                                  '--process', workflow], check=True, capture_output=True, text=True)
        assert json.loads(checked.stdout)['ready']
    dk = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPOSITORY, '--pr', PR]
    planned = subprocess.run([*dk, 'finalizer-plan'], check=True, capture_output=True, text=True)
    assert json.loads(planned.stdout)['groups'][0] == [reference]
    selected = tmp_path/'definition.json'
    write(selected, definition(tmp_path, reference))
    generated = subprocess.run([*dk, 'finalizer-spec', '--run', 'run', '--finalizer', reference,
                                '--definition', str(selected)], check=True, capture_output=True, text=True)
    assert json.loads(generated.stdout)['skill']['identity'] == native
    config.validate({'processes': {'pr-fix': {'finalizers': [{'skill': reference, 'agent': {'model': 'chosen'}}]}}})


@pytest.mark.parametrize('value', [
    None, {}, FIRST, [None], [True], [3], [{'skill': '/skills/bad'}], [[]],
    [[[FIRST]]],
    ['/skills/unqualified'], [{'skill': 'team:first', 'agent': {'bad': True}}], ['/agent/team:first'], [':first'],
    ['team:'], ['team:first/extra'], ['team:first:extra'],
    ['team:first\n'], ['team:bad name'], ['team:first\x00'],
])
def test_invalid_format_is_rejected(value):
    with pytest.raises(ValueError):
        config.validate({'processes': {'pr-fix': {'finalizers': value}}})


def test_finalizer_claim_resume_recovery_and_new_run_isolation(tmp_path):
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': PLAN}}})
    store = Store(REPOSITORY, PR)
    task = spec(tmp_path)
    started = store.start(task, run='run')
    assert started['action'] == 'invoke'
    assert store.start(spec(tmp_path), run='run')['action'] == 'recover_required'
    with pytest.raises(ValueError, match='original run'):
        store.start(task, run='other', recover=started['claim']['id'])
    recovered = store.start(task, run='run', recover=started['claim']['id'])
    finish(store, task, recovered['claim'])
    assert store.start(spec(tmp_path), run='run')['action'] == 'reuse'
    other = spec(tmp_path, run='other')
    assert capture(task)['key'] != capture(other)['key']
    started = store.start(other, run='other')
    finish(store, other, started['claim'])
    for run in store.status([])['runs'].values():
        assert run['cycles'] == [] and run['retries'] == {}
        assert run['ci_repairs'] == {} and run['ci_reruns'] == {}


def test_plan_and_resource_drift_stale_evidence_without_hiding_unfinished_work(tmp_path):
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': PLAN}}})
    store = Store(REPOSITORY, PR)
    task = spec(tmp_path)
    first = store.start(task, run='run')
    original_key = capture(task)['key']
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': [LAST, [RIGHT, LEFT], FIRST]}}})
    changed = spec(tmp_path)
    assert capture(changed)['key'] == original_key
    with pytest.raises(ValueError, match='Inputs changed'):
        finish(store, changed, first['claim'])
    assert store.start(changed, run='run')['action'] == 'recover_required'
    recovered = store.start(changed, run='run', recover=first['claim']['id'])
    finish(store, changed, recovered['claim'])
    assert store.start(spec(tmp_path), run='run')['action'] == 'reuse'
    Path(changed['skill']['resources']['skill']).write_text('New governing instructions.\n')
    assert not store.status([spec(tmp_path)])['tasks'][0]['reusable']
    next_claim = store.start(spec(tmp_path), run='run')
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': []}}})
    with pytest.raises(ValueError, match='No configured finalizer'):
        spec(tmp_path)
    # Removing a finalizer cannot erase the prior task's pending effects.
    assert store.status([])['unfinished'][0]['id'] == next_claim['claim']['id']


def test_spec_captures_whole_plan_and_rejects_wrong_runner_or_assignment(tmp_path):
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': PLAN}}})
    task = spec(tmp_path)
    assert task['skill']['identity'] == 'external:first'
    assert task['options']['finalization'] == {'run': 'run', 'skill': 'external:first', 'plan': PLAN}
    assert task['options']['execution'] == {'host': 'native'}
    assert str(config.path(REPOSITORY)) in task['configuration']['sources']
    with pytest.raises(ValueError, match='configured invocation skill'):
        spec(tmp_path, skill_identity='team:wrong')
    with pytest.raises(ValueError, match='No configured finalizer'):
        spec(tmp_path, 'team:missing')
    with pytest.raises(ValueError, match='retained run'):
        spec(tmp_path, run=' ')
    with pytest.raises(ValueError, match='execution options'):
        spec(tmp_path, options=[])
    with pytest.raises(ValueError, match='Reserved invocation'):
        spec(tmp_path, resources={'invocation_contract': 'override'})
    with pytest.raises(ValueError, match='Nonempty assigned'):
        spec(tmp_path, assigned_instructions='')


def test_noncacheable_ci_finalizer_requires_new_live_observation(tmp_path):
    reference = 'dk-workflows:dk-ci'
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': [reference]}}})
    store = Store(REPOSITORY, PR)
    task = spec(tmp_path, reference, cacheable=False)
    started = store.start(task, run='run')
    finish(store, task, started['claim'])
    assert not store.status([task])['tasks'][0]['reusable']
    assert store.start(task, run='run')['action'] == 'invoke'


def test_instruction_only_finalizers_have_distinct_list_positions(tmp_path):
    plan = [{'instructions': 'Check scope.'}, {'instructions': 'Check publication receipts.'}]
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': plan}}})
    tasks = [config.make_finalizer_spec(repository=REPOSITORY, pr=PR, run='run', index=index,
        definition=definition(tmp_path, 'dk-workflows:dk-pr-fix')) for index in range(2)]
    assert capture(tasks[0])['key'] != capture(tasks[1])['key']
    assert all(task['task']['instructions'].endswith(call['instructions']) for task, call in zip(tasks, plan))


def test_public_cli_roundtrip_plan_spec_and_empty_plan(tmp_path):
    settings = tmp_path/'settings.json'
    write(settings, {'processes': {'pr-fix': {'finalizers': PLAN}}})
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPOSITORY]
    from config_request_admission import admitted
    request_id = admitted({'domain':'workflow', 'action':'set', 'value':json.loads(settings.read_text())}, repository=REPOSITORY)
    subprocess.run([*command, 'set', '--file', str(settings), '--request-id', request_id], check=True, capture_output=True)
    shown = subprocess.run([*command, 'show', '--expanded'], check=True, capture_output=True, text=True)
    assert json.loads(shown.stdout)['settings']['processes']['pr-fix']['finalizers'] == PLAN
    dk = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPOSITORY, '--pr', PR]
    planned = subprocess.run([*dk, 'finalizer-plan'], check=True, capture_output=True, text=True)
    assert json.loads(planned.stdout)['groups'] == [[FIRST], [LEFT, RIGHT], [LAST]]
    selected = tmp_path/'definition.json'
    write(selected, definition(tmp_path, FIRST))
    generated = subprocess.run([*dk, 'finalizer-spec', '--run', 'run', '--finalizer', FIRST,
                                '--definition', str(selected)], check=True, capture_output=True, text=True)
    assert json.loads(generated.stdout) == spec(tmp_path)
    missing = subprocess.run([*dk, 'finalizer-spec', '--run', 'run', '--finalizer', LAST,
                              '--definition', str(selected)], capture_output=True, text=True)
    assert missing.returncode == 2
    assert json.loads(missing.stdout)['status'] == 'blocked'
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': []}}})
    planned = subprocess.run([*dk, 'finalizer-plan'], check=True, capture_output=True, text=True)
    assert json.loads(planned.stdout)['groups'] == []


def test_parallel_group_executes_concurrently_and_failed_sibling_blocks_later_group(tmp_path):
    """A bounded coordinator stand-in uses real concurrent skill processes.

    Each parallel fixture waits for its sibling's launch marker, so a serial
    dispatch fails. Native/model invocation itself is outside this offline test.
    """
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'finalizers': PLAN}}})
    store = Store(REPOSITORY, PR)
    first = spec(tmp_path)
    started = store.start(first, run='run')
    finish(store, first, started['claim'])
    parallel = [spec(tmp_path, reference) for reference in (LEFT, RIGHT)]
    claims = store.start_batch(parallel, run='run')['tasks']
    worker = tmp_path/'worker.py'
    worker.write_text('''import json, sys, time
from pathlib import Path
root, name, sibling = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
(root / (name + '.started')).write_text('started')
deadline = time.monotonic() + 5
while not (root / (sibling + '.started')).exists():
    if time.monotonic() >= deadline:
        raise SystemExit('Sibling was not dispatched concurrently')
    time.sleep(0.01)
print(json.dumps({'complete': name != 'right', 'findings': []}))
''')
    processes = []
    try:
        for name, sibling in [('left', 'right'), ('right', 'left')]:
            processes.append(subprocess.Popen(
                [sys.executable, str(worker), str(tmp_path), name, sibling],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for task, claim, process in zip(parallel, claims, processes):
            output, error = process.communicate(timeout=10)
            assert process.returncode == 0, error
            result = json.loads(output)
            store.record(task, claim['claim']['id'], output.encode(), result,
                         'complete' if result['complete'] else 'incomplete')
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    coverage = store.status(parallel)['tasks']
    assert [task['reusable'] for task in coverage] == [True, False]
    last = spec(tmp_path, LAST)
    assert capture(last)['key'] not in json.loads(store.manifest.read_text())['entries']
    resumed = store.start_batch(parallel, run='run')['tasks']
    assert [task['action'] for task in resumed] == ['reuse', 'invoke']
    finish(store, parallel[1], resumed[1]['claim'])
    assert all(task['reusable'] for task in store.status(parallel)['tasks'])
    last_claim = store.start(last, run='run')
    finish(store, last, last_claim['claim'])
    assert all(task['reusable'] for task in store.status([first, *parallel, last])['tasks'])
    run = store.status([])['runs']['run']
    assert run['cycles'] == [] and run['retries'] == {} and run['ci_repairs'] == {} and run['ci_reruns'] == {}
