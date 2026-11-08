"""Completion-only checks retain analysis, evidence and bounded recovery state."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from checklist_reports import accept, report
from state import capture, read, write
from tasks import Tasks
from test_batch_state import cli, definition, sibling
from test_state import case, done, REPO, PR


def setup_task(spec, *, checklist=None, **fields):
    value = definition(spec)
    value.update(fields)
    if checklist is not None:
        value['checklist'] = checklist
    return {'tasks': [{'definition': value}]}


def initial(tasks, body):
    task = tasks.prepare(body)['tasks'][0]
    assert task['action'] == 'invoke'
    result = tasks.finish(task['key'], task['claim']['id'], b'Original task output',
                          {'complete': True, 'findings': [{'id': 'original-finding'}]})
    return task, result


def submit(tasks, check, unfinished=()):
    value = report(check['checklist'], check['result'], unfinished)
    return tasks.finish(check['key'], check['claim'], json.dumps(value).encode(), value,
                        round_id=check['round'])


def test_first_followup_is_mandatory_even_when_initial_report_claims_all_items(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    prepared = tasks.prepare(setup_task(spec))['tasks'][0]
    assert capture(prepared['assignment']) == capture(spec)
    assert prepared['checklist']['max_checks'] == 3 and prepared['checklist']['required']
    value = {'complete': True, **report(prepared['checklist'])}
    check = tasks.finish(prepared['key'], prepared['claim']['id'], b'All done', value)
    assert check['action'] == 'recheck' and check['check_number'] == 1
    status = tasks.resume()['tasks'][0]
    assert status['analysis_complete'] and not status['coverage_complete'] and not status['reusable']
    assert status['checklist']['checks_used'] == 1
    assert accept(tasks, check)['checklist_complete']
    assert tasks.prepare(setup_task(spec))['tasks'][0]['action'] == 'reuse'
    assert len(read(store.manifest)['entries'][prepared['key']]['attempts']) == 1


@pytest.mark.parametrize('maximum', [1, 2, 3, 5])
@pytest.mark.parametrize('required', [True, False])
def test_exact_additional_allowance_and_mandatory_vs_best_effort(case, maximum, required):
    spec, store = case
    tasks = Tasks(REPO, PR)
    body = setup_task(spec, checklist={'max_checks': maximum, 'required': required})
    prepared, check = initial(tasks, body)
    rounds = []
    for number in range(1, maximum + 1):
        rounds.append(check['round'])
        assert check['check_number'] == number
        check = submit(tasks, check, ('scope', 'evidence'))
        assert (check['action'] == 'recheck') == (number < maximum)
    assert check['action'] == ('failed' if required else 'complete')
    assert not check['checklist_complete']
    assert {item['id'] for item in check['omissions']} == {'scope', 'evidence'}
    resumed = tasks.resume()['tasks'][0]
    assert resumed['coverage_complete'] == (not required)
    assert resumed['checklist']['checks_used'] == maximum
    assert len(set(rounds)) == maximum
    repeated = tasks.prepare(body)['tasks'][0]
    assert repeated['action'] == ('failed' if required else 'reuse')
    if not required:
        assert not repeated['checklist_complete']
    assert len(read(store.manifest)['entries'][prepared['key']]['attempts']) == 1


def test_early_success_stops_without_spending_remaining_rounds(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    _, check = initial(tasks, setup_task(spec))
    second = submit(tasks, check, ('deliverables',))
    assert second['check_number'] == 2
    assert submit(tasks, second)['checklist_complete']
    assert tasks.resume()['tasks'][0]['checklist']['checks_used'] == 2


def test_duplicate_initial_and_round_submissions_are_idempotent(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    prepared, first = initial(tasks, setup_task(spec))
    before = store.manifest.read_bytes()
    repeated = tasks.finish(prepared['key'], prepared['claim']['id'], b'Original task output',
                            {'complete': True, 'findings': [{'id': 'original-finding'}]})
    assert repeated == first and store.manifest.read_bytes() == before
    value = report(first['checklist'], first['result'], ('scope',))
    second = tasks.finish(first['key'], first['claim'], b'Round one', value, round_id=first['round'])
    before = store.manifest.read_bytes()
    assert tasks.finish(first['key'], first['claim'], b'Round one', value, round_id=first['round']) == second
    assert store.manifest.read_bytes() == before
    with pytest.raises(ValueError, match='different submission'):
        tasks.finish(first['key'], first['claim'], b'Different output', value, round_id=first['round'])
    terminal = accept(tasks, second)
    value = report(second['checklist'], second['result'])
    # Replaying a previous submission after later rounds settled returns its original next round.
    assert tasks.finish(first['key'], first['claim'], b'Round one',
                        report(first['checklist'], first['result'], ('scope',)), round_id=first['round']) == second
    assert terminal['status'] == 'complete'


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown', 'no-evidence', 'no-reason', 'status', 'completion'])
def test_malformed_responses_do_not_consume_or_replace_rounds(case, mutation):
    spec, store = case
    tasks = Tasks(REPO, PR)
    _, check = initial(tasks, setup_task(spec))
    value = report(check['checklist'])
    if mutation == 'missing': value['checklist'].pop()
    if mutation == 'duplicate': value['checklist'][1] = value['checklist'][0]
    if mutation == 'unknown': value['checklist'][0]['id'] = 'unassigned'
    if mutation == 'no-evidence': value['checklist'][0]['evidence'] = []
    if mutation == 'no-reason': value['checklist'][0] = {'id': 'scope', 'status': 'unfinished'}
    if mutation == 'status': value['checklist'][0]['status'] = 'passed'
    if mutation == 'completion': value['complete'] = False
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError):
        tasks.finish(check['key'], check['claim'], b'Invalid report', value, round_id=check['round'])
    assert store.manifest.read_bytes() == before
    assert tasks.prepare(setup_task(spec))['tasks'][0]['round'] == check['round']


@pytest.mark.parametrize('checklist', [{'max_checks': 0}, {'max_checks': True}, {'required': 'yes'},
    {'items': [{'id': 'scope', 'text': 'Replace a built-in'}]},
    {'items': [{'id': 'custom', 'text': ''}]}, {'items': 'invalid'}, {'unexpected': True}])
def test_invalid_contracts_reject_entire_batch(case, checklist):
    spec, store = case
    tasks = Tasks(REPO, PR)
    body = setup_task(spec, checklist=checklist)
    with pytest.raises(ValueError): tasks.prepare(body)
    assert not store.manifest.exists()


def test_null_contract_is_invalid(case):
    spec, store = case
    body = setup_task(spec)
    body['tasks'][0]['definition']['checklist'] = None
    with pytest.raises(ValueError): Tasks(REPO, PR).prepare(body)
    assert not store.manifest.exists()


def test_checklist_only_invalidation_and_legacy_adoption_preserve_original_analysis(case):
    spec, store = case
    done(store, spec, run='original', cycle='c1')
    original = read(store.manifest)
    key = capture(spec)['key']
    tasks = Tasks(REPO, PR)
    body = setup_task(spec, checklist={'items': [{'id': 'custom', 'text': 'Verify custom deliverable.'}]})
    first = tasks.prepare(body)['tasks'][0]
    assert first['action'] == 'recheck' and first['claim'] == store.saved(original['entries'][key])['claim']
    assert first['checklist']['items'][-1]['id'] == 'custom'
    accept(tasks, first)
    body['tasks'][0]['definition']['checklist']['items'][0]['text'] = 'Verify changed deliverable.'
    second = tasks.prepare(body)['tasks'][0]
    assert second['action'] == 'recheck' and second['round'] != first['round']
    accept(tasks, second)
    after = read(store.manifest)
    assert after['runs'] == original['runs']
    assert after['entries'][key]['result'] == original['entries'][key]['result']
    assert after['entries'][key]['attempts'] == original['entries'][key]['attempts']
    assert capture(tasks.saved_spec(key)) == capture(spec)


def test_fresh_process_resume_preserves_pending_round_counts_siblings_and_results(case, tmp_path):
    spec, store = case
    tasks = Tasks(REPO, PR)
    body = setup_task(spec)
    body['run'], body['cycle'] = 'run', 'c1'
    body['tasks'].append({'definition': definition(sibling(spec))})
    first, sibling_task = tasks.prepare(body)['tasks']
    check = tasks.finish(first['key'], first['claim']['id'], b'First original', {'complete': True})
    sibling_check = tasks.finish(sibling_task['key'], sibling_task['claim']['id'], b'Sibling original', {'complete': True})
    accept(tasks, sibling_check)
    pending = submit(tasks, check, ('scope',))
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    resumed = json.loads(subprocess.check_output(cli('resume'), text=True, timeout=60))
    assert {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before
    current = {item['key']: item for item in resumed['tasks']}
    assert current[first['key']]['checklist']['pending_round'] == pending['round']
    assert current[first['key']]['checklist']['checks_used'] == 2
    assert current[sibling_task['key']]['reusable']
    request = tmp_path/'request.json'
    write(request, {'tasks': [{'key': first['key']}, {'key': sibling_task['key']}]})
    prepared = json.loads(subprocess.check_output(cli('prepare', '--file', str(request)), text=True, timeout=60))['tasks']
    assert [item['action'] for item in prepared] == ['recheck', 'reuse']
    assert prepared[0]['round'] == pending['round']
    accept(Tasks(REPO, PR), prepared[0])
    assert read(store.manifest)['runs']['run']['cycles'] == ['c1']
    assert tasks.result(first['key'], 'original')['original'] == 'First original'
    assert all(item['reusable'] for item in Tasks(REPO, PR).resume()['tasks'])


def test_governing_input_invalidation_refreshes_only_affected_work(case, tmp_path):
    spec, store = case
    tasks = Tasks(REPO, PR)
    other = sibling(spec)
    source = tmp_path/'other.txt'
    source.write_text('Independent scope')
    other['inputs']['context'] = str(source)
    for task_spec in (spec, other):
        _, check = initial(tasks, setup_task(task_spec))
        accept(tasks, check)
    key = capture(spec)['key']
    previous = read(store.manifest)['entries'][key]['result']
    Path(spec['inputs']['context']).write_text('Changed primary inputs')
    status = {item['key']: item for item in tasks.resume()['tasks']}
    assert not status[key]['coverage_complete'] and status[capture(other)['key']]['reusable']
    current = tasks.prepare(setup_task(spec))['tasks'][0]
    assert current['action'] == 'invoke'
    tasks.finish(key, current['claim']['id'], b'Fresh output', {'complete': True})
    assert read(store.manifest)['entries'][key]['checklist']['rounds'] != []
    assert (store.root/'results'/previous['file']).exists()


@pytest.mark.parametrize('status', ['incomplete', 'failed'])
def test_best_effort_never_overrides_failed_or_incomplete_execution(case, status):
    spec, store = case
    tasks = Tasks(REPO, PR)
    prepared = tasks.prepare(setup_task(spec, required=False))['tasks'][0]
    assert not prepared['checklist']['required']
    result = tasks.finish(prepared['key'], prepared['claim']['id'], b'Execution did not finish',
                          {'complete': False}, status=status)
    assert result['status'] == status and result.get('action') != 'recheck'
    assert not tasks.resume()['tasks'][0]['coverage_complete']


def test_native_evidence_survives_checklist_changes_and_cannot_be_bypassed(case):
    from native_review_observations import CONTRACT, observations
    spec, store = case
    tasks = Tasks(REPO, PR)
    body = setup_task(spec, native_review=CONTRACT, checklist={'required': False})
    prepared = tasks.prepare(body)['tasks'][0]
    result = tasks.finish(prepared['key'], prepared['claim']['id'], b'No observations', {'complete': True})
    assert result['status'] == 'incomplete' and not tasks.resume()['tasks'][0]['coverage_complete']
    prepared = tasks.prepare(body)['tasks'][0]
    output = b'Actual native report'
    native_spec = {**spec, 'native_review': CONTRACT}
    evidence = observations(native_spec, prepared['claim'], output)
    check = tasks.finish(prepared['key'], prepared['claim']['id'], output, {'complete': True}, native_evidence=evidence)
    accept(tasks, check)
    original = read(store.manifest)['entries'][prepared['key']]['result']
    envelope = read(store.root/'results'/original['file'])
    body['tasks'][0]['definition']['checklist']['max_checks'] = 1
    refreshed = tasks.prepare(body)['tasks'][0]
    assert refreshed['action'] == 'recheck'
    accept(tasks, refreshed)
    assert read(store.root/'results'/original['file']) == envelope
    assert tasks.result(prepared['key'], 'original')['original'] == output.decode()


def test_completed_check_evidence_damage_blocks_acceptance_without_relaunch(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    _, check = initial(tasks, setup_task(spec))
    accept(tasks, check)
    saved = tasks.result(check['key'], 'checklist')['checklist']
    (store.root/'results'/saved['rounds'][-1]['response']['file']).unlink()
    assert not tasks.resume()['tasks'][0]['coverage_complete']
    with pytest.raises(ValueError, match='damaged'):
        tasks.prepare(setup_task(spec))


def test_role_defaults_and_publication_only_when_assigned_and_authorized(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    from native_review_observations import CONTRACT
    for role in ('content-review', 'finding-verification', 'reviewer-fixes', 'ci-repair', 'feedback-fixes'):
        body = setup_task(spec, responsibility=role, native_review=CONTRACT)
        value = tasks.prepare(body)['tasks'][0]
        assert value['checklist']['required']
        ids = {item['id'] for item in value['checklist']['items']}
        assert 'publication' not in ids
        if 'fix' in role or role == 'ci-repair': assert {'tests', 'dispositions'} <= ids
    for authorized in (False, True):
        body = setup_task(spec, responsibility='publication-' + str(authorized),
                          options={'publication': {'assigned': True, 'authorized': authorized}})
        value = tasks.prepare(body)['tasks'][0]
        assert ('publication' in {item['id'] for item in value['checklist']['items']}) == authorized


@pytest.mark.parametrize('plan', [[], ['team:links'], [['team:links', 'team:licenses']],
    [{'instructions': 'Check licenses.'}, {'instructions': 'Check licenses.'}]])
def test_fixer_completion_uses_live_selected_plan_without_restoring_disabled_actions(case, plan):
    import config
    spec, store = case
    write(config.path(REPO), {'skill_defaults': {'dk-workflows:dk-pr-fix': {'options': {'finalizers': plan}}}})
    fields = definition(spec)
    fields.pop('responsibility')
    fields['skill_identity'] = 'dk-workflows:dk-pr-fix'
    body = {'run': 'fix-run', 'tasks': [{'kind': 'completion', 'definition': fields}]}
    tasks = Tasks(REPO, PR)
    prepared, check = initial(tasks, body)
    assert prepared['assignment']['options']['completion_plan'] == plan
    assert prepared['checklist']['required']
    texts = [item['text'] for item in check['checklist']['items'] if item['id'].startswith('finalizer-')]
    references = [ref for group in plan for ref in (group if isinstance(group, list) else [group])]
    assert len(texts) == len(references)
    assert all(any(str(ref) in text for text in texts) for ref in references)
    assert len({item['id'] for item in check['checklist']['items']}) == len(check['checklist']['items'])
    assert not any('dk-ci' in text or 'dk-commit-split' in text for text in texts)
    assert not tasks.resume()['tasks'][0]['coverage_complete']
    accept(tasks, check)
    assert tasks.resume()['tasks'][0]['coverage_complete']
    assert not tasks.resume()['tasks'][0]['reusable']
    write(config.path(REPO), {'skill_defaults': {'dk-workflows:dk-pr-fix': {'options': {'finalizers': ['team:changed']}}}})
    assert not tasks.resume()['tasks'][0]['coverage_complete']
    assert tasks.prepare(body)['tasks'][0]['action'] == 'invoke'
    assert read(store.manifest)['runs']['fix-run']['cycles'] == []


def test_legacy_managed_entries_need_checklist_refresh_on_resume_and_finish(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    prepared = tasks.prepare(setup_task(spec))['tasks'][0]
    with store.locked() as manifest:
        manifest['entries'][prepared['key']].pop('checklist')
        write(store.manifest, manifest)
    resumed = tasks.resume()['tasks'][0]
    assert resumed['reason'] == 'checklist_refresh_required'
    result = tasks.finish(prepared['key'], prepared['claim']['id'], b'Legacy managed original', {'complete': True})
    assert result['action'] == 'recheck'
    accept(tasks, result)
    assert tasks.resume()['tasks'][0]['coverage_complete']


def test_followup_rejects_drift_without_spending_or_accepting_round(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    _, check = initial(tasks, setup_task(spec))
    before = store.manifest.read_bytes()
    Path(spec['inputs']['context']).write_text('Governing input changed during checking')
    with pytest.raises(ValueError, match='unchanged complete primary evidence'):
        accept(tasks, check)
    assert store.manifest.read_bytes() == before
    assert not tasks.resume()['tasks'][0]['coverage_complete']



def test_optional_reviewer_defaults_and_explicit_checklist_override(case):
    from native_review_observations import CONTRACT
    spec, store = case
    tasks = Tasks(REPO, PR)
    body = setup_task(spec, responsibility='content-review', native_review=CONTRACT, required=False)
    first = tasks.prepare(body)['tasks'][0]
    assert not first['checklist']['required']
    assert {'scope', 'evidence', 'validation', 'deliverables', 'native-evidence'} <= {
        item['id'] for item in first['checklist']['items']}
    body['tasks'][0]['definition']['skill_identity'] = 'reviewer:required-override'
    body['tasks'][0]['definition']['checklist'] = {'required': True}
    assert tasks.prepare(body)['tasks'][0]['checklist']['required']


def test_completion_keeps_invocation_local_plan_and_invalidates_on_its_change(case):
    spec, store = case
    fields = definition(spec)
    fields.pop('responsibility')
    fields['skill_identity'] = 'dk-workflows:dk-pr-fix'
    body = {'run': 'local-plan', 'tasks': [{'kind': 'completion', 'definition': fields,
                                         'options': {'finalizers': []}}]}
    tasks = Tasks(REPO, PR)
    _, check = initial(tasks, body)
    assert check['assignment']['options']['completion_plan'] == []
    accept(tasks, check)
    assert tasks.resume()['tasks'][0]['coverage_complete']
    body['tasks'][0]['options']['finalizers'] = ['team:new-duty']
    new = tasks.prepare(body)['tasks'][0]
    assert new['action'] == 'invoke'
    assert new['assignment']['options']['completion_plan'] == ['team:new-duty']


def test_skill_selected_optional_task_defaults_to_best_effort(case):
    import config
    spec, store = case
    fields = definition(spec)
    fields['skill_identity'] = 'team:check'
    write(config.path(REPO), {'skill_defaults': {'dk-workflows:dk-pr-fix': {'options': {
        'tasks': [{'skill': 'team:check', 'required': False}]}}}})
    body = {'tasks': [{'kind': 'skill', 'skill': 'dk-workflows:dk-pr-fix',
                      'selection': 'tasks', 'index': 0, 'definition': fields}]}
    tasks = Tasks(REPO, PR)
    value, check = initial(tasks, body)
    assert not value['checklist']['required']
    check = submit(tasks, check, ('scope',))
    check = submit(tasks, check, ('scope',))
    terminal = submit(tasks, check, ('scope',))
    assert terminal['action'] == 'complete' and not terminal['checklist_complete']
    assert tasks.resume()['tasks'][0]['coverage_complete']


def test_followup_findings_survive_rounds_refresh_and_fresh_session(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    body = setup_task(spec)
    _, check = initial(tasks, body)
    original = tasks.result(check['key'], 'original')
    first = report(check['checklist'], check['result'], ('scope',))
    added = {'id': 'followup-finding', 'body': 'Discovered while finishing missing work.'}
    first['findings'] = [added]
    second = tasks.finish(check['key'], check['claim'], json.dumps(first).encode(), first, round_id=check['round'])
    accept(tasks, second)
    expected = [{'id': 'original-finding'}, added]
    assert tasks.result(check['key'])['findings'] == expected
    assert tasks.result(check['key'], 'normalized')['normalized']['findings'] == expected
    assert tasks.resume()['tasks'][0]['finding_count'] == 2
    assert len(tasks.result(check['key'])['sources']) == 2
    body['tasks'][0]['definition']['checklist'] = {'max_checks': 1}
    refreshed = tasks.prepare(body)['tasks'][0]
    accept(tasks, refreshed)
    resumed = Tasks(REPO, PR)
    assert resumed.result(check['key'])['findings'] == expected
    assert resumed.resume()['tasks'][0]['finding_count'] == 2
    assert resumed.result(check['key'], 'original') == original
    Path(spec['inputs']['context']).write_text('Fresh primary inputs')
    current = resumed.prepare(body)['tasks'][0]
    result = resumed.finish(current['key'], current['claim']['id'], b'Fresh primary report',
                            {'complete': True, 'findings': []})
    accept(resumed, result)
    assert resumed.result(check['key'])['findings'] == []


def test_missing_earlier_followup_cannot_hide_its_findings(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    _, check = initial(tasks, setup_task(spec))
    value = report(check['checklist'], check['result'], ('scope',))
    value['findings'] = [{'id': 'later'}]
    second = tasks.finish(check['key'], check['claim'], b'Additional finding', value, round_id=check['round'])
    accept(tasks, second)
    gate = tasks.result(check['key'], 'checklist')['checklist']
    (store.root/'results'/gate['rounds'][0]['response']['file']).unlink()
    assert not tasks.resume()['tasks'][0]['coverage_complete']
    with pytest.raises(ValueError, match='damaged'):
        tasks.prepare(setup_task(spec))


@pytest.mark.parametrize('damage', ['missing', 'edited'])
@pytest.mark.parametrize('terminal', [False, True])
def test_submission_and_replay_require_intact_earlier_findings(case, damage, terminal):
    spec, store = case
    tasks = Tasks(REPO, PR)
    _, check = initial(tasks, setup_task(spec))
    first = report(check['checklist'], check['result'], ('scope',))
    first['findings'] = [{'id': 'additional'}]
    second = tasks.finish(check['key'], check['claim'], b'Additional finding', first, round_id=check['round'])
    response = report(second['checklist'], second['result'])
    original = json.dumps(response).encode()
    if terminal:
        tasks.finish(second['key'], second['claim'], original, response, round_id=second['round'])
    gate = tasks.result(check['key'], 'checklist')['checklist']
    path = store.root/'results'/gate['rounds'][0]['response']['file']
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('{}')
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='intact earlier response evidence'):
        tasks.finish(second['key'], second['claim'], original, response, round_id=second['round'])
    assert store.manifest.read_bytes() == before


@pytest.mark.parametrize('damage', ['missing', 'edited'])
@pytest.mark.parametrize('terminal', [False, True])
def test_primary_submission_replay_requires_every_response(case, damage, terminal):
    spec, store = case
    tasks = Tasks(REPO, PR)
    task, first = initial(tasks, setup_task(spec))
    value = report(first['checklist'], first['result'], ('scope',))
    value['findings'] = [{'id': 'additional'}]
    second = tasks.finish(first['key'], first['claim'], b'Additional finding', value, round_id=first['round'])
    if terminal:
        accept(tasks, second)
    gate = tasks.result(first['key'], 'checklist')['checklist']
    path = store.root/'results'/gate['rounds'][0]['response']['file']
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('{}')
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='intact earlier response evidence'):
        tasks.finish(task['key'], task['claim']['id'], b'Original task output',
                     {'complete': True, 'findings': [{'id': 'original-finding'}]})
    assert store.manifest.read_bytes() == before
