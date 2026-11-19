"""Bundled behavior composition, native handoffs and retained-state boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import alternatives_compat
import behavior
import config
import config_adapters
import config_requests
import invocation
import skill_config
from state import Store, capture, write
from task_policy import make_spec
from config_request_admission import admitted

REPO = 'github.com/owner/repo'
UPSTREAM = 'github.com/upstream/repo'
PR = 'https://' + REPO + '/pull/1'
REVIEW = 'dk-workflows:dk-pr-review'
CONFIG = 'dk-workflows:dk-config'
CI = 'dk-workflows:dk-ci'


def save(scope, skill, fields):
    path = config.path(scope)
    saved = json.loads(path.read_text()) if path.exists() else {}
    saved.setdefault('skill_defaults', {}).setdefault(skill, {})['behavior'] = fields
    write(path, saved)


def cli(*args):
    return subprocess.run([sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPO,
                           *args], capture_output=True, text=True)


@pytest.mark.parametrize('skill', skill_config.catalog())
def test_every_shipped_role_is_inspectable_and_synced(skill):
    assert 'default' in behavior.roles(skill)
    for role, path in behavior.roles(skill).items():
        shipped = behavior.resolve(skill, role, REPO)
        assert shipped['default'].strip() and shipped['effective'].strip()
        assert (ROOT/'claude/skills'/path.parent.parent.name/'prompts'/path.name).read_bytes() == path.read_bytes()
        replaced = behavior.resolve(skill, role, REPO, behavior={role: {'override': 'Replacement.', 'append': 'Extra.'}})
        assert replaced['effective'] == 'Replacement.\n\nExtra.'
        assert replaced['default'] == shipped['default']
        assert replaced['sources'] == {'override': 'invocation', 'append': 'invocation'}


def test_independent_field_precedence_null_clearing_and_read_only_inspection():
    save(None, REVIEW, {'finding-verification': {'override': 'User replacement.', 'append': 'User extra.'}})
    save(UPSTREAM, REVIEW, {'finding-verification': {'append': 'Upstream extra.'}})
    save(REPO, REVIEW, {'finding-verification': {'override': None}})
    files = {p: p.read_bytes() for p in [config.path(), config.path(UPSTREAM), config.path(REPO)]}
    selected = behavior.resolve(REVIEW, 'finding-verification', REPO, UPSTREAM)
    assert selected['effective'] == selected['default'] + '\n\nUpstream extra.'
    assert selected['sources'] == {'override': str(config.path(REPO)), 'append': str(config.path(UPSTREAM))}
    selected = behavior.resolve(REVIEW, 'finding-verification', REPO, UPSTREAM,
        behavior={'finding-verification': {'override': 'Local.', 'append': None}})
    assert selected['effective'] == 'Local.'
    for section in ('default', 'effective', 'sources'):
        result = cli('--upstream', UPSTREAM, 'behavior-show', '--skill', REVIEW,
                     '--role', 'finding-verification', '--section', section)
        assert result.returncode == 0, result.stdout + result.stderr
        assert set(json.loads(result.stdout)) == {'skill', 'role', section} | ({'default_path'} if section == 'default' else {'overridden'} if section == 'effective' else set())
    assert all(p.read_bytes() == data for p, data in files.items())


@pytest.mark.parametrize('fields', [None, [], {'unknown': {}}, {'default': None},
    {'default': {'typo': 'x'}}, *[{'default': {'override': value}} for value in ('', ' \n', 'x\x00y', 1, False)]])
def test_bad_behavior_is_rejected_in_shared_invocations_and_scopes(fields):
    with pytest.raises(ValueError):
        invocation.validate({'skill': REVIEW, 'behavior': fields})
    with pytest.raises(ValueError):
        config.validate({'skill_defaults': {REVIEW: {'behavior': fields}}})


def test_json_schemas_and_runtime_accept_same_role_contract():
    schemas = [json.loads(path.read_text()) for path in [*ROOT.glob('schemas/*.schema.json'),
                *ROOT.glob('codex/skills/*/config.schema.json')]]
    registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    for schema_name, wrap in [('invocation', lambda b: {'skill': REVIEW, 'behavior': b}),
                              ('config', lambda b: {'skill_defaults': {REVIEW: {'behavior': b}}})]:
        schema = json.loads((ROOT/'schemas'/(schema_name + '.schema.json')).read_text())
        validator = Draft202012Validator(schema, registry=registry)
        assert validator.is_valid(wrap({'finding-verification': {'override': None, 'append': 'x'}}))
        assert not validator.is_valid(wrap({'unknown-role': {'append': 'x'}}))
        assert not validator.is_valid(wrap({'default': {'append': ' \n'}}))
    with pytest.raises(ValueError, match='only for bundled'):
        invocation.validate({'skill': 'external:review', 'behavior': {'default': {'append': 'x'}}})


def definition(tmp_path, skill=CI, responsibility='inspect'):
    source = tmp_path/'contract.md'
    source.write_text('Mandatory output and permission contract.')
    return {'skill_identity': skill, 'responsibility': responsibility,
            'resources': {'entry': str(source)}, 'assigned_instructions': 'Original user instruction.',
            'instructions': {}, 'inputs': {}, 'options': {}, 'governing_inputs_complete': True}


def test_specialized_options_are_suppressed_only_by_full_replacement(tmp_path):
    reply = 'dk-workflows:dk-pr-feedback-reply'
    local = {'reply_prompt': 'Specialized default methodology.'}
    default = behavior.resolve(reply, repository=REPO, options=local)
    assert 'Specialized default methodology.' in default['effective']
    spec = make_spec(repository=REPO, pr=PR, **definition(tmp_path, reply), behavior={
        'skill': reply, 'options': local, 'behavior': {'default': {'override': 'Replacement.', 'append': 'Addition.'}}})
    assert spec['task']['instructions'] == 'Replacement.\n\nAddition.\n\nOriginal additional instructions:\nOriginal user instruction.'
    assert 'Specialized default methodology.' not in spec['task']['instructions']
    assert 'behavior_default' not in spec['skill']['resources']
    assert 'behavior_entry' in spec['skill']['resources']
    assert Path(spec['skill']['resources']['entry']).read_text().startswith('Mandatory')
    assert skill_config.effective(reply, REPO, options=local)['options']['reply_prompt'] == local['reply_prompt']


def test_generated_alternatives_replacement_suppresses_helper_methodology(tmp_path):
    import alternatives
    original = definition(tmp_path, alternatives.legacy_options(skill_config.defaults(REVIEW))['alternatives']['review_skill'])
    original.update(git={'checkout': str(tmp_path)}, native_review={'required': True})
    save(None, REVIEW, {'alternatives-comparison': {'override': 'Entire replacement.', 'append': 'Extra.'}})
    execution = alternatives.stage_execution(original, 'comparison', skill_config.defaults(REVIEW))
    # Inspect without capturing synthetic native/Git evidence.
    execution.pop('git'); execution.pop('native_review')
    spec = make_spec(repository=REPO, pr=PR, **execution)
    assert spec['task']['instructions'].startswith('Entire replacement.\n\nExtra.')
    assert behavior.roles(REVIEW)['alternatives-comparison'].read_text().strip() not in spec['task']['instructions']
    assert skill_config.defaults(REVIEW)['alternatives']['scoring']['profile_selection_prompt'] not in spec['task']['instructions']
    assert 'Original user instruction.' in spec['task']['instructions']
    assert 'alternatives_contract' in spec['skill']['resources']


def test_fresh_process_reuse_partial_recovery_and_role_invalidation(tmp_path):
    store = Store(REPO, PR)
    first = make_spec(repository=REPO, pr=PR, **definition(tmp_path))
    sibling = make_spec(repository=REPO, pr=PR, **definition(tmp_path, CONFIG))
    initial = capture(first)
    start = store.start(first)
    store.record(first, start['claim']['id'], b'Original accepted result', {'complete': True}, 'complete')
    store.start(sibling)
    original_entries = json.loads(store.manifest.read_text())['entries']
    before = store.manifest.read_bytes()
    spec_path = tmp_path/'spec.json'
    write(spec_path, first)
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR,
               'start', '--spec', str(spec_path)]
    assert json.loads(subprocess.check_output(command, text=True))['action'] == 'reuse'
    assert store.manifest.read_bytes() == before
    save(REPO, CI, {'failure-diagnosis': {'append': 'Unrelated internal role.'}})
    assert capture(first)['fingerprint'] == capture(make_spec(repository=REPO, pr=PR, **definition(tmp_path)))['fingerprint']
    assert store.start(first)['action'] == 'reuse'
    assert store.start(sibling)['action'] == 'recover_required'
    save(REPO, CI, {'default': {'append': 'New governing methodology.'}})
    with pytest.raises(ValueError, match='consumed role behavior'):
        capture(first)
    updated = make_spec(repository=REPO, pr=PR, **definition(tmp_path))
    assert capture(updated)['key'] == initial['key']
    assert store.start(updated)['action'] == 'invoke'
    current_entries = json.loads(store.manifest.read_text())['entries']
    assert current_entries[capture(sibling)['key']] == original_entries[capture(sibling)['key']]
    assert current_entries[initial['key']]['result'] == original_entries[initial['key']]['result']


def test_selected_role_edits_do_not_invalidate_unconsumed_siblings(tmp_path):
    options = {'alternatives': {'enabled': False}, 'tasks': [CI, CONFIG]}
    def spec(index, skill):
        return skill_config.make_spec(repository=REPO, pr=PR, skill='dk-workflows:dk-pr-fix',
            kind='tasks', index=index, options=options, definition=definition(tmp_path, skill))
    a, b = spec(0, CI), spec(1, CONFIG)
    fingerprints = capture(a), capture(b)
    save(REPO, CI, {'failure-diagnosis': {'append': 'Other role.'}})
    assert capture(spec(0, CI)) == fingerprints[0]
    assert capture(spec(1, CONFIG)) == fingerprints[1]
    save(REPO, CI, {'default': {'override': 'Changed default.'}})
    assert capture(spec(0, CI))['key'] == fingerprints[0]['key']
    assert capture(spec(0, CI))['fingerprint'] != fingerprints[0]['fingerprint']
    assert capture(spec(1, CONFIG)) == fingerprints[1]


def test_request_bound_cli_scoped_reset_and_uncertain_write_reconciliation(tmp_path, monkeypatch):
    checkout = tmp_path/'checkout'; checkout.mkdir()
    existing = {'skill_defaults': {CONFIG: {'instructions': 'Keep original assignment.',
        'behavior': {'default': {'append': 'Keep sibling.'}}}, CI: {'options': {'timeout_seconds': 100}}}}
    write(config.path(REPO), existing)
    operation = {'domain': 'behavior', 'action': 'set', 'scope': 'repo', 'skill': CONFIG,
                 'role': 'configuration-agent', 'value': {'override': 'New configuration method.'}}
    request_id = admitted(operation, repository=REPO, checkout=checkout)
    fields = tmp_path/'fields.json'; write(fields, operation['value'])
    result = cli('--checkout', str(checkout), 'behavior-set', '--skill', CONFIG,
                 '--role', 'configuration-agent', '--file', str(fields))
    assert result.returncode == 2 and '--request-id' in result.stdout
    original_write = config_requests.atomic_text
    writes = []
    def interrupted(path, value):
        original_write(path, value); writes.append(str(path))
        raise OSError('Interrupted after store write before receipt')
    monkeypatch.setattr(config_requests, 'atomic_text', interrupted)
    with pytest.raises(OSError):
        config_requests.Requests().apply(request_id)
    monkeypatch.setattr(config_requests, 'atomic_text', lambda *_: pytest.fail('Repeated uncertain write'))
    assert config_requests.Requests().apply(request_id)['status'] == 'applied'
    assert len(writes) == 1
    after = json.loads(config.path(REPO).read_text())
    assert after['skill_defaults'][CONFIG]['instructions'] == 'Keep original assignment.'
    assert after['skill_defaults'][CI] == existing['skill_defaults'][CI]
    reset = {k: v for k, v in operation.items() if k != 'value'} | {'action': 'reset'}
    reset_id = admitted(reset, repository=REPO, checkout=checkout)
    monkeypatch.setattr(config_requests, 'atomic_text', original_write)
    result = cli('--checkout', str(checkout), 'behavior-reset', '--skill', CONFIG,
        '--role', 'configuration-agent', '--request-id', reset_id)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(config.path(REPO).read_text()) == existing
    # A subsequent assignment resolves the edited agent behavior; the old
    # handoff itself remains the original independent assignment.
    new_id = config_requests.Requests().start('Inspect current settings.', {'checkout': str(checkout), 'repository': REPO})['id']
    old = config_requests.Requests().resume(new_id)['handoff']
    save(REPO, CONFIG, {'configuration-agent': {'override': 'Next assignment.'}})
    new = config_requests.Requests().resume(new_id)['handoff']
    assert old['behavior']['effective'] != new['behavior']['effective'] == 'Next assignment.'


def test_role_schema_catalog_matches_packaged_resources():
    schema = json.loads((ROOT/'schemas/config.schema.json').read_text())
    for skill in skill_config.catalog():
        declared = schema['properties']['skill_defaults']['properties'][skill]['properties']['behavior']['propertyNames']['enum']
        assert set(declared) == set(behavior.roles(skill))


def test_reset_absent_role_preserves_unrelated_document_shape(tmp_path):
    context = {'repository': REPO, 'checkout': str(tmp_path)}
    raw = json.dumps({'accounts': {'personal': []}})
    operation = config_adapters.normalize({'domain': 'behavior', 'action': 'reset', 'scope': 'repo',
                                           'skill': CONFIG, 'role': 'configuration-agent'})
    assert json.loads(config_adapters.project(context, operation, raw)) == json.loads(raw)


def test_finding_verification_uses_owning_role_and_keeps_output_contract(tmp_path):
    save(REPO, REVIEW, {'finding-verification': {'override': 'Independent replacement.'}})
    spec = skill_config.make_spec(repository=REPO, pr=PR, skill=REVIEW, kind='tasks', index=2,
        options={'alternatives': {'enabled': False}},
        definition=definition(tmp_path, REVIEW, 'finding-verification'))
    assert spec['behavior_configuration']['role'] == 'finding-verification'
    assert spec['task']['instructions'].startswith('Independent replacement.')
    assert behavior.roles(REVIEW)['finding-verification'].read_text() not in spec['task']['instructions']
    assert 'Return independent verification dispositions' in spec['task']['instructions']


@pytest.mark.parametrize('plan', [lambda call: [call], lambda call: [{'parallel': [call]}], lambda call: [[call]]])
def test_owned_instruction_only_calls_reject_unknown_roles(plan):
    call = {'instructions': 'Inspect supplied evidence.',
            'behavior': {'finding-verificaton': {'append': 'Extra guidance.'}}}
    with pytest.raises(ValueError, match='Unknown behavior role'):
        skill_config.validate(REVIEW, {'tasks': plan(call)})
    call['behavior'] = {'finding-verification': {'append': 'Extra guidance.'}}
    assert skill_config.validate(REVIEW, {'tasks': plan(call)})['tasks'] == plan(call)
    with pytest.raises(ValueError, match='Unknown behavior role'):
        config.validate(invocation.merge(config.DEFAULTS, {'processes': {'pr-review': {'tasks': {'verify_findings': {
            'instructions': 'Inspect supplied evidence.',
            'behavior': {'finding-verificaton': {'append': 'Extra guidance.'}}}}}}}), resolved=True)


@pytest.mark.parametrize('skill', ['dk-workflows:dk-issue-alternatives', 'dk-workflows:dk-pr-alternatives'])
def test_direct_alternatives_default_keeps_specialized_guidance(skill):
    options = {'assessment_prompt': 'Direct assessment methodology.',
               'evaluation_prompt': 'Direct evaluation methodology.',
               'selection': {'prompt': 'Direct selection methodology.'}}
    default = behavior.resolve(skill, options=options)
    assert all(text in default['effective'] for text in [options['assessment_prompt'],
               options['evaluation_prompt'], options['selection']['prompt']])
    replaced = behavior.resolve(skill, options=options, behavior={'default': {'override': 'Replacement only.'}})
    assert replaced['effective'] == 'Replacement only.'
    assert default['default'] == replaced['default']


def test_behavior_resources_preserve_caller_paths_and_freshness(tmp_path):
    original = definition(tmp_path)
    source = original['resources']['entry']
    original['resources'].update({label: source for label in
                                 ('behavior_contract', 'behavior_entry', 'behavior_default')})
    spec = make_spec(repository=REPO, pr=PR, **original)
    assert all(spec['skill']['resources'][label] == source for label in original['resources'])
    assert str(ROOT/'runtime/behavior.py') in spec['skill']['resources'].values()
    assert str(behavior.roles(CI)['default']) in spec['skill']['resources'].values()
    first = capture(spec)
    Path(source).write_text('Changed caller governing contract.')
    assert capture(spec)['fingerprint'] != first['fingerprint']


def test_external_behavior_is_rejected_by_both_public_schemas():
    schemas = [json.loads(path.read_text()) for path in [*ROOT.glob('schemas/*.schema.json'),
                *ROOT.glob('codex/skills/*/config.schema.json')]]
    registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    call = {'skill': 'external:review', 'behavior': {'default': {'append': 'Extra.'}}}
    for name, value in [('invocation', call), ('config', {'skill_defaults': {
            'external:review': {'behavior': call['behavior']}}})]:
        schema = json.loads((ROOT/'schemas'/(name + '.schema.json')).read_text())
        validator = Draft202012Validator(schema, registry=registry)
        assert not validator.is_valid(value)
    assert Draft202012Validator(json.loads((ROOT/'schemas/invocation.schema.json').read_text()),
                                registry=registry).is_valid({'skill': 'external:review', 'instructions': 'Original.'})


def test_approver_discovery_order_is_replaceable_and_inspectable():
    skill = 'dk-workflows:dk-pr-approvers'
    default = behavior.resolve(skill)
    assert 'Prefer appropriate existing requests and previous human reviewers' in default['default']
    override = 'Select eligible CODEOWNERS before previous reviewers.'
    assert behavior.resolve(skill, behavior={'default': {'override': override}})['effective'] == override
    entry = (skill_config.directory(skill)/'SKILL.md').read_text()
    assert 'Prefer appropriate existing requests and previous human reviewers' not in entry
    assert 'Confirm selected users or teams can review the target repository' in entry


@pytest.mark.parametrize('alias,canonical', alternatives_compat.SKILLS.items())
def test_behavior_aliases_share_canonical_schema_roles_and_defaults(alias, canonical):
    schemas = [json.loads(path.read_text()) for path in [*ROOT.glob('schemas/*.schema.json'),
                *ROOT.glob('codex/skills/*/config.schema.json')]]
    registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    validators = {name: Draft202012Validator(json.loads((ROOT/'schemas'/(name + '.schema.json')).read_text()),
                                           registry=registry) for name in ('invocation', 'config')}
    for role in behavior.roles(canonical):
        fields = {role: {'append': 'Alias guidance.'}}
        call = {'skill': alias, 'behavior': fields}
        assert validators['invocation'].is_valid(call)
        assert validators['config'].is_valid({'skill_defaults': {alias: {'behavior': fields}}})
        assert invocation.validate(call)['skill'] == canonical
        assert config.validate({'skill_defaults': {alias: {'behavior': fields}}})['skill_defaults'][canonical]['behavior'] == fields
    bad = {'unknown-role': {'append': 'Invalid.'}}
    assert not validators['invocation'].is_valid({'skill': alias, 'behavior': bad})
    assert not validators['config'].is_valid({'skill_defaults': {alias: {'behavior': bad}}})
    with pytest.raises(ValueError, match='Unknown behavior role'):
        invocation.validate({'skill': alias, 'behavior': bad})
    with pytest.raises(ValueError, match='Unknown behavior role'):
        config.validate({'skill_defaults': {alias: {'behavior': bad}}})
    operation = config_adapters.normalize({'domain': 'behavior', 'action': 'set', 'scope': 'repo',
                                          'skill': alias, 'role': 'default', 'value': {'append': 'Alias guidance.'}})
    assert operation['skill'] == canonical
    save(REPO, canonical, {'default': {'override': 'Canonical scoped replacement.'}})
    assert behavior.resolve(alias, repository=REPO) == behavior.resolve(canonical, repository=REPO)
    assert behavior.resolve(alias, repository=REPO)['default_path'] == str(behavior.roles(canonical)['default'])


def test_legacy_owned_roles_are_validated_after_scope_composition():
    task = {'instructions': 'Independently verify supplied findings.',
            'behavior': {'finding-verification': {'append': 'Preserve contrary evidence.'}}}
    write(config.path(), {'processes': {'pr-fix': {'coordinator': REVIEW}}})
    write(config.path(REPO), {'processes': {'pr-fix': {'tasks': {'verify_findings': task}}}})
    loaded = config.effective(REPO)['settings']['processes']['pr-fix']
    assert loaded['coordinator'] == REVIEW
    assert loaded['tasks']['verify_findings'] == task
    write(config.path(), {'processes': {'pr-fix': {'coordinator': 'external:review'}}})
    with pytest.raises(ValueError, match='only for bundled'):
        config.effective(REPO)
    task.pop('behavior')
    write(config.path(REPO), {'processes': {'pr-fix': {'tasks': {'verify_findings': task}}}})
    assert config.effective(REPO)['settings']['processes']['pr-fix']['coordinator'] == 'external:review'
    write(config.path(), {'processes': {'pr-fix': {'coordinator': CI, 'tasks': {'verify_findings': {**task,
        'behavior': {'finding-verification': {'append': 'Repository owner guidance.'}}}}}}})
    write(config.path(REPO), {'processes': {'pr-fix': {'coordinator': REVIEW}}})
    assert config.effective(REPO)['settings']['processes']['pr-fix']['coordinator'] == REVIEW
