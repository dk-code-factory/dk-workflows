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
import skill_config
from state import read, write
from tasks import Tasks
from test_state import case, REPO, PR, ROOT, git
from test_pr_assessment import initial, complete, comparison, descriptor, purpose, inventory
from test_alternatives import finish, selection
from test_fix_alternatives import planned
from native_review_observations import observations
from checklist_reports import report as checklist_report


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
        result = json.loads(subprocess.check_output(command, text=True, timeout=60))
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


def test_distinct_direct_invocations_refresh_and_interrupted_request_resumes_same_work(case, tmp_path, monkeypatch):
    spec, store = case; tasks = Tasks(REPO, PR)
    monkeypatch.setattr(alternatives, 'live_pr', lambda pr: {'body': 'Original PR scope', 'headRefOid': spec['git']['head']})
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
        before = files(store)
        for owner in (assessment.FIX, assessment.REVIEW):
            settled = plans.gate(tasks, owner, request=source)
            assert settled['outcome'] == 'planned' and settled['design_context']
        assert files(store) == before
        assert read(store.manifest)['entries'][refs['assessment']]['result'] == shared
    assert len(read(store.manifest)['entries'][refs['comparison:storage']]['attempts']) == 3


def selected(tasks, spec):
    refs = {'assessment': complete(tasks, spec, 'assessment', purpose())}
    refs['inventory'] = complete(tasks, spec, 'inventory', inventory(('one', 'inherited')), refs)
    refs['comparison'] = complete(tasks, spec, 'comparison', comparison(spec), refs, decision='one')
    refs['selection_one'] = complete(tasks, spec, 'selection', selection(),
        {'assessment': refs['assessment'], 'comparison': refs['comparison']}, decision='one')
    return refs


def test_cross_route_uses_consumer_enablement_and_retains_alignment_context(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, owner=assessment.FIX)
    write(skill_config.scope_path(REPO), {'skill_defaults': {
        assessment.FIX: {'options': {'alternatives': {'enabled': False}}}}})
    result = plans.gate(tasks, assessment.REVIEW)
    assert result['outcome'] == 'planned' and result['references'] == refs
    assert result['governing_evidence'] and result['current_alignment_required']


def test_cross_route_mismatch_uses_consumer_publication_permissions(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, owner=assessment.FIX, mismatch=True)
    complete(tasks, spec, 'verification', {'disposition': 'confirmed', 'reason': 'Independent mismatch',
        'required_changes': 'Apply the selected write owner', 'evidence': ['code:1']},
        {'alignment': refs['alignment']}, owner=assessment.FIX)
    assert skill_config.defaults(assessment.FIX)['permissions']['post_review'] is False
    assert skill_config.defaults(assessment.REVIEW)['permissions']['post_review'] is True
    assert plans.gate(tasks, assessment.REVIEW)['outcome'] == 'mismatch_verified'


