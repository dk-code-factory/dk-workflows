"""Offline shared rubric, architectural inventory and recovery acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import assessment_alternatives as plans
import pr_assessment as assessment
import skill_config
from state import read, write
from tasks import Tasks
from test_state import case, REPO, PR, ROOT, git
from test_batch_state import definition
from test_alternatives import compared, finish, selection
from native_review_observations import CONTRACT


def purpose(kind='small_fix'):
    weights = assessment.SMALL_FIX if kind == 'small_fix' else assessment.FEATURE
    return {'purpose': 'Make interrupted writes recoverable', 'scope': 'write path', 'work_type': kind,
            'rationale': 'Requirements distinguish recovery from new features', 'mandatory_constraints': ['Never duplicate writes'],
            'rubrics': [{'id': 'writes', 'scope': 'write path', 'work_type': kind, 'weights': weights,
                         'anchors': {k: {'0': 'Fails required cases', '50': 'Covers ordinary cases', '100': 'Covers relevant edges'} for k in weights},
                         'rationale': 'Recovery and focused implementation' if kind == 'small_fix' else 'Actual maintenance consequences',
                         'evidence': ['issue:1']}], 'evidence': ['issue:1']}


def inventory(*ids):
    return {'coverage': 'Examined inherited branch architecture and the complete PR branch',
            'decisions': [{'id': name, 'origin': origin, 'scope': 'write path', 'rubric': 'writes',
                           'decision': 'Journal write ownership', 'questionable': True,
                           'reason': 'Recovery cost warrants comparison under writes rubric', 'evidence': ['code:1']}
                          for name, origin in ids], 'evidence': ['branch:1']}


def comparison(spec, kind='small_fix'):
    value = compared()
    value.pop('profile'); value.pop('profile_rationale'); value.pop('anchors')
    value.update(rubric='writes', target_revision=spec['git']['target'])
    for option in value['solutions']:
        rating = option['ratings']['quality']
        weights = purpose(kind)['rubrics'][0]['weights']
        option['ratings'] = {k: rating for k in weights}
        option['rating_evidence'] = {k: 'code:1' for k in weights}
    return value


def descriptor(spec, stage, deps=None, *, owner=assessment.REVIEW, decision=None, policy=None, inputs=None):
    value = deepcopy(definition(spec))
    value['skill_identity'] = plans.identity(owner, stage, decision)[1]
    value['native_review'] = CONTRACT
    if stage == 'alignment':
        metadata = Path(spec['git']['checkout']).parent/'assessment-pr.json'
        if not metadata.exists():
            write(metadata, {'body': 'Original PR scope', 'headRefOid': spec['git']['head']})
        value['inputs']['alternatives_current_pr'] = str(metadata)
    if inputs:
        value['inputs'].update(inputs)
    result = {'kind': plans.KIND, 'skill': owner, 'stage': stage, 'dependencies': deps or {}, 'definition': value}
    if decision:
        result['decision'] = decision
    if policy:
        result['policy_options'] = policy
    return result


def complete(tasks, spec, stage, report, deps=None, **kwargs):
    task = tasks.prepare({'tasks': [descriptor(spec, stage, deps, **kwargs)]})['tasks'][0]
    assert task['action'] == 'invoke'
    result = finish(tasks, task, report)
    assert result['checklist_complete']
    return task['key']


def initial(tasks, spec, *, owner=assessment.REVIEW, kind='small_fix', mismatch=False):
    refs = {}
    refs['assessment'] = complete(tasks, spec, 'assessment', purpose(kind), owner=owner)
    refs['inventory'] = complete(tasks, spec, 'inventory', inventory(('storage', 'inherited'), ('boundary', 'materially_changed')),
                                 {'assessment': refs['assessment']}, owner=owner)
    for decision in ('storage', 'boundary'):
        refs['comparison:' + decision] = complete(tasks, spec, 'comparison', comparison(spec, kind),
            {k: refs[k] for k in ('assessment', 'inventory')}, owner=owner, decision=decision)
        refs['selection:' + decision] = complete(tasks, spec, 'selection', selection(),
            {'assessment': refs['assessment'], 'comparison': refs['comparison:' + decision]}, owner=owner, decision=decision)
    refs['interactions'] = complete(tasks, spec, 'interactions', {'compatible': True, 'reason': 'One coherent write owner', 'evidence': ['code:1']},
        {'assessment': refs['assessment'], 'inventory': refs['inventory'], **{'selection_' + d: refs['selection:' + d] for d in ('storage', 'boundary')}}, owner=owner)
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': 'mismatch' if mismatch else 'aligned', 'reason': 'Matches selected owner',
                                                         'required_changes': 'Apply the selected write owner', 'evidence': ['code:1']},
        {k: refs[k] for k in ('assessment', 'interactions')}, owner=owner)
    return refs


@pytest.mark.parametrize('kind', ['small_fix', 'feature'])
def test_shared_rubric_and_target_arithmetic(case, kind):
    spec, _ = case
    report = purpose(kind)
    assert assessment.validate(report) == report
    result = plans.compare(comparison(spec, kind), report, 'writes', spec['git']['target'])
    assert result['solutions'][0]['total'] == 90
    assert result['solutions'][1]['total'] == 70
    if kind == 'feature':
        assert 'low_loc' not in result['weights'] and 'low_intrusiveness' not in result['weights']
        report['rubrics'][0]['weights'] = {'low_loc': 100}
        with pytest.raises(ValueError, match='Feature ranking'):
            assessment.validate(report)
    else:
        assert result['weights']['edge_coverage'] == 40 and result['weights']['low_loc'] == 10


def test_mixed_scopes_and_rubric_substitution_rejected(case):
    spec, _ = case
    report = purpose(); feature = purpose('feature')['rubrics'][0]; feature['id'] = 'new-api'
    report.update(work_type='mixed', rubrics=[*report['rubrics'], feature])
    assessment.validate(report)
    value = comparison(spec); value['weights'] = {'quality': 100}
    with pytest.raises(ValueError, match='cannot replace'):
        plans.compare(value, report, 'writes', spec['git']['target'])
    with pytest.raises(ValueError, match='target revision'):
        plans.compare(comparison(spec), report, 'writes', spec['git']['head'])


@pytest.mark.parametrize('mode', ['automatic', 'manual', 'semi-manual'])
@pytest.mark.parametrize('second,expected', [(80, 'implemented'), (81, None), (90, None)])
def test_modes_score_gap_and_pending(case, mode, second, expected):
    spec, _ = case
    report = comparison(spec)
    report['solutions'][1]['ratings'] = {k: second for k in report['solutions'][1]['ratings']}
    compared_value = plans.compare(report, purpose(), 'writes', spec['git']['target'])
    policy = {**assessment.resolve()['selection'], 'mode': mode}
    automatic = selection(choice=None if mode == 'manual' else expected)
    assert plans.select(compared_value, policy, automatic) == (None if mode == 'manual' else expected)
    manual = {'mode': 'manual', 'choice': 'smaller', 'user_source': 'message:1', 'reason': 'Prefer this feasible option'}
    if mode == 'automatic':
        with pytest.raises(ValueError, match='cannot consume a manual'):
            plans.select(compared_value, policy, manual)
    else:
        assert plans.select(compared_value, policy, manual) == 'smaller'


def test_confidence_doubts_feasibility_checks_shared(case):
    spec, _ = case
    compared_value = plans.compare(comparison(spec), purpose(), 'writes', spec['git']['target'])
    policy = assessment.resolve()['selection']
    assert plans.select(compared_value, policy, selection(confident=False)) is None
    compared_value['solutions'][1]['material_doubts'] = ['Unknown recovery boundary']
    assert plans.select(compared_value, policy, selection()) is None
    compared_value['solutions'][1]['material_doubts'] = []
    compared_value['solutions'][0]['feasible'] = False
    with pytest.raises(ValueError, match='feasible compared'):
        plans.select(compared_value, policy, {'mode': 'manual', 'choice': 'implemented', 'user_source': 'message:1', 'reason': 'Chosen'})


def test_cross_route_entry_resumes_original_inventory_and_shared_assessment(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    shared = complete(tasks, spec, 'assessment', purpose(), owner=assessment.FIX)
    fix = complete(tasks, spec, 'inventory', inventory(('implementation', 'introduced')), {'assessment': shared}, owner=assessment.FIX)
    with pytest.raises(ValueError, match='Resume the original alternatives chain'):
        complete(tasks, spec, 'inventory', inventory(('ownership', 'inherited')), {'assessment': shared})
    original = plans.accepted(tasks, shared)['reference']
    assert plans.accepted(tasks, fix)['spec']['inputs']['assessed_assessment'] == original
    resumed = plans.gate(tasks, assessment.REVIEW)
    assert resumed['stage'] == 'comparison' and resumed['next_task']['skill'] == assessment.FIX
    assert plans.key(tasks, assessment.FIX, 'assessment') == plans.key(tasks, assessment.REVIEW, 'assessment')
    assert len(read(store.manifest)['entries'][shared]['attempts']) == 1


def test_completed_fresh_process_and_later_commit_reuse(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    original = {key: plans.accepted(tasks, key)['reference'] for key in refs.values()}
    checkout = spec['git']['checkout']; Path(checkout, 'code').write_text('authorized subsequent implementation\n')
    git(checkout, 'commit', '-qam', 'implement selected approach')
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO, '--pr', PR, 'plan', '--workflow', 'review']
    for _ in range(2):
        result = json.loads(subprocess.check_output(command, text=True))
        assert result['outcome'] == 'planned' and result['release_reviewers']
        assert set(result['governing_evidence']) == set(original.values())
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}


def test_partial_recovery_preserves_completed_decision_and_claim(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    shared = complete(tasks, spec, 'assessment', purpose())
    inv = complete(tasks, spec, 'inventory', inventory(('one', 'inherited'), ('two', 'introduced')), {'assessment': shared})
    first = complete(tasks, spec, 'comparison', comparison(spec), {'assessment': shared, 'inventory': inv}, decision='one')
    chosen = complete(tasks, spec, 'selection', selection(), {'assessment': shared, 'comparison': first}, decision='one')
    pending = tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': shared, 'inventory': inv}, decision='two')]})['tasks'][0]
    result = plans.gate(Tasks(REPO, PR), assessment.REVIEW)
    assert result['stage'] == 'comparison' and result['next_task']['decision'] == 'two'
    retry = tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]
    assert retry['action'] == 'recover_required' and retry['unfinished']['id'] == pending['claim']['id']
    assert plans.accepted(tasks, chosen)['report']['choice'] == 'implemented'
    assert len(read(store.manifest)['entries'][first]['attempts']) == 1


def test_mode_only_change_preserves_settled_selection(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    paths = {k: plans.accepted(tasks, refs[k])['reference'] for k in ('assessment', 'inventory', 'comparison:storage', 'comparison:boundary')}
    settings = {'skill_defaults': {assessment.PR: {'options': {'selection': {'mode': 'manual'}}}}}
    write(skill_config.scope_path(REPO), settings)
    result = plans.gate(tasks, assessment.REVIEW)
    assert result['outcome'] == 'planned' and result['design_context']
    assert plans.accepted(tasks, refs['selection:storage'], historical=True)['report']['choice'] == 'implemented'
    assert {k: plans.accepted(tasks, refs[k])['reference'] for k in paths} == paths


def test_mode_only_change_does_not_repeat_selection_or_alignment(case):
    spec, _ = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    original = {name: plans.accepted(tasks, refs[name])['reference'] for name in ('interactions', 'alignment')}
    write(skill_config.scope_path(REPO), {'skill_defaults': {assessment.PR: {'options': {'selection': {'mode': 'automatic'}}}}})
    result = plans.gate(tasks, assessment.REVIEW)
    assert result['outcome'] == 'planned' and result['current_alignment_required']
    assert plans.gate(Tasks(REPO, PR), assessment.REVIEW)['outcome'] == 'planned'
    assert {name: plans.accepted(tasks, refs[name])['reference'] for name in original} == original


def test_manual_choice_requires_bound_source(case, tmp_path):
    spec, _ = case; tasks = Tasks(REPO, PR)
    shared = complete(tasks, spec, 'assessment', purpose())
    inv = complete(tasks, spec, 'inventory', inventory(('one', 'inherited')), {'assessment': shared})
    compared_key = complete(tasks, spec, 'comparison', comparison(spec), {'assessment': shared, 'inventory': inv}, decision='one')
    source = tmp_path/'choice.txt'; source.write_text('Choose smaller to simplify recovery')
    report = selection(mode='manual', choice='smaller', user_source=str(source), reason='User prefers smaller',
                       rejection_reasons={'implemented': 'User declines additional complexity'}, evidence=[str(source)])
    d = descriptor(spec, 'selection', {'assessment': shared, 'comparison': compared_key}, decision='one')
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    with pytest.raises(ValueError, match='bound as an input'):
        finish(tasks, task, report)
    d['definition']['inputs']['user_choice'] = str(source)
    task = tasks.prepare({'recover': {task['key']: task['claim']['id']}, 'tasks': [d]})['tasks'][0]
    assert finish(tasks, task, report)['checklist_complete']


def test_changed_requirements_need_live_alignment_and_missing_evidence_needs_restoration(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    Path(spec['inputs']['context']).write_text('Changed governing scope')
    result = plans.gate(tasks, assessment.REVIEW)
    assert result['outcome'] == 'planned' and result['current_alignment_required']
    Path(spec['inputs']['context']).write_text('scope and decisions')
    path = plans.accepted(tasks, refs['comparison:boundary'])['reference']
    Path(path).unlink()
    recovery = plans.gate(tasks, assessment.REVIEW)
    assert recovery['stage'] == 'comparison' and recovery['outcome'] == 'recovery_required'
    assert recovery['next_task'] == {'key': refs['comparison:boundary']}
    assert plans.accepted(tasks, refs['comparison:storage'])['report']['rubric'] == 'writes'


def test_shared_and_legacy_configuration_conflicts(case):
    _, store = case
    path = skill_config.scope_path(REPO)
    saved = {'skill_defaults': {assessment.FIX: {'options': {'alternatives': {'auto_pick': {'min_score_gap': 15}}}}}}
    write(path, saved); before = path.read_bytes()
    assert assessment.resolve(REPO)['selection']['min_score_gap'] == 15
    assert path.read_bytes() == before
    saved['skill_defaults'][assessment.REVIEW] = {'options': {'alternatives': {'auto_pick': {'min_score_gap': 20}}}}
    write(path, saved)
    assert assessment.resolve(REPO, owner=assessment.FIX)['selection']['min_score_gap'] == 15
    assert assessment.resolve(REPO, owner=assessment.REVIEW)['selection']['min_score_gap'] == 20
    saved['skill_defaults'].pop(assessment.REVIEW)
    saved['skill_defaults'][assessment.SKILL] = {'options': {'selection': {'min_score_gap': 10}}}
    write(path, saved)
    with pytest.raises(ValueError, match='configuration conflict'):
        assessment.resolve(REPO)
    saved['skill_defaults'][assessment.ISSUE] = {'options': {'selection': {'min_score_gap': 25}}}
    write(path, saved)
    assert assessment.resolve(REPO)['selection']['min_score_gap'] == 25
    assert not store.manifest.exists()


def test_selection_is_owned_by_each_alternatives_skill(case):
    _, store = case
    saved = {'skill_defaults': {
        assessment.ISSUE: {'options': {'selection': {'mode': 'automatic'}}},
        assessment.PR: {'options': {'selection': {'mode': 'manual', 'min_score_gap': 20}}}}}
    path = skill_config.scope_path(REPO); write(path, saved); original = path.read_bytes()
    assert assessment.resolve(REPO, owner=assessment.FIX)['selection']['mode'] == 'automatic'
    assert assessment.resolve(REPO, owner=assessment.REVIEW)['selection']['mode'] == 'manual'
    for skill in (assessment.ISSUE, assessment.PR):
        assert assessment.resolve(REPO, skill=skill)['selection'] == skill_config.effective(skill, REPO)['options']['selection']
    assert assessment.resolve(REPO, owner=assessment.REVIEW,
                              alternatives_options={'selection': {'mode': 'semi-manual'}})['selection']['mode'] == 'semi-manual'
    assert path.read_bytes() == original and not store.manifest.exists()
    for skill in (assessment.ISSUE, assessment.PR):
        with pytest.raises(ValueError, match='custom_prompt'):
            skill_config.effective(skill, options={'scoring': {'profile': 'custom'}})


def test_former_assessment_selection_is_a_read_only_fallback(case):
    _, store = case
    path = skill_config.scope_path(REPO)
    saved = {'skill_defaults': {assessment.SKILL: {'options': {'selection': {'mode': 'manual'}}}}}
    write(path, saved); before = path.read_bytes()
    for skill in (assessment.ISSUE, assessment.PR):
        assert skill_config.effective(skill, REPO)['options']['selection']['mode'] == 'manual'
    assert path.read_bytes() == before
    saved['skill_defaults'][assessment.ISSUE] = {'options': {'selection': {'mode': 'automatic'}}}
    write(path, saved)
    assert skill_config.effective(assessment.ISSUE, REPO)['options']['selection']['mode'] == 'automatic'
    assert skill_config.effective(assessment.PR, REPO)['options']['selection']['mode'] == 'manual'
    assert not store.manifest.exists()


def test_controller_injects_assessment_without_an_installed_assessment_skill(case):
    spec, _ = case; tasks = Tasks(REPO, PR)
    assert skill_config.directory(assessment.SKILL) is None
    assert assessment.SKILL not in skill_config.catalog()
    task = tasks.prepare({'tasks': [descriptor(spec, 'assessment')]})['tasks'][0]
    assigned = task['assignment']
    assert assigned['skill']['identity'] == assessment.PR
    assert skill_config.defaults(assessment.PR)['assessment_prompt'] in assigned['task']['instructions']
    assert all('/skills/dk-pr-assessment' not in path for path in assigned['skill']['resources'].values())
    assert 'assessed_prompt' not in assigned['skill']['resources']
    assert finish(tasks, task, purpose())['checklist_complete']
    for owner in (assessment.FIX, assessment.REVIEW):
        calls = [call.get('skill') for _, call in skill_config.calls(owner, stage='alternatives')]
        assert assessment.SKILL not in calls
        assert plans.gate(Tasks(REPO, PR), owner)['stage'] == 'inventory'
    from state import task_key
    assert plans.key(tasks, assessment.FIX, 'assessment') == plans.key(tasks, assessment.REVIEW, 'assessment')
    assert plans.key(tasks, assessment.FIX, 'assessment') == task_key(tasks.store.subject, 'pr-purpose-assessment', assessment.SKILL)


def test_old_issue_name_preserves_configuration_and_task_keys(case):
    from state import task_key
    import invocation
    old = 'dk-workflows:dk-issue-implementation-alternatives'
    spec, store = case
    path = skill_config.scope_path(REPO)
    write(path, {'skill_defaults': {old: {'options': {'selection': {'mode': 'automatic'}}}}})
    before = path.read_bytes()
    assert invocation.normalize(old)['skill'] == assessment.ISSUE
    assert skill_config.effective(old, REPO)['options']['selection']['mode'] == 'automatic'
    assert assessment.resolve(REPO, owner=assessment.FIX)['selection']['mode'] == 'automatic'
    for stage in ('inventory', 'comparison', 'selection', 'interactions', 'alignment'):
        name, identity = plans.identity(assessment.FIX, stage, 'storage' if stage in {'comparison', 'selection'} else None)
        assert task_key(store.subject, name, identity) == task_key(store.subject, name, old)
    assert path.read_bytes() == before and not store.manifest.exists()


def test_scoped_and_invocation_selection_cli_match_the_selected_skill(case, tmp_path):
    spec, store = case
    write(skill_config.scope_path(REPO), {'skill_defaults': {
        assessment.ISSUE: {'options': {'selection': {'mode': 'automatic'}}},
        assessment.PR: {'options': {'selection': {'mode': 'manual'}}}}})
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO]
    for skill, mode in ((assessment.ISSUE, 'automatic'), (assessment.PR, 'manual')):
        result = json.loads(subprocess.check_output([*command, '--skill', skill, 'policy'], text=True))
        assert result['selection']['mode'] == mode
    local = tmp_path/'options.json'; write(local, {'selection': {'mode': 'semi-manual'}})
    result = json.loads(subprocess.check_output([
        *command, '--skill', assessment.PR, '--alternatives-options', str(local), 'policy'], text=True))
    assert result['selection']['mode'] == 'semi-manual' and not store.manifest.exists()


def test_legacy_whole_comparison_cannot_complete_architectural_inventory(case):
    from test_alternatives import complete as legacy_complete
    spec, _ = case; tasks = Tasks(REPO, PR)
    old = legacy_complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Compare recovery', 'evidence': ['code:1']})
    with pytest.raises(ValueError, match='legacy results remain recoverable'):
        plans.accepted(tasks, old)
    assert plans.gate(tasks, assessment.REVIEW)['stage'] == 'comparison'


def test_disabled_alternatives_bypasses_plan_and_prevents_new_native_work(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    options = {'alternatives': {'enabled': False}}
    assert plans.gate(tasks, assessment.REVIEW, options=options)['release_reviewers']
    d = descriptor(spec, 'assessment'); d['options'] = options
    with pytest.raises(ValueError, match='Alternatives is disabled'):
        tasks.prepare({'tasks': [d]})
    assert not store.manifest.exists()


def test_issue_only_evaluation_creates_no_pr_record(case, tmp_path):
    spec, store = case
    report = comparison(spec)
    report['solutions'][0]['kind'] = 'alternative'
    for name, value in [('assessment', purpose()), ('comparison', report), ('selection', selection())]:
        write(tmp_path/(name + '.json'), value)
    result = json.loads(subprocess.check_output([
        sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO,
        '--checkout', spec['git']['checkout'], 'issue-evaluate',
        '--assessment', str(tmp_path/'assessment.json'), '--comparison', str(tmp_path/'comparison.json'),
        '--selection', str(tmp_path/'selection.json'), '--target', spec['git']['target']], text=True))
    assert result['choice'] == 'implemented'
    assert not store.manifest.exists()


def test_manual_refresh_reuses_shared_assessment_and_same_request_siblings(case, tmp_path, monkeypatch):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    shared_result = plans.accepted(tasks, refs['assessment'])['reference']
    request = tmp_path/'request.json'
    write(request, {'id': 'message-2', 'source': 'conversation:2', 'instruction': 'Review PR alternatives again', 'skill': 'dk-workflows:dk-pr-review-alternatives', 'origin': 'user'})
    result = plans.gate(tasks, assessment.REVIEW, request=str(request))
    assert result['stage'] == 'inventory'
    task = tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]
    assert task['action'] == 'invoke'
    finish(tasks, task, inventory(('storage', 'inherited'), ('boundary', 'materially_changed')))
    result = plans.gate(tasks, assessment.REVIEW, request=str(request))
    assert result['stage'] == 'comparison' and result['next_task']['decision'] == 'storage'
    task = tasks.prepare({'tasks': [result['next_task']]})['tasks'][0]
    finish(tasks, task, comparison(spec))
    again = plans.gate(Tasks(REPO, PR), assessment.REVIEW, request=str(request))
    assert again['stage'] == 'selection'
    assert plans.accepted(tasks, refs['assessment'])['reference'] == shared_result
    assert len(read(store.manifest)['entries'][refs['assessment']]['attempts']) == 1


def test_interactions_need_every_settled_choice_and_cannot_release_conflicts(case):
    spec, _ = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    deps = {'assessment': refs['assessment'], 'inventory': refs['inventory'], 'selection_storage': refs['selection:storage']}
    with pytest.raises(ValueError, match='exactly every'):
        tasks.prepare({'tasks': [descriptor(spec, 'interactions', deps)]})
    deps['selection_boundary'] = refs['selection:boundary']
    task_descriptor = descriptor(spec, 'interactions', deps)
    # Changed semantic interaction evidence is a new explicit request.
    source = Path(spec['git']['checkout']).parent/'interaction-request.json'
    write(source, {'id': 'message-3', 'source': 'conversation:3', 'instruction': 'Recheck approach interactions', 'skill': 'dk-workflows:dk-pr-review-alternatives', 'origin': 'user'})
    task_descriptor['definition']['inputs']['alternatives_request'] = str(source)
    task = tasks.prepare({'tasks': [task_descriptor]})['tasks'][0]
    finish(tasks, task, {'compatible': False, 'reason': 'Two owners conflict', 'required_changes': 'Choose one owner', 'evidence': ['code:1']})
    assert plans.gate(tasks, assessment.REVIEW)['outcome'] == 'interactions_blocked'


def test_empty_inventory_retains_only_justified_decisions(case):
    spec, _ = case; tasks = Tasks(REPO, PR)
    shared = complete(tasks, spec, 'assessment', purpose())
    retained = inventory(('write-owner', 'inherited'))
    retained['decisions'][0]['questionable'] = False
    retained['decisions'][0]['reason'] = 'Meets the scoped rubric without material trade-off concerns'
    inv = complete(tasks, spec, 'inventory', retained, {'assessment': shared})
    with pytest.raises(ValueError, match='only decisions judged questionable'):
        tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': shared, 'inventory': inv}, decision='write-owner')]})
    assert plans.gate(tasks, assessment.REVIEW)['stage'] == 'interactions'


def test_added_inventory_sibling_reuses_unchanged_comparisons(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    preserved = {k: plans.accepted(tasks, refs[k])['reference'] for k in ('comparison:storage', 'comparison:boundary')}
    source = tmp_path/'scope-request.json'
    write(source, {'id': 'message-4', 'source': 'conversation:4', 'instruction': 'Include the additional API decision', 'skill': 'dk-workflows:dk-pr-review-alternatives', 'origin': 'user'})
    task = tasks.prepare({'tasks': [descriptor(spec, 'inventory', {'assessment': refs['assessment']},
                            inputs={'alternatives_reassessment': str(source)})]})['tasks'][0]
    finish(tasks, task, inventory(('storage', 'inherited'), ('boundary', 'materially_changed'), ('api', 'introduced')))
    result = plans.gate(Tasks(REPO, PR), assessment.REVIEW)
    assert result['stage'] == 'comparison' and result['next_task']['decision'] == 'api'
    assert {k: plans.accepted(tasks, refs[k])['reference'] for k in preserved} == preserved
    assert all(len(read(store.manifest)['entries'][refs[k]]['attempts']) == 1 for k in preserved)


@pytest.mark.parametrize('owner', [assessment.FIX, assessment.REVIEW])
def test_managed_workflow_occurrences_consume_new_plan(case, owner):
    spec, _ = case; tasks = Tasks(REPO, PR)
    initial(tasks, spec, owner=owner)
    fields = deepcopy(definition(spec)); fields['skill_identity'] = 'dk-workflows:dk-pr-feedback-read'
    task = tasks.prepare({'tasks': [{'kind': 'skill', 'skill': owner, 'selection': 'tasks', 'index': 0,
                                     'definition': fields}]})['tasks'][0]
    prefix = 'alternatives_fix_governing_' if owner == assessment.FIX else 'alternatives_governing_'
    assert len([k for k in task['assignment']['inputs'] if k.startswith(prefix)]) == 8
    assert task['assignment']['git']['head'] == spec['git']['head']
    assert Path(spec['git']['checkout'], 'code').read_text() == 'review me\n'


def test_new_design_rejection_preserves_verification_publication_and_later_head_recovery(case, monkeypatch):
    import alternatives
    from test_alternatives import verified_journal
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, mismatch=True)
    task = tasks.prepare({'tasks': [descriptor(spec, 'verification', {'alignment': refs['alignment']})]})['tasks'][0]
    for name in ('assessed_assessment', 'assessed_selection_storage', 'assessed_selection_storage_comparison',
                 'assessed_selection_boundary', 'assessed_selection_boundary_comparison'):
        assert name in task['assignment']['inputs']
    report = {'disposition': 'confirmed', 'reason': 'Selected owner differs materially',
              'required_changes': 'Use the selected owner', 'evidence': ['code:1']}
    finish(tasks, task, report, handle='independent-verifier')
    assert plans.gate(tasks, assessment.REVIEW)['outcome'] == 'mismatch_verified'
    assert plans.gate(tasks, assessment.REVIEW, analysis_only=True)['outcome'] == 'publication_blocked'
    operation = verified_journal(tasks, spec, kind='post-review', event='REQUEST_CHANGES', comments=[], body=report['required_changes'])
    live = {'body': 'Original PR scope', 'headRefOid': spec['git']['head']}
    monkeypatch.setattr(alternatives, 'live_pr', lambda pr: live)
    before = store.manifest.read_bytes()
    result = plans.gate(tasks, assessment.REVIEW, review_operation=operation)
    assert result['outcome'] == 'alternatives_rejected' and not result['ordinary_review_complete']
    live['headRefOid'] = 'f' * 40
    result = plans.gate(Tasks(REPO, PR), assessment.REVIEW, review_operation=operation)
    assert result['outcome'] == 'planned' and result['release_reviewers']
    assert store.manifest.read_bytes() == before
    assert Path(spec['git']['checkout'], 'code').read_text() == 'review me\n'


@pytest.mark.parametrize('owner', [assessment.FIX, assessment.REVIEW])
def test_fix_handoff_and_readonly_review(case, owner):
    spec, _ = case; tasks = Tasks(REPO, PR)
    initial(tasks, spec, owner=owner, mismatch=True)
    result = plans.gate(tasks, owner)
    if owner == assessment.FIX:
        assert result['release_reviewers'] and result['required_changes'] == 'Apply the selected write owner'
        # Authorized implementation consumes the original plan as governing input.
        downstream = deepcopy(spec)
        plans.enforce(downstream, tasks, owner)
        assert any(k.startswith('alternatives_fix_governing_') for k in downstream['inputs'])
        assert any('apply selected approaches' in item['text'].lower() or 'apply its selected approach' in item['text'].lower()
                   for item in __import__('checklists').contract(downstream, {})['items'])
    else:
        assert not result['release_reviewers'] and result['stage'] == 'verification'
        assert Path(spec['git']['checkout'], 'code').read_text() == 'review me\n'
