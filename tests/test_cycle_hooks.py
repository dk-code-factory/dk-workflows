"""Cycle-hook configuration and native task receipts across resume boundaries."""
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


def hook(hook_id='context', **values):
    return {'id': hook_id, 'instructions': '  Read Decisions.\nPreserve constraints.\n', **values}


def test_hook_phases_inherit_separately_and_empty_list_disables():
    write(config.path(), {'cycle_hooks': {
        'before_cycle': [hook(skill='team:context')], 'after_cycle': [hook('report')]}})
    upstream = 'github.com/upstream/repo'
    write(config.path(upstream), {'cycle_hooks': {'after_cycle': [hook('audit')]}})
    write(config.path(REPOSITORY), {'cycle_hooks': {'before_cycle': []}})
    settings = config.effective(REPOSITORY, upstream)['settings']
    assert settings['cycle_hooks'] == {'before_cycle': [], 'after_cycle': [hook('audit')]}
    assert config.DEFAULTS['cycle_hooks'] == {'before_cycle': [], 'after_cycle': []}
    available = list(settings['skills'].values())
    assert config.preflight(settings, available)['ready']
    config.path(REPOSITORY).unlink()
    inherited = config.effective(REPOSITORY, upstream)['settings']
    assert config.preflight(inherited, available) == {
        'ready': False, 'missing_selected_skills': ['team:context']}
    assert config.preflight(inherited, available + ['team:context'])['ready']


@pytest.mark.parametrize('hooks', [
    None, [], {'before_cleanup': []}, {'before_cycle': 'Read context'},
    {'before_cycle': [None]}, {'before_cycle': [hook(), hook()]},
    {'before_cycle': [hook(instructions='  ')]},
    {'before_cycle': [hook(instructions='text\x00')]},
    {'before_cycle': [hook(hook_id='bad/id')]},
    {'before_cycle': [hook(skill=' ')]}, {'before_cycle': [hook(skill=False)]},
    {'before_cycle': [hook(options=[])]}, {'before_cycle': [hook(command=['test'])]},
])
def test_malformed_hooks_are_rejected(hooks):
    with pytest.raises(ValueError):
        config.validate({'cycle_hooks': hooks})


@pytest.fixture
def configured(tmp_path):
    write(config.path(REPOSITORY), {'cycle_hooks': {
        'before_cycle': [hook()],
        'after_cycle': [hook('audit', skill='team:audit', options={'focus': ['invariants']})]}})
    resource = tmp_path/'SKILL.md'
    resource.write_text('Return original evidence and explicit completion.\n')
    definition = {'skill_identity': RUNNER, 'resources': {'skill': str(resource)},
                  'instructions': {}, 'inputs': {}, 'options': {'host': 'native'},
                  'governing_inputs_complete': True}
    return definition


def define(definition, *, run='run', cycle='c1', phase='before_cycle', hook_id='context'):
    return config.make_hook_spec(repository=REPOSITORY, pr=PR, run=run, cycle=cycle,
                                 phase=phase, hook_id=hook_id, definition=definition)


def finish(store, spec, claim, status='complete'):
    store.record(spec, claim['id'], b'Original native hook output.\n',
                 {'complete': status == 'complete', 'findings': []}, status)


