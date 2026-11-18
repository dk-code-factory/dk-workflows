"""Inspection derives live selection and never changes retained recovery state."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from checklist_reports import finish, report as checklist_report
from next_actions import inspect
from shared import publication
from shared.workflow_storage import operations_root
from state import Store, capture, read, write
from tasks import Tasks
from test_batch_state import cli, definition
from test_state import case, REPO, PR


OWNER = 'dk-workflows:dk-pr-fix'


def setup(spec, calls=None, finalizers=None):
    calls = calls if calls is not None else [{'instructions': 'Inspect one.'}]
    options = {'tasks': calls, 'finalizers': finalizers or [], 'alternatives': {'enabled': False}}
    fields = definition(deepcopy(spec))
    fields['skill_identity'] = OWNER
    fields['responsibility'] = 'bounded-inspection'
    request = {'skill': OWNER, 'run': 'run', 'options': options,
               'git': {key: spec['git'][key] for key in ('head', 'base', 'target')},
               'definitions': {kind + '/' + str(i): deepcopy(fields)
                               for kind, values in [('tasks', calls), ('finalizers', finalizers or [])]
                               for i in range(sum(len(v['parallel']) if isinstance(v, dict) and 'parallel' in v else 1
                                                  for v in values))}}
    return request


def descriptor(request, location):
    kind, index = location.split('/')
    return {'kind': 'skill', 'skill': request['skill'], 'selection': kind, 'index': int(index),
            'options': request['options'], 'definition': deepcopy(request['definitions'][location])}


def prepare(tasks, request, location, **execution):
    return tasks.prepare({'run': request['run'], 'tasks': [descriptor(request, location)], **execution})['tasks'][0]


def complete(tasks, prepared, **report):
    return finish(tasks, prepared['key'], prepared['claim']['id'], b'Original bounded report',
                  {'complete': True, **report})


def rows(result):
    return {item['location']: item for item in result['items'] if 'location' in item}


def snapshot(root):
    return {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_initial_inspection_creates_no_state_even_for_a_new_subject(case):
    spec, store = case
    other = 'https://github.com/acme/project/pull/8'
    root = store.root.parent
    before = snapshot(root)
    result = inspect(REPO, other, setup(spec), checkout=spec['git']['checkout'])
    assert rows(result)['tasks/0']['action'] == 'invoke_native'
    assert snapshot(root) == before
    assert not Path(result['manifest']).parent.exists()


def test_fresh_process_reuses_original_without_a_saved_inspection_plan(case, tmp_path):
    spec, store = case
    request = setup(spec)
    tasks = Tasks(REPO, PR)
    prepared = prepare(tasks, request, 'tasks/0')
    complete(tasks, prepared, findings=[{'body': 'Original substantive finding'}])
    request.pop('definitions')
    path = tmp_path/'inspection.json'
    write(path, request)
    before = snapshot(store.root)
    for _ in range(2):
        result = json.loads(subprocess.check_output(cli('next', '--file', str(path)), text=True, timeout=60))
        row = rows(result)['tasks/0']
        assert row['action'] == 'consume_result' and row['key'] == prepared['key']
        assert row['result'] == tasks.result(prepared['key'], 'normalized')['result']
        assert result['selected_complete'] and result['readiness_established'] is False
        assert 'Original substantive finding' not in json.dumps(result)
    assert snapshot(store.root) == before


def test_parallel_partial_failure_keeps_completed_sibling_and_blocks_later_groups(case):
    spec, store = case
    request = setup(spec, [{'parallel': [{'instructions': 'A'}, {'instructions': 'B'}]}, {'instructions': 'C'}])
    tasks = Tasks(REPO, PR)
    a, b = [prepare(tasks, request, 'tasks/' + str(i)) for i in range(2)]
    complete(tasks, a)
    tasks.finish(b['key'], b['claim']['id'], b'Failed work', {'complete': False}, status='failed')
    request.pop('definitions')
    before = snapshot(store.root)
    result = rows(inspect(REPO, PR, request))
    assert result['tasks/0']['action'] == 'consume_result'
    assert result['tasks/1']['action'] == 'invoke_native'
    assert result['tasks/2']['action'] == 'blocked_prerequisite'
    assert snapshot(store.root) == before


def test_changed_input_refreshes_only_affected_parallel_work_and_finish_rechecks(case, tmp_path):
    spec, store = case
    request = setup(spec, [{'parallel': [{'instructions': 'A'}, {'instructions': 'B'}]}])
    independent = tmp_path/'independent'
    independent.write_text('Independent source')
    request['definitions']['tasks/1']['inputs'] = {'scope': str(independent)}
    tasks = Tasks(REPO, PR)
    a, b = [prepare(tasks, request, 'tasks/' + str(i)) for i in range(2)]
    for task in (a, b):
        complete(tasks, task)
    Path(spec['inputs']['context']).write_text('Changed governing input')
    result = rows(inspect(REPO, PR, request))
    assert result['tasks/0']['action'] == 'invoke_native'
    assert result['tasks/1']['action'] == 'consume_result'
    new = prepare(tasks, request, 'tasks/0')
    assert new['action'] == 'invoke'
    Path(spec['inputs']['context']).write_text('Changed after inspection and claim')
    with pytest.raises(ValueError, match='changed'):
        complete(tasks, new)
    assert store.status([])['unfinished'][0]['id'] == new['claim']['id']


@pytest.mark.parametrize('damage', ['missing', 'damaged'])
def test_lost_primary_evidence_requests_restoration_not_repeat_analysis(case, damage):
    spec, store = case
    request = setup(spec)
    tasks = Tasks(REPO, PR)
    item = prepare(tasks, request, 'tasks/0')
    complete(tasks, item)
    path = Path(tasks.result(item['key'], 'normalized')['result'])
    path.unlink() if damage == 'missing' else path.write_text('{}')
    before = snapshot(store.root)
    row = rows(inspect(REPO, PR, request))['tasks/0']
    assert row['action'] == 'restore_evidence'
    assert row['result_reference']['pointer'].endswith('/result')
    assert snapshot(store.root) == before


def test_pending_checklist_keeps_original_round_and_claim_recovery_spans_runs(case):
    spec, store = case
    request = setup(spec, [{'parallel': [{'instructions': 'A'}, {'instructions': 'B'}]}])
    tasks = Tasks(REPO, PR)
    a, b = [prepare(tasks, request, 'tasks/' + str(i)) for i in range(2)]
    checked = tasks.finish(a['key'], a['claim']['id'], b'Complete primary', {'complete': True})
    assert checked['action'] == 'recheck'
    request['run'] = 'new-session'
    before = snapshot(store.root)
    result = inspect(REPO, PR, request)
    assert rows(result)['tasks/0']['action'] == 'check_completion'
    assert rows(result)['tasks/1']['action'] == 'reconcile_claim'
    assert any(i.get('key') == b['key'] and 'location' not in i for i in result['items'])
    assert read(store.manifest)['entries'][a['key']]['checklist']['rounds'][-1]['id'] == checked['round']
    assert snapshot(store.root) == before


def test_exhausted_review_allowance_does_not_prevent_unchanged_consumption(case):
    spec, store = case
    request = setup(spec)
    request['options']['limits'] = {'review_cycles': 1}
    tasks = Tasks(REPO, PR)
    item = prepare(tasks, request, 'tasks/0', cycle='one')
    complete(tasks, item)
    request['execution'] = {'tasks/0': {'cycle': 'two'}}
    assert rows(inspect(REPO, PR, request))['tasks/0']['action'] == 'consume_result'
    Path(spec['inputs']['context']).write_text('Changed source')
    before = snapshot(store.root)
    result = inspect(REPO, PR, request)
    assert rows(result)['tasks/0']['action'] == 'exhausted_allowance'
    assert result['review_budget'] == {'used': 1, 'limit': 1, 'remaining': 0}
    assert snapshot(store.root) == before


def test_optional_failure_can_finish_ordering_but_not_an_explicit_result_dependency(case):
    spec, _ = case
    request = setup(spec, [{'instructions': 'Optional', 'required': False}, {'instructions': 'Next'}])
    tasks = Tasks(REPO, PR)
    item = prepare(tasks, request, 'tasks/0')
    tasks.finish(item['key'], item['claim']['id'], b'Unavailable optional work', {'complete': False}, status='failed')
    result = rows(inspect(REPO, PR, request))
    assert result['tasks/0']['action'] == 'optional_incomplete'
    assert result['tasks/1']['action'] == 'invoke_native'
    request['dependencies'] = {'tasks/1': ['tasks/0']}
    assert rows(inspect(REPO, PR, request))['tasks/1']['action'] == 'blocked_prerequisite'


def test_explicit_parallel_dependency_and_applicability_require_current_original_evidence(case):
    spec, store = case
    request = setup(spec, [{'parallel': [{'instructions': 'Decide scope'}, {'instructions': 'Conditional work'}]}])
    tasks = Tasks(REPO, PR)
    request['dependencies'] = {'tasks/1': ['tasks/0']}
    assert rows(inspect(REPO, PR, request))['tasks/1']['action'] == 'blocked_prerequisite'
    item = prepare(tasks, request, 'tasks/0')
    complete(tasks, item, applicability={'conditional': {'applicable': False,
             'invocation': {'instructions': 'Conditional work'}, 'reason': 'Original evidence excludes this scope.',
             'evidence': [spec['inputs']['context']]}})
    request['applicability'] = {'tasks/1': {'key': item['key'], 'pointer': '/applicability/conditional'}}
    assert rows(inspect(REPO, PR, request))['tasks/1']['action'] == 'not_applicable'
    Path(spec['inputs']['context']).write_text('Scope changed')
    before = snapshot(store.root)
    assert rows(inspect(REPO, PR, request))['tasks/1']['action'] == 'blocked_prerequisite'
    assert snapshot(store.root) == before


def test_live_replacement_and_reordering_do_not_infer_coverage_from_saved_entries(case):
    spec, store = case
    request = setup(spec, [{'instructions': 'A'}, {'instructions': 'B'}])
    tasks = Tasks(REPO, PR)
    for location in ('tasks/0', 'tasks/1'):
        complete(tasks, prepare(tasks, request, location))
    request.pop('definitions')
    assert inspect(REPO, PR, request)['selected_complete']
    before = snapshot(store.root)
    request['options']['tasks'].reverse()
    result = rows(inspect(REPO, PR, request))
    assert result['tasks/0']['action'] == 'invoke_native'
    assert result['tasks/1']['action'] == 'blocked_prerequisite'
    request['options']['tasks'][0] = {'skill': 'dk-workflows:dk-pr-description'}
    assert rows(inspect(REPO, PR, request))['tasks/0']['action'] == 'definition_required'
    assert snapshot(store.root) == before


def test_empty_and_custom_finalizers_are_authoritative_and_outputs_are_paged(case):
    spec, store = case
    request = setup(spec, [], [{'instructions': 'Only selected finalizer'}])
    assert set(rows(inspect(REPO, PR, request))) == {'finalizers/0'}
    tasks = Tasks(REPO, PR)
    complete(tasks, prepare(tasks, request, 'finalizers/0'))
    request['options']['finalizers'] = []
    request.pop('definitions')
    assert inspect(REPO, PR, request)['selected_complete']
    assert inspect(REPO, PR, request)['items'] == []
    request['options'].pop('finalizers')
    result = inspect(REPO, PR, request, limit=2)
    assert result['total'] == 7 and len(result['items']) == 2 and result['next_after'] == 2
    page = inspect(REPO, PR, request, after=2, limit=2)
    assert page['items'][0]['location'] == 'finalizers/2'
    assert result['items'][0]['action'] == 'definition_required'
    assert all(i['action'] == 'blocked_prerequisite' for i in result['items'][1:] + page['items'])


@pytest.mark.parametrize('status,action', [('prepared', 'inspect_publication'), ('pending', 'reconcile_publication')])
def test_uncertain_and_prepared_publications_keep_original_intent_across_runs(case, status, action):
    spec, store = case
    request = setup(spec)
    directory = operations_root() / publication.digest(PR) / 'original-run'
    original = publication.prepare('edit-pr', PR, {'head': spec['git']['head']},
                                   {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'],
                                    'fields': {'body': 'Reviewed body'}}, ['original-publisher', 'reconcile'],
                                   directory=directory)
    path = Path(original['operation'])
    value = read(path)
    if status == 'pending':
        value.update(status='pending', intent={'operation_id': value['operation_id'], 'request_hash': value['request_hash']})
        write(path, value)
    request['run'] = 'different-run'
    before = snapshot(store.root.parent.parent) | snapshot(operations_root())
    result = inspect(REPO, PR, request)
    assert result['items'][0]['action'] == action and result['items'][0]['operation'] == str(path)
    assert rows(result)['tasks/0']['reason'] == 'publication_recovery_required'
    assert snapshot(store.root.parent.parent) | snapshot(operations_root()) == before


def test_prerequisite_cycles_and_unknown_current_locations_reject_without_mutation(case):
    spec, store = case
    request = setup(spec, [{'instructions': 'A'}, {'instructions': 'B'}])
    request['dependencies'] = {'tasks/0': ['tasks/1']}
    before = snapshot(store.root)
    with pytest.raises(ValueError, match='Cyclic'):
        inspect(REPO, PR, request)
    request['dependencies'] = {'removed/0': []}
    with pytest.raises(ValueError, match='outside'):
        inspect(REPO, PR, request)
    assert snapshot(store.root) == before


def test_checklist_only_changes_do_not_repeat_primary_analysis(case):
    spec, store = case
    request = setup(spec)
    tasks = Tasks(REPO, PR)
    item = prepare(tasks, request, 'tasks/0')
    complete(tasks, item)
    original = read(store.manifest)['entries'][item['key']]['result']
    request['definitions']['tasks/0']['checklist'] = {
        'items': [{'id': 'extra', 'text': 'Finish the extra deliverable.'}]}
    before = snapshot(store.root)
    assert rows(inspect(REPO, PR, request))['tasks/0']['action'] == 'check_completion'
    assert read(store.manifest)['entries'][item['key']]['result'] == original
    assert snapshot(store.root) == before


def test_completion_allowance_and_damaged_check_evidence_have_distinct_recovery(case):
    spec, store = case
    request = setup(spec)
    request['definitions']['tasks/0']['checklist'] = {'max_checks': 1}
    tasks = Tasks(REPO, PR)
    item = prepare(tasks, request, 'tasks/0')
    check = tasks.finish(item['key'], item['claim']['id'], b'Primary done', {'complete': True})
    tasks.finish(item['key'], check['claim'], b'Unfinished deliverable',
                 checklist_report(check['checklist'], unfinished=('deliverables',)), round_id=check['round'])
    assert rows(inspect(REPO, PR, request))['tasks/0']['action'] == 'exhausted_allowance'
    entry = read(store.manifest)['entries'][item['key']]
    (store.root/'results'/entry['checklist']['rounds'][0]['response']['file']).unlink()
    assert rows(inspect(REPO, PR, request))['tasks/0']['action'] == 'restore_evidence'


def test_current_pins_and_missing_resources_cannot_inherit_old_coverage(case):
    spec, store = case
    request = setup(spec)
    tasks = Tasks(REPO, PR)
    item = prepare(tasks, request, 'tasks/0')
    complete(tasks, item)
    before = snapshot(store.root)
    pins = request.pop('git')
    row = rows(inspect(REPO, PR, request))['tasks/0']
    assert row['action'] == 'definition_required' and 'current Git pins' in row['detail']
    request['git'] = pins
    Path(next(iter(spec['skill']['resources'].values()))).unlink()
    assert rows(inspect(REPO, PR, request))['tasks/0']['action'] == 'definition_required'
    assert snapshot(store.root) == before


def test_read_only_store_rejects_mutation_and_live_alternatives_reads(case):
    import alternatives
    spec, store = case
    readonly = Tasks(REPO, PR, read_only=True)
    before = snapshot(store.root)
    with pytest.raises(ValueError, match='Read-only'):
        readonly.store.start(spec)
    with pytest.raises(ValueError, match='Read-only'):
        readonly.store.start_batch([spec])
    with alternatives.evaluation(readonly):
        with pytest.raises(ValueError, match='Read-only'):
            alternatives.live_pr(PR)
    assert snapshot(store.root) == before


def test_large_configured_prompts_are_not_copied_into_routine_status(case):
    spec, _ = case
    request = setup(spec, [{'instructions': 'Repeated-private-context ' * 10000}])
    result = inspect(REPO, PR, request)
    assert len(json.dumps(result)) < 3000
    assert 'Repeated-private-context' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['dependency', 'applicability'])
@pytest.mark.parametrize('state', ['invalidated', 'claimed', 'incomplete'])
def test_original_key_cannot_borrow_another_reordered_reviewers_result(case, kind, state):
    import config
    spec, store = case
    request = setup(spec, [{'instructions': 'Conditional work'}])
    owner = 'dk-workflows:dk-pr-reviewers-run'
    calls = [{'skill': 'team:check', 'instructions': text} for text in ('A', 'B')]
    def configure(values):
        write(config.path(REPO), {'skill_defaults': {owner: {'options': {'reviewers': values}}}})
    configure(calls)
    fields = definition(deepcopy(spec))
    fields.update(skill_identity='team:check', responsibility='bounded-inspection',
                  native_review={'schema': 2, 'required': False})
    descriptors = [{'kind': 'skill', 'skill': owner, 'selection': 'reviewers', 'index': i,
                    'definition': deepcopy(fields)} for i in range(2)]
    tasks = Tasks(REPO, PR)
    a, b = tasks.prepare({'run': 'original', 'cycle': 'one', 'tasks': descriptors})['tasks']
    decision = {'applicability': {'conditional': {'applicable': False,
                'invocation': {'instructions': 'Conditional work'},
                'reason': 'Original scope exclusion.', 'evidence': [spec['inputs']['context']]}}}
    complete(tasks, a, **decision)
    complete(tasks, b)
    store.invalidate([a['key']], 'Original evidence requires refresh')
    if state != 'invalidated':
        pending = tasks.prepare({'run': 'original', 'cycle': 'two', 'tasks': descriptors[:1]})['tasks'][0]
        if state == 'incomplete':
            tasks.finish(pending['key'], pending['claim']['id'], b'Unfinished original decision',
                         {'complete': False, **decision}, status='incomplete')
    configure(list(reversed(calls)))
    rebuilt = tasks.saved_spec(a['key'])
    assert capture(rebuilt)['key'] == b['key']
    assert store.status([rebuilt])['tasks'][0]['reusable']
    if kind == 'dependency':
        request['dependencies'] = {'tasks/0': ['key:' + a['key']]}
    else:
        request['applicability'] = {'tasks/0': {'key': a['key'], 'pointer': '/applicability/conditional'}}
    before = snapshot(store.root)
    assert rows(inspect(REPO, PR, request))['tasks/0']['action'] == 'blocked_prerequisite'
    assert snapshot(store.root) == before


@pytest.mark.parametrize('completion', ['failed', 'incomplete'])
def test_invalidated_optional_failure_requires_refresh(case, completion):
    spec, store = case
    request = setup(spec, [{'instructions': 'Optional', 'required': False}])
    tasks = Tasks(REPO, PR)
    task = prepare(tasks, request, 'tasks/0')
    tasks.finish(task['key'], task['claim']['id'], b'Unavailable optional work',
                 {'complete': False}, status=completion)
    assert inspect(REPO, PR, request)['selected_complete']
    store.invalidate([task['key']], 'Refresh this affected optional work')
    before = snapshot(store.root)
    result = inspect(REPO, PR, request)
    assert rows(result)['tasks/0']['action'] == 'invoke_native'
    assert not result['selected_complete']
    assert snapshot(store.root) == before


@pytest.mark.parametrize('flag', ['ci_repair', 'ci_rerun', 'retry'])
def test_invalid_cycle_selector_is_not_an_exhausted_allowance(case, flag):
    spec, store = case
    request = setup(spec)
    request['options']['limits'] = {'review_cycles': 1}
    tasks = Tasks(REPO, PR)
    complete(tasks, prepare(tasks, request, 'tasks/0', cycle='one'))
    Path(spec['inputs']['context']).write_text('Changed scope needs current work')
    request['execution'] = {'tasks/0': {'cycle': 'nonexistent', flag: True}}
    before = snapshot(store.root)
    row = rows(inspect(REPO, PR, request))['tasks/0']
    assert row['action'] == 'blocked_prerequisite' and row['reason'] == 'budget_rejected'
    assert 'cycle' in row['detail'].lower()
    assert snapshot(store.root) == before
