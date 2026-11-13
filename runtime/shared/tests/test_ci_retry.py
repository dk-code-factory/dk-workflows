"""Native retry diagnoses remain bound to logs, PR changes and every failed job."""
import copy
import io
import json
from pathlib import Path
import zipfile

import pytest
from shared.ci_retry import capture, requests, validate
from shared.flaky_actions import evidence_file
from test_flaky_tests import FakeAPI

HEAD = 'a' * 40
PR = 'https://github.com/owner/repo/pull/7'


class API:
    repo = 'github.com/owner/repo'

    def __init__(self):
        self.pull = {'head': {'sha': HEAD, 'ref': 'feature', 'repo': {'id': 2, 'full_name': 'fork/repo'}},
                     'base': {'sha': 'b' * 40, 'ref': 'main', 'repo': {'id': 1, 'full_name': 'owner/repo'}},
                     'changed_files': 1}
        self.files = [{'filename': 'app.py', 'status': 'modified', 'patch': '@@ -1 +1 @@\n-old\n+new'}]
        self.runs = {run_id: {'id': run_id, 'head_sha': HEAD, 'run_attempt': 1,
            'status': 'completed', 'conclusion': 'failure', 'path': f'.github/workflows/{run_id}.yml'}
            for run_id in (11, 22)}

    def request(self, path):
        if path == 'pulls/7':
            return copy.deepcopy(self.pull)
        if path.startswith('pulls/7/files?'):
            return copy.deepcopy(self.files)
        return copy.deepcopy(self.runs[int(path.rsplit('/', 1)[-1])])


@pytest.fixture
def context(tmp_path):
    api = API()
    registry = type('Registry', (), {'path': tmp_path / 'registry.json'})()
    checks, evidence = [], {'repo': api.repo, 'head': HEAD, 'checks': []}
    for run_id in api.runs:
        jobs = [{'id': run_id * 10, 'name': 'install', 'status': 'completed',
                 'conclusion': 'failure', 'steps': [{'name': 'packages', 'conclusion': 'failure'}]},
                {'id': run_id * 10 + 1, 'name': 'lint', 'status': 'completed', 'conclusion': 'success'}]
        source = evidence_file(registry, {'run': api.runs[run_id], 'jobs': jobs})
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('install/1_packages.txt', 'Temporary failure resolving archive.ubuntu.com\n')
            bundle.writestr('lint/1_lint.txt', 'All checks passed\n')
        logs = evidence_file(registry, archive.getvalue(), '.zip')
        link = f'https://github.com/owner/repo/actions/runs/{run_id}/job/{run_id * 10}'
        checks.append({'name': 'install', 'bucket': 'fail', 'link': link})
        checks.append({'name': 'lint', 'bucket': 'pass', 'link': link + '1'})
        evidence['checks'].append({'run_id': run_id, 'attempt': 1, 'link': link,
                                  'source_file': source, 'archive_file': logs})
    snapshot = {'result': 'failure', 'head': HEAD, 'checks': checks}
    decision = copy.deepcopy(snapshot) | {'evidence_file': evidence_file(registry, evidence)}
    handoff = capture(api, PR, HEAD, decision, snapshot)
    diagnosis = {'capture': handoff['capture'], 'runs': [{'run_id': run_id, 'attempt': 1,
        'jobs': [{'job_id': run_id * 10, 'verdict': 'unrelated', 'retryable': True,
            'reason': 'System package mirror DNS failed before project tests',
            'change_comparison': 'app.py changes do not alter workflow, mirror hostname or network configuration',
            'log_evidence': [{'member': 'install/1_packages.txt',
                'excerpt': 'Temporary failure resolving archive.ubuntu.com'}]}]} for run_id in api.runs]}
    return api, decision, snapshot, handoff, diagnosis


def test_unrelated_infrastructure_failure_prepares_each_failed_workflow(context):
    _, _, _, handoff, diagnosis = context
    result = requests(handoff, diagnosis, automatic_limit=2)
    assert result['blockers'] == []
    assert [(r['run_id'], r['attempt'], r['automatic_limit']) for r in result['requests']] == [(11, 1, 2), (22, 1, 2)]
    assert all(r['kind'] == 'ci-rerun' and r['head'] == HEAD for r in result['requests'])
    assert all([j['job_id'] for j in r['diagnosis']['jobs']] == [r['run_id'] * 10] for r in result['requests'])


