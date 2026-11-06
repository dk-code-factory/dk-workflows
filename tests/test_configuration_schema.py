"""Schema/runtime agreement and uniform invocation behavior at every call site."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import config
import invocation
from state import Store, capture, make_spec, write

SCHEMAS = {path.stem.removesuffix('.schema'): json.loads(path.read_text())
           for path in (ROOT/'schemas').glob('*.schema.json')}
REGISTRY = Registry().with_resources((schema['$id'], Resource.from_contents(schema))
                                     for schema in SCHEMAS.values())
REPOSITORY = 'github.com/owner/repo'
PR = 'https://github.com/owner/repo/pull/1'


def validator(name):
    return Draft202012Validator(SCHEMAS[name], registry=REGISTRY)


def available(settings):
    return sorted({call['skill'] for _, call in config.calls(settings) if 'skill' in call})


@pytest.mark.parametrize('schema', list(SCHEMAS))
def test_published_schemas_are_valid(schema):
    Draft202012Validator.check_schema(SCHEMAS[schema])


@pytest.mark.parametrize('value', [
    'dk-ci', 'team:review', {'skill': 'dk-ci'}, {'instructions': 'Read Decisions.'},
    {'skill': 'team:review', 'instructions': '  Preserve this prompt.\n',
     'options': {'focus': ['security']}, 'required': False,
     'agent': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'}},
    {'instructions': 'Read code.', 'agent': {}},
])
def test_valid_invocations_share_one_contract(value):
    validator('invocation').validate(value)
    assert invocation.validate(value) == value


INVALID = [None, [], {}, '', ' ', '/skills/dk-ci', 'dk-ci\n', 'team/review',
           {'id': 'user-id', 'skill': 'dk-ci'}, {'skill': False},
           {'instructions': '  '}, {'instructions': 'a\x00b'},
           {'skill': 'dk-ci', 'options': []}, {'skill': 'dk-ci', 'required': 1},
           {'skill': 'dk-ci', 'agent': False}, {'skill': 'dk-ci', 'agent': {'model': ''}},
           {'skill': 'dk-ci', 'agent': {'reasoning_effort': 'unlimited'}},
           {'skill': 'dk-ci', 'agent': {'temperature': 0.7}},
           {'skill': 'dk-ci', 'options': {'reviewers': [None]}}]


@pytest.mark.parametrize('value', INVALID)
def test_invalid_invocations_rejected_by_schema_and_runtime(value):
    assert not validator('invocation').is_valid(value)
    with pytest.raises(ValueError):
        invocation.validate(value)


def locations(value):
    return [
        {'processes': {'pr-fix': {'run': value}}},
        {'processes': {'pr-review': {'verification': value}}},
        {'processes': {'pr-fix': {'operations': [value]}}},
        {'processes': {'pr-fix': {'finalizers': [value]}}},
        {'processes': {'pr-fix': {'hooks': {'before_cycle': [value]}}}},
        {'processes': {'pr-review': {'hooks': {'after_cycle': [value]}}}},
        {'skill_settings': {'dk-pr-reviewers-run': {'options': {'reviewers': [value]}}}},
    ]


@pytest.mark.parametrize('value', ['dk-ci', {'skill': 'team:review', 'instructions': 'Check APIs.',
                                          'agent': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'}}])
def test_same_invocation_works_in_processes_supporting_skills_reviewers_hooks_and_drafting(value):
    for settings in locations(value):
        validator('config').validate(settings)
        assert config.validate(settings) == settings
    validator('drafting').validate({'commit_message': value})
    validator('commit-policy').validate(value)
    import commit_policy
    assert commit_policy.validate(value) == value


@pytest.mark.parametrize('value', INVALID)
def test_no_call_site_bypasses_the_shared_contract(value):
    for settings in locations(value):
        assert not validator('config').is_valid(settings)
        with pytest.raises(ValueError):
            config.validate(settings)
    assert not validator('drafting').is_valid({'commit_message': value})
    assert not validator('commit-policy').is_valid(value)


def test_defaults_and_shipped_examples_validate_offline():
    validator('config').validate(config.DEFAULTS)
    for path in (ROOT/'examples').glob('*.json'):
        value = json.loads(path.read_text())
        validator('config').validate(value)
        assert config.validate(value) == value
    expanded = json.loads((ROOT/'examples/config.expanded.json').read_text())
    assert {key: value for key, value in expanded.items() if key != '$schema'} == config.DEFAULTS


def test_schema_metadata_roundtrips_without_becoming_a_setting(tmp_path):
    settings = {'$schema': '../schemas/config.schema.json', 'schema_version': 1,
                'processes': {'pr-fix': {'limits': {'review_cycles': 3}}}}
    source = tmp_path/'config.json'
    write(source, settings)
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPOSITORY]
    subprocess.run([*command, 'set', '--file', str(source)], check=True, capture_output=True)
    assert json.loads(config.path(REPOSITORY).read_text()) == settings
    compact = json.loads(subprocess.check_output([*command, 'show'], text=True))['settings']
    expanded = json.loads(subprocess.check_output([*command, 'show', '--expanded'], text=True))['settings']
    assert '$schema' not in compact and '$schema' not in expanded
    assert compact['processes']['pr-fix']['limits'] == {'review_cycles': 3}
    assert expanded['processes']['pr-fix']['limits']['ci_reruns_per_head'] == 3
    assert config.DEFAULTS['processes']['pr-fix']['limits']['review_cycles'] == 5


def test_preflight_resolves_short_names_and_reports_ambiguity():
    settings = deepcopy(config.DEFAULTS)
    settings['processes']['pr-fix']['operations'] = ['review']
    installed = [name for name in available(settings) if name != 'review'] + ['one:review', 'two:review']
    result = config.preflight(settings, installed)
    assert not result['ready']
    assert result['ambiguous_selected_skills'] == {'review': ['one:review', 'two:review']}
    settings['processes']['pr-fix']['operations'] = ['one:review']
    result = config.preflight(settings, installed)
    assert result['ready'] and result['resolved_skills']['one:review'] == 'one:review'


def test_preflight_checks_nested_optional_reviewers_and_every_agent_setting():
    settings = deepcopy(config.DEFAULTS)
    settings['skill_settings']['dk-pr-reviewers-run'] = {'options': {'reviewers': [
        {'skill': 'team:review', 'required': False, 'agent': {'model': 'chosen', 'reasoning_effort': 'high'}}]}}
    installed = available(settings)
    assert not config.preflight(settings, installed)['ready']
    assert config.preflight(settings, [name for name in installed if name != 'team:review'])['missing_selected_skills'] == ['team:review']
    capabilities = {'delegation': True, 'models': {'chosen': ['high']}}
    assert config.preflight(settings, installed, capabilities)['ready']
    capabilities['models']['chosen'] = ['low']
    assert config.preflight(settings, installed, capabilities)['unsupported_agents']


def test_shared_defaults_local_overrides_and_qualified_resolution():
    settings = deepcopy(config.DEFAULTS)
    settings['skill_settings']['team:review'] = {
        'agent': {'model': 'chosen', 'reasoning_effort': 'high'},
        'options': {'focus': 'security', 'paths': ['src']}}
    settings['processes']['pr-fix']['operations'] = [
        {'skill': 'review', 'options': {'paths': ['auth']}, 'instructions': 'Keep exact.\n'}]
    capabilities = {'delegation': True, 'models': {'chosen': ['high']}}
    installed = [name for name in available(settings) if name != 'review'] + ['team:review']
    assert config.preflight(settings, installed, capabilities)['ready']
    call = config.select(settings, 'pr-fix', 'operation', 0)
    assert call['agent']['model'] == 'chosen'
    assert call['options'] == {'focus': 'security', 'paths': ['auth']}
    assert call['instructions'] == 'Keep exact.\n'


def test_recursive_review_coordinator_does_not_loop_forever():
    settings = deepcopy(config.DEFAULTS)
    settings['skill_settings']['dk-pr-reviewers-run'] = {'options': {'reviewers': ['dk-pr-reviewers-run']}}
    with pytest.raises(ValueError, match='Recursive reviewer'):
        config.preflight(settings, [])


def test_explicit_finite_nested_calls_can_reuse_a_skill():
    settings = deepcopy(config.DEFAULTS)
    settings['processes']['pr-fix']['operations'] = [
        {'skill': 'team:review', 'options': {'reviewers': [
            {'skill': 'team:review', 'options': {'reviewers': ['team:leaf']}}]}}]
    assert config.preflight(settings, available(settings))['ready']


@pytest.fixture
def definition(tmp_path):
    resource = tmp_path/'SKILL.md'
    resource.write_text('Read the supplied inputs and complete the assigned task.')
    return {'skill_identity': 'team:review', 'resources': {'skill': str(resource)},
            'responsibility': 'analysis', 'assigned_instructions': 'Review the complete change.',
            'instructions': {}, 'inputs': {}, 'options': {}, 'governing_inputs_complete': True}


def test_model_effort_prompt_and_options_reach_specs_and_invalidate_reuse(definition):
    selected = {'skill': 'team:review', 'instructions': '  Review authentication.\n',
                'options': {'focus': 'security'},
                'agent': {'model': 'chosen', 'reasoning_effort': 'high'}}
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'operations': [selected]}}})
    spec = config.make_invocation_spec(repository=REPOSITORY, pr=PR, process='pr-fix',
                                       kind='operation', index=0, definition=definition)
    assert spec['options']['invocation'] == selected
    assert spec['task']['instructions'] == selected['instructions']
    assert spec['options']['configured'] == selected['options']
    store = Store(REPOSITORY, PR)
    claimed = store.start(spec, run='run', cycle='one')
    store.record(spec, claimed['claim']['id'], b'Original report', {'complete': True}, 'complete')
    assert store.start(spec, run='run', cycle='one')['action'] == 'reuse'
    original_fingerprint = capture(spec)['fingerprint']
    selected['agent']['model'] = 'different'
    write(config.path(REPOSITORY), {'processes': {'pr-fix': {'operations': [selected]}}})
    changed = config.make_invocation_spec(repository=REPOSITORY, pr=PR, process='pr-fix',
                                          kind='operation', index=0, definition=definition)
    assert capture(changed)['fingerprint'] != original_fingerprint
    assert not store.status([changed])['tasks'][0]['reusable']


def test_process_limits_cannot_be_reset_by_a_later_spec(definition):
    spec = make_spec(repository=REPOSITORY, pr=PR, **definition)
    spec['options']['process_limits'] = {'review_cycles': 1}
    store = Store(REPOSITORY, PR)
    claim = store.start(spec, run='run', cycle='one')
    store.record(spec, claim['claim']['id'], b'Done', {'complete': True}, 'complete')
    changed = deepcopy(spec)
    changed['task']['instructions'] = 'Changed inputs'
    changed['options']['process_limits'] = {'review_cycles': 5}
    with pytest.raises(ValueError, match='cycle limit'):
        store.start(changed, run='run', cycle='two')
    grant = store.continue_run(run='run', request_id='user-request-2',
        authorization='Continue the review.', source='Fixture user message', cycles=2)
    assert grant['review_budget'] == {'used': 1, 'limit': 3, 'remaining': 2}
    assert store.start(changed, run='run', cycle='two')['action'] == 'invoke'
    assert store.status([], run='run')['runs']['run']['limits']['review_cycles'] == 1


def test_legacy_configuration_fails_without_rewriting_or_resetting_it():
    legacy = {'skills': {'description': 'dk-workflows:dk-pr-description'}}
    write(config.path(REPOSITORY), legacy)
    with pytest.raises(ValueError, match='process configuration'):
        config.effective(REPOSITORY)
    assert json.loads(config.path(REPOSITORY).read_text()) == legacy


def test_preflight_cli_and_verification_definition_preserve_agent_controls(definition, tmp_path):
    selected = {'skill': 'team:review', 'instructions': 'Verify findings independently.',
                'agent': {'model': 'chosen', 'reasoning_effort': 'high'}}
    write(config.path(REPOSITORY), {'processes': {'pr-review': {'verification': selected}}})
    settings = config.effective(REPOSITORY)['settings']
    installed, capabilities = tmp_path/'available.json', tmp_path/'host.json'
    write(installed, available(settings))
    write(capabilities, {'delegation': True, 'models': {'chosen': ['high']}})
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPOSITORY, 'preflight',
               '--process', 'pr-review', '--available', str(installed)]
    blocked = json.loads(subprocess.check_output(command, text=True))
    assert not blocked['ready'] and blocked['unsupported_agents']
    ready = json.loads(subprocess.check_output([*command, '--capabilities', str(capabilities)], text=True))
    assert ready['ready']
    path = tmp_path/'definition.json'
    write(path, {**definition, 'responsibility': 'finding-verification'})
    spec = json.loads(subprocess.check_output([
        sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPOSITORY, '--pr', PR,
        'invocation-spec', '--process', 'pr-review', '--kind', 'verification', '--definition', str(path)], text=True))
    assert spec['task']['id'] == 'finding-verification'
    assert spec['options']['invocation'] == selected


def test_short_name_settings_do_not_leak_to_another_qualified_skill():
    settings = deepcopy(config.DEFAULTS)
    settings['skill_settings'] = {'first:review': {'agent': {'model': 'first-model'}},
                                  'second:review': {'agent': {'model': 'second-model'}}}
    settings['processes']['pr-fix']['operations'] = ['first:review']
    installed = available(settings) + ['second:review']
    support = {'delegation': True, 'models': {'first-model': [], 'second-model': []}}
    assert config.preflight(settings, installed, support)['ready']
    assert config.select(settings, 'pr-fix', 'operation', 0)['agent']['model'] == 'first-model'


def test_standalone_review_uses_the_same_reviewer_selection():
    settings = deepcopy(config.DEFAULTS)
    settings['skill_settings']['dk-pr-reviewers-run'] = {'options': {'reviewers': ['team:review']}}
    assert config.select(settings, 'pr-reviewers-run', 'reviewer', 0) == {'skill': 'team:review'}


@pytest.mark.parametrize('kind', ['make', 'capture'])
def test_standalone_managed_tasks_cannot_bypass_invocation_validation(definition, kind):
    value = deepcopy(definition)
    value['options']['invocation'] = {'skill': '/skills/invalid'}
    with pytest.raises(ValueError, match='without a /skills/'):
        if kind == 'make':
            make_spec(repository=REPOSITORY, pr=PR, **value)
        else:
            spec = make_spec(repository=REPOSITORY, pr=PR, **definition)
            spec['options'] = value['options']
            capture(spec)
