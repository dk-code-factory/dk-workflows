"""List-position hook receipts survive interruption without user-supplied IDs."""
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
RUNNER = 'dk-workflows:dk-pr-fix'


def hook(**values):
    return {'instructions': '  Read Decisions.\nPreserve constraints.\n', **values}


def settings(**phases):
    return {'processes': {'pr-fix': {'hooks': phases}}}


def test_hook_phases_inherit_separately_and_empty_list_disables():
    write(config.path(), settings(before_cycle=[hook(skill='team:context')], after_cycle=[hook()]))
    upstream = 'github.com/upstream/repo'
    write(config.path(upstream), settings(after_cycle=[hook(instructions='Audit.')]))
    write(config.path(REPOSITORY), settings(before_cycle=[]))
    effective = config.effective(REPOSITORY, upstream)['settings']
    assert effective['processes']['pr-fix']['hooks'] == {
        'before_cycle': [], 'after_cycle': [hook(instructions='Audit.')]}
    assert config.DEFAULTS['processes']['pr-fix']['hooks'] == {'before_cycle': [], 'after_cycle': []}
    available = sorted({call['skill'] for _, call in config.calls(effective) if 'skill' in call})
    assert config.preflight(effective, available)['ready']
    config.path(REPOSITORY).unlink()
    inherited = config.effective(REPOSITORY, upstream)['settings']
    assert config.preflight(inherited, available)['missing_selected_skills'] == ['team:context']
    assert config.preflight(inherited, available + ['team:context'])['ready']


@pytest.mark.parametrize('hooks', [
    None, [], {'before_cleanup': []}, {'before_cycle': 'Read context'},
    {'before_cycle': [None]}, {'before_cycle': [hook(id='old-id')]},
    {'before_cycle': [hook(instructions='  ')]},
    {'before_cycle': [hook(instructions='text\x00')]},
    {'before_cycle': [hook(skill='/skills/team-review')]},
    {'before_cycle': [hook(skill=False)]}, {'before_cycle': [hook(options=[])]},
    {'before_cycle': [hook(command=['test'])]},
])
def test_malformed_hooks_are_rejected(hooks):
    with pytest.raises(ValueError):
        config.validate({'processes': {'pr-fix': {'hooks': hooks}}})


@pytest.fixture
def configured(tmp_path):
    write(config.path(REPOSITORY), settings(
        before_cycle=[hook()], after_cycle=[hook(skill='team:audit', options={'focus': ['invariants']})]))
    resource = tmp_path/'SKILL.md'
    resource.write_text('Return original evidence and explicit completion.\n')
    return {'skill_identity': RUNNER, 'resources': {'skill': str(resource)},
            'instructions': {}, 'inputs': {}, 'options': {'host': 'native'},
            'governing_inputs_complete': True}


def define(definition, *, run='run', cycle='c1', phase='before_cycle', index=0):
    return config.make_hook_spec(repository=REPOSITORY, pr=PR, run=run, cycle=cycle,
                                 phase=phase, index=index, definition=definition)


def finish(store, spec, claim, status='complete'):
    store.record(spec, claim['id'], b'Original native hook output.\n',
                 {'complete': status == 'complete', 'findings': []}, status)


def test_hook_resume_cycle_and_run_isolation_without_review_budget(configured):
    store, spec = Store(REPOSITORY, PR), define(configured)
    first = store.start(spec, run='run')
    assert first['action'] == 'invoke'
    assert store.start(spec, run='run')['action'] == 'recover_required'
    with pytest.raises(ValueError, match='original run'):
        store.start(spec, run='replacement', recover=first['claim']['id'])
    resumed = store.start(spec, run='run', recover=first['claim']['id'])
    finish(store, spec, resumed['claim'])
    assert store.start(define(configured), run='run')['action'] == 'reuse'
    after = define({**configured, 'skill_identity': 'team:audit'}, phase='after_cycle')
    for occurrence, run in [(after, 'run'), (define(configured, cycle='c2'), 'run'),
                            (define(configured, run='next'), 'next')]:
        started = store.start(occurrence, run=run)
        assert started['action'] == 'invoke'
        finish(store, occurrence, started['claim'])
    assert store.start(spec, run='run')['action'] == 'reuse'
    for run in store.status([])['runs'].values():
        assert run['cycles'] == [] and run['retries'] == {}
        assert run['ci_repairs'] == {} and run['ci_reruns'] == {}


def test_failed_hook_and_changed_guidance_cannot_reuse(configured):
    store, spec = Store(REPOSITORY, PR), define(configured)
    started = store.start(spec, run='run')
    finish(store, spec, started['claim'], status='incomplete')
    assert not store.status([spec])['tasks'][0]['reusable']
    retry = store.start(spec, run='run')
    finish(store, spec, retry['claim'])
    assert store.start(spec, run='run')['action'] == 'reuse'
    Path(configured['resources']['skill']).write_text('Changed governing guidance.\n')
    assert not store.status([spec])['tasks'][0]['reusable']


def test_changed_lists_require_reconciliation_and_active_claim_recovery(configured):
    store, spec = Store(REPOSITORY, PR), define(configured)
    started = store.start(spec, run='run')
    write(config.path(REPOSITORY), settings(before_cycle=[hook(instructions='New task.')]))
    with pytest.raises(ValueError, match='Inputs changed'):
        finish(store, spec, started['claim'])
    changed = define(configured)
    with pytest.raises(ValueError, match='Hook list changed'):
        store.start(changed, run='run')
    observed = store.status([])['hook_phases'][0]['snapshot']
    with pytest.raises(ValueError, match='active hook claims'):
        store.reconcile_hooks(changed['options']['hook_phase'], observed, 'Inspected prior effects.')
    store.invalidate([started['claim']['key']], 'Stopped prior agent and reconciled its effects.',
                     recover=started['claim']['id'])
    with pytest.raises(ValueError, match='snapshot changed'):
        store.reconcile_hooks(changed['options']['hook_phase'], 'outdated', 'Inspected prior effects.')
    store.reconcile_hooks(changed['options']['hook_phase'], observed, 'New instructions need a deliberate invocation.')
    assert store.start(changed, run='run')['action'] == 'invoke'