@pytest.mark.parametrize('verdict,retryable', [('caused_by_pr', True), ('ambiguous', True), ('unrelated', False)])
def test_pr_caused_ambiguous_and_nonrecoverable_failures_do_not_retry(context, verdict, retryable):
    _, _, _, handoff, diagnosis = context
    diagnosis['runs'][0]['jobs'][0].update(verdict=verdict, retryable=retryable)
    result = requests(handoff, diagnosis)
    assert [r['run_id'] for r in result['requests']] == [22]
    assert result['blockers'][0]['run_id'] == 11
    assert result['blockers'][0]['verdict'] == verdict


def test_fresh_session_unchanged_capture_reuses_native_diagnosis(context):
    api, decision, snapshot, handoff, diagnosis = context
    # Only existing content-addressed evidence and the native report cross sessions.
    restored = json.loads(json.dumps(diagnosis))
    fresh = capture(API(), PR, HEAD, json.loads(json.dumps(decision)), json.loads(json.dumps(snapshot)))
    assert fresh['capture'] == handoff['capture']
    assert requests(fresh, restored) == requests(handoff, diagnosis)


@pytest.mark.parametrize('pr', [PR + '/', PR.replace('/owner/repo/', '/OWNER/REPO/'),
                              PR.replace('/owner/repo/', '/OWNER/REPO/') + '/'])
def test_equivalent_pr_url_reuses_complete_capture_and_native_diagnosis(context, pr):
    _, decision, snapshot, handoff, diagnosis = context
    fresh = capture(API(), pr, HEAD, json.loads(json.dumps(decision)), json.loads(json.dumps(snapshot)))
    assert fresh == handoff
    assert requests(fresh, json.loads(json.dumps(diagnosis))) == requests(handoff, diagnosis)


class RecollectionAPI(FakeAPI):
    """Use the existing Actions transport fixture with current PR reads."""
    def request(self, path, binary=False):
        if path == 'pulls/7':
            return copy.deepcopy(API().pull)
        if path.startswith('pulls/7/files?'):
            return copy.deepcopy(API().files)
        return super().request(path, binary=binary)


def recollected_diagnosis(api, registry):
    from shared.flaky_actions import collect_snapshot
    from test_flaky_tests import snapshot

    current = snapshot()
    evidence = collect_snapshot(api, registry, current)
    decision = current | {'evidence_file': evidence['evidence_file']}
    return capture(api, 'https://github.com/acme/project/pull/7', HEAD, decision, current), decision


