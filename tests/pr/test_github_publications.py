"""Concrete remote payloads and recovery, including CI observer receipt handoff."""
import importlib.util
from pathlib import Path
import pytest
from shared import publication
from shared.ci_observation import for_pr
from shared.diagnostics import Diagnostic

ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value
publisher=module('publisher',ROOT/'scripts/publish.py')
fixture=module('github_fixture',ROOT/'tests/pr/fixtures/github_remote.py')
KINDS=sorted(publisher.KINDS - {'ci-approve'})  # Evidence-backed approvals have a dedicated suite.

@pytest.fixture
def remote(monkeypatch):
    value=fixture.Remote()
    for key in ('metadata','api','run'):monkeypatch.setattr(publisher,key,getattr(value,key))
    return value

@pytest.mark.parametrize('kind',KINDS)
def test_lost_response_reconciles_same_intent_once(remote,kind):
    request=fixture.request(kind)
    operation=publisher.prepare(request)['operation']
    remote.state['lose_response']=[kind]
    with pytest.raises(Diagnostic):publisher.publish(operation)
    assert publication.load(operation)['status']=='pending'
    assert publisher.prepare(request)['operation']==operation
    for _ in range(2):
        assert publisher.publish(operation,reconcile=True)['status']=='verified'
    assert remote.state['writes']=={kind:1}

@pytest.mark.parametrize('kind',KINDS)
def test_stale_head_blocks_before_dispatch(remote,kind):
    operation=publisher.prepare(fixture.request(kind))['operation']
    remote.state['metadata']['headRefOid']='d'*40
    with pytest.raises(Diagnostic):publisher.publish(operation)
    assert remote.state['writes']=={}
    assert publication.load(operation)['status']=='prepared'


def test_ci_rerun_receipt_opens_one_new_window_and_resume_keeps_it(remote):
    request=fixture.request('ci-rerun')
    with for_pr(fixture.PR,fixture.HEAD,clock=lambda:10) as old:assert old.current['started']==10
    operation=publisher.prepare(request)['operation']
    publisher.publish(operation)
    with for_pr(fixture.PR,fixture.HEAD,rerun=operation,clock=lambda:20) as refreshed:
        assert refreshed.current['started']==20
    with for_pr(fixture.PR,fixture.HEAD,rerun=operation,clock=lambda:30) as resumed:
        assert resumed.current['started']==20
    assert remote.state['writes']['ci-rerun']==1


def test_advanced_rerun_remains_uncertain_without_resend(remote):
    operation=publisher.prepare(fixture.request('ci-rerun'))['operation']
    remote.state['lose_response']=['ci-rerun']
    with pytest.raises(Diagnostic):publisher.publish(operation)
    remote.state['run']['run_attempt']=3
    for _ in range(2):
        with pytest.raises(Diagnostic):publisher.publish(operation,reconcile=True)
    assert remote.state['writes']['ci-rerun']==1


@pytest.mark.parametrize('lost_response', [False, True])
def test_changed_rerun_reason_reuses_exact_attempt_without_duplicate_send(remote, lost_response):
    request = fixture.request('ci-rerun')
    operation = publisher.prepare(request)['operation']
    if lost_response:
        remote.state['lose_response'] = ['ci-rerun']
        with pytest.raises(Diagnostic):
            publisher.publish(operation)
    else:
        publisher.publish(operation)
    refreshed = publisher.prepare({**request, 'reason': 'DNS outage confirmed from logs'})
    assert refreshed['operation'] == operation
    publisher.publish(operation, reconcile=True)
    assert remote.state['writes']['ci-rerun'] == 1


