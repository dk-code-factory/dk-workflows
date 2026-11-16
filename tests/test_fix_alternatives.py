"""Initial alternatives planning is PR-wide; ordinary fix coverage stays live."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import alternatives
import config
import fix_alternatives as fix
import skill_config
from state import read, write
from tasks import Tasks
from test_state import case, REPO, PR, ROOT, git
from test_batch_state import definition
from test_alternatives import compared, selection, finish, initial, aligned, ordinary as incoming_task
from native_review_observations import CONTRACT, PREFLIGHT_CAPABILITIES
import invocation


def descriptor(spec, stage, dependencies=None, options=None):
    fields = definition(spec)
    fields['skill_identity'] = alternatives.REVIEW_SKILL if stage in {'assessment', 'comparison'} else fix.OWNER
    fields['native_review'] = CONTRACT
    metadata = Path(spec['git']['checkout']).parent/'initial-pr.json'
    if not metadata.exists():
        write(metadata, {'url': PR, 'body': 'Initial scope', 'headRefOid': spec['git']['head']})
    fields['inputs']['alternatives_current_pr'] = str(metadata)
    return {'kind': 'alternatives', 'skill': fix.OWNER, 'stage': stage,
            'definition': fields, 'dependencies': dependencies or {}, 'options': options or {}}


def complete(tasks, spec, stage, report, deps=None, options=None):
    task = tasks.prepare({'tasks': [descriptor(spec, stage, deps, options)]})['tasks'][0]
    assert task['action'] == 'invoke'
    assert finish(tasks, task, report)['checklist_complete']
    return task['key']


def planned(tasks, spec, *, required=True, alignment='mismatch', options=None):
    refs = {'assessment': complete(tasks, spec, 'assessment', {
        'required': required, 'reason': 'State choices' if required else 'Typo only', 'evidence': ['diff']}, options=options)}
    if not required:
        return refs
    refs['comparison'] = complete(tasks, spec, 'comparison', compared(), refs, options)
    refs['selection'] = complete(tasks, spec, 'selection', selection(), {'comparison': refs['comparison']}, options)
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': alignment, 'reason': 'Original implementation assessed',
        'required_changes': 'Use the selected state owner', 'evidence': ['diff']},
        {k: refs[k] for k in ('comparison', 'selection')}, options)
    return refs


def ordinary(spec, options=None):
    fields = definition(spec)
    fields['skill_identity'] = 'dk-workflows:dk-pr-feedback-read'
    return {'kind': 'skill', 'skill': fix.OWNER, 'selection': 'tasks', 'index': 0,
            'definition': fields, 'options': options or {}}


def test_default_fix_gate_has_shared_policy_and_isolates_alternatives_preflight(case):
    defaults = skill_config.defaults(fix.OWNER)
    assert defaults['alternatives'] == skill_config.defaults(alternatives.OWNER)['alternatives']
    calls = list(skill_config.calls(fix.OWNER, options={'tasks': ['missing:review']}, stage='alternatives'))
    assert [call['skill'] for _, call in calls] == [fix.OWNER, alternatives.REVIEW_SKILL]
    native = {calls[1][0]: CONTRACT}
    assert invocation.preflight(calls, [fix.OWNER, alternatives.REVIEW_SKILL], PREFLIGHT_CAPABILITIES,
                                native_reviews=native)['ready']
    with pytest.raises(ValueError, match='custom_prompt'):
        skill_config.effective(fix.OWNER, options={'alternatives': {'scoring': {'profile': 'custom'}}})


def test_fix_gate_constructs_each_missing_stage_and_returns_initial_mismatch_as_fix_guidance(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    reports = {'assessment': {'required': True, 'reason': 'State choices', 'evidence': ['diff']},
               'comparison': compared(), 'selection': selection(),
               'alignment': {'alignment': 'mismatch', 'reason': 'Different owner',
                             'required_changes': 'Use the selected state owner', 'evidence': ['diff']}}
    for stage in fix.STAGES:
        result = fix.gate(tasks)
        assert result['outcome'] == 'review_required' and result['stage'] == stage
        current = result['next_task']
        current['definition'] = descriptor(spec, stage)['definition']
        task = tasks.prepare({'run': 'original', 'cycle': 'initial', 'tasks': [current]})['tasks'][0]
        assert finish(tasks, task, reports[stage])['checklist_complete']
    result = fix.gate(tasks)
    assert result['outcome'] == 'planned' and result['release_reviewers']
    assert result['initial_alignment'] == 'mismatch' and result['required_changes'] == 'Use the selected state owner'
    assert read(store.manifest)['runs']['original']['cycles'] == ['initial']
    task = tasks.prepare({'tasks': [ordinary(spec)]})['tasks'][0]
    assert len([k for k in task['assignment']['inputs'] if k.startswith('alternatives_fix_governing_')]) == 4
    assert any(item['id'] == 'alternatives-plan' for item in task['checklist']['items'])


def test_once_per_pr_survives_new_heads_runs_and_fresh_sessions_without_state_writes(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, spec)
    original = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    checkout = Path(spec['git']['checkout'])
    (checkout/'code').write_text('fixed implementation\n'); git(checkout, 'commit', '-qam', 'Fix selected owner')
    changed = deepcopy(spec); changed['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO, '--pr', PR, 'fix-gate']
    for _ in range(2):
        result = json.loads(subprocess.check_output(command, text=True, timeout=60))
        assert result['outcome'] == 'planned' and result['references'] == refs
    assert original == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    for run in ('next-run', 'another-run'):
        for stage, key in refs.items():
            request = descriptor(changed, stage, {dep: refs[dep] for dep in alternatives.REQUIRES[stage]})
            task = Tasks(REPO, PR).prepare({'run': run, 'cycle': 'later', 'tasks': [request]})['tasks'][0]
            assert task['action'] == 'reuse' and task['key'] == key
            assert task['assignment']['git']['head'] == spec['git']['head']
    task = tasks.prepare({'tasks': [ordinary(changed)]})['tasks'][0]
    assert task['assignment']['git']['head'] == changed['git']['head']
    for key in refs.values():
        assert len(read(store.manifest)['entries'][key]['attempts']) == 1


def test_partial_resume_reuses_completed_stage_and_reconciles_only_pending_claim(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    assessment = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'State choices', 'evidence': ['diff']})
    task = tasks.prepare({'run': 'run', 'cycle': 'initial', 'tasks': [descriptor(spec, 'comparison', {'assessment': assessment})]})['tasks'][0]
    result = fix.gate(Tasks(REPO, PR))
    assert result['next_task'] == {'key': task['key']} and result['stage'] == 'comparison'
    assert tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]['action'] == 'recover_required'
    resumed = tasks.prepare({'run': 'run', 'cycle': 'initial', 'recover': {task['key']: task['claim']['id']},
                             'tasks': [result['next_task']]})['tasks'][0]
    assert finish(tasks, resumed, compared())['checklist_complete']
    assert fix.gate(tasks)['stage'] == 'selection'
    assert len(read(store.manifest)['entries'][assessment]['attempts']) == 1


def test_completed_plan_shares_git_capture_only_within_each_live_boundary(case, monkeypatch):
    import state
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, spec)
    calls = []
    original = state._capture_git
    def captured(source, **kwargs):
        calls.append(deepcopy(source))
        return original(source, **kwargs)
    monkeypatch.setattr(state, '_capture_git', captured)
    for _ in range(2):
        calls.clear()
        result = fix.gate(tasks)
        assert result['outcome'] == 'planned' and result['references'] == refs
        assert len(calls) == 1
    Path(spec['inputs']['context']).write_text('Changed governing requirements')
    calls.clear()
    with pytest.raises(ValueError, match='reassessment'):
        fix.gate(tasks)
    assert len(calls) == 1
    assert all(len(entry['attempts']) == 1 for entry in read(store.manifest)['entries'].values())


def test_pending_choice_settles_without_repeating_comparison(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    options = {'alternatives': {'auto_pick': {'enabled': False}}}
    a = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'State choices', 'evidence': ['diff']}, options=options)
    c = complete(tasks, spec, 'comparison', compared(), {'assessment': a}, options)
    s = complete(tasks, spec, 'selection', selection(choice=None), {'comparison': c}, options)
    assert fix.gate(tasks, options=options)['outcome'] == 'choice_pending'
    source = tmp_path/'choice.txt'; source.write_text('Choose implemented approach')
    current = descriptor(spec, 'selection', {'comparison': c}, options)
    current['definition']['inputs']['user_choice'] = str(source)
    task = tasks.prepare({'tasks': [current]})['tasks'][0]
    assert task['action'] == 'invoke' and task['key'] == s
    assert finish(tasks, task, {**selection(), 'mode': 'manual', 'user_source': str(source)})['checklist_complete']
    assert fix.gate(tasks, options=options)['stage'] == 'alignment'
    assert len(read(store.manifest)['entries'][c]['attempts']) == 1


@pytest.mark.parametrize('change', ['context', 'policy', 'missing-result'])
def test_changed_governing_inputs_block_without_repeating_or_destroying_completed_review(case, change):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, spec)
    options = None
    if change == 'context':
        Path(spec['inputs']['context']).write_text('Different requirements')
    elif change == 'policy':
        options = {'alternatives': {'auto_pick': {'min_score_gap': 20}}}
    else:
        Path(alternatives.accepted(tasks, refs['comparison'])['reference']).unlink()
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='reassessment|missing|damaged'):
        fix.gate(tasks, options=options)
    assert store.manifest.read_bytes() == before
    assert all(len(entry['attempts']) == 1 for entry in read(store.manifest)['entries'].values())


def test_disablement_and_simple_change_skip_preserve_state(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    with pytest.raises(ValueError, match='Initial alternatives gate'):
        tasks.prepare({'tasks': [ordinary(spec)]})
    assert not store.manifest.exists()
    options = {'alternatives': {'enabled': False, 'review_skill': 'unavailable:review'}}
    assert fix.gate(tasks, options=options)['outcome'] == 'disabled'
    assert [call['skill'] for _, call in skill_config.calls(fix.OWNER, options=options, stage='alternatives')] == [fix.OWNER]
    assert tasks.prepare({'tasks': [ordinary(spec, options)]})['tasks'][0]['action'] == 'invoke'
    refs = planned(tasks, spec, required=False)
    assert fix.gate(tasks)['outcome'] == 'skipped'
    assert len(refs) == 1


def test_fix_and_incoming_workflow_evidence_remain_independent(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    incoming = initial(tasks, spec, required=False)
    assert fix.gate(tasks)['stage'] == 'assessment'
    fixed = planned(tasks, spec, required=False)
    assert fixed['assessment'] != incoming['assessment']
    assert alternatives.accepted(tasks, incoming['assessment'])['report']['required'] is False
    assert alternatives.gate(tasks, incoming)['outcome'] == 'skipped'
    assert len(read(store.manifest)['entries']) == 2


@pytest.mark.parametrize('required', [False, True])
def test_fix_plan_cannot_release_incoming_review(case, required, monkeypatch):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, spec, required=required, alignment='aligned')
    metadata = Path(spec['git']['checkout']).parent/'initial-pr.json'
    monkeypatch.setattr(alternatives, 'live_pr', lambda pr: read(metadata))
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='incoming workflow evidence'):
        alternatives.gate(tasks, refs)
    with pytest.raises(ValueError, match='incoming workflow evidence'):
        tasks.prepare({'tasks': [incoming_task(spec, refs)]})
    assert store.manifest.read_bytes() == before


@pytest.mark.parametrize('stage', ['assessment', 'comparison', 'selection', 'alignment'])
def test_incoming_gate_rejects_each_fix_owned_result(case, stage, monkeypatch):
    spec, store = case; tasks = Tasks(REPO, PR)
    incoming = aligned(tasks, spec)
    fixed = planned(tasks, spec)
    incoming[stage] = fixed[stage]
    monkeypatch.setattr(alternatives, 'live_pr', lambda pr: pytest.fail('Foreign evidence reached live checks'))
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError):
        alternatives.gate(tasks, incoming)
    assert store.manifest.read_bytes() == before


def test_owner_separation_rejects_incoming_dependencies(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    incoming = initial(tasks, spec)
    with pytest.raises(ValueError, match='same workflow owner'):
        tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': incoming['assessment']})]})


def test_unaccepted_native_output_and_completion_checks_are_not_a_completed_initial_review(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    task = tasks.prepare({'tasks': [descriptor(spec, 'assessment')]})['tasks'][0]
    report = {'required': False, 'reason': 'Typo only', 'evidence': ['diff']}
    assert finish(tasks, task, report, native=False)['status'] == 'incomplete'
    pending = fix.gate(tasks)
    assert pending['outcome'] == 'review_required' and pending['next_task'] == {'key': task['key']}
    task = tasks.prepare({'tasks': [pending['next_task']]})['tasks'][0]
    from native_review_observations import observations
    report = {**report, 'complete': True, 'findings': []}
    output = json.dumps(report).encode()
    result = tasks.finish(task['key'], task['claim']['id'], output, report,
                          native_evidence=observations(task['assignment'], task['claim'], output))
    assert result['action'] == 'recheck'
    assert fix.gate(tasks)['outcome'] == 'review_required'
    from checklist_reports import report as checklist_report
    checked = checklist_report(result['checklist'], result['result'])
    assert tasks.finish(task['key'], result['claim'], json.dumps(checked).encode(), checked,
                        round_id=result['round'])['checklist_complete']
    assert fix.gate(tasks)['outcome'] == 'skipped'


def test_completed_plan_is_required_even_for_empty_fix_tasks_and_finalizers(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    fields = definition(spec)
    fields.pop('responsibility')
    fields['skill_identity'] = fix.OWNER
    item = {'kind': 'completion', 'definition': fields, 'options': {'tasks': [], 'finalizers': []}}
    with pytest.raises(ValueError, match='Initial alternatives gate'):
        tasks.prepare({'run': 'completion', 'tasks': [item]})
    refs = planned(tasks, spec, required=False)
    task = tasks.prepare({'run': 'completion', 'tasks': [item]})['tasks'][0]
    assert task['assignment']['options']['completion_plan'] == []
    assert any(entry['id'] == 'alternatives-plan' for entry in task['checklist']['items'])
    assert fix.gate(Tasks(REPO, PR.replace('/7', '/8')))['stage'] == 'assessment'
    assert fix.gate(tasks)['references'] == refs


def test_custom_provider_and_policy_changes_cannot_create_a_second_initial_review(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    options = {'alternatives': {'review_skill': 'team:planning'}}
    current = descriptor(spec, 'assessment', options=options)
    current['definition']['skill_identity'] = 'team:planning'
    task = tasks.prepare({'tasks': [current]})['tasks'][0]
    assert task['key'] == fix.key(tasks, 'assessment')
    assert finish(tasks, task, {'required': False, 'reason': 'Typo only', 'evidence': ['diff']})['checklist_complete']
    assert fix.gate(tasks, options=options)['outcome'] == 'skipped'
    before = store.manifest.read_bytes()
    replacement = deepcopy(current)
    replacement['definition']['skill_identity'] = 'other:planning'
    replacement['options']['alternatives']['review_skill'] = 'other:planning'
    with pytest.raises(ValueError, match='reassessment'):
        tasks.prepare({'tasks': [replacement]})
    with pytest.raises(ValueError, match='changed'):
        fix.gate(tasks, options=replacement['options'])
    assert store.manifest.read_bytes() == before


@pytest.mark.parametrize('field,value', [('cacheable', False), ('governing_inputs_complete', False)])
def test_once_per_pr_rejects_nonreusable_original_assignments_before_claiming(case, field, value):
    spec, store = case
    item = descriptor(spec, 'assessment')
    item['definition'][field] = value
    with pytest.raises(ValueError, match='cacheable original evidence'):
        Tasks(REPO, PR).prepare({'tasks': [item]})
    assert not store.manifest.exists()
