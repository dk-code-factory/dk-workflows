"""Native retry diagnoses remain bound to logs, PR changes and every failed job."""
import copy
import io
import json
from pathlib import Path
import zipfile

import pytest
from shared.ci_retry import capture, requests
from shared.flaky_actions import evidence_file

HEAD = 'a' * 40
PR = 'https://github.com/owner/repo/pull/7'


class API:
    repo = 'github.com/owner/repo'

    def __init__(self):
        self.pull = {'head': {'sha': HEAD}, 'base': {'sha': 'b' * 40}, 'changed_files': 1}
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
