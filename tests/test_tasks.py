"""Managed task lifecycle preserves cache reuse without model-built resume specs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

import config
from state import capture, read, write
from tasks import Tasks
from checklist_reports import accept, finish, report as checklist_report
from test_batch_state import cli, definition, sibling
from test_state import case, done, git, REPO, PR


def request(spec, **options):
    return {'tasks': [{'definition': definition(spec)}], **options}


def run_cli(*arguments):
    return json.loads(subprocess.check_output(cli(*arguments), text=True, timeout=60))


def test_prepare_finish_and_fresh_session_resume_need_no_spec_reconstruction(case, tmp_path):
    spec, store = case
    source = tmp_path/'request.json'
    write(source, request(spec, run='run', cycle='c1'))
    prepared = run_cli('prepare', '--file', str(source))['tasks'][0]
    assert prepared['action'] == 'invoke'
    source.unlink()  # The model's transient request is not needed on resume.
    report = tmp_path/'report.json'
    write(report, {'complete': True, 'findings': [{'id': 'f1', 'body': 'Finding evidence'}]})
    original = tmp_path/'original.txt'
    original.write_text('Original report\n' + 'Private detail. '*1000)
    finished = run_cli('finish', '--key', prepared['key'], '--claim', prepared['claim']['id'],
                       '--report', str(report), '--original', str(original))
    assert finished['action'] == 'recheck'
    write(report, checklist_report(finished['checklist'], finished['result']))
    finished = run_cli('finish', '--key', prepared['key'], '--claim', finished['claim'],
                       '--round', finished['round'], '--report', str(report))
    assert finished['status'] == 'complete'
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    resumed = run_cli('resume', '--run', 'run')
    task = resumed['tasks'][0]
    assert task['reusable'] and task['coverage_complete'] and task['finding_count'] == 1
    assert 'findings' not in task and 'Private detail' not in json.dumps(resumed)
    assert run_cli('result', '--key', task['key'])['findings'] == [{'id': 'f1', 'body': 'Finding evidence'}]
    assert run_cli('result', '--key', task['key'], '--section', 'original')['original'] == original.read_text()
    assert {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before
    write(source, request(spec, run='new-session', cycle='c1'))
    assert run_cli('prepare', '--file', str(source))['tasks'][0]['action'] == 'reuse'
    assert 'new-session' not in store.status([])['runs']
    assert store.status([])['runs']['run']['cycles'] == ['c1']


def test_managed_adoption_keeps_legacy_result_fingerprint_and_budgets(case):
    spec, store = case
    done(store, spec, run='original', cycle='c1')
    before = read(store.manifest)
    tasks = Tasks(REPO, PR)
    assert tasks.resume()['unmanaged'][0]['reason'] == 'definition_required'
    result = tasks.prepare(request(spec, run='other', cycle='c1'))['tasks'][0]
    assert result['action'] == 'recheck'
    accept(tasks, result)
    assert tasks.prepare(request(spec))['tasks'][0]['action'] == 'reuse'
    after = read(store.manifest)
    key = capture(spec)['key']
    assert after['entries'][key]['result'] == before['entries'][key]['result']
    assert after['runs'] == before['runs']
    assert {p.name for p in store.root.iterdir()} == {'manifest.json', '.lock', 'results'}


def test_relative_inputs_remain_reusable_from_another_session_directory(case, tmp_path):
    spec, store = case
    fields = deepcopy(definition(spec))
    for field in ('resources', 'instructions', 'inputs'):
        fields[field] = {key: os.path.relpath(path, tmp_path) for key, path in fields[field].items()}
    fields['git']['checkout'] = os.path.relpath(fields['git']['checkout'], tmp_path)
    source = tmp_path/'request.json'
    write(source, {'tasks': [{'definition': fields}]})
    first = json.loads(subprocess.check_output(cli('prepare', '--file', str(source)), cwd=tmp_path, text=True))['tasks'][0]
    tasks = Tasks(REPO, PR)
    assert first['key'] == capture(spec)['key']
    finish(tasks, first['key'], first['claim']['id'], b'original result', {'complete': True})
    other = tmp_path/'other-session'
    other.mkdir()
    resumed = json.loads(subprocess.check_output(cli('resume'), cwd=other, text=True))
    assert resumed['tasks'][0]['reusable']


def test_partial_failure_retains_successful_sibling_and_exact_recovery(case):
    spec, store = case
    other = sibling(spec)
    tasks = Tasks(REPO, PR)
    body = request(spec, run='run', cycle='c1')
    body['tasks'].append({'definition': definition(other)})
    first, second = tasks.prepare(body)['tasks']
    finish(tasks, first['key'], first['claim']['id'], b'completed', {'complete': True, 'findings': []})
    replayed = tasks.prepare(body)['tasks']
    assert [item['action'] for item in replayed] == ['reuse', 'recover_required']
    assert tasks.resume()['unfinished'][0]['id'] == second['claim']['id']
    body['recover'] = {second['key']: second['claim']['id']}
    recovered = tasks.prepare(body)['tasks'][1]
    assert recovered['action'] == 'invoke'
    assert recovered['claim']['run'] == 'run' and recovered['claim']['cycle'] == 'c1'
    assert len(store.status([])['runs']['run']['cycles']) == 1


def test_missing_or_changed_sources_do_not_hide_unchanged_siblings(case, tmp_path):
    spec, store = case
    other = sibling(spec)
    resource = tmp_path/'other.md'
    resource.write_text('Other installed reviewer')
    other['skill']['resources'] = {'entry': str(resource)}
    tasks = Tasks(REPO, PR)
    for current in (spec, other):
        started = tasks.prepare(request(current))['tasks'][0]
        finish(tasks, started['key'], started['claim']['id'], b'complete', {'complete': True})
    resource.write_text('Changed instructions')
    resumed = {item['key']: item for item in tasks.resume()['tasks']}
    assert resumed[capture(spec)['key']]['reusable']
    assert not resumed[capture(other)['key']]['reusable']
    resource.unlink()
    resumed = tasks.resume()['tasks']
    assert sum(item['reusable'] for item in resumed) == 1
    assert any(item['reason'] == 'refresh_required' for item in resumed)


def test_native_binding_is_returned_and_original_observations_still_required(case):
    from native_review_observations import CONTRACT, observations
    spec, store = case
    fields = definition(spec)
    fields['native_review'] = CONTRACT
    tasks = Tasks(REPO, PR)
    started = tasks.prepare({'tasks': [{'definition': fields}]})['tasks'][0]
    native_spec = {**spec, 'native_review': CONTRACT}
    assert started['native'] == store.native_binding(native_spec, started['claim']['id'])
    output = b'Native skill completed.'
    saved = finish(tasks, started['key'], started['claim']['id'], output, {'complete': True},
                         native_evidence=observations(native_spec, started['claim'], output))
    assert saved['status'] == 'complete'
    assert tasks.resume()['tasks'][0]['reusable']


def test_concurrent_prepare_and_lost_response_keep_one_claim(case, tmp_path):
    spec, store = case
    path = tmp_path/'request.json'
    write(path, request(spec, run='run', cycle='c1'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run_cli('prepare', '--file', str(path)), range(2)))
    assert sorted(item['tasks'][0]['action'] for item in outcomes) == ['invoke', 'recover_required']
    assert len(Tasks(REPO, PR).resume()['unfinished']) == 1
    assert len(store.status([])['runs']['run']['cycles']) == 1


@pytest.mark.parametrize('kind', ['hook', 'finalizer'])
def test_occurrences_share_lifecycle_and_preserve_completion_across_sessions(case, kind):
    spec, store = case
    fields = definition(spec)
    if kind == 'hook':
        fields.pop('responsibility')
        fields.pop('assigned_instructions')
        fields['skill_identity'] = 'dk-workflows:dk-pr-fix'
        write(config.path(REPO), {'processes': {'pr-fix': {'hooks': {'before_cycle': [{'instructions': 'Check scope.'}]}}}})
        selector = {'kind': kind, 'phase': 'before_cycle', 'index': 0}
    else:
        fields.pop('responsibility')
        write(config.path(REPO), {'processes': {'pr-fix': {'finalizers': ['reviewer:a']}}})
        selector = {'kind': kind, 'finalizer': 'reviewer:a'}
    body = {'run': 'run', 'cycle': 'c1', 'tasks': [{**selector, 'definition': fields}]}
    tasks = Tasks(REPO, PR)
    started = tasks.prepare(body)['tasks'][0]
    assert started['checklist']['required']
    checked = tasks.finish(started['key'], started['claim']['id'], b'completed', {'complete': True})
    assert checked['action'] == 'recheck' and checked['check_number'] == 1
    accept(tasks, checked)
    assert Tasks(REPO, PR).prepare(body)['tasks'][0]['action'] == 'reuse'
    assert store.status([])['runs']['run']['cycles'] == []
    assert tasks.resume(run='run')['tasks'][0]['reusable']
    write(config.path(REPO), {'processes': {'pr-fix': {'finalizers': [], 'hooks': {'before_cycle': []}}}})
    assert tasks.resume(run='run')['tasks'][0]['reason'] == 'refresh_required'


def test_history_only_cleanup_preserves_managed_content_review(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    first = tasks.prepare(request(spec))['tasks'][0]
    finish(tasks, first['key'], first['claim']['id'], b'complete', {'complete': True})
    git(spec['git']['checkout'], 'commit', '--amend', '-qm', 'New message')
    changed = deepcopy(spec)
    changed['git']['head'] = git(spec['git']['checkout'], 'rev-parse', 'HEAD')
    assert capture(spec)['fingerprint'] == capture(changed)['fingerprint']
    assert tasks.prepare({'tasks': [{'key': first['key'], 'git': {'head': changed['git']['head']}}]})['tasks'][0]['action'] == 'reuse'
    assert tasks.resume()['tasks'][0]['reusable']
    assert tasks.resume()['tasks'][0]['git']['head'] == changed['git']['head']


def test_resume_stays_small_and_progress_reads_original_pages_without_writes(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    first = tasks.prepare(request(spec, run='run', cycle='c1'))['tasks'][0]
    finish(tasks, first['key'], first['claim']['id'], b'original', {'complete': True},
                 progress={'disposition': 'resolved', 'source': 'original source'})
    initial_size = len(json.dumps(tasks.resume()))
    records = [{'claim': first['claim']['id'], 'value': {'source': 'sourced acceptance '*1000, 'index': i}}
               for i in range(43)]
    with store.locked() as manifest:
        manifest['runs']['run']['progress'] = records
        write(store.manifest, manifest)
    before = store.manifest.read_bytes()
    assert len(json.dumps(tasks.resume())) < initial_size + 20
    collected, offset = [], 0
    while offset is not None:
        page = tasks.progress('run', after=offset, limit=10)
        collected.extend(page['records'])
        offset = page['next']
    assert collected == records
    assert store.manifest.read_bytes() == before
    with pytest.raises(ValueError):
        tasks.progress('run', limit=101)


def test_invalid_request_and_input_drift_do_not_replace_active_definition(case):
    spec, store = case
    tasks = Tasks(REPO, PR)
    started = tasks.prepare(request(spec, run='run', cycle='c1'))['tasks'][0]
    before = store.manifest.read_bytes()
    other = sibling(spec)
    other['inputs']['context'] += '.missing'
    invalid = {'tasks': [{'definition': definition(spec)}, {'definition': definition(other)}]}
    with pytest.raises(OSError):
        tasks.prepare(invalid)
    assert store.manifest.read_bytes() == before
    Path(spec['inputs']['context']).write_text('Changed while running')
    with pytest.raises(ValueError, match='Inputs changed'):
        finish(tasks, started['key'], started['claim']['id'], b'stale', {'complete': True})
    assert store.manifest.read_bytes() == before