def test_unrelated_configuration_change_does_not_repeat_a_completed_hook(configured):
    store, spec = Store(REPOSITORY, PR), define(configured)
    started = store.start(spec, run='run')
    finish(store, spec, started['claim'])
    value = json.loads(config.path(REPOSITORY).read_text())
    value['extensions'] = {'unrelated': 'new setting'}
    write(config.path(REPOSITORY), value)
    assert store.start(define(configured), run='run')['action'] == 'reuse'


def test_reconciling_a_later_hook_edit_preserves_an_unchanged_completed_position(configured):
    write(config.path(REPOSITORY), settings(before_cycle=[hook(), hook(instructions='Original later task.')]))
    store, first = Store(REPOSITORY, PR), define(configured)
    started = store.start(first, run='run')
    finish(store, first, started['claim'])
    observed = store.status([])['hook_phases'][0]['snapshot']
    write(config.path(REPOSITORY), settings(before_cycle=[hook(), hook(instructions='Updated later task.')]))
    refreshed = define(configured)
    with pytest.raises(ValueError, match='Hook list changed'):
        store.start(refreshed, run='run')
    store.reconcile_hooks(refreshed['options']['hook_phase'], observed,
                          'The completed first hook is unchanged; only the unstarted second hook changed.')
    assert store.start(refreshed, run='run')['action'] == 'reuse'
    assert store.start(define(configured, index=1), run='run')['action'] == 'invoke'


def test_removing_all_hooks_can_be_explicitly_reconciled(configured):
    store, spec = Store(REPOSITORY, PR), define(configured)
    started = store.start(spec, run='run')
    finish(store, spec, started['claim'])
    observed = store.status([])['hook_phases'][0]['snapshot']
    write(config.path(REPOSITORY), settings(before_cycle=[]))
    result = json.loads(subprocess.check_output([
        sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPOSITORY, '--pr', PR,
        'reconcile-hooks', '--run', 'run', '--cycle', 'c1', '--phase', 'before_cycle',
        '--expected', observed, '--reason', 'Prior effects inspected; no hooks remain.'], text=True))
    assert result['reconciled'] and store.status([])['runs']['run']['cycles'] == []
    assert 'hook_reconciliations' not in json.loads(store.manifest.read_text())


def test_identical_hooks_have_distinct_positions(configured):
    write(config.path(REPOSITORY), settings(before_cycle=[hook(), hook()]))
    first, second = define(configured), define(configured, index=1)
    assert capture(first)['key'] != capture(second)['key']
    store = Store(REPOSITORY, PR)
    for spec in (first, second):
        started = store.start(spec, run='run')
        assert started['action'] == 'invoke'
        finish(store, spec, started['claim'])
    assert [store.start(spec, run='run')['action'] for spec in (first, second)] == ['reuse', 'reuse']


def test_hook_spec_preserves_prompt_options_and_selected_runner(configured):
    selected = {**configured, 'skill_identity': 'team:audit'}
    spec = define(selected, phase='after_cycle')
    assert spec['task']['instructions'] == hook()['instructions']
    assert spec['options']['configured'] == {'focus': ['invariants']}
    assert spec['options']['execution'] == {'host': 'native'}
    assert config.path(REPOSITORY).as_posix() in spec['configuration']['sources']
    with pytest.raises(ValueError, match='configured invocation skill'):
        define(configured, phase='after_cycle')
    with pytest.raises(ValueError, match='owning process'):
        define(selected)
    with pytest.raises(ValueError, match='execution options'):
        define({**configured, 'options': []})
    with pytest.raises(ValueError, match='No configured invocation'):
        define(configured, index=99)


def test_config_and_hook_spec_cli_roundtrip(configured, tmp_path):
    configuration = settings(before_cycle=[hook(), 'team:audit'])
    source = tmp_path/'settings.json'
    write(source, configuration)
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPOSITORY]
    from config_request_admission import admitted
    request_id = admitted({'domain':'workflow', 'action':'set', 'value':configuration}, repository=REPOSITORY)
    result = subprocess.run([*command, 'set', '--file', str(source), '--request-id', request_id], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    shown = subprocess.run([*command, 'show'], text=True, capture_output=True, check=True)
    assert json.loads(shown.stdout)['settings'] == configuration
    dk = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPOSITORY, '--pr', PR]
    specs = []
    for index, identity in enumerate((RUNNER, 'team:audit')):
        definition_path = tmp_path/f'definition-{index}.json'
        write(definition_path, {**configured, 'skill_identity': identity})
        result = subprocess.run([*dk, 'hook-spec', '--definition', str(definition_path),
                                 '--run', 'cli-run', '--cycle', 'c1', '--phase', 'before_cycle',
                                 '--index', str(index)], text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        spec = json.loads(result.stdout)
        specs.append(spec)
        path = tmp_path/f'hook-{index}.json'
        write(path, spec)
        started = subprocess.run([*dk, 'start', '--spec', str(path), '--run', 'cli-run'],
                                 text=True, capture_output=True, check=True)
        finish(Store(REPOSITORY, PR), spec, json.loads(started.stdout)['claim'])
    store = Store(REPOSITORY, PR)
    assert [store.start(spec, run='cli-run')['action'] for spec in specs] == ['reuse', 'reuse']