def test_pending_manual_choice_can_finish_without_a_new_comparison(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = selected(tasks, spec)
    # Simulate the valid initial unresolved choice under a manual selection mode.
    # There is no settled choice to replace and no completed comparison to rerun.
    policy = {'selection': {'mode': 'manual'}}
    d = descriptor(spec, 'selection', {k: refs[k] for k in ('assessment', 'comparison')}, decision='one', policy=policy)
    source = manual(tmp_path, 2); d['definition']['inputs']['alternatives_request'] = source
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    finish(tasks, task, selection(choice=None))
    original = read(store.manifest)['entries'][refs['comparison']]['result']
    choice_source = tmp_path/'choice.txt'; choice_source.write_text('Choose the smaller evaluated option')
    d['definition']['inputs']['user_choice'] = str(choice_source)
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['action'] == 'invoke'
    report = selection(mode='manual', choice='smaller', user_source=str(choice_source),
        reason='User selected the smaller option', rejection_reasons={'implemented': 'User declines its additional complexity'},
        evidence=[str(choice_source)])
    finish(tasks, task, report)
    assert read(store.manifest)['entries'][refs['comparison']]['result'] == original


def test_unfinished_interactions_consume_historical_comparisons_after_guidance_update(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = selected(tasks, spec)
    Path(spec['skill']['resources']['entry']).write_text('Updated installed review guidance')
    result = plans.gate(tasks, assessment.REVIEW)
    assert result['stage'] == 'interactions'
    d = result['next_task']
    d['definition'] = descriptor(spec, 'interactions')['definition']
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['action'] == 'invoke'
    assert all(len(read(store.manifest)['entries'][key]['attempts']) == 1 for key in refs.values())


def test_interrupted_manual_request_reuses_completed_stages_after_guidance_update(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    initial(tasks, spec)
    source = manual(tmp_path, 2)
    for report in (inventory(('storage', 'inherited'), ('boundary', 'materially_changed')), comparison(spec)):
        task = tasks.prepare({'tasks': [plans.gate(tasks, assessment.REVIEW, request=source)['next_task']]})['tasks'][0]
        finish(tasks, task, report)
    Path(spec['skill']['resources']['entry']).write_text('Updated installed review guidance')
    result = plans.gate(Tasks(REPO, PR), assessment.REVIEW, request=source)
    assert result['stage'] == 'selection' and result['next_task']['decision'] == 'storage'
    assert tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]['action'] == 'invoke'


def test_pre_upgrade_manual_request_keeps_original_bytes_and_claim(case, tmp_path, monkeypatch):
    import alternatives_history
    spec, store = case; tasks = Tasks(REPO, PR)
    initial(tasks, spec)
    path = tmp_path/'old-request.json'
    write(path, {'id': 'old-user-message', 'source': 'conversation:old', 'instruction': 'Review PR alternatives again'})
    before = path.read_bytes()
    # The previous release accepted these exact three-field original artifacts.
    with monkeypatch.context() as previous:
        previous.setattr(alternatives_history, 'request', lambda source, **kwargs: read(source))
        task = tasks.prepare({'tasks': [plans.gate(tasks, assessment.REVIEW, request=str(path))['next_task']]})['tasks'][0]
        finish(tasks, task, inventory(('storage', 'inherited'), ('boundary', 'materially_changed')))
        pending = tasks.prepare({'tasks': [plans.gate(tasks, assessment.REVIEW, request=str(path))['next_task']]})['tasks'][0]
    result = plans.gate(Tasks(REPO, PR), assessment.REVIEW, request=str(path))
    assert result['stage'] == 'comparison'
    replay = tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]
    assert replay['action'] == 'recover_required' and replay['unfinished']['id'] == pending['claim']['id']
    finish(tasks, pending, comparison(spec))
    assert path.read_bytes() == before


def test_completed_manual_request_cannot_be_replaced_by_mutating_its_artifact(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    initial(tasks, spec)
    source = manual(tmp_path, 2)
    task = tasks.prepare({'tasks': [plans.gate(tasks, assessment.REVIEW, request=source)['next_task']]})['tasks'][0]
    finish(tasks, task, inventory(('storage', 'inherited'), ('boundary', 'materially_changed')))
    changed = read(source); changed['id'] = 'different-user-message'
    write(source, changed)
    before = files(store)
    with pytest.raises(ValueError, match='Original manual request changed'):
        plans.gate(tasks, assessment.REVIEW, request=source)
    assert files(store) == before


def test_cross_route_reuse_does_not_validate_consumer_options_as_retained_owner(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    result = plans.gate(tasks, assessment.FIX,
        options={'completion_instructions': 'Retain the selected completion duties'})
    assert result['outcome'] == 'planned' and result['references'] == refs
    assert result['current_alignment_required']


@pytest.mark.parametrize('definition', [None, 'damaged', {'inputs': []}])
def test_damaged_nested_definition_retains_original_recovery_key(case, definition):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    key = refs['comparison:storage']
    with store.locked() as manifest:
        manifest['entries'][key]['managed']['definition'] = definition
        write(store.manifest, manifest)
    before = files(store)
    for owner in (assessment.FIX, assessment.REVIEW):
        result = plans.gate(tasks, owner)
        assert result['outcome'] == 'recovery_required' and result['next_task'] == {'key': key}
    assert files(store) == before


def test_same_manual_request_resumes_its_owner_after_switching_routes(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    source = manual(tmp_path, 2)
    next_task = plans.gate(tasks, assessment.REVIEW, request=source)['next_task']
    task = tasks.prepare({'tasks': [next_task]})['tasks'][0]
    finish(tasks, task, inventory(('storage', 'inherited'), ('boundary', 'materially_changed')))
    before = files(store)
    resumed = plans.gate(tasks, assessment.FIX, request=source)
    assert resumed['stage'] == 'comparison' and resumed['next_task']['skill'] == assessment.REVIEW
    assert resumed['next_task']['dependencies']['inventory'] == refs['inventory']
    other = descriptor(spec, 'inventory', {'assessment': refs['assessment']}, owner=assessment.FIX,
                       inputs={'alternatives_request': source})
    with pytest.raises(ValueError, match='original alternatives chain'):
        tasks.prepare({'tasks': [other]})
    assert files(store) == before


@pytest.mark.parametrize('change', ['changed', 'removed'])
def test_original_checklist_finishes_in_fresh_process_after_guidance_update(case, tmp_path, change):
    spec, store = case; tasks = Tasks(REPO, PR)
    task = tasks.prepare({'tasks': [descriptor(spec, 'assessment')]})['tasks'][0]
    report = {'complete': True, 'findings': [], **purpose()}
    original = json.dumps(report).encode()
    pending = tasks.finish(task['key'], task['claim']['id'], original, report,
                           native_evidence=observations(task['assignment'], task['claim'], original))
    assert pending['action'] == 'recheck'
    entry = read(store.manifest)['entries'][task['key']]
    primary = deepcopy(entry['result']); attempts = deepcopy(entry['attempts'])
    path = Path(spec['skill']['resources']['entry'])
    if change == 'changed':
        path.write_text('Updated installed guidance')
    else:
        path.unlink()
    request = tmp_path/'recheck.json'; write(request, {'tasks': [{'key': task['key']}]})
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR]
    replay = json.loads(subprocess.check_output([*command, 'prepare', '--file', str(request)], text=True, timeout=60))['tasks'][0]
    assert replay['action'] == 'recheck' and replay['round'] == pending['round']
    assert replay['assignment']['historical_completion_only']
    followup = tmp_path/'followup.json'; write(followup, checklist_report(replay['checklist']))
    result = json.loads(subprocess.check_output([*command, 'finish', '--key', task['key'],
        '--claim', task['claim']['id'], '--round', replay['round'], '--report', str(followup)], text=True, timeout=60))
    assert result['checklist_complete'] and result['action'] == 'complete'
    before = files(store)
    # A lost response replays the same original round without another allowance.
    result = tasks.finish(task['key'], task['claim']['id'], followup.read_bytes(), read(followup), round_id=replay['round'])
    assert result['checklist_complete'] and files(store) == before
    entry = read(store.manifest)['entries'][task['key']]
    assert entry['result'] == primary and entry['attempts'] == attempts
    assert len(entry['checklist']['rounds']) == 1
    assert plans.gate(tasks, assessment.REVIEW)['stage'] == 'inventory'


def test_original_checklist_rejects_changed_primary_binding_and_wrong_claim(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    task = tasks.prepare({'tasks': [descriptor(spec, 'assessment')]})['tasks'][0]
    report = {'complete': True, 'findings': [], **purpose()}
    original = json.dumps(report).encode()
    pending = tasks.finish(task['key'], task['claim']['id'], original, report,
                           native_evidence=observations(task['assignment'], task['claim'], original))
    followup = checklist_report(pending['checklist'])
    before = files(store)
    with pytest.raises(ValueError, match='original claim'):
        tasks.finish(task['key'], 'another-claim', json.dumps(followup).encode(), followup,
                     round_id=pending['round'])
    assert files(store) == before
    with store.locked() as manifest:
        manifest['entries'][task['key']]['components']['options']['changed'] = True
        write(store.manifest, manifest)
    before = files(store)
    with pytest.raises(ValueError, match='intact complete primary'):
        tasks.prepare({'tasks': [{'key': task['key']}]})
    assert files(store) == before
