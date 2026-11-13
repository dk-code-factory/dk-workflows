"""Live simplification composition, native coverage and isolated task recovery."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import config
import invocation
import skill_config
from simplification import OWNERS, RUNNER, REVIEWER
from state import capture, write
from tasks import Tasks
from test_state import case, REPO, PR, ROOT
from test_batch_state import definition, cli
from checklist_reports import report as checklist_report
from native_review_observations import CONTRACT, observations, PREFLIGHT_CAPABILITIES

FIX = 'dk-workflows:dk-pr-fix'
REVIEW = 'dk-workflows:dk-pr-review'


def save(owner=FIX, **policy):
    write(config.path(REPO), {'skill_defaults': {owner: {'options': {
        'tasks': [{'skill': RUNNER}], 'simplification': policy}}}})


def selector(owner=FIX, **options):
    return {'skill': owner, 'index': 0, 'options': options}


def selected(workflow=None):
    return skill_config.plan(RUNNER, 'reviewers', REPO, workflow=workflow, resolved=True)


def descriptor(spec, index=0, workflow=None, identity=REVIEWER):
    fields = {**definition(spec), 'skill_identity': identity,
              'responsibility': 'content-review', 'native_review': CONTRACT}
    return {'kind': 'skill', 'skill': RUNNER, 'selection': 'reviewers', 'index': index,
            'workflow': workflow or selector(), 'definition': fields}


def finish(tasks, task, native=True):
    report = {**checklist_report(task['checklist']), 'findings': []}
    output = json.dumps(report).encode()
    return tasks.finish(task['key'], task['claim']['id'], output, report,
                        native_evidence=observations(task['assignment'], task['claim'], output) if native else None)


@pytest.mark.parametrize('fix_enabled,review_enabled', [(False, False), (False, True), (True, False), (True, True)])
def test_workflow_gates_are_independent_and_standalone_runner_has_no_injection(fix_enabled, review_enabled):
    write(config.path(REPO), {'skill_defaults': {
        owner: {'options': {'tasks': [{'skill': RUNNER}], 'simplification': {'enabled': enabled}}}
        for owner, enabled in [(FIX, fix_enabled), (REVIEW, review_enabled)]}})
    assert selected()['groups'] == []
    for owner, enabled in [(FIX, fix_enabled), (REVIEW, review_enabled)]:
        plan = selected(selector(owner))
        assert len(plan['groups']) == int(enabled)
        if enabled:
            call = plan['resolved_groups'][0][0]
            assert call['invocation']['skill'] == REVIEWER
            assert call['configuration']['options'] == {'priority': 'change-first', 'allow_untouched_code': False}
            assert call['invocation']['required'] is True


@pytest.mark.parametrize('priority', ['change-first', 'code-first'])
@pytest.mark.parametrize('untouched', [False, True])
def test_workflow_scope_governs_provider_defaults_and_invocation_options(priority, untouched):
    save(priority=priority, allow_untouched_code=untouched,
         reviewer={'skill': REVIEWER, 'options': {'priority': 'code-first', 'allow_untouched_code': True}})
    call = selected(selector())['resolved_groups'][0][0]
    assert call['configuration']['options'] == {'priority': priority, 'allow_untouched_code': untouched}
    instructions = call['invocation']['instructions']
    assert 'priority=' + priority in instructions
    assert ('within changed hunks' in instructions) == (not untouched)
    assert 'earlier suggestions do not expand' in instructions


def test_replacement_preserves_options_controls_instructions_and_optionality():
    provider = {'skill': 'custom:simplify', 'instructions': 'Exact prompt.\n',
                'options': {'opaque': 4}, 'agent': {'model': 'chosen', 'reasoning_effort': 'high'}, 'required': False}
    save(reviewer=provider)
    call = selected(selector())['resolved_groups'][0][0]['invocation']
    assert call['instructions'].endswith(provider['instructions'])
    assert all(call[key] == provider[key] for key in provider if key != 'instructions')
    assert 'overrides conflicting provider options' in call['instructions']


@pytest.mark.parametrize('bad', [None, [], {'enabled': 1}, {'priority': 'lines'},
                               {'allow_untouched_code': 'yes'}, {'unknown': True},
                               {'reviewer': 'simplify'}, {'reviewer': {'instructions': 'Only prompt'}},
                               {'reviewer': {'skill': REVIEWER, 'options': {'priority': 'short'}}}])
@pytest.mark.parametrize('owner', sorted(OWNERS))
def test_malformed_settings_fail_before_execution(owner, bad):
    with pytest.raises(ValueError):
        skill_config.effective(owner, REPO, options={'simplification': bad})


def test_disabled_provider_is_not_resolved_or_preflighted_but_enabled_provider_is():
    save(enabled=False, reviewer='missing:simplifier')
    calls = list(skill_config.calls(FIX, REPO, stage='review'))
    assert all(call.get('skill') != 'missing:simplifier' for _, call in calls)
    assert invocation.preflight(calls, skill_config.catalog())['ready']
    save(enabled=True, reviewer='missing:simplifier')
    calls = list(skill_config.calls(FIX, REPO, stage='review'))
    result = invocation.preflight(calls, skill_config.catalog())
    assert result['missing_selected_skills'] == ['missing:simplifier']
    assert result['unsupported_native_reviews']
    requirements = {loc: CONTRACT for loc, call in calls if call.get('skill') == 'missing:simplifier'}
    assert invocation.preflight(calls, [*skill_config.catalog(), 'missing:simplifier'],
                                PREFLIGHT_CAPABILITIES, native_reviews=requirements)['ready']


def test_custom_plans_keep_order_groups_and_removing_runner_does_not_restore_it():
    tasks = [{'instructions': 'First'}, {'parallel': [{'skill': RUNNER}, {'instructions': 'Sibling'}]},
             {'instructions': 'Last'}]
    write(config.path(REPO), {'skill_defaults': {FIX: {'options': {'tasks': tasks}}, RUNNER: {'options': {
        'reviewers': [{'parallel': ['custom:a', 'custom:b']}, 'custom:c'], 'execution': 'sequential', 'max_parallel': 2}}}})
    assert skill_config.plan(FIX, 'tasks', REPO)['groups'] == skill_config.groups(tasks)
    plan = selected({'skill': FIX, 'index': 1})
    assert plan['groups'][0] == ['custom:a', 'custom:b']
    assert plan['groups'][1] == ['custom:c']
    assert plan['groups'][2][0]['skill'] == REVIEWER
    assert plan['options']['execution'] == 'sequential' and plan['options']['max_parallel'] == 2
    save(enabled=True)
    write(config.path(REPO), {'skill_defaults': {FIX: {'options': {'tasks': []}}}})
    assert all(call.get('skill') != REVIEWER for _, call in skill_config.calls(FIX, REPO, stage='review'))
    with pytest.raises(ValueError):
        selected(selector())


def test_repeated_native_calls_recursion_and_missing_native_controls_are_preflighted():
    save(reviewer={'skill': FIX})
    with pytest.raises(ValueError, match='Recursive'):
        list(skill_config.calls(FIX, REPO, stage='review'))
    save(reviewer={'skill': 'custom:simplify', 'agent': {'model': 'missing'}})
    calls = list(skill_config.calls(FIX, REPO, stage='review'))
    requirements = {loc: CONTRACT for loc, call in calls if call.get('skill') == 'custom:simplify'}
    result = invocation.preflight(calls, [*skill_config.catalog(), 'custom:simplify'],
                                  PREFLIGHT_CAPABILITIES, native_reviews=requirements)
    assert result['unsupported_agents']


def test_native_evidence_is_mandatory_and_successful_results_reuse_in_a_fresh_process(case):
    spec, _ = case
    save()
    tasks = Tasks(REPO, PR)
    task = tasks.prepare({'run': 'same-run', 'cycle': 'c1', 'tasks': [descriptor(spec)]})['tasks'][0]
    assert task['native']['requirements'] == CONTRACT
    assert finish(tasks, task)['checklist_complete']
    path = tasks.store.root/'request.json'
    write(path, {'run': 'same-run', 'cycle': 'c1', 'tasks': [{'key': task['key']}]})
    fresh = json.loads(subprocess.check_output(cli('prepare', '--file', str(path)), text=True))
    assert fresh['tasks'][0]['action'] == 'reuse'
    assert tasks.resume(run='same-run')['tasks'][0]['coverage_complete']
    # A different content review cannot claim coverage from complete prose alone.
    task2 = tasks.prepare({'tasks': [descriptor(spec, workflow=selector(simplification={'priority': 'code-first'}))]})['tasks'][0]
    assert finish(tasks, task2, native=False)['status'] == 'incomplete'


def test_live_policy_gate_and_provider_changes_refresh_only_simplification(case):
    spec, _ = case
    save()
    saved = json.loads(config.path(REPO).read_text())
    saved['skill_defaults'][RUNNER] = {'options': {'reviewers': ['custom:ordinary']}}
    write(config.path(REPO), saved)
    tasks = Tasks(REPO, PR)
    ordinary, simple = [descriptor(spec, i, identity=identity) for i, identity in enumerate(['custom:ordinary', REVIEWER])]
    first = tasks.prepare({'run': 'retained', 'cycle': 'c1', 'tasks': [ordinary, simple]})['tasks']
    for task in first:
        assert finish(tasks, task)['checklist_complete']
    history = tasks.store.manifest.read_bytes()
    saved['skill_defaults'][FIX]['options']['simplification'] = {'priority': 'code-first'}
    write(config.path(REPO), saved)
    status = tasks.resume(run='retained')['tasks']
    by_key = {t['key']: t for t in status}
    assert by_key[first[0]['key']]['reusable']
    assert not by_key[first[1]['key']]['reusable']
    assert tasks.store.manifest.read_bytes() == history
    assert tasks.prepare({'tasks': [ordinary]})['tasks'][0]['action'] == 'reuse'
    fresh = tasks.prepare({'run': 'retained', 'cycle': 'c2', 'tasks': [simple]})['tasks'][0]
    assert fresh['action'] == 'invoke'
    saved['skill_defaults'][FIX]['options']['simplification']['enabled'] = False
    write(config.path(REPO), saved)
    with pytest.raises(ValueError):
        finish(tasks, fresh)
    assert len(selected(selector())['resolved_groups']) == 1  # only ordinary
    assert tasks.prepare({'tasks': [ordinary]})['tasks'][0]['action'] == 'reuse'
    saved['skill_defaults'][FIX]['options']['simplification'] = {'reviewer': 'custom:replacement'}
    write(config.path(REPO), saved)
    with pytest.raises(ValueError, match='identity'):
        tasks.prepare({'tasks': [simple]})
    replacement = descriptor(spec, 1, identity='custom:replacement')
    assert tasks.prepare({'tasks': [replacement]})['tasks'][0]['action'] == 'invoke'


def test_interrupted_recovery_preserves_completed_sibling_and_cycle_budget(case):
    spec, _ = case
    save()
    tasks = Tasks(REPO, PR)
    simple = descriptor(spec)
    launched = tasks.prepare({'run': 'retained', 'cycle': 'c1', 'tasks': [simple]})['tasks'][0]
    second_session = Tasks(REPO, PR)
    again = second_session.prepare({'run': 'retained', 'cycle': 'c1', 'tasks': [simple]})['tasks'][0]
    assert again['action'] == 'recover_required'
    assert again['unfinished'] == launched['claim']
    assert finish(second_session, launched)['checklist_complete']
    assert second_session.prepare({'run': 'retained', 'cycle': 'c1', 'tasks': [simple]})['tasks'][0]['action'] == 'reuse'
    assert tasks.store.status([])['runs']['retained']['cycles'] == ['c1']


def test_runner_handoff_and_cli_derive_live_selection(case, tmp_path):
    spec, _ = case
    save()
    fields = {**definition(spec), 'skill_identity': RUNNER, 'responsibility': 'skill-task'}
    task = {'kind': 'skill', 'skill': FIX, 'selection': 'tasks', 'index': 0, 'definition': fields}
    prepared = Tasks(REPO, PR).prepare({'tasks': [task]})['tasks'][0]
    assert prepared['assignment']['options']['workflow'] == selector()
    source = tmp_path/'workflow.json'; write(source, selector())
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPO,
               'skill-plan', '--skill', RUNNER, '--kind', 'reviewers', '--resolved', '--workflow', str(source)]
    result = json.loads(subprocess.check_output(command, text=True))
    assert result == selected(selector())
    with pytest.raises(ValueError, match='derive options'):
        skill_config.plan(RUNNER, 'reviewers', REPO, options={'reviewers': []}, workflow=selector())


@pytest.mark.parametrize('invalid', [{}, {'skill': 'custom:workflow', 'index': 0},
                                    {'skill': FIX, 'index': True}, {'skill': FIX, 'index': 0, 'extra': True}])
def test_invalid_workflow_selectors_fail(invalid):
    with pytest.raises(ValueError):
        selected(invalid)


def test_removed_runner_rejects_inflight_finish_and_preserves_original_claim(case):
    spec, _ = case
    save()
    tasks = Tasks(REPO, PR)
    task = tasks.prepare({'tasks': [descriptor(spec)]})['tasks'][0]
    before = tasks.store.manifest.read_bytes()
    write(config.path(REPO), {'skill_defaults': {FIX: {'options': {'tasks': []}}}})
    with pytest.raises(ValueError):
        finish(tasks, task)
    assert tasks.store.manifest.read_bytes() == before
    resumed = tasks.resume(keys=[task['key']])
    assert resumed['tasks'][0]['reason'] == 'refresh_required'
    assert resumed['unfinished'][0]['id'] == task['claim']['id']


def test_live_invocation_options_keep_owner_scope_and_override_saved_policy():
    save(priority='change-first')
    workflow = selector(simplification={'priority': 'code-first', 'allow_untouched_code': True})
    assert selected(workflow)['resolved_groups'][0][0]['configuration']['options'] == {
        'priority': 'code-first', 'allow_untouched_code': True}
    save(priority='change-first', enabled=False)
    assert selected(workflow)['groups'] == []  # local policy did not freeze the live gate


def test_review_preflight_excludes_disabled_provider_and_finalizers():
    options = {'architecture': {'enabled': False}, 'tasks': [{'skill': RUNNER}],
               'finalizers': ['missing:ending'], 'simplification': {'enabled': False, 'reviewer': 'missing:simplifier'}}
    calls = list(skill_config.calls(REVIEW, REPO, stage='review', options=options))
    assert invocation.preflight(calls, skill_config.catalog())['ready']
    options['simplification']['enabled'] = True
    assert any(call.get('skill') == 'missing:simplifier'
               for _, call in skill_config.calls(REVIEW, REPO, stage='review', options=options))


def test_workflow_review_limits_govern_leaf_claims_without_resetting_retained_budget(case):
    spec, _ = case
    save()
    saved = json.loads(config.path(REPO).read_text())
    saved['skill_defaults'][FIX]['options']['limits'] = {'review_cycles': 1}
    write(config.path(REPO), saved)
    tasks = Tasks(REPO, PR)
    task = tasks.prepare({'run': 'bounded', 'cycle': 'c1', 'tasks': [descriptor(spec)]})['tasks'][0]
    assert task['assignment']['options']['process_limits']['review_cycles'] == 1
    assert finish(tasks, task)['checklist_complete']
    saved['skill_defaults'][FIX]['options']['simplification'] = {'priority': 'code-first'}
    write(config.path(REPO), saved)
    with pytest.raises(ValueError, match='cycle'):
        tasks.prepare({'run': 'bounded', 'cycle': 'c2', 'tasks': [descriptor(spec)]})
    assert tasks.store.status([])['runs']['bounded']['cycles'] == ['c1']
