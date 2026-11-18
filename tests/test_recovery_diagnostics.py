"""Typed recovery routes preserve original evidence and certainty across sessions."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import config
import config_requests
from native_review import binding
from next_actions import inspect
from shared import publication
from shared.diagnostics import Diagnostic
from shared.recovery_diagnostics import Condition, cli_failure, condition, from_exception
from shared.workflow_storage import operations_root
from state import capture, read, write
from tasks import Tasks
from test_batch_state import cli, definition, sibling
from test_config_requests import service, preview, admit
from test_next_actions import setup, snapshot
from test_state import case, done, REPO, PR, ROOT
from checklist_reports import finish


def managed(tasks, spec, **options):
    return tasks.prepare({'tasks': [{'definition': definition(spec)}], **options})['tasks'][0]


def complete(tasks, prepared):
    return finish(tasks, prepared['key'], prepared['claim']['id'], b'Original evidence',
                  {'complete': True, 'findings': [{'id': 'retained'}]})


def run(arguments):
    result = subprocess.run(arguments, text=True, capture_output=True, timeout=30)
    return result, json.loads(result.stdout)


def test_classification_ignores_message_words_and_retains_unknown_dispatch():
    for message in ['claim stale budget pending', 'entirely different wording']:
        diagnostic = from_exception(condition('stale_inputs', message))
        assert diagnostic['code'] == 'stale_inputs'
        assert diagnostic['action']['kind'] == 'agent_task'
        unknown = from_exception(ValueError(message))
        assert unknown['code'] == 'unclassified_failure'
        assert unknown['dispatched'] is None and unknown['outcome'] == 'unknown'
    original = Diagnostic('publication_uncertain', 'private failure',
                          {'kind': 'command', 'argv': ['read-original'], 'artifacts': ['operation']},
                          dispatched=True)
    assert from_exception(original)['dispatched'] is True
    result = cli_failure(original, ['--key', 'abc', '--authorization', 'private instruction'])
    assert result['diagnostic']['code'] == original.code
    assert result['diagnostic']['action']['argv'] == ['read-original']
    assert result['diagnostic']['action']['artifacts'] == ['operation', 'key:abc']
    assert original.action['artifacts'] == ['operation']


def test_legacy_definition_and_active_claim_have_original_targeted_routes(case):
    spec, store = case
    started = done(store, spec, run='original', cycle='c1')
    tasks = Tasks(REPO, PR)
    before = snapshot(store.root)
    legacy = tasks.resume()['unmanaged'][0]
    assert legacy['reason'] == legacy['diagnostic']['code'] == 'definition_required'
    assert started['claim']['key'] in legacy['diagnostic']['action']['artifacts'][0]
    with pytest.raises(Condition) as error:
        tasks.saved_spec(legacy['key'])
    assert error.value.code == 'definition_required'
    assert snapshot(store.root) == before
    pending = managed(tasks, sibling(spec), run='original', cycle='c1')
    before = snapshot(store.root)
    resumed = tasks.resume(keys=[pending['key']])['tasks'][0]
    diagnostic = resumed['diagnostic']
    assert resumed['reason'] == 'unfinished' and diagnostic['code'] == 'active_claim'
    assert pending['key'] in diagnostic['action']['artifacts'][0]
    again = managed(tasks, sibling(spec), run='original', cycle='c1')
    assert again['action'] == 'recover_required'
    assert again['unfinished']['id'] == pending['claim']['id']
    assert again['diagnostic']['code'] == 'active_claim'
    assert snapshot(store.root) == before


@pytest.mark.parametrize('damage', ['missing', 'damaged'])
def test_result_restoration_and_fresh_session_reuse_keep_original_siblings(case, damage):
    spec, store = case
    tasks = Tasks(REPO, PR)
    first = managed(tasks, spec, run='original', cycle='c1')
    complete(tasks, first)
    second = managed(tasks, sibling(spec), run='original', cycle='c1')
    complete(tasks, second)
    path = Path(tasks.result(first['key'])['result'])
    original = path.read_bytes()
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('{broken')
    before = snapshot(store.root)
    result = tasks.resume()
    rows = {row['key']: row for row in result['tasks']}
    assert rows[first['key']]['diagnostic']['code'] == 'evidence_' + damage
    assert rows[second['key']]['reusable']
    assert rows[second['key']]['finding_count'] == 1
    with pytest.raises(Condition) as error:
        tasks.result(first['key'])
    assert error.value.code == 'evidence_' + damage
    assert snapshot(store.root) == before
    path.write_bytes(original)
    before = snapshot(store.root)
    for _ in range(2):
        process, resumed = run(cli('resume', '--run', 'original'))
        assert process.returncode == 0
        assert all(row['reusable'] for row in resumed['tasks'])
        assert resumed['runs']['original']['review_budget']['used'] == 1
    assert snapshot(store.root) == before


def test_missing_inputs_and_selective_drift_keep_claim_result_and_allowances(case, tmp_path):
    spec, store = case
    affected = tmp_path/'affected-input'
    affected.write_text('old')
    spec = deepcopy(spec)
    spec['inputs']['specific'] = str(affected)
    other = sibling(spec)
    other['inputs'].pop('specific')
    tasks = Tasks(REPO, PR)
    first, second = [managed(tasks, value, run='r', cycle='c1') for value in (spec, other)]
    complete(tasks, first)
    complete(tasks, second)
    affected.unlink()
    before = snapshot(store.root)
    rows = {row['key']: row for row in tasks.resume()['tasks']}
    assert rows[first['key']]['reason'] == 'refresh_required'
    assert rows[first['key']]['diagnostic']['code'] == 'evidence_missing'
    assert rows[second['key']]['reusable']
    assert snapshot(store.root) == before
    affected.write_text('new')
    before = snapshot(store.root)
    rows = {row['key']: row for row in tasks.resume()['tasks']}
    assert rows[first['key']]['diagnostic']['code'] == 'stale_inputs'
    assert rows[second['key']]['reusable']
    assert snapshot(store.root) == before
    pending = managed(tasks, spec, run='r', cycle='c2')
    affected.write_text('changed in flight')
    before = snapshot(store.root)
    report = tmp_path/'complete.json'
    report.write_text('{"complete":true}')
    process, response = run(cli('finish', '--key', pending['key'], '--claim', pending['claim']['id'],
                                '--report', str(report)))
    assert process.returncode == 2 and response['status'] == 'blocked'
    assert response['diagnostic']['code'] == 'stale_inputs'
    assert 'claim:' + pending['claim']['id'] in response['diagnostic']['action']['artifacts']
    assert snapshot(store.root) == before


@pytest.mark.parametrize('kind', ['review', 'retry', 'ci_repair', 'ci_rerun'])
def test_budget_rejections_are_typed_without_mutating_original_state(case, kind):
    spec, store = case
    for i in range(5 if kind == 'review' else 1):
        candidate = deepcopy(spec)
        candidate['options']['revision'] = i
        if kind != 'review':
            candidate['options']['process_limits'] = {
                {'retry': 'retries_per_task', 'ci_repair': 'ci_repairs_per_cycle',
                 'ci_rerun': 'ci_reruns_per_head'}[kind]: 0}
        done(store, candidate, run='r', cycle='c' + str(i))
    spec = candidate
    if kind == 'review':
        spec['options']['revision'] = 6
    else:
        Path(spec['inputs']['context']).write_text('changed')
    source = store.root/'request-spec.json'
    write(source, spec)
    arguments = ['start', '--spec', str(source), '--run', 'r', '--cycle', 'c6' if kind == 'review' else 'c0']
    if kind != 'review':
        arguments += ['--' + kind.replace('_', '-')]
    if kind == 'ci_rerun':
        arguments += ['--head', spec['git']['head']]
    before = snapshot(store.root)
    process, result = run(cli(*arguments))
    assert process.returncode == 2 and result['status'] == 'blocked'
    assert result['diagnostic']['code'] == 'allowance_exhausted'
    assert result['diagnostic']['action']['kind'] == 'human_input'
    assert 'run:r' in result['diagnostic']['action']['artifacts']
    assert snapshot(store.root) == before


def test_missing_execution_contract_and_capability_are_distinct_at_config_boundary(tmp_path):
    options = tmp_path/'options.json'
    options.write_text(json.dumps({'reviewers': ['code-review-ad:code-review-ad']}))
    available = tmp_path/'available.json'
    available.write_text('["dk-workflows:dk-pr-reviewers-run","code-review-ad:code-review-ad"]')
    arguments = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPO,
                 'skill-preflight', '--skill', 'dk-workflows:dk-pr-reviewers-run',
                 '--options', str(options), '--available', str(available)]
    process, result = run(arguments)
    assert process.returncode == 0 and not result['ready']
    assert result['diagnostics'][0]['code'] == 'execution_contract_missing'
    contract = tmp_path/'contracts.json'
    location = result['unsupported_native_reviews'][0]['location']
    contract.write_text(json.dumps({location: {'schema': 2, 'required': True,
                                             'skill_version': '1.0', 'passes': []}}))
    process, result = run(arguments + ['--native-reviews', str(contract)])
    assert process.returncode == 0 and not result['ready']
    assert result['diagnostics'][0]['code'] == 'execution_capability_missing'


def test_legacy_contract_missing_binding_is_typed_without_new_state(case):
    spec, store = case
    claim = store.start(spec)['claim']
    before = snapshot(store.root)
    with pytest.raises(Condition) as error:
        binding(capture(spec)['components'], claim)
    assert isinstance(error.value, ValueError)
    assert error.value.code == 'execution_contract_missing'
    assert snapshot(store.root) == before


@pytest.mark.parametrize('script', ['dk.py', 'config.py', 'config_requests.py', 'pr_config.py'])
def test_configuration_import_failure_uses_compatible_structured_boundary(tmp_path, script):
    # Copied installed defaults are malformed before main imports config defaults.
    plugin = tmp_path/'plugin'
    import shutil
    shutil.copytree(ROOT/'runtime', plugin/'runtime', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'codex', plugin/'codex')
    (plugin/'scripts').mkdir()
    shutil.copy2(ROOT/'scripts'/script, plugin/'scripts'/script)
    (plugin/'codex/skills/dk-pr-fix/config.json').write_text('{invalid')
    process, result = run([sys.executable, str(plugin/'scripts'/script), '--help'])
    assert process.returncode == 2 and result['status'] == 'blocked'
    assert result['diagnostic']['code'] == 'configuration_invalid'
    assert result['diagnostic']['action']['artifacts']
    if script == 'pr_config.py':
        assert 'Cannot read skill configuration' in process.stderr


def test_configuration_stale_and_uncertain_recovery_keep_receipts_and_error_type(service, monkeypatch):
    store, _ = service
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result)
    original_atomic = config_requests.atomic_text
    def interrupted(path, content):
        original_atomic(path, content)
        raise OSError('A different interruption explanation')
    monkeypatch.setattr(config_requests, 'atomic_text', interrupted)
    with pytest.raises(OSError) as error:
        store.apply(request_id)
    diagnostic = from_exception(error.value)
    assert diagnostic['code'] == 'configuration_write_uncertain'
    assert diagnostic['dispatched'] is None and diagnostic['outcome'] == 'unknown'
    assert diagnostic['action']['kind'] == 'command'
    assert diagnostic['action']['argv'][-2:] == ['--request-id', request_id]
    before = snapshot(store.root)
    process, result = run([sys.executable, str(ROOT/'scripts/config_requests.py'),
                           'resume', '--request-id', request_id])
    assert process.returncode == 0 and result['status'] == 'applying'
    assert result['diagnostic']['code'] == 'configuration_write_uncertain'
    assert snapshot(store.root) == before
    monkeypatch.setattr(config_requests, 'atomic_text', lambda *_: pytest.fail('Must preserve completed write'))
    assert store.apply(request_id)['status'] == 'applied'


def test_configuration_changed_admission_is_structured_and_read_only(service):
    store, context = service
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result)
    target = config.path(context['repository'])
    write(target, {'skill_defaults': {'dk-workflows:dk-ci': {'options': {'timeout_seconds': 42}}}})
    before = snapshot(store.root)
    result = store.resume(request_id)
    assert result['admission'] == 'stale'
    assert result['diagnostic']['code'] == 'stale_inputs'
    assert result['diagnostic']['action']['artifacts'] == [str(store._path(request_id))]
    assert snapshot(store.root) == before


def test_uncertain_publication_targets_original_read_only_operation(case, tmp_path):
    spec, store = case
    request = setup(spec)
    operation = publication.prepare('reply-comment', PR, {}, {'body': 'Original public answer'},
        [sys.executable, str(ROOT/'scripts/publish.py'), 'reconcile'],
        directory=operations_root(repository=spec['git']['checkout'])/'original')
    path = Path(operation['operation'])
    value = read(path)
    value['status'] = 'pending'
    value['intent'] = {key: value[key] for key in ('operation_id', 'request_hash')}
    write(path, value)
    request['publications'] = [str(path)]
    before = snapshot(path.parent)
    inspected = inspect(REPO, PR, request, checkout=spec['git']['checkout'])
    row = next(v for v in inspected['items'] if v.get('operation') == str(path))
    assert row['action'] == 'reconcile_publication'
    assert row['diagnostic']['code'] == 'publication_uncertain'
    assert row['diagnostic']['dispatched'] is True and row['diagnostic']['outcome'] == 'unknown'
    assert row['diagnostic']['action']['argv'] == value['reconcile_argv']
    process, failed = run(cli('prepare', '--file', str(tmp_path/'missing-input')))
    assert process.returncode == 2 and failed['diagnostic']['dispatched'] is None
    assert snapshot(path.parent) == before


def test_legacy_configuration_admission_and_missing_request_do_not_create_state(service):
    store, context = service
    request_id = store.start('Original request with no admission', context)['id']
    before = snapshot(store.root)
    with pytest.raises(Condition) as error:
        store.apply(request_id)
    assert error.value.code == 'admission_required'
    assert store.resume(request_id)['status'] == 'open'
    missing_id = 'cfg-' + 'f'*32
    process, response = run([sys.executable, str(ROOT/'scripts/config_requests.py'),
                             'resume', '--request-id', missing_id])
    assert process.returncode == 2 and response['diagnostic']['code'] == 'evidence_missing'
    assert str(store._path(missing_id)) in response['diagnostic']['action']['artifacts']
    assert snapshot(store.root) == before


def test_damaged_configuration_request_exposes_original_reference_without_writes(service):
    store, context = service
    request_id = store.start('Original request', context)['id']
    path = store._path(request_id)
    path.write_text('{broken')
    before = snapshot(store.root)
    process, response = run([sys.executable, str(ROOT/'scripts/config_requests.py'),
                             'resume', '--request-id', request_id])
    assert process.returncode == 2 and response['diagnostic']['code'] == 'evidence_damaged'
    assert str(path) in response['diagnostic']['action']['artifacts']
    assert snapshot(store.root) == before


def test_invalid_scoped_configuration_has_a_typed_configuration_route(service):
    _, context = service
    source = config.path(context['repository'])
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{invalid')
    before = source.read_bytes()
    process, result = run([sys.executable, str(ROOT/'scripts/config.py'), '--repository',
                           context['repository'], 'show'])
    assert process.returncode == 2 and result['status'] == 'blocked'
    assert result['diagnostic']['code'] == 'configuration_invalid'
    assert str(source) in result['diagnostic']['action']['artifacts']
    assert source.read_bytes() == before


@pytest.mark.parametrize('batch', [False, True])
def test_explicit_raw_recovery_drops_obsolete_claim_diagnostic(case, batch):
    spec, store = case
    original = store.start(spec, run='r', cycle='c1')['claim']
    if batch:
        result = store.start_batch([spec], run='r', cycle='c1',
                                   recover={original['key']: original['id']})['tasks'][0]
    else:
        result = store.start(spec, recover=original['id'], run='r', cycle='c1')
    assert result['action'] == 'invoke' and result['claim']['id'] != original['id']
    assert result.get('diagnostic', {}).get('code') != 'active_claim'
    assert store.status([spec])['runs']['r']['review_budget']['used'] == 1


def test_managed_missing_contract_has_known_code_before_any_claim(case, tmp_path):
    spec, store = case
    spec = deepcopy(spec)
    spec['task']['id'] = 'content-review'
    source = tmp_path/'request.json'
    write(source, {'tasks': [{'definition': definition(spec)}]})
    before = snapshot(store.root)
    process, result = run(cli('prepare', '--file', str(source)))
    assert process.returncode == 2 and result['status'] == 'blocked'
    assert result['diagnostic']['code'] == 'execution_contract_missing'
    assert str(source) in result['diagnostic']['action']['artifacts']
    assert snapshot(store.root) == before


@pytest.mark.parametrize('scope', ['user', 'repo', 'upstream'])
def test_json_valid_bad_saved_configuration_retains_its_source(service, scope):
    _, context = service
    upstream = 'github.com/base/repo'
    source = config.path(None if scope == 'user' else upstream if scope == 'upstream' else context['repository'])
    write(source, {'accounts': []})
    before = source.read_bytes()
    process, result = run([sys.executable, str(ROOT/'scripts/config.py'), '--repository',
                           context['repository'], '--upstream', upstream, 'show'])
    assert process.returncode == 2 and result['diagnostic']['code'] == 'configuration_invalid'
    assert str(source) in result['diagnostic']['action']['artifacts']
    assert source.read_bytes() == before


@pytest.mark.parametrize('error_type', [PermissionError, OSError])
def test_intact_inaccessible_result_keeps_legacy_blocker_and_unavailable_route(case, monkeypatch, error_type):
    import task_storage
    spec, store = case
    tasks = Tasks(REPO, PR)
    prepared = managed(tasks, spec)
    complete(tasks, prepared)
    result_path = Path(tasks.result(prepared['key'])['result'])
    original_read = task_storage.read
    def inaccessible(path):
        if Path(path) == result_path:
            raise error_type('Access explanation without diagnostic keywords')
        return original_read(path)
    monkeypatch.setattr(task_storage, 'read', inaccessible)
    before = snapshot(store.root)
    status = tasks.resume(keys=[prepared['key']])['tasks'][0]
    assert status['diagnostic']['code'] == 'evidence_unavailable'
    with pytest.raises(Condition) as error:
        tasks.result(prepared['key'])
    assert error.value.code == 'evidence_unavailable'
    entry = read(store.manifest)['entries'][prepared['key']]
    assert store._load_saved(entry)[1] == 'damaged_result'
    assert snapshot(store.root) == before


@pytest.mark.parametrize('flag', ['--options', '--workflow'])
def test_cli_json_errors_preserve_supported_configuration_input_paths(tmp_path, flag):
    path = tmp_path/'bad.json'
    path.write_text('{broken')
    command = ['skill-show', '--skill', 'dk-workflows:dk-ci'] if flag == '--options' else [
        'skill-plan', '--skill', 'dk-workflows:dk-pr-reviewers-run', '--kind', 'reviewers']
    process, result = run([sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPO,
                           *command, flag, str(path)])
    assert process.returncode == 2 and result['diagnostic']['code'] == 'evidence_damaged'
    assert str(path) in result['diagnostic']['action']['artifacts']