@pytest.mark.parametrize('change', ['metadata', 'repo_identity', 'actor_identity', 'jobs', 'logs'])
def test_fresh_process_recollection_preserves_only_unchanged_diagnosis(tmp_path, change):
    import os
    import subprocess
    import sys
    from shared.flaky_tests import Registry
    from test_flaky_tests import JOB, REPO, SIGNATURE

    api = RecollectionAPI()
    api.run.update(repository={'id': 1, 'full_name': 'acme/project', 'description': 'old'},
        head_repository={'id': 2, 'full_name': 'fork/project', 'description': 'old'},
        actor={'id': 3, 'login': 'actor', 'avatar_url': 'old'},
        triggering_actor={'id': 4, 'login': 'trigger', 'avatar_url': 'old'})
    registry = Registry(REPO)
    original, original_decision = recollected_diagnosis(api, registry)
    diagnosis = {'capture': original['capture'], 'runs': [{'run_id': 10, 'attempt': 1,
        'jobs': [{'job_id': 101, 'verdict': 'unrelated', 'retryable': True,
            'reason': 'External service failed', 'change_comparison': 'No service configuration changes',
            'log_evidence': [{'member': f'{JOB}/1_Tests.txt', 'excerpt': SIGNATURE}]}]}]}
    original_request = requests(original, diagnosis)['requests'][0]
    artifacts = {path: Path(path).read_bytes() for path in
        (original_request['diagnosis']['source_file'], original_request['diagnosis']['archive_file'],
         original_decision['evidence_file'])}
    if change == 'metadata':
        for name in ('repository', 'head_repository'):
            api.run[name].update(description='new', owner={'id': 5, 'avatar_url': 'new'})
        for name in ('actor', 'triggering_actor'):
            api.run[name]['avatar_url'] = 'new'
    elif change == 'repo_identity':
        api.run['repository']['id'] += 10
    elif change == 'actor_identity':
        api.run['actor']['id'] += 10
    elif change == 'jobs':
        api.job_changes['completed_at'] = '2026-11-15T00:00:00Z'
    else:
        api.texts[1] += '\nAdditional failure evidence'
    request_file = tmp_path/'recollection.json'
    request_file.write_text(json.dumps({'run': api.run, 'job_changes': api.job_changes,
        'text': api.texts[1], 'original': original, 'diagnosis': diagnosis, 'change': change,
        'evidence_file': original_decision['evidence_file']}))
    script = '''
import json, sys
from pathlib import Path
from shared.ci_retry import requests, validate
from shared.flaky_tests import Registry
from test_ci_retry import RecollectionAPI, recollected_diagnosis
from test_flaky_tests import REPO
state = json.loads(Path(sys.argv[1]).read_text())
api = RecollectionAPI()
api.run, api.job_changes, api.texts[1] = state['run'], state['job_changes'], state['text']
current, snapshot = recollected_diagnosis(api, Registry(REPO))
original = state['original']
if state['change'] == 'metadata':
    assert current['runs'][0]['source_file'] != original['runs'][0]['source_file']
    assert snapshot['evidence_file'] != state['evidence_file']
    assert current['runs'][0]['capture'] == original['runs'][0]['capture']
    assert current['capture'] == original['capture']
    prepared = requests(current, state['diagnosis'])['requests'][0]
    validate(prepared, api, snapshot)
else:
    assert current['runs'][0]['capture'] != original['runs'][0]['capture']
    try:
        requests(current, state['diagnosis'])
    except ValueError as exc:
        assert 'inputs changed' in str(exc), str(exc)
    else:
        raise AssertionError('changed diagnosis inputs were reused')
'''
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, '-c', script, str(request_file)],
        env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(root/'runtime'), str(Path(__file__).parent)])},
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert all(Path(path).read_bytes() == data for path, data in artifacts.items())


@pytest.mark.parametrize('reference', ['head', 'base'])
def test_unrelated_repository_metadata_preserves_diagnosis(context, reference):
    api, decision, snapshot, handoff, diagnosis = context
    api.pull[reference]['repo'].update(stargazers_count=3, open_issues_count=8,
        updated_at='2026-11-14T00:00:00Z', pushed_at='2026-11-14T00:00:00Z',
        owner={'login': 'owner', 'avatar_url': 'https://example.test/new-avatar'})
    fresh = API()
    fresh.pull = json.loads(json.dumps(api.pull))
    current = capture(fresh, PR, HEAD, decision, snapshot)
    assert current['capture'] == handoff['capture']
    assert current['runs'] == handoff['runs']
    for request in requests(current, json.loads(json.dumps(diagnosis)))['requests']:
        validate(request, fresh, snapshot)


@pytest.mark.parametrize('reference', ['head', 'base'])
def test_repository_activity_during_live_reads_preserves_diagnosis(context, monkeypatch, reference):
    api, decision, snapshot, handoff, diagnosis = context
    original = api.request

    def active_repository(path):
        result = original(path)
        if path == 'pulls/7':
            repo = api.pull[reference]['repo']
            repo['stargazers_count'] = repo.get('stargazers_count', 0) + 1
        return result

    monkeypatch.setattr(api, 'request', active_repository)
    current = capture(api, PR, HEAD, decision, snapshot)
    assert current['capture'] == handoff['capture']
    for request in requests(current, diagnosis)['requests']:
        validate(request, api, snapshot)


