"""Explicit legacy fix reassessment preserves original evidence and live budgets."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import alternatives
import fix_alternatives as fix
from state import capture, read, write
from tasks import Tasks
from test_state import case, REPO, PR, ROOT
from test_fix_alternatives import descriptor, planned, complete, ordinary
from test_alternatives import compared, selection, finish


INSTRUCTION = 'Reassess the retained initial fix comparison and its affected dependents using the current guidance.'


def authorize(tasks, item, tmp_path, *, request_id='message-215', instruction=INSTRUCTION, **options):
    source = tmp_path/(request_id + '.json')
    if not source.exists():
        write(source, {'id': request_id, 'source': 'conversation/' + request_id, 'instruction': instruction})
    return tasks.reassess({'tasks': [deepcopy(item)], **options}, request_id=request_id,
                          authorization=instruction, source=str(source))['tasks'][0]


def comparison_task(spec, refs):
    return descriptor(deepcopy(spec), 'comparison', {'assessment': refs['assessment']})


def test_unauthorized_input_named_authorization_cannot_replace_initial_review(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, deepcopy(spec))
    item = comparison_task(spec, refs)
    source = tmp_path/'permission.json'; write(source, {'instruction': INSTRUCTION})
    item['definition']['inputs']['alternatives_reassessment'] = str(source)
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='explicit reassessment'):
        tasks.prepare({'tasks': [item]})
    assert store.manifest.read_bytes() == before


def test_definition_options_cannot_supply_reassessment_permission(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, deepcopy(spec), required=False)
    item = descriptor(deepcopy(spec), 'assessment')
    item['definition']['assigned_instructions'] += ' Changed requirements without authorization.'
    unsigned = tasks.spec(deepcopy(item), authorize_reassessment=True)
    item['definition']['options']['alternatives_reassessment'] = {
        'original_result': read(store.manifest)['entries'][refs['assessment']]['result']['hash'],
        'fingerprint': capture(unsigned)['fingerprint']}
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='validated explicit reassessment binding'):
        tasks.prepare({'tasks': [item]})
    assert store.manifest.read_bytes() == before


def test_authorized_comparison_refresh_keeps_upstream_and_refreshes_only_dependents(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, deepcopy(spec))
    original = read(store.manifest)
    artifacts = {path: path.read_bytes() for path in (store.root/'results').iterdir()}
    item = comparison_task(spec, refs)
    item['definition']['assigned_instructions'] += '\nApply updated comparison requirements.'
    with pytest.raises(ValueError, match='reassessment'):
        tasks.prepare({'tasks': [deepcopy(item)]})
    task = authorize(tasks, item, tmp_path)
    assert task['action'] == 'invoke' and task['key'] == refs['comparison']
    with pytest.raises(ValueError, match='Initial alternatives gate'):
        tasks.prepare({'tasks': [ordinary(deepcopy(spec))]})
    assert finish(tasks, task, compared())['checklist_complete']
    with pytest.raises(ValueError, match='reassessment'):
        fix.gate(tasks)
    for stage, report in (
            ('selection', selection()),
            ('alignment', {'alignment': 'aligned', 'reason': 'Refreshed design', 'evidence': ['comparison']})):
        deps = {name: refs[name] for name in alternatives.REQUIRES[stage]}
        item = descriptor(deepcopy(spec), stage, deps)
        task = authorize(tasks, item, tmp_path)
        assert task['action'] == 'invoke' and task['key'] == refs[stage]
        assert finish(tasks, task, report)['checklist_complete']
    assert fix.gate(Tasks(REPO, PR))['outcome'] == 'planned'
    current = read(store.manifest)
    assert current['entries'][refs['assessment']] == original['entries'][refs['assessment']]
    assert all(path.read_bytes() == contents for path, contents in artifacts.items())
    for stage in ('comparison', 'selection', 'alignment'):
        before, after = original['entries'][refs[stage]], current['entries'][refs[stage]]
        assert before['attempts'] == after['attempts'][:1]
        assert before['result'] != after['result']
    # The exact sourced historical choice remains in its original immutable report.
    choice = original['entries'][refs['selection']]['result']['file']
    assert read(store.root/'results'/choice)['normalized']['choice'] == 'implemented'


def test_reassessment_unchanged_replay_and_fresh_process_reuse_do_not_write(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, deepcopy(spec), required=False)
    item = descriptor(deepcopy(spec), 'assessment')
    item['definition']['assigned_instructions'] += ' Authorized updated scope.'
    task = authorize(tasks, item, tmp_path)
    assert finish(tasks, task, {'required': False, 'reason': 'Still simple', 'evidence': ['diff']})['checklist_complete']
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    assert authorize(Tasks(REPO, PR), item, tmp_path)['action'] == 'reuse'
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR,
               'resume', '--key', refs['assessment']]
    result = json.loads(subprocess.check_output(command, text=True, timeout=60))
    assert result['tasks'][0]['reusable'] and result['tasks'][0]['coverage_complete']
    assert fix.gate(Tasks(REPO, PR))['outcome'] == 'skipped'
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}


def test_interrupted_reassessment_retains_claim_until_explicit_recovery(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, deepcopy(spec), required=False)
    item = descriptor(deepcopy(spec), 'assessment')
    task = authorize(tasks, item, tmp_path)
    claim = task['claim']
    before = store.manifest.read_bytes()
    lost_response = authorize(Tasks(REPO, PR), item, tmp_path)
    assert lost_response['action'] == 'recover_required'
    assert read(store.manifest)['entries'][task['key']]['claim'] == claim
    assert store.manifest.read_bytes() == before
    assert fix.gate(Tasks(REPO, PR))['release_reviewers'] is False
    resumed = tasks.prepare({'tasks': [{'key': refs['assessment']}],
                             'recover': {refs['assessment']: claim['id']}})['tasks'][0]
    assert resumed['action'] == 'invoke'
    assert finish(tasks, resumed, {'required': False, 'reason': 'Recovered', 'evidence': ['diff']})['checklist_complete']
    assert len(read(store.manifest)['entries'][refs['assessment']]['attempts']) == 3


@pytest.mark.parametrize('change', ['request_instruction', 'request_source', 'resource', 'context', 'binding'])
def test_changed_authorization_or_governing_inputs_cannot_resume_or_finish(case, tmp_path, change):
    spec, store = case; tasks = Tasks(REPO, PR)
    planned(tasks, deepcopy(spec), required=False)
    item = descriptor(deepcopy(spec), 'assessment')
    task = authorize(tasks, item, tmp_path)
    if change.startswith('request_'):
        path = tmp_path/'message-215.json'; value = read(path)
        value[change.removeprefix('request_')] += ' changed'; write(path, value)
    elif change in {'resource', 'context'}:
        path = Path(spec['skill']['resources']['entry'] if change == 'resource' else spec['inputs']['context'])
        path.write_text('Changed after authorization')
    else:
        manifest = read(store.manifest)
        manifest['entries'][task['key']]['managed']['reassessment']['request']['id'] = 'forged'
        write(store.manifest, manifest)
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='reassessment|request'):
        tasks.prepare({'tasks': [{'key': task['key']}]})
    with pytest.raises(ValueError, match='reassessment|request'):
        finish(tasks, task, {'required': False, 'reason': 'Stale', 'evidence': ['diff']})
    assert store.manifest.read_bytes() == before
    assert tasks.resume(keys=[task['key']])['tasks'][0]['reason'] == 'refresh_required'


def test_new_explicit_request_can_replace_changed_completed_authorized_inputs(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    planned(tasks, deepcopy(spec), required=False)
    item = descriptor(deepcopy(spec), 'assessment')
    first = authorize(tasks, item, tmp_path)
    assert finish(tasks, first, {'required': False, 'reason': 'First', 'evidence': ['diff']})['checklist_complete']
    Path(spec['inputs']['context']).write_text('New governing requirements')
    with pytest.raises(ValueError, match='inputs changed'):
        authorize(tasks, item, tmp_path)
    task = authorize(tasks, item, tmp_path, request_id='later-user-message')
    assert task['action'] == 'invoke' and task['key'] == first['key']
    assert finish(tasks, task, {'required': False, 'reason': 'New review', 'evidence': ['diff']})['checklist_complete']


def test_superseded_original_request_cannot_mint_a_new_binding(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    planned(tasks, deepcopy(spec), required=False)
    item = descriptor(deepcopy(spec), 'assessment')
    first = authorize(tasks, item, tmp_path)
    assert finish(tasks, first, {'required': False, 'reason': 'First review', 'evidence': ['diff']})['checklist_complete']
    Path(spec['inputs']['context']).write_text('Second authorized scope')
    second = authorize(tasks, item, tmp_path, request_id='second-message')
    assert finish(tasks, second, {'required': False, 'reason': 'Second review', 'evidence': ['diff']})['checklist_complete']
    Path(spec['inputs']['context']).write_text('Third scope requires a new request')
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='superseded'):
        authorize(Tasks(REPO, PR), item, tmp_path)
    assert store.manifest.read_bytes() == before


def test_new_authorization_recovers_changed_inputs_after_incomplete_replacements(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = planned(tasks, deepcopy(spec), required=False)
    original = read(store.manifest)['entries'][refs['assessment']]['result']
    original_path = store.root/'results'/original['file']
    original_bytes = original_path.read_bytes()
    item = descriptor(deepcopy(spec), 'assessment')
    for number in range(2):
        task = authorize(tasks, item, tmp_path, request_id='request-' + str(number))
        assert finish(tasks, task, {'required': False, 'reason': 'Missing observations', 'evidence': ['diff']},
                      native=False)['status'] == 'incomplete'
        Path(spec['inputs']['context']).write_text('Changed scope ' + str(number))
        with pytest.raises(ValueError, match='inputs changed'):
            tasks.prepare({'tasks': [{'key': task['key']}]})
    recovered = authorize(Tasks(REPO, PR), item, tmp_path, request_id='renewed-request')
    assert recovered['action'] == 'invoke'
    assert finish(tasks, recovered, {'required': False, 'reason': 'Recovered', 'evidence': ['diff']})['checklist_complete']
    assert fix.gate(Tasks(REPO, PR))['outcome'] == 'skipped'
    assert original_path.read_bytes() == original_bytes
    assert read(store.manifest)['entries'][refs['assessment']]['attempts'][0]['result'] == original['file']


@pytest.mark.parametrize('boundary', ['saved_key', 'low_level'])
def test_incomplete_reassessment_retry_retains_run_and_budget_at_execution_boundary(case, tmp_path, boundary):
    spec, store = case; tasks = Tasks(REPO, PR)
    item = descriptor(deepcopy(spec), 'assessment')
    initial = tasks.prepare({'run': 'retained', 'cycle': 'initial', 'tasks': [item]})['tasks'][0]
    assert finish(tasks, initial, {'required': False, 'reason': 'Initial', 'evidence': ['diff']})['checklist_complete']
    task = authorize(tasks, item, tmp_path, run='retained', cycle='replacement')
    assert finish(tasks, task, {'required': False, 'reason': 'Missing observations', 'evidence': ['diff']},
                  native=False)['status'] == 'incomplete'

    def start(**options):
        if boundary == 'saved_key':
            return Tasks(REPO, PR).prepare({'tasks': [{'key': task['key']}], **options})['tasks'][0]
        return store.start(tasks.saved_spec(task['key']), **options)

    before = store.manifest.read_bytes()
    for options, error in (
            ({}, 'original run'),
            ({'run': 'different', 'cycle': 'replacement'}, 'original run'),
            ({'run': 'retained'}, 'budgeted review cycle'),
            ({'run': 'retained', 'cycle': 'replacement', 'ci_repair': True}, 'review-cycle allowance'),
            ({'run': 'retained', 'cycle': 'replacement', 'ci_rerun': True}, 'review-cycle allowance'),
            ({'run': 'retained', 'cycle': 'replacement'}, 'explicit retry')):
        with pytest.raises(ValueError, match=error):
            start(**options)
        assert store.manifest.read_bytes() == before
    unbound_spec = deepcopy(task['assignment'])
    unbound_spec['options'].pop('alternatives_reassessment')
    with pytest.raises(ValueError, match='original authorization binding'):
        tasks.prepare({'tasks': [deepcopy(item)]})
    with pytest.raises(ValueError, match='original authorization binding'):
        store.start(unbound_spec)
    resumed = start(run='retained', cycle='replacement', retry=True)
    assert resumed['action'] == 'invoke' and resumed['claim']['run'] == 'retained'
    assert read(store.manifest)['runs']['retained']['retries']['replacement:' + task['key']] == 1


def test_completed_reassessment_reuses_without_reserving_original_run(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    item = descriptor(deepcopy(spec), 'assessment')
    initial = tasks.prepare({'run': 'retained', 'cycle': 'initial', 'tasks': [item]})['tasks'][0]
    assert finish(tasks, initial, {'required': False, 'reason': 'Initial', 'evidence': ['diff']})['checklist_complete']
    task = authorize(tasks, item, tmp_path, run='retained', cycle='replacement')
    assert finish(tasks, task, {'required': False, 'reason': 'Reassessed', 'evidence': ['diff']})['checklist_complete']
    before = store.manifest.read_bytes()
    for options in ({}, {'run': 'fresh-session', 'cycle': 'fresh-cycle'}):
        reused = Tasks(REPO, PR).prepare({'tasks': [{'key': task['key']}], **options})['tasks'][0]
        assert reused['action'] == 'reuse'
        assert store.start(tasks.saved_spec(task['key']), **options)['action'] == 'reuse'
    assert store.manifest.read_bytes() == before


def test_removed_old_installation_returns_recovery_and_cli_accepts_rebuilt_definition(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    planned(tasks, deepcopy(spec), required=False)
    old = Path(spec['skill']['resources']['entry']); old.unlink()
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO, '--pr', PR, 'fix-gate']
    result = json.loads(subprocess.check_output(command, text=True, timeout=60))
    assert result['outcome'] == 'reassessment_required' and not result['release_reviewers']
    assert result['stage'] == 'assessment' and 'reassess' in result['recovery']
    replacement = tmp_path/'installed-reviewer.md'; replacement.write_text('Updated installed reviewer')
    item = descriptor(deepcopy(spec), 'assessment')
    item['definition']['resources']['entry'] = str(replacement)
    request = tmp_path/'prepare.json'; write(request, {'tasks': [item]})
    original = tmp_path/'message.json'; write(original, {'id': 'user-message', 'source': 'conversation/12', 'instruction': INSTRUCTION})
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR,
               'reassess', '--file', str(request), '--request-id', 'user-message',
               '--authorization', INSTRUCTION, '--source', str(original)]
    task = json.loads(subprocess.check_output(command, text=True, timeout=60))['tasks'][0]
    assert task['action'] == 'invoke'
    assert finish(tasks, task, {'required': False, 'reason': 'Rebuilt sources reviewed', 'evidence': ['diff']})['checklist_complete']
    assert fix.gate(Tasks(REPO, PR))['outcome'] == 'skipped'


@pytest.mark.parametrize('field', ['id', 'instruction', 'source'])
def test_inexact_or_incomplete_original_request_rejects_before_writes(case, tmp_path, field):
    spec, store = case; tasks = Tasks(REPO, PR)
    planned(tasks, deepcopy(spec), required=False)
    original = {'id': 'message-215', 'source': 'conversation/message-215', 'instruction': INSTRUCTION}
    original[field] = '' if field == 'source' else 'Other user request'
    write(tmp_path/'message-215.json', original)
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='exact original'):
        authorize(tasks, descriptor(deepcopy(spec), 'assessment'), tmp_path)
    assert store.manifest.read_bytes() == before


def test_reassessment_does_not_reset_run_retry_ci_or_publication_recovery(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    item = descriptor(deepcopy(spec), 'assessment')
    original = tasks.prepare({'run': 'retained', 'cycle': 'initial', 'tasks': [item]})['tasks'][0]
    assert finish(tasks, original, {'required': False, 'reason': 'Simple', 'evidence': ['diff']})['checklist_complete']
    manifest = read(store.manifest)
    run = manifest['runs']['retained']
    run['limits']['review_cycles'] = 1
    run['retries']['initial:task'] = 1
    run['ci_repairs']['initial'] = 3
    run['ci_reruns'][spec['git']['head']] = 2
    run['progress'].append({'publication': 'pending-original-intent'})
    write(store.manifest, manifest)
    before = store.manifest.read_bytes()
    for run_id in (None, 'replacement-run'):
        with pytest.raises(ValueError, match='original run'):
            authorize(tasks, item, tmp_path, **({'run': run_id} if run_id else {}))
    with pytest.raises(ValueError, match='budgeted review cycle'):
        authorize(tasks, item, tmp_path, run='retained')
    for flag in ('ci_repair', 'ci_rerun'):
        with pytest.raises(ValueError, match='review-cycle allowance'):
            authorize(tasks, item, tmp_path, run='retained', cycle='initial', **{flag: True})
    with pytest.raises(ValueError, match='cycle limit reached'):
        authorize(tasks, item, tmp_path, run='retained', cycle='second')
    with pytest.raises(ValueError, match='new cycle'):
        authorize(tasks, item, tmp_path, run='retained', cycle='initial')
    assert store.manifest.read_bytes() == before
    assert read(store.manifest)['runs']['retained'] == run


def test_authorization_does_not_silently_recover_original_active_claim(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    planned(tasks, deepcopy(spec), required=False)
    original = authorize(tasks, descriptor(deepcopy(spec), 'assessment'), tmp_path)
    before = store.manifest.read_bytes()
    blocked = authorize(tasks, descriptor(deepcopy(spec), 'assessment'), tmp_path, request_id='another-request')
    assert blocked['action'] == 'recover_required'
    assert read(store.manifest)['entries'][original['key']]['claim'] == original['claim']
    assert store.manifest.read_bytes() == before


def test_policy_only_selection_refresh_keeps_manual_choice_source_and_completed_upstreams(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = {'assessment': complete(tasks, deepcopy(spec), 'assessment',
                                  {'required': True, 'reason': 'State design', 'evidence': ['diff']})}
    refs['comparison'] = complete(tasks, deepcopy(spec), 'comparison', compared(), refs)
    choice = tmp_path/'choice.txt'; choice.write_text('Choose implemented for compatibility with existing clients.')
    item = descriptor(deepcopy(spec), 'selection', {'comparison': refs['comparison']})
    item['definition']['inputs']['user_choice'] = str(choice)
    selected = tasks.prepare({'tasks': [deepcopy(item)]})['tasks'][0]
    manual = selection(mode='manual', user_source=str(choice), evidence=[str(choice)])
    assert finish(tasks, selected, manual)['checklist_complete']
    refs['selection'] = selected['key']
    original = read(store.manifest)
    original_report = Path(tasks.result(selected['key'], 'normalized')['result'])
    original_bytes = original_report.read_bytes()
    original_source = choice.read_bytes()
    item['options'] = {'alternatives': {'auto_pick': {'min_score_gap': 20}}}
    with pytest.raises(ValueError, match='reassessment'):
        tasks.prepare({'tasks': [deepcopy(item)]})
    current = authorize(tasks, item, tmp_path)
    assert finish(tasks, current, manual)['checklist_complete']
    now = read(store.manifest)
    for stage in ('assessment', 'comparison'):
        assert now['entries'][refs[stage]] == original['entries'][refs[stage]]
    assert original_report.read_bytes() == original_bytes and choice.read_bytes() == original_source
    assert read(original_report)['normalized']['user_source'] == str(choice)
    assert tasks.result(selected['key'], 'normalized')['normalized']['user_source'] == str(choice)


def test_reassessment_checklist_resumes_without_repeating_primary_analysis(case, tmp_path):
    from checklist_reports import report as checklist_report
    from native_review_observations import observations
    spec, store = case; tasks = Tasks(REPO, PR)
    planned(tasks, deepcopy(spec), required=False)
    task = authorize(tasks, descriptor(deepcopy(spec), 'assessment'), tmp_path)
    report = {'complete': True, 'required': False, 'reason': 'Reassessed simple scope', 'evidence': ['diff'], 'findings': []}
    output = json.dumps(report).encode()
    result = tasks.finish(task['key'], task['claim']['id'], output, report,
                          native_evidence=observations(task['assignment'], task['claim'], output))
    assert result['action'] == 'recheck'
    assert not fix.gate(Tasks(REPO, PR))['release_reviewers']
    resumed = Tasks(REPO, PR).prepare({'tasks': [{'key': task['key']}]})['tasks'][0]
    assert resumed['action'] == 'recheck' and resumed['round'] == result['round']
    checked = checklist_report(resumed['checklist'], resumed['result'])
    assert tasks.finish(task['key'], resumed['claim'], json.dumps(checked).encode(), checked,
                        round_id=resumed['round'])['checklist_complete']
    assert fix.gate(Tasks(REPO, PR))['outcome'] == 'skipped'
    assert len(read(store.manifest)['entries'][task['key']]['attempts']) == 2


def test_superseded_authorization_cannot_replace_newer_incomplete_reassessment(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    planned(tasks, deepcopy(spec), required=False)
    item = descriptor(deepcopy(spec), 'assessment')
    first = authorize(tasks, item, tmp_path)
    assert finish(tasks, first, {'required': False, 'reason': 'First review', 'evidence': ['diff']})['checklist_complete']
    old_descriptor = deepcopy(read(store.manifest)['entries'][first['key']]['managed'])
    second = authorize(tasks, item, tmp_path, request_id='later-user-message')
    assert finish(tasks, second, {'required': False, 'reason': 'No native evidence', 'evidence': ['diff']},
                  native=False)['status'] == 'incomplete'
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='superseded'):
        tasks.prepare({'tasks': [old_descriptor]})
    assert store.manifest.read_bytes() == before
