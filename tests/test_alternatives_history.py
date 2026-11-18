"""Canonical PR comparisons survive routes, versions and recovery offline."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import alternatives
import assessment_alternatives as plans
import pr_assessment as assessment
from state import read, write
from tasks import Tasks
from test_state import case, REPO, PR, ROOT, git
from test_pr_assessment import initial, complete, comparison, descriptor, purpose, inventory
from test_alternatives import finish, selection
from test_fix_alternatives import planned


def files(store):
    return {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}


def manual(tmp_path, number):
    path = tmp_path/f'request-{number}.json'
    write(path, {'id': f'user-message-{number}', 'source': f'conversation:{number}',
                 'instruction': f'$dk-workflows:dk-pr-review-alternatives {PR}',
                 'skill': 'dk-workflows:dk-pr-review-alternatives', 'origin': 'user'})
    return str(path)


@pytest.mark.parametrize('owner', [assessment.FIX, assessment.REVIEW])
def test_both_routes_reuse_completed_comparison_after_changed_guidance_and_head(case, owner):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, owner=owner)
    # The prior installation may be removed. Its captured guidance remains
    # evidence; it is not adopted as today's repository instructions.
    Path(spec['skill']['resources']['entry']).unlink()
    Path(spec['inputs']['context']).write_text('New requirements need live alignment')
    checkout = spec['git']['checkout']
    Path(checkout, 'code').write_text('Later author implementation\n')
    git(checkout, 'commit', '-qam', 'later implementation')
    before = files(store)
    for route in ('fix', 'review'):
        command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO,
                   '--pr', PR, 'plan', '--workflow', route]
        result = json.loads(subprocess.check_output(command, text=True))
        assert result['outcome'] == 'planned'
        assert result['references'] == refs
        assert result['design_context'] and result['current_alignment_required']
    assert files(store) == before


@pytest.mark.parametrize('legacy_owner', [assessment.FIX, assessment.REVIEW])
def test_legacy_comparisons_supply_context_across_routes_without_a_new_inventory(case, legacy_owner):
    spec, store = case; tasks = Tasks(REPO, PR)
    if legacy_owner == assessment.FIX:
        refs = planned(tasks, spec, alignment='aligned')
    else:
        from test_alternatives import aligned
        refs = aligned(tasks, spec)
    Path(spec['skill']['resources']['entry']).unlink()
    before = files(store)
    for owner in (assessment.FIX, assessment.REVIEW):
        result = plans.gate(tasks, owner)
        assert result['outcome'] == 'planned' and result['release_reviewers']
        assert result['references'] == refs and result['current_alignment_required']
    assert files(store) == before


@pytest.mark.parametrize('damage', ['missing-result', 'result-bytes', 'native', 'claim', 'checklist'])
def test_invalid_history_requires_original_recovery_without_recomparison(case, damage):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    key = refs['comparison:storage']
    original = plans.accepted(tasks, key)['reference']
    if damage == 'missing-result':
        Path(original).unlink()
    elif damage == 'result-bytes':
        Path(original).write_text('{}')
    else:
        with store.locked() as manifest:
            entry = manifest['entries'][key]
            if damage == 'native':
                entry['components']['native_review']['skill_version'] = 'changed'
            elif damage == 'claim':
                entry['claim'] = {'id': 'retained-unresolved-claim'}
            else:
                entry['checklist']['outcome'] = None
            write(store.manifest, manifest)
    before = files(store)
    for owner in (assessment.REVIEW, assessment.FIX):
        result = plans.gate(tasks, owner)
        assert result['outcome'] == 'recovery_required'
        assert result['next_task'] == {'key': key}
    assert files(store) == before


def test_partial_cross_route_resume_preserves_completed_siblings_and_original_claim(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    a = complete(tasks, spec, 'assessment', purpose())
    inv = complete(tasks, spec, 'inventory', inventory(('one', 'inherited'), ('two', 'introduced')), {'assessment': a})
    first = complete(tasks, spec, 'comparison', comparison(spec), {'assessment': a, 'inventory': inv}, decision='one')
    complete(tasks, spec, 'selection', selection(), {'assessment': a, 'comparison': first}, decision='one')
    pending = tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': a, 'inventory': inv}, decision='two')]})['tasks'][0]
    before = files(store)
    result = plans.gate(Tasks(REPO, PR), assessment.FIX)
    assert result['stage'] == 'comparison' and result['next_task']['skill'] == assessment.REVIEW
    assert files(store) == before
    replay = tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]
    assert replay['action'] == 'recover_required' and replay['unfinished']['id'] == pending['claim']['id']
    assert len(read(store.manifest)['entries'][first]['attempts']) == 1


@pytest.mark.parametrize('change', ['missing-skill', 'downstream', 'iteration'])
def test_only_direct_dedicated_manual_requests_authorize_new_comparisons(case, tmp_path, change):
    spec, store = case; tasks = Tasks(REPO, PR)
    initial(tasks, spec)
    source = manual(tmp_path, 2)
    request = read(source)
    if change == 'missing-skill':
        request.pop('skill')
    elif change == 'downstream':
        request['origin'] = 'downstream'
    else:
        request['skill'] = 'dk-workflows:dk-pr-iter'
    write(source, request)
    before = files(store)
    with pytest.raises(ValueError, match='direct user invocation'):
        plans.gate(tasks, assessment.REVIEW, request=source)
    assert files(store) == before


def test_distinct_direct_invocations_refresh_and_interrupted_request_resumes_same_work(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    shared = read(store.manifest)['entries'][refs['assessment']]['result']
    for number in (2, 3):
        source = manual(tmp_path, number)
        result = plans.gate(tasks, assessment.REVIEW, request=source)
        assert result['stage'] == 'inventory'
        task = tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]
        assert task['action'] == 'invoke'
        finish(tasks, task, inventory(('storage', 'inherited'), ('boundary', 'materially_changed')))
        result = plans.gate(Tasks(REPO, PR), assessment.REVIEW, request=source)
        assert result['stage'] == 'comparison'
        task = tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]
        assert task['action'] == 'invoke'
        replay = tasks.prepare({'tasks': [plans.gate(tasks, assessment.REVIEW, request=source)['next_task']]})['tasks'][0]
        assert replay['action'] == 'recover_required' and replay['unfinished']['id'] == task['claim']['id']
        finish(tasks, task, comparison(spec))
        # Finish this requested chain before a distinct invocation can replace it.
        for decision in ('storage', 'boundary'):
            if decision == 'boundary':
                task = tasks.prepare({'tasks': [plans.gate(tasks, assessment.REVIEW, request=source)['next_task']]})['tasks'][0]
                finish(tasks, task, comparison(spec))
            task = tasks.prepare({'tasks': [plans.gate(tasks, assessment.REVIEW, request=source)['next_task']]})['tasks'][0]
            finish(tasks, task, selection())
        for report in ({'compatible': True, 'reason': 'Compatible ownership', 'evidence': ['code:1']},
                       {'alignment': 'aligned', 'reason': 'Matches choices', 'evidence': ['code:1']}):
            task = tasks.prepare({'tasks': [plans.gate(tasks, assessment.REVIEW, request=source)['next_task']]})['tasks'][0]
            finish(tasks, task, report)
        assert plans.gate(tasks, assessment.REVIEW)['outcome'] == 'planned'
        assert read(store.manifest)['entries'][refs['assessment']]['result'] == shared
    assert len(read(store.manifest)['entries'][refs['comparison:storage']]['attempts']) == 3
