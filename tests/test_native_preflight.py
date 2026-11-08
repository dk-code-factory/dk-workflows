"""Offline compatibility preflight; fixture capabilities are not host telemetry."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import config
import invocation
import skill_config
from state import Store, make_spec, write

SKILL = 'code-review-ad:code-review-ad'
CONTRACT = {'schema': 1, 'skill_version': '1.0.7', 'passes': [
    {'role': 'toolkit', 'drivers': [['python3', '/installed/aspect-driver.py', '--pass', 'toolkit']]}]}
CAPABILITIES = {'delegation': True, 'native_evidence': {
    'availability': 'available', 'inventory_complete': True, 'invocation_results': True,
    'child_results': True, 'tool_observations': True}}
CALLS = [('reviewer', {'skill': SKILL})]


def check(capabilities=None, requirements=None):
    return invocation.preflight(CALLS, [SKILL], capabilities,
                                native_reviews={'reviewer': CONTRACT} if requirements is None else requirements)


def test_issue_reproduction_without_resolved_requirements_is_blocked():
    result = invocation.preflight(CALLS, [SKILL], {'delegation': True})
    assert not result['ready']
    assert result['unsupported_native_reviews'][0]['location'] == 'reviewer'
    assert 'resolved native review requirements' in result['unsupported_native_reviews'][0]['missing']


@pytest.mark.parametrize('capabilities', [None, {}, {'delegation': True},
    {'delegation': True, 'native_evidence': {'availability': 'unknown'}},
    {'delegation': True, 'native_evidence': {'availability': 'unavailable'}}])
def test_unknown_or_unavailable_transport_blocks_before_launch(capabilities):
    result = check(capabilities)
    assert not result['ready']
    diagnostic = result['unsupported_native_reviews'][0]
    assert 'available native evidence transport' in diagnostic['missing']
    assert 'keep review blocked' in diagnostic['supported_path']


@pytest.mark.parametrize('field', ['inventory_complete', 'invocation_results', 'child_results', 'tool_observations'])
@pytest.mark.parametrize('value', [False, None])
def test_partial_transport_is_not_supported(field, value):
    support = deepcopy(CAPABILITIES)
    if value is None:
        support['native_evidence'].pop(field)
    else:
        support['native_evidence'][field] = value
    result = check(support)
    assert not result['ready']
    assert field in result['unsupported_native_reviews'][0]['missing']


def test_full_transport_and_delegation_support_pass_without_agent_overrides():
    result = check(CAPABILITIES)
    assert result['ready'] and not result['unsupported_native_reviews']
    support = deepcopy(CAPABILITIES)
    support['delegation'] = False
    assert not check(support)['ready']


def test_contract_controls_required_capabilities_and_conditional_passes():
    support = deepcopy(CAPABILITIES)
    support['native_evidence'].pop('child_results')
    support['native_evidence'].pop('tool_observations')
    requirements = {'reviewer': {**CONTRACT, 'passes': []}}
    assert check(support, requirements)['ready']
    requirements['reviewer']['passes'] = [{'role': 'spec', 'drivers': []}]
    assert not check(support, requirements)['ready']
    support['native_evidence']['child_results'] = True
    assert check(support, requirements)['ready']
    requirements['reviewer']['passes'].append(CONTRACT['passes'][0])
    assert not check(support, requirements)['ready']


def test_repeated_reviewers_require_each_occurrence_contract_including_optional():
    calls = [('owner/reviewers/0/0', {'skill': 'team:check'}),
             ('owner/reviewers/1/0', {'skill': 'team:check', 'required': False})]
    result = invocation.preflight(calls, ['team:check'], CAPABILITIES,
                                  native_reviews={calls[0][0]: CONTRACT})
    assert not result['ready']
    assert [item['location'] for item in result['unsupported_native_reviews']] == [calls[1][0]]
    assert invocation.preflight(calls, ['team:check'], CAPABILITIES,
                               native_reviews={location: CONTRACT for location, _ in calls})['ready']


@pytest.mark.parametrize('location', [
    '/processes/reviewers/coordinator', '/processes/example/tasks/reviewers',
    '/processes/reviewers/tasks/reviewers', '/processes/reviewers/finalizers/0/0',
    'owner/reviewers/0/0/tasks/0/0'])
def test_ordinary_occurrences_with_reviewer_named_ancestors_are_not_reviews(location):
    result = invocation.preflight([(location, {'skill': 'team:analysis'})], ['team:analysis'])
    assert result['ready']
    assert result['unsupported_native_reviews'] == []


@pytest.mark.parametrize('location', [
    '/processes/reviewers/coordinator/options/reviewers/0',
    '/processes/example/tasks/reviewers/options/reviewers/0',
    'owner/reviewers/0/0', 'owner/tasks/1/0/reviewers/0/0'])
def test_structural_reviewer_occurrences_still_require_contracts(location):
    result = invocation.preflight([(location, {'skill': 'team:check'})], ['team:check'])
    assert not result['ready']
    assert result['unsupported_native_reviews'][0]['location'] == location
    assert invocation.preflight([(location, {'skill': 'team:check'})], ['team:check'],
                                CAPABILITIES, native_reviews={location: CONTRACT})['ready']


@pytest.mark.parametrize('process', ['reviewers', 'example'])
def test_legacy_cli_allows_ordinary_process_and_task_names_reviewers(tmp_path, process):
    repository = 'github.com/owner/repo'
    write(config.path(repository), {'processes': {process: {
        'coordinator': 'team:analysis', 'tasks': {'reviewers': 'team:analysis'}}}})
    installed = tmp_path/'available.json'
    write(installed, ['team:analysis'])
    result = json.loads(subprocess.check_output([
        sys.executable, str(ROOT/'scripts/config.py'), '--repository', repository,
        'preflight', '--process', process, '--available', str(installed)], text=True))
    assert result['ready']
    assert result['unsupported_native_reviews'] == []


def test_explicit_contract_covers_independent_verification_with_unrelated_skill():
    calls = [('workflow/tasks/2/0', {'instructions': 'Verify findings.'})]
    result = invocation.preflight(calls, [], {'delegation': True},
                                  native_reviews={calls[0][0]: {**CONTRACT, 'passes': []}})
    assert not result['ready']
    assert invocation.preflight([('ordinary', {'skill': 'team:analysis'})], ['team:analysis'])['ready']


@pytest.mark.parametrize('evidence', [False, [], {'inventory_complete': 1}, {'unknown': True},
                                     {'availability': 'maybe'}])
def test_malformed_transport_is_rejected(evidence):
    with pytest.raises(ValueError, match='native evidence'):
        check({'delegation': True, 'native_evidence': evidence})


@pytest.mark.parametrize('requirements', [[], {'unknown': CONTRACT}, {'reviewer': {}},
                                         {'reviewer': {**CONTRACT, 'passes': None}}])
def test_malformed_or_unselected_contracts_are_rejected(requirements):
    with pytest.raises(ValueError):
        check(CAPABILITIES, requirements)


@pytest.mark.parametrize('mode', ['preflight', 'skill-preflight'])
def test_cli_checks_requirements_without_changing_retained_work_or_budgets(tmp_path, mode):
    repository = 'github.com/owner/repo'
    pr = 'https://' + repository + '/pull/1'
    owner = 'dk-workflows:dk-pr-reviewers-run'
    write(config.path(repository), {'skill_defaults': {owner: {'options': {'reviewers': [SKILL]}}}})
    settings = config.effective(repository)['settings']
    calls = list(config.calls(settings, 'pr-reviewers-run')) if mode == 'preflight' else list(skill_config.calls(owner, repository))
    available = sorted({call['skill'] for _, call in calls if 'skill' in call})
    locations = [location for location, call in calls if call.get('skill') == SKILL]
    installed, requirements, capabilities = [tmp_path/name for name in ('available.json', 'reviews.json', 'host.json')]
    write(installed, available)
    write(requirements, {location: CONTRACT for location in locations})
    write(capabilities, CAPABILITIES)
    # Create actual completed and pending siblings in an already-consumed cycle.
    resource = tmp_path/'skill.md'
    resource.write_text('Offline analysis assignment')
    def spec(task):
        return make_spec(repository=repository, pr=pr, skill_identity='team:analysis',
                         resources={'skill': str(resource)}, responsibility=task,
                         assigned_instructions='Inspect inputs.', instructions={}, inputs={},
                         options={}, governing_inputs_complete=True)
    store = Store(repository, pr)
    completed = spec('completed')
    started = store.start(completed, run='retained', cycle='one')
    store.record(completed, started['claim']['id'], b'Original findings', {'complete': True}, 'complete')
    pending = spec('pending')
    store.start(pending, run='retained', cycle='one')
    before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', repository, mode,
               '--available', str(installed)]
    command += ['--process', 'pr-reviewers-run'] if mode == 'preflight' else ['--skill', owner]
    blocked = json.loads(subprocess.check_output(command, text=True))
    assert not blocked['ready'] and blocked['unsupported_native_reviews']
    blocked = json.loads(subprocess.check_output([*command, '--native-reviews', str(requirements)], text=True))
    assert not blocked['ready']
    ready = json.loads(subprocess.check_output([*command, '--native-reviews', str(requirements),
                                               '--capabilities', str(capabilities)], text=True))
    assert ready['ready']
    assert {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before
    assert store.start(completed, run='retained', cycle='one')['action'] == 'reuse'
    assert store.start(pending, run='retained', cycle='one')['action'] == 'recover_required'