@pytest.mark.parametrize('reference', ['head', 'base'])
@pytest.mark.parametrize('field', ['ref', 'repo_id', 'repo_name'])
def test_reference_identity_changes_invalidate_diagnosis(context, reference, field):
    api, decision, snapshot, _, diagnosis = context
    value = api.pull[reference]
    if field == 'ref':
        value['ref'] = 'other-branch'
    elif field == 'repo_id':
        value['repo']['id'] += 10
    else:
        value['repo']['full_name'] = 'other/repo'
    current = capture(api, PR, HEAD, decision, snapshot)
    with pytest.raises(ValueError, match='inputs changed'):
        requests(current, diagnosis)


@pytest.mark.parametrize('phase', ['capture', 'validate'])
@pytest.mark.parametrize('reference', ['head', 'base'])
@pytest.mark.parametrize('field', ['sha', 'ref', 'repo_id', 'repo_name'])
def test_revision_changes_during_live_reads_block_diagnosis(context, monkeypatch, phase, reference, field):
    api, decision, snapshot, handoff, diagnosis = context
    original = api.request

    def moved_revision(path):
        result = original(path)
        if path == 'pulls/7':
            value = api.pull[reference]
            if field == 'sha':
                value['sha'] = 'c' * 40
            elif field == 'ref':
                value['ref'] = 'other-branch'
            elif field == 'repo_id':
                value['repo']['id'] += 10
            else:
                value['repo']['full_name'] = 'other/repo'
        return result

    monkeypatch.setattr(api, 'request', moved_revision)
    with pytest.raises(ValueError, match='moved during'):
        if phase == 'capture':
            capture(api, PR, HEAD, decision, snapshot)
        else:
            validate(requests(handoff, diagnosis)['requests'][0], api, snapshot)


@pytest.mark.parametrize('change', ['diff', 'base', 'attempt', 'head', 'checks'])
def test_relevant_input_changes_invalidate_diagnosis(context, change):
    api, decision, snapshot, handoff, diagnosis = context
    if change == 'diff':
        api.files[0]['patch'] += '\n+another change'
    elif change == 'base':
        api.pull['base']['sha'] = 'c' * 40
    elif change == 'attempt':
        api.runs[11]['run_attempt'] = 2
    elif change == 'head':
        api.pull['head']['sha'] = 'd' * 40
    else:
        snapshot['checks'][0]['bucket'] = 'pass'
    if change in {'head', 'checks'}:
        with pytest.raises(ValueError, match='changed'):
            capture(api, PR, HEAD, decision, snapshot)
    else:
        fresh = capture(api, PR, HEAD, decision, snapshot)
        with pytest.raises(ValueError, match='inputs changed'):
            requests(fresh, diagnosis)


def test_every_failed_job_requires_a_complete_native_decision(context):
    _, _, _, handoff, diagnosis = context
    handoff['runs'][0]['jobs'].append({'job_id': 119, 'job': 'other failure'})
    with pytest.raises(ValueError, match='every failed job'):
        requests(handoff, diagnosis)


@pytest.mark.parametrize('other_conclusion', ['failure', 'success'])
def test_colliding_log_job_directories_block_only_affected_workflow(context, other_conclusion):
    api, decision, snapshot, _, _ = context
    evidence = json.loads(Path(decision['evidence_file']).read_text())
    source = json.loads(Path(evidence['checks'][0]['source_file']).read_text())
    source['jobs'][0]['name'] = 'install/a'
    source['jobs'][1].update(name='install:a', conclusion=other_conclusion)
    registry = type('Registry', (), {'path': Path(decision['evidence_file']).parent.parent / 'registry.json'})()
    evidence['checks'][0]['source_file'] = evidence_file(registry, source)
    decision['evidence_file'] = evidence_file(registry, evidence)
    handoff = capture(api, PR, HEAD, decision, snapshot)
    assert [run['run_id'] for run in handoff['runs']] == [22]
    assert 'log job directories are ambiguous' in handoff['blockers'][0]['reason']


@pytest.mark.parametrize('change', ['comparison', 'citation', 'wrong_job', 'invented_excerpt'])
def test_unrelated_verdict_requires_change_comparison_and_real_failed_job_logs(context, change):
    _, _, _, handoff, diagnosis = context
    job = diagnosis['runs'][0]['jobs'][0]
    if change == 'comparison':
        job['change_comparison'] = ''
    elif change == 'citation':
        job['log_evidence'] = []
    elif change == 'wrong_job':
        job['log_evidence'][0] = {'member': 'lint/1_lint.txt', 'excerpt': 'All checks passed'}
    else:
        job['log_evidence'][0]['excerpt'] = 'invented failure'
    with pytest.raises(ValueError):
        requests(handoff, diagnosis)


