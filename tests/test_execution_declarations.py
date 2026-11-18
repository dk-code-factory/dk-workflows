"""Explicit requirements share one contract across preflight and task lifecycle."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from test_state import case, REPO, PR
import invocation
import native_review
from native_review import binding, contract, required
from native_review_observations import CONTRACT, PREFLIGHT_CAPABILITIES, observations
from state import capture, make_spec, read, write
from tasks import Tasks
from test_batch_state import cli, definition
from checklist_reports import finish

NONE = {'schema': 2, 'required': False}
NEEDED = {**CONTRACT, 'schema': 2, 'required': True}


def request(spec, declaration, **options):
    return {'tasks': [{'definition': {**definition(spec), 'native_review': declaration}}], **options}


@pytest.mark.parametrize('task,skill', [
    ('content-review', 'code-review-ad:code-review-ad'),
    ('finding-verification:one', 'team:replacement'),
    ('analyze', 'team:analysis'),
])
@pytest.mark.parametrize('needed', [False, True])
def test_explicit_requirement_survives_renamed_tasks_and_replacement_skills(case, task, skill, needed):
    spec, _ = case
    fields = {**definition(spec), 'responsibility': task, 'skill_identity': skill,
              'native_review': NEEDED if needed else NONE}
    generated = make_spec(repository=REPO, pr=PR, **fields)
    assert required(generated) is needed
    assert required(capture(generated)['components']) is needed
    preflight = invocation.preflight([('selected', {'skill': skill})], [skill],
        PREFLIGHT_CAPABILITIES if needed else None,
        native_reviews={'selected': generated['native_review']})
    assert preflight['ready']
    tasks = Tasks(REPO, PR)
    prepared = tasks.prepare({'tasks': [{'definition': fields}]})['tasks'][0]
    assert ('native' in prepared) is needed
    assert ('native-evidence' in {i['id'] for i in prepared['checklist']['items']}) is needed
    assert prepared['assignment']['native_review'] == generated['native_review']
    original = b'Original native result'
    evidence = observations(generated, prepared['claim'], original) if needed else None
    result = finish(tasks, prepared['key'], prepared['claim']['id'], original,
                    {'complete': True, 'findings': []}, native_evidence=evidence)
    assert result['checklist_complete']
    assert tasks.prepare({'tasks': [{'key': prepared['key']}]})['tasks'][0]['action'] == 'reuse'


@pytest.mark.parametrize('declaration', [
    {'schema': 2}, {'schema': 2, 'required': None}, {'schema': 2, 'required': 1},
    {'schema': 2, 'required': 'false'}, {'schema': 2, 'required': True},
    {'schema': 2, 'required': True, 'skill_version': 'v1'},
    {**NONE, 'passes': []}, {**NONE, 'skill_version': 'v1'},
    {**NEEDED, 'unknown': True}, {**NEEDED, 'passes': None},
    {**NEEDED, 'skill_version': ''}, {'schema': 3, 'required': True},
    {**NONE, 'schema': 2.0}, {**NONE, 'schema': '2'},
])
def test_invalid_declarations_fail_before_claims_and_at_preflight(case, declaration):
    spec, store = case
    with pytest.raises(ValueError, match='native_review'):
        Tasks(REPO, PR).prepare(request(spec, declaration, run='retained', cycle='one'))
    assert not store.manifest.exists()
    with pytest.raises(ValueError, match='native_review'):
        invocation.preflight([('ordinary', {'skill': 'team:analyze'})], ['team:analyze'],
                             native_reviews={'ordinary': declaration})
    malformed = {**spec, 'native_review': declaration}
    with pytest.raises(ValueError, match='native_review'):
        capture(malformed)


@pytest.mark.parametrize('capabilities', [None, {}, {'delegation': True},
    {'delegation': True, 'native_evidence': {'availability': 'unknown'}},
    {'delegation': True, 'native_evidence': {'availability': 'unavailable'}}])
def test_explicit_required_work_with_unrelated_names_needs_supported_host(capabilities):
    calls = [('ordinary', {'skill': 'team:analysis'})]
    result = invocation.preflight(calls, ['team:analysis'], capabilities,
                                 native_reviews={'ordinary': NEEDED})
    assert not result['ready']
    assert result['unsupported_native_reviews'][0]['location'] == 'ordinary'


def test_explicit_none_overrides_legacy_list_and_skill_inference():
    call = ('owner/reviewers/0/0', {'skill': 'code-review-ad:code-review-ad'})
    result = invocation.preflight([call], [call[1]['skill']], native_reviews={call[0]: NONE})
    assert result['ready']
    assert not result['unsupported_native_reviews']
    # Legacy declarations keep requiring evidence, even if an extra field says otherwise.
    assert required({'native_review': {**CONTRACT, 'required': False}})
    assert contract(CONTRACT) == CONTRACT


def test_no_evidence_binding_can_be_created_for_explicit_none(case):
    spec, store = case
    spec['native_review'] = NONE
    claim = store.start(spec)['claim']
    with pytest.raises(ValueError, match='not required'):
        binding(capture(spec)['components'], claim)


@pytest.mark.parametrize('schema', [1.0, True], ids=['numeric-one', 'boolean-one'])
def test_retained_legacy_schema_values_reuse_and_recover_in_fresh_process(case, tmp_path, monkeypatch, schema):
    spec, store = case
    body = request(spec, {**CONTRACT, 'schema': schema}, run='retained', cycle='one')
    tasks = Tasks(REPO, PR)
    current_contract = native_review.contract

    def legacy_contract(value):
        # Construct pre-existing records under the original schema == 1 predicate.
        assert value.get('schema') == 1
        current_contract({**value, 'schema': 1})
        return value

    with monkeypatch.context() as previous:
        previous.setattr(native_review, 'contract', legacy_contract)
        completed = tasks.prepare(deepcopy(body))['tasks'][0]
        generated = tasks.spec(body['tasks'][0])
        result = finish(tasks, completed['key'], completed['claim']['id'], b'Legacy result',
                        {'complete': True, 'findings': []},
                        native_evidence=observations(generated, completed['claim'], b'Legacy result'))
        original_bytes = Path(result['result']).read_bytes()
        original_manifest = read(store.manifest)
        sibling = deepcopy(body)
        sibling['tasks'][0]['definition']['responsibility'] = 'pending-review'
        pending = tasks.prepare(sibling)['tasks'][0]

    before = store.manifest.read_bytes()
    source = tmp_path/'legacy-reuse.json'
    write(source, {'tasks': [{'key': completed['key']}], 'run': 'retained', 'cycle': 'one'})
    reused = json.loads(subprocess.check_output(cli('prepare', '--file', str(source))))['tasks'][0]
    assert reused['action'] == 'reuse'
    assert reused['key'] == completed['key']
    assert store.manifest.read_bytes() == before
    entry = read(store.manifest)['entries'][completed['key']]
    assert type(entry['components']['native_review']['schema']) is type(schema)
    assert entry['components']['native_review']['schema'] == schema
    assert entry == original_manifest['entries'][completed['key']]
    assert capture(generated)['fingerprint'] == completed['claim']['fingerprint']
    assert Path(result['result']).read_bytes() == original_bytes
    assert tasks.prepare({'tasks': [{'key': pending['key']}]})['tasks'][0]['action'] == 'recover_required'
    pending_spec = tasks.spec(sibling['tasks'][0])
    recovered = finish(tasks, pending['key'], pending['claim']['id'], b'Recovered legacy result',
                       {'complete': True, 'findings': []},
                       native_evidence=observations(pending_spec, pending['claim'], b'Recovered legacy result'))
    assert recovered['checklist_complete']
    assert Path(result['result']).read_bytes() == original_bytes
    assert store.status([], run='retained')['runs']['retained']['cycles'] == ['one']


def test_fresh_session_reuses_completed_siblings_and_preserves_pending_and_limits(case, tmp_path):
    spec, store = case
    tasks = Tasks(REPO, PR)
    body = request(spec, NONE, run='retained', cycle='one')
    completed = tasks.prepare(deepcopy(body))['tasks'][0]
    finish(tasks, completed['key'], completed['claim']['id'], b'Original completed analysis', {'complete': True})
    original_result = store.root/'results'/read(store.manifest)['entries'][completed['key']]['result']['file']
    original_bytes = original_result.read_bytes()
    sibling = deepcopy(spec)
    sibling['task']['id'] = 'pending-review'
    pending = tasks.prepare(request(sibling, NEEDED, run='retained', cycle='one'))['tasks'][0]
    before = store.manifest.read_bytes()
    source = tmp_path/'saved-key.json'
    write(source, {'tasks': [{'key': completed['key']}], 'run': 'retained', 'cycle': 'one'})
    reused = json.loads(subprocess.check_output(cli('prepare', '--file', str(source))))['tasks'][0]
    assert reused['action'] == 'reuse'
    assert store.manifest.read_bytes() == before
    assert tasks.prepare({'tasks': [{'key': pending['key']}]})['tasks'][0]['action'] == 'recover_required'
    # Redefine only the completed task's requirement; retain the original result and sibling claim.
    with pytest.raises(ValueError, match='new cycle'):
        tasks.prepare(request(spec, NEEDED, run='retained', cycle='one'))
    assert store.manifest.read_bytes() == before
    changed = tasks.prepare(request(spec, NEEDED, run='retained', cycle='two'))['tasks'][0]
    assert changed['key'] == completed['key'] and changed['action'] == 'invoke'
    assert changed['claim']['fingerprint'] != completed['claim']['fingerprint']
    assert original_result.read_bytes() == original_bytes
    assert tasks.prepare({'tasks': [{'key': pending['key']}]})['tasks'][0]['action'] == 'recover_required'
    assert read(store.manifest)['entries'][pending['key']]['claim'] == pending['claim']
    assert store.status([], run='retained')['runs']['retained']['cycles'] == ['one', 'two']


def test_declaration_drift_rejects_inflight_completion_without_erasing_claim(case):
    spec, store = case
    spec['native_review'] = NONE
    claim = store.start(spec, run='retained', cycle='one')['claim']
    before = store.manifest.read_bytes()
    spec['native_review'] = NEEDED
    with pytest.raises(ValueError, match='changed'):
        store.record(spec, claim['id'], b'Stale completion', {'complete': True}, 'complete')
    assert store.manifest.read_bytes() == before