def test_hook_resume_cycle_and_run_isolation_without_review_budget(configured):
    store = Store(REPOSITORY, PR)
    spec = define(configured)
    first = store.start(spec, run='run')
    assert first['action'] == 'invoke'
    assert store.start(spec, run='run')['action'] == 'recover_required'
    # An interrupted hook recovers its existing claim before any reviewer starts.
    with pytest.raises(ValueError, match='original run'):
        store.start(spec, run='replacement', recover=first['claim']['id'])
    resumed = store.start(spec, run='run', recover=first['claim']['id'])
    finish(store, spec, resumed['claim'])
    assert store.start(define(configured), run='run')['action'] == 'reuse'
    # Other phases, cycles and runs have independent receipts.
    after = define({**configured, 'skill_identity': 'team:audit'}, phase='after_cycle', hook_id='audit')
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
    store = Store(REPOSITORY, PR)
    spec = define(configured)
    started = store.start(spec, run='run')
    finish(store, spec, started['claim'], status='incomplete')
    assert not store.status([spec])['tasks'][0]['reusable']
    retry = store.start(spec, run='run')
    finish(store, spec, retry['claim'])
    assert store.start(spec, run='run')['action'] == 'reuse'
    Path(configured['resources']['skill']).write_text('Changed governing guidance.\n')
    assert not store.status([spec])['tasks'][0]['reusable']


def test_live_configuration_drift_blocks_recording(configured):
    store = Store(REPOSITORY, PR)
    spec = define(configured)
    started = store.start(spec, run='run')
    write(config.path(REPOSITORY), {'cycle_hooks': {'before_cycle': [hook(instructions='New task.')]}})
    with pytest.raises(ValueError, match='Inputs changed'):
        finish(store, spec, started['claim'])
    assert store.start(define(configured), run='run')['action'] == 'recover_required'
    assert store.status([])['unfinished'][0]['id'] == started['claim']['id']


def test_hook_spec_preserves_instructions_options_and_requires_selected_runner(configured):
    selected = {**configured, 'skill_identity': 'team:audit'}
    spec = define(selected, phase='after_cycle', hook_id='audit')
    assert spec['task']['instructions'] == hook()['instructions']
    assert spec['options']['configured'] == {'focus': ['invariants']}
    assert spec['options']['execution'] == {'host': 'native'}
    assert config.path(REPOSITORY).as_posix() in spec['instructions'].values()
    assert capture(spec)['key'] != capture(define(configured))['key']
    with pytest.raises(ValueError, match='configured hook skill'):
        define(configured, phase='after_cycle', hook_id='audit')
    with pytest.raises(ValueError, match='owning PR workflow'):
        define(selected)
    with pytest.raises(ValueError, match='execution options'):
        define({**configured, 'options': []})
    with pytest.raises(ValueError, match='No configured hook'):
        define(configured, hook_id='missing')


def test_config_and_hook_spec_cli_roundtrip(configured, tmp_path):
    # Use the public configuration writer, then generate and claim a real hook spec.
    configuration = {'cycle_hooks': {'before_cycle': [hook('first'), hook('second')]}}
    source = tmp_path/'settings.json'
    write(source, configuration)
    config_command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPOSITORY]
    result = subprocess.run([*config_command, 'set', '--file', str(source)], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    shown = subprocess.run([*config_command, 'show'], text=True, capture_output=True, check=True)
    assert json.loads(shown.stdout)['settings']['cycle_hooks']['before_cycle'] == configuration['cycle_hooks']['before_cycle']
    definition_path = tmp_path/'definition.json'
    write(definition_path, configured)
    dk = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPOSITORY, '--pr', PR]
    specs = []
    for hook_id in ('first', 'second'):
        result = subprocess.run([*dk, 'hook-spec', '--definition', str(definition_path),
                                 '--run', 'cli-run', '--cycle', 'c1', '--phase', 'before_cycle',
                                 '--hook-id', hook_id], text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        spec = json.loads(result.stdout)
        specs.append(spec)
        path = tmp_path/(hook_id+'.json')
        write(path, spec)
        started = subprocess.run([*dk, 'start', '--spec', str(path), '--run', 'cli-run'],
                                 text=True, capture_output=True, check=True)
        finish(Store(REPOSITORY, PR), spec, json.loads(started.stdout)['claim'])
    store = Store(REPOSITORY, PR)
    assert [store.start(spec, run='cli-run')['action'] for spec in specs] == ['reuse', 'reuse']
    assert store.status([])['runs']['cli-run']['cycles'] == []