def test_missing_archive_leaves_only_affected_workflow_blocked(context):
    api, decision, snapshot, old_handoff, diagnosis = context
    # Both fixture workflows share the same archive, so replace only one source reference.
    evidence = json.loads(Path(decision['evidence_file']).read_text())
    evidence['checks'][0]['archive_file'] += '.missing'
    registry = type('Registry', (), {'path': Path(decision['evidence_file']).parent.parent / 'registry.json'})()
    decision['evidence_file'] = evidence_file(registry, evidence)
    handoff = capture(api, PR, HEAD, decision, snapshot)
    diagnosis['capture'] = handoff['capture']
    diagnosis['runs'] = [r for r in diagnosis['runs'] if r['run_id'] == 22]
    result = requests(handoff, diagnosis)
    assert [r['run_id'] for r in result['requests']] == [22]
    assert result['blockers']


@pytest.mark.parametrize('unavailable_run', [11, 22])
def test_failed_log_collection_preserves_independent_retry(context, monkeypatch, unavailable_run):
    from shared.flaky_actions import collect_snapshot
    from shared.flaky_tests import Registry

    api, _, snapshot, original_handoff, diagnosis = context
    original = api.request
    archives, jobs = {}, {}
    for run in original_handoff['runs']:
        run_id = run['run_id']
        archives[run_id] = Path(run['archive_file']).read_bytes()
        jobs[run_id] = json.loads(Path(run['source_file']).read_text())['jobs']
        for job in jobs[run_id]:
            job.update(run_id=run_id, head_sha=HEAD,
                html_url=f'https://github.com/owner/repo/actions/runs/{run_id}/job/{job["id"]}')
            for number, step in enumerate(job.get('steps', []), 1):
                step['number'] = number

    def collect_request(path, binary=False):
        if '/attempts/' in path:
            run_id = int(path.split('/')[2])
            if binary:
                if run_id == unavailable_run:
                    raise RuntimeError('Workflow logs are unavailable')
                return archives[run_id]
            return original(f'actions/runs/{run_id}')
        return original(path)

    monkeypatch.setattr(api, 'request', collect_request)
    monkeypatch.setattr(api, 'jobs', lambda run_id, attempt: copy.deepcopy(jobs[run_id]), raising=False)
    evidence = collect_snapshot(api, Registry(api.repo), snapshot)
    unavailable = next(item for item in evidence['checks'] if item['run_id'] == unavailable_run)
    assert unavailable['archive_file'] is None and unavailable['error'] is None
    handoff = capture(api, PR, HEAD, snapshot | {'evidence_file': evidence['evidence_file']}, snapshot)
    available_run = next(run['run_id'] for run in original_handoff['runs'] if run['run_id'] != unavailable_run)
    assert [run['run_id'] for run in handoff['runs']] == [available_run]
    assert handoff['blockers'] == [{'link': unavailable['link'],
        'reason': 'Failed workflow logs are unavailable; collect current failure evidence again'}]
    diagnosis['capture'] = handoff['capture']
    diagnosis['runs'] = [run for run in diagnosis['runs'] if run['run_id'] == available_run]
    result = requests(handoff, diagnosis)
    assert [request['run_id'] for request in result['requests']] == [available_run]
    assert result['blockers'] == handoff['blockers']
    validate(result['requests'][0], api, snapshot)