def test_automatic_rerun_allowance_survives_new_process_and_does_not_reset(remote, tmp_path):
    import json
    import os
    import subprocess
    import sys
    for attempt in range(1, 4):
        remote.state['run'].update(run_attempt=attempt, status='completed', conclusion='failure')
        operation = publisher.prepare({**fixture.request('ci-rerun'), 'attempt': attempt})['operation']
        publisher.publish(operation)
    remote.state['run'].update(status='completed', conclusion='failure')
    snapshot = tmp_path/'remote.json'
    snapshot.write_text(json.dumps(remote.state))
    script = '''
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1])
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value
publisher = load('fresh_publisher', root/'scripts/publish.py')
fixture = load('fresh_fixture', root/'tests/pr/fixtures/github_remote.py')
remote = fixture.Remote(json.loads(Path(sys.argv[2]).read_text()))
for key in ('metadata', 'api', 'run'): setattr(publisher, key, getattr(remote, key))
try:
    publisher.prepare({**fixture.request('ci-rerun'), 'attempt': 4})
except ValueError as exc:
    assert 'allowance exhausted (3/3)' in str(exc), str(exc)
else:
    raise AssertionError('fresh process restored automatic retries')
'''
    result = subprocess.run([sys.executable, '-c', script, str(ROOT), str(snapshot)],
        env={**os.environ, 'PYTHONPATH': str(ROOT/'runtime')}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert remote.state['writes']['ci-rerun'] == 3


def test_explicit_user_retry_is_bounded_and_preserves_exhausted_automatic_allowance(remote):
    request = {**fixture.request('ci-rerun'), 'automatic_limit': 1}
    operation = publisher.prepare(request)['operation']
    publisher.publish(operation)
    remote.state['run'].update(status='completed', conclusion='failure')
    with pytest.raises(ValueError, match='allowance exhausted'):
        publisher.prepare({**request, 'attempt': 2, 'automatic_limit': 3})
    authorization = {'request_id': 'user-turn-22', 'authorization': 'Retry failed test workflow once',
                     'source': 'conversation:user-turn-22'}
    explicit = {**request, 'attempt': 2, 'user_retry': authorization}
    operation = publisher.prepare(explicit)['operation']
    publisher.publish(operation)
    assert publisher.prepare({**explicit, 'reason': 'User requested retry'})['operation'] == operation
    remote.state['run'].update(status='completed', conclusion='failure')
    with pytest.raises(ValueError, match='different exact attempt'):
        publisher.prepare({**explicit, 'attempt': 3})
    with pytest.raises(ValueError, match='allowance exhausted'):
        publisher.prepare({**request, 'attempt': 3})
    assert remote.state['writes']['ci-rerun'] == 2


def test_uncertain_retry_blocks_new_attempt_and_new_user_authorization(remote):
    request = fixture.request('ci-rerun')
    operation = publisher.prepare(request)['operation']
    remote.state['lose_response'] = ['ci-rerun']
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    remote.state['run'].update(status='completed', conclusion='failure')
    for extra in ({}, {'user_retry': {'request_id': 'new', 'authorization': 'Retry once',
                                     'source': 'conversation:new'}}):
        with pytest.raises(ValueError, match='requires reconciliation'):
            publisher.prepare({**request, 'attempt': 2, **extra})
    assert remote.state['writes']['ci-rerun'] == 1


def test_prepared_retries_share_allowance_across_independent_workflows(remote, monkeypatch):
    original_api = remote.api
    def api(pr, path, **kwargs):
        if path.endswith('/actions/runs/78'):
            return {**remote.state['run'], 'id': 78}
        return original_api(pr, path, **kwargs)
    monkeypatch.setattr(publisher, 'api', api)
    request = {**fixture.request('ci-rerun'), 'automatic_limit': 1}
    first = publisher.prepare(request)['operation']
    with pytest.raises(ValueError, match='allowance exhausted'):
        publisher.prepare({**request, 'run_id': 78})
    assert publisher.prepare({**request, 'reason': 'Reconstructed diagnosis'})['operation'] == first
    assert remote.state['writes'] == {}


def test_lowered_limit_is_retained_on_existing_prepared_retry(remote):
    request = fixture.request('ci-rerun')
    operation = publisher.prepare(request)['operation']
    with pytest.raises(ValueError, match='allowance exhausted'):
        publisher.prepare({**request, 'automatic_limit': 0})
    assert publication.load(operation)['request']['approved']['automatic_limit'] == 0
    with pytest.raises(ValueError, match='allowance exhausted'):
        publisher.publish(operation)
    with pytest.raises(ValueError, match='allowance exhausted'):
        publisher.prepare(request)
    assert remote.state['writes'] == {}


def test_user_can_authorize_disabled_prepared_retry_without_erasing_its_reservation(remote):
    request = fixture.request('ci-rerun')
    operation = publisher.prepare(request)['operation']
    with pytest.raises(ValueError, match='allowance exhausted'):
        publisher.prepare({**request, 'automatic_limit': 0})
    user = {'request_id': 'retry-disabled', 'authorization': 'Retry this workflow once',
            'source': 'conversation:retry-disabled'}
    assert publisher.prepare({**request, 'user_retry': user})['operation'] == operation
    publisher.publish(operation)
    remote.state['run'].update(status='completed', conclusion='failure')
    with pytest.raises(ValueError, match=r'allowance exhausted \(1/0\)'):
        publisher.prepare({**request, 'attempt': 2})
    assert remote.state['writes']['ci-rerun'] == 1


def test_lowered_limit_does_not_prevent_uncertain_retry_reconciliation(remote):
    request = fixture.request('ci-rerun')
    operation = publisher.prepare(request)['operation']
    remote.state['lose_response'] = ['ci-rerun']
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    assert publisher.prepare({**request, 'automatic_limit': 0})['operation'] == operation
    publisher.publish(operation, reconcile=True)
    assert remote.state['writes']['ci-rerun'] == 1


@pytest.fixture
def diagnosed_retry(remote, tmp_path):
    import io
    import zipfile
    from types import SimpleNamespace
    from shared.ci_retry import capture, requests
    from shared.flaky_actions import evidence_file

    remote.state['pull']['changed_files'] = 1
    remote.state['checks'] = [{'name': 'install', 'bucket': 'fail', 'state': 'FAILURE',
        'link': 'https://github.com/owner/repo/actions/runs/77/job/770', 'workflow': 'CI'}]
    registry = SimpleNamespace(path=tmp_path/'registry.json')
    jobs = [{'id': 770, 'name': 'install', 'status': 'completed', 'conclusion': 'failure',
             'steps': [{'name': 'packages', 'conclusion': 'failure'}]}]
    source = evidence_file(registry, {'run': remote.state['run'], 'jobs': jobs})
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as bundle:
        bundle.writestr('install/1_packages.txt', 'Temporary failure resolving archive.ubuntu.com\n')
    archive = evidence_file(registry, data.getvalue(), '.zip')
    evidence = {'head': fixture.HEAD, 'repo': 'github.com/owner/repo', 'checks': [{
        'link': remote.state['checks'][0]['link'], 'attempt': 1, 'source_file': source,
        'archive_file': archive}]}
    snapshot = {'head': fixture.HEAD, 'result': 'failure', 'checks': remote.state['checks']}
    api = SimpleNamespace(repo='github.com/owner/repo',
                          request=lambda path: remote.api(fixture.PR, 'repos/owner/repo/' + path))
    handoff = capture(api, fixture.PR, fixture.HEAD,
                      snapshot | {'evidence_file': evidence_file(registry, evidence)}, snapshot)
    diagnosis = {'capture': handoff['capture'], 'runs': [{'run_id': 77, 'attempt': 1, 'jobs': [{
        'job_id': 770, 'verdict': 'unrelated', 'retryable': True, 'reason': 'Package mirror DNS outage',
        'change_comparison': 'code.py changes leave the mirror and workflow unchanged',
        'log_evidence': [{'member': 'install/1_packages.txt',
                          'excerpt': 'Temporary failure resolving archive.ubuntu.com'}]}]}]}
    return requests(handoff, diagnosis)['requests'][0]


@pytest.mark.parametrize('change', ['base', 'diff', 'checks', 'missing_archive', 'corrupt_archive', 'missing_source'])
def test_prepared_retry_revalidates_diagnosis_before_dispatch(remote, diagnosed_retry, change):
    operation = publisher.prepare(diagnosed_retry)['operation']
    if change == 'base':
        remote.state['pull']['base']['sha'] = 'c'*40
    elif change == 'diff':
        remote.state['files'][0]['patch'] += '\n+changed again'
    elif change == 'checks':
        remote.state['checks'][0]['state'] = 'TIMED_OUT'
    else:
        key = 'source_file' if change == 'missing_source' else 'archive_file'
        path = Path(diagnosed_retry['diagnosis'][key])
        if change.startswith('missing'):
            path.unlink()
        else:
            path.write_bytes(b'corrupt evidence')
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    assert remote.state['writes'] == {}
    assert publication.load(operation)['status'] == 'prepared'


def test_unchanged_diagnosis_dispatches_and_missing_evidence_does_not_block_reconciliation(remote, diagnosed_retry):
    operation = publisher.prepare(diagnosed_retry)['operation']
    remote.state['lose_response'] = ['ci-rerun']
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    Path(diagnosed_retry['diagnosis']['archive_file']).unlink()
    publisher.publish(operation, reconcile=True)
    assert remote.state['writes']['ci-rerun'] == 1


def test_same_capture_preserves_original_diagnosis_without_another_preparation(remote, diagnosed_retry):
    import copy
    operation = publisher.prepare(diagnosed_retry)['operation']
    reworded = copy.deepcopy(diagnosed_retry)
    reworded['diagnosis']['jobs'][0]['reason'] = 'Reworded DNS diagnosis'
    reworded['reason'] = 'Reworded DNS diagnosis'
    reused = publisher.prepare(reworded)
    assert reused['operation'] == operation
    assert not reused.get('preparations')
    assert reused['request']['approved']['diagnosis'] == diagnosed_retry['diagnosis']


def test_refreshed_diagnosis_reuses_prepared_operation_and_retains_original(remote, diagnosed_retry, tmp_path):
    from types import SimpleNamespace
    from shared.ci_retry import capture, requests
    from shared.flaky_actions import evidence_file
    operation = publisher.prepare(diagnosed_retry)['operation']
    remote.state['pull']['base']['sha'] = 'c'*40
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    snapshot = {'head': fixture.HEAD, 'result': 'failure', 'checks': remote.state['checks']}
    evidence = {'head': fixture.HEAD, 'repo': 'github.com/owner/repo', 'checks': [{
        'link': remote.state['checks'][0]['link'], 'attempt': 1,
        'source_file': diagnosed_retry['diagnosis']['source_file'],
        'archive_file': diagnosed_retry['diagnosis']['archive_file']}]}
    adapter = SimpleNamespace(repo='github.com/owner/repo',
        request=lambda path: remote.api(fixture.PR, 'repos/owner/repo/' + path))
    handoff = capture(adapter, fixture.PR, fixture.HEAD, snapshot | {
        'evidence_file': evidence_file(SimpleNamespace(path=tmp_path/'registry.json'), evidence)}, snapshot)
    diagnosis = {'capture': handoff['capture'], 'runs': [{'run_id': 77, 'attempt': 1,
        'jobs': diagnosed_retry['diagnosis']['jobs']}]}
    refreshed = publisher.prepare(requests(handoff, diagnosis)['requests'][0])
    assert refreshed['operation'] == operation
    original = publication.load(refreshed['preparations'][0])
    assert original['request']['approved']['diagnosis'] == diagnosed_retry['diagnosis']
    publisher.publish(operation)
    assert remote.state['writes']['ci-rerun'] == 1


def test_independent_workflow_retry_preserves_sibling_diagnosis(remote, diagnosed_retry, monkeypatch, tmp_path):
    import copy
    import json
    from types import SimpleNamespace
    from shared.ci_retry import capture, requests
    from shared.flaky_actions import evidence_file

    original_api = remote.api
    sibling_run = {**remote.state['run'], 'id': 78}
    def api(pr, path, *, payload=None):
        if path.endswith('/actions/runs/78'):
            return copy.deepcopy(sibling_run)
        if path.endswith('/actions/runs/78/rerun-failed-jobs'):
            sibling_run.update(run_attempt=2, status='queued', conclusion=None)
            return remote.changed('ci-rerun', '')
        return original_api(pr, path, payload=payload)
    monkeypatch.setattr(publisher, 'api', api)
    monkeypatch.setattr(remote, 'api', api)
    sibling_check = {**remote.state['checks'][0],
        'link': 'https://github.com/owner/repo/actions/runs/78/job/780'}
    remote.state['checks'].append(sibling_check)
    source = json.loads(Path(diagnosed_retry['diagnosis']['source_file']).read_text())
    source['run'] = sibling_run
    source['jobs'][0]['id'] = 780
    registry = SimpleNamespace(path=tmp_path/'registry.json')
    sibling_source = evidence_file(registry, source)
    evidence = {'head': fixture.HEAD, 'repo': 'github.com/owner/repo', 'checks': [
        {'link': check['link'], 'attempt': 1, 'source_file': source_path,
         'archive_file': diagnosed_retry['diagnosis']['archive_file']}
        for check, source_path in zip(remote.state['checks'],
            [diagnosed_retry['diagnosis']['source_file'], sibling_source])]}
    snapshot = {'head': fixture.HEAD, 'result': 'failure', 'checks': remote.state['checks']}
    adapter = SimpleNamespace(repo='github.com/owner/repo',
                              request=lambda path: api(fixture.PR, 'repos/owner/repo/' + path))
    handoff = capture(adapter, fixture.PR, fixture.HEAD,
                      snapshot | {'evidence_file': evidence_file(registry, evidence)}, snapshot)
    decisions = []
    for run_id in (77, 78):
        jobs = copy.deepcopy(diagnosed_retry['diagnosis']['jobs'])
        jobs[0]['job_id'] = run_id * 10
        decisions.append({'run_id': run_id, 'attempt': 1, 'jobs': jobs})
    retries = requests(handoff, {'capture': handoff['capture'], 'runs': decisions})['requests']
    operations = [publisher.prepare(request)['operation'] for request in retries]
    publisher.publish(operations[0])
    remote.state['checks'][0].update(bucket='pending', state='QUEUED')
    publisher.publish(operations[1])
    assert remote.state['writes']['ci-rerun'] == 2


def test_fresh_process_reuses_unchanged_prepared_diagnosis(remote, diagnosed_retry, tmp_path):
    import json
    import os
    import subprocess
    import sys

    operation = publisher.prepare(diagnosed_retry)['operation']
    state = tmp_path/'remote.json'
    state.write_text(json.dumps(remote.state))
    request = tmp_path/'request.json'
    request.write_text(json.dumps(diagnosed_retry))
    script = '''
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1])
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value
publisher = load('fresh_publisher', root/'scripts/publish.py')
fixture = load('fresh_fixture', root/'tests/pr/fixtures/github_remote.py')
remote = fixture.Remote(json.loads(Path(sys.argv[2]).read_text()))
for key in ('metadata', 'api', 'run'): setattr(publisher, key, getattr(remote, key))
request = json.loads(Path(sys.argv[3]).read_text())
prepared = publisher.prepare(request)
assert prepared['operation'] == sys.argv[4]
assert not prepared.get('preparations')
assert publisher.publish(prepared['operation'])['status'] == 'verified'
assert remote.state['writes'] == {'ci-rerun': 1}
'''
    result = subprocess.run([sys.executable, '-c', script, str(ROOT), str(state), str(request), operation],
        env={**os.environ, 'PYTHONPATH': str(ROOT/'runtime')}, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_changed_head_gets_own_allowance_but_same_explicit_request_does_not_expand(remote):
    request = {**fixture.request('ci-rerun'), 'automatic_limit': 0,
        'user_retry': {'request_id': 'user-turn-23', 'authorization': 'Retry this run once',
                       'source': 'conversation:user-turn-23'}}
    publisher.publish(publisher.prepare(request)['operation'])
    remote.state['metadata']['headRefOid'] = 'c'*40
    remote.state['run'].update(head_sha='c'*40, run_attempt=3, status='completed', conclusion='failure')
    with pytest.raises(ValueError, match='different exact attempt'):
        publisher.prepare({**request, 'head': 'c'*40, 'attempt': 3})
    automatic = {key: value for key, value in request.items() if key != 'user_retry'}
    operation = publisher.prepare({**automatic, 'head': 'c'*40, 'attempt': 3,
                                   'automatic_limit': 1})['operation']
    publisher.publish(operation)
    assert remote.state['writes']['ci-rerun'] == 2


@pytest.mark.parametrize('extra', [{'automatic_limit': -1}, {'automatic_limit': True},
    {'automatic_limit': 4}, {'user_retry': {}}, {'user_retry': {'request_id': 'unbound'}}])
def test_rerun_authorization_options_are_validated_before_reserving(remote, extra):
    with pytest.raises(ValueError):
        publisher.prepare({**fixture.request('ci-rerun'), **extra})
    assert remote.state['writes'] == {}


def test_thread_answer_is_verified_and_paginated(remote):
    request=fixture.request('resolve-thread')
    remote.state['thread']['comments']=[{'id':str(i),'databaseId':i+1000,'body':'context','author':{'login':'reviewer'},'url':'url'} for i in range(102)]+remote.state['thread']['comments']
    operation=publisher.prepare(request)['operation']
    assert publisher.publish(operation)['receipt']['reply_id']==42


def test_thread_without_our_answer_cannot_be_resolved(remote):
    remote.state['thread']['comments'][0]['author']['login']='someone-else'
    with pytest.raises(ValueError,match='verified posted answer'):
        publisher.prepare(fixture.request('resolve-thread'))
    assert not remote.state['writes']


def test_unconfirmed_creation_never_creates_a_second_pr(remote):
    operation=publisher.prepare(fixture.request('create-draft-pr'))['operation']
    remote.state['lose_response']=['create-draft-pr']
    with pytest.raises(Diagnostic):publisher.publish(operation)
    remote.state['pulls'][0]['body']='Changed by another actor'
    with pytest.raises(Diagnostic):publisher.publish(operation,reconcile=True)
    assert remote.state['writes']['create-draft-pr']==1


def test_partition_target_and_diff_line_are_fresh_inputs(remote):
    request=fixture.request('create-draft-pr');request['base_branch']='nondefault'
    with pytest.raises(ValueError,match='default branch'):publisher.prepare(request)
    request=fixture.request('post-review');request['comments'][0]['line']=50
    with pytest.raises(ValueError,match='changed line'):publisher.prepare(request)
    assert not remote.state['writes']


def test_review_request_already_satisfied_does_not_send_again(remote):
    remote.state['requested']={'users':[{'login':'reviewer'}],'teams':[{'slug':'team'}]}
    operation=publisher.prepare(fixture.request('request-review'))['operation']
    assert publisher.publish(operation)['status']=='verified'
    assert not remote.state['writes']


@pytest.mark.parametrize('method', ['squash', 'merge'])
def test_merge_binds_reviewed_head_and_reconciles_exact_commit(remote, method):
    request = fixture.request('merge-pr')
    request['merge_method'] = method
    operation = publisher.prepare(request)['operation']
    result = publisher.publish(operation)
    assert result['receipt']['head'] == fixture.HEAD
    assert result['receipt']['base'] == 'b'*40
    assert result['receipt']['merge_method'] == method
    assert publisher.publish(operation)['status'] == 'verified'
    assert remote.state['writes'] == {'merge-pr': 1}


@pytest.mark.parametrize('method', ['squash', 'merge'])
@pytest.mark.parametrize('historical', ['rest', 'graphql', 'both'])
def test_merge_uses_live_target_instead_of_historical_pr_base_metadata(remote, method, historical):
    if historical in ('rest', 'both'):
        remote.state['pull']['base']['sha'] = 'c'*40
    if historical in ('graphql', 'both'):
        remote.state['merge_gate']['baseRefOid'] = 'c'*40
    request = fixture.request('merge-pr')
    request['merge_method'] = method
    operation = publisher.prepare(request)['operation']
    result = publisher.publish(operation)
    assert result['status'] == 'verified'
    assert result['receipt']['base'] == 'b'*40
    assert publisher.publish(operation)['status'] == 'verified'
    assert remote.state['writes'] == {'merge-pr': 1}


@pytest.mark.parametrize('ref', [None, 'partition', 'missing'])
def test_merge_requires_consistent_target_branch_identity(remote, ref):
    if ref == 'missing':
        remote.state['merge_gate'].pop('baseRefName', None)
    else:
        remote.state['merge_gate']['baseRefName'] = ref
    with pytest.raises(ValueError, match='review and merge requirements'):
        publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


def test_historical_metadata_does_not_hide_a_changed_live_target(remote):
    remote.state['pull']['base']['sha'] = 'c'*40
    remote.state['merge_gate']['baseRefOid'] = 'c'*40
    remote.state['branches']['main'] = 'd'*40
    with pytest.raises(ValueError, match='Merge target tip changed'):
        publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


def test_target_drift_during_ci_observation_blocks_merge_preparation(remote, monkeypatch):
    original = remote.run

    def changing(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[1:3] == ['pr', 'checks']:
            remote.state['branches']['main'] = 'd'*40
        return result

    monkeypatch.setattr(publisher, 'run', changing)
    with pytest.raises(ValueError, match='Merge target tip changed'):
        publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


@pytest.mark.parametrize('state', ['SKIPPED', 'NEUTRAL'])
@pytest.mark.parametrize('method', ['squash', 'merge'])
def test_successful_ci_can_include_skipped_or_neutral_checks(remote, state, method):
    remote.state['checks'].append({'name': 'optional', 'bucket': 'skipping', 'state': state})
    request = fixture.request('merge-pr')
    request['merge_method'] = method
    operation = publisher.prepare(request)['operation']
    assert publisher.publish(operation)['status'] == 'verified'
    assert remote.state['writes'] == {'merge-pr': 1}


@pytest.mark.parametrize('state', ['SKIPPED', 'NEUTRAL'])
def test_skips_or_neutral_checks_alone_do_not_establish_successful_validation(remote, state):
    remote.state['checks'] = [{'name': 'optional', 'bucket': 'skipping', 'state': state}]
    with pytest.raises(ValueError, match='successful CI checks'):
        publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


@pytest.mark.parametrize('state', [None, 'PENDING', 'FAILURE', 'CANCELLED', 'UNKNOWN', 'SUCCESS'])
def test_skipping_bucket_cannot_mask_unsafe_or_inconsistent_check_states(remote, state):
    remote.state['checks'].append({'name': 'unsafe', 'bucket': 'skipping', 'state': state})
    with pytest.raises(ValueError, match='successful CI checks'):
        publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


@pytest.mark.parametrize('block', ['review', 'graphql', 'rest'])
def test_skipped_checks_do_not_override_repository_requirements(remote, block):
    remote.state['checks'].append({'name': 'optional', 'bucket': 'skipping', 'state': 'SKIPPED'})
    if block == 'review':
        remote.state['merge_gate']['reviewDecision'] = 'REVIEW_REQUIRED'
    elif block == 'graphql':
        remote.state['merge_gate']['mergeStateStatus'] = 'BLOCKED'
    else:
        remote.state['pull']['mergeable_state'] = 'blocked'
    with pytest.raises(ValueError):
        publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


def test_merge_recovery_preserves_resolved_method_after_config_changes(remote):
    import config
    from state import write
    upstream = 'github.com/upstream/project'
    skill = 'dk-workflows:dk-pr-publish'
    write(config.path(upstream), {'skill_defaults': {skill: {'options': {'default_merge_method': 'squash'}}}})
    request = fixture.request('merge-pr')
    request.pop('merge_method')
    operation = publisher.prepare(request, upstream=upstream, options={'default_merge_method': 'merge'})['operation']
    assert publication.load(operation)['request']['approved']['merge_method'] == 'merge'
    remote.state['lose_response'] = ['merge-pr']
    with pytest.raises(Diagnostic):
        publisher.publish(operation)
    retained = publication.load(operation)
    write(config.path(), {'skill_defaults': {skill: {'options': {'default_merge_method': 'squash'}}}})
    reloaded = module('fresh_publisher', ROOT/'scripts/publish.py')
    for key in ('metadata', 'api', 'run'):
        setattr(reloaded, key, getattr(remote, key))
    result = reloaded.publish(operation, reconcile=True)
    assert result['status'] == 'verified'
    assert result['receipt']['merge_method'] == 'merge'
    assert publication.load(operation)['request'] == retained['request']
    assert remote.state['writes'] == {'merge-pr': 1}


@pytest.mark.parametrize('decision', ['APPROVED', '', None])
@pytest.mark.parametrize('own_pr', [False, True])
def test_authorized_merge_accepts_own_pr_and_no_required_approval(remote, decision, own_pr):
    remote.state['merge_gate']['reviewDecision'] = decision
    if own_pr:
        remote.state['pr_author']['id'] = remote.state['actor_id']
    operation = publisher.prepare(fixture.request('merge-pr'))['operation']
    assert publisher.publish(operation)['status'] == 'verified'
    assert remote.state['writes'] == {'merge-pr': 1}


@pytest.mark.parametrize('decision', ['REVIEW_REQUIRED', 'CHANGES_REQUESTED', 'UNKNOWN', 'missing'])
def test_merge_rejects_unsatisfied_or_unknown_review_requirements(remote, decision):
    if decision == 'missing':
        del remote.state['merge_gate']['reviewDecision']
    else:
        remote.state['merge_gate']['reviewDecision'] = decision
    with pytest.raises(ValueError, match='review and merge requirements'):
        publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


def test_review_requirement_added_after_preparation_blocks_dispatch(remote):
    remote.state['merge_gate']['reviewDecision'] = ''
    operation = publisher.prepare(fixture.request('merge-pr'))['operation']
    remote.state['merge_gate']['reviewDecision'] = 'REVIEW_REQUIRED'
    with pytest.raises(Diagnostic): publisher.publish(operation)
    assert not remote.state['writes']
    assert publication.load(operation)['status'] == 'prepared'


@pytest.mark.parametrize('failure', ['pending-ci', 'failed-ci', 'missing-ci', 'unapproved', 'target',
                                     'draft', 'unknown-mergeability', 'rest-blocked', 'graphql-blocked'])
def test_merge_readiness_failures_block_all_writes(remote, failure):
    if failure == 'pending-ci': remote.state['checks'][0]['bucket'] = 'pending'
    if failure == 'failed-ci': remote.state['checks'][0]['bucket'] = 'fail'
    if failure == 'missing-ci': remote.state['checks'] = []
    if failure == 'unapproved': remote.state['merge_gate']['reviewDecision'] = 'REVIEW_REQUIRED'
    if failure == 'target': remote.state['branches']['main'] = 'd'*40
    if failure == 'draft': remote.state['pull']['draft'] = True
    if failure == 'unknown-mergeability': remote.state['pull']['mergeable'] = None
    if failure == 'rest-blocked':
        remote.state['merge_gate']['reviewDecision'] = ''
        remote.state['pull']['mergeable_state'] = 'blocked'
    if failure == 'graphql-blocked':
        remote.state['merge_gate'].update(reviewDecision=None, mergeStateStatus='BLOCKED')
    with pytest.raises(ValueError): publisher.prepare(fixture.request('merge-pr'))
    assert not remote.state['writes']


def test_merge_target_change_after_preparation_blocks_dispatch(remote):
    operation = publisher.prepare(fixture.request('merge-pr'))['operation']
    remote.state['branches']['main'] = 'd'*40
    with pytest.raises(Diagnostic): publisher.publish(operation)
    assert not remote.state['writes']
    assert publication.load(operation)['status'] == 'prepared'


@pytest.mark.parametrize('drift', ['actor', 'target', 'method'])
def test_unexpected_merge_receipt_remains_uncertain_and_never_resends(remote, drift):
    operation = publisher.prepare(fixture.request('merge-pr'))['operation']
    remote.state['lose_response'] = ['merge-pr']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    if drift == 'actor': remote.state['pull']['merged_by']['id'] = 99
    if drift == 'target': remote.state['merge_commit']['parents'][0]['sha'] = 'd'*40
    if drift == 'method': remote.state['merge_commit']['parents'].append({'sha': fixture.HEAD})
    for _ in range(2):
        with pytest.raises(Diagnostic): publisher.publish(operation, reconcile=True)
    assert remote.state['writes'] == {'merge-pr': 1}
    assert publication.load(operation)['status'] == 'pending'


def test_self_approval_is_blocked_and_actor_drift_blocks_dispatch(remote):
    request = fixture.request('post-review')
    request['event'] = 'APPROVE'
    remote.state['pr_author']['id'] = remote.state['actor_id']
    with pytest.raises(ValueError, match='own PR'): publisher.prepare(request)
    remote.state['pr_author']['id'] = 2
    operation = publisher.prepare(request)['operation']
    remote.state['actor_id'] = 3
    with pytest.raises(Diagnostic): publisher.publish(operation)
    assert not remote.state['writes']


@pytest.fixture
def review_transport(remote, monkeypatch):
    """Vary list and individual evidence independently, as GitHub does."""
    controls = {'list': lambda values: values, 'detail': lambda value: value, 'reads': []}
    original = remote.api

    def api(pr, path, **kwargs):
        value = original(pr, path, **kwargs)
        if '/reviews/' in path and path.split('?')[0].endswith('/comments'):
            return controls['list'](value)
        if '/pulls/comments/' in path:
            controls['reads'].append(path)
            return controls['detail'](value)
        return value

    monkeypatch.setattr(remote, 'api', api)
    monkeypatch.setattr(publisher, 'api', api)
    return controls


@pytest.mark.parametrize('field', ['line', 'side', 'both'])
@pytest.mark.parametrize('missing', ['omitted', 'null'])
def test_review_list_missing_locations_uses_individual_comment(remote, review_transport, field, missing):
    keys = ('line', 'side') if field == 'both' else (field,)

    def incomplete(values):
        for value in values:
            for key in keys:
                if missing == 'null': value[key] = None
                else: value.pop(key)
        return values

    review_transport['list'] = incomplete
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    result = publisher.publish(operation)
    assert result['status'] == 'verified'
    assert result['receipt']['comments'] == [
        {'id': 200, 'path': 'code.py', 'line': 1, 'side': 'RIGHT', 'body': 'Consider the value.'}]
    assert review_transport['reads'] == ['repos/owner/repo/pulls/comments/200']
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('change', [
    {'id': 201}, {'pull_request_review_id': 101},
    {'pull_request_url': 'https://api.github.com/repos/owner/repo/pulls/8'},
    {'user': {'login': 'someone-else'}}, {'original_commit_id': 'd'*40},
    {'path': 'other.py'}, {'body': 'Edited'}, {'line': 2}, {'side': 'LEFT'},
    {'original_line': 2}, {'original_line': True}, {'original_line': 0},
    {'line': None, 'original_line': None}, {'side': None},
    {'in_reply_to_id': 199}, {'start_line': 1}, {'original_start_line': 1},
])
def test_review_conflicting_or_incomplete_individual_evidence_stays_pending(
        remote, review_transport, change):
    review_transport['detail'] = lambda value: {**value, **change}
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    for _ in range(2):
        with pytest.raises(Diagnostic): publisher.publish(operation, reconcile=True)
    assert publication.load(operation)['status'] == 'pending'
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('field', ['in_reply_to_id', 'start_line', 'original_start_line'])
def test_review_conflicting_list_identity_evidence_stays_pending(remote, review_transport, field):
    review_transport['list'] = lambda values: [{**value, field: 1} for value in values]
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    pending = publication.load(operation)
    with pytest.raises(Diagnostic): publisher.publish(operation, reconcile=True)
    retained = publication.load(operation)
    assert retained['status'] == 'pending'
    assert retained['intent'] == pending['intent']
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('failure', ['unavailable', 'extra', 'missing', 'duplicate', 'ambiguous-review'])
def test_review_unavailable_or_ambiguous_evidence_never_resends(remote, review_transport, failure):
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    remote.state['lose_response'] = ['post-review']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    if failure == 'unavailable':
        def unavailable(value): raise OSError('Individual comment unavailable')
        review_transport['detail'] = unavailable
    elif failure == 'ambiguous-review':
        remote.state['reviews'].append({**remote.state['reviews'][0], 'id': 101})
    else:
        comments = remote.state['review_comments']['100']
        if failure == 'missing': comments.clear()
        elif failure == 'extra': comments.append({**comments[0], 'id': 201})
        else: comments.append(dict(comments[0]))
    with pytest.raises(Diagnostic): publisher.publish(operation, reconcile=True)
    assert publication.load(operation)['status'] == 'pending'
    assert remote.state['writes'] == {'post-review': 1}


@pytest.mark.parametrize('current_line', [None, 12])
def test_pending_review_recovers_original_location_after_head_changes(remote, current_line):
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    remote.state['lose_response'] = ['post-review']
    with pytest.raises(Diagnostic): publisher.publish(operation)
    pending = publication.load(operation)
    remote.state['metadata']['headRefOid'] = 'd'*40
    remote.state['review_comments']['100'][0].update(line=current_line, commit_id='d'*40)
    for _ in range(2):
        with pytest.raises(Diagnostic) as exc:
            publisher.publish(operation, reconcile=True)
        assert exc.value.code == 'publication_not_ready'
        verified = publication.load(operation)
        assert verified['status'] == 'verified'
        assert verified['receipt']['comments'][0]['line'] == 1
        for key in ('request', 'request_hash', 'operation_id', 'intent', 'failure'):
            assert verified.get(key) == pending.get(key)
    # The historical receipt releases the pending-target block, while a new
    # publication must independently pass live head validation.
    request = fixture.request('post-review')
    request.update(head='d'*40, body='A new authorized review')
    new_operation = publisher.prepare(request)['operation']
    assert new_operation != operation
    assert publication.load(new_operation)['status'] == 'prepared'
    assert remote.state['writes'] == {'post-review': 1}
    result = publisher.publish(new_operation)
    assert result['status'] == 'verified'
    assert result['receipt']['comments'][0]['id'] != verified['receipt']['comments'][0]['id']
    assert publication.load(operation)['receipt'] == verified['receipt']
    assert remote.state['writes'] == {'post-review': 2}


def test_pending_list_only_verification_failure_recovers_without_changing_intent(remote, monkeypatch):
    operation = publisher.prepare(fixture.request('post-review'))['operation']
    observe = publisher.Handler.observe

    def old_observe(self, expected):
        raise ValueError('Published review comments differ from the prepared payload')

    monkeypatch.setattr(publisher.Handler, 'observe', old_observe)
    with pytest.raises(Diagnostic): publisher.publish(operation)
    pending = publication.load(operation)
    assert pending['status'] == 'pending'
    monkeypatch.setattr(publisher.Handler, 'observe', observe)
    assert publisher.publish(operation, reconcile=True)['status'] == 'verified'
    verified = publication.load(operation)
    for key in ('request', 'request_hash', 'operation_id', 'intent', 'response'):
        assert verified[key] == pending[key]
    assert remote.state['writes'] == {'post-review': 1}


def test_review_current_location_requires_original_commit_and_all_comments_are_verified(remote, review_transport):
    request = fixture.request('post-review')
    request['comments'].append({**request['comments'][0], 'body': 'Second finding'})
    review_transport['detail'] = lambda value: {k: v for k, v in value.items() if k != 'original_line'}
    review_transport['list'] = lambda values: [
        {k: v for k, v in value.items() if k != 'original_line'} for value in reversed(values)]
    operation = publisher.prepare(request)['operation']
    assert publisher.publish(operation)['status'] == 'verified'
    assert len(review_transport['reads']) == 2
    assert remote.state['writes'] == {'post-review': 1}