@pytest.mark.parametrize('field,label', [('source_file', 'source evidence is'), ('archive_file', 'logs are')])
@pytest.mark.parametrize('missing', ['absent', None, ''])
def test_unavailable_evidence_reference_blocks_only_affected_workflow(context, field, label, missing):
    api, decision, snapshot, _, _ = context
    evidence = json.loads(Path(decision['evidence_file']).read_text())
    if missing == 'absent':
        del evidence['checks'][0][field]
    else:
        evidence['checks'][0][field] = missing
    registry = type('Registry', (), {'path': Path(decision['evidence_file']).parent.parent / 'registry.json'})()
    decision['evidence_file'] = evidence_file(registry, evidence)
    handoff = capture(api, PR, HEAD, decision, snapshot)
    assert [run['run_id'] for run in handoff['runs']] == [22]
    assert handoff['blockers'] == [{'link': evidence['checks'][0]['link'],
        'reason': f'Failed workflow {label} unavailable; collect current failure evidence again'}]


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_unavailable_shared_evidence_blocks_all_retry_diagnosis(context, damage):
    api, decision, snapshot, _, _ = context
    path = Path(decision['evidence_file'])
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('{}')
    with pytest.raises((OSError, ValueError)):
        capture(api, PR, HEAD, decision, snapshot)


@pytest.mark.parametrize('unavailable_run', [11, 22])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_source_evidence_loss_during_workflow_read_preserves_independent_retry(
        context, monkeypatch, unavailable_run, damage):
    api, decision, snapshot, original_handoff, diagnosis = context
    original = api.request
    source = Path(next(run['source_file'] for run in original_handoff['runs']
                       if run['run_id'] == unavailable_run))

    def damage_source(path):
        if path == f'actions/runs/{unavailable_run}':
            if damage == 'missing':
                source.unlink()
            else:
                source.write_text('{}')
        return original(path)

    monkeypatch.setattr(api, 'request', damage_source)
    handoff = capture(api, PR, HEAD, decision, snapshot)
    available = next(run for run in original_handoff['runs'] if run['run_id'] != unavailable_run)
    assert handoff['runs'] == [available]
    assert len(handoff['blockers']) == 1
    assert handoff['blockers'][0]['link'].endswith(f'/job/{unavailable_run * 10}')
    assert handoff['blockers'][0]['reason']
    diagnosis['capture'] = handoff['capture']
    diagnosis['runs'] = [run for run in diagnosis['runs'] if run['run_id'] != unavailable_run]
    result = requests(handoff, diagnosis)
    assert [request['run_id'] for request in result['requests']] == [available['run_id']]
    assert result['blockers'] == handoff['blockers']
    validate(result['requests'][0], api, snapshot)


@pytest.mark.parametrize('unavailable_run', [11, 22])
def test_unavailable_workflow_metadata_preserves_independent_retry(context, monkeypatch, unavailable_run):
    api, decision, snapshot, original_handoff, diagnosis = context
    original = api.request
    reason = f'GitHub request unavailable: actions/runs/{unavailable_run}'

    def unavailable_metadata(path):
        if path == f'actions/runs/{unavailable_run}':
            raise RuntimeError(reason)
        return original(path)

    monkeypatch.setattr(api, 'request', unavailable_metadata)
    handoff = capture(api, PR, HEAD, decision, snapshot)
    available = next(run for run in original_handoff['runs'] if run['run_id'] != unavailable_run)
    assert handoff['runs'] == [available]
    assert handoff['blockers'] == [{'link': next(check['link'] for check in snapshot['checks']
        if check['link'].endswith(f'/job/{unavailable_run * 10}')), 'reason': reason}]
    diagnosis['capture'] = handoff['capture']
    diagnosis['runs'] = [run for run in diagnosis['runs'] if run['run_id'] != unavailable_run]
    result = requests(handoff, diagnosis)
    assert [request['run_id'] for request in result['requests']] == [available['run_id']]
    assert result['blockers'] == handoff['blockers']
    validate(result['requests'][0], api, snapshot)


@pytest.mark.parametrize('endpoint', ['pulls/7', 'pulls/7/files?per_page=100&page=1'])
def test_unavailable_shared_pr_inputs_block_all_retry_diagnosis(context, monkeypatch, endpoint):
    api, decision, snapshot, _, _ = context
    original = api.request

    def unavailable_changes(path):
        if path == endpoint:
            raise RuntimeError('GitHub request unavailable: ' + endpoint)
        return original(path)

    monkeypatch.setattr(api, 'request', unavailable_changes)
    with pytest.raises(RuntimeError, match='GitHub request unavailable'):
        capture(api, PR, HEAD, decision, snapshot)
